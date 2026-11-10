# AWS Production setup by hand (AWS Console, no scripts)

This builds exactly what `infra/aws/create-env.sh production` builds, by clicking in the AWS Console. Use it when CloudShell or the command line is not available. Allow about 2 hours, of which about 15 minutes are waiting for the database.

It replaces steps 1 to 4 of `docs/AWS_PRODUCTION_SETUP.md`. When you reach the end of this page, carry on with that guide from **step 5**.

Screens change from time to time, so a label may differ slightly. The values to enter are what matter. Everything is created in **Asia Pacific (Mumbai) ap-south-1**; set the region at the top right before every step.

> If AWS answers "account verification in progress" when you launch the server or the database, wait for it to clear (up to two days) or ask AWS Support. Nothing below works around that. If you built UAT by hand in this account, EC2 and RDS already work and only CloudShell was blocked.

Names used below (keep them exactly, other steps look them up):

| Thing | Name |
|---|---|
| VPC | `crm-prod-vpc` |
| Server firewall | `crm-prod-app` |
| Database firewall | `crm-prod-db` |
| Server role | `crm-prod-app-role` |
| DB subnet group | `crm-prod-db-subnets` |
| DB settings group | `crm-prod-pg` |
| Database | `crm-prod-db` |
| Server | `crm-prod-app` |
| Fixed IP | `crm-prod-ip` |
| Alarm topic | `crm-prod-alerts` |

## 0. On your Mac: the deploy key and the first-boot script

```
ssh-keygen -t ed25519 -C "crm-deploy-prod" -f ~/.ssh/crm_deploy_prod -N ""
cd alpharupee-collections-crm
git checkout main && git pull
bash infra/aws/render-user-data.sh production ~/.ssh/crm_deploy_prod.pub
```

The last command writes `crm-production-user-data.sh` in the current folder. It contains only the public key, no secrets. Open it in a text editor and check the line `DOMAIN=` says `crm.alpharupee.in`. Keep it ready for step 5. Never paste the private file (`crm_deploy_prod`, without `.pub`) anywhere except through `scripts/github-production.sh` in the main guide.

## 1. Network (VPC)

1. Console search bar, type **VPC**, open it, click **Create VPC**.
2. Choose **VPC and more**.
3. Fill in:
   - Name tag auto-generation: tick it, value `crm-prod` (this names the VPC `crm-prod-vpc`)
   - IPv4 CIDR block: `10.30.0.0/16` (UAT uses `10.20.0.0/16`, so the two never overlap)
   - Number of Availability Zones: `2`
   - Number of public subnets: `2`
   - Number of private subnets: `2`
   - NAT gateways: **None**
   - VPC endpoints: **None**
   - DNS options: tick both **Enable DNS hostnames** and **Enable DNS resolution**
4. Click **Create VPC**, wait until it says complete, then **View VPC**.

Public subnets reach the internet; private subnets do not, which is what you want for the database.

## 2. Firewalls (security groups)

VPC, Security groups, **Create security group**. Create two.

**First: `crm-prod-app`**

- Name `crm-prod-app`, description `CRM application server`, VPC `crm-prod-vpc`
- Inbound rules, add three:

| Type | Port | Source |
|---|---|---|
| HTTP | 80 | Anywhere-IPv4 `0.0.0.0/0` |
| HTTPS | 443 | Anywhere-IPv4 `0.0.0.0/0` |
| SSH | 22 | Anywhere-IPv4 `0.0.0.0/0` |

  Port 22 stays open so GitHub's deploy runners can connect; it accepts only the deploy key.
- Outbound: keep the default **All traffic, 0.0.0.0/0**. Do not delete or narrow it. The server must reach the internet to install packages and to talk to MSG91, Smartflo, Easebuzz and Let's Encrypt.

**Second: `crm-prod-db`**

- Name `crm-prod-db`, description `CRM database`, VPC `crm-prod-vpc`
- Inbound rule, one only:

| Type | Port | Source |
|---|---|---|
| PostgreSQL | 5432 | **Custom**, then choose the security group `crm-prod-app` |

Do not add any other inbound rule. This keeps the database reachable only from the server.

## 3. Server role (IAM)

1. Console search, **IAM**, Roles, **Create role**.
2. Trusted entity: **AWS service**, use case **EC2**. Next.
3. Search and tick **AmazonSSMManagedInstanceCore**. Next.
4. Role name `crm-prod-app-role`. Create role.

The permission to read passwords is added in step 6b, after the database exists, so it can name exactly the two secrets the server needs and nothing else.

## 4. Database (RDS)

**4a. Settings group (parameter group)**

1. RDS, left menu **Parameter groups**, **Create parameter group**.
2. Parameter group family **postgres17**, type **DB Parameter Group**, name `crm-prod-pg`, description `CRM production settings`. Create.
3. Open `crm-prod-pg`, **Edit**, search for each name, set the value, then **Save changes**:

| Parameter | Value |
|---|---|
| `rds.force_ssl` | `1` |
| `log_min_duration_statement` | `500` |
| `idle_in_transaction_session_timeout` | `60000` |
| `autovacuum_vacuum_scale_factor` | `0.05` |
| `autovacuum_analyze_scale_factor` | `0.02` |

`rds.force_ssl` makes the database refuse any unencrypted connection. The next two log queries slower than half a second and close sessions left idle inside a transaction for a minute. The last two keep busy tables tidy.

**4b. Subnet group**

1. RDS, **Subnet groups**, **Create DB subnet group**.
2. Name `crm-prod-db-subnets`, description `CRM production database subnets`, VPC `crm-prod-vpc`.
3. Availability Zones: tick `ap-south-1a` and `ap-south-1b`.
4. Subnets: choose the two **private** subnets (names contain `private`). Not the public ones.
5. Create.

**4c. The database**

1. RDS, **Databases**, **Create database**.
2. **Standard create**, engine **PostgreSQL**, version **PostgreSQL 17.x** (the newest 17; not 18).
3. Templates: **Production**. Availability and durability: **Single-AZ DB instance deployment (1 instance)**. (If your console does not offer that under Production, pick the **Dev/Test** template; the other settings below are the same.)
4. Settings:
   - DB instance identifier `crm-prod-db`
   - Master username `crm_admin`
   - Credentials management: **Managed in AWS Secrets Manager** (leave the default key). Nobody ever sees or types the admin password.
5. Instance configuration: **Standard classes**, `db.m6g.large`.
6. Storage: type **General Purpose SSD (gp3)**, allocated storage `100` GiB. Tick **Enable storage autoscaling** and set maximum storage threshold `500` GiB.
7. Connectivity:
   - Compute resource: **Don't connect to an EC2 compute resource**
   - VPC `crm-prod-vpc`, DB subnet group `crm-prod-db-subnets`
   - Public access: **No**
   - VPC security group: **Choose existing**, add `crm-prod-db`, remove `default`
8. Monitoring: tick **Enable Performance Insights** (default retention is fine).
9. Expand **Additional configuration**:
   - Initial database name: `crm`
   - DB parameter group: `crm-prod-pg`
   - Backup retention period: `14` days
   - Backup window: choose **Select window**, start time `22:00` UTC, duration `0.5` hours (03:30 IST)
   - Copy tags to snapshots: tick
   - Encryption: enabled (default)
   - Auto minor version upgrade: enabled
   - Maintenance window: **Select window**, Saturday `20:30` UTC, duration `1` hour (Sunday 02:00 IST, outside calling hours)
   - Deletion protection: **tick Enable deletion protection**
10. Create database. It shows "Creating" for about 10 to 15 minutes. Carry on with step 5 meanwhile.

## 5. Application server (EC2)

1. Console search, **EC2**, **Launch instance**.
2. Name `crm-prod-app`.
3. Image: **Ubuntu**, **Ubuntu Server 24.04 LTS**, **64-bit (x86)**.
4. Instance type `m6i.large`.
5. Key pair: **Proceed without a key pair**. (You connect through Session Manager; the deploy key goes in through the first-boot script.)
6. Network settings, **Edit**:
   - VPC `crm-prod-vpc`
   - Subnet: a **public** subnet in `ap-south-1a` (name contains `public1`)
   - Auto-assign public IP: **Enable** (needed for first boot; the fixed IP in step 6 replaces it)
   - Firewall: **Select existing security group**, `crm-prod-app`
7. Storage: `50` GiB, **gp3**. Expand **Advanced** and set Encrypted to **Yes**.
8. Resource tags: **Add new tag** twice:
   - Key `Name`, value `crm-prod-app`, resource types **Instances**
   - Key `Name`, value `crm-prod-app-root`, resource types **Volumes** (the daily snapshot in step 7 finds the disk by this tag)
9. Expand **Advanced details**:
   - IAM instance profile: `crm-prod-app-role`
   - Termination protection: **Enable**
   - Metadata version: **V2 only (token required)**
   - **User data**: paste the whole content of `crm-production-user-data.sh`
10. **Launch instance**. Wait until Status checks show 2/2 passed (about 3 minutes).

## 6. Fixed public IP and permission to read the passwords

**6a. Fixed IP**

1. EC2, left menu **Elastic IPs**, **Allocate Elastic IP address**, Network border group `ap-south-1`, tag key `Name`, value `crm-prod-ip`, Allocate.
2. Select it, **Actions**, **Associate Elastic IP address**, Resource type **Instance**, choose `crm-prod-app`, Associate.
3. **Write the IP down.** It is the fixed Production address: your `DEPLOY_HOST`, and the address you give the LMS team and others to allow-list.

**6b. Permission to read exactly two secrets**

1. RDS, `crm-prod-db`, tab **Configuration**, copy the **Master credentials ARN** (starts `arn:aws:secretsmanager:ap-south-1:`). If it is not there yet, wait until the database is Available.
2. Console search, **Secrets Manager**, find your LMS secret `alpharupee/lms/readonlyuser`, open it, copy its **Secret ARN**.
3. IAM, Roles, `crm-prod-app-role`, **Add permissions**, **Create inline policy**, tab **JSON**, paste this and replace the two `PASTE_...` values with the ARNs you copied:

```
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "PASTE_THE_DATABASE_SECRET_ARN",
        "PASTE_THE_LMS_SECRET_ARN"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "rds:DescribeDBInstances",
      "Resource": "*"
    }
  ]
}
```

4. Next, policy name `read-secrets`, Create policy.

(If the LMS secret is in another AWS account or not created yet, leave out its line and add it later.)

## 7. Daily backup of the server disk

1. EC2, left menu **Lifecycle Manager**, **Create lifecycle policy**.
2. Policy type **EBS snapshot policy**.
3. Target resources: **Volume**. Target resource tags: key `Name`, value `crm-prod-app-root`.
4. Description `crm-prod daily snapshots`. IAM role: **Default role**. Policy status **Enabled**.
5. Schedule: name `daily`, frequency **Daily**, every `24` hours, starting at `21:00` UTC (02:30 IST). Retention type **Count**, `14`. Tick **Copy tags from source**.
6. Create policy.

The database already backs itself up (step 4c), so this covers only the application server's disk.

## 8. Alarms by e-mail

**8a. The e-mail topic**

1. Console search, **SNS**, Topics, **Create topic**, type **Standard**, name `crm-prod-alerts`. Create.
2. Open it, **Create subscription**, protocol **Email**, endpoint `baby.j@alpharupee.in` (or whoever should be told). Create.
3. AWS e-mails that address. **Click Confirm subscription in the message**, or no alarm will reach anyone.

**8b. The seven alarms**

CloudWatch, Alarms, **Create alarm**, **Select metric** for each row below. Notification: in alarm, choose `crm-prod-alerts`. Missing data: **Treat missing data as good**. Alarm name: `crm-prod-` plus the name in the first column.

| Name | Metric (dimension) | Statistic, period | Condition | Datapoints |
|---|---|---|---|---|
| `server-recover` | EC2 `StatusCheckFailed_System` (instance `crm-prod-app`) | Maximum, 1 minute | `>= 1` | 2 of 2 |
| `server-unreachable` | EC2 `StatusCheckFailed_Instance` (same instance) | Maximum, 1 minute | `>= 1` | 3 of 3 |
| `server-cpu-high` | EC2 `CPUUtilization` (same instance) | Average, 5 minutes | `> 80` | 3 of 3 |
| `db-cpu-high` | RDS `CPUUtilization` (`crm-prod-db`) | Average, 5 minutes | `> 70` | 3 of 3 |
| `db-storage-low` | RDS `FreeStorageSpace` (`crm-prod-db`) | Minimum, 5 minutes | `< 21474836480` (20 GiB) | 2 of 2 |
| `db-memory-low` | RDS `FreeableMemory` (`crm-prod-db`) | Average, 5 minutes | `< 1073741824` (1 GiB) | 3 of 3 |
| `db-connections-high` | RDS `DatabaseConnections` (`crm-prod-db`) | Maximum, 5 minutes | `> 200` | 2 of 2 |

For `server-recover` only, on the notification page also click **Add EC2 action** and choose **Recover this instance** for the in-alarm state. This restarts the server on healthy hardware if AWS's hardware under it fails, keeping its disk and its fixed IP.

## 9. DNS

At the DNS provider for `alpharupee.in`, add:

| Type | Name | Value |
|---|---|---|
| A | `crm` | the Elastic IP from step 6a |

## 10. Check the servers

1. EC2, `crm-prod-app`, **Connect**, tab **Session Manager**, **Connect**. If Connect is greyed out, wait five minutes and refresh.
2. In the terminal:

```
sudo cat /var/log/crm-bootstrap.done
curl -s http://127.0.0.1/healthz
```

Expected: `crm first-boot setup finished` and `ok`. If the first file is missing, wait two minutes. If it never appears, send the output of `sudo tail -n 40 /var/log/cloud-init-output.log`.

3. RDS, `crm-prod-db` shows **Available**, Publicly accessible **No**. Note the **Endpoint**.
4. In the terminal, check the server may read the database secret (replace the ARN with the one from step 6b):

```
aws secretsmanager get-secret-value --region ap-south-1 --secret-id PASTE_THE_DATABASE_SECRET_ARN --query Name --output text
```

It should print a name, not the password. An "AccessDenied" means the policy in step 6b has a wrong ARN.

## 11. Carry on with the main guide

Continue `docs/AWS_PRODUCTION_SETUP.md` from **step 5** (its steps 1 to 4 were done here). Wherever it says `show-env.sh production`, take the values from the Console instead: the server IP from step 6a, and the server's own SSH fingerprint from EC2, select the instance, Actions, Monitor and troubleshoot, **Get system log**, the line `(ED25519)` between `BEGIN SSH HOST KEY FINGERPRINTS` and `END`.

## Quick checklist

- [ ] VPC `crm-prod-vpc` (`10.30.0.0/16`) with 2 public and 2 private subnets, no NAT
- [ ] `crm-prod-db` firewall allows 5432 only from `crm-prod-app`
- [ ] Role `crm-prod-app-role` with Session Manager and the `read-secrets` policy
- [ ] Parameter group `crm-prod-pg` set and attached to the database
- [ ] RDS `crm-prod-db` Available, `db.m6g.large`, 100 GiB (max 500), public access No, deletion protection on, backups 14 days, Performance Insights on
- [ ] EC2 `crm-prod-app` `m6i.large`, 50 GiB encrypted, no key pair, role attached, user data pasted, termination protection on, tags `crm-prod-app` and `crm-prod-app-root`
- [ ] Elastic IP attached, DNS A record added
- [ ] Lifecycle Manager policy `crm-prod daily snapshots` enabled
- [ ] SNS subscription confirmed, seven `crm-prod-` alarms created, `server-recover` has the Recover action
- [ ] Session Manager shows `crm first-boot setup finished` and `ok`

## Deleting Production later

This is destructive; do it only on purpose. Delete in this order: turn off termination protection and terminate the EC2 instance, release the Elastic IP, RDS database (turn off deletion protection first, keep a final snapshot), DB subnet group, parameter group, the two security groups, the VPC, then the IAM role, the lifecycle policy, the alarms and the SNS topic.
