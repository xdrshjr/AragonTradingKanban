# Aragon Research Terminal — API

FastAPI 后端：标的解析、行情/财务取数与缓存、报告版本化、`aragon exec` 生成编排与 SSE 进度。

单独运行：

```text
python -m uvicorn app.main:app --port 8000 --workers 1
```

`--workers 1` 不是建议——生成编排是进程内 `asyncio` 任务，多 worker 会让 SSE 连到没有任务的进程。`app/main.py::assert_single_worker()` 会在启动时直接报错拦下。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `app/config.py` | 全部配置（`Settings`），SQLite 相对路径归一化为绝对路径 |
| `app/db/session.py` | engine（**每连接** `PRAGMA foreign_keys=ON` / WAL / busy_timeout） |
| `app/db/migrations/` | **schema 唯一真源**。`create_all` 只允许出现在 `tests/conftest.py` |
| `app/agent/preflight.py` | `aragon info --json` 的能力预检（CLI/版本/ajv/submit_result/Key） |
| `app/agent/aragon_runner.py` | 子进程驱动：argv 拼装、NDJSON 行解析、进程树终止、`structured.value` 提取 |
| `app/agent/mock_runner.py` | 无网络确定性生成（离线演示与 E2E 的基础） |
| `app/services/snapshot.py` | 组装 run 工作区：`snapshot.json` / `bars.csv` / `fundamentals.md` / `sources.md` / `prompt.md` |
| `app/services/report_validator.py` | 结构校验 + 修补 + **接地校验**（数字必须能在快照里找到）+ 未知块裁剪 |
| `app/services/report_service.py` | 版本化 CRUD：单 `is_latest` 指针、软删、pin/archive、版本裁剪 |
| `app/services/exporter.py` | 自包含 HTML（内联 CSS + 手写 SVG）与 Markdown；零 `<script>` |
| `app/services/run_manager.py` | 六阶段推进、事件写入与广播、取消、启动恢复、工作区清理 |

## 数据库迁移

```text
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "describe the change"
```

`runs.report_id ↺ reports.run_id` 是一对**循环外键**（设计文档 §8.1）。写入顺序固定为：建 `runs`（`report_id=NULL`）→ 建 `reports`（`run_id=<run>`）→ `UPDATE runs SET report_id=<report>`。删除方向：`runs.report_id` 用 `ON DELETE SET NULL`，报告本体走软删（`deleted_at`），硬删只发生在保留期清理作业里。

## 测试

```text
python -m pytest -q
```

全部无需网络：行情走 synthetic provider，生成走 `tests/fixtures/fake_aragon.py`（可用 `FAKE_ARAGON_MODE` 切换 ok / unsatisfied / budget / usage / huge / hang / garbage 剧本）。

`tests/test_aragon_runner.py` 里的 argv 断言是刻意写的：fake CLI 永远配合，真实 CLI 不会。
