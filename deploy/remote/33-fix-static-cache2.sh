#!/usr/bin/env bash
# Next already emits `cache-control: public, max-age=31536000, immutable` for
# /_next/static — our add_header only duplicated it. Let Next's own header stand.
set -euo pipefail
CONF=/etc/nginx/sites-available/aragon-trading.conf
python3 - "$CONF" <<'PY'
import sys, re
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
s = s.replace('        add_header Cache-Control "public, max-age=31536000, immutable";\n', '')
open(p, 'w', encoding='utf-8').write(s)
PY
nginx -t
systemctl reload nginx
echo RELOADED
A=$(curl -s https://aragontrading.online/ | grep -o '/_next/static/chunks/[^"]*\.js' | head -1)
curl -sI "https://aragontrading.online$A" | grep -iE '^(HTTP|cache-control)' | tr -d '\r'
echo "--- sw.js (must stay no-store):"
curl -sI https://aragontrading.online/sw.js | grep -iE '^(HTTP|cache-control|service-worker-allowed)' | tr -d '\r'
