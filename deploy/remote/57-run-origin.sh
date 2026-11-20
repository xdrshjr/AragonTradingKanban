#!/usr/bin/env bash
# Second run is US:NVDA (not something I created). Find its origin: manual click
# vs scheduler tick. `runs.reason` carries that.
set -uo pipefail
DB=/opt/aragon-trading/api/data/app.db
python3 - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
c.row_factory = sqlite3.Row
for r in c.execute('select id,instrument_id,reason,retry_count,agent_provider,agent_model,cost_source,cost_usd_est from runs'):
    print(dict(r))
print()
print('schedules table:')
try:
    for r in c.execute('select * from schedules'):
        print(' ', dict(r))
except Exception as e:
    print(' ', e)
print()
print('run_events for NVDA run:')
for r in c.execute("select type, count(*) n from run_events where run_id='9ac333b9e3d74113a03fc1df7a35e0fa' group by type"):
    print(' ', tuple(r))
c.close()
PY
echo
echo "=== nginx access log: who hit what around 12:49 ==="
grep -E '12:4[5-9]|12:5[0-2]' /var/log/nginx/aragon-trading.access.log 2>/dev/null | grep -vE 'GET /_next|GET /favicon' | tail -20
