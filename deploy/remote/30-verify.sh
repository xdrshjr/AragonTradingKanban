#!/usr/bin/env bash
# End-to-end verification for aragontrading.online.
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

echo "=== 1. TLS chain / cert ==="
echo | openssl s_client -connect aragontrading.online:443 -servername aragontrading.online 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates 2>/dev/null
echo -n "http2 negotiated: "
curl -s -o /dev/null -w '%{http_version}\n' "$D/"
echo -n "hsts/http->https redirect: "
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://aragontrading.online/

echo
echo "=== 2. Page shell ==="
curl -s -o /tmp/home.html -w 'home=%{http_code} bytes=%{size_download}\n' "$D/"
echo -n "title: "
grep -o '<title>[^<]*</title>' /tmp/home.html | head -1
echo -n "api base baked into bundle: "
curl -s "$D/" | grep -o 'aragontrading\.online' | head -1
echo -n "sw.js cache header: "
curl -sI "$D/sw.js" | grep -i 'cache-control' | tr -d '\r'
echo -n "next static asset: "
ASSET=$(grep -o '/_next/static/[^"]*\.js' /tmp/home.html | head -1)
curl -s -o /dev/null -w "asset=$ASSET status=%{http_code} cache=%{header_json}" "$D$ASSET" 2>/dev/null | head -c 0
curl -sI "$D$ASSET" | grep -iE '^(HTTP|cache-control)' | tr -d '\r'

echo
echo "=== 3. API through nginx (same origin) ==="
curl -s -o /dev/null -w 'health     =%{http_code}\n' "$D/api/v1/health"
curl -s "$D/api/v1/health"; echo
curl -s -o /dev/null -w 'capabilities=%{http_code}\n' "$D/api/v1/capabilities"
curl -s "$D/api/v1/capabilities" | python3 -c "
import json,sys
d=json.load(sys.stdin)
a=d.get('aragon',{})
print('  agentMode        :', d.get('agentMode'))
print('  available        :', a.get('available'))
print('  canStructured    :', a.get('canStructured'))
print('  hasSubmitResult  :', a.get('hasSubmitResult'))
print('  keySource        :', a.get('keySource'), '| keyPresent:', a.get('keyPresent'))
print('  degradedReason   :', a.get('degradedReason'))
"
curl -s -o /dev/null -w 'instruments=%{http_code}\n' "$D/api/v1/instruments"
curl -s -o /dev/null -w 'watchlist  =%{http_code}\n' "$D/api/v1/watchlist"
curl -s -o /dev/null -w 'cost       =%{http_code}\n' "$D/api/v1/cost/summary"
curl -s -o /dev/null -w 'docs       =%{http_code}\n' "http://127.0.0.1:8000/docs"

echo
echo "=== 4. SSE endpoint (headers must not be buffered) ==="
curl -s -D - -o /dev/null --max-time 5 -H 'Accept: text/event-stream' "$D/api/v1/runs/does-not-exist/events" | head -6 | tr -d '\r'

echo
echo "=== 5. Cross-origin API from browser perspective ==="
curl -s -D - -o /dev/null -H 'Origin: https://aragontrading.online' "$D/api/v1/health" | grep -i 'access-control' | tr -d '\r'
