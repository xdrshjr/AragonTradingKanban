#!/usr/bin/env bash
# 三个站点的 HTTP 版本与状态（只看，不动任何配置）
set -uo pipefail
for d in aragontrading.online aragonagent.com skillrouter.net; do
  printf '  %-24s ' "$d"
  curl -s -o /dev/null -w 'http=%{http_version} code=%{http_code}\n' --max-time 20 "https://$d/"
done
