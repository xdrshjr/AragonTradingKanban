#!/usr/bin/env bash
# 逐条验收：服务 / 端口 / 健康 / TLS / 同源 API / 邻项目未受影响 / 重启存活
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
D=https://aragontrading.online
pass=0; fail=0
ck() { # ck "<名称>" "<期望>" "<实际>"
  if [ "$2" = "$3" ]; then printf '  [✓] %-46s %s\n' "$1" "$3"; pass=$((pass+1));
  else printf '  [✗] %-46s 期望=%s 实际=%s\n' "$1" "$2" "$3"; fail=$((fail+1)); fi
}
ckin() { # 子串包含
  case "$3" in *"$2"*) printf '  [✓] %-46s %s\n' "$1" "含 '$2'"; pass=$((pass+1));;
  *) printf '  [✗] %-46s 期望含=%s 实际=%s\n' "$1" "$2" "$3"; fail=$((fail+1));; esac
}

echo "=== 1. systemd ==="
for u in aragon-trading-api aragon-trading-web; do
  ck "$u active"   active   "$(systemctl is-active $u)"
  ck "$u enabled"  enabled  "$(systemctl is-enabled $u)"
done

echo
echo "=== 2. 端口只监听回环 ==="
ck "api 仅 127.0.0.1:8000" "yes" "$(ss -ltn | grep -q '127.0.0.1:8000' && ! ss -ltn | grep -qE '0.0.0.0:8000|\[::\]:8000' && echo yes || echo no)"
ck "web 仅 127.0.0.1:3000" "yes" "$(ss -ltn | grep -q '127.0.0.1:3000' && ! ss -ltn | grep -qE '0.0.0.0:3000|\[::\]:3000' && echo yes || echo no)"

echo
echo "=== 3. 健康 ==="
ck "GET $D/" 200 "$(curl -s -o /dev/null -w '%{http_code}' $D/)"
ck "GET $D/api/v1/health" 200 "$(curl -s -o /dev/null -w '%{http_code}' $D/api/v1/health)"
ck "GET $D/US:AAPL (报告页)" 200 "$(curl -s -o /dev/null -w '%{http_code}' $D/US:AAPL)"
ck "HTTP→HTTPS 301" 301 "$(curl -s -o /dev/null -w '%{http_code}' http://aragontrading.online/)"
ck "www 可达" 200 "$(curl -s -o /dev/null -w '%{http_code}' https://www.aragontrading.online/)"

echo
echo "=== 4. TLS ==="
ck "HTTP/2 协商" 2 "$(curl -s -o /dev/null -w '%{http_version}' $D/)"
SUBJ=$(echo | openssl s_client -connect aragontrading.online:443 -servername aragontrading.online 2>/dev/null | openssl x509 -noout -subject 2>/dev/null)
ckin "证书 CN" "aragontrading.online" "$SUBJ"
V=$(echo | openssl s_client -connect aragontrading.online:443 -servername aragontrading.online 2>/dev/null | openssl x509 -noout -checkend 604800 2>&1)
ckin "证书 7 天内不过期" "will not expire" "$V"

echo
echo "=== 5. 引擎与同源 API ==="
CAP=$(curl -s $D/api/v1/capabilities)
ckin "agentMode=real" '"agentMode":"real"' "$CAP"
ckin "canStructured" '"canStructured":true' "$CAP"
ckin "无降级原因" '"degradedReason":null' "$CAP"
ckin "keySource=env" '"keySource":"env"' "$CAP"
for p in instruments watchlist cost/summary schedules search?q=AAPL; do
  ck "GET /api/v1/$p" 200 "$(curl -s -o /dev/null -w '%{http_code}' "$D/api/v1/$p")"
done

echo
echo "=== 6. 邻项目 aragon-router 未受影响 ==="
ck "aragon-router active" active "$(systemctl is-active aragon-router)"
ck "3100 仍在监听" "yes" "$(ss -ltn | grep -q '127.0.0.1:3100' && echo yes || echo no)"
ck "skillrouter.net 仍可达(跟随跳转)" 200 "$(curl -sL -o /dev/null -w '%{http_code}' --max-time 20 https://skillrouter.net/)"
ck "8090 健康端口仍在" "yes" "$(ss -ltn | grep -q ':8090' && echo yes || echo no)"
ck "nginx 配置测试通过" "ok" "$(nginx -t 2>&1 | grep -c 'test is successful' | sed 's/1/ok/')"
ck "nginx 无 warn" 0 "$(nginx -t 2>&1 | grep -c '\[warn\]')"

echo
echo "=== 7. 数据 ==="
DB=/opt/aragon-trading/api/data/app.db
ck "生产库存在" "yes" "$([ -f $DB ] && echo yes || echo no)"
ck "run 数（AAPL+NVDA）" 2 "$(python3 -c "import sqlite3;print(sqlite3.connect('file:$DB?mode=ro',uri=True).execute('select count(*) from runs').fetchone()[0])")"
ck "report 数" 2 "$(python3 -c "import sqlite3;print(sqlite3.connect('file:$DB?mode=ro',uri=True).execute('select count(*) from reports').fetchone()[0])")"
ck "watchlist 数" 3 "$(python3 -c "import sqlite3;print(sqlite3.connect('file:$DB?mode=ro',uri=True).execute('select count(*) from watchlist').fetchone()[0])")"
ck "alembic head" ok "$(cd /opt/aragon-trading/api && ./.venv/bin/python -m alembic current 2>/dev/null | grep -q '0005_engine_probes (head)' && echo ok || echo no)"

echo
echo "=== 8. 重启存活（systemd 重启后自动恢复）==="
systemctl restart aragon-trading-web.service
for i in $(seq 1 30); do curl -sf --max-time 2 http://127.0.0.1:3000/ >/dev/null 2>&1 && break; sleep 1; done
ck "web 重启后 :3000" 200 "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/)"
ck "web 重启后公网" 200 "$(curl -s -o /dev/null -w '%{http_code}' $D/)"

echo
echo "────────────────────────────────────────────"
printf '  通过 %d 项，失败 %d 项\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1

