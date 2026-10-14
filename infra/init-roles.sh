#!/usr/bin/env bash
set -euo pipefail
# Passwords stay in environment/stdin, never psql secret arguments.
app_password=${APP_DB_PASSWORD//\'/\'\'}
migration_password=${MIGRATION_DB_PASSWORD//\'/\'\'}
worker_password=${WORKER_DB_PASSWORD:?Set dedicated Worker database password}
worker_password=${worker_password//\'/\'\'}
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -f /docker-entrypoint-initdb.d/runtime-roles.sql >/dev/null
{
  printf "ALTER ROLE itemtalk_app LOGIN PASSWORD '%s';\n" "$app_password"
  printf "ALTER ROLE itemtalk_migrator LOGIN PASSWORD '%s';\n" "$migration_password"
  printf "ALTER ROLE itemtalk_worker LOGIN PASSWORD '%s';\n" "$worker_password"
  printf '%s\n' \
    'GRANT CONNECT ON DATABASE itemtalk TO itemtalk_app, itemtalk_worker, itemtalk_migrator;' \
    'GRANT USAGE, CREATE ON SCHEMA public TO itemtalk_migrator;' \
    'GRANT USAGE ON SCHEMA public TO itemtalk_app;' \
    'ALTER DEFAULT PRIVILEGES FOR ROLE itemtalk_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO itemtalk_app;' \
    'ALTER DEFAULT PRIVILEGES FOR ROLE itemtalk_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO itemtalk_app;'
} | psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" >/dev/null
