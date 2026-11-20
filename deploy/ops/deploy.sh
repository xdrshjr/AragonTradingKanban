#!/usr/bin/env bash
# ============================================================================
# ops/deploy.sh — 在服务器上把"已上传的产物"落成运行态
#   由本地 deploy/deploy.ps1 调用；也可手工分步执行。
#   前置：/root/deploy-tmp/aragon-src.tar.gz 与 aragon-web.tar.gz 已上传。
#   用法：sudo bash ops/deploy.sh [--no-src] [--no-web] [--no-restart]
# ============================================================================
set -euo pipefail

DIR=/opt/aragon-trading
TMP=/root/deploy-tmp
RUN_USER=aragon
DO_SRC=1; DO_WEB=1; DO_RESTART=1

for arg in "$@"; do
  case "$arg" in
    --no-src)     DO_SRC=0 ;;
    --no-web)     DO_WEB=0 ;;
    --no-restart) DO_RESTART=0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31m!! %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 运行（需要 systemctl / chown）"

# ── 0. 备份关键状态 ────────────────────────────────────────────────────────
log "备份 (env / db / units / nginx)"
BK="$DIR/.deploy-backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
cp "$DIR/.env" "$BK/env.bak" 2>/dev/null || true
if [ -f "$DIR/api/data/app.db" ]; then
  sqlite3 "$DIR/api/data/app.db" ".backup '$BK/app.db'" 2>/dev/null \
    || cp "$DIR/api/data/app.db" "$BK/app.db"   # 热备失败就冷拷
fi
cp /etc/systemd/system/aragon-trading-*.service "$BK/" 2>/dev/null || true
cp /etc/nginx/sites-available/aragon-trading.conf "$BK/" 2>/dev/null || true
systemctl list-units 'aragon-trading-*' --no-pager > "$BK/service-state.txt" 2>/dev/null || true
# 记下发布前的数据指纹：解包后如果变了，说明源码包里混进了运行态数据。
if [ -f "$DIR/api/data/app.db" ]; then
  python3 - "$DIR/api/data/app.db" > "$BK/db-fingerprint.txt" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
for t in ('instruments', 'watchlist', 'reports', 'runs', 'cost_ledger'):
    try:
        print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
    except Exception:
        print(t, '?')
c.close()
PY
  echo "  数据指纹: $(tr '\n' ' ' < "$BK/db-fingerprint.txt")"
fi
date -Iseconds > "$BK/backup.timestamp"
# 只留最近 5 份
ls -dt "$DIR/.deploy-backups"/*/ 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
echo "  $BK"

# ── 1. 源码 ─────────────────────────────────────────────────────────────────
if [ "$DO_SRC" = "1" ] && [ -f "$TMP/aragon-src.tar.gz" ]; then
  log "解包源码"
  # 双保险：即使本地打包漏了排除项，这里也绝不让包里的 api/data（开发库）
  # 与 api/workspace（快照/审计）落到生产上。生产库只属于这台机器。
  tar -xzf "$TMP/aragon-src.tar.gz" -C "$DIR" \
      --exclude='./api/data' --exclude='./api/data/*' \
      --exclude='./api/workspace' --exclude='./api/workspace/*' \
      --exclude='./api/.venv' --exclude='./api/.venv/*' \
      --exclude='./.aragon-home' --exclude='./.aragon-home/*'

  # 硬校验：解包后数据指纹必须与发布前一致。
  if [ -f "$BK/db-fingerprint.txt" ]; then
    AFTER=$(python3 - "$DIR/api/data/app.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
for t in ('instruments', 'watchlist', 'reports', 'runs', 'cost_ledger'):
    try:
        print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
    except Exception:
        print(t, '?')
c.close()
PY
)
    if [ "$AFTER" != "$(cat "$BK/db-fingerprint.txt")" ]; then
      echo "  发布前: $(tr '\n' ' ' < "$BK/db-fingerprint.txt")"
      echo "  发布后: $(printf '%s' "$AFTER" | tr '\n' ' ')"
      cp "$BK/app.db" "$DIR/api/data/app.db"
      chown "$RUN_USER:$RUN_USER" "$DIR/api/data/app.db"
      die "源码包覆盖了生产数据库！已从 $BK/app.db 回滚。检查打包排除项。"
    fi
    echo "  数据指纹未变 ✓"
  fi
  echo "  ok"
else
  log "跳过源码（无包或 --no-src）"
fi

# ── 2. 前端产物 ─────────────────────────────────────────────────────────────
if [ "$DO_WEB" = "1" ] && [ -f "$TMP/aragon-web.tar.gz" ]; then
  log "解包前端 standalone 产物"
  rm -rf "$DIR/web-dist.next.new"
  mkdir -p "$DIR/web-dist.new"
  tar -xzf "$TMP/aragon-web.tar.gz" -C "$DIR/web-dist.new"
  rm -rf "$DIR/web-dist.old"
  [ -d "$DIR/web-dist" ] && mv "$DIR/web-dist" "$DIR/web-dist.old"
  mv "$DIR/web-dist.new" "$DIR/web-dist"
  rm -rf "$DIR/web-dist.old"
  echo "  ok"
else
  log "跳过前端产物（无包或 --no-web）"
fi

# ── 3. 依赖 ─────────────────────────────────────────────────────────────────
if [ "$DO_SRC" = "1" ]; then
  log "同步 Python 依赖 (pip install -e .[data])"
  cd "$DIR/api"
  [ -x .venv/bin/python ] || python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip wheel setuptools
  ./.venv/bin/pip install -q -e '.[data]'
  echo "  ok"
fi

# ── 4. 迁移 ─────────────────────────────────────────────────────────────────
log "数据库迁移 (alembic upgrade head)"
cd "$DIR/api"
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m alembic current

# ── 4.5 重启前自检（关键：宁可在这一步失败，也不要让线上躺下）───────────────
log "重启前自检：能否 import 应用 + 版本是否自洽"
cd "$DIR/api"
./.venv/bin/python - <<'PY'
import sys
try:
    from app.main import app          # 触发全部 import 链（含 config/dotenv/路由）
    from app import __version__
except Exception as exc:              # noqa: BLE001
    print(f"  !! import 失败: {type(exc).__name__}: {exc}")
    sys.exit(1)
# 必须查 OpenAPI schema，不能遍历 app.routes：FastAPI >=0.116 用 _IncludedRouter
# 惰性包含子路由，顶层只有 17 个 Route/_IncludedRouter，`.path` 取不到 /api/v1/*。
try:
    paths = set(app.openapi().get("paths", {}))
except Exception as exc:              # noqa: BLE001  schema 生成炸了也是上线阻断项
    print(f"  !! OpenAPI schema 生成失败: {type(exc).__name__}: {exc}")
    sys.exit(1)
need = (
    "/api/v1/health",
    "/api/v1/capabilities",
    "/api/v1/instruments",
    "/api/v1/runs/{run_id}",          # 裸 /runs 的 POST 是 include_in_schema=False
)
missing = [p for p in need if p not in paths]
if missing:
    print(f"  !! 缺少必要路由: {missing}")
    sys.exit(1)
print(f"  ✓ import ok, version={__version__}, {len(paths)} 条路由（含 health/capabilities）")
PY

# 前端产物完整性：standalone server.js + 静态资源 + 该域名已内联
if [ "$DO_WEB" = "1" ]; then
  [ -f "$DIR/web-dist/server.js" ] || die "web-dist/server.js 缺失"
  [ -d "$DIR/web-dist/.next/static" ] || die "web-dist/.next/static 缺失"
  if grep -rq '127\.0\.0\.1:8000' "$DIR/web-dist/.next/static/chunks/" 2>/dev/null; then
    die "前端产物里仍有回环 API 地址 —— 拒绝上线（NEXT_PUBLIC_API_BASE 未生效）"
  fi
  echo "  ✓ 前端产物完整且无回环地址"
fi

# ── 5. 权限 ─────────────────────────────────────────────────────────────────
log "权限"
mkdir -p "$DIR/api/data" "$DIR/api/workspace/runs" "$DIR/api/workspace/asks" "$DIR/.aragon-home" /home/aragon
chown -R "$RUN_USER:$RUN_USER" "$DIR" /home/aragon
chmod 600 "$DIR/.env"
chmod 755 /home/aragon
echo "  ok"

# ── 6. 重启 ─────────────────────────────────────────────────────────────────
if [ "$DO_RESTART" = "1" ]; then
  log "重启服务"
  systemctl restart aragon-trading-api.service
  for i in $(seq 1 60); do
    curl -sf --max-time 3 http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf --max-time 5 http://127.0.0.1:8000/api/v1/health >/dev/null \
    || die "api 未在 60s 内就绪：journalctl -u aragon-trading-api -n 50"
  echo "  api ok"

  systemctl restart aragon-trading-web.service
  for i in $(seq 1 60); do
    curl -sf --max-time 3 http://127.0.0.1:3000/ >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf --max-time 5 http://127.0.0.1:3000/ >/dev/null \
    || die "web 未在 60s 内就绪：journalctl -u aragon-trading-web -n 50"
  echo "  web ok"

  systemctl reload nginx
  echo "  nginx reloaded"
else
  log "跳过重启（--no-restart）"
fi

# ── 7. 冒烟 ─────────────────────────────────────────────────────────────────
log "冒烟检查"
curl -s -o /dev/null -w '  https://aragontrading.online/          %{http_code}\n' https://aragontrading.online/
curl -s -o /dev/null -w '  https://aragontrading.online/api/v1/health  %{http_code}\n' https://aragontrading.online/api/v1/health
# ★ 报告库列表必须 200 且零跳转。nginx 对"名字以 / 结尾的前缀 location"有 auto_redirect：
#   以前这里的 `location /api/v1/reports/` 会把 `/api/v1/reports` 301 补斜杠，FastAPI 又
#   307 剥回来 → 死循环。只看首页/health 的冒烟网不住这种错，必须逐条钉住。
for p in "reports?scope=latest" "reports?scope=all"; do
  OUT=$(curl -s -o /dev/null -w '%{http_code}|%{num_redirects}' "https://aragontrading.online/api/v1/$p")
  printf '  /api/v1/%-26s %s (期望 200|0)\n' "$p" "$OUT"
  [ "$OUT" = "200|0" ] || die "/api/v1/$p 返回 $OUT —— 检查 nginx location 是否带尾斜杠（auto_redirect 会与 FastAPI 互踢）"
done
curl -s https://aragontrading.online/api/v1/capabilities \
  | python3 -c "import json,sys;d=json.load(sys.stdin);a=d['aragon'];print('  agentMode=%s canStructured=%s degraded=%s'%(d['agentMode'],a['canStructured'],a['degradedReason']))"
# ★v15：上线必须是 enforced（open = 所有访客拥有全部权限）；匿名访问 /admin 必须 401。
AUTH_MODE_LIVE=$(curl -s https://aragontrading.online/api/v1/auth/config   | python3 -c "import json,sys;print(json.load(sys.stdin).get('authMode','?'))" 2>/dev/null)
echo "  auth/config authMode=$AUTH_MODE_LIVE (期望 enforced)"
[ "$AUTH_MODE_LIVE" = "enforced" ] || die "authMode=$AUTH_MODE_LIVE —— 检查 systemd 单元里的 AUTH_MODE / .env（open 会让站点无鉴权）"
ADMIN_ANON=$(curl -s -o /dev/null -w '%{http_code}' https://aragontrading.online/api/v1/admin/overview)
echo "  anonymous GET /admin/overview  $ADMIN_ANON (期望 401)"
[ "$ADMIN_ANON" = "401" ] || die "匿名访问 /admin/overview 返回 $ADMIN_ANON（应为 401）"

log "完成"
