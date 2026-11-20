#!/usr/bin/env bash
set -uo pipefail
DB=/opt/aragon-trading/api/data/app.db
python3 - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
c.row_factory = sqlite3.Row
cols = [r[1] for r in c.execute("pragma table_info(runs)")]
print('runs columns:', cols)
print()
for r in c.execute('select * from runs'):
    d = dict(r)
    keep = {k: d.get(k) for k in ('id','instrument_id','status','stage','pct','exit_code','report_id',
                                  'started_at','finished_at','error_code','tokens_in','tokens_out') if k in d}
    print(' ', keep)
print()
print('reports:')
for r in c.execute('select id,instrument_id,created_at from reports'):
    print(' ', dict(r))
PY
echo
echo "=== workspace mtimes ==="
ls -ld --time-style=full-iso /opt/aragon-trading/api/workspace/runs/*/ | awk '{print "  "$6" "$7" "$9}'
echo
echo "=== api log: run creation lines ==="
journalctl -u aragon-trading-api --no-pager -o short-iso | grep -iE 'run|spawn|generate' | tail -25
