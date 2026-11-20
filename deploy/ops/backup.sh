#!/usr/bin/env bash
# ============================================================================
# ops/backup.sh — 备份数据库（SQLite 热备）+ 配置，保留最近 N 份
#   用法：bash ops/backup.sh [保留份数，默认 7]
#   建议 crontab -e 加一行：  30 3 * * * /opt/aragon-trading/ops/backup.sh 7
# ============================================================================
set -euo pipefail
KEEP="${1:-7}"
DIR=/opt/aragon-trading
DB="$DIR/api/data/app.db"
DST="$DIR/.backups"
TS=$(date +%Y%m%d_%H%M%S)

mkdir -p "$DST/$TS"

if [ -f "$DB" ]; then
  # .backup 走 SQLite 自己的在线备份 API：WAL 下的热备也是自洽的，
  # 直接 cp 可能拷到半个事务。
  python3 - "$DB" "$DST/$TS/app.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
s.close(); d.close()
print('  db ok')
PY
fi

cp "$DIR/.env" "$DST/$TS/env.bak" 2>/dev/null || true
cp /etc/nginx/sites-available/aragon-trading.conf "$DST/$TS/" 2>/dev/null || true
cp /etc/systemd/system/aragon-trading-*.service "$DST/$TS/" 2>/dev/null || true

echo "  备份 → $DST/$TS  ($(du -sh "$DST/$TS" | awk '{print $1}'))"

# 只留最近 KEEP 份
ls -dt "$DST"/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -rf 2>/dev/null || true
echo "  现有备份：$(ls -d "$DST"/*/ 2>/dev/null | wc -l) 份"
