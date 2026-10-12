#!/usr/bin/env bash
set -euo pipefail
# Passwords stay in environment/stdin, never psql secret arguments.
app_password=${APP_DB_PASSWORD//\'/\'\'}
migration_password=${MIGRATION_DB_PASSWORD//\'/\'\'}
{
  printf "CREATE ROLE itemtalk_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '%s';\n" "$app_password"
  printf "CREATE ROLE itemtalk_migrator LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '%s';\n" "$migration_password"
  printf '%s\n' \
    'GRANT CONNECT ON DATABASE itemtalk TO itemtalk_app, itemtalk_migrator;' \
    'GRANT USAGE, CREATE ON SCHEMA public TO itemtalk_migrator;' \
    'GRANT USAGE ON SCHEMA public TO itemtalk_app;' \
    'ALTER DEFAULT PRIVILEGES FOR ROLE itemtalk_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO itemtalk_app;' \
    'ALTER DEFAULT PRIVILEGES FOR ROLE itemtalk_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO itemtalk_app;'
} | psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" >/dev/null
