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
- 参数：`-NoBrowser` 不自动开浏览器 / `-Mock` 强制 `AGENT_MODE=mock`（只对新起的进程生效）/ `-Restart` 先停再起 / `-NoReload` API 不带 `--reload`。
- 退出码：两个脚本都是 `0` 成功、`1` 有服务没就绪或端口没释放，可直接写进批处理判断。
- 改了 `PY_API_PORT` 记得同步 `NEXT_PUBLIC_API_BASE`（脚本会告警，但不会替你改前端指向）。
- 这两个 `.ps1` 是 Windows-only 的便捷入口，跨平台入口仍然是 `npm run dev`。

> ⚠️ **API 必须单 worker。** 生成编排是进程内的 `asyncio` 任务，订阅者队列在内存里；多 worker 会导致"任务在 A 进程、SSE 连接打到 B 进程"，表现为进度条永远不动。启动时的 `assert_single_worker()` 会直接 `RuntimeError` 拒绝启动，而不是让你在线上慢慢发现。跨进程恢复靠启动时把遗留 `running` 置为 `failed(interrupted)` + `run_events` 重放。

> ⚠️ **Schema 只有一个真源：Alembic。** `api/app` 里不存在 `create_all`；它只允许出现在 `tests/conftest.py`。校验命令：`grep -r "create_all" api/app` 必须为空。

---

## 环境变量

全部有默认值，`.env` 不存在也能启动（详见 `.env.example` 的注释）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PY_API_PORT` | `8000` | API 端口 |
| `DATABASE_URL` | `sqlite:///api/data/app.db` | 可换 Postgres |
| `ALLOW_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | CORS |
| `API_TOKEN` | 空 | 非空时启用 Bearer；SSE 例外，走 60 秒 `?token=` 短签名 |
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
| 凭据 | `hasApiKey === true` | 强制 mock |

`GET /api/v1/capabilities` 会把这些原样告诉前端，界面据此显示"示例模式"横幅和**具体原因**（不是一句"引擎不可用"）。

---

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
  routers/          health · capabilities · symbols · instruments · reports · runs · watchlist
                    asks · search · schedules · cost（v6）
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

测试：`api/tests`（含 argv 契约、fake CLI 的全部失败剧本、cron 表驱动、租约并发、依赖方向守卫、v6 的数字分类表与导入无环）、`web/tests`（含 `grounding` / `reading-view` / `range-hint` / `cost-panel` 四套新用例 + Playwright）。首屏预算由 `web/scripts/check-bundle.mjs` 机器化断言（**238.5 kB / 240 kB**，八个面板均不在首屏 chunk，且 `ReadingView` / `RangeHint` / `CostPanel` 必须能在 `react-loadable-manifest.json` 里被证明住在 async chunk —— AC-52 的双向判据）。

> 判据不是某个固定数字（`504` 会随重构上下浮动），而是「`pytest --collect-only` 的实测数量**不得低于**改动前」。

---

## 免责声明

本项目由 AI 生成研究报告，**仅供研究参考，不构成投资建议**。
