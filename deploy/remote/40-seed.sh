#!/usr/bin/env bash
# Seed the three demo instruments through the public HTTPS origin, exactly as the
# browser would (proves nginx -> Next/API routing + akshare/yfinance resolution).
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

for q in "贵州茅台" "宁德时代" "AAPL"; do
  echo "--- resolve: $q"
  curl -s -X POST "$D/api/v1/symbols/resolve" \
    -H 'content-type: application/json' \
    --data "{\"query\":\"$q\"}" \
    | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception as e:
    print('  bad json:', sys.stdin); raise SystemExit(1)
i=d.get('instrument') or {}
print('  id=%-12s name=%-12s market=%-6s source=%s created=%s' % (
    i.get('id'), i.get('name'), i.get('market'), d.get('source'), d.get('created')))
print('  INSTRUMENT=' + str(i.get('id')))
"
done

echo
echo "--- watchlist:"
curl -s "$D/api/v1/watchlist" -o /dev/null -w '  status=%{http_code}\n'
echo "--- instruments:"
curl -s "$D/api/v1/instruments" | python3 -c "
import json,sys
d=json.load(sys.stdin)
items=d.get('items') or d.get('instruments') or []
print('  count=', len(items))
for i in items[:8]:
    print('   ', i.get('id'), i.get('name'), i.get('market'))
"
