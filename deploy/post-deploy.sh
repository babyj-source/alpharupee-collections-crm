#!/usr/bin/env bash
# Runs ON the server inside the new release folder, before the "current" link moves.
# Does nothing for the static prototype; builds the Django backend once it exists.
set -euo pipefail

BASE="$(cd ../.. && pwd)"          # /opt/crm  (release = $BASE/releases/<id>)
ENV_FILE="$BASE/shared/.env"

if [ -f backend/manage.py ]; then
  echo "post-deploy: building backend"
  python3 -m venv venv
  venv/bin/pip install --quiet --upgrade pip
  venv/bin/pip install --quiet -r backend/requirements.txt
  [ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE. Run deploy/server/setup-env.sh first." >&2; exit 1; }
  set -a; . "$ENV_FILE"; set +a
  venv/bin/python backend/manage.py migrate --noinput
  venv/bin/python backend/manage.py createcachetable   # shared cache for rate limits; does nothing if it exists
  venv/bin/python backend/manage.py collectstatic --noinput
else
  echo "post-deploy: static release only (no backend yet)"
fi
