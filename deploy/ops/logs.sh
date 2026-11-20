#!/usr/bin/env bash
# ============================================================================
# ops/logs.sh — 看日志
#   用法：bash ops/logs.sh [api|web|nginx|nginx-err|all] [行数]
#         bash ops/logs.sh api 200        # 最近 200 行
#         bash ops/logs.sh api follow     # 实时跟随（Ctrl-C 退出）
# ============================================================================
set -uo pipefail
W="${1:-all}"
N="${2:-120}"

api()  { journalctl -u aragon-trading-api --no-pager -n "$N" -o short-iso; }
web()  { journalctl -u aragon-trading-web --no-pager -n "$N" -o short-iso; }
ngx()  { tail -n "$N" /var/log/nginx/aragon-trading.access.log 2>/dev/null || echo '(无 access log)'; }
ngxe() { tail -n "$N" /var/log/nginx/aragon-trading.error.log  2>/dev/null || echo '(无 error log)'; }

case "$W" in
  api)  [ "$N" = "follow" ] && exec journalctl -u aragon-trading-api -f -o short-iso || api ;;
  web)  [ "$N" = "follow" ] && exec journalctl -u aragon-trading-web -f -o short-iso || web ;;
  nginx)     ngx ;;
  nginx-err) ngxe ;;
  all)
    echo "════════ API (最近 $N 行) ════════"; api
    echo; echo "════════ WEB (最近 $N 行) ════════"; web
    echo; echo "════════ nginx error (最近 $N 行) ════════"; ngxe
    ;;
  *) echo "用法: bash ops/logs.sh [api|web|nginx|nginx-err|all] [行数|follow]"; exit 2 ;;
esac
