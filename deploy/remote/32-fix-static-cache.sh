#!/usr/bin/env bash
# Drop the duplicate Cache-Control: `expires 365d` and `add_header Cache-Control`
# both emitted one, so responses carried two conflicting headers.
set -euo pipefail
CONF=/etc/nginx/sites-available/aragon-trading.conf
sed -i '/^        expires 365d;$/d' "$CONF"
nginx -t
systemctl reload nginx
echo RELOADED
A=$(curl -s https://aragontrading.online/ | grep -o '/_next/static/chunks/[^"]*\.js' | head -1)
echo "asset: $A"
curl -sI "https://aragontrading.online$A" | grep -iE '^(HTTP|cache-control)' | tr -d '\r'
