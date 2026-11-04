#!/usr/bin/env bash
# Deletes an environment built by create-env.sh. Takes a final database snapshot first.
#   bash infra/aws/destroy-env.sh uat
set -euo pipefail
ENV_NAME="${1:?usage: destroy-env.sh uat|production}"
[ "$ENV_NAME" = "production" ] && { echo "Refusing to delete production from a script. Do it by hand in the console."; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env/$ENV_NAME.env"
export AWS_DEFAULT_REGION="$AWS_REGION"
P="$NAME_PREFIX"

echo "This deletes the $ENV_NAME server, database, network and IP in $AWS_REGION."
read -r -p "Type the environment name ($ENV_NAME) to continue: " ans
[ "$ans" = "$ENV_NAME" ] || { echo "Cancelled."; exit 1; }

ids() { aws ec2 "$1" --filters "Name=tag:Name,Values=$2" "Name=tag:Project,Values=collections-crm" --query "$3" --output text | tr '\t' '\n' | grep -v '^None$' || true; }

IID="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$P-app" "Name=instance-state-name,Values=pending,running,stopped,stopping" --query 'Reservations[].Instances[].InstanceId' --output text | tr '\t' '\n' | grep -v '^None$' || true)"
if [ -n "$IID" ]; then aws ec2 terminate-instances --instance-ids $IID >/dev/null; aws ec2 wait instance-terminated --instance-ids $IID; fi

for A in $(ids describe-addresses "$P-ip" 'Addresses[].AllocationId'); do aws ec2 release-address --allocation-id "$A"; done

if aws rds describe-db-instances --db-instance-identifier "$P-db" >/dev/null 2>&1; then
  aws rds modify-db-instance --db-instance-identifier "$P-db" --no-deletion-protection --apply-immediately >/dev/null
  aws rds delete-db-instance --db-instance-identifier "$P-db" --final-db-snapshot-identifier "$P-final-$(date +%Y%m%d%H%M)" >/dev/null
  aws rds wait db-instance-deleted --db-instance-identifier "$P-db"
  aws rds delete-db-subnet-group --db-subnet-group-name "$P-db-subnets" || true
fi

for G in $(ids describe-security-groups "$P-db" 'SecurityGroups[].GroupId') $(ids describe-security-groups "$P-app" 'SecurityGroups[].GroupId'); do aws ec2 delete-security-group --group-id "$G" || true; done
for S in $(ids describe-subnets "$P-*" 'Subnets[].SubnetId'); do aws ec2 delete-subnet --subnet-id "$S" || true; done
for R in $(ids describe-route-tables "$P-public-rt" 'RouteTables[].RouteTableId'); do
  for A in $(aws ec2 describe-route-tables --route-table-ids "$R" --query 'RouteTables[0].Associations[?Main==`false`].RouteTableAssociationId' --output text); do aws ec2 disassociate-route-table --association-id "$A" || true; done
  aws ec2 delete-route-table --route-table-id "$R" || true
done
VPC="$(ids describe-vpcs "$P-vpc" 'Vpcs[0].VpcId')"
for I in $(ids describe-internet-gateways "$P-igw" 'InternetGateways[].InternetGatewayId'); do
  [ -n "$VPC" ] && aws ec2 detach-internet-gateway --internet-gateway-id "$I" --vpc-id "$VPC" || true
  aws ec2 delete-internet-gateway --internet-gateway-id "$I" || true
done
[ -n "$VPC" ] && aws ec2 delete-vpc --vpc-id "$VPC" || true

aws iam remove-role-from-instance-profile --instance-profile-name "$P-app-role" --role-name "$P-app-role" 2>/dev/null || true
aws iam delete-instance-profile --instance-profile-name "$P-app-role" 2>/dev/null || true
aws iam detach-role-policy --role-name "$P-app-role" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore 2>/dev/null || true
aws iam delete-role-policy --role-name "$P-app-role" --policy-name read-db-secret 2>/dev/null || true
aws iam delete-role --role-name "$P-app-role" 2>/dev/null || true
echo "Done. A final database snapshot named $P-final-* was kept (delete it in RDS > Snapshots when you no longer need it)."
