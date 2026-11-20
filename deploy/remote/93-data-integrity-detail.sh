#!/usr/bin/env bash
# 逐条核对：发版前就存在的记录，内容是否被改动（不只是行数）
set -uo pipefail
DB=/opt/aragon-trading/api/data/app.db

echo "=== 发版前就存在的 6 条 run（id/状态/报告/时间 应与发版前完全一致）==="
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:/opt/aragon-trading/api/data/app.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
rows = c.execute("""
    select id, instrument_id, status, exit_code, report_id, started_at, finished_at,
           tokens_in, tokens_out, retry_count
    from runs order by started_at
""").fetchall()
for r in rows:
    print("  %s  %-12s %-9s exit=%-4s report=%-8s %s -> %s  tok=%s/%s retry=%s" % (
        r['id'][:8], r['instrument_id'], r['status'], r['exit_code'],
        (r['report_id'] or '-')[:8], r['started_at'], r['finished_at'],
        r['tokens_in'], r['tokens_out'], r['retry_count']))
print("  合计:", len(rows), "条")
print()
print("=== 报告（原有 3 份 + 本次 1 份）===")
for r in c.execute("select id, instrument_id, created_at, pinned from reports order by created_at"):
    print("  %s %-12s %s pinned=%s" % (r[0][:8], r[1], r[2], r[3]))
print()
print("=== 标的与自选（应与发版前一致）===")
for r in c.execute("select id, symbol, name, market from instruments order by id"):
    print("  inst:", r[0], r[1], r[2], r[3])
for r in c.execute("select instrument_id, sort_order from watchlist order by sort_order"):
    print("  watch:", r[0], "order=", r[1])
c.close()
PY

echo
echo "=== 数据库完整性 ==="
python3 -c "
import sqlite3
c = sqlite3.connect('file:/opt/aragon-trading/api/data/app.db?mode=ro', uri=True)
print('  integrity_check:', c.execute('pragma integrity_check').fetchone()[0])
print('  alembic:', c.execute('select version_num from alembic_version').fetchone()[0])
print('  foreign_keys off in ro mode (正常)')
"
ls -l "$DB" | awk '{print "  db 文件:", $5, "bytes, mtime", $6, $7, $8}'

echo
echo "=== 本次发版的部署备份（回滚点）==="
ls -1dt /opt/aragon-trading/.deploy-backups/*/ 2>/dev/null | head -3 | sed 's/^/  /'
ls -1dt /opt/aragon-trading/.backups/*/ 2>/dev/null | head -2 | sed 's/^/  /'
