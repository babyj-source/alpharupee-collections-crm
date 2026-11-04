# GitHub setup, step by step

Do this once. Steps 1 to 3 need an organization owner; the rest needs repo admin.

## 1. Organization and teams
1. Create or choose the GitHub organization (github.com/organizations/plan). Turn on two-factor authentication for all members: Org settings > Authentication security.
2. Create two teams: `crm-maintainers` (people who review code) and `crm-admins` (people who change pipelines and deploy scripts).
3. Edit `.github/CODEOWNERS` and replace `@ORG/...` with the real team names.

## 2. Create the repository and apply the rules
On your own computer (with git and the GitHub CLI installed):

```
gh auth login            # sign in as an organization owner; follow the browser prompts
cd alpharupee-collections-crm
scripts/github-setup.sh <ORG> <REPO> <reviewer1> <reviewer2>
```

The reviewer names are the GitHub usernames who must approve a Production deployment. The script:
- creates a private repo and pushes `main`
- allows squash merges only, deletes merged branches
- protects `main`: pull request, 1 approval, CI check `build-and-test` must pass, no force-push, no deletion
- creates the `uat` and `production` environments (production with required reviewers)
- protects `v*` tags from deletion or rewrite

If you prefer clicking, the same settings are under Settings > Branches, Environments and Rules.

## 3. Create the deploy key (on your machine, not in chat)
One key pair per environment, so UAT's key cannot reach Production.

```
ssh-keygen -t ed25519 -C "crm-deploy-uat" -f crm_deploy_uat -N ""
ssh-keygen -t ed25519 -C "crm-deploy-prod" -f crm_deploy_prod -N ""
```

- `*.pub` goes on the server, in `/home/deploy/.ssh/authorized_keys` (AWS step, later).
- The private file (no `.pub`) goes into GitHub as the `SSH_PRIVATE_KEY` secret of the matching environment. Delete the local copy afterwards.

## 4. Add variables and secrets
Settings > Environments > `uat`, then again for `production`:

| Where | Name | Value |
|---|---|---|
| Variable | `DEPLOY_HOST` | server DNS or IP |
| Variable | `DEPLOY_USER` | `deploy` |
| Variable | `DEPLOY_PATH` | `/opt/crm` |
| Variable | `HEALTH_URL` | optional |
| Variable | `RESTART_CMD` | `sudo systemctl restart crm`, or `none` |
| Secret | `SSH_PRIVATE_KEY` | contents of the private key file |
| Secret | `SSH_KNOWN_HOSTS` | see below |

To get `SSH_KNOWN_HOSTS`, run `ssh-keyscan -t ed25519 <host>` once the server exists, and compare the fingerprint with the one shown by `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server before saving it. This is what stops someone impersonating your server.

Also set Settings > Actions > General > Workflow permissions to "Read repository contents".

## 5. First run
1. Open a small pull request (for example, edit the README). CI runs; get a review; merge.
2. "Deploy to UAT" runs. It will fail with a clear message until the variables above exist and the server is ready; that is expected before the AWS step.
3. When UAT works, tag `v0.1.0` and approve the Production run.

## SSH hardening (because we deploy over SSH)
- Dedicated `deploy` user on each server, not `ec2-user` or root. It owns only `DEPLOY_PATH`; allow just the restart command in sudoers (`deploy ALL=(root) NOPASSWD: /bin/systemctl restart crm`).
- GitHub-hosted runners use a wide, changing IP range, so SSH (port 22) has to accept those ranges. Keep password login off, keys only, and consider a non-default port plus `fail2ban`. If this is not acceptable to Security, switch to the OIDC + AWS SSM option or a self-hosted runner inside the VPC; the workflow structure stays the same and only the "Upload and deploy" step changes.
- Rotate the deploy keys every 6 months or when someone with access leaves.
- Production reviewers should not be the person who merged the change.
- The database server accepts connections only from the application server's security group; the pipeline never touches the database. Schema changes go through a reviewed migration step.
