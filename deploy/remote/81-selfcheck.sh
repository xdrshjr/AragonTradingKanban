#!/usr/bin/env bash
# 单独验证"重启前自检"这道闸本身能不能跑通（生产磁盘上已是新代码）。
set -uo pipefail
cd /opt/aragon-trading/api
./.venv/bin/python - <<'PY'
from app.main import app
from app import __version__
routes = [p for p in (getattr(r, "path", None) for r in app.routes) if p]
print("  version      :", __version__)
print("  routes       :", len(routes))
print("  /health      :", "/api/v1/health" in routes)
print("  /capabilities:", "/api/v1/capabilities" in routes)
try:
    import dotenv
    print("  python-dotenv: importable")
except Exception as e:
    print("  python-dotenv: MISSING ->", e)
    raise SystemExit(1)
PY
echo "  selfcheck exit=$?"
