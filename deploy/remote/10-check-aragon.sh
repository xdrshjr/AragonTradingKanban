#!/usr/bin/env bash
# Inspect `aragon info --json` as the runtime user and print only what preflight reads.
set -u
export PATH=/opt/node22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/home/aragon
export ARAGON_HOME="${ARAGON_HOME:-/opt/aragon-trading/.aragon-home}"

mkdir -p "$ARAGON_HOME"

timeout 120 aragon info --json > /tmp/aragon-info.json 2>/tmp/aragon-info.err
echo "exit=$?"
python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/aragon-info.json'))
except Exception as e:
    print('parse failed:', e)
    print(open('/tmp/aragon-info.err').read()[:2000])
    raise SystemExit(1)
print('cli        :', d.get('cli'))
print('home       :', d.get('home'))
print('provider   :', d.get('provider'), '/', d.get('model'))
print('hasApiKey  :', d.get('hasApiKey'))
feats = d.get('features') or []
print('structured_result in features:', 'structured_result' in feats)
print('submit_result in tools        :', 'submit_result' in (d.get('tools') or []))
print('result block:', json.dumps(d.get('result'))[:600])
missing = [f for f in ('structured_result',) if f not in feats]
print('features count:', len(feats))
print('features:', ','.join(feats))
PY
