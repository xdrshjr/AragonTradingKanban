#!/usr/bin/env bash
# 新版 capabilities 的运行时字段是否正常暴露（v12 的 sessionDirMb / retryLayersWarning）
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
curl -s https://aragontrading.online/api/v1/capabilities -o /tmp/cap.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/cap.json'))
print("top keys:", sorted(d.keys()))
print()
print("runtime:", json.dumps(d.get("runtime"), ensure_ascii=False))
a = d.get("aragon") or {}
print()
print("aragon:")
for k in ("available", "versionOk", "canStructured", "hasSubmitResult", "keySource",
          "keyPresent", "degradedReason", "baseUrlHost", "modelKnown", "retryLayersWarning",
          "sessionDirMb"):
    if k in a:
        print(f"  {k}: {a[k]}")
print()
print("agentMode:", d.get("agentMode"))
print("features:", json.dumps(d.get("features"), ensure_ascii=False)[:400])
print("limits:", json.dumps(d.get("limits"), ensure_ascii=False)[:400])
PY
