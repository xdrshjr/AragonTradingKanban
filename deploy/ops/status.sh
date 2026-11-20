#!/usr/bin/env bash
# ============================================================================
# ops/status.sh — 一眼看清 aragontrading.online 这套服务现在怎么样
# ============================================================================
set -uo pipefail

# ★v15 用户与权限：写路由、capabilities 全量、/cost、/schedules、/agent/probe 不再对匿名开放。
# 脚本以 `service` 凭据（API_TOKEN）调用：环境变量优先，其次读服务的 .env。令牌只发往 https，
# 绝不明文发往 http://（本脚本里 http://… 只用来验证 301 跳转）。没有令牌就匿名请求，行为同以前的公开读。
TOKEN="${API_TOKEN:-$(sed -n 's/^API_TOKEN=//p' /opt/aragon-trading/.env /opt/aragon-trading/api/.env 2>/dev/null | tail -1)}"
curl() {
  local a
  for a in "$@"; do case "$a" in http://*) command curl "$@"; return;; esac; done
  command curl ${TOKEN:+-H "Authorization: Bearer $TOKEN"} "$@"
}

D="${DOMAIN:-https://aragontrading.online}"
DIR=/opt/aragon-trading

hr() { printf '%.0s─' {1..64}; echo; }
row() { printf '  %-26s %s\n' "$1" "$2"; }
ok()  { printf '  [✓] %-22s %s\n' "$1" "$2"; }
bad() { printf '  [✗] %-22s %s\n' "$1" "$2"; }

echo "Aragon Trading Kanban · 生产状态"
hr

echo "服务"
for u in aragon-trading-api aragon-trading-web nginx aragon-router; do
  a=$(systemctl is-active "$u" 2>/dev/null)
  e=$(systemctl is-enabled "$u" 2>/dev/null)
  if [ "$a" = "active" ]; then ok "$u" "$a / $e"; else bad "$u" "$a / $e"; fi
done

echo
echo "进程与端口"
row "api  Main PID" "$(systemctl show -p MainPID --value aragon-trading-api)"
row "web  Main PID" "$(systemctl show -p MainPID --value aragon-trading-web)"
row "内存 (api / web)" "$(systemctl show -p MemoryCurrent --value aragon-trading-api | numfmt --to=iec 2>/dev/null) / $(systemctl show -p MemoryCurrent --value aragon-trading-web | numfmt --to=iec 2>/dev/null)"
ss -ltnp 2>/dev/null | grep -E '127\.0\.0\.1:(3000|8000)\b' | sed 's/^/  /'
echo "  其它占用（勿动）："
ss -ltn 2>/dev/null | grep -E ':(80|443|8090|3100)\b' | sed 's/^/    /'

echo
echo "健康检查"
h=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/api/v1/health)
[ "$h" = "200" ] && ok "api :8000/health" "$h" || bad "api :8000/health" "$h"
w=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:3000/)
[ "$w" = "200" ] && ok "web :3000/" "$w" || bad "web :3000/" "$w"
p=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$D/")
[ "$p" = "200" ] && ok "public $D" "$p" || bad "public $D" "$p"
a=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$D/api/v1/health")
[ "$a" = "200" ] && ok "public /api/v1" "$a" || bad "public /api/v1" "$a"

echo
echo "认证与安全（v15）"
# authMode 取自**正在运行的进程**（/auth/config 公开），不是 .env —— 配置文件说了不算，进程说了才算。
cfg=$(curl -s --max-time 5 http://127.0.0.1:8000/api/v1/auth/config)
am=$(printf '%s' "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin).get('authMode','?'))" 2>/dev/null)
[ "$am" = "enforced" ] && ok "auth: enforced" "（open = 无鉴权，仅限本地演示）" || bad "auth: ${am:-unreachable}" "期望 enforced；open 会让所有访客拥有全部权限"
# ★v16：自助找回（恢复码）开关，同样取自运行中的进程（管理员可在后台"系统设置"里改）。
rec=$(printf '%s' "$cfg" | python3 -c "import json,sys; print('on' if json.load(sys.stdin).get('recoveryEnabled') else 'off')" 2>/dev/null)
ok "recovery: ${rec:-?}" "（自助找回密码；关闭后只能找管理员 / 离线 CLI）"
# TRUST_PROXY / COOKIE_SECURE：systemd 单元的 Environment= 优先，其次是 .env（与进程读取顺序一致）。
unit_env() { systemctl show -p Environment --value aragon-trading-api 2>/dev/null | xargs -n1 | sed -n "s/^$1=//p" | tail -1; }
dot_env() { sed -n "s/^$1=//p" "$DIR/.env" "$DIR/api/.env" 2>/dev/null | tail -1; }
tp=$(unit_env TRUST_PROXY); [ -z "$tp" ] && tp=$(dot_env TRUST_PROXY)
cs=$(unit_env COOKIE_SECURE); [ -z "$cs" ] && cs=$(dot_env COOKIE_SECURE)
case "$tp" in 1|true|yes|on) ok "trust_proxy: 1" "已信任 nginx 的 X-Forwarded-*";; *) bad "trust_proxy: ${tp:-0}" "nginx 之后必须为 1，否则所有访客共用 127.0.0.1 的限速桶";; esac
case "$cs" in 1|true|yes|on) ok "cookie_secure: 1" "";; *) bad "cookie_secure: ${cs:-auto}" "站点走 https 时建议显式设为 1";; esac
row "注册模式" "$(printf '%s' "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin).get('registrationMode','?'))" 2>/dev/null)"
python3 - <<'PY'
import sqlite3, os
db='/opt/aragon-trading/api/data/app.db'
if not os.path.exists(db):
    raise SystemExit
c=sqlite3.connect(f'file:{db}?mode=ro', uri=True)
try:
    root=c.execute("select username, password_is_default, must_change_password from users where role='root'").fetchone()
    if root is None:
        print('  [✗] %-22s %s' % ('root', '库里没有 root（服务启动一次会自动引导）'))
    elif root[1]:
        print('  [✗] %-22s %s' % ('root 仍是默认口令', '%s：请登录后立即修改（%s）' % (root[0], '下次登录会被强制改密' if root[2] else '未设强制改密！')))
    else:
        print('  [✓] %-22s %s' % ('root 口令已更换', root[0]))
    users=c.execute("select count(*) from users where status!='deleted'").fetchone()[0]
    print('  %-26s %s' % ('用户数（含管理员）', users))
except Exception as e:
    print('  %-26s (%s)' % ('用户表', e))
c.close()
PY

echo
echo "AI 引擎"
curl -s --max-time 20 "$D/api/v1/capabilities" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('  (capabilities 不可读)'); raise SystemExit
a=d.get('aragon',{})
print('  %-26s %s' % ('agentMode', d.get('agentMode')))
print('  %-26s %s' % ('aragon 版本', a.get('version')))
print('  %-26s %s / %s' % ('provider / model', a.get('provider'), a.get('model')))
print('  %-26s %s' % ('结构化结果可用', a.get('canStructured')))
print('  %-26s %s' % ('submit_result 已注册', a.get('hasSubmitResult')))
print('  %-26s %s' % ('keySource', a.get('keySource')))
print('  %-26s %s' % ('降级原因', a.get('degradedReason') or '（无）'))
"

echo
echo "数据"
python3 - <<'PY'
import sqlite3, os
db='/opt/aragon-trading/api/data/app.db'
if not os.path.exists(db):
    print('  (数据库不存在)'); raise SystemExit
c=sqlite3.connect(f'file:{db}?mode=ro', uri=True)
for label, q in [('instrument', 'select count(*) from instruments'),
                 ('watchlist',  'select count(*) from watchlist'),
                 ('report',     'select count(*) from reports'),
                 ('run',        'select count(*) from runs'),
                 ('run 失败',   "select count(*) from runs where status='failed'"),
                 ('成本已估($)', "select coalesce(round(sum(usd_est),4),0) from cost_ledger"),
                 ('成本已核($)', "select coalesce(round(sum(usd),4),0) from cost_ledger")]:
    try: print('  %-26s %s' % (label, c.execute(q).fetchone()[0]))
    except Exception as e: print('  %-26s (%s)' % (label, e))
c.close()
print('  %-26s %s' % ('db 大小', os.popen(f'du -h {db}').read().split()[0]))
PY

echo
echo "磁盘 / 证书"
row "根分区" "$(df -h / | tail -1 | awk '{print $3" used / "$2" ("$5")"}')"
row "项目目录" "$(du -sh $DIR | awk '{print $1}')"
row "代码包版本" "$(git -C $DIR rev-parse --short HEAD 2>/dev/null || echo '（未用 git 部署）')"
ce=$(openssl x509 -enddate -noout -in /etc/letsencrypt/live/aragontrading.online/fullchain.pem 2>/dev/null | cut -d= -f2)
row "TLS 到期" "$ce"
hn=$(hostname); row "主机" "$hn · $(hostname -I | awk '{print $1}')"
hr
echo "日志：journalctl -u aragon-trading-api -f   /   -u aragon-trading-web -f"
echo "nginx：tail -f /var/log/nginx/aragon-trading.error.log"
