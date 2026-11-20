#!/usr/bin/env bash
# 发版收尾：日志无异常 + 数据完整 + 三站正常 + 新报告可访问
set -uo pipefail
D=https://aragontrading.online

echo "=== 1. API 日志里的异常（发版后应无 ERROR/Traceback）==="
journalctl -u aragon-trading-api --since "-40min" --no-pager -o cat \
  | grep -iE 'error|traceback|exception|critical' \
  | grep -viE 'errorMessage|error_code|error=\"|no error|errors\.' | tail -12 || echo "  （无）"
echo "  ---"
journalctl -u aragon-trading-api --since "-40min" --no-pager -o cat | grep -cE 'level=error' | sed 's/^/  level=error 行数: /'

echo
echo "=== 2. Web 日志 ==="
journalctl -u aragon-trading-web --since "-40min" --no-pager -o cat | grep -iE 'error|unhandled|exception' | tail -8 || echo "  （无）"

echo
echo "=== 3. 数据最终状态 ==="
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:/opt/aragon-trading/api/data/app.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
print("  runs:", c.execute("select count(*) from runs").fetchone()[0],
      "| succeeded:", c.execute("select count(*) from runs where status='succeeded'").fetchone()[0],
      "| failed:", c.execute("select count(*) from runs where status='failed'").fetchone()[0])
print("  reports:", c.execute("select count(*) from reports").fetchone()[0])
print("  instruments:", c.execute("select count(*) from instruments").fetchone()[0],
      "| watchlist:", c.execute("select count(*) from watchlist").fetchone()[0])
print()
print("  发版前的 6 条（应原封不动）:")
for r in c.execute("""select id,instrument_id,status,report_id,finished_at from runs
                      where started_at < '2026-11-18T05:50:00Z' order by started_at"""):
    print("    %s %-12s %-9s %s %s" % (r['id'][:8], r['instrument_id'], r['status'],
                                        (r['report_id'] or '-')[:8], r['finished_at']))
print()
print("  本次发版后的新 run:")
for r in c.execute("""select id,instrument_id,status,report_id,tokens_in,tokens_out from runs
                      where started_at >= '2026-11-18T05:50:00Z' order by started_at"""):
    print("    %s %-12s %-9s %s tok=%s/%s" % (r['id'][:8], r['instrument_id'], r['status'],
                                              (r['report_id'] or '-')[:8], r['tokens_in'], r['tokens_out']))
c.close()
PY

echo
echo "=== 4. 本次新增报告可访问 ==="
for rid in 50e411ce340c4640825bc4354017fee3 697fecf9ca5b432f82fbe7af48dda81c; do
  printf '  %s  ' "${rid:0:8}"
  curl -s -o /dev/null -w 'report=%{http_code} ' "$D/api/v1/reports/$rid"
  curl -s -o /dev/null -w 'html=%{http_code} ' "$D/api/v1/reports/$rid/export?format=html"
  curl -s -o /dev/null -w 'md=%{http_code}\n' "$D/api/v1/reports/$rid/export?format=md"
done

echo
echo "=== 5. 三站与关键端口 ==="
for d in aragontrading.online aragonagent.com skillrouter.net; do
  printf '  %-22s ' "$d"
  curl -sL -o /dev/null -w 'code=%{http_code} http=%{http_version}\n' --max-time 20 "https://$d/"
done
ss -ltn | grep -E ':(3000|3100|3200|8000|8090)\b' | awk '{print "  "$4}'

echo
echo "=== 6. 内存/磁盘 ==="
free -h | head -2 | sed 's/^/  /'
df -h / | tail -1 | sed 's/^/  /'

echo
echo "=== 7. 本项目服务 ==="
systemctl is-active aragon-trading-api aragon-trading-web | paste -sd' ' - | sed 's/^/  api web: /'
