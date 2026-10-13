#!/usr/bin/env bash
# Install only the ItemTalk prefix; preserve the existing site's exact rollback copy.
set -euo pipefail
umask 077
operation=${1:?Expected install or remove}
release=${2:?Expected release directory}
base=/opt/itemtalk
site=/etc/nginx/sites-available/echolens-hub
snippet=/etc/nginx/snippets/itemtalk.conf
include="    include $snippet;"
[[ $release == "$base/releases/"* && -d $release ]] || exit 2
restore_site() {
  sudo -n cp -a "$(cat "$release/.nginx-backup")" "$site"
  sudo -n nginx -t
  sudo -n systemctl reload nginx
  sudo -n rm -f "$snippet"
  rm -f "$release/.nginx-installed"
}
case "$operation" in
  install)
    if grep -Fqx "$include" "$site"; then exit 0; fi
    [[ ! -e $snippet ]] || { echo 'Existing unmanaged ItemTalk snippet'; exit 2; }
    backup=$base/backups/nginx-before-$(date -u +%Y%m%dT%H%M%SZ).conf
    sudo -n cp -a "$site" "$backup"
    printf '%s\n' "$backup" > "$release/.nginx-backup"
    python3 - "$site" "$release/nginx-site.next" "$include" <<'PY'
from pathlib import Path
import sys
source, target, include = sys.argv[1:]
text = Path(source).read_text()
anchor = '    ssl_protocols '
if text.count(anchor) != 1 or 'listen 443 ssl' not in text:
    raise SystemExit('Unexpected existing HTTPS site structure')
Path(target).write_text(text.replace(anchor, include + '\n\n' + anchor, 1))
PY
    trap 'restore_site' ERR
    sudo -n install -m 644 "$release/infra/nginx-itemtalk-location.conf" "$snippet"
    sudo -n install -m 644 "$release/nginx-site.next" "$site"
    sudo -n nginx -t
    sudo -n systemctl reload nginx
    touch "$release/.nginx-installed"
    trap - ERR
    ;;
  remove)
    if [[ -f $release/.nginx-installed ]]; then restore_site; fi
    ;;
  *) exit 2 ;;
esac
