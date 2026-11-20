#!/usr/bin/env bash
# What did the engine actually get asked to run? argv.json is the audit trail
# (the API key is redacted to *** inside it, by design).
set -uo pipefail
HOST_RUNS=/opt/aragon-trading/api/workspace/runs
echo "=== run workspaces ==="
ls -1 "$HOST_RUNS" | sed 's/^/  /'

R=$(ls -1 "$HOST_RUNS" | head -1)
DIR="$HOST_RUNS/$R"
echo
echo "=== $DIR ==="
ls -la "$DIR" | sed 's/^/  /'

for f in argv.json request.json prompt.md; do
  if [ -f "$DIR/$f" ]; then
    echo
    echo "--- $f ---"
    case "$f" in
      argv.json) python3 -c "
import json,sys
d=json.load(open('$DIR/$f'))
argv = d if isinstance(d, list) else (d.get('argv') or [])
print('  ', ' '.join(argv)[:1200])
" ;;
      *.json) head -c 900 "$DIR/$f"; echo ;;
      *) head -c 300 "$DIR/$f"; echo ;;
    esac
  fi
done

echo
echo "=== audit (NDJSON) sample ==="
A="$DIR/audit.ndjson"
[ -f "$A" ] || A=$(ls "$DIR"/*.ndjson 2>/dev/null | head -1)
if [ -n "${A:-}" ] && [ -f "$A" ]; then
  wc -l < "$A" | sed 's/^/  lines: /'
  head -3 "$A" | cut -c1-400 | sed 's/^/  /'
  echo "  ..."
  tail -2 "$A" | cut -c1-400 | sed 's/^/  /'
else
  echo "  (no ndjson)"
fi
