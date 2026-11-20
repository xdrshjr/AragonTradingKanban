#!/usr/bin/env bash
# /sw.js: Next sets the no-store pair itself; our add_header duplicated it.
# Keep the nginx guarantee (a stale sw.js would pin every visitor to an old cache
# forever) but emit exactly one value by hiding the upstream header first.
set -euo pipefail
CONF=/etc/nginx/sites-available/aragon-trading.conf
python3 - "$CONF" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
old = """        proxy_set_header Host $host;
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header Service-Worker-Allowed "/" always;
"""
new = """        proxy_set_header Host $host;
        proxy_hide_header Cache-Control;
        proxy_hide_header Service-Worker-Allowed;
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header Service-Worker-Allowed "/" always;
"""
assert old in s, 'sw.js block not found as expected'
s = s.replace(old, new, 1)
open(p, 'w', encoding='utf-8').write(s)
print('rewritten')
PY
nginx -t
systemctl reload nginx
echo RELOADED
echo "--- sw.js headers:"
curl -sI https://aragontrading.online/sw.js | grep -iE '^(HTTP|cache-control|service-worker-allowed)' | tr -d '\r'
