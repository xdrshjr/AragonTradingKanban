#!/usr/bin/env bash
# 查清 app.routes 到底是什么（新版 FastAPI 用 _IncludedRouter 惰性包含）
set -uo pipefail
cd /opt/aragon-trading/api
./.venv/bin/python - <<'PY'
import fastapi
print("  fastapi:", fastapi.__version__)
from app.main import app

print("  len(app.routes):", len(app.routes))
for r in app.routes:
    print("   ", type(r).__name__, getattr(r, "path", getattr(r, "prefix", "-")))

paths = sorted(app.openapi().get("paths", {}))
print("  openapi paths:", len(paths))
for p in paths[:8]:
    print("   ", p)
print("  has /api/v1/health:", "/api/v1/health" in paths)
print("  has /api/v1/capabilities:", "/api/v1/capabilities" in paths)
PY
