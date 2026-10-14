#!/usr/bin/env bash
# 只在受限服务器文件加载环境，凭据不出现在参数/日志中。
set -euo pipefail
umask 077
base=/opt/itemtalk
# 部署持有独占锁；定时备份共享部署锁，且不允许自身并行启动。
exec 9>"$base/.deploy.lock"
flock -s -n 9 || { echo 'Deployment active; backup not started'; exit 1; }
exec 8>"$base/.backup.lock"
flock -n 8 || { echo 'Backup already running'; exit 1; }
release=$(readlink -f "$base/current")
[[ -f $release/.env.image_tag ]] || { echo 'No active release'; exit 1; }
compose() {
  docker compose --project-name itemtalk --env-file "$base/.env.production" \
    --env-file "$release/.env.image_tag" -f "$release/docker-compose.yml" "$@"
}
storage=$(compose exec -T api python - <<'PY'
from app.config import settings
try:
    backend = settings().storage_backend
    if backend not in {"local", "cos"}:
        raise ValueError
    print(backend)
except Exception:
    raise SystemExit("Backup configuration unavailable; details suppressed") from None
PY
)
if [[ $storage == cos ]]; then
  # 只启动独立运维容器；不自动启动、重建或暂停DB/API/Worker。
  compose --profile backup-tools run --rm --no-deps backup
  exit 0
fi
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
