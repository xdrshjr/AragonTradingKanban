#!/usr/bin/env bash
# 新版 run 诊断字段 + 报告可渲染性（v12 新增 hard deadline / salvage / 会话恢复）
set -uo pipefail
D=https://aragontrading.online
RID=50e411ce340c4640825bc4354017fee3

echo "=== run 记录（新版新增列）==="
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:/opt/aragon-trading/api/data/app.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
r = c.execute("select * from runs order by started_at desc limit 1").fetchone()
d = dict(r)
for k in sorted(d):
    v = d[k]
    if v is None or v == '':
        continue
    print(f"  {k:24s} {str(v)[:90]}")
print()
print("  最近这条 run:")
print("   id       ", d.get('id'))
print("   status   ", d.get('status'), "| exit", d.get('exit_code'), "| retry", d.get('retry_count'),
      "| structured_attempts", d.get('structured_attempts'))
print("   tokens   ", d.get('tokens_in'), "/", d.get('tokens_out'))
print("   cost     ", d.get('cost_source'), "usd=", d.get('cost_usd'), "est=", d.get('cost_usd_est'))
print("   failure  ", d.get('failure_fingerprint'))
c.close()
PY

echo
echo "=== 本次 run 的产物目录 ==="
ls -1t /opt/aragon-trading/api/workspace/runs/ | head -3 | sed 's/^/  /'
LATEST=$(ls -1t /opt/aragon-trading/api/workspace/runs/ | head -1)
echo "  最新: $LATEST"
ls -la "/opt/aragon-trading/api/workspace/runs/$LATEST" | tail -n +4 | awk '{printf "  %8s  %s\n", $5, $9}'

echo
echo "=== engine.json（引擎身份自证，v12 新增）==="
python3 -c "
import json
d=json.load(open('/opt/aragon-trading/api/workspace/runs/$LATEST/engine.json'))
print(' ', json.dumps(d, ensure_ascii=False)[:700])
" 2>/dev/null || echo "  (无 engine.json)"

echo
echo "=== 报告可渲染 ==="
curl -s -o /dev/null -w '  GET /reports/<id>          %{http_code}\n' "$D/api/v1/reports/$RID"
curl -s -o /dev/null -w '  export html                %{http_code}\n' "$D/api/v1/reports/$RID/export?format=html"
curl -s -o /dev/null -w '  export md                  %{http_code}\n' "$D/api/v1/reports/$RID/export?format=md"
curl -s "$D/api/v1/reports/$RID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
doc=d.get('doc') or {}
print('  sections:', len(doc.get('sections') or []), '| rating:', doc.get('rating'), '| provisional:', doc.get('provisional'))
print('  headline:', (doc.get('headline') or '')[:80])
q=d.get('quality') or {}
print('  quality keys:', sorted(q.keys())[:10])
"
echo
echo "=== 页面（CN:600519.SH）==="
curl -s -o /tmp/p.html -w '  page http=%{http_code} bytes=%{size_download}\n' "$D/CN:600519.SH"
grep -c '贵州茅台' /tmp/p.html | sed 's/^/  含“贵州茅台”次数: /'
