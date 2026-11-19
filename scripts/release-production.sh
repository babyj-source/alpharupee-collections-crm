#!/usr/bin/env bash
# Run on YOUR computer, in the repo, to release what is on main to Production:
#   bash ~/alpharupee-collections-crm/scripts/release-production.sh v1.0.0
# It checks everything a release needs, shows you what will ship, asks you to type DEPLOY, then creates and pushes the tag.
# GitHub then waits for a reviewer to approve in Actions > Deploy to Production. Nothing reaches Production before that approval.
set -euo pipefail
VER="${1:?usage: release-production.sh vX.Y.Z   (for example v1.0.0)}"
[[ "$VER" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "The version must look like v1.0.0"; exit 1; }
cd "$(dirname "$0")/.."
FULL="${REPO_SLUG:-babyj-source/alpharupee-collections-crm}"
ok() { printf '  ok  %s\n' "$1"; }
die() { printf '  STOP  %s\n' "$1" >&2; exit 1; }

echo "==> Checks"
command -v gh >/dev/null || die "Install the GitHub CLI (gh)."
WHO="$(gh api user --jq .login 2>/dev/null || true)"
[ "$WHO" = "babyj-source" ] || die "The active GitHub account is '$WHO'. Run: gh auth switch -u babyj-source && gh auth setup-git"
ok "GitHub account babyj-source is active"
git fetch --quiet origin main --tags
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "You are not on main. Run: git checkout main && git pull"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "You have uncommitted changes."
SHA="$(git rev-parse HEAD)"
[ "$SHA" = "$(git rev-parse origin/main)" ] || die "Your main differs from GitHub's main. Run: git pull --ff-only"
ok "On main, same as GitHub ($(git rev-parse --short HEAD))"
git rev-parse -q --verify "refs/tags/$VER" >/dev/null && die "Tag $VER already exists."
[ -z "$(git ls-remote --tags origin "refs/tags/$VER")" ] || die "Tag $VER already exists on GitHub."
ok "Tag $VER is free"
CI="$(gh run list -R "$FULL" --workflow ci.yml --commit "$SHA" --json status,conclusion --jq '[.[] | select(.status=="completed")] | map(.conclusion) | first // ""')"
[ "$CI" = "success" ] || die "CI has not passed on this commit (result: '${CI:-none yet}'). Wait for the checks on main, then run again."
ok "CI passed on this commit"
curl -fsS --max-time 15 https://uat-crm.alpharupee.in/api/health >/dev/null || die "UAT's health check failed. Fix UAT first."
ok "UAT is healthy"

H="$(TZ=Asia/Kolkata date +%-H)"
if [ "$H" -ge 8 ] && [ "$H" -lt 19 ]; then
  echo "  NOTE  It is inside calling hours (08:00-19:00 IST). The restart drops open screens for a few seconds."
  read -r -p "  Release now anyway? Type yes: " a; [ "$a" = "yes" ] || { echo "Stopped. Nothing was changed."; exit 1; }
fi

echo
echo "==> What will ship as $VER"
PREV="$(git tag --list 'v[0-9]*' --sort=-v:refname | head -1)"
if [ -n "$PREV" ]; then echo "  since $PREV:"; git log --oneline "$PREV..HEAD" | sed 's/^/    /'; else
  echo "  First Production release: everything on main, up to"; git log --oneline -1 | sed 's/^/    /'; fi
echo
read -r -p "Type DEPLOY $VER to create the tag (anything else stops): " a
[ "$a" = "DEPLOY $VER" ] || { echo "Stopped. Nothing was changed."; exit 1; }
git tag "$VER"
git push origin "$VER"
echo
echo "Tag $VER pushed. Now open: https://github.com/$FULL/actions/workflows/deploy-production.yml"
echo "A reviewer opens the run, clicks Review deployments, ticks production, then Approve and deploy."
echo "When it is green: https://crm.alpharupee.in/api/health should answer ok. If the health check fails, the previous release is restored automatically."
