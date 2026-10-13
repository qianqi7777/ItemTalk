#!/usr/bin/env bash
# 只在受限服务器文件加载环境，凭据不出现在参数/日志中。
set -euo pipefail
umask 077
base=/opt/itemtalk
release=$(readlink -f "$base/current")
[[ -f $release/.env.image_tag ]] || { echo 'No active release'; exit 1; }
mkdir -p "$base/backups"
target=$base/backups/daily-$(date -u +%Y%m%dT%H%M%SZ).dump
docker compose --project-name itemtalk --env-file "$base/.env.production" \
  --env-file "$release/.env.image_tag" -f "$release/docker-compose.yml" \
  exec -T db pg_dump -U itemtalk_admin -d itemtalk -Fc > "$target"
[[ -s $target ]] || { echo 'Backup empty'; exit 1; }
docker compose --project-name itemtalk --env-file "$base/.env.production" \
  --env-file "$release/.env.image_tag" -f "$release/docker-compose.yml" \
  exec -T db pg_restore --list < "$target" >/dev/null
docker compose --project-name itemtalk --env-file "$base/.env.production" \
  --env-file "$release/.env.image_tag" -f "$release/docker-compose.yml" \
  exec -T api tar -czf - -C /app/.local-data/assets . > "$target.assets.tar.gz"
tar -tzf "$target.assets.tar.gz" >/dev/null
sha256sum "$target" "$target.assets.tar.gz" > "$target.sha256"
echo 'Backup created and archive contents verified; restore drill still required'
