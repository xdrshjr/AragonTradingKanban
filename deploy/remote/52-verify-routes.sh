#!/usr/bin/env bash
# The app has exactly two routes: `/` and `/[symbol]` (report opens as a panel on
# the symbol page). Verify both, plus search with proper URL encoding.
set -uo pipefail
D=https://aragontrading.online
RID=1221174a30eb4ef09a99f55ea7bab768

echo "=== routes that exist ==="
for p in "/" "/AAPL" "/600519.SH" "/CN:600519.SH" "/US:AAPL"; do
  printf '  %-18s http=%s bytes=%s\n' "$p" \
    "$(curl -s -o /tmp/p.html -w '%{http_code}' "$D$p")" "$(wc -c </tmp/p.html)"
done

echo
echo "=== /US:AAPL carries instrument + report data? ==="
curl -s "$D/US:AAPL" -o /tmp/sym.html
wc -c < /tmp/sym.html | sed 's/^/  bytes: /'
for pat in 'Apple' 'AAPL' '核心逻辑' '中性'; do
  n=$(grep -c "$pat" /tmp/sym.html || true)
  echo "  '$pat' occurrences: $n"
done

echo
echo "=== search (URL-encoded) ==="
curl -s -G "$D/api/v1/search" --data-urlencode 'q=茅台' -o /tmp/s1.json -w '  search 茅台 http=%{http_code}\n'
head -c 400 /tmp/s1.json; echo
curl -s -G "$D/api/v1/symbols/search" --data-urlencode 'q=maotai' -o /tmp/s2.json -w '  symbols/search maotai http=%{http_code}\n'
head -c 400 /tmp/s2.json; echo

echo
echo "=== pinyin dimension (mt -> 茅台) ==="
curl -s -G "$D/api/v1/symbols/search" --data-urlencode 'q=mt' -o /tmp/s3.json -w '  symbols/search mt http=%{http_code}\n'
python3 -c "
import json
d=json.load(open('/tmp/s3.json'))
hits=d.get('hits') or d.get('items') or []
print('  hits:', len(hits))
for h in hits[:5]: print('   ', h.get('id'), h.get('name'), h.get('matchedBy'))
" 2>/dev/null || head -c 200 /tmp/s3.json

echo
echo "=== bars / quote for a resolved instrument ==="
curl -s -o /tmp/b.json -w '  bars   http=%{http_code}\n' "$D/api/v1/instruments/CN:600519.SH/bars?interval=1d&range=1mo"
python3 -c "
import json
d=json.load(open('/tmp/b.json'))
print('  bars keys:', sorted(d.keys()))
b=d.get('bars') or []
print('  bar count:', len(b), '| source:', d.get('source'))
" 2>/dev/null || head -c 300 /tmp/b.json
curl -s -o /tmp/q.json -w '  quote  http=%{http_code}\n' "$D/api/v1/instruments/CN:600519.SH/quote"
head -c 300 /tmp/q.json; echo
