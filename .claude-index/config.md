# Index Configuration

> Managed by the `project-indexer` skill. Regenerate with「regenerate index」/「更新索引」.
> This run was **unattended**, so every question took its deterministic default
> (standard exclusions, auto-detected priorities, auto-detected tech stack,
> Clean Code preset A = Strict, environments skipped). Details in the run summary.

## Excluded Directories

- node_modules
- .git
- .next
- dist
- build
- out
- coverage
- `__pycache__`
- .venv / venv
- .ruff_cache
- .pytest_cache
- .mypy_cache
- .run
- .agentmesh
- .claude-index
- web-dist
- playwright-report
- test-results
- api/workspace/runs
- api/workspace/asks
- api/data
- docs/plans/user-auth-permissions/walkthrough (61 screenshot PNGs)

## Priority Directories

- api/app/ (FastAPI 后端：routers → services → db)
- api/tests/
- web/app/ (Next.js App Router 路由)
- web/components/ (三栏工作台与报告渲染)
- web/lib/ (前端 API 客户端、类型镜像、布局常量)
- contracts/ (跨端契约：权限表、run 错误码)
- deploy/ (systemd / nginx / 运维脚本)

## Tech Stack

- Frontend: Next.js 15 (App Router, RSC) · React 19 · TypeScript 5.7 · Tailwind CSS 4 · TanStack Query · zustand · lightweight-charts · echarts · Radix UI · vitest + Playwright
- Backend: Python 3.11+ · FastAPI · SQLAlchemy 2 + Alembic · SQLite (WAL) · uvicorn (`--workers 1`)
- Agent 引擎: `aragon exec` 子进程（stream-json + `--result-schema`）
- Language: Python 与 TypeScript 双主语言（Python 234 个源文件 / TS+TSX 225 个）
- Tooling: npm workspaces-less 双包（根 `package.json` 编排 + `web/`），ruff，eslint，pytest，vitest，Playwright

## Index Settings

- Generated: 2026-11-20 09:55 (-0500)
- Project Root: `M:\takoAI\AragonTradingKanban`
- Index Version: 1.0
- Environments Configured: No
- Project Size: Large (≈1,000 files after exclusions) → focused index: 核心代码全覆盖，测试与静态资源按目录汇总

## Clean Code Settings

- Enabled: Yes
- Preset: strict
- MAX_FILE_LINES: 800
- MAX_METHOD_LINES: 50
- MAX_FUNCTION_PARAMS: 5
- MAX_CYCLOMATIC: 10
- MAX_LINE_LENGTH: 80
- MAX_NESTING_DEPTH: 4

> Preset `strict` (file 1000 / method 60 / params 5 / cyclomatic 10) intersected with
> the language-aware baseline for the two primary languages:
> Python (800 / 50 / 80 / 5) and TypeScript (1000 / 60 / 100 / 5) → **min** per
> column = 800 / 50 / 80 / 5. `MAX_CYCLOMATIC` 10 and `MAX_NESTING_DEPTH` 4 are the
> fixed defaults. Polyglot repos take the tightest rule so nobody applies the loose
> one to the wrong file.

## CLAUDE.md Budget

- Compaction: Enabled
- Measured: 2026-11-20 09:58 (-0500)
- CLAUDE.md Characters: 4,846
- Tier: lean (<= 40,000) — no compaction pass run (nothing to do beyond Step 6)

## Custom Rules

- 生成物/运行态不参与索引：`api/workspace/runs/**` 每次 run 落盘，是运行时数据而非源码。
- 项目里 `web/public/logo/*.png` 与 `docs/plans/**/walkthrough/*.png` 是设计稿与验收截图，只登记目录不登记文件。
