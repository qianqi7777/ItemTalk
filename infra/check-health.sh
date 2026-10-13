#!/usr/bin/env bash
# 非零退出供 systemd/监控采集；不打印生产配置、用户内容或付费响应。
set -euo pipefail
base=/opt/itemtalk
release=$(readlink -f "$base/current")
expected=$(basename "$release")
curl --fail --silent --show-error --max-time 10 https://49.233.65.195/itemtalk/api/v1/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="healthy" and d["buildVersion"]==sys.argv[1]' "$expected"
# shortlived IP 证书提前 24 小时告警；不改变现有续期服务。
openssl s_client -connect 49.233.65.195:443 -servername 49.233.65.195 </dev/null 2>/dev/null \
  | openssl x509 -checkend 86400 -noout
echo 'HTTPS certificate and application version healthy'
