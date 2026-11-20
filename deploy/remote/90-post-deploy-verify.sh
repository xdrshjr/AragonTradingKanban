#!/usr/bin/env bash
# 发版后完整验收：本项目可用 + 数据未变 + 别的项目未受影响
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
ck() { if [ "$2" = "$3" ]; then printf '  [✓] %-44s %s\n' "$1" "$3"; pass=$((pass+1));
       else printf '  [✗] %-44s 期望=%s 实际=%s\n' "$1" "$2" "$3"; fail=$((fail+1)); fi; }

echo "=== A. 本项目服务 ==="
for u in aragon-trading-api aragon-trading-web; do
  ck "$u active" active "$(systemctl is-active $u)"
  ck "$u enabled" enabled "$(systemctl is-enabled $u)"
done
ck "api 重启过（uptime 很小）" yes "$(curl -s $D/api/v1/health | python3 -c 'import json,sys;print("yes" if json.load(sys.stdin)["uptimeSec"]<3600 else "no")')"

echo
echo "=== B. 数据未被改动（基线取自本次发版前 ops/deploy.sh 写的指纹）==="
# ★ 不要在脚本里写死行数：上次钉死 4/3/3/6/3，数据正常增长后每次发版都假警报。
#   ops/deploy.sh 在解包**之前**把 <table> <count> 写进
#   .deploy-backups/<ts>/db-fingerprint.txt，解包后又比对一次；这里取最近一份当基线。
python3 - <<'PY'
import glob, os, sqlite3, sys
snaps = sorted(glob.glob('/opt/aragon-trading/.deploy-backups/*/db-fingerprint.txt'))
if not snaps:
    print('  [SKIP] 没有 db-fingerprint.txt（没跑过 ops/deploy.sh），无法比对基线')
    raise SystemExit(0)
snap = snaps[-1]
base = {}
for line in open(snap, encoding='utf-8'):
    parts = line.split()
    if len(parts) == 2 and parts[1].isdigit():
        base[parts[0]] = int(parts[1])
print(f'  基线文件: {snap}')
c = sqlite3.connect('file:/opt/aragon-trading/api/data/app.db?mode=ro', uri=True)
ok = True
for t, want in base.items():
    got = c.execute(f'select count(*) from {t}').fetchone()[0]
    flag = '✓' if got == want else '✗'
    if got != want: ok = False
    print(f'  [{flag}] {t:16s} 发版前={want} 现在={got}')
c.close()
raise SystemExit(0 if ok else 1)
PY
ck "数据指纹一致" 0 "$?"

echo
echo "=== C. 接口全量（含新版路由）==="
for p in health capabilities instruments watchlist cost/summary schedules "search?q=AAPL" "symbols/search?q=mt" agent/probe; do
  ck "GET /api/v1/$p" 200 "$(curl -s -o /dev/null -w '%{http_code}' "$D/api/v1/$p")"
done

# ★ 报告库列表：必须 200 且 **不做任何重定向**。
#   踩过的坑：nginx 里 `location /api/v1/reports/`（带尾斜杠）有 auto_redirect，
#   请求正好是 `/api/v1/reports` 时它 301 补斜杠，FastAPI 的 redirect_slashes 又 307
#   剥回来 → 浏览器 ERR_TOO_MANY_REDIRECTS，而只看"首页 200 / health 200"的冒烟看不见。
for p in "reports?scope=latest" "reports?scope=all" "reports?scope=trash"; do
  out=$(curl -s -o /dev/null -w '%{http_code}|%{num_redirects}' "$D/api/v1/$p")
  ck "GET /api/v1/$p（应 200 且 0 跳转）" "200|0" "$out"
done
curl -s -o /dev/null -w '  POST /api/v1/agent/probe   http=%{http_code}\n' -X POST "$D/api/v1/agent/probe" -H 'content-type: application/json' --data '{"force":false}'

echo
echo "=== D. 前端产物 / 页面 ==="
ck "首页" 200 "$(curl -s -o /dev/null -w '%{http_code}' $D/)"
ck "/US:AAPL 报告页" 200 "$(curl -s -o /dev/null -w '%{http_code}' $D/US:AAPL)"
ck "HTTP/2" 2 "$(curl -s -o /dev/null -w '%{http_version}' $D/)"
ck "sw.js no-store" "yes" "$(curl -sI $D/sw.js | grep -qi 'no-store' && echo yes || echo no)"
ck "_next 静态资源" 200 "$(A=$(curl -s $D/ | grep -o '/_next/static/chunks/[^"]*\.js' | head -1); curl -s -o /dev/null -w '%{http_code}' "$D$A")"
ck "报告导出 HTML" 200 "$(RID=$(curl -s $D/api/v1/instruments/US:AAPL/reports/latest | python3 -c 'import json,sys;print((json.load(sys.stdin) or {}).get("report",{}).get("id",""))' 2>/dev/null); curl -s -o /dev/null -w '%{http_code}' "$D/api/v1/reports/$RID/export?format=html")"

echo
echo "=== E. 别的项目未受影响 ==="
ck "aragon-router active" active "$(systemctl is-active aragon-router)"
ck "skillrouter.net" 200 "$(curl -sL -o /dev/null -w '%{http_code}' --max-time 20 https://skillrouter.net/)"
ck "skillrouter.net HTTP/2" 2 "$(curl -sL -o /dev/null -w '%{http_version}' --max-time 20 https://skillrouter.net/)"
ck "3100 监听" yes "$(ss -ltn | grep -q '127.0.0.1:3100' && echo yes || echo no)"
ck "aragonagent.com" 200 "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://aragonagent.com/)"
ck "aragonagent.com HTTP/2" 2 "$(curl -s -o /dev/null -w '%{http_version}' --max-time 20 https://aragonagent.com/)"
ck "3200 监听" yes "$(ss -ltn | grep -q ':3200' && echo yes || echo no)"
ck "8090 健康端口" yes "$(ss -ltn | grep -q ':8090' && echo yes || echo no)"
ck "nginx 配置有效" ok "$(nginx -t 2>&1 | grep -q 'test is successful' && echo ok || echo no)"
ck "三站证书都在" 3 "$(ls -d /etc/letsencrypt/live/*/ 2>/dev/null | grep -cE 'aragontrading|aragonagent|skillrouter')"
ck "3 个 site 都 enabled" 3 "$(ls /etc/nginx/sites-enabled/ | grep -cE 'aragon-router-domain|aragon-trading|aragon-web')"

echo
echo "────────────────────────────────────────────"
printf '  通过 %d 项，失败 %d 项\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
