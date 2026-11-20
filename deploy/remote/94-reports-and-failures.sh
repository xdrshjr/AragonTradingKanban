#!/usr/bin/env bash
set -uo pipefail
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:/opt/aragon-trading/api/data/app.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
cols = [r[1] for r in c.execute("pragma table_info(reports)")]
print("  reports 列:", cols)
print()
sel = [x for x in ('id','instrument_id','version','status','as_of','pinned','engine_fingerprint') if x in cols]
for r in c.execute(f"select {','.join(sel)} from reports"):
    print("  ", {k: r[k] for k in sel})
print()
print("=== 发版前 3 次失败的 run 详情（判断新版本是否已覆盖这个错误）===")
for r in c.execute("""select id, instrument_id, status, exit_code, error_code, error_message,
                             failure_fingerprint, structured_attempts, started_at
                      from runs where status='failed' order by started_at"""):
    print("  ", r['id'][:8], r['instrument_id'], "exit=", r['exit_code'], "code=", r['error_code'])
    print("      msg:", (r['error_message'] or '')[:160])
    print("      fp :", r['failure_fingerprint'], "| attempts:", r['structured_attempts'], "|", r['started_at'])
print()
print("=== 自选与标的 ===")
for r in c.execute("select id, name, market from instruments order by id"):
    print("  inst:", r['id'], r['name'], r['market'])
for r in c.execute("select instrument_id, sort_order from watchlist order by sort_order"):
    print("  watch:", r['instrument_id'], "order=", r['sort_order'])
c.close()
PY
