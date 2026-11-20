#!/usr/bin/env bash
# Align the certbot-managed 443 listen lines with the pre-existing aragon-router
# site (`listen 443 ssl http2`). A plain `ssl` listen on the same address:port
# makes nginx warn "protocol options redefined" and silently turns HTTP/2 off
# for BOTH sites — the first site in load order wins.
set -euo pipefail

CONF=/etc/nginx/sites-available/aragon-trading.conf
cp "$CONF" "${CONF}.bak.$(date +%Y%m%d_%H%M%S)"

python3 - "$CONF" <<'PY'
import re, sys
path = sys.argv[1]
src = open(path, encoding='utf-8').read()

src = src.replace('listen [::]:443 ssl ipv6only=on; # managed by Certbot',
                  'listen [::]:443 ssl http2; # managed by Certbot')
src = src.replace('listen 443 ssl; # managed by Certbot',
                  'listen 443 ssl http2; # managed by Certbot')

# Give ACME/tls a modern floor without touching the shared options-ssl-nginx.conf.
if 'ssl_protocols' not in src:
    src = src.replace(
        'ssl_certificate /etc/letsencrypt/live/',
        'ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_certificate /etc/letsencrypt/live/',
        1,
    )

open(path, 'w', encoding='utf-8').write(src)
print('rewritten')
PY

nginx -t
systemctl reload nginx
echo "--- listen lines now:"
grep -n 'listen.*443' "$CONF"
echo "--- warning gone? (anything printed above from nginx -t is still a problem)"
