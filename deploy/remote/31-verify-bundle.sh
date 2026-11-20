#!/usr/bin/env bash
# Confirm the client bundle really carries https://aragontrading.online and no
# loopback base leaked into it (NEXT_PUBLIC_* is inlined at build time).
set -uo pipefail
D=https://aragontrading.online

curl -s "$D/" -o /tmp/home.html
mapfile -t CHUNKS < <(grep -o '/_next/static/chunks/[^"]*\.js' /tmp/home.html | sort -u | head -6)

echo "chunks referenced on /: ${#CHUNKS[@]}"
for c in "${CHUNKS[@]}"; do
  body=$(curl -s "$D$c")
  pub=$(printf '%s' "$body" | grep -c 'https://aragontrading\.online' || true)
  loop=$(printf '%s' "$body" | grep -c '127\.0\.0\.1:8000' || true)
  lh=$(printf '%s' "$body" | grep -c 'localhost:3000' || true)
  printf '%-52s public=%s loopback=%s localhost=%s\n' "$c" "$pub" "$loop" "$lh"
done

echo
echo "--- full sweep over every chunk in the build ---"
TOTAL_PUB=0; TOTAL_LOOP=0
for f in $(ls /opt/aragon-trading/web-dist/.next/static/chunks/*.js | head -200); do
  n=$(basename "$f")
  if grep -q 'aragontrading\.online' "$f"; then TOTAL_PUB=$((TOTAL_PUB+1)); fi
  if grep -q '127\.0\.0\.1:8000' "$f"; then TOTAL_LOOP=$((TOTAL_LOOP+1)); echo "  LEAK in $n"; fi
done
echo "chunks containing the public origin : $TOTAL_PUB"
echo "chunks containing 127.0.0.1:8000     : $TOTAL_LOOP"
