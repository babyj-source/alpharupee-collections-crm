# AWS Production setup, step by step

Result: one Production application server and one private PostgreSQL database server in AWS Mumbai (`ap-south-1`), reachable at `https://crm.alpharupee.in`. A version tag such as `v1.0.0` deploys to it after a named approver clicks Approve in GitHub.

It follows `docs/AWS_UAT_SETUP.md` (same scripts) with three differences: bigger servers and safety features, HTTPS and the settings file **before** the first deploy, and an approval step in GitHub.

**No CloudShell, or AWS refuses to open it?** Build the servers by clicking in the Console with `docs/AWS_PRODUCTION_MANUAL.md` instead. It replaces steps 1 to 4 below and sends you back here at step 5.

Time: about 90 minutes, of which about 15 are waiting for AWS. Do it outside calling hours the first time; agents do not use it until the checklist in step 13 is done.

| Item | Production setting |
|---|---|
| Application server | One EC2 `m6i.large` (2 vCPU, 8 GB), 50 GB encrypted disk, fixed Elastic IP, Session Manager access, termination protection on, daily disk snapshot (kept 14 days) |
| Database server | One RDS PostgreSQL 17 `db.m6g.large` (2 vCPU, 8 GB), single zone, 100 GB that grows by itself up to 500 GB, encrypted, private, deletion protection, 14-day backups with point-in-time restore |
| Network | Own VPC `10.30.0.0/16`, separate from UAT. The database accepts connections only from the application server |
| Access | Ports 80/443 open to the internet (OTP login), port 22 key-only for the `deploy` user. Server IP is fixed, so the LMS, Smartflo, MSG91 and Easebuzz can allow-list it |
| Web service | nginx, then gunicorn with 3 processes x 4 threads, a worker restarts after about 1000 requests, web queries cancelled after 60 s |
| Shared cache | Rate limits and the dialler's one-at-a-time lock live in the database, so all workers agree (no Redis) |
| Database safety | Only TLS connections, the certificate is verified (`verify-full`), slow queries (over 0.5 s) logged, idle transactions closed after 60 s, `pg_stat_statements` and Performance Insights on |
| Alarms (e-mail) | Server hardware or reachability fails (hardware failure triggers an automatic restart), server CPU, database CPU, free storage, free memory, connections |
| Deploy | Tag `vX.Y.Z` on `main` with CI green; the `production` environment needs a reviewer's approval; automatic rollback if the health check fails; one-click manual rollback workflow |

What a single server means in practice: if the server or database fails, the site is down until AWS restarts it or you restore it (minutes for a hardware problem, longer for a restore). Two settings reduce the risk: the server restarts itself on hardware failure, and `DB_MULTI_AZ=true` in `infra/aws/env/production.env` gives the database an automatic standby in a second zone (about doubles the database cost). You can switch that on later.

## 1. Make the Production deploy key (on your computer)

Use a key that is not the UAT one.

```
ssh-keygen -t ed25519 -C "crm-deploy-prod" -f ~/.ssh/crm_deploy_prod -N ""
```

You get `~/.ssh/crm_deploy_prod` (private) and `~/.ssh/crm_deploy_prod.pub` (public). The private file is only ever sent to GitHub, by the script in step 8.

## 2. Open AWS CloudShell

1. Sign in to the AWS Console (same account as UAT) with an admin user.
2. Set the region at the top right to **Asia Pacific (Mumbai) ap-south-1**.
3. Click the `>_` CloudShell icon and wait for the prompt.

## 3. Get the code into CloudShell and build Production

Upload `crm_deploy_prod.pub` to CloudShell (Actions menu, Upload file). Then:

```
read -s -p "GitHub token: " GH_TOKEN; echo
git clone https://babyj-source:${GH_TOKEN}@github.com/babyj-source/alpharupee-collections-crm.git
unset GH_TOKEN
cd alpharupee-collections-crm
cp ~/crm_deploy_prod.pub .
```

Look at the settings and change the e-mail for alarms if the right person is someone else:

```
cat infra/aws/env/production.env
```

Check `DOMAIN` (`crm.alpharupee.in`) and `ALERT_EMAIL`. Then build:

```
bash infra/aws/create-env.sh production
```

It prints each resource as it is made and ends with the server IP, after about 15 minutes. If it stops with an error, read the message, fix the cause and run the same command again; it reuses what already exists.

Write down the **server IP** it prints. This is your fixed Production IP.

## 4. Add the DNS record and confirm the alarm e-mail

At your DNS provider for `alpharupee.in`:

| Type | Name | Value |
|---|---|---|
| A | `crm` | the server IP |

AWS also sent a "Confirm subscription" e-mail to the alarm address. Click the link in it, or no alarm will ever reach you.

Print the values you need next:

```
bash infra/aws/show-env.sh production
```

## 5. Check the server is ready

AWS Console, EC2, Instances, select `crm-prod-app`, **Connect**, tab **Session Manager**, **Connect**. In the terminal:

```
sudo cat /var/log/crm-bootstrap.done
curl -s http://127.0.0.1/healthz
```

You should see `crm first-boot setup finished` and `ok`. If the first file is missing, wait two minutes; first boot installs packages.

## 6. Copy the helper scripts to the server (one time)

The helper scripts normally arrive with a deploy, but the first deploy needs the settings file and HTTPS first. So copy three scripts over once from your computer, in your copy of the repo (replace `<IP>`):

```
ssh -i ~/.ssh/crm_deploy_prod deploy@<IP> "mkdir -p /opt/crm/shared/tools"
```

```
scp -i ~/.ssh/crm_deploy_prod deploy/server/setup-env.sh deploy/server/set-lms.sh deploy/server/enable-https.sh deploy@<IP>:/opt/crm/shared/tools/
```

The first time, SSH asks you to trust the server. Compare the fingerprint with the ed25519 one `show-env.sh` printed, then answer `yes`.

## 7. Turn on HTTPS and create the settings file (before the first deploy)

Only after the DNS record resolves (`nslookup crm.alpharupee.in` shows the server IP). In the Session Manager terminal, one command per line (do not paste several lines at once):

```
sudo bash /opt/crm/shared/tools/enable-https.sh crm.alpharupee.in baby.j@alpharupee.in
```

Open `https://crm.alpharupee.in/`; you should see the "Server ready" placeholder over HTTPS. Then:

```
sudo -iu deploy
```

```
bash /opt/crm/shared/tools/setup-env.sh crm-prod crm.alpharupee.in production
```

It reads the database password from AWS without showing it, downloads the AWS database certificate, creates the application's own database user, and asks with hidden input for the **Production** MSG91 auth key, the OTP template id and the Smartflo token. Press Enter to skip any you do not have; run it again later. It also copies the LMS read-only login from AWS Secrets Manager (`alpharupee/lms/readonlyuser`); if that secret is missing it says so and you run `set-lms.sh` later (step 12).

The result is `/opt/crm/shared/.env`, readable only by the deploy user. Do not copy values from the UAT file: Production uses its own keys and its own secret key.

## 8. Set up GitHub for Production

On your computer, signed in with `gh auth login`:

```
scripts/github-production.sh babyj-source/alpharupee-collections-crm <IP> ~/.ssh/crm_deploy_prod baby-jose
```

Replace `baby-jose` with the GitHub username(s) who must approve each Production release (add more usernames at the end; at least one is required). The script:

1. shows the server's SSH fingerprint and waits for you to confirm it equals the one from `show-env.sh`
2. makes the `production` environment need those reviewers, and only lets tags `v*` and `main` deploy to it
3. saves `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`, `HEALTH_URL` and `RESTART_CMD` as variables
4. sends the private key and the server's host key to GitHub as secrets, without printing them

Check in GitHub, Settings, Environments, `production`: Required reviewers lists the right people, and Variables and Secrets are filled in.

Branch protection and the `v*` tag protection came from `scripts/github-setup.sh` earlier. If the repository is on a plan without environment protection rules, GitHub refuses the reviewer step; then the approval gate does not exist, and you should either upgrade or treat the tag itself as the control (only tag after a colleague agrees).

## 9. The first Production release

1. Merge this change to `main`. UAT deploys automatically; confirm UAT still works.
2. Wait until CI on `main` is green.
3. Create the tag:

```
git checkout main && git pull
git tag v1.0.0
git push origin v1.0.0
```

4. In GitHub, Actions, **Deploy to Production**: the first job checks the tag is on `main`, that CI passed on that commit, and warns if it is inside 08:00-19:00 IST. Then it waits for approval. A reviewer opens the run, clicks **Review deployments**, ticks `production`, **Approve and deploy**.
5. The deploy builds the release, runs the database migrations, restarts the service and checks `https://crm.alpharupee.in/api/health`. If that check fails, the previous release is restored automatically.

When it is green, create the first administrator. In Session Manager:

```
sudo -iu deploy
```

```
set -a; . /opt/crm/shared/.env; set +a
```

```
cd /opt/crm/current/backend && ../venv/bin/python manage.py create_crm_user admin@alpharupee.in --name "Admin Name" --mobile 91XXXXXXXXXX --role admin --must-change
```

Then sign in at `https://crm.alpharupee.in` with that e-mail; the OTP goes to that mobile through MSG91.

## 10. Check the servers are healthy

- `https://crm.alpharupee.in/` loads over HTTPS and OTP login works.
- RDS, `crm-prod-db`: Available, Publicly accessible **No**, Encryption on, Deletion protection on.
- CloudWatch, Alarms: seven `crm-prod-` alarms, none in "Insufficient data" for more than an hour.
- EC2, Instance, Actions, Instance settings: termination protection is on. Data Lifecycle Manager shows the `crm-prod daily snapshots` policy.
- On the server, `sudo -iu deploy`, then `sudo systemctl status crm` is active, and `sudo journalctl -u crm -n 20` shows no errors.

## 11. Raise HTTPS strictness after a clean week

HSTS starts at one day so a mistake can be undone. After a week of normal use, set it to one year. On the server as the deploy user, change `DJANGO_HSTS_SECONDS=86400` to `DJANGO_HSTS_SECONDS=31536000` in `/opt/crm/shared/.env` (for example with `sed -i`), then `sudo systemctl restart crm`.

## 12. Connect the integrations (Production values only)

Each has its own switch; nothing is dialled, sent or charged until you turn it on. Run these on the server as the deploy user.

| Integration | Commands | Notes |
|---|---|---|
| LMS (read-only) | `bash /opt/crm/current/server-tools/set-lms.sh` | First ask the LMS team to allow the Production server IP (from step 3) to reach the replica on its database port. |
| Smartflo | `bash /opt/crm/current/server-tools/set-smartflo.sh on`, then `set-smartflo.sh caller <number>` | Use the Production token. Ask Smartflo whether they restrict API access by IP; if yes, give them the Production IP. |
| SMS | `bash /opt/crm/current/server-tools/set-sms.sh on` | Reuses `MSG91_AUTHKEY`. Check the templates are approved for the Production sender. |
| WhatsApp | `set-whatsapp.sh number <n>`, `set-whatsapp.sh token`, `set-whatsapp.sh on` | `token` prints the webhook address to register in MSG91 for `crm.alpharupee.in`. |
| Easebuzz | `set-easebuzz.sh key`, `set-easebuzz.sh production`, `set-easebuzz.sh token`, `set-easebuzz.sh on` | Production merchant key and salt. `token` prints the webhook address to register in the Easebuzz dashboard. |

Scheduled jobs (once, with sudo; each one is a systemd timer on this server):

```
sudo bash /opt/crm/current/server-tools/install-payments-timer.sh
sudo bash /opt/crm/current/server-tools/install-whatsapp-auto-timer.sh
sudo bash /opt/crm/current/server-tools/install-sync-timer.sh
```

Install the payments timer after Easebuzz is on. Install the WhatsApp timer only when you want automatic reminders. Install the LMS sync timer last, only after the LMS allow-list works and the pre-go-live items below are done.

## 13. Before agents use it: go-live checklist

- [ ] **LMS sync updates only changed rows.** Today it rewrites every account each run (about 15 minutes apart), which keeps the database busy and overwrites mobile numbers the team corrected in the CRM. This is a code change still to be made before the sync timer is switched on.
- [ ] **Restore test.** RDS, `crm-prod-db`, Actions, Restore to point in time, into a new instance named `crm-prod-db-restore-test` in the same VPC settings; confirm the data is there; delete the test instance.
- [ ] **Alarm test.** In CloudWatch open any `crm-prod-` alarm, Actions, set state to ALARM, and confirm the e-mail arrives.
- [ ] **Rollback test.** Make a harmless second release, then run **Roll back Production** (step 15) once in a quiet hour.
- [ ] **Separate secrets.** The Smartflo, MSG91 and Easebuzz keys in `/opt/crm/shared/.env` are the Production ones, not UAT's.
- [ ] **Retention.** Decide how long to keep audit and call-activity records; the tables grow every day and nothing deletes them yet.
- [ ] **Smartflo UAT checks done.** The dialler outcome and queue checks from UAT pass before agents rely on them.
- [ ] **Terms.** DLT, WhatsApp and Easebuzz approvals are in place for the Production sender and domain.

## 14. Releasing after go-live

1. Merge pull requests to `main` as usual (UAT updates).
2. When UAT is checked, tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. Prefer after 19:00 IST. The restart drops open screens for a few seconds.
4. The approver reviews the run and approves.

Database changes run during the deploy, before the switch, so every migration must work with the previous release too (add columns before using them, drop them in a later release).

## 15. Rolling back

If a release is bad after it passed the health check: GitHub, Actions, **Roll back Production**, Run workflow, type `ROLLBACK`, a reviewer approves. It puts the service back on the previous release and checks health.

Rollback is for code only; it does not undo database migrations. Migrations are written to be compatible with the release before, so that is normally safe. For data damage (a wrong bulk change, deleted rows), use RDS point-in-time restore into a new instance, check the data, and then either copy the rows back or point the server at the restored database.

You can also roll back by hand in Session Manager: `sudo -iu deploy bash /opt/crm/current/server-tools/rollback.sh`.

## 16. Switching on automatic database failover later

Edit `DB_MULTI_AZ=true` in `infra/aws/env/production.env`, then in the RDS console choose `crm-prod-db`, Modify, tick **Multi-AZ**, apply during the Sunday window. The script only sets this at creation; the console change works on the running database.

## Notes and limits

- **Cost.** The architecture deck estimated about $300 a month (about Rs 27,000) for this layout, and a one-year commitment on the servers saves about a third. That figure was not checked against AWS's current price list; confirm with the AWS Pricing Calculator for `ap-south-1`.
- **Deploy key.** The private deploy key lives only in your file and the GitHub secret. Rotate it by making a new key, replacing the public key in `/home/deploy/.ssh/authorized_keys` through Session Manager, and re-running step 8.
- **UAT is unchanged.** The new gunicorn settings come with newly built servers; UAT keeps its current service file. UAT does pick up the shared cache on its next deploy. To give UAT the new service file too, copy `deploy/server/crm.service` to `/etc/systemd/system/crm.service` and run `sudo systemctl daemon-reload && sudo systemctl restart crm`.
- **Not yet verified against a real AWS account.** The server and deploy logic were tested locally, but the AWS commands (database parameter group, alarms, snapshot policy, access policy) have never run against your account, nor have the GitHub API calls in `github-production.sh`. Expect to fix one or two small things on the first run; paste the error and the script can be corrected.
- **Evening CI.** Some WhatsApp tests only passed inside the 08:00-19:00 IST calling window (they failed when run in the evening). That is fixed in this change; without the fix, a merge between 19:00 and 08:00 IST turned CI red, and Production tags need green CI.
