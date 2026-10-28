#!/usr/bin/env bash
# One-time database setup for the compose stack: creates the tables as the owner role, then the
# least-privilege `dsoreg_app` role (deploy/postgres_roles.sql). Safe to run again: it stops early
# if the roles already exist.
#
#   bash deploy/docker/setup.sh        (from the repository root)
set -euo pipefail
cd "$(dirname "$0")/../.."

[ -f .env ] || { echo "no .env: copy deploy/docker/env.example to .env and fill it in" >&2; exit 1; }
# read just the two passwords (a plain `source` would choke on HSTS_VALUE, which contains a ';')
env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//'; }
DB_OWNER_PASSWORD=$(env_value DB_OWNER_PASSWORD)
DB_APP_PASSWORD=$(env_value DB_APP_PASSWORD)
: "${DB_OWNER_PASSWORD:?set in .env}" "${DB_APP_PASSWORD:?set in .env}"

docker compose up -d db
until docker compose exec -T db pg_isready -U dsoreg_owner -d dsoreg >/dev/null 2>&1; do sleep 1; done

psql() { docker compose exec -T db psql -v ON_ERROR_STOP=1 -U dsoreg_owner -d dsoreg "$@"; }

if [ "$(psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'dsoreg_app'")" = "1" ]; then
  echo "dsoreg_app already exists: database is set up"
  exit 0
fi

# the API's own image creates the tables, connecting as the owner
docker compose run --rm -e "DATABASE_URL=postgresql+psycopg://dsoreg_owner:${DB_OWNER_PASSWORD}@db:5432/dsoreg" \
  backend python -m dsoreg.cli init-db

sed "s/CHANGE-ME-LONG-RANDOM/${DB_APP_PASSWORD}/" deploy/postgres_roles.sql | psql -q
echo "database ready. Next: docker compose up -d --build"
