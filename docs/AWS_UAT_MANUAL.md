# AWS UAT setup by hand (AWS Console, no scripts)

This builds exactly what `infra/aws/create-env.sh` builds, by clicking in the AWS Console.
Use it when CloudShell or the CLI is not available. Allow about 60 minutes, of which about 12 are waiting for the database.

Screens change from time to time, so a label may differ slightly. The values to enter are what matter.

Everything is created in **Asia Pacific (Mumbai) ap-south-1**. Set the region at the top right of the Console before every step, and check it again if a resource seems to be missing.

> A new AWS account that is still "being verified" may refuse to launch EC2 or RDS. If you get an account verification message, wait for it to clear (up to two days) or ask AWS Support. Nothing below works around that.

Names used below (keep them exactly, other steps look them up):

| Thing | Name |
|---|---|
| VPC | `crm-uat-vpc` |
| Server firewall | `crm-uat-app` |
| Database firewall | `crm-uat-db` |
| Server role | `crm-uat-app-role` |
| DB subnet group | `crm-uat-db-subnets` |
| Database | `crm-uat-db` |
| Server | `crm-uat-app` |
| Fixed IP | `crm-uat-ip` |

## 1. Network (VPC)

1. Console search bar, type **VPC**, open it, click **Create VPC**.
2. Choose **VPC and more**.
3. Fill in:
   - Name tag auto-generation: tick it, value `crm-uat` (this names the VPC `crm-uat-vpc`)
   - IPv4 CIDR block: `10.20.0.0/16`
   - Number of Availability Zones: `2`
   - Number of public subnets: `2`
   - Number of private subnets: `2`
   - NAT gateways: **None**
   - VPC endpoints: **None**
   - DNS options: tick both **Enable DNS hostnames** and **Enable DNS resolution**
4. Click **Create VPC** and wait until it says complete, then **View VPC**.

The wizard creates the internet gateway and the route tables for you. Public subnets reach the internet, private subnets do not, which is what you want for the database.

## 2. Firewalls (security groups)

VPC, Security groups, **Create security group**. Create two.

**First: `crm-uat-app`**

- Name `crm-uat-app`, description `CRM application server`, VPC `crm-uat-vpc`
- Inbound rules, add three:

| Type | Port | Source |
|---|---|---|
| HTTP | 80 | Anywhere-IPv4 `0.0.0.0/0` |
| HTTPS | 443 | Anywhere-IPv4 `0.0.0.0/0` |
| SSH | 22 | Anywhere-IPv4 `0.0.0.0/0` |

  (Port 22 stays open so GitHub's deploy runners can connect. It accepts only the deploy key. To make UAT private, replace the 80 and 443 sources with your office IP ranges.)
- Outbound: keep the default rule **All traffic, 0.0.0.0/0**. Do not delete or narrow it. The server must reach the internet on ports 80 and 443 to install packages and talk to MSG91, Smartflo and Let's Encrypt. (If first boot fails with `Network is unreachable` or timeouts in `/var/log/cloud-init-output.log`, check this rule and that the server had a public IP at launch.)
- Create.

**Second: `crm-uat-db`**

- Name `crm-uat-db`, description `CRM database`, VPC `crm-uat-vpc`
- Inbound rule, one only:

| Type | Port | Source |
|---|---|---|
| PostgreSQL | 5432 | **Custom**, then choose the security group `crm-uat-app` |

- Create. Do not add any other inbound rule. This is what keeps the database reachable only from the server.

## 3. Server role (IAM)

This lets you open the server in the browser (Session Manager) and lets the server read its database password from AWS.

1. Console search, **IAM**, Roles, **Create role**.
2. Trusted entity: **AWS service**, use case **EC2**. Next.
3. Search and tick **AmazonSSMManagedInstanceCore**. Next.
4. Role name `crm-uat-app-role`. Create role.
5. Open the role, tab **Permissions**, **Add permissions**, **Create inline policy**, tab **JSON**, paste this after replacing `ACCOUNT_ID` with your 12-digit AWS account number (top right menu shows it):

```
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:rds!db-*"
    },
    {
      "Effect": "Allow",
      "Action": "rds:DescribeDBInstances",
      "Resource": "*"
    }
  ]
}
```

6. Next, policy name `read-db-secret`, Create policy.

## 4. Database (RDS)

**4a. Subnet group**

1. Console search, **RDS**, left menu **Subnet groups**, **Create DB subnet group**.
2. Name `crm-uat-db-subnets`, description `CRM UAT database subnets`, VPC `crm-uat-vpc`.
3. Availability Zones: tick `ap-south-1a` and `ap-south-1b`.
4. Subnets: choose the two **private** subnets (their names contain `private`). Do not pick the public ones.
5. Create.

**4b. The database**

1. RDS, **Databases**, **Create database**.
2. Choose **Standard create**, engine **PostgreSQL**, engine version **PostgreSQL 17.x** (the newest 17 in the list; not 18).
3. Templates: **Dev/Test**. Availability: **Single-AZ DB instance deployment (1 instance)**.
4. Settings:
   - DB instance identifier `crm-uat-db`
   - Master username `crm_admin`
   - Credentials management: **Managed in AWS Secrets Manager** (leave the default encryption key). This way nobody ever sees or types the admin password.
5. Instance configuration: **Burstable classes**, `db.t4g.medium`.
6. Storage: type **General Purpose SSD (gp3)**, allocated storage `50` GiB.
7. Connectivity:
   - Compute resource: **Don't connect to an EC2 compute resource**
   - VPC `crm-uat-vpc`, DB subnet group `crm-uat-db-subnets`
   - Public access: **No**
   - VPC security group: **Choose existing**, add `crm-uat-db`, and remove `default`
8. Expand **Additional configuration**:
   - Initial database name: `crm`
   - Backup retention period: `7` days
   - Encryption: enabled (default)
   - Deletion protection: **tick Enable deletion protection**
9. Create database. It shows "Creating" for about 10 minutes. Carry on with step 5 meanwhile.

## 5. Application server (EC2)

**5a. Make the first-boot script on your Mac.** It contains only the public key, no secrets.

```
cd alpharupee-collections-crm
bash infra/aws/render-user-data.sh uat ~/crm-keys/crm_deploy_uat.pub
```

This writes `crm-uat-user-data.sh` in the current folder. Open it in a text editor, check `DOMAIN=` shows `uat-crm.alpharupee.in` (edit `infra/aws/env/uat.env` and run it again if not), and keep it ready to copy.

**5b. Launch the instance**

1. Console search, **EC2**, **Launch instance**.
2. Name `crm-uat-app`.
3. Application and OS image: **Ubuntu**, choose **Ubuntu Server 24.04 LTS**, architecture **64-bit (x86)**.
4. Instance type `t3.medium`.
5. Key pair: **Proceed without a key pair**. (You connect through Session Manager; the deploy key goes in through the first-boot script.)
6. Network settings, click **Edit**:
   - VPC `crm-uat-vpc`
   - Subnet: a **public** subnet in `ap-south-1a` (name contains `public1`)
   - Auto-assign public IP: **Enable**. The server needs internet access during its very first boot to install software; the fixed IP in step 6 replaces this temporary one.
   - Firewall: **Select existing security group**, choose `crm-uat-app`
7. Configure storage: `30` GiB, **gp3**. Expand **Advanced** and set Encrypted to **Yes**.
8. Expand **Advanced details**:
   - IAM instance profile: `crm-uat-app-role`
   - Metadata version: **V2 only (token required)**
   - **User data**: paste the whole content of `crm-uat-user-data.sh`
9. Click **Launch instance**. Open the instance and wait for Status checks to show 2/2 passed (about 3 minutes).

## 6. Fixed public IP

1. EC2, left menu **Elastic IPs**, **Allocate Elastic IP address**, Network border group `ap-south-1`, tag key `Name`, value `crm-uat-ip`, Allocate.
2. Select it, **Actions**, **Associate Elastic IP address**, Resource type **Instance**, choose `crm-uat-app`, Associate.
3. Write the IP down. It is your `DEPLOY_HOST`.
4. Check first boot finished (step 8). If `/var/log/crm-bootstrap.done` is missing and the log shows timeouts, the server had no internet at boot. Re-run it with `sudo bash /var/lib/cloud/instance/scripts/part-001`.

## 7. DNS

At the DNS provider for `alpharupee.in`, add:

| Type | Name | Value |
|---|---|---|
| A | `uat-crm` | the Elastic IP from step 6 |

## 8. Check that the server finished setting itself up

1. EC2, select `crm-uat-app`, **Connect**, tab **Session Manager**, **Connect**. If Connect is greyed out, wait five minutes and refresh; the server needs a few minutes to register.
2. In the terminal:

```
sudo cat /var/log/crm-bootstrap.done
curl -s http://127.0.0.1/healthz
```

Expected: `crm first-boot setup finished` and `ok`. If the first file is missing, wait two minutes and try again. If it never appears, send me the output of `sudo tail -n 40 /var/log/cloud-init-output.log`.

Also check the database: RDS, Databases, `crm-uat-db` should say **Available**. Copy the **Endpoint** (a long name ending in `rds.amazonaws.com`) for reference.

## 9. Continue with the same steps as the scripted guide

From here nothing depends on how the resources were created. Follow `docs/AWS_UAT_SETUP.md`:

- **Step 6** Add the GitHub settings for UAT. `DEPLOY_HOST` is the Elastic IP. To check the server's key, find its fingerprint in the EC2 console: select the instance, Actions, Monitor and troubleshoot, **Get system log**, and find the line `(ED25519)` between `BEGIN SSH HOST KEY FINGERPRINTS` and `END`. Compare it with `ssh-keyscan -t ed25519 <IP> | ssh-keygen -lf -` on your Mac.
- **Step 7** Run the first deploy from GitHub Actions.
- **Step 8** Turn on HTTPS.
- **Step 9** Create the application settings file with `setup-env.sh crm-uat uat-crm.alpharupee.in`.
- **Step 10** Check the end state.

## Quick checklist

- [ ] VPC `crm-uat-vpc` with 2 public and 2 private subnets, no NAT
- [ ] `crm-uat-db` firewall allows 5432 only from `crm-uat-app`
- [ ] Role `crm-uat-app-role` with Session Manager policy and the `read-db-secret` policy
- [ ] RDS `crm-uat-db` Available, public access No, deletion protection on
- [ ] EC2 `crm-uat-app` running, no key pair, role attached, user data pasted
- [ ] Elastic IP attached, DNS A record added
- [ ] Session Manager shows `crm first-boot setup finished` and `ok`

## Deleting UAT later

Delete in this order: EC2 instance (terminate), release the Elastic IP, RDS database (turn off deletion protection first, keep a final snapshot), DB subnet group, the two security groups, the VPC (this removes subnets, route tables and the internet gateway), then the IAM role. Do the database before the VPC, otherwise the VPC cannot be deleted.
