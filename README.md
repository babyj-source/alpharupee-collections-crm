# AlphaRupee Collections CRM

Collections CRM integrating Sensei LMS/LOS, Smartflo, MSG91 and CRIF.
`app/` currently holds the clickable prototype (v3). The real application replaces it later; the pipeline stays the same.

## How code reaches AWS

```
feature branch --PR + review + CI--> main --auto--> UAT
                                       |
                              tag vX.Y.Z (on main)
                                       |
                        approval in GitHub "production" environment
                                       |
                                       v
                                  Production
```

| Event | Workflow | Goes to | Approval |
|---|---|---|---|
| Pull request into `main` | `ci.yml` | nothing (checks only) | 1 review + green CI |
| Merge to `main` | `deploy-uat.yml` | UAT | none |
| Push tag `vX.Y.Z` | `deploy-production.yml` | Production | required reviewers |

Both deploys use the same reusable job (`_deploy.yml`) and the same server script (`deploy/deploy.sh`), so what you test in UAT is the process that runs in Production.

## Day to day

1. `git checkout -b feature/short-name`, commit, push, open a pull request into `main`.
2. After review and green CI, squash-merge. UAT updates by itself; test there.
3. To release: `git checkout main && git pull && git tag v1.0.0 && git push origin v1.0.0`.
4. A reviewer approves the "Deploy to Production" run in the Actions tab. Production updates and a GitHub Release is created.

## Rollback

Each deploy is a numbered folder under `DEPLOY_PATH/releases/`; `current` is a symlink to the live one. A failed health check rolls back automatically. To roll back by hand, on the server run `deploy/rollback.sh --base <DEPLOY_PATH>`, or re-run the deploy of an earlier tag.

## Configuration (set per environment in GitHub, never in code)

| Name | Kind | Example |
|---|---|---|
| `DEPLOY_HOST` | variable | UAT/Production server DNS name or IP |
| `DEPLOY_USER` | variable | `deploy` |
| `DEPLOY_PATH` | variable | `/opt/crm` |
| `HEALTH_URL` | variable | `http://127.0.0.1:8080/health` (empty skips the check) |
| `RESTART_CMD` | variable | `sudo systemctl restart crm` (`none` for static files) |
| `SSH_PRIVATE_KEY` | secret | private key for the deploy user |
| `SSH_KNOWN_HOSTS` | secret | output of `ssh-keyscan` for the server, checked by you |

Full setup steps: `docs/GITHUB_SETUP.md`.

## Rules

- No secrets, customer data or real mobile numbers in the repo.
- Smartflo, MSG91, CRIF and Sensei credentials live on the servers (or a secrets manager), not in GitHub.
- Release tags are protected from deletion and rewriting.
