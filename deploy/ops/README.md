# Aragon Trading Kanban · 生产环境运维手册

| 项 | 值 |
| --- | --- |
| 域名 | **https://aragontrading.online**（`www` 同证书，HTTP 301 → HTTPS） |
| 服务器 | `47.82.166.177`（root，`~/.ssh/inkclaw.pem`） |
| 目录 | `/opt/aragon-trading` |
| 运行用户 | `aragon`（uid 996，与同机 aragon-router 共用一个用户，但目录互不重叠） |
| Web | Next.js 15 standalone，`127.0.0.1:3000` |
| API | FastAPI + uvicorn，**单 worker**，`127.0.0.1:8000` |
| 数据库 | SQLite `/opt/aragon-trading/api/data/app.db`（WAL） |
| 反代 | nginx `/etc/nginx/sites-available/aragon-trading.conf` |
| 证书 | Let's Encrypt，certbot systemd timer 自动续期 |

> ⚠️ 这台机器**不是本项目独占**：同一台跑着 aragon-router（`skillrouter.net`，
> `127.0.0.1:3100`，nginx 80/443/8090）与 **AragonAgent 产品站**
> （`aragonagent.com` / `www`，`127.0.0.1:3200`，`/etc/nginx/sites-available/aragon-web.conf`）。
> 本项目的 3000/8000 端口与 nginx `server_name` 都已避让；**改 nginx 或 443 监听参数前
> 先看 `nginx -t` 与另外两个 site 的 `listen` 行**，三个 site 共享 `:443`，
> `http2` 参数必须一致（不一致 nginx 会给那个 site 关掉 HTTP/2）。
>
> 已知遗留：`aragon-web.conf` 用的是 `listen 443 ssl;`（无 http2），`nginx -t` 会报
> `protocol options redefined` 警告；目前因加载顺序由 `aragon-router-domain`
> 生效，三站实测仍都是 HTTP/2。**该文件属另一个项目，本项目不去改它**，需要时由其
> 负责人补 `http2`。

---

## 一、日常命令

```bash
ssh -i ~/.ssh/inkclaw.pem root@47.82.166.177

/opt/aragon-trading/ops/status.sh          # 一眼看全：服务/端口/健康/引擎/数据/证书
/opt/aragon-trading/ops/logs.sh api 200    # API 最近 200 行
/opt/aragon-trading/ops/logs.sh web follow # 实时跟随 web 日志
/opt/aragon-trading/ops/logs.sh nginx-err  # nginx 错误日志
```

`status.sh` 的「认证与安全」一节（v15）会打印 `auth: enforced|open`（取自**运行中的进程**，不是 `.env`）、`trust_proxy: 1`、`cookie_secure: 1`、root 是否仍是默认口令。上线检查清单：`auth: enforced`、`trust_proxy: 1`、root 口令已更换。写路由与运维读路由（`/cost`、`/schedules`、`/agent/probe`、`capabilities` 全量）现在需要 `API_TOKEN`（service 凭据）：`deploy/remote/*.sh` 已自动读取环境变量或 `/opt/aragon-trading/.env`，且**绝不**把它发往 `http://`。忘记 root 密码：`sudo -u aragon /opt/aragon-trading/api/.venv/bin/python -m app.cli reset-root-password`（在 `/opt/aragon-trading/api` 下执行，见仓库根 README）。

| 操作 | 命令 |
| --- | --- |
| 重启 API | `systemctl restart aragon-trading-api` |
| 重启 Web | `systemctl restart aragon-trading-web` |
| 重载 nginx | `nginx -t && systemctl reload nginx` |
| 看实时日志 | `journalctl -u aragon-trading-api -f` |
| 手动备份 | `bash /opt/aragon-trading/ops/backup.sh 7` |
| 重装 unit / nginx | `bash /opt/aragon-trading/ops/install.sh` |

## 二、发版（本地一条命令）

在 Windows 本机仓库根：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
```

它做四件事：打源码包（排除 `node_modules/.venv/.next/.git`）→ 带
`NEXT_PUBLIC_API_BASE=https://aragontrading.online` 本地 `next build` 并打包
standalone 产物 → scp 两个包 → 远程跑 `ops/deploy.sh`（备份 → 解包 →
`pip install -e .[data]` → `alembic upgrade head` → 重启 → 冒烟）。

> **为什么在本地构建前端**：这台服务器 2 vCPU / 3.4 GiB，`next build` 大概率 OOM，
> 而且 `NEXT_PUBLIC_*` 是**构建期内联**的——在服务器上构建就得在服务器上放一份
> 构建期环境变量，不如本机一次构建、产物直传。（首次部署时实测：本机构建 85–115 s，
> 产物 59 MB / 1994 文件，压缩后 17 MB。）

### 两个已经踩过的坑（脚本里各有一道闸）

1. **`.next` 必须删干净再构建。** Next 会把上一轮的 hashed chunk 留在
   `web/.next/static/chunks/` 里，`app-build-manifest.json` 仍可能引用它——于是产物里
   混进**上一次构建**的代码。首轮没带 `NEXT_PUBLIC_API_BASE` 构建出的
   `127.0.0.1:8000` chunk 就是这样一直跟着走的。`deploy.ps1` 现在构建前
   `Remove-Item web\.next`，并在打包后扫一遍产物：**出现回环 API 地址就中止**。
2. **运行态数据绝不能进源码包。** `api/data/` 是本机开发库（1.8 MB、含本机跑过的
   run），打进去会**覆盖生产库**——实测发生过一次：一次发版把生产上两次真实生成
   换成了本机 18 条 run。现在三道闸：
   - 打包排除 `api/data`、`api/workspace`、`api/.venv`、`.aragon-home`；
   - `ops/deploy.sh` 解包时再 `--exclude` 一遍（双保险）；
   - 解包后比对**数据指纹**（几张表的行数），不一致就从本次备份回滚并中止发布。

   > 万一真的被覆盖：`.deploy-backups/<时间戳>/app.db` 是**解包之前**的备份，
   > 停 API → 删 `app.db-wal`/`app.db-shm` → 拷回去 → 起 API。

### `npm` 必须走 `npm.cmd`

本机 Node 装的 `npm.ps1` 在 `-Command` 分支里用
`$MyInvocation.Statement.Substring(InvocationName.Length)` 截参数，配 `& npm run build`
会截成 `pm run build`，报 `Unknown command: "pm"`。`deploy.ps1` 里显式用
`(Get-Command npm.cmd).Source`。另外脚本是 **UTF-8 带 BOM** 存的——PowerShell 5.1 会把
无 BOM 的 UTF-8 当 ANSI 读，中文注释里的引号能把解析搞坏。

### 重启前自检（`ops/deploy.sh` 的 4.5 步）

新代码若 import 失败就重启，等于把线上搞挂。所以重启前先跑：

1. `from app.main import app` —— 触发完整 import 链（含 `python-dotenv` 这类
   **没写进 pyproject 的传递依赖**）；
2. 用 **OpenAPI schema** 校验关键路由存在；
3. 前端产物完整性（`server.js` / `.next/static` / 无回环 API 地址）。

> ⚠️ **校验路由必须查 `app.openapi()["paths"]`，不能遍历 `app.routes`。**
> FastAPI ≥ 0.116（这台装的是 0.163.1）用 `_IncludedRouter` **惰性**包含子路由，
> 顶层 `app.routes` 只有 17 项且 `.path` 取不到 `/api/v1/*`——照着遍历会误判成
> "路由全丢了"并阻断发版（实测被自己这道闸拦了两次）。
>
> 另外裸 `/api/v1/runs` 的 `POST ""` 是 `include_in_schema=False`，不在 schema 里；
> 要查 `/api/v1/runs/{run_id}`。

只想重发某一层：

```powershell
... .\deploy\deploy.ps1 -SkipWeb    # 只更后端
... .\deploy\deploy.ps1 -SkipSrc    # 只更前端产物
... .\deploy\deploy.ps1 -SkipBuild  # 复用本机已有 .next/standalone
```

## 三、配置

全部在 `/opt/aragon-trading/.env`（权限 `600`，`aragon` 属主）。改完重启 API 生效。

关键项：

| 变量 | 生产值 | 说明 |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | `https://aragontrading.online` | **构建期内联**到浏览器包；改了必须重新构建前端 |
| `INTERNAL_API_BASE` | `http://127.0.0.1:8000` | RSC/SSR 走回环，不绕 TLS |
| `ALLOW_ORIGINS` | `https://aragontrading.online,https://www.aragontrading.online` | 同源部署下只是保险 |
| `AGENT_MODE` | `auto` | 预检不过才降级 mock |
| `ARAGON_BASE_URL` | `https://api.deepseek.com` | 只以 argv 形式传给子进程 |
| `ARAGON_API_KEY` | `sk-…`（明文仅在 .env，600） | `argv.json` 里已脱敏为 `***`，不入库不入日志 |
| `ARAGON_MODEL` | `deepseek-flash` | 实际 argv 见 `workspace/runs/<id>/argv.json` |

**不要把 `.env` 提交进 git。** 换 key 就 `vim /opt/aragon-trading/.env` + 重启 API。

## 四、架构接线（nginx 侧的两个要点）

1. **API 同源**：`location /api/ { proxy_pass http://aragon_trading_api; }`
   ——**不带尾斜杠**，`/api/v1/...` 原样透传（后端 router 也挂在 `/api/v1`）。
   带了尾斜杠会把前缀剥掉，全部 404。
2. **SSE 必须不缓冲**：`/api/` 段设了 `proxy_buffering off; proxy_read_timeout 3600s;`。
   进度条、追问流式输出都依赖它。

## 五、排障顺序

```bash
/opt/aragon-trading/ops/status.sh        # 先看它，六成问题一眼定位
journalctl -u aragon-trading-api -n 100  # API 起不来 / 500
journalctl -u aragon-trading-web -n 100  # 页面 500 / standalone 缺文件
tail -50 /var/log/nginx/aragon-trading.error.log
```

| 症状 | 多半是 | 处理 |
| --- | --- | --- |
| 页面 502 | `:3000` 进程死了 | `systemctl restart aragon-trading-web`；看日志里是否缺 `.next/static` |
| 页面 200 但接口全红 | `NEXT_PUBLIC_API_BASE` 烘焙错了 | 重新构建前端（`deploy.ps1`） |
| 生成一直卡 `research` | 模型端点不可达 / key 失效 | `ops/status.sh` 看 `degradedReason`；`workspace/runs/<id>/aragon.stderr.log` |
| 生成失败 `provider_unavailable` | DeepSeek 侧 5xx | runner 已自带 2 次重试，仍失败就等上游 |
| `Invalid HTTP request` | URL 里裸中文没编码 | 浏览器不会，curl 要 `--data-urlencode` |
| 454 错误 / 引擎不可用 | `aragon info` 预检不过 | `bash /opt/aragon-trading/deploy/10-check-aragon.sh` |

## 六、备份与恢复

```bash
bash /opt/aragon-trading/ops/backup.sh 7      # 手动
crontab -e                                    # 每天 03:30
# 30 3 * * * /opt/aragon-trading/ops/backup.sh 7
```

备份落在 `/opt/aragon-trading/.backups/<时间戳>/`：SQLite 热备（走 `.backup` API，
WAL 下自洽）+ `.env` + nginx 站点 + 两个 unit。

恢复：

```bash
systemctl stop aragon-trading-api
cp /opt/aragon-trading/.backups/<ts>/app.db /opt/aragon-trading/api/data/app.db
chown aragon:aragon /opt/aragon-trading/api/data/app.db
systemctl start aragon-trading-api
```

每次 `ops/deploy.sh` 也会在 `/opt/aragon-trading/.deploy-backups/` 留一份（保留 5 份）。

## 七、成本

`/api/v1/cost/summary` 与界面成本面板读同一张 `cost_ledger` 表。

⚠️ **已知口径问题（部署时实测）**：`pricing.py` 的内置价目表不认识
`deepseek-flash`，所以真实 DeepSeek 生成会被按 **Claude Sonnet** 单价估算
（实测一次 AAPL：101k 输入 / 22.7k 输出 → 估算 $0.6453，实际 DeepSeek 成本约
$0.01 量级），且 `costKnown=false`、只进 `usd_est` 不进 `usd`。
想让成本显示正确，在 `.env` 里配掉价：

```
ARAGON_PRICE_OVERRIDE={"deepseek-flash":{"in":0.00014,"out":0.00028}}
```

## 八、卸载

```bash
systemctl disable --now aragon-trading-web aragon-trading-api
rm /etc/systemd/system/aragon-trading-{api,web}.service
systemctl daemon-reload
rm /etc/nginx/sites-enabled/aragon-trading.conf \
   /etc/nginx/sites-available/aragon-trading.conf
nginx -t && systemctl reload nginx
certbot delete --cert-name aragontrading.online
rm -rf /opt/aragon-trading
```

`aragon-router` 不受影响（不同 unit、不同 site、不同端口）。
