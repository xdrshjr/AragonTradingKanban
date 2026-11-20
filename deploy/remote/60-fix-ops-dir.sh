#!/usr/bin/env bash
# Fix the ops/ directory (a `cp *.sh` also dragged the one-off verify scripts in)
# and lay the deploy sources where ops/install.sh expects them.
set -uo pipefail
DIR=/opt/aragon-trading

echo "=== ops/ 只保留运维脚本 ==="
cd "$DIR/ops"
KEEP="status.sh deploy.sh logs.sh install.sh backup.sh"
for f in *.sh; do
  case " $KEEP " in
    *" $f "*) ;;
    *) rm -f "$f"; echo "  removed stray: $f" ;;
  esac
done
ls -l | sed 's/^/  /'

echo
echo "=== bash -n 语法检查 ==="
fail=0
for f in "$DIR"/ops/*.sh; do
  if bash -n "$f" 2>/tmp/syn.err; then
    echo "  ok   $(basename "$f")"
  else
    echo "  FAIL $(basename "$f"): $(cat /tmp/syn.err)"; fail=1
  fi
done

echo
echo "=== deploy/ 源文件 ==="
mkdir -p "$DIR/deploy/systemd" "$DIR/deploy/nginx" "$DIR/deploy/remote"
ls -R "$DIR/deploy" | sed 's/^/  /'

chown -R aragon:aragon "$DIR/ops" "$DIR/deploy"
exit "$fail"
