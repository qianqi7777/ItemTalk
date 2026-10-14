#!/usr/bin/env bash
# 仅安装/启用方案；凭据由Compose读取受限文件，不source、不打印其值。
set -euo pipefail
umask 077
base=/opt/itemtalk
mode=${1:-daily}
[[ $mode == daily || $mode == weekly ]] || { echo 'Unknown backup mode'; exit 1; }
# 部署持有独占锁；定时备份共享部署锁，且不允许自身并行启动。
exec 9>"$base/.deploy.lock"
flock -s -n 9 || { echo 'Deployment active; backup not started'; exit 1; }
exec 8>"$base/.backup.lock"
flock -n 8 || { echo 'Backup already running'; exit 1; }
release=$(readlink -f "$base/current")
[[ $release =~ ^/opt/itemtalk/releases/[a-f0-9]{40}$ && -f $release/.env.image_tag ]] \
  || { echo 'No verified active release'; exit 1; }
[[ -f $base/.env.backup && ! -L $base/.env.backup ]] \
  || { echo 'Restricted backup environment missing'; exit 1; }
[[ $(stat -c %a "$base/.env.backup") == 600 && $(stat -c %u "$base/.env.backup") == "$(id -u)" ]] \
  || { echo 'Backup environment permissions must be owner-only'; exit 1; }
compose() {
  docker compose --project-name itemtalk --env-file "$base/.env.production" \
    --env-file "$release/.env.image_tag" -f "$release/docker-compose.yml" "$@"
}
# 未明确启用生产时失败关闭，不创建备份或发送通知。
compose --profile backup-tools run --rm --no-deps --entrypoint python backup -c \
  'import os; raise SystemExit(0 if os.environ.get("BACKUP_JOBS_ENABLED") == "true" else "Production backup jobs disabled")'
# 只启动独立运维容器；不自动启动、重建或暂停DB/API/Worker。
compose --profile backup-tools run --rm --no-deps --entrypoint python backup \
  -m app.backup_jobs "$mode" --real-cos --pg-bin /usr/lib/postgresql/16/bin --output-directory /backups
