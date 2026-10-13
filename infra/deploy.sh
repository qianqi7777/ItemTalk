#!/usr/bin/env bash
set -euo pipefail
umask 077
sha=${1:?Expected full commit SHA}
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid release SHA'; exit 2; }
base=/opt/itemtalk
release=$base/releases/$sha
[[ -d $release && -f $base/.env.production ]] || { echo 'Release/configuration missing'; exit 2; }
[[ $(cat "$release/infra/schema-compatibility.txt") == expand-only ]] || { echo 'Non-compatible migration requires maintenance'; exit 2; }
exec 9>"$base/.deploy.lock"
flock -n 9 || { echo 'Deployment already running'; exit 2; }
previous=$(readlink -f "$base/current" || true)
printf 'IMAGE_TAG=sha-%s\nRELEASE_SHA=%s\n' "$sha" "$sha" > "$release/.env.image_tag"
compose() {
  docker compose --project-name itemtalk --env-file "$base/.env.production" \
    --env-file "$release/.env.image_tag" -f "$release/docker-compose.yml" "$@"
}
compose config --quiet
compose pull db api worker caddy
compose up -d db
for attempt in {1..30}; do
  if compose exec -T db pg_isready -U itemtalk_admin -d itemtalk >/dev/null; then break; fi
  sleep 2
done
compose exec -T db pg_isready -U itemtalk_admin -d itemtalk >/dev/null
mkdir -p "$base/backups"
backup=$base/backups/pre-$sha-$(date -u +%Y%m%dT%H%M%SZ).dump
compose exec -T db pg_dump -U itemtalk_admin -d itemtalk -Fc > "$backup"
[[ -s $backup ]] || { echo 'Backup failed'; exit 1; }
compose exec -T db pg_restore --list < "$backup" >/dev/null
rollback() {
  trap - ERR
  if [[ -n $previous && $previous != "$release" && -f $previous/.env.image_tag ]]; then
    docker compose --project-name itemtalk --env-file "$base/.env.production" \
      --env-file "$previous/.env.image_tag" -f "$previous/docker-compose.yml" up -d --remove-orphans
    echo 'Previous application restored; database not downgraded'
  else
    compose stop api worker caddy || true
    bash "$release/infra/nginx-route.sh" remove "$release"
    echo 'First release stopped; new Nginx route removed'
  fi
  exit 1
}
trap rollback ERR
compose run --rm migration
compose run --rm migration current > "$release/migration-revision.txt"
docker image inspect "ghcr.io/qianqi7777/itemtalk-api:sha-$sha" \
  "ghcr.io/qianqi7777/itemtalk-frontend:sha-$sha" \
  "ghcr.io/qianqi7777/itemtalk-db:sha-$sha" --format '{{join .RepoDigests "\n"}}' > "$release/image-digests.txt"
compose up -d api worker caddy
bash "$release/infra/nginx-route.sh" install "$release"
healthy=false
for attempt in {1..20}; do
  if health=$(curl --fail --silent --show-error --max-time 10 https://49.233.65.195/itemtalk/api/v1/health); then
    if printf '%s' "$health" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="healthy" and d["buildVersion"]==sys.argv[1]' "$sha"; then
      healthy=true
      break
    fi
  fi
  sleep 3
done
[[ $healthy == true ]] || { echo 'Release health/version check failed'; false; }
ln -sfn "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
echo 'Release healthy; current pointer updated'
