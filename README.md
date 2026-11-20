# Aragon Research Terminal · 投研报告工作台

**一次生成，反复查看。** 输入任意投资标的（A 股 / 港股 / 美股），进入工作台：左栏标列表（15%）、中间 K 线（右区 40%）、右侧可交互的投研报告（右区 60%）。报告生成一次即成为结构化文档，再次打开是毫秒级的本地渲染——AI 只在生成时消耗一次成本。

```
┌───────────────┬──────────────────────┬────────────────────────────────────┐
│  SymbolRail   │  PricePanel          │  ReportPanel                       │
│  搜索 / 自选   │  蜡烛 · 成交量 · MA   │  评级 · 章节 Tab · 12 种内容块      │
│  最近标的      │  区间 / 周期 / 复权    │  图表 · 指标卡 · 同业 · 情景 · 来源 │
│  15%          │  34%（右区 40%）      │  51%（右区 60%）                    │
└───────────────┴──────────────────────┴────────────────────────────────────┘
        ▲ 引用角标 [S1] → 来源抽屉        ▲ 段落里的日期 → K 线自动定位
```

---

## 架构

```
Browser
  Next.js 15 (web/, :3000)  App Router · RSC 首屏 · 客户端交互
    ├─ RSC fetch ────────────┐
    └─ TanStack Query / SSE ─┼── fetch http://127.0.0.1:8000
                             ▼
Python FastAPI (api/, :8000)               ← 必须以 --workers 1 运行
  routers/ → services/ → { market_data · report_* · run_manager }
                ├─ SQLAlchemy → SQLite (api/data/app.db, WAL, 外键开启)
                ├─ workspace/runs/<runId>/  ← 快照 · 提示词 · NDJSON 审计
                └─ agent/aragon_runner → spawn(aragon exec …)
                             │
                             ▼
     aragon exec --output-format stream-json --result-schema …
     子进程 · strict 权限 · 只读工具 · 进程退出即终止
```

三层边界很清楚：**web 不含任何 LLM 或数据源依赖**，**api 不含任何 UI 逻辑**，**aragon 子进程只做一次性研究生成**。

### 为什么报告是「结构化文档」而不是 HTML 字符串

任务要求"是 HTML 那种，可以互动切换"。如果让模型直接输出 HTML：结构无法保证、无法做章节导航与图表联动、存在 XSS 面、导出要重新猜模板。因此报告是一个 **`aragon.research.report/v1` 文档**——章节 → 块（12 种判别联合：`paragraph / heading / bullets / callout / metrics / table / chart / quote / timeline / comparables / scenario / divider`）。模型只产出数据，渲染由 React 组件按 `type` 分派，未知块降级为一张标签卡片而不是让页面崩掉。

这个结构通过 `aragon exec --result-schema` 由 CLI 自己用 JSON Schema 校验并修复（返回在 `result.structured.value`），所以"报告长什么样"是**接口**而不是约定。

---

## 三分钟启动（Windows cmd.exe，逐条执行）

前置：Node ≥ 20、Python ≥ 3.11。`aragon` CLI 可选——没有它就自动进入示例模式，产品仍然完整可演示。

```text
npm run setup
```

这一步会：检查工具链 → 从 `.env.example` 生成 `.env` → 创建 `api/.venv` → 安装前后端依赖 → `alembic upgrade head`。任何缺失项都会打印逐条修复建议。

```text
npm run dev
```

| 地址 | 说明 |
| --- | --- |
| http://localhost:3000 | 前端 |
| http://localhost:8000/docs | API 文档（OpenAPI） |

想先灌入三个演示标的，让左栏不空：

```text
npm run seed
```

### 只想看演示（没有 `aragon`、没有网络）

在 `.env` 里写一行，然后 `npm run dev`：

```text
AGENT_MODE=mock
```

示例模式会完整走完六个阶段、落库一版结构合规的报告，并在三处标注"示例"：顶部横幅、报告首块 `callout(warn)`、`quality.source="mock"`。

### 单独启动

```text
cd api
python -m uvicorn app.main:app --port 8000 --workers 1
```

```text
cd web
npm run dev
```

### Windows 一键启停（PowerShell 5.1）

`npm run dev` 是**前台**跑法：任一子进程退出就收掉全部，日志混在一个终端里、带 `[api]`/`[web]` 前缀。
仓库根的两个脚本是**后台**跑法：服务独立常驻、日志分文件，适合 "起一次、关电脑前再关掉" 的用法。
两者启动参数一致（`--workers 1`、web 强制 `NODE_ENV=development`），差别只在退出语义和日志形态。

```text
PowerShell 里：
.\start-all.ps1                 # 后台起 API :8000 + Web :3000，等到就绪再打印地址并打开浏览器
.\start-all.ps1 -Restart -Mock  # 先停再起，并强制示例模式
.\stop-all.ps1                  # 关闭两个服务（手工起的同端口进程也一并收掉）
.\stop-all.ps1 -DryRun          # 只列出会被结束的进程，不真的执行

cmd.exe 里请走 powershell：
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-all.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-all.ps1
```

- 运行态（PID + 日志）写进 `.run\`（`/.run/` 已在 `.gitignore` 里，可随时删）。uvicorn 把 INFO 日志写 **stderr**，所以在 `.run\logs\api.err.log`；Next 在 `.run\logs\web.out.log`。
- `start-all.ps1` 是幂等的且**认身份**：端口上跑着的确实是本项目服务（命令行对得上、`/api/v1/health` 返回 200）才复用；占端口的是别的东西时它报冲突并跳过，不会假装就绪。
- `stop-all.ps1` 只收三类目标：`.run\*.pid` 里记录的进程（会核对进程名与启动时间，PID 被系统复用就跳过）、监听 :8000 / :3000 的进程、命令行可定位到本仓库的 `next dev`。结束前可先 `-DryRun` 看一眼。
- 参数：`-NoBrowser` 不自动开浏览器 / `-Mock` 强制 `AGENT_MODE=mock`（只对新起的进程生效）/ `-Restart` 先停再起 / `-Reload` API 带 `--reload`（**默认关闭**，见下节；`-NoReload` 是兼容别名）。
- 退出码：两个脚本都是 `0` 成功、`1` 有服务没就绪或端口没释放，可直接写进批处理判断。
- 改了 `PY_API_PORT` 记得同步 `NEXT_PUBLIC_API_BASE`（脚本会告警，但不会替你改前端指向）。
- 这两个 `.ps1` 是 Windows-only 的便捷入口，跨平台入口仍然是 `npm run dev`。

> ⚠️ **API 必须单 worker。** 生成编排是进程内的 `asyncio` 任务，订阅者队列在内存里；多 worker 会导致"任务在 A 进程、SSE 连接打到 B 进程"，表现为进度条永远不动。启动时的 `assert_single_worker()` 会直接 `RuntimeError` 拒绝启动，而不是让你在线上慢慢发现。跨进程恢复靠启动时把遗留 `running` 置为 `failed(interrupted)` + `run_events` 重放。

> ⚠️ **Schema 只有一个真源：Alembic。** `api/app` 里不存在 `create_all`；它只允许出现在 `tests/conftest.py`。校验命令：`grep -r "create_all" api/app` 必须为空。

---

## 用户与权限（v15）

站点现在有**账号**：游客只读、登录用户有每日额度、管理员管理人与站点、一个唯一的根管理员（root）管理管理员。设计与验收口径见 `docs/plans/user-auth-permissions/spec.md`。

**三种身份与一个脚本凭据**

| 身份 | 能做什么 |
| --- | --- |
| 游客（无会话） | 浏览全站：报告、行情、检索、导出、`cite` 追问；**不能**生成 / 整理 / 加自选。写操作一律 `401` |
| `user` | 游客能做的 + 生成报告、`deep` 追问（各有**每日额度**，默认 3 / 10，北京时间 0 点重置）、个人自选、整理**自己**的报告；额度用完可「申请额外额度」 |
| `admin` | `user` + 整理任何人的报告、定时刷新、成本只读、引擎自检、**后台**（概览、用户、邀请码、额度申请、审计、设置、平台精选）。管理员对 `user` 可停用 / 重置密码 / 踢下线 / 调额度 / 解锁 / 删除，**不能**动其它管理员 |
| `root`（唯一） | `admin` + 任命 / 降级管理员、改月度预算。不可被降级、停用、删除（`403 root_protected`） |
| `service`（脚本） | 带 `API_TOKEN` 的 `Authorization: Bearer`。可触发生成、整理报告、读概览 / 审计、维护平台精选；**不能**管人、额度、邀请码、注册模式与月度预算（令牌泄露的最坏后果被限死） |

- **会话**：HttpOnly Cookie（`aragon_sid`），库里只存 `sha256(令牌)`。不勾「记住我」是浏览器会话 Cookie（服务端 12 小时到期）；勾选后绝对 30 天、空闲 7 天；每人最多 10 个活会话。停用 / 改角色 / 改密**下一次请求立即生效**，不用等会话过期。
- **注册**：三个模式，管理员在「系统设置」里随时切换、**无需重启**：`open`（图形验证码）/ `invite`（只许邀请码；邀请码注册免验证码，是受限用户的保底通道）/ `closed`。邀请码永远只产生普通用户。
- **登录防护**：连错 3 次要验证码，连错 5 次锁 15 分钟（对不存在的用户名同样成立，响应与真用户**逐字相同**）；同 IP 限速。管理员可在后台解锁，或离线 `python -m app.cli unlock <用户名>`。
- **额度**：日额度先用、赠送余额后用；失败 / 取消 / 超时的生成**自动退还**；管理员 / root / 脚本 / 定时刷新不扣额度。另有**全站日总量上限**（默认 60，设置里可调，`0` = 不限）——它是没设月度预算时唯一的「人头」护栏。
- **审计**：所有后台动作与登录 / 锁定 / 改密写入只追加的 `audit_log`（保留 365 天），后台「审计日志」可筛选；日志与审计里**永不**出现密码、令牌、验证码答案。
- **`AUTH_MODE=open`**：旧的无鉴权行为（所有请求都是全权限的 `service`）。仅用于本地演示与测试；启动时 API 会打一条 ERROR 横幅。**不要**在任何别人能访问的机器上用它。

**第一次登录**

1. 空库启动时自动创建根管理员：用户名 `jdqqjr`，初始密码见 `.env.example` 的 `ROOT_ADMIN_PASSWORD`（需求里公开的那一个）。
2. 用它登录会被**强制先改密**（`AUTH_FORCE_ROOT_PASSWORD_CHANGE=1`，默认）：改完之前除 `/auth/me`、`/auth/password`、`/auth/logout` 外的接口都是 `403 password_change_required`。
3. ⚠️ `ROOT_ADMIN_PASSWORD` **只在第一次引导（库里还没有 root）时读取一次**。之后改这个环境变量**什么都不会发生**——否则谁能改 `.env` 谁就能悄悄重置 root 密码。改 root 密码：登录后在账号页改；忘了见下一节。

**上线（nginx 之后）必做**：systemd 单元里 `TRUST_PROXY=1`、`COOKIE_SECURE=1`（`deploy/systemd/aragon-trading-api.service` 已写好）。不设 `TRUST_PROXY` 则所有访客都是 `127.0.0.1`，共用一个登录限速桶；不设 `COOKIE_SECURE` 则 Cookie 缺 `Secure`。`deploy/ops/status.sh` 会逐项核对并打印 `auth: enforced` / `trust_proxy: 1`、root 是否仍是默认口令。**`API_TOKEN` 若曾以 `NEXT_PUBLIC_API_TOKEN` 的形式构建进过前端，视为已泄露，先轮换再上线**（前端已不再读它；设置它等于把管理员级凭据公开给所有访客）。

### 找回密码、注销账号、导出（v16）

没有邮件 / 短信通道，所以普通用户的「忘记密码」靠**恢复码**自助闭环：

- **恢复码**：注册成功时**只展示一次**（`XXXXX-XXXXX-XXXXX-XXXXX`，约 100 bit 熵；库里只存 `sha256`）。弹窗要求勾选「我已保存」才能继续，可复制或下载 `.txt`。没保存也能登录，账号页会常驻一条提示；老用户 / 丢了响应的人在账号页「恢复码」卡输入当前密码即可**重新生成**（旧码立即作废）。
- **找回**：登录页「忘记密码？」→ `/recover`：用户名 + 恢复码 + 新密码 + 图形验证码。成功后**全部会话被吊销**、锁定解除、**旧恢复码作废并当场签发新码**（要再保存一次），**不会自动登录**。所有「码 / 用户」类失败的响应**逐字节相同**（防枚举），失败会写审计 `auth.recovery.failure` 并出现在后台概览「安全事件（24h）」。
- **root 不适用**：根管理员继续走离线 CLI（上一节）；`python -m app.cli clear-recovery <用户名>` 可吊销某账号的恢复码。管理员可在「系统设置」里整体关闭自助找回（`authRecoveryEnabled`，初始值取 `AUTH_RECOVERY_ENABLED`）。
- **自助注销**：账号页底部「危险区」，需要**同时**输入密码与用户名。立即生效：会话全吊销、个人自选清空、待处理额度申请自动拒绝、用户名释放；账本与审计保留。root 不可注销。
- **管理员导出**：「用户」「审计」页的 CSV 导出（UTF-8 带 BOM、与当前筛选一致、单次上限 50 000 行、公式注入已转义、**不含任何凭据列**）；每次导出都写审计并限频（6 次 / 分钟）。用户表可多选后**批量赠送额度**（−50 ~ 50，全成全败）。
- **保留期**：已吊销 / 已过期的会话在 `AUTH_SESSION_RETAIN_DAYS`（默认 30 天）后由清扫循环删除，活会话不受影响。
- **HSTS**：`deploy/nginx/aragon-trading.conf` 在 server 级加了 `Strict-Transport-Security: max-age=15552000`（180 天，**不含** `includeSubDomains` / `preload`）；上线后用 `curl -I https://<域名>` 核对。

### 忘记 root 密码

没有邮件找回，也没有任何 HTTP 后门：**能在持有数据库文件的那台机器上跑命令，就是授权**。

```text
cd api
python -m app.cli reset-root-password            # 交互输入（不回显、输两遍）
python -m app.cli reset-root-password --password "新的强密码"   # 脚本用；会留在 shell 历史里
python -m app.cli create-admin <用户名>           # 再建一个管理员（无终端时生成临时密码，只打印一次）
python -m app.cli unlock <用户名>                 # 解除登录锁定
```

- 重置会：**吊销 root 的全部会话**、解除锁定、置**强制下次登录改密**、写审计 `root.password.cli_reset`（`actor=system`，不含密码）。新密码仍要满足密码策略（至少 8 位、至少两类字符、不是弱口令）。
- 退出码：`0` 成功 / `1` 被拒（密码太弱、用户名不存在…）/ `2` 环境没就绪（库打不开、还没迁移、还没有 root——先启动一次服务让它自动引导）。
- 服务运行中也可执行：被吊销的会话在服务的**下一次请求**就失效。生产上用服务账号执行：`sudo -u aragon /opt/aragon-trading/api/.venv/bin/python -m app.cli reset-root-password`（工作目录 `/opt/aragon-trading/api`）。

### 回环主机对齐（`localhost` ≠ `127.0.0.1`）

浏览器把 `localhost` 与 `127.0.0.1` 当成**两个不同的主机**，登录 Cookie 不会在它们之间共享。开发期最常见的症状：「登录成功，但立刻掉线」。

- 前端已自动处理：若页面主机与 `NEXT_PUBLIC_API_BASE` 的主机都属于 `{localhost, 127.0.0.1, [::1]}` 且不相等，API 地址的主机名会被换成页面当前的主机名（端口、协议不变）。`ALLOW_ORIGINS` 默认已同时含两种写法。
- 所以**打开页面时用哪个都行，但别中途混用**（书签、`start-all.ps1` 打开的 `http://localhost:3000`、手敲的 `http://127.0.0.1:3000` 各是各的会话）。
- 生产走 nginx 同源（`https://aragontrading.online`），不会触发这条。
- 用 curl 验证登录时同样要保持主机名一致：`curl -c jar -b jar http://127.0.0.1:8000/api/v1/auth/login …`。

---

## 环境变量

全部有默认值，`.env` 不存在也能启动（详见 `.env.example` 的注释）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PY_API_PORT` | `8000` | API 端口 |
| `DATABASE_URL` | `sqlite:///api/data/app.db` | 可换 Postgres |
| `ALLOW_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | CORS 白名单。Cookie 会话下**不许含 `*`**（启动即报错）；同源部署（nginx）留空 |
| `API_TOKEN` | 空 | v15 起是**脚本用的 `service` 凭据**（`Authorization: Bearer`），不再是整站密码：匿名 / 错误的 Bearer 按游客处理。SSE 走 60 秒 `?token=`（用 `SESSION_SECRET` 签名）。**只放服务端** |
| `NEXT_PUBLIC_API_TOKEN` | — | ⚠️ **已废弃**：`NEXT_PUBLIC_*` 会被编进浏览器包，设置它等于把 `API_TOKEN` 公开给所有访客。前端不再读取，请勿定义 |
| `AUTH_MODE` | `enforced` | `open` = 旧的无鉴权行为（所有请求为全权限 `service`），仅限本地演示 / 测试，启动打 ERROR 横幅 |
| `ROOT_ADMIN_USERNAME` / `ROOT_ADMIN_PASSWORD` | `jdqqjr` / 见 `.env.example` | **仅在库里还没有 root 时读取一次**；之后改它无效（忘记密码见「忘记 root 密码」） |
| `AUTH_FORCE_ROOT_PASSWORD_CHANGE` | `1` | 默认口令首次登录强制改密；`0` 时界面出现不可关闭的红色横幅 |
| `SESSION_SECRET` | 空 | SSE 令牌签名密钥；空 = 首次启动生成并存库（重启不变） |
| `COOKIE_SECURE` | `auto` | `auto`（https 或受信代理的 https 才加 `Secure`）/ `1` / `0`。本地 http 下加了 `Secure` 浏览器会直接丢 Cookie |
| `TRUST_PROXY` | `0` | `1` 时信任 nginx 的 `X-Forwarded-For/Proto/Host`（取最右一跳）。**nginx 之后必须为 1**（systemd 单元已设），否则所有访客共用 `127.0.0.1` 的限速桶；API 可被公网直连时绝不能设 1 |
| `QUOTA_TZ` | `Asia/Shanghai` | 「每日额度」的自然日所在时区 |
| `AUTH_MAX_SESSIONS_PER_USER` | `10` | 超出踢最旧的会话 |
| `AUTH_SESSION_ABS_DAYS` / `AUTH_SESSION_IDLE_DAYS` | `30` / `7` | 「记住我」的绝对 / 空闲有效期；不记住是浏览器会话 Cookie + 服务端 12 小时 |
| `AUTH_SESSION_RETAIN_DAYS` | `30` | 死会话（已吊销 / 空闲过期）在库里再保留多少天后被清扫；活会话不受影响 |
| `AUTH_RECOVERY_ENABLED` | `1` | 自助找回（恢复码）的**初始**开关；后台「系统设置」可随时覆盖，无需重启 |
| `RATE_LIMIT_ENABLED` | `1` | `0` 关闭进程内限速器，仅供测试 |
| `AUDIT_RETENTION_DAYS` | `365` | 审计日志保留期，每天清一次 |
| `AUTH_TEST_HOOKS` | `0` | `1` 才会挂载 `GET /auth/_test/captcha-answer`（交出验证码答案，仅 e2e 用，**绝不能**在可访问的机器上开） |
| `AGENT_MODE` | `auto` | `auto` 由预检决定 / `real` 缺能力直接报错 / `mock` 永不联网 |
| `FORCE_PREFLIGHT` | `0` | `1` 时忽略 60 秒能力缓存 |
| `ARAGON_REQUIRED_TOOLS` | `read_file,glob,grep,list_dir,submit_result` | allow 列表基线，运行时与 `info.tools` 求交集 |
| `ARAGON_BIN` | 空（`which aragon`） | 可指向绝对路径 |
| `ARAGON_PROVIDER` / `ARAGON_MODEL` | 空 | 覆盖 CLI 配置，写在子命令之前 |
| `ARAGON_MAX_TURNS` | `40` | 预算 |
| `ARAGON_MAX_DURATION_MS` | `900000` | 预算；触发即 exit 3 |
| `ARAGON_ENV_PASSTHROUGH` | 空 | 额外透传给子进程的变量名（白名单之外的正规出口） |
| `MAX_REPORT_COST_USD` | `0.60` | 仅在成本已知时告警，不阻断 |
| `DATA_PROVIDERS` | `akshare,yfinance,synthetic` | 数据源链，逐个降级 |
| `ALLOW_SYNTHETIC` | `1` | 是否允许合成数据兜底 |
| `RUN_RETENTION_DAYS` | `14` | 工作区与 `report_asks` 的保留期（同一窗口） |
| `TRASH_RETENTION_DAYS` | `30` | ★v14：回收站窗口。软删的报告在此天数后由 `purge_trash()` **硬删**（全项目唯一硬删入口）；`0` 仅供测试 |
| `ASK_DAILY_LIMIT` | `30` | 单报告 24h 内 `deep` 追问上限；超过 → `429 too_many_asks` |
| `ASK_DEEP_MAX_TURNS` / `ASK_DEEP_MAX_DURATION_MS` | `12` / `180000` | `deep` 的独立预算（与生成预算分开） |
| `ASK_MAX_QUESTION_CHARS` | `500` | 问题长度上限（`422 invalid_question`） |
| `ASK_TOP_K` | `5` | `cite` 档返回的命中块数 |
| `SEARCH_FTS` | `1` | `0` 时强制走 `LIKE` 兜底（无 FTS5 的构建自动如此） |
| `SEARCH_PINYIN` | `auto` | `auto` 按 `pypinyin` 是否可 import 决定；`off` 关闭拼音维度 |
| `SCHEDULER_ENABLED` | `1` | 调度 ticker 总开关；`0` 时 `features.schedule=false` |
| `SCHEDULER_TICK_SECONDS` | `30` | tick 间隔 |
| `SCHEDULER_LEASE_SECONDS` | `120` | 租约过期时间（进程崩溃后的回收窗口） |
| `NEXT_PUBLIC_API_BASE` | `http://127.0.0.1:8000` | 前端直连 API |
| `ENABLE_PY_REWRITE` | `0` | `1` 时走 Next 同源改写 |

### AI 引擎的能力预检（这是本项目最贵的一个坑）

`--permission-mode strict` 表示"基线为空"，`--allow-tool` 是**唯一**的注册入口。而 `submit_result` 也是工具——**不在 allow 列表里就不会注册**，模型拿不到任何提交结构化结果的渠道，`--result-schema` 便永不满足（实测：exit 3、`structured:null`，两分钟后才知道）。

所以 `api/app/agent/preflight.py` 在每次启动时跑 `aragon info --json` 并逐项判定：

| 检查项 | 判据 | 不通过时 |
| --- | --- | --- |
| CLI 可执行 | `shutil.which(ARAGON_BIN or "aragon")` | 降级 mock |
| 版本 | `>=0.6.66,<0.7` | 记 warn + 降级 mock |
| 结构化结果 | `"structured_result" in features` 且 `result.ajv === true` | 强制 mock |
| 提交工具 | `"submit_result" in tools` | 强制 mock |
| 凭据 | `effective_key_source() != "none"`（见下） | 强制 mock |

`GET /api/v1/capabilities` 会把这些原样告诉前端，界面据此显示"示例模式"横幅和**具体原因**（不是一句"引擎不可用"）。

#### 「到底有没有钥匙」只有一个判据（v9）

`ARAGON_API_KEY` 是 v9 新加的一等设置：它和 `ARAGON_BASE_URL` 一起被拼进 `aragon exec` 的
全局 flag（`--base-url` / `--api-key`），因为**实测证明环境变量不被 CLI 采纳**——
`OPENAI_BASE_URL` / `OPENAI_API_KEY` 都不会生效。所以"换一个第三方 OpenAI 兼容端点"只有
这一条路。

于是"有没有钥匙"变成两个来源的合并事实，由 `preflight.effective_key_source()` 唯一判定：

| 取值 | 含义 | `fullyCapable` |
| --- | --- | --- |
| `env` | `.env` 里的 `ARAGON_API_KEY`（**比 CLI 自己的 profile 更强**） | ✅ |
| `cli_profile` | 只有 CLI 自己的配置里有钥匙 | ✅ |
| `none` | 两处都没有 | ❌ 降级 mock |

修之前这里只看 CLI 的 `hasApiKey`：**只填 `.env` 的机器上会静默退回示例模式**——
用户拿到一份"示例"报告，而界面上没有任何一行红字。那是这类事故里最坏的一种（不是红，是绿）。
`/capabilities` 因此多出 `keyPresent` / `keySource` / `baseUrlHost` / `verified` / `verifiedAt` 五个字段。

> ⚠️ **`--api-key` 会出现在进程命令行里**（同机其他用户/工具可读）。这是当前 CLI 的唯一入口
> （环境变量不被采纳），所以本项目的承诺是**更窄但真实的那半条**：密钥不落盘、不进日志、
> 不进 SSE、不进报告、不进数据库 —— `argv.json` 落盘时会打码成 `***`，而 `build_argv()`
> 的返回值**必须**是真的（打码只在三个落盘点做，否则每次生成都会拿着 `***` 去请求）。

### 三步接上第三方端点（v9）

```text
1) 在 .env 里填三项：
     ARAGON_BASE_URL=https://api.deepseek.com
     ARAGON_API_KEY=sk-…
     ARAGON_MODEL=deepseek-flash
2) 重启 API，打开工作台，点右上角端点徽标 → 「跑一次引擎自检」（约 10 秒，约 $0.002）
3) 徽标从「未验证」变「已验证」⇒ 生成就不再用示例模式
```

自检跑的是**一次最小真实生成**：读一个 6 字节的文件 + 提交一份 153 字节的结构化结果。
它同时验证四件事——端点可达、密钥有效、`read_file` 已注册、`submit_result` 能提交。
「`aragon doctor` 全绿」不等于这些（它只检查"配置里能不能解析出一个 key"）。

| 端点 | 说明 |
| --- | --- |
| `POST /api/v1/agent/probe` | 跑一次自检（`{"force": true}` 跳过 TTL 缓存）。失败也是 **200**：那是一次测量，不是一次请求失败 |
| `GET /api/v1/agent/probe` | 纯读：`{last, verified, verifiedAt, staleReason, probeCostHintUsd, probeTtlSec}`，**不 spawn** |

`staleReason` 四态：`never` / `expired`（超 TTL）/ `identity_changed`（改了端点、模型或 CLI 版本）/ `failed`。
探针**永不阻断生成**：它只让面板变红（降级永不上抛）。有生成在跑时探针不 spawn（409 `probe_busy`）。

---

## 专业 K 线工作台

打开标的后，行情面板提供日、周、月 K 线与独立成交量及振荡指标副图。
悬停任一图窗可同步查看交易日期、OHLC、前收、涨跌额/幅、振幅、量额及指标。
头部和左栏选中标的始终显示最新周期；历史检视不会改写最新价格。

“指标”内可切换 MA5/10/20/60、EMA12/26、BOLL(20,2)，
以及 MACD(12,26,9)、RSI14、KDJ(9,3,3)、ATR14。
默认 MA5/20/60、成交量、MACD 开启。EMA 以 SMA 为初值，MACD 柱为
`2*(DIF-DEA)`；图表 RSI 全平时为 50，历史报告保留原算法。
缺少预热历史时显示“历史不足”，不会补零；成交额缺失显示“—”。
设置仅存于当前浏览器，旧指标开关会迁移到 `aragon.chart.v2`。

“更多”中可选择时间窗及复权、缩放/适配/回到最新、按交易日期定位、
切换收盘线或对数坐标、扩展大图、查看分页数据表与下载。
对数坐标仅适用于所有可显示价格均为正的行情。
普通拖拽平移；有可反查报告且服务支持时，“报告框选”或 Shift+拖拽可联动报告。
“测量”及起止日期输入只计算本地首末收盘收益、区间高低与累计量额，
不会改报告区间。图形聚焦后，左右键逐根检视、Home/End 首尾、
`+/-` 缩放、`0` 适配；Escape 逐层退出工具和固定检视，再关闭扩展。
移动端轻触固定读数；拖动和双指缩放不会误选行情。

涨跌使用相邻同复权 K 线收盘比较，可能不同于交易所的除权参考涨跌幅。
周/月按同一源日线聚合，显示真实首末交易日；末周期可能未结束。
成交量保留数据源单位：akshare A 股为手（每手 100 股），yfinance 为股，
合成数据为模拟单位，不把不同单位混算。yfinance 的复权请求会标明实际自动复权，
不会将其冒称为可验证的后复权；来源、陈旧状态和合成行情均有提示。

CSV 导出整个已加载窗口的原精度数据、全部 19 项指标及口径元数据；
PNG 导出当前可见图形和图例，包含来源/复权说明，不含 DOM 工具栏和测量卡片。
图表专用请求使用原 `/bars?profile=chart`，固定十年历史先算后裁，
首次取数可能较慢；同口径查询共用 60 秒前端缓存，无自动轮询。
旧服务缺少图表扩展字段时显示简化模式；损坏数据会显示错误并提供重试。
此功能不改变报告快照、不触发研究生成或消耗 AI 额度。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `npm run setup` | 检查环境 + 装依赖 + 建库 |
| `npm run dev` | 同时拉起 API 与 web |
| `npm run seed` | 写入三个演示标的 |
| `npm test` | 后端 pytest + 前端 vitest |
| `npm run ci` | ruff + pytest + typecheck + eslint + vitest |
| `npm run typecheck` / `npm run lint` / `npm run build` | 前端单项 |
| `npm run test:e2e` | Playwright + **零依赖 fake API**（不需要 Python / aragon / 数据库）；没有 chromium 时输出 skip 并**退出码 0**，`PW_REQUIRED=1` 才变成失败 |

数据库迁移：

```text
cd api
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "change description"
```

---

## 本期包含（v14：让「生成一次」真的能反复查看）

Iteration 7 问的是一个更朴素的问题：**生成过的研报，用户不靠记忆还能不能找回来？**
答案曾经是「不能」——首页列的是**自选标的**而不是报告，点进去永远是最新版，切到 v12 按 F5 又跳回
v13，误删的版本没有回去的路，同样的内容会变成 v14/v15/v16。（设计文档：`docs/plans/report-library/spec.md`）

| 能力 | 入口 | 关键设计 | 验收 |
| --- | --- | --- | --- |
| **U1 找得到** | 首页「最近生成的研报」+ `/reports` 报告库 | 首页从**最坏 14 次**串行 RSC fetch 降到 **2 次**（`GET /reports?scope=latest&limit=6` + `GET /watchlist`）；5 个 scope（`latest/all/starred/archived/trash`）+ keyset 游标（**不用 OFFSET**）+ 服务端计数 | AC-137~AC-140 |
| **U2 打得开 / 留得住** | 列表每一行的 `?report=<id>` 深链；历史版本横幅 | `?report=` 是"我在看哪一版"的**唯一写者**（`selectVersion` 写 URL，不再是 zustand 与 URL 两个真源）；`latestReportId` 有写死的取值顺序，横幅不会把最新版说成历史版 | AC-141~AC-143 |
| **U3 更新不重复** | 报告面板「基于此版本更新」 | 更新 = 生成新版本；内容与最新**非临时**版完全一致时**不新增版本**（`run.done.deduped=true`），但**照记这次的花费**（省一次版本 ≠ 省一次调用） | AC-144~AC-146 |
| **U4 坏数据不炸** | 详情页 / 导出 / 区间反查 / 版本比较 | 五条读路径统一走 `load_doc_safe()`：`doc_json` 损坏时返回**能过 `ReportDoc` 的最小壳** + `integrity=corrupt`，而不是 500 | AC-147 / AC-149 |
| **U5 救得回** | `/reports` 的「回收站」tab | 版本裁剪从**硬删**改为**软删**；硬删只剩 `purge_trash()` 一个入口且必须过 `TRASH_RETENTION_DAYS`（30 天）；恢复时同事务重建 FTS | AC-148 / AC-150 / AC-151 |
| **U6 生成中可见** | 首页「正在生成」条 | `active`（≤3 条）来自 `runs` 的活跃三态，与既有 `ACTIVE_STATUSES` 同一定义；`run.done` 后失效 `['reports','library']` | AC-152 |

两条不变量（写在这里是因为它们最容易被下一次改动破掉）：

1. **`scope=latest` 与 `report_service.latest_of()` 逐字同形**（`deleted_at IS NULL`，**不筛** `archived`）。
   加一个 `archived = 0` 看起来更"一致"，实际会让"最新版被归档"的标的出现两个不同的「最新」。
2. **`doc_sha1` 为 NULL 的语义是「不知道」，不是「不同」**——老数据不回填（`scripts/backfill-report-sha1.py`
   是**运维可选工具**，默认 dry-run，跑 `--apply` 等于用户显式同意推翻这条）。

---

## 本期包含（v12：让「可用稳健」在本机可被证伪）

Iteration 6 问的是前五轮没问的那个问题：**这台机器上、用默认参数，生成到底起不起来？**
答案是「起不来」——而它一直以「引擎不可用」的样子出现。

| 能力 | 入口 | 关键设计 | 验收 |
| --- | --- | --- | --- |
| **执行环境自证（S1）** | 启动日志 + `GET /capabilities` | `spawn_capability()` 是**唯一**判据（同步纯函数，因为闸门在 `RunManager.start()` 第 0 步、`mkdir` 之前）；`fullyCapable` / `degradedReason` / `capabilities.runtime` 三处都读它；`--reload` 从两个默认启动入口去掉 | AC-110~AC-113 / AC-132 |
| **墙钟兜底（S2）** | 自动 | `runner.run()` 外面套 `asyncio.wait_for`（截止 = `--max-duration` + 120s）；另有一个**不看 `SCHEDULER_ENABLED`** 的 60 秒活性清扫，把「永久 running」变成「一定结束」 | AC-114 / AC-115 / AC-130 |
| **逐字段收敛（S3）** | 降级徽标的抽屉 | `clamp_doc()` 按**块型**分派上限（`paragraph 4000 / heading 120 / callout 1200 / quote 800`），在句边界截断、留痕到 `quality.clamp`、必降 `degraded`；601 字的 summary 不再是「整份消失」 | AC-117 / AC-118 |
| **散文救援（S4，防御性）** | 降级徽标 | `structured=null` 时从模型正文里抠 JSON（有界补全括号）→ 走同一套抢救；`stop_reason=length` 归 `output_truncated`，不再伪装成「模型不听话」 | AC-119 / AC-120 / AC-134 |
| **接地口径（S5）** | 可信度面板 + `scripts/grounding-explain.py` | 派生目录补 `ttm:` / `extreme:` / `bollinger:` 三类；区间破折号（`1,296.22–1,509.76`，含 ASCII 连字符）不再被读成负号 | AC-121 / AC-122 / AC-135 |
| **前端动作确定性（S6）** | 失败卡片 | `wait` 真的等（8 秒后自动重试一次，带倒计时）；`stream_closed` 进码表；SSE 看门狗住在 `lib/sse.ts`，服务端改发**真实** `ping` 帧（`event: ping` + `data: {}`）⇒ 安静不等于断 | AC-123~AC-125 / AC-131 |
| **重试不重付（S7）** | `engine.json` 的 `session` | attempt 1 带 `--session-id`、attempt 2 带 `--resume`（且不再喂 prompt）；run 结束后只删**本 run 自己**的会话（`aragon sessions rm <id> --yes`），**没有**全局 `prune` | AC-126 / AC-127 / AC-133 |

### Windows 上为什么不能热重载（`--reload` = 能力杀手）

`uvicorn` 的 `use_subprocess = bool(reload or workers > 1)`（`uvicorn/config.py:378-379`）。
在 Windows 上它一旦为真，安装的事件循环就是 `_WindowsSelectorEventLoop`，而
`asyncio.create_subprocess_exec` **在 Selector 循环上直接抛 `NotImplementedError('')`**。

后果不是「偶发失败」，是**每一次生成都在 `research` 阶段以 `internal` 结束**（10 秒、token 全空）。
库里那行 `runs.7aa5aeaa` 就是它：`stage=research / pct=20 / error_code=internal / error_message='NotImplementedError: '`。

所以 `npm run dev` 与 `start-all.ps1` 默认都**不带** `--reload`：

```text
DEV_RELOAD=1 npm run dev          # 想热重载就显式付这个代价
powershell -File .\start-all.ps1 -Reload
```

打开后 `GET /api/v1/capabilities` 的 `runtime.loopOk` 会是 `false`、`runtime.loopReason`
是 `event_loop_selector:_WindowsSelectorEventLoop`，生成入口**同步**返回 `503 event_loop_unsupported`
（不写 `runs` 行、不建工作区目录），界面给的是「去设置」而不是「查看日志」。
服务本身不拒绝启动：既有报告仍然可读可检索，只是不能生成新报告。

### 三个新错误码（run 级，18 → 21）

| 码 | 触发 | hint 指向的动作 |
| --- | --- | --- |
| `event_loop_unsupported` | `spawn_capability()` 为假，或 spawn 抛 `NotImplementedError`（**按类型分支**，其余异常仍是 `aragon_unavailable`） | 去掉 `--reload` 后重启 |
| `agent_timeout` | `asyncio.wait_for` 超时，或活性清扫到点 | 调大 `ARAGON_MAX_DURATION_MS`，或看日志 |
| `output_truncated` | `stop_reason ∈ {length, max_tokens}`（`max_turns` 仍是 `budget_exceeded`，两件事不混） | 调大 `ARAGON_MAX_OUTPUT_TOKENS`，或减半资料 |

---

## 本期包含（v7：AI 引擎稳健性）

Iteration 4 补的不是功能，而是**证据链**：点一次「生成最新版本」之后，任何一次失败都必须能回答
三个问题——**这是什么失败、我下一步做什么、这次花了多少钱**。

| 能力 | 入口 | 关键设计 | 验收 |
| --- | --- | --- | --- |
| **确定性 spawn（H1）** | 自动（`ARAGON_AGENT_POSTURE`） | `lean` 体位注入 6 个 `--no-*`（实测每轮省 2551 input tokens，+76%，且 stderr 不再被 skills 告警污染）；`--max-tokens 40000` 取代 `auto`；`--retry-max 6` 取代 CLI 默认的 10（否则网络问题会被记成"模型太慢"） | AC-67~AC-69 |
| **进门前三道闸门（H2）** | 自动 | `schema_budget.measure()`：当前 18392 B / depth 11 / nodes 658，**深度余量只有 1**；漂移（workspace 与真源字节不一致）在 spawn 前失败；`prompt+system+schema ≤ 32KB` 拦住"把 bars.csv 内联进提示词" | AC-70~AC-72 / AC-88 |
| **失败分类学（H3）** | 失败卡片 | 15 条 run 级码 + 逐码 hint（**服务端下发**，前端不拼文案）；分类第一真源是 CLI 的 `error.code` 而不是 exit code；`run.retry` 让重试可见；失败指纹（同一根因三次 → 更强措辞） | AC-73~AC-76 / AC-82 / AC-85 |
| **成本三态（H4）** | `⌘K` →「成本…」 | `reported / estimated / unknown` 分列分措辞：`spentUsd` 只含引擎回执，`spentIncludingEstimatedUsd` 才含"按 token × 单价估算"的那部分；预算闸门用**含估算**的基数（否则对着一个恒 `known:false` 的账本永远放行） | AC-77~AC-80 / AC-86 / AC-89 |
| **提示词纪律（H5）** | 自动 | 「提交前自检」6 条 + 「资料读取顺序与预算」4 条（实测一次真实生成 72,810 input tokens，其中 `bars.csv` 47.6 KB）；`quality.prompt = {version, sha1, bytes}` 只做追溯，**不进**引擎指纹 | AC-81 |
| **能力与健康（H6）** | `/capabilities.engine` | `posture / schemaSha1 / schema{bytes,depth,nodes,headroom} / limits / priceVersion / lastFailure`；`aragon doctor` 与 `aragon models` **只 warn 不降级**（实测 `deepseek-flash` 不在内置名录里却能跑通） | AC-70 / C-22 |

### 为什么"成本"要分三个数

实测 5 次真实生成，CLI **一次都没有** 报出成本（`cost: {amount: 0, known: false}`）。
于是 `/cost/summary` 的 `spentUsd` 恒为 0、预算闸门对着空账本永远放行——它不是"没超"，是
**"永远不知道"**。而与此同时我们手上有 55.3 万 input token / 13.5 万 output token 的精确计数
与一份公开单价比。所以引入了第三种来源 `estimated`：它与 `reported` 分列、分措辞、分计数，
永不互相冒充（`NULL ≠ 0` 的语义一字未改）。

### 为什么重试要自己数

CLI 的 `retry` 事件里 `attempt` 会在新请求上**从 1 重新开始**（实测一个进程里出现两段 1→6）。
拿它当累计会少报一半重试，所以 `retries` 是本地自增，`attempt / maxRetries` 只用于显示。

## 上一期（v6：D 层 D1–D7）

Iteration 3 补的不是功能数量，而是**「凭什么是可信的」**：三个真实的投研读者问题——
「这个 1234.5 亿是从哪来的」「这份报告整体在说什么」「我这一年花了多少钱」——
在这一版里各自有了一条能走回去的路径。

| 能力 | 入口 | 关键设计 | 验收 |
| --- | --- | --- | --- |
| **数字溯源（D1/D2）** | 报告头「已核验 48/51」徽标 · 表格与指标卡上的小圆点 · 块脚注 | 数字**先分类、后比对**：`fact / projection / identifier / derived` 四类，同业估值与目标价是无条件 `projection`（不进展业分母，也不设「模型自证」的升格通道）；未接地的数字定位到**块 id**，可点、可跳 | AC-46~AC-50 / AC-60 / AC-61 / AC-66 |
| **区间反查（D3）** | 在 K 线上 **Shift + 拖拽**框一段 | 每个块算出「它说的是哪段时间」（7 种模式，锚在 `doc.asOf`，**不用 `today()`**），框选 → `GET /reports/{id}/blocks` → 报告头下方联动条列出相关段落；普通拖拽仍是图表平移——两者不能抢同一个手势 | AC-53 / AC-54 / AC-64 |
| **全文视图（D4）** | 报告头 `章节 \| 全文` 分段 | 换**容器**不换**渲染器**：`ReportRenderer` 与 12 种块一行不改，大纲用 `↑↓/Home/End`（**不碰** `j/k`，那是切标的），阅读进度线尊重「减少动画」偏好 | AC-51 / AC-52 / AC-64 / AC-65 |
| **成本治理（D5）** | `⌘K` →「成本…」 | 只追加的 `cost_ledger`（活过 14 天的 run 保留期）；`NULL ≠ 0`（未知成本单列「另有 4 次成本未知」）；月度预算默认**不设限**，设了也只在超限时 402 并允许**显式覆盖**（留痕到 `runs.reason`） | AC-55~AC-57 / AC-62 / AC-63 |
| **引擎指纹（D6）** | 版本比较顶部横幅 | `sha1(版本\|provider\|模型\|allow\|schema)` 写进 `quality.engine`；两版不同时只提示「差异可能来自模型更换而非观点变化」，**不改变任何 delta**（O-10） | AC-58 |
| **e2e 脱钩（D7）** | `npm run test:e2e` | 零依赖 fake API（`web/tests/e2e/fixtures/fake-api.mjs`）顶掉 Python；没有浏览器时**显式 skip + 退出码 0**，`PW_REQUIRED=1` 才变失败 | AC-59 |

> **这一轮最重要的两条**：①未接地的数字**只标不改**（改数比标错更危险）；②老报告不做回填
> （回填会伪造数据）——没有 `quality.grounding` / `quality.engine` 就按缺失渲染，界面明示「早期版本」。

### 为什么同业 PE 不再拉低接地率

v4 把 `comparables.peers[].pe/pb/growthPct` 和散文里的数字塞进同一个分母，于是「模型给了同业估值」
会当场把一份诚实报告判成 `degraded`，模型学到的策略就是**不给同业估值**——恰好抹掉投研里最有用的一块。
现在这些数字是 `projection` 类：**计次、展示、不进分母**（AC-47 断言「删掉整个 comparables 块，ratio 完全相等」）。

## 上一期（v4：A 层 + B 层）

Iteration 2 在「生成 → 阅读 → 引用联动 → 版本切换 → 导出」之上补了四块能力。
每一块的开关都来自 `/capabilities.features` 的**实测值**，不是硬编码的常量。

| 能力 | 入口 | 关键设计 | 验收 |
| --- | --- | --- | --- |
| **追问（A1）** | 报告头「追问」按钮 | `cite` 档零 token：BM25 + 中文 bigram 检索报告自己的块，返回原文与**校验过的**引用；`deep` 档才 spawn 一次 `aragon exec`，按日额度 30 次 | AC-25/26/27/28/40/44 |
| **全文检索（A2）** | 标列表搜索框 / `⌘K` → 「搜索标的或报告内容」 | FTS5 **一块一行**（带 `section_id`/`block_id`）；摘要由服务层从块原文切片生成，不用 `snippet()`；无 FTS5 时降级 `LIKE` 且 `degraded=true` | AC-32/33/39 |
| **版本比较（A3）** | 报告头版本变化角标 | 结构 diff（章节/块/指标），`tone = direction × polarity` **只在服务端算一次**；`polarity` 缺省即灰色，绝不猜 | AC-34/42 |
| **定时刷新（A4）** | 报告头「定时刷新」 | 只做触发源：`tick_claim()` 在 `to_thread` 里只做 DB 抢租约，`start()/schedule()` 回事件循环；容量不足时**让位**给手动生成 | AC-29/30/31/43 |
| **离线只读（A5）** | 自动 | service worker 只缓存**同源导航文档**（整页 HTML 已含报告）+ `/_next/static/*`；跨源 API 一律放行不拦截；「离线副本 · 生成于 …」的日期取自页面 `<meta>` | AC-37/45 |

### 追问为什么默认不调模型

「问一句就要一次完整生成」在成本和延迟上都不可接受，而且引入第二个幻觉面。所以默认档是**抽取式**的：

```
POST /reports/{id}/ask {question, mode:"cite"}
  → 中文 bigram 切词 → BM25（k1=1.2, b=0.75）
  → 章节标题命中 ×1.6 / 结构化块 ×1.3
  → top_k=5，且 ≥ 0.15×top1 才保留
  → 答案 = 命中块的**原文**；citations = sourceRefs ∪ 正文行内 [S1]
  → 逐条校验 ∈ doc.sources，非法条目丢弃并计入 citationsDropped
```

没有生成步骤，就没有幻觉路径。`deep` 档是用户显式点的那一下，写入 `report_asks` + `ask_events`，
**不建 `runs` 行**（C-12：ask 有自己的 `AskRegistry` 与 `POST /asks/{id}/events-token`）。

### 离线副本必须自证年龄

断网打开一份读过的报告时，页面会显示「离线副本 · 生成于 …」。这个时间**只能**来自
`<meta name="aragon-report-generated-at">`——没有它就不显示徽标，而不是显示一个可能是今天的日期。
过期数据不许冒充最新（R-19）。

---

## 新增端点（v3/v4）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/reports/{id}/ask` | `mode=cite` → 200 同步；`mode=deep` → 202 `{askId, streamUrl}` |
| GET | `/reports/{id}/asks?limit=20` | 该报告的追问历史 |
| GET | `/asks/{askId}` | 单次追问详情 |
| GET | `/asks/{askId}/events?token=` | `ask.stage/delta/usage/done/error`（复用 §7.4 帧格式） |
| POST | `/asks/{askId}/events-token` | 绑定 **askId** 的 60 秒短签名 |
| POST | `/asks/{askId}/cancel` | `202 cancelling`，已结束则 `409 ask_not_active` |
| GET | `/search?q=&limit=20` | 标的 + 报告块；每条命中带 `sectionId`/`blockId`/`snippet` |
| GET | `/reports/{a}/diff/{b}` | 结构 diff；`a === b` → `422 diff_same_report` |
| GET/POST/PATCH/DELETE | `/schedules[/{id}]` | cron 刷新；cron 非法 → `422 invalid_cron`，重复启用 → `409 duplicate_schedule` |

新增错误码：`report_not_ready`（临时版不接受 deep）、`invalid_question`、`too_many_asks`
（带 `detail.suggestMode`，前端**显式询问**才降级）、`ask_not_active`、`invalid_cron`、
`duplicate_schedule`、`diff_same_report`、`ask_deep_unavailable`（引擎不支持结构化时）。

## 新增端点（v6）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/reports/{id}/blocks?from=&to=&limit=` | 区间反查：这段时间被报告里的哪几段讲到（`limit` 夹到 200） |
| GET | `/cost/summary?window=30d` | 窗口汇总；`spentUsd` **只含已知**，未知次数单列 `unknownRuns` |
| GET | `/cost/entries?limit=50` | 账本明细（只追加表，`(kind, ref_id)` 唯一） |
| POST | `/cost/budget` | `{monthlyUsd}`；`0` = 不设限，改完即时生效（无缓存） |
| POST | `/runs/estimate` | `{symbol, kind}` → `{estimatedUsd}` 或 `{estimatedUsd: null, reason}` |
| POST | `/instruments/{id}/reports?override=1` | **既有唯一创建入口**上新增 `?override=1`；超限无 override → **402 `monthly_budget_exceeded`** |

v6 新增错误码：`monthly_budget_exceeded`（402）、`invalid_range`（422）、`range_too_wide`（422）。

> ⚠️ **既有码一个字都没改**：`budget_exceeded`（409，语义是 turns/duration 超预算）与
> `run_conflict`（409）保持原样。`POST /api/v1/runs` **仍然不存在**（405）——生成只有
> `POST /instruments/{id}/reports` 一个入口，第二个入口等于第二个并发真源。

`/capabilities` 新增 5 个实测布尔（`grounding / rangeQuery / readingMode / costLedger /
engineFingerprint`，默认 `false`）与一个**新的**顶层 `engine { fingerprint, drift,
schemaBytes, allowTools }`，`limits` 增加 `monthlyBudgetUsd / minFactClaims /
maxBlockRangeYears / maxBlockRangeDays`。

`/capabilities.features` 现在是 `mock, exportHtml, exportMd, pdf, ask, askDeep, fullTextSearch,
pinyinSearch, schedule, offline, realtimeQuote, semanticSearch`，`limits` 增加
`askDailyLimit / askMaxQuestionChars / schedulerTickSeconds`。

---

## 本期不包含（与设计方案 §18.2 的 C 层逐条对齐）

明确**不做**，并且代码里没有悬挂分支：不留半成品开关、不留"以后实现"的空接口。

| 项 | 现状 |
| --- | --- |
| PDF 导出（C1） | `501 export_unsupported` 并提示「用浏览器打印」（打印样式已完整适配）。需 headless Chromium +200MB，不做 |
| 多用户账号 / 团队协作 / 评论批注（C2） | 单用户本地优先，仅保留可选 Bearer 门禁。账号会污染所有 ACL 路径 |
| 向量检索 / embedding 服务（C3） | 不做。单份报告 ~40 块，BM25 + bigram 召回足够，且 `cite` 档的可行性不依赖向量 |
| 全市场爬取式定时生成（C4） | 不做。调度对象只能是显式配置的标的，否则数据源限流会变成常态故障 |
| 报告正文 i18n（C5） | 不做。模型直出中文；文案已集中于 `web/lib/copy.ts` |
| 实时行情推送 / Level-2 / 交易下单 | 非目标（属于后续 Kanban 阶段） |
| `chart` 块 scatter / waterfall 导出 SVG | 导出时降级为数据表（Python 侧无 ECharts），界面内正常渲染 |
| **Postgres 上的全文检索** | FTS5 是 SQLite 专有。Postgres 上迁移**照常完成**（只跳过 FTS 段并记 `fts_available=0`），`/search` 走 `LIKE` 兜底、`features.fullTextSearch=false`。**不是 `raise`**——那会让非 FTS 的表也建不出来 |

---

## 常见的「看起来像坏了」其实是设计

| 现象 | 原因 |
| --- | --- |
| 追问默认只给「命中段落 + 原文」 | `cite` 档不生成自然语言。读者要的是**依据**，不是转述 |
| 临时版（`provisional`）上追问什么都没找到 | 临时版的 `doc.sources` 是空数组，任何引用都不合法。界面会明确提示「没有来源清单」，而不是显示空白 |
| 「1 处引用未能在来源清单中找到，已丢弃」 | 模型引用了不存在的 sourceId。服务端丢弃并计数，`grounded` 置 false——宁可说没找到，不装作找到了 |
| 深度追问按钮是灰的 | `features.askDeep=false`：CLI 缺 `submit_result` / ajv / Key。`cite` 档始终可用 |
| 版本比较里 PE 上涨是绿色（利好）？ | 若显示为绿，说明该指标的 `polarity` 是 `bullish`。`polarity` 缺省时**一律中性灰**，不猜 |
| `/search` 返回 `degraded: true` | 当前构建没有 `ENABLE_FTS5`，走的是文本扫描兜底。不 500，但 `features.fullTextSearch=false` |
| 定时刷新没在整点触发 | ticker 默认 30s 一轮（`SCHEDULER_TICK_SECONDS`），且容量不足时会顺延 5 分钟让位给手动生成 |
| K 线面板出现「报价与 K 线来自不同数据源」 | `/quote`（60s 缓存）与 `/bars`（15min 缓存）**各自**走 provider 链，两侧可能落在不同源上。这不是数据坏了，是分叉被**说出来**了：`meta.reasonCode=divergent_source` + 一条中文告警，且以 K 线的数为当前口径。彻底的解不是钉住某个源，而是一个 `/screen` 端点一次解析（v7 第一项） |
| 左栏选中行没有「复权」角标，其余行也没有 | 角标只在**选中行**且口径非前复权时出现：那一行的价格来自图表那条序列（`?tf=&win=&adj=`）。图表还没数据时它退回 `/quote`，此时口径留空——实时报价本来就不复权，标成「前复权」是在替它背书 |
| 周/月线的第一根看起来「不在画布起点」 | 周/月线由**同一条日线**按 ISO 周 / 自然月聚合，`ts` 取桶内最后一根，所以首尾桶天然是残缺的。它不会被写进缓存，覆盖率只按日线判定，因此不会出现「换个周期就永远未命中」 |
| `/bars.meta.reasonCode` 不是 `ok` | 一个回答只带一个码：`uncovered`（缓存没覆盖该窗口，已重新取数）/`provider_fallback`（链上降级，告警在 `warnings`）/`mixed_source`（序列曾有两个来源，本次已接管重写）/`legacy_generation`（旧一代合成数据已退役，一次性迁移）/`divergent_source`（报价与 K 线不同源）。原因从不写在 `stale` 里 |

---

## 常见故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 顶部出现"示例模式 · <原因>" | 预检未通过 | 看 tooltip 的具体原因；装 `aragon` / 配 Key / 升级 CLI |
| 生成 2 分钟后失败 `result_schema_unsatisfied` | 模型未能提交合规结构 | 看 `api/workspace/runs/<id>/aragon.ndjson`；`ARAGON_MODEL` 换成更强的模型 |
| 报告写着"临时版" | 该次生成失败，落的是可读兜底版 | 原始产出仍可阅读；点"生成最新版本"重试 |
| 进度条长时间不动 | API 跑了多个 worker | 必须 `--workers 1`（启动会直接报错拦下） |
| 数据带"合成数据"标记 | 真实数据源全挂，降级到 synthetic | 检查网络/依赖；`DATA_PROVIDERS` 调整顺序。合成分价由**固定日历锚点**（`_WALK_ANCHOR`）单向生成：同一日期永远同一价，区间/复权/跨日都不再改写历史；锚点常量一改，全部合成价位整体变更一次（正常现象，不是数据损坏） |
| 成本显示"成本未知" | CLI 返回 `cost.known=false` | 正常现象——未知不会被伪装成 $0.00 |
| K 线空白 | 该标的无行情数据 | 面板底部会标注数据来源与根数 |
| 报告头出现「3 个数字未找到依据」 | 有三个数字在快照里找不到出处 | 点开徽标 → 每条样本都带 `blockId`，点一下跳到那一格。**不会**自动替换数字 |
| 报告头出现「数字太少，未评估」 | `factClaims < 5` | 短报告不判分：3 个数字里错 1 个就是 0.67，判 `degraded` 只会让所有短报告变黄条 |
| 版本比较顶部有琥珀横幅 | 两版的引擎指纹不同 | 只是提示「差异可能来自模型更换而非观点变化」，**不改变任何 delta 数值** |
| 点「生成最新版本」弹出「本月预算已用完」 | 你在成本面板里设了月度预算 | 主按钮是「仍然生成」——覆盖会留痕到 `runs.reason`，账本照常记 |
| 全文视图里按 `j` 把标的切走了 | 设计如此 | `j/k` 一直是「下一个/上一个标的」。大纲导航是 `↑↓/Home/End` |

每个 run 的完整审计材料在 `api/workspace/runs/<runId>/`：`argv.json`（实际命令行）、`aragon.ndjson`（逐行原始事件）、`aragon.stderr.log`、`snapshot.json`（事实底座）、`prompt.md`、`result.json`、`report.json`。

---

## 代码地图

```
api/app/
  agent/            preflight（能力预检）· aragon_runner（子进程契约）
                    fingerprint（引擎指纹）· pricing（模型单价表）
                    ask_runner（追问复用同一套 argv 契约）· mock_runner
                    prompts/（research_* + ask_*）· schemas/（report_doc + ask）
  services/         snapshot（事实底座）· report_validator（结构+接地）
                    report_service（版本化）· exporter（自包含 HTML/MD）
                    run_manager（六阶段 + SSE + 取消）· indicators · market_data/
                    block_text（块→文本 / 块 id / 引用抽取的唯一真源）
                    ask_service（cite 检索 + deep 编排）· ask_registry
                    search_index（FTS5 DDL 与逐块索引）· search_service · search_fallback
                    report_diff（结构 diff + 语义色）· cron · scheduler · scheduler_ticker
                    sse_token（run 与 ask 共用的短签名）
                    numeric_claims（数字分类与接地的唯一真源）· block_dates（区间抽取）
                    cost_service（账本 / 预估 / 预算闸门）
                    user_service · session_service · auth_service · invite_service · captcha（v15 账号与会话）
                    quota_service · quota_requests（额度账本 / 预留 / 退还 / 申请）· settings_service（带校验的设置）
                    audit_service（只追加审计）· admin_user_service · admin_overview · featured_service（后台）
                    bootstrap（根管理员引导，只读一次环境变量）
  security/         policy（逐路由默认拒绝的策略表）· deps（主体解析 / enforce）· objects（对象级规则与管理员矩阵）
                    permissions（读 contracts/permissions.json）· passwords（scrypt）· csrf · ratelimit
  cli.py            离线恢复：reset-root-password · create-admin · unlock（见「忘记 root 密码」）
  routers/          health · capabilities · symbols · instruments · reports · runs · watchlist
                    asks · search · schedules · cost（v6）· auth · me · admin（v15）
  db/               session（WAL + 外键）· models/ · migrations/（唯一 schema 真源）
web/
  app/              落地页 · [symbol] 工作台（RSC 直出 + ?report&section&block 深链）
  components/       shell（三栏 · SearchDialog）· chart（K 线）· report（12 种块 · CompareView）
                    ask（追问抽屉 + SSE）· schedule（cron 表单）· generation（SSE）· ui/
  lib/              api · types（镜像后端 schema）· layout（15/34/51 单一真源）· sse · copy
                    blocks（块 id 镜像）· focus（焦点归还）· offline（SW 注册 + 离线徽标）
                    grounding（三态判定的唯一实现 + 样本索引，v6）
  public/sw.js      手写 service worker：同源导航文档 + /_next/static，跨源一律放行
  scripts/          check-bundle.mjs（AC-35：首屏预算与懒加载边界）
```

> **与 spec §6.3 的文件粒度差异（有意为之，非遗漏）**：方案里按块拆出的
> `ParagraphBlock / HeadingBlock / BulletsBlock / QuoteBlock / TimelineBlock /
> ComparablesBlock / ScenarioBlock` 合并为 `report/blocks/MiscBlocks.tsx` +
> `ReportRenderer` 里的 `Heading`；`TimeframeBar` 与 `useChartMarkers` 并入
> `PricePanel`（分段控件）与 `CandleChart`（`chart:focus` 定位）；`ReportTabs`
> 并入 `ReportPanel`（Tab 与其键盘交互本来共享同一份 state）。12 种块、章节
> 导航、日期→K 线联动这些**能力**一个不少，只是文件更少、state 更集中。

测试：`api/tests`（含 argv 契约、fake CLI 的全部失败剧本、cron 表驱动、租约并发、依赖方向守卫、v6 的数字分类表与导入无环）、`web/tests`（含 `grounding` / `reading-view` / `range-hint` / `cost-panel` 四套新用例 + Playwright）。首屏预算由 `web/scripts/check-bundle.mjs` 机器化断言（**240.9 kB / 245 kB** —— 预算 v9 由 240 放到 245，理由与三次实测见 `check-bundle.mjs::DEFAULT_BUDGET_KB` 的注释与 spec 的 I-94；**11 个面板**均不在首屏 chunk，且 `ReadingView` / `RangeHint` / `CostPanel` / `KeyDrawer` / `EndpointBadge` / `SalvageNotice` 必须能在 `react-loadable-manifest.json` 里被证明住在 async chunk —— AC-52 的双向判据。v9 新增的三块面板进这张表，正是「把预算抬 5 kB」换来的那条更严的判据）。

> 判据不是某个固定数字（`504` 会随重构上下浮动），而是「`pytest --collect-only` 的实测数量**不得低于**改动前」。

---

## 免责声明

本项目由 AI 生成研究报告，**仅供研究参考，不构成投资建议**。
