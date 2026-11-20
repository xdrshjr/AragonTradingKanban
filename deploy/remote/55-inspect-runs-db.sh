#!/usr/bin/env bash
# There are two run workspaces on disk; the ledger shows one run. Find out what
# the second one is before calling the deploy clean.
set -uo pipefail
DB=/opt/aragon-trading/api/data/app.db

python3 - "$DB" <<'PY'
import sqlite3, sys, os, json
db = sys.argv[1]
c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
c.row_factory = sqlite3.Row
print('=== runs ===')
for r in c.execute('select id,instrument_id,status,stage,pct,exit_code,error_code,report_id,created_at from runs order by created_at'):
    print('  %s %-12s %-10s %-10s exit=%s err=%s report=%s %s' % (
        r['id'][:8], r['instrument_id'], r['status'], r['stage'], r['exit_code'],
        r['error_code'], (r['report_id'] or '-')[:8], r['created_at']))
print()
print('=== reports ===')
for r in c.execute('select id,instrument_id,version,created_at from reports order by created_at'):
    print('  %s %-12s v%s %s' % (r['id'][:8], r['instrument_id'], r['version'], r['created_at']))
print()
print('=== schedules ===')
try:
    rows = list(c.execute('select * from schedules'))
    print('  count:', len(rows))
    for r in rows[:5]:
        d = dict(r)
        print('  ', {k: d[k] for k in ('id','instrument_id','enabled','cron','next_run_at') if k in d})
except Exception as e:
    print('  ', e)
print()
print('=== run_events counts per run ===')
try:
    for r in c.execute('select run_id, count(*) n from run_events group by run_id'):
        print('  %s %s' % (r[0][:8], r[1]))
except Exception as e:
    print('  ', e)
c.close()
PY

echo
echo "=== per-workspace engine.json ==="
for d in /opt/aragon-trading/api/workspace/runs/*/; do
  echo "  --- $(basename "$d")"
  [ -f "$d/engine.json" ] && python3 -c "
import json;d=json.load(open('$d/engine.json'));print('     ',json.dumps(d,ensure_ascii=False)[:400])" || echo "      (no engine.json)"
  ls -la "$d" | tail -n +4 | awk '{print "      "$5"\t"$9}'
done
