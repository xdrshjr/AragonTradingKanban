#!/usr/bin/env bash
# One REAL generation through the public origin: create → poll → verify the
# structured report landed. This is the only test that proves the AI engine is
# wired end to end (spawn aragon → --result-schema → submit_result → doc_json).
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
INST="${1:-US:AAPL}"
MAX_WAIT="${2:-420}"

echo "=== capabilities before run ==="
curl -s "$D/api/v1/capabilities" | python3 -c "
import json,sys
d=json.load(sys.stdin); a=d.get('aragon',{})
print('agentMode=%s available=%s canStructured=%s keySource=%s degraded=%s' % (
 d.get('agentMode'), a.get('available'), a.get('canStructured'), a.get('keySource'), a.get('degradedReason')))
"

echo
echo "=== POST /api/v1/instruments/$INST/reports ==="
RESP=$(curl -s -X POST "$D/api/v1/instruments/$INST/reports" -H 'content-type: application/json' --data '{}')
echo "$RESP" | head -c 500; echo
RUN=$(printf '%s' "$RESP" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('runId') or d.get('id') or '')
")
if [ -z "$RUN" ]; then echo "!! no runId returned"; exit 1; fi
echo "runId=$RUN"
echo "$RUN" > /tmp/last-run-id

echo
echo "=== polling /api/v1/runs/$RUN ==="
START=$(date +%s)
while true; do
  BODY=$(curl -s "$D/api/v1/runs/$RUN")
  printf '%s' "$BODY" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('  t=%3ss status=%-10s stage=%-16s pct=%s exit=%s err=%s report=%s cost=%s' % (
  d.get('elapsedSec','?') if 'elapsedSec' in d else '',
  d.get('status'), d.get('stage'), d.get('pct'), d.get('exitCode'),
  d.get('errorCode'), d.get('reportId'), d.get('costUsd')))
"
  ST=$(printf '%s' "$BODY" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('status'))")
  NOW=$(date +%s); EL=$((NOW-START))
  if [ "$ST" != "queued" ] && [ "$ST" != "running" ] && [ "$ST" != "cancelling" ]; then
    echo "  -> terminal after ${EL}s: $ST"
    echo "$BODY" > /tmp/last-run.json
    break
  fi
  if [ "$EL" -gt "$MAX_WAIT" ]; then echo "  -> timeout after ${EL}s (still $ST)"; echo "$BODY" > /tmp/last-run.json; break; fi
  sleep 6
done

echo
echo "=== run detail (diagnostics) ==="
python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/last-run.json'))
except Exception:
    raise SystemExit(0)
for k in ('id','status','stage','pct','exitCode','errorCode','errorMessage','hint',
          'reportId','costUsd','costKnown','usage','engineFingerprint','failureClass'):
    if k in d:
        print('  %-18s %s' % (k, d[k]))
PY

echo
echo "=== report as rendered ==="
RID=$(python3 -c "import json;print(json.load(open('/tmp/last-run.json')).get('reportId') or '')" 2>/dev/null)
if [ -n "$RID" ]; then
  curl -s -o /dev/null -w '  GET /reports/%{http_code}\n' "$D/api/v1/reports/$RID"
  curl -s "$D/api/v1/reports/$RID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
doc=d.get('doc') or d.get('docJson') or {}
secs=doc.get('sections') or []
print('  title      :', doc.get('title'))
print('  docVersion :', doc.get('schema') or doc.get('version'))
print('  sections   :', len(secs))
blocks=0
for s in secs:
    blocks += len(s.get('blocks') or [])
print('  blocks     :', blocks)
q=(d.get('quality') or {})
print('  quality.src:', q.get('source'))
print('  rating     :', (doc.get('meta') or {}).get('rating'))
"
else
  echo "  no reportId — generation did not produce a report"
fi
