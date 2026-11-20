#!/usr/bin/env bash
# ============================================================================
# ops/install.sh — 幂等安装：systemd units + nginx site + 证书续期钩子
#   首次部署和重装都跑它；已存在的内容会先备份再覆盖。
#   用法：sudo bash ops/install.sh [--no-nginx] [--no-units]
# ============================================================================
set -euo pipefail

DIR=/opt/aragon-trading
DOMAIN=aragontrading.online
WWW=www.aragontrading.online
DO_UNITS=1; DO_NGINX=1
for arg in "$@"; do
  case "$arg" in
    --no-nginx) DO_NGINX=0 ;;
    --no-units) DO_UNITS=0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行"; exit 1; }

TS=$(date +%Y%m%d_%H%M%S)

if [ "$DO_UNITS" = "1" ]; then
  log "systemd units"
  for u in aragon-trading-api aragon-trading-web; do
    if [ -f "/etc/systemd/system/$u.service" ]; then
      cp "/etc/systemd/system/$u.service" "/etc/systemd/system/$u.service.bak.$TS"
    fi
    install -m 644 "$DIR/deploy/systemd/$u.service" "/etc/systemd/system/$u.service"
    echo "  $u"
  done
  systemctl daemon-reload
  systemctl enable aragon-trading-api.service aragon-trading-web.service
  echo "  enabled"
fi

if [ "$DO_NGINX" = "1" ]; then
  log "nginx site"
  A=/etc/nginx/sites-available/aragon-trading.conf
  if [ -f "$A" ]; then cp "$A" "$A.bak.$TS"; fi
  install -m 644 "$DIR/deploy/nginx/aragon-trading.conf" "$A"
  ln -sfn "$A" /etc/nginx/sites-enabled/aragon-trading.conf
  nginx -t
  systemctl reload nginx
  echo "  reloaded"

  log "TLS 证书"
  CERT=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
  if [ -f "$CERT" ]; then
    openssl x509 -enddate -noout -in "$CERT"
  else
    echo "  未找到证书，签发中…"
    certbot --nginx -d "$DOMAIN" -d "$WWW" --agree-tos \
      --register-unsafely-without-email --redirect --non-interactive
  fi
  # 续期：certbot 的 systemd timer 会自己续；这里只确认它开着。
  systemctl list-timers 'certbot*' --no-pager 2>/dev/null | sed 's/^/  /' || true
fi

log "完成 —— 用 bash $DIR/ops/status.sh 验收"
