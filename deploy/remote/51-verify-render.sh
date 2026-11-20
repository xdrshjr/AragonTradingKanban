#!/usr/bin/env bash
# Confirm the generated report is actually rendered by the Next app (SSR HTML),
# not merely present in the database.
set -uo pipefail
D=https://aragontrading.online
RID=$(python3 -c "import json;print(json.load(open('/tmp/last-run.json')).get('reportId') or '')" 2>/dev/null)
INST=$(python3 -c "import json;print(json.load(open('/tmp/last-run.json')).get('instrumentId') or '')" 2>/dev/null)
echo "reportId=$RID instrument=$INST"

echo
echo "=== report doc shape ==="
curl -s "$D/api/v1/reports/$RID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('  top-level keys :', sorted(d.keys()))
doc=d.get('doc') or d.get('docJson') or {}
print('  doc keys       :', sorted(doc.keys()))
print('  schema         :', doc.get('schema'))
md=doc.get('meta') or {}
print('  meta           :', json.dumps(md, ensure_ascii=False)[:300])
for s in (doc.get('sections') or [])[:6]:
    bl=s.get('blocks') or []
    print('  section %-14s blocks=%d  first=%s' % (
        repr(s.get('title'))[:14], len(bl), (bl[0].get('type') if bl else None)))
"

echo
echo "=== SSR render of the workspace page ==="
for path in "/" "/?instrument=$INST" "/reports/$RID"; do
  code=$(curl -s -o /tmp/page.html -w '%{http_code}' "$D$path")
  size=$(wc -c < /tmp/page.html)
  printf '  %-28s http=%s bytes=%s\n' "$path" "$code" "$size"
done

echo
echo "=== does the page carry real report content? ==="
curl -s "$D/" -o /tmp/home2.html
grep -q 'Aragon Research Terminal' /tmp/home2.html && echo "  [✓] app shell title present" || echo "  [✗] no title"
grep -o '贵州茅台\|宁德时代\|Apple' /tmp/home2.html | sort -u | sed 's/^/  rail symbol: /' || echo "  (rail rendered client-side)"

echo
echo "=== export endpoints ==="
curl -s -o /tmp/r.html -w '  html export http=%{http_code} bytes=%{size_download}\n' "$D/api/v1/reports/$RID/export?format=html"
curl -s -o /tmp/r.md   -w '  md   export http=%{http_code} bytes=%{size_download}\n' "$D/api/v1/reports/$RID/export?format=md"
head -c 200 /tmp/r.md 2>/dev/null; echo
grep -o '<title>[^<]*</title>' /tmp/r.html 2>/dev/null | head -1 | sed 's/^/  export title: /'

echo
echo "=== blocks range endpoint (range query) ==="
curl -s -o /dev/null -w '  /blocks http=%{http_code}\n' "$D/api/v1/reports/$RID/blocks?offset=0&limit=5"

echo
echo "=== search + pinyin ==="
curl -s "$D/api/v1/search?q=茅台" | head -c 300; echo
