#!/usr/bin/env bash
# `npm run seed` equivalent: resolve + add to the platform-FEATURED watchlist so the left rail is
# not empty on first visit. Idempotent (adding the same instrument twice is a no-op).
# v15: the old table is now the platform's featured list, edited through
# POST /api/v1/admin/featured-watchlist (needs the service credential; `service` has no personal
# watchlist, so POST /watchlist would be a 403).
set -uo pipefail

# ★v15 用户与权限：写路由、capabilities 全量、/cost、/schedules、/agent/probe 不再对匿名开放。
# 脚本以 `service` 凭据（API_TOKEN）调用：环境变量优先，其次读服务的 .env。令牌只发往 https，
# 绝不明文发往 http://（本脚本里 http://… 只用来验证 301 跳转）。没有令牌就匿名请求，行为同以前的公开读。
TOKEN="${API_TOKEN:-$(sed -n 's/^API_TOKEN=//p' /opt/aragon-trading/.env /opt/aragon-trading/api/.env 2>/dev/null | tail -1)}"
curl() {
  local a
  for a in "$@"; do case "$a" in http://*) command curl "$@"; return;; esac; done
  command curl ${TOKEN:+-H "Authorization: Bearer $TOKEN"} "$@"
}
D=https://aragontrading.online

for inst in "CN:600519.SH" "CN:300750.SZ" "US:AAPL"; do
  printf '  POST /admin/featured-watchlist %-14s ' "$inst"
  curl -s -o /tmp/wl.json -w 'http=%{http_code} ' -X POST "$D/api/v1/admin/featured-watchlist" \
    -H 'content-type: application/json' --data "{\"instrumentId\":\"$inst\"}"
  head -c 120 /tmp/wl.json; echo
done

echo
echo "=== watchlist now ==="
curl -s "$D/api/v1/watchlist" | python3 -c "
import json,sys
d=json.load(sys.stdin)
items=d.get('items') or d.get('entries') or []
print('  count:', len(items))
for i in items:
    print('   ', i.get('instrumentId') or i.get('id'), i.get('name'), i.get('sortOrder', ''))
" 2>/dev/null || curl -s "$D/api/v1/watchlist" | head -c 600
