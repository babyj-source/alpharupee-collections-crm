#!/usr/bin/env bash
# Run this on YOUR machine, once, after `gh auth login` as an organization owner/admin.
# It creates the repo, pushes this code, protects main, and creates the uat and production environments.
# It never asks for, prints or stores any key or password.
#
# Usage: scripts/github-setup.sh ORG REPO [PRODUCTION_REVIEWER_USERNAME ...]
#   e.g. scripts/github-setup.sh copperhive alpharupee-collections-crm baby-jose neha-head
set -euo pipefail

ORG="${1:?usage: github-setup.sh ORG REPO [REVIEWER ...]}"
REPO="${2:?usage: github-setup.sh ORG REPO [REVIEWER ...]}"
shift 2
REVIEWERS=("$@")
FULL="$ORG/$REPO"

command -v gh >/dev/null  || { echo "Install the GitHub CLI first: https://cli.github.com"; exit 1; }
command -v git >/dev/null || { echo "Install git first"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run: gh auth login   (then re-run this script)"; exit 1; }

cd "$(dirname "$0")/.."

echo "==> 1. Create private repository $FULL"
if gh repo view "$FULL" >/dev/null 2>&1; then
  echo "    already exists, reusing"
else
  gh repo create "$FULL" --private --description "AlphaRupee Collections CRM"
fi

echo "==> 2. Push the code (branch: main)"
[ -d .git ] || git init -b main
git checkout -B main >/dev/null 2>&1
git add -A
git diff --cached --quiet || git commit -m "Initial commit: CRM prototype, CI and deploy pipeline"
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$FULL.git"
gh auth setup-git >/dev/null 2>&1 || true
# If the repo was created with a README/licence, bring that history in first so the push is accepted.
if git ls-remote --exit-code --heads origin main >/dev/null 2>&1; then
  git pull --no-rebase --allow-unrelated-histories -X ours --no-edit origin main || true
fi
git push -u origin main

warn() { echo "    !! $1 failed. Likely needs a paid GitHub plan or an org. Set it by hand: $2"; }

echo "==> 3. Repository settings"
gh api -X PATCH "repos/$FULL" \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F has_wiki=false >/dev/null \
  || warn "Repository settings" "Settings > General"

echo "==> 4. Protect main (pull request + passing CI + no force push)"
gh api -X PUT "repos/$FULL/branches/main/protection" --input - >/dev/null <<JSON || warn "Branch protection" "Settings > Branches > Add rule for main"
{
  "required_status_checks": { "strict": true, "contexts": ["build-and-test"] },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

echo "==> 5. Environments"
gh api -X PUT "repos/$FULL/environments/uat" >/dev/null \
  && echo "    uat: created (no approval, deploys on every merge to main)" \
  || warn "Environment uat" "Settings > Environments > New environment"

REV_JSON=""
for u in "${REVIEWERS[@]}"; do
  id="$(gh api "users/$u" --jq .id)"
  REV_JSON="$REV_JSON{\"type\":\"User\",\"id\":$id},"
done
if [ -n "$REV_JSON" ]; then
  gh api -X PUT "repos/$FULL/environments/production" --input - >/dev/null <<JSON \
    && echo "    production: created with required reviewers: ${REVIEWERS[*]}" \
    || warn "Environment production with reviewers" "Settings > Environments > production > Required reviewers"
{ "reviewers": [${REV_JSON%,}], "prevent_self_review": false, "deployment_branch_policy": null }
JSON
else
  gh api -X PUT "repos/$FULL/environments/production" >/dev/null \
    && echo "    production: created WITHOUT reviewers; add them in Settings > Environments > production" \
    || warn "Environment production" "Settings > Environments > New environment"
fi

echo "==> 6. Tag protection for release tags (v*)"
gh api -X POST "repos/$FULL/rulesets" --input - >/dev/null <<JSON || warn "Tag ruleset" "Settings > Rules > Rulesets > New tag ruleset (pattern v*, block deletion and updates)"
{
  "name": "Release tags are protected",
  "target": "tag",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/tags/v*"], "exclude": [] } },
  "rules": [ { "type": "deletion" }, { "type": "non_fast_forward" }, { "type": "update" } ]
}
JSON

cat <<MSG

Finished. Repository: https://github.com/$FULL
Any line above starting with "!!" needs doing by hand in GitHub.

Still to do by hand (these hold secrets, so the script does not touch them):
  For each environment (uat, production):  Settings > Environments > <name>
    Variables:  DEPLOY_HOST  DEPLOY_USER  DEPLOY_PATH  HEALTH_URL  RESTART_CMD
    Secrets:    SSH_PRIVATE_KEY  SSH_KNOWN_HOSTS
  Also: Settings > Actions > General > Workflow permissions = "Read repository contents"
MSG
