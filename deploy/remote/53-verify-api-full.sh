#!/usr/bin/env bash
# Correct parameter names: timeframe / range / adjust (not interval).
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

for tf in 1d 1w 1m; do
  curl -s -o /tmp/b.json -w "  bars timeframe=$tf http=%{http_code} " \
    "$D/api/v1/instruments/CN:600519.SH/bars?timeframe=$tf&range=1y"
  python3 -c "
import json
d=json.load(open('/tmp/b.json'))
b=d.get('bars') or []
print('count=%d source=%s first=%s last=%s' % (
  len(b), d.get('source'),
  (b[0].get('time') if b else None), (b[-1].get('time') if b else None)))
" 2>/dev/null || head -c 200 /tmp/b.json
done

echo
echo "=== export formats ==="
RID=1221174a30eb4ef09a99f55ea7bab768
for f in html md; do
  curl -s -o /tmp/x.$f -w "  export format=$f http=%{http_code} bytes=%{size_download}\n" "$D/api/v1/reports/$RID/export?format=$f"
done
curl -s -o /dev/null -w "  export bad format http=%{http_code}\n" "$D/api/v1/reports/$RID/export?format=pdf"

echo
echo "=== blocks range (correct params) ==="
curl -s -o /tmp/bl.json -w '  blocks http=%{http_code}\n' "$D/api/v1/reports/$RID/blocks?from=0&to=4"
head -c 200 /tmp/bl.json; echo
curl -s "$D/api/v1/reports/$RID/../" -o /dev/null -w '  (sanity) %{http_code}\n'

echo
echo "=== cost ledger ==="
curl -s "$D/api/v1/cost/summary" | head -c 400; echo
curl -s "$D/api/v1/cost/entries?limit=3" | head -c 400; echo

echo
echo "=== schedules / asks surfaces alive ==="
curl -s -o /dev/null -w '  schedules %{http_code}\n' "$D/api/v1/schedules"
curl -s -o /dev/null -w '  reports?inst %{http_code}\n' "$D/api/v1/instruments/US:AAPL/reports"
curl -s -o /dev/null -w '  latest %{http_code}\n' "$D/api/v1/instruments/US:AAPL/reports/latest"
