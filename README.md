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
| `RUN_RETENTION_DAYS` | `14` | 工作区清理 |
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
| `npm run test:e2e` | Playwright（自动以 `AGENT_MODE=mock` 启动）；首次需 `npx playwright install chromium` |

数据库迁移：

```text
cd api
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "change description"
```

---

## 本期不包含（与设计方案 §15.1 逐条对齐）

明确**不做**，并且在代码里留下 501 或 TODO，不留半成品：

| 项 | 现状 |
| --- | --- |
| `POST /reports/{id}/ask`（与报告对话） | 契约已预留，返回 `501 ask_not_implemented` |
| PDF 导出 | `501 export_unsupported` 并提示"用浏览器打印"（打印样式已完整适配） |
| 自动定时批量生成全市场报告 | 未实现调度器；`runs` 表字段已预留 |
| 多用户账号 / 团队协作 / 评论批注 | 本期为单用户本地优先，仅保留可选 Bearer 门禁 |
| 实时行情推送 / Level-2 / 交易下单 | 非目标（属于后续 Kanban 阶段） |
| `chart` 块的 scatter / waterfall 导出 SVG | 导出时降级为数据表（Python 侧无 ECharts），界面内正常渲染 |
| 报告内自由问答、英文报告（i18n） | 文案已集中于 `web/lib/copy.ts`，但报告正文为模型直出中文 |

---

## 常见故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 顶部出现"示例模式 · <原因>" | 预检未通过 | 看 tooltip 的具体原因；装 `aragon` / 配 Key / 升级 CLI |
| 生成 2 分钟后失败 `result_schema_unsatisfied` | 模型未能提交合规结构 | 看 `api/workspace/runs/<id>/aragon.ndjson`；`ARAGON_MODEL` 换成更强的模型 |
| 报告写着"临时版" | 该次生成失败，落的是可读兜底版 | 原始产出仍可阅读；点"生成最新版本"重试 |
| 进度条长时间不动 | API 跑了多个 worker | 必须 `--workers 1`（启动会直接报错拦下） |
| 数据带"合成数据"标记 | 真实数据源全挂，降级到 synthetic | 检查网络/依赖；`DATA_PROVIDERS` 调整顺序 |
| 成本显示"成本未知" | CLI 返回 `cost.known=false` | 正常现象——未知不会被伪装成 $0.00 |
| K 线空白 | 该标的无行情数据 | 面板底部会标注数据来源与根数 |

每个 run 的完整审计材料在 `api/workspace/runs/<runId>/`：`argv.json`（实际命令行）、`aragon.ndjson`（逐行原始事件）、`aragon.stderr.log`、`snapshot.json`（事实底座）、`prompt.md`、`result.json`、`report.json`。

---

## 代码地图

```
api/app/
  agent/            preflight（能力预检）· aragon_runner（子进程契约）
                    mock_runner（离线确定性生成）· prompts/ · schemas/
  services/         snapshot（事实底座）· report_validator（结构+接地）
                    report_service（版本化）· exporter（自包含 HTML/MD）
                    run_manager（六阶段 + SSE + 取消）· indicators · market_data/
  routers/          health · capabilities · symbols · instruments · reports · runs · watchlist
  db/               session（WAL + 外键）· models/ · migrations/（唯一 schema 真源）
web/
  app/              落地页 · [symbol] 工作台（RSC 直出）
  components/       shell（三栏）· chart（K 线）· report（12 种块）· generation（SSE）· ui/
  lib/              api · types（镜像后端 schema）· layout（15/34/51 单一真源）· sse · copy
```

> **与 spec §6.3 的文件粒度差异（有意为之，非遗漏）**：方案里按块拆出的
> `ParagraphBlock / HeadingBlock / BulletsBlock / QuoteBlock / TimelineBlock /
> ComparablesBlock / ScenarioBlock` 合并为 `report/blocks/MiscBlocks.tsx` +
> `ReportRenderer` 里的 `Heading`；`TimeframeBar` 与 `useChartMarkers` 并入
> `PricePanel`（分段控件）与 `CandleChart`（`chart:focus` 定位）；`ReportTabs`
> 并入 `ReportPanel`（Tab 与其键盘交互本来共享同一份 state）。12 种块、章节
> 导航、日期→K 线联动这些**能力**一个不少，只是文件更少、state 更集中。

测试：`api/tests`（277 项，包含 argv 契约断言与 fake CLI 的全部失败剧本）、`web/tests`（64 项 + Playwright）。

---

## 免责声明

本项目由 AI 生成研究报告，**仅供研究参考，不构成投资建议**。
