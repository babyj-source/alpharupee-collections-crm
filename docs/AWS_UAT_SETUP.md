# AWS UAT setup, step by step

Result: a UAT server (Ubuntu 24.04, nginx) and a separate private PostgreSQL database in AWS Mumbai (`ap-south-1`).
Every merge to `main` then deploys to it through GitHub.

No CloudShell or CLI? Use `docs/AWS_UAT_MANUAL.md`, which builds the same thing by clicking in the Console.

Time: about 40 minutes, of which about 12 are waiting for AWS.

| Resource | Setting |
|---|---|
| Application server | EC2 `t3.medium`, 30 GB encrypted disk, fixed IP, Session Manager access (no SSH password, no ec2 key pair) |
| Database | RDS PostgreSQL 17, `db.t4g.medium`, 50 GB, encrypted, private subnets, 7-day backups, deletion protection; password managed by AWS |
| Network | Own VPC `10.20.0.0/16`. Database accepts connections only from the application server |
| Access | Ports 80/443 to everyone (narrow it in `env/uat.env` if UAT should be private), port 22 key-only for the `deploy` user |

These sizes are lighter than Production. Prices are not confirmed (see the cost notes in the architecture deck).

## 1. Make the deploy keys (on your computer)

You may already have done this from `docs/GITHUB_SETUP.md` step 3. If not:

```
ssh-keygen -t ed25519 -C "crm-deploy-uat" -f crm_deploy_uat -N ""
```

You get `crm_deploy_uat` (private) and `crm_deploy_uat.pub` (public).
Never paste the private file anywhere except the GitHub secret in step 6.

## 2. Open AWS CloudShell

1. Sign in to the AWS Console with an admin user (one that can create VPC, EC2, RDS and IAM roles).
2. Set the region at the top right to **Asia Pacific (Mumbai) ap-south-1**.
3. Click the `>_` CloudShell icon in the top bar and wait for the prompt.

## 3. Get the code into CloudShell and build UAT

In CloudShell, upload `crm_deploy_uat.pub`: Actions menu, Upload file.
Then fetch the repo. It is private, so use a token that can read it (create at GitHub, Settings, Developer settings, Fine-grained tokens, read-only on this repo). Type it when asked; it does not appear on screen:

```
read -s -p "GitHub token: " GH_TOKEN; echo
git clone https://babyj-source:${GH_TOKEN}@github.com/babyj-source/alpharupee-collections-crm.git
unset GH_TOKEN
cd alpharupee-collections-crm
cp ~/crm_deploy_uat.pub .
```

Check the settings, especially `DOMAIN`:

```
cat infra/aws/env/uat.env
```

Build everything:

```
bash infra/aws/create-env.sh uat
```

It prints each resource as it creates it and ends with the server IP. If it stops with an error, read the message, fix the cause and run the same command again; it reuses what already exists.

## 4. Add the DNS record

At your DNS provider for `alpharupee.in`, add:

| Type | Name | Value |
|---|---|---|
| A | `uat-crm` | the server IP printed at the end |

Then print the values you need next:

```
bash infra/aws/show-env.sh uat
```

## 5. Check the server is ready

Open the AWS Console, EC2, Instances, select `crm-uat-app`, click **Connect**, tab **Session Manager**, **Connect**. In the terminal that opens:

```
sudo tail -n 3 /var/log/crm-bootstrap.done
curl -s http://127.0.0.1/healthz
```

You should see `crm first-boot setup finished` and `ok`. If the first file does not exist yet, wait two minutes; first boot installs packages.

## 6. Add the GitHub settings for UAT

In GitHub, repo `alpharupee-collections-crm`, Settings, Environments, `uat`.

Variables (from `show-env.sh`):

| Name | Value |
|---|---|
| `DEPLOY_HOST` | server IP |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_PATH` | `/opt/crm` |
| `HEALTH_URL` | `http://127.0.0.1/healthz` until step 8, then `https://<your domain>/healthz` (or `/api/health` once the backend is deployed) |
| `RESTART_CMD` | `sudo systemctl restart crm` |

Secrets:

| Name | How to get it |
|---|---|
| `SSH_PRIVATE_KEY` | open `crm_deploy_uat` in a text editor and paste the whole content, including the BEGIN and END lines |
| `SSH_KNOWN_HOSTS` | on your computer run `ssh-keyscan -t ed25519 <server IP>` and paste the single output line |

Before saving `SSH_KNOWN_HOSTS`, check the key is really your server's. `show-env.sh` prints the server's own ed25519 fingerprint (`SHA256:...`), recorded by the server at first boot. On your computer run:

```
ssh-keyscan -t ed25519 <server IP> | ssh-keygen -lf -
```

The `SHA256:...` value must be identical. If it differs, stop and do not save the secret; this check is what stops anyone impersonating the server.

## 7. Run the first deploy

In GitHub, Actions, **Deploy to UAT**, **Run workflow**, branch `main`.
It packages the prototype, uploads it over SSH, switches the release and checks `/healthz`.

Then open `http://<server IP>/`. You should see the CRM prototype.

## 8. Turn on HTTPS

Only after the DNS record resolves (test with `nslookup uat-crm.alpharupee.in`). In the Session Manager terminal:

```
sudo bash /opt/crm/current/server-tools/enable-https.sh uat-crm.alpharupee.in baby.j@alpharupee.in
```

Then open `https://uat-crm.alpharupee.in/`. OTP login must only be used over HTTPS.

HTTPS turns port 80 into a redirect that answers 404 to `127.0.0.1`, so now change the GitHub `uat` variable `HEALTH_URL` to `https://uat-crm.alpharupee.in/healthz`, or the next deploy fails its health check and rolls back.

## 9. Create the application settings file (secrets, typed on the server)

Still in Session Manager:

```
sudo -iu deploy
bash /opt/crm/current/server-tools/setup-env.sh crm-uat uat-crm.alpharupee.in
```

It reads the database password from AWS without showing it, creates the application's own database user, and asks you with hidden input for the MSG91 key, the OTP template id, the Smartflo token and the LMS replica details. Press Enter to skip any you do not have yet; run it again later to fill them in. Nothing is sent to chat or GitHub.

## 10. Check the end state

- `https://uat-crm.alpharupee.in/` loads the page over HTTPS.
- Merge a small pull request to `main`; Actions shows **Deploy to UAT** green and the page updates.
- In RDS, `crm-uat-db` is Available and Publicly accessible is **No**.

## Notes and limits

- **LMS replica access.** The CRM must be able to reach the LMS replica on the network. If it lives in another VPC or on-premises, a VPC peering or VPN is needed. Tell me where it is and I will add it to the scripts.
- **Single server, single database.** UAT has no high availability. Production adds a second server, a load balancer and Multi-AZ (see the architecture deck).
- **Deletion protection** is on for the database. `infra/aws/destroy-env.sh uat` turns it off and takes a final snapshot before deleting.
- **Rollback.** On a failed health check the previous release is restored automatically. Manual, in a Session Manager terminal: `sudo -iu deploy bash /opt/crm/current/server-tools/rollback.sh`.
- **Database changes** run during deploy before the switch, so every migration must be backward compatible with the previous release.
- **Not yet verified against a real AWS account.** I tested the deploy logic and the server bootstrap generation locally, but the AWS CLI calls have never run. Expect to fix one or two small things on the first run; paste the error and I will correct the script.
