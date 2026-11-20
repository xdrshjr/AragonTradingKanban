#!/usr/bin/env bash
# RESTORE: the source tarball carried api/data/app.db (the dev database) and
# overwrote production. ops/deploy.sh backed the good DB up *before* extracting,
# so the pre-clobber state is in .deploy-backups/<ts>/app.db.
#
# Also deletes the stray WAL/SHM so SQLite cannot replay the clobbered pages on
# top of the restored file.
set -euo pipefail
DIR=/opt/aragon-trading
DB="$DIR/api/data/app.db"

echo "=== 现有部署备份 ==="
ls -1dt "$DIR"/.deploy-backups/*/ | head -5 | sed 's/^/  /'

BK="$DIR/.deploy-backups/20261117_210156"
[ -f "$BK/app.db" ] || { echo "!! 找不到 $BK/app.db"; exit 1; }

echo
echo "=== 覆盖前备份里有什么（应当是生产数据：AAPL + NVDA） ==="
python3 - "$BK/app.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
for t in ('instruments','watchlist','reports','runs'):
    print('  %-14s %s' % (t, c.execute(f'select count(*) from {t}').fetchone()[0]))
for r in c.execute('select id,instrument_id,status from runs order by started_at'):
    print('   run', r[0][:8], r[1], r[2])
c.close()
PY

echo
echo "=== 停 API → 恢复数据库 ==="
systemctl stop aragon-trading-api.service
cp -a "$DB" "$DIR/.deploy-backups/clobbered_$(date +%H%M%S).db" 2>/dev/null || true
rm -f "$DB-wal" "$DB-shm" "$DB-journal"
cp "$BK/app.db" "$DB"
chown aragon:aragon "$DB"
chmod 644 "$DB"
ls -l "$DB"

echo
echo "=== 起 API ==="
systemctl start aragon-trading-api.service
for i in $(seq 1 60); do
  curl -sf --max-time 3 http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1 && break
  sleep 1
done
curl -s -w '\n  health=%{http_code}\n' http://127.0.0.1:8000/api/v1/health

echo
echo "=== 恢复后核对 ==="
python3 - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
for t in ('instruments','watchlist','reports','runs','alembic_version'):
    print('  %-16s %s' % (t, c.execute(f'select count(*) from {t}').fetchone()[0]))
print('  alembic:', [r[0] for r in c.execute('select version_num from alembic_version')])
for r in c.execute('select id,instrument_id,status,report_id from runs order by started_at'):
    print('   run', r[0][:8], r[1], r[2], (r[3] or '')[:8])
c.close()
PY
