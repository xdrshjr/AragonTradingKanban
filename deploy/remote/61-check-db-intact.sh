#!/usr/bin/env bash
# Did the source tarball overwrite the production database with the local dev one?
# Production had: 4 instruments, 3 watchlist rows, 2 reports, 2 runs.
set -uo pipefail
DB=/opt/aragon-trading/api/data/app.db
ls -l "$DB"
python3 - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
for t in ('instruments','watchlist','reports','runs','cost_ledger','alembic_version'):
    try:
        print('  %-16s %s' % (t, c.execute(f'select count(*) from {t}').fetchone()[0]))
    except Exception as e:
        print('  %-16s (%s)' % (t, e))
print('  alembic:', [r[0] for r in c.execute('select version_num from alembic_version')])
print('  runs:')
for r in c.execute('select id,instrument_id,status from runs'):
    print('   ', r[0][:8], r[1], r[2])
c.close()
PY
echo
echo "=== 本地开发库有什么（对比用） ==="
echo "  (应当只有这台服务器的数据；如果出现只在本机跑过的标的，说明被覆盖了)"
