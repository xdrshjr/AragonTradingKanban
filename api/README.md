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
| `app/agent/preflight.py` | `aragon info --json` 的能力预检（CLI/版本/ajv/submit_result/Key）；`spawn_capability()` 是「本进程能不能 spawn 子进程」的**唯一**判据（Windows 要 Proactor loop，`--reload` 必坏），`fullyCapable` / `degradedReason` / `capabilities.runtime` 三处都读它；`session_dir_size_mb()` 只读量会话目录（60s 缓存）、`running_loop_name()` 只报类名、不另做判定 |
| `app/agent/aragon_runner.py` | 子进程驱动：argv 拼装、NDJSON 行解析、进程树终止、`structured.value` 提取 |
| `app/agent/mock_runner.py` | 无网络确定性生成（离线演示与 E2E 的基础） |
| `app/agent/failure.py` | run 级失败分类学：一张码表 + `hint_for()`（`hint` 只写在 `FailureSpec` 里，写别处没人读）；`event_loop_unsupported` / `agent_timeout` / `output_truncated` 只追加，`STOP_REASON_MAP` 把 `length` / `max_tokens` 归到 `output_truncated`（截断 ≠ 轮次烧完），`max_turns` 仍是 `budget_exceeded` |
| `app/services/snapshot.py` | 组装 run 工作区：`snapshot.json` / `bars.csv` / `fundamentals.md` / `sources.md` / `prompt.md` |
| `app/services/report_validator.py` | 结构校验 + 修补 + **接地校验**（数字必须能在快照里找到）+ 未知块裁剪；`clamp_doc()` 只收敛「长了/多了」的格式（按句边界截，记 `quality.clamp`、强制 `degraded`），`rescue_from_text()` 是 L1.5（从正文捞回 JSON，有界补括号），`SalvageReport.as_payload()` 发 `prunedBlocks` / `clamped` / `how` |
| `app/services/report_service.py` | 版本化 CRUD：单 `is_latest` 指针、软删、pin/archive、版本裁剪 |
| `app/services/exporter.py` | 自包含 HTML（内联 CSS + 手写 SVG）与 Markdown；零 `<script>` |
| `app/services/run_manager.py` | 六阶段推进、事件写入与广播、取消、启动恢复、工作区清理；`hard_deadline_seconds()` 给 `runner.run()` 套一层 `asyncio.wait_for` 硬截止（超时 ⇒ `agent_timeout`，`--max-duration` 管不住卡死的 CLI），`sweep_stale_runs()` 是**同步**对账（跳过 `_tasks` 里还活着的 run，不抢活协程的活），`_cleanup_session()` 只删本 run 的 `sessions rm <id> --yes`、绝不用全局 `prune` |
| `app/main.py` | 应用装配：单 worker 断言、启动即自证 `spawn_capability()`（不 raise）、迁移与启动恢复；`STALE_SWEEP_SECONDS`（60s）+ `_sweep_loop()` 是**独立于 `SCHEDULER_ENABLED`** 的常驻活性清扫 |
| `app/routers/runs.py` | run 的创建 / 取消 / 事件流；SSE 保活发**真实帧** `event: ping` + `data: {}`（注释帧到不了 `EventSource`，前端看门狗必然假断流重连） |

## 用户、权限与离线恢复（v15）

默认 `AUTH_MODE=enforced`：每个 `/api/v1` 路由都在 `app/security/policy.py` 的策略表里登记了所需权限（默认拒绝；启动期会校验"策略表 == 真实路由"，漏登记进程起不来）。`tests/conftest.py` 把测试默认设为 `AUTH_MODE=open`，需要真实鉴权的用例用 `auth_client` / `make_user` / `login_as` 夹具。

| 模块 | 职责 |
| --- | --- |
| `app/security/` | `principal`（身份）· `permissions`（读 `contracts/permissions.json`）· `policy`（策略表）· `deps`（Cookie→主体、`enforce`）· `objects`（对象级规则、管理员矩阵）· `passwords`（scrypt，KDF 并发上限）· `csrf`（Origin 校验，忽略 scheme）· `ratelimit` |
| `app/routers/auth.py` / `me.py` / `admin.py` | 公开认证面 / 个人额度 / 管理后台（一个文件一个路由器） |
| `app/services/` | `user_service`（行级机制）· `admin_user_service`（矩阵 + 审计编排）· `session_service` · `auth_service` · `invite_service` · `quota_service` · `audit_service` · `settings_service` · `bootstrap` |
| `app/cli.py` | 离线恢复，见下 |

**忘记 root 密码 / 离线恢复**（授权模型：能访问数据库文件的机器）：

```text
python -m app.cli reset-root-password [--password …]   # 缺省交互输入；吊销 root 全部会话 + 强制下次改密 + 审计
python -m app.cli create-admin <username> [--password …]
python -m app.cli unlock <username>
python -m app.cli clear-recovery <username>            # 吊销某账号的恢复码（审计 actor=system）
```

退出码 `0` 成功 / `1` 被拒（策略、用户不存在）/ `2` 环境没就绪（库打不开、未迁移、尚无 root）。库路径取自 `DATABASE_URL`（与服务同一份配置）。`ROOT_ADMIN_PASSWORD` 只在**首次引导**时读取，改它不会重置 root；详见仓库根 `README.md`「用户与权限」。

**迭代 2（v16）**：`security/recovery.py`（恢复码：生成 / 归一化 / `sha256`，纯函数）· `services/account_lifecycle.py`（管理员注销与自助注销**副作用的唯一实现**，授权由调用方先做）· `services/export_service.py`（CSV：BOM、`csv_safe` 公式转义、500 行一块、自开短会话）· `session_service.purge_dead`（死会话清扫，`asyncio.to_thread`，不在事件循环线程上）。`recover` 的固定顺序（限速 → 验证码 → 新密码策略 → 码校验 → CAS 轮换）本身就是安全设计，见 spec §25.3.3。迁移 `0008_account_recovery` 只加 2 列 + 2 个索引。

## 研究流程图（report-workflow-graph，M59–M65）

管理员可在后台把"生成一份研报的过程"配置成**图工作流**；之后每个用户的每次生成都按默认模板执行。

- **软约束，不是状态机。** 图被 `workflow_compiler` 编译成 `workflow.md`（写给 Agent 的计划）+ ≤2 KB 的内联索引（进 `prompt.md`）；
  Python 侧**不**判断"这一步做完没有"。**过程**归流程图（读什么、几步、完成标准），**契约**归代码与系统提示词 v9
  （事实边界、来源标注、输出 Schema、接地校验、五个固定章节）——流程不能放宽后者，冲突时以系统提示词为准。
- **数据面**：`workflow_templates`（草稿 + `draft_revision`）→ `workflow_versions`（只追加、不可变）→ 默认指针（偏唯一索引 `ux_workflow_default` 保证全站至多一个）。
  `RunManager.start()` 把默认版本**冻结**进 `runs.workflow_version_id`；之后改默认/发布新版本都不影响在飞的 run。
- **并发**：路由是同步 `def`、在线程池里真并发，SQLite 忽略 `FOR UPDATE`——所以一切写入靠 `UPDATE … WHERE draft_revision=:rev` 的 CAS
  + `UNIQUE(template_id, version)` + 偏唯一索引；`IntegrityError` 映射为 409，绝不 500。
- **回退而不失败**：版本文档损坏 / 编译失败 / `plan_md` 超 48 KiB → 回退到**代码内置**标准流程（`quality.workflow.source="builtin-code"`，
  `run.log code=workflow_fallback`），用户的生成照常成功。"无默认行"同属回退，**不是**关闭；唯一走旧提示词的条件是 `workflow.enabled=0`。
- **观测而非强制**：Agent 输出 `[[wf:S2:done]]` 进度标记，`workflow_trace.TraceParser` 流式解析成 `run.workflow` SSE 事件并从展示文本与 `AgentResult.text`
  里剔除；漏标只会让 `quality.workflow.adherence` 变成 `partial/none`，**不会**使生成失败。
- **AI 助手**（`workflow_assist_service`）：一次 `aragon exec` 结构化提交一批 op，服务端分配 id、盖章 `expectRev`、整批原子应用到**草稿**；
  它没有发布/设默认的能力（C-6）。`AGENT_MODE=mock` 时由 `MockAssistRunner` 确定性出 op。
- **运维要点**：`PUT /admin/workflows/enabled` 立即生效（无需重启）；API 仍须**单 worker**（`AssistRegistry` 与 `RunManager._trackers` 都是进程内内存）；
  `usage` 接口可看每版本的遵循度分布与回退次数。AC-W37：上线前须在**真实引擎**下做 `workflow.enabled=0/1` 的 A/B 对照，未达标则出厂默认值保持 `"0"`。

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
