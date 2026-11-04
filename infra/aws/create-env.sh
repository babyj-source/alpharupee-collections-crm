#!/usr/bin/env bash
# Creates one environment (network, firewall rules, database, application server) in AWS.
# Run it in AWS CloudShell, signed in to the right AWS account and region.
#
#   bash infra/aws/create-env.sh uat
#
# Safe to run again: each resource is looked up by its Name tag first and reused if it exists.
# No passwords are created or printed here: the database password is generated and held by
# AWS (RDS-managed secret in Secrets Manager).
set -euo pipefail

ENV_NAME="${1:?usage: create-env.sh uat|production}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck disable=SC1090
source "$HERE/env/$ENV_NAME.env"
export AWS_DEFAULT_REGION="$AWS_REGION"

P="$NAME_PREFIX"
TAGS_COMMON="Key=Project,Value=collections-crm Key=Env,Value=$ENV_TAG"
say() { printf '\n==> %s\n' "$*"; }
tagspec() { echo "ResourceType=$1,Tags=[{Key=Name,Value=$2},{Key=Project,Value=collections-crm},{Key=Env,Value=$ENV_TAG}]"; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
echo "AWS account $ACCOUNT, region $AWS_REGION, environment $ENV_NAME"
[ -f "$HERE/$DEPLOY_PUBKEY_FILE" ] || [ -f "$DEPLOY_PUBKEY_FILE" ] || { echo "Deploy public key not found: $DEPLOY_PUBKEY_FILE (see docs/GITHUB_SETUP.md step 3)"; exit 1; }
PUBKEY_PATH="$DEPLOY_PUBKEY_FILE"; [ -f "$PUBKEY_PATH" ] || PUBKEY_PATH="$HERE/$DEPLOY_PUBKEY_FILE"
DEPLOY_PUBKEY="$(cat "$PUBKEY_PATH")"
case "$DEPLOY_PUBKEY" in ssh-ed25519\ *|ssh-rsa\ *) ;; *) echo "That file is not a public key. Use the .pub file."; exit 1;; esac

find_by_name() { # type-specific describe, prints the first id or empty
  aws ec2 "$1" --filters "Name=tag:Name,Values=$2" "Name=tag:Project,Values=collections-crm" \
    --query "$3" --output text 2>/dev/null | grep -v '^None$' | head -n1 || true
}

# ---------------------------------------------------------------- network
say "VPC"
VPC_ID="$(find_by_name describe-vpcs "$P-vpc" 'Vpcs[0].VpcId')"
if [ -z "$VPC_ID" ]; then
  VPC_ID="$(aws ec2 create-vpc --cidr-block "$VPC_CIDR" --tag-specifications "$(tagspec vpc "$P-vpc")" --query Vpc.VpcId --output text)"
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames '{"Value":true}'
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support '{"Value":true}'
fi
echo "    $VPC_ID"

say "Internet gateway"
IGW_ID="$(find_by_name describe-internet-gateways "$P-igw" 'InternetGateways[0].InternetGatewayId')"
if [ -z "$IGW_ID" ]; then
  IGW_ID="$(aws ec2 create-internet-gateway --tag-specifications "$(tagspec internet-gateway "$P-igw")" --query InternetGateway.InternetGatewayId --output text)"
  aws ec2 attach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID"
fi
echo "    $IGW_ID"

mapfile -t AZS < <(aws ec2 describe-availability-zones --filters Name=state,Values=available --query 'AvailabilityZones[].ZoneName' --output text | tr '\t' '\n' | sort | head -n2)
AZ_A="${AZS[0]}"; AZ_B="${AZS[1]}"

mk_subnet() { # name cidr az
  local id; id="$(find_by_name describe-subnets "$1" 'Subnets[0].SubnetId')"
  if [ -z "$id" ]; then
    id="$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block "$2" --availability-zone "$3" \
      --tag-specifications "$(tagspec subnet "$1")" --query Subnet.SubnetId --output text)"
  fi
  echo "$id"
}
say "Subnets (2 public for the app, 2 private for the database)"
PUB_A="$(mk_subnet "$P-public-a" "$PUB_CIDR_A" "$AZ_A")"
PUB_B="$(mk_subnet "$P-public-b" "$PUB_CIDR_B" "$AZ_B")"
PRIV_A="$(mk_subnet "$P-private-a" "$PRIV_CIDR_A" "$AZ_A")"
PRIV_B="$(mk_subnet "$P-private-b" "$PRIV_CIDR_B" "$AZ_B")"
echo "    public $PUB_A $PUB_B   private $PRIV_A $PRIV_B"

say "Route table for public subnets"
RT_ID="$(find_by_name describe-route-tables "$P-public-rt" 'RouteTables[0].RouteTableId')"
if [ -z "$RT_ID" ]; then
  RT_ID="$(aws ec2 create-route-table --vpc-id "$VPC_ID" --tag-specifications "$(tagspec route-table "$P-public-rt")" --query RouteTable.RouteTableId --output text)"
  aws ec2 create-route --route-table-id "$RT_ID" --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW_ID" >/dev/null
  aws ec2 associate-route-table --route-table-id "$RT_ID" --subnet-id "$PUB_A" >/dev/null
  aws ec2 associate-route-table --route-table-id "$RT_ID" --subnet-id "$PUB_B" >/dev/null
fi
echo "    $RT_ID"

# ---------------------------------------------------------------- security groups
mk_sg() { # name description
  local id; id="$(find_by_name describe-security-groups "$1" 'SecurityGroups[0].GroupId')"
  if [ -z "$id" ]; then
    id="$(aws ec2 create-security-group --group-name "$1" --description "$2" --vpc-id "$VPC_ID" \
      --tag-specifications "$(tagspec security-group "$1")" --query GroupId --output text)"
    SG_NEW="$SG_NEW $id"
  fi
  echo "$id"
}
SG_NEW=""
say "Security groups"
APP_SG="$(mk_sg "$P-app" "CRM application server")"
DB_SG="$(mk_sg "$P-db" "CRM database")"
case "$SG_NEW" in *"$APP_SG"*)
  aws ec2 authorize-security-group-ingress --group-id "$APP_SG" --ip-permissions \
    "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=$WEB_ALLOWED_CIDR,Description=http}]" \
    "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=$WEB_ALLOWED_CIDR,Description=https}]" \
    "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$SSH_ALLOWED_CIDR,Description=deploy-ssh-key-only}]" >/dev/null ;;
esac
case "$SG_NEW" in *"$DB_SG"*)
  aws ec2 authorize-security-group-ingress --group-id "$DB_SG" --ip-permissions \
    "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$APP_SG,Description=from-app-server-only}]" >/dev/null ;;
esac
echo "    app $APP_SG   db $DB_SG (database accepts only the app server)"

# ---------------------------------------------------------------- IAM role for the server
say "Instance role (Session Manager access + read the database secret)"
ROLE="$P-app-role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --tags $TAGS_COMMON --assume-role-policy-document '{
    "Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name read-db-secret --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":[\"secretsmanager:GetSecretValue\"],\"Resource\":\"arn:aws:secretsmanager:$AWS_REGION:$ACCOUNT:secret:rds!db-*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"rds:DescribeDBInstances\"],\"Resource\":\"*\"}]}"
if ! aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  echo "    waiting for the role to become usable"; sleep 15
fi

# ---------------------------------------------------------------- database
say "Database (RDS PostgreSQL $DB_ENGINE_VERSION, private, encrypted)"
DB_ID="$P-db"
if ! aws rds describe-db-instances --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then
  aws rds create-db-subnet-group --db-subnet-group-name "$P-db-subnets" --db-subnet-group-description "$P database subnets" \
    --subnet-ids "$PRIV_A" "$PRIV_B" --tags $TAGS_COMMON >/dev/null 2>&1 || true
  aws rds create-db-instance \
    --db-instance-identifier "$DB_ID" \
    --engine postgres --engine-version "$DB_ENGINE_VERSION" \
    --db-instance-class "$DB_INSTANCE_CLASS" \
    --allocated-storage "$DB_STORAGE_GB" --storage-type gp3 --storage-encrypted \
    --db-name crm --master-username crm_admin --manage-master-user-password \
    --db-subnet-group-name "$P-db-subnets" --vpc-security-group-ids "$DB_SG" \
    --no-publicly-accessible --backup-retention-period "$DB_BACKUP_DAYS" \
    --no-multi-az --auto-minor-version-upgrade --deletion-protection \
    --enable-performance-insights --tags $TAGS_COMMON >/dev/null
  echo "    creation started (takes about 10 minutes)"
else
  echo "    $DB_ID already exists"
fi

# ---------------------------------------------------------------- application server
say "Application server (Ubuntu 24.04, $APP_INSTANCE_TYPE)"
INSTANCE_ID="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$P-app" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null | grep -v '^None$' || true)"
if [ -z "$INSTANCE_ID" ]; then
  AMI="$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)"
  UD="$(mktemp)"
  b64() { base64 -w0 "$1"; }
  sed -e "s|__DOMAIN__|$DOMAIN|g" \
      -e "s|__DEPLOY_PUBKEY__|$DEPLOY_PUBKEY|g" \
      -e "s|__SUDOERS_B64__|$(b64 "$ROOT/deploy/server/sudoers-deploy")|" \
      -e "s|__UNIT_B64__|$(b64 "$ROOT/deploy/server/crm.service")|" \
      -e "s|__NGINX_B64__|$(b64 "$ROOT/deploy/server/nginx-crm.conf")|" \
      "$HERE/user-data.sh" > "$UD"
  SIZE="$(wc -c < "$UD")"; [ "$SIZE" -lt 16000 ] || { echo "user-data is $SIZE bytes, over the 16 KB limit"; exit 1; }
  INSTANCE_ID="$(aws ec2 run-instances --image-id "$AMI" --instance-type "$APP_INSTANCE_TYPE" \
    --subnet-id "$PUB_A" --security-group-ids "$APP_SG" --iam-instance-profile "Name=$ROLE" \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1 \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$APP_DISK_GB,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
    --user-data "file://$UD" \
    --tag-specifications "$(tagspec instance "$P-app")" "$(tagspec volume "$P-app-root")" \
    --query 'Instances[0].InstanceId' --output text)"
  rm -f "$UD"
fi
echo "    $INSTANCE_ID"
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

say "Fixed public IP"
EIP_ALLOC="$(aws ec2 describe-addresses --filters "Name=tag:Name,Values=$P-ip" --query 'Addresses[0].AllocationId' --output text 2>/dev/null | grep -v '^None$' || true)"
if [ -z "$EIP_ALLOC" ]; then
  EIP_ALLOC="$(aws ec2 allocate-address --domain vpc --tag-specifications "$(tagspec elastic-ip "$P-ip")" --query AllocationId --output text)"
fi
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" >/dev/null
EIP="$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].PublicIp' --output text)"
echo "    $EIP"

say "Waiting for the database to become available"
aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
DB_HOST="$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].Endpoint.Address' --output text)"

cat <<MSG

==================== $ENV_NAME is built ====================
Server instance : $INSTANCE_ID
Server IP       : $EIP        (DEPLOY_HOST)
Database host   : $DB_HOST  (private, reachable only from the server)
Site name       : $DOMAIN

Next:
 1. DNS: create an A record   $DOMAIN  ->  $EIP
 2. Save these details:  bash infra/aws/show-env.sh $ENV_NAME
 3. Follow docs/AWS_UAT_SETUP.md from step 4 (connect, create the .env file, add GitHub variables, HTTPS).
MSG
