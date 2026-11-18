# 23. Iteration 7 增量设计：让「生成一次」真的能被反复查看与复用（U1–U7）

> **本文档在全项目 spec 中的位置**：这是 `docs/plans/investment-research-report/spec.md`
> （下称 *主 spec*，§1–§22，8866 行）的 **§23 增量**。主 spec 是本项目的唯一真源，
> 本文档不改它一行（本轮设计节点的硬约束），只做**追加**：新的需求定义、新的证据、
> 新的接口、新的数据模型、新的验收编号（AC-137 起）与新的里程碑（M38 起）。
> 下游实现节点请把本文档当作施工图，把主 spec 当作字典（既有裁决、既有错误码、既有 AC 在那边）。

| 项 | 值 |
| --- | --- |
| 需求原文 | 对用户已经生成过的研报，要能够正确保存下来，后续可以反复查看；**首页要显示最近生成的研报**；**点进某一个进去以后，要能显示之前生成好的，并且可以更新**；其余由设计者补齐。总之：**让生成的结果能够复用** |
| 已有能力（不可回退） | 报告是 `aragon.research.report/v1` 结构化文档、落库 `reports.doc_json`、版本不可变、`?report=` 深链、版本比较、追问、全文检索、导出、定时刷新、离线副本 |
| 本轮要做 | 报告库（首页「最近生成的研报」+ `/reports` 全文库）、`?report=` 深链的**唯一写者**、历史版本横幅与「基于此版本更新」、内容去重（复用已有版本）、回收站与软删式裁剪、内容完整性读路径、报告与 run 工作区的解耦不变量 |
| 本轮明确不做 | 多用户/权限、向量语义检索、PDF 导出、跨库迁移、报告人工编辑（见 §23.2 V 层） |
| 交付物 | `api/` + `web/` 代码，`0006` 迁移，`AC-137`~`AC-152`，`M38`~`M44` |
| **文档版本** | **v2**（2026-11-18 评审后修订）。v1 = 设计节点原始稿；本轮评审把 **3 条 P0 + 16 条 P1 全部在正文改掉**（清单见下），P2 12 条能改的已改、其余记录在案。文末 §23.11 是评审结论：**有条件通过**。 |

---

## 评审记录（Iteration 7 设计评审 · v1 → v2）

**评审方法与边界**：逐节对照四个维度（可行性 / 完整性 / 一致性 / 规模合理性），并对 v1 里**每一条 `file:line` 与每一个数字**在本机代码上重核了一遍（v1 自称 O-31 = 15/15；本轮复核结果是 13/15 准确 + 2 条行号偏移 + 1 条计数错误，见 P2-1~P2-4）。只有"能在代码里指出来"的才写进下面的表；拿不出来的不写。本轮**只改 `spec.md`**，不碰源码、不 commit。

### A. P0（3 条，全部已在正文修正）

| 编号 | 维度 | 结论与证据（本机核查） | 修在哪 |
| --- | --- | --- | --- |
| **P0-1** | 健壮性 / 可行性 | v1 的"最小可渲染壳"**自身过不了 `ReportDoc`**：`summary` 有 `min_length=10`（`api/app/schemas/report.py:242`），v1 给的是 `summary: "…"`（1 个字符）；`asOf` 有 `min_length=10`（`:239`），v1 说"沿用列值"。而 `ReportDetail.doc` 的声明类型就是 `ReportDoc`（`:411-413`）⇒ 壳会在**序列化**时抛 `ResponseValidationError`，只是把 500 从 `json.loads` 挪到 pydantic，洞没堵。更要紧的是：损坏横幅写着"请先导出/截图"，而导出（`routers/reports.py:164`）、区间反查（`:223`）、版本比较（`:280-281`）三条路**仍是裸 `report.doc`** ⇒ 用户照着指引点"导出"拿到的是 500。 | §23.3.6 重写：逐字段合规的壳 + 5 条读路径统一到 `load_doc_safe`；AC-147 加"壳能过 `ReportDoc`、导出返回 200" |
| **P0-2** | 一致性（自相矛盾） | `scope=latest` 的谓词带 `archived = 0`，而 `latest_of()` 只筛 `deleted_at IS NULL`（`api/app/services/report_service.py:264-293`）。`set_flags(archived=True)` 会**把 `is_latest` 清掉**（`:330-335`）⇒ "归档过最新版"的标的：CTE 退到"次新且未归档"，`latest_of()` 退到"最新但已归档"，两个不同 id。AC-139 要求"逐条等于 `latest_of()`"，而同一份夹具为了验 `scope=archived` 必须归档一份（通常正是最新那份）⇒ **这条 AC 必然红**，且实现者最可能的"修法"是反过来去改 `latest_of()`（回退既有端点语义）。 | §23.3.2 改谓词 + 写明"归档最新版后首页仍显示它"是既有行为、本轮不动；AC-139 加夹具说明 |
| **P0-3** | 可行性（指错了文件） | `quality.prompt` 由 `prompt_quality(system_path)` 产出（`api/app/services/run_manager.py:551`），`system_path` 是 **`research_system.md`** 的工作区副本（`api/app/services/snapshot.py:443-449`），版本标记 `<!-- version: 7 -->` **只在 `research_system.md:1`**（`ask_system.md:1` 同）——`research_task.md.j2` **没有任何标记**（`grep "version:" api/app/agent/prompts` 只命中两个 system 文件）。因此 v1 的三处推断全部落空：①`research_task.md.j2` 的 `PROMPT_VERSION` 7→8 —— 这个常量与这个标记都不存在（真正的解析器是 `report_validator.PROMPT_VERSION_RE`，`:123-143`）；②`quality.prompt.bytes` 现在是 `research_system.md` 的 **3822 B**，不是 2163，`2163+2048=4211` 这一整段算术对着错误的文件；③`base_digest` 落在 task 模板里 ⇒ 它**根本不改变** `quality.prompt.bytes`，用后者去证明"预算仍然够"等于什么都没证明。 | §23.3.4(b) 重写（宿主、标记、预算三件事各归其位）；§23.6.1 拆成两行 |

### B. P1（16 条，全部已在正文修正）

| 编号 | 维度 | 结论与证据 | 修在哪 |
| --- | --- | --- | --- |
| **P1-1** | 完整性（会红门禁） | 三个**既有**用例会红，但 v1 的变更表一个都没列：`api/tests/test_capabilities_api.py:154` 是 **`features` 精确相等**的 17 键字典 ⇒ 加 `reportLibrary` 必红；`api/tests/test_report_service.py:181-200` 断言 `session.get(Report, first.id) is None`（注释原话 "the old version should have been trimmed"）⇒ 裁剪改软删必红；`api/tests/test_prompt_discipline.py:100/:110` 断言两个 system 提示词 `version == 7` **且** `bytes == len(research_system.md)` ⇒ 同 P0-3。 | §23.6.4 增三行；新增 §23.7.4「既有用例的语义变更清单」 |
| **P1-2** | 一致性 / 性能 | `ReportLibraryItem.integrity` 逐行求值，但 §23.3.6 说列表"不解析 doc 深层结构"、§23.5.1 要求 p95 < 30 ms。20 行 × 16 KB 的 `json.loads` 同时违反这两条。 | §23.4.3 / §23.3.6：列表用 SQL 算 `case when json_valid(doc_json) then 'ok' else 'corrupt' end`（本机 SQLite **3.41.2** 实测 `json_valid` 可用，返回 `(0, 1)`）；Python 只在详情路径解析 |
| **P1-3** | 可行性（会静默丢行） | 游标 `"{generated_at}\|{id}"` 没规定"原样透传"。`generated_at` 是 **Text** 列、由 `_iso()` 生成（秒级、`Z` 结尾，`report_service.py:33-34`、`:26`）。任何 `datetime.fromisoformat(...).isoformat()` 往返都会把 `Z` 变成 `+00:00`，字符串比较 `generated_at < ?` 于是**整段漏行**；秒级精度还意味着"同一秒的两份报告"必然并列，`id` 的 tie-break 不是可选项而是正确性的一部分。 | §23.3.2 写死游标契约：三段式正则校验、**不做时间解析/归一化**、客户端 `encodeURIComponent` |
| **P1-4** | 完整性（自相矛盾） | `counts[scope] == total` 与 `q` / `instrumentId` 过滤不能同时成立：过滤后 `total` 应该变小，而 `counts` 若跟着变小就不再是"全库计数"，tab 徽章会随搜索跳动。 | §23.3.2 定义 `counts` = 不含 q/instrumentId 的全库计数、`total` = 当前过滤命中数；AC-140 只在无过滤时断言三者相等 |
| **P1-5** | 一致性（自相矛盾） | 首页要 `counts.all` 渲染「查看全部 N 份报告 →」（§23.3.7 第 3 条），R-66 却把 `withCounts=0` 当首页逃生阀——同一份响应既要又要。 | §23.3.7 / R-66：首页固定 `withCounts=1`（4 个 COUNT 仍远小于 14 次 fetch）；逃生阀改成 `staleTime` + 骨架 + `limit` |
| **P1-6** | 完整性（默认视图未定） | 5 个 scope，但 §23.3.8 的草图只有 4 个 tab；"库的默认视图"两处说法不同（§23.3.2 表说 `latest` 用于"首页/库默认"，§23.2.1 与草图都是"全部"）。 | §23.3.2 / §23.3.8：库页默认 `all`、4 个 tab；`latest` 只服务首页 / 命令面板 / 工作台默认打开 |
| **P1-7** | 完整性（前端无权威来源） | `HistoricalBanner` 判定"这是不是历史版本"需要**最新版 id**，但深链时 `initialDetail` 已被 `?report=` 那份**替换**（`web/app/[symbol]/page.tsx:151-154`），`initialReports` 又默认 `includeArchived=false`（`report_service.list_versions:303-305`）⇒ 前端只能猜；猜错就把最新版渲染成历史版（反之亦然）。 | §23.3.3 写死 `latestReportId` 取值顺序（`initialReports` 里 `isLatest` → 否则 version 最大且非 provisional → 否则 version 最大，与 `latest_of()` 同序） |
| **P1-8** | 完整性（数据会被连带删除） | `purge_trash` 的副作用被低估：`report_asks.report_id` 是 **`ondelete="CASCADE"`**（`api/app/models/ask.py:30-32`）⇒ 永久清理会**连该版本的全部追问历史一起删**；`cost_ledger.report_id` 是**无外键**的 `String(36)`（`api/app/models/cost.py:36`）⇒ 硬删后账本里留下指向不存在报告的孤儿引用（账本不能改，只能接受并写清）。 | §23.3.5 补完整副作用清单 + 计数日志；§23.3.8 回收站文案必须写明"含追问记录" |
| **P1-9** | 一致性（第二真源） | `relativeTime` **已有唯一实现**：`web/lib/format.ts:80-93`（刚刚 / N 分钟前 / N 小时前 / **N 天前（<30 天）** / 日期），被 `web/components/report/ReportPanel.tsx:404` 与 `web/components/report/VersionSwitcher.tsx:174` 使用。v1 要在新文件 `web/lib/library.ts` 里再写一份，且档位不同（新文 `<7d` vs 既有 `<30d`）⇒ 同一份报告在首页与工作台显示不同的话。这直接违反本文自己的 O-39。 | §23.6.3：`library.ts` 只加 `groupByDay / libraryHref / cursor / scopeLabels`，`relativeTime` 一律从 `format.ts` 引 |
| **P1-10** | 稳健性（靠运气） | 去重基线写成 `latest_of()`，而它在"标的只有临时版"时返回 **provisional** 行（`report_service.py:285-293`）。内容相同几乎不可能，但"命中后把 `runs.report_id` 指向一份临时版"是**碰运气**而不是设计。 | §23.3.4(a)：基线定义为"最新**非临时**、未删除版本"；provisional 照写 `doc_sha1`（列一致性）但永不参与比较 |
| **P1-11** | 完整性（空字段） | `PromptQuality` 加两个字段，但**生产者没人写**：`prompt_quality()` 只产出 `{version, sha1, bytes}`（`api/app/services/report_validator.py:127-143`），`run_manager.py:551` 直接把它塞进 `validate_doc(prompt=...)`。只加响应字段 = 又一次"声明了但没有生产者"（`test_every_quality_key_has_a_response_field` 盯的是反方向，救不了这一侧）。 | §23.3.4(b)：写明生产者改法（`prompt_quality(path, *, base=None)` 或 run_manager 侧合并），并加"四个键都在响应模型上**且都有生产者**"的用例 |
| **P1-12** | 可验证性（AC 写不成） | AC-146 要求 base_digest"不含任何数值型指标（用正则断言无 `\d` 组）"，但摘要**必须**含 `asOf`（例 `as-of: 2026-11-17`）与版本号 ⇒ 按字面写这条 AC 永远红。 | AC-146 收窄为"`keyMetrics` **标签段**内不得出现数字"，asOf / 版本行显式豁免 |
| **P1-13** | 完整性（硬约束打架） | 回收站 tab 要显示"删除于 X / 还剩 N 天"，但 `ReportSummary` 没有 `deletedAt`，而硬约束 2 规定 `ReportSummary` 不追加键。 | §23.4.3：`deletedAt: str \| None` 加到**新模型** `ReportLibraryItem`（新增模型可以追加），`ReportSummary` 一个字不动 |
| **P1-14** | 可行性（永不执行） | `purge_trash` 的第二个调用点写成 "scheduler tick"，但 ticker 只在 `SCHEDULER_ENABLED=1` 时启动（`api/app/main.py:186-189`；测试套件就是 0）⇒ 关掉定时刷新的部署**永远不会清理回收站**，`.db` 只增不减。 | §23.3.5：挂到常驻 `_sweep_loop`（`main.py:104-121`，每 `STALE_SWEEP_SECONDS` 秒）+ 启动一次；理由与 V11-P1-4 同构 |
| **P1-15** | 完整性（未定义路径） | `baseReportId` 只校验"属于该标的 / 未被硬删"。指向 **provisional**（失败说明，没有 `sections`）或**损坏 doc** 时，摘要生成要么崩、要么产出一份"章节为空"的假结构，而 `quality.prompt.baseReportId` 已经落库，事后看起来像"用户选的"。 | §23.3.4(b)：定义"取不到结构 ⇒ 按无 base 处理"（不 422、不崩），`run.log` 记 info，`quality.prompt.baseReportId` 仍记原值 |
| **P1-16** | 一致性（自相矛盾） | 硬约束 5 写"老数据不回填，`doc_sha1` 为 NULL 保持 NULL"（延续 V11-P0-1），而 §23.6.1 与 M38 又要求新增并交付 `scripts/backfill-report-sha1.py` 去回填。两者只能留一个。 | 硬约束 5 + §23.6.1 + M38：脚本降级为**运维可选工具**，默认不进任何门禁、不在启动时执行，脚本头部必须写明"跑过它就等于推翻硬约束 5，需用户显式同意" |

### C. P2（12 条：能改的已改，其余记录在案）

| 编号 | 结论 | 处理 |
| --- | --- | --- |
| **P2-1** | `ReportSummary` 是 **23** 个键，不是 24（`api/app/schemas/report.py:383-408` 逐个点：id / instrumentId / version / status / title / headline / stance / asOf / generatedAt / isLatest / pinned / archived / provisional / groundedRatio / quality / provider / model / costUsd / costKnown / tokensIn / tokensOut / durationMs / docBytes） | 已改正文 |
| **P2-2** | `FeaturesOut` 是 **17** 个布尔，不是 15（`api/app/schemas/capabilities.py:73-94`，两处引用都已改） | 已改正文 |
| **P2-3** | `report_service.py:312` 实为 `:311`（`:312` 是 `.limit(...)`）——`order_by(pinned.desc(), version.desc())` 在 `:311` | 已改正文 |
| **P2-4** | "`grep -rn restore api/app` 零命中"不准确：有 2 处，都是 `config._restore_empty_placeholders`（`api/app/config.py:286、:307`），与报告恢复无关 | 已改为"没有任何报告恢复端点" |
| **P2-5** | 键盘纪律的函数名是 `isEditableTarget`（`web/lib/keyboard.ts`），不是 `isTypingTarget`；`P / E / Delete / /` 与既有那批键位（`j` `k` `g` `r` `t` `s` `\` `?` `Escape` `⌘K`）**不冲突**（逐个核对过 `matchHotkey`），但库页 handler 必须注册在页面级并在输入框聚焦时全部让路 | 已改正文 |
| **P2-6** | 门禁里的绝对数（972 / 260 / ≥28 / ≥18）会随实现漂移 | 已改为"以最后一次绿的门禁输出为基线 + 新增下限" |
| **P2-7** | 裁剪改软删后，回收站会混入"系统自动清理"的行；本轮不新增列，只能在文案与日志层区分 | 已改 §23.3.8（文案）+ §23.3.5（`logger.info` 计数） |
| **P2-8** | `includeActive` 的 `stage / pct / startedAt` 真实来源是 `runs.stage` / `runs.pct` / `runs.started_at`（`api/app/models/run.py:29-30、:42`），活跃状态常量是 `ACTIVE_STATUSES`（`:13`） | 已改正文（写明来源，别让实现者猜） |
| **P2-9** | `downgrade` 用 `op.drop_column` ⇒ 要求 SQLite ≥ 3.35（本机 **3.41.2** 实测：窗口函数与 `json_valid` 都可用） | 已改正文 |
| **P2-10** | `useInfiniteQuery` + `initialData` 必须带 `initialDataUpdatedAt`，否则首屏立刻重复请求一次，与 AC-137"只发一次 `/reports`"冲突 | 已改正文 |
| **P2-11** | AC-150 的"唯一存活版本"要补"**且未归档**"前提，否则与 §23.3.5 的抢指针规则自相矛盾 | 已改正文 |
| **P2-12** | AC-137 的"id 集合相等"只在 `N ≤ 6` 时成立（首页 `limit=6`），N > 6 时必须改成"是库内集合的子集且按时间序取前 6" | 已改正文 |

### D. 复核后**成立**的关键判断（本轮不动）

这些不是客套——是逐条在代码里指到了才写进来的，它们决定了下文的修法**不需要**动结构：

- `ux_reports_latest` 部分唯一索引真实存在（`api/app/models/report.py:48-54`）⇒ R-70 的缓解手段是真的，"抢指针前先查有没有"这条规则有数据库背书。
- FTS 成对与 savepoint 的形状已是既有事实（`report_service.py:169-173`；`search_index.reindex_report_payload` / `delete_report_index` 都在），restore 照抄即可，不需要新机制。
- 活跃 run 只用 `('queued','running','cancelling')` 三态（`api/app/models/run.py:13`），`count_active_runs()` 已存在（`report_service.py:392-397`）⇒ `active` 条不需要新的计数口径。
- `cost_ledger.report_id` **无外键**（`models/cost.py:36`）⇒ 去重与清理都不可能撞账本（但见 P1-8 的孤儿引用）。
- 迁移只 `add_column` + `create_index`，与 `0005_engine_probes.py` 头注释第 1、3 条（不 batch、不重建表）一致。
- `report_doc.schema.json` 顶层确为 `additionalProperties: false`（18392 B，`api/app/agent/schemas/`）⇒ "绝不往模型返回的 doc 里加字段"是硬要求，本文遵守。
- `DEFAULT_MAX_PROMPT_BYTES = 32_768`（`api/app/agent/schema_budget.py:56`）⇒ 32 KB 预算的说法成立。
- `/reports` 与 `/reports/{report_id}` 不会互相吞（段数不同，与注册顺序无关）；`/api/v1` 前缀由 `main.py:282-287` 统一加，路由文件里不写前缀。

---

## 23.0 本轮的定位、硬约束与 oracle 断言

### 23.0.1 「结果可复用」在 v13 的五条可测定义（U-0a ~ U-0e）

「能够正确保存下来，后续可以反复查看」是一句**无法反驳**的话，所以先把它翻译成五条能被反驳的判据。
每一条都在本机真实数据库上先测了一遍（§23.0.2），结论是：**保存这件事基本做到了，复用这件事基本没做**。

| 编号 | 一句话定义 | 可反驳的判据 | 本轮实测答案 |
| --- | --- | --- | --- |
| **U-0a 找得到** | 生成过的报告，用户**不靠记忆**也能再次找到 | 打开首页，不改任何地址，能否看到最近 3 份报告（任意标的） | ❌ **不成立**：首页列的是**自选标的**（`web/app/page.tsx:16-39`），不是报告；能看到的行数 = 自选 ∩ 有报告 = **3 行标的卡片**，其中只有 1 行是"最近"生成的，另外 2 行是 45 小时前的 v1（E-119/E-120） |
| **U-0b 打得开** | 从列表点进去，打开的必须**就是列表上那一份**，不是"最新那一份" | 首页每一行的 `href` 是否带 `?report=<id>` | ❌ **不成立**：`href={/${instrument.id}}`（`web/app/page.tsx:100`）⇒ 点 v12 的卡片，进去看到的是 v13 |
| **U-0c 留得住** | 刷新页面 / 切版本 / 分享链接之后，看到的还是同一份 | 在报告里切到 v12，按 F5 | ❌ **不成立**：`selectVersion()` 只写 zustand（`WorkspaceClient.tsx:301-307`），不写 URL ⇒ 刷新回到 latest；且 URL 是 `?report=` 的**唯一**入口（`app/[symbol]/page.tsx:146-154`），于是"当前读的是哪一版"有两个真源 |
| **U-0d 救得回** | 误删的版本能找回 | `DELETE /reports/{id}` 之后有没有一条回去的路 | ❌ **不成立**：软删（`report_service.soft_delete`）已经实现了，但 `api/app` 里**没有任何报告恢复端点**（`grep -rn restore api/app` 只命中 `config._restore_empty_placeholders` 两处，与此无关），本机 5 行 `deleted_at` 非空（E-119）是**永久不可见**的 |
| **U-0e 不重复** | 同样的内容不该变成 v14、v15、v16 | 同一份 doc 连续生成两次，`reports` 行数是否 +2 | ❌ **不成立**：`create_version()` 无条件 `max(version)+1`（`report_service.py:121-124`）；本机茅台已经有 **13** 个版本、其中 8 行存活（E-119），而 13 个版本里有多少是同内容的重复生成，库里没有任何字段能回答 |

三条附带结论（本轮一并处理）：

- **U-0a 的根因不是"首页难看"，是"可发现性挂在错的表上"**：`loadRail()` 的数据源是 `watchlist`。
  把茅台从自选里移除，它的 8 份存活版本**立刻从首页彻底消失**，而 `reports` 一行没少（E-120）。
  「我的报告在哪」不应该由「我的自选股」回答。
- **裁剪已经在倒计时了**：`MAX_VERSIONS_PER_INSTRUMENT = 20`（`report_service.py:30`），
  而茅台已经用掉 **13/20**（E-121）。再过 7 次生成，最老的未置顶版本会被 `session.delete`
  **硬删除**（`report_service.py:247`）——连回收站都进不去。这正是"必须正确保存下来"的对立面。
- **保存链路上有一个 500 洞**：`Report.doc` 直接 `json.loads`（`models/report.py:60`），
  `GET /reports/{id}` 直接用它（`routers/reports.py:62`、`:72`）。库里的 JSON 一旦损坏
  （磁盘位翻转、外键孤儿、手工 SQL、半写事务），读这份报告就是 500，而**列表页仍然列着它**。
  `summarize()` 防了这一手（`report_service.py:41-46` 的 try/except），详情路径没防——两条路
  对同一份数据的"可读性"判断不一致，这是本轮最容易踩的坑之一（E-122）。

### 23.0.2 基线实测（只读，可复现）

探针脚本：`docs/plans/report-library/probe_db.py`（**只读**打开 `api/data/app.db`，
`uri=True&mode=ro`，不写任何东西）。命令：

```text
python docs/plans/report-library/probe_db.py
```

| 证据 | 内容 | 摘录 |
| --- | --- | --- |
| **E-119** | 每一行报告（15 行，按 `generated_at` 倒序） | 600519.SH 有 v1–v13；**存活 8 行**、软删 5 行（全部 `deleted_at=2026-11-16T07:39:12Z`，一次批量删）；`pinned=0` **全部未置顶**、`archived=0` 全部未归档；`json_valid(doc_json)=1` 15/15 |
| **E-120** | 首页口径 vs 库里有什么 | 首页最近标的分块可见 **3** 份（= 自选 ∩ `is_latest`）；`instruments` 4 个（`0700.HK` 有 0 份报告）、`watchlist` 3 个；**存活未删除版本 10 份，其中 7 份首页看不见** |
| **E-121** | 版本代数与裁剪上限 | `600519.SH`: total=13 live=8 v1..v13（**13/20**）；`AAPL`: 1/1；`300750.SZ`: 1/1；`0700.HK`: 0 |
| **E-122** | 可读性抽查 | 10 份存活报告的关键字段（`schema/instrument/asOf/headline/rating/summary/sections`）齐全 10/10；但"损坏时会怎样"两条读路径不一致（代码层证据，见 §23.3.6） |
| **E-123** | `quality_json` 覆盖 | 存活 10 份**全部**带 `quality`（首页若要显示可信度/降级标记，数据是有的，缺的是出口） |
| **E-124** | `runs` ↔ `reports` 的对账 | `succeeded` 12 / `failed` 7（19 次真实生成）；12 次成功里 `report_id IS NULL` **0** 次（指针没丢，好），但有 **2** 次成功生成的报告现在 `deleted_at` 非空 ⇒ **那次成功的产物永久不可见**（没有 restore 通道）；`reports` 存活 10 行、`avg(doc_bytes)=16074` |
| **E-125** | 报告年龄量级 | v13 = 4.0 小时前、v12 = 22.3 小时前、v11 = 37.3 小时前、v10 = 39.5 小时前、AAPL v1 = 45.2 小时前（首页相对时间必须有「小时」这一档，只写日期会全糊成同一天） |
| **E-126** | 首页请求数（代码层） | `1 × /watchlist + 1 × /instruments + 最多 12 × /instruments/{id}/reports/latest` = **最坏 14 次串行 RSC fetch**（`web/app/page.tsx:25-34`）；一次 `/reports` 就能替代全部 |
| **E-127** | 成本只在一个地方落账 | `cost_service.record(kind="run")` 在 `api/app` 里**只有一处**：`report_service.py:190`（在 `create_version()` 内）。⇒ 任何"跳过 create_version"的去重实现都会**静默吞掉这次生成的花费** |
| **E-128** | 裁剪调用点在 commit 之后且无保护 | `report_service.py:205` `session.commit()` → `:219` `_trim_versions(...)`，没有 try/except ⇒ 裁剪抛异常时报告**已经落库**，但 API 以 500 结束（用户以为失败、其实成功） |
| **E-129** | 没有分页 | `grep -rn "cursor" api/app` 只命中 sqlite/session 与 ask 事件重放；`/instruments/{id}/reports` 是 `limit` + `total` OFFSET-free 全量取（`report_service.py:306-317`）。库变大后首页/库页需要一个**游标**而不是 `limit=100` 的天花板 |
| **E-130** | 契约与开关 | `contracts/run_error_codes.json` version=2、21 条 run 码；`FeaturesOut`（`schemas/capabilities.py:57-94`）已有 15 个实测布尔，**没有**报告库开关；alembic head = `0005_engine_probes` |

> 探针的**局限**（必须写出来，否则 E-119 会被读成"全项目的现状"）：
> 本机 `app.db` 里 `pinned=0`、`archived=0`，所以「置顶/归档」在真实数据上**没有被验证过**；
> 本轮 AC 里凡涉及置顶/归档的，都只能用夹具证明（§23.7.1），不能声称"实测通过"。

### 23.0.3 硬约束（写代码之前先写死的七条）

1. **只加一次迁移**：`0006_report_library`，新增 `reports.doc_sha1` + 3 个部分索引。
   不新建表（`reports` 已经装得下）、不改既有列类型、不重建表（SQLite 重建表会锁库）。
2. **不改既有响应键的含义**，只允许**追加**：`ReportSummary` 一个字段都不改名；
   列表用**新模型** `ReportLibraryItem`；`ReportDetail` 只追加 `integrity`；
   SSE `run.done` 只追加 `deduped`；`/capabilities` 只追加 `features.reportLibrary`。
3. **不新增 run 级错误码**。本轮 3 条新码全是 HTTP 码（§23.5.5），
   **不进** `RUN_ERROR_CODES`，但必须同时改 `contracts/run_error_codes.json` 的 `httpCodes`
   （`test_http_codes_never_leak_into_the_run_code_set` 会立刻红，这正是我们要的）。
4. **成本账本不许丢行**：任何去重/复用路径都必须**照记**这一次 run 的花费（E-127）。
   「省了一次版本」不等于「省了一次调用」。
5. **老数据不回填**（§19 裁决的延续）：`doc_sha1` 为 NULL 的历史行**保持 NULL**；
   `NULL` 的语义是"不知道"，**不是**"不同"——去重只在两侧都非 NULL 时生效。
   ★评审 P1-16：这条与"交付一个回填脚本"**互斥**，所以 `scripts/backfill-report-sha1.py`
   在本轮降级为**运维可选工具**——不进门禁、不进里程碑判据、**不在启动时执行**，脚本头部
   必须写明"跑过它就等于推翻本硬约束与 V11-P0-1 的'不回填'，需要用户显式同意"。
   默认行为是 `--dry-run`（只打印会有多少行被写入），要真写必须显式加 `--apply`。
6. **硬删除只有一个入口**：`purge_trash()`，且必须过 `TRASH_RETENTION_DAYS`。
   `_trim_versions()` 从硬删改为**软删**（这才是"能救回来"的前提）。
7. **每一条修复配一条先红后绿的用例**：写代码之前先写用例、先看它红，把红输出贴进实现节点 findings。

### 23.0.4 oracle 断言（写完自检，见 §23.10）

| 编号 | 断言 | 结果 |
| --- | --- | --- |
| O-31 | 本文引用的每个 `file:line` 真实存在且语义相符 | ⚠️ v1 自称 15/15；**v2 复核实为 13 准确 + 2 条行号偏移 + 1 条计数错误**，已改正并补 3 行（§23.10.1） |
| O-32 | 每条 U-0a~U-0e 的否定结论都有一条可重跑的证据（脚本 + 命令 + 输出摘录） | ✅ E-119~E-130 |
| O-33 | 新增接口的每个字段都能说出"谁读它、读不到会怎样" | ✅ §23.5 每张表都有「消费者」列 |
| O-34 | 不违反主 spec 的任何既有裁决（K-1~K-16、V10-P0-1/2、V11-P0-1、V12 六条） | ✅ §23.8.2 逐条对照 |
| O-35 | 「不新增 run 码」在写完后再核一遍 | ✅ 3 条全是 HTTP 码，`RUN_ERROR_CODES` 仍 21 条 |
| O-36 | 「只加一次迁移」在写完后再核一遍 | ✅ 只有 `0006`，且只 `add_column` + `create_index` |
| O-37 | 每条 AC 都能机器断言（不是"会更好"） | ✅ AC-137~AC-152 全部给出断言方式 |
| O-38 | 设计里没有"以后再补"的字段（要么本轮给出口，要么明确不做） | ✅ §23.2 的 V 层逐条写了"不做" |
| O-39 | 每个新文件都能说出它替代/拆分了谁，不制造第二真源 | ✅ §23.6 的「意图」列 |
| O-40 | 本文档的每个数字都能用一条命令重算，且探针只读 | ✅ `probe_db.py`（`mode=ro`） |

---

## 23.1 概述：为什么"已经保存下来了"仍然不算"能反复查看"

**第一段：保存这件事，v13 已经做到了技术上的那一半。**
报告从来不是一段 HTML 字符串，而是一份 `aragon.research.report/v1` 结构化文档，落在
`reports.doc_json`（本机 15 行、存活 10 行、`json_valid` 15/15，E-119），带 `version` 不可变、
`is_latest` 由数据库部分唯一索引守着的版本链，还有 `quality` 信封（10/10，E-123）、
成本三态、引擎指纹、来源编号。它不依赖 `workspace/runs/<runId>/` 存活，也不依赖模型在线。
**所以"要能正确保存下来"的答案不是"再加一个保存功能"**——那会变成第二个真源。

**第二段：复用这件事，缺的是"出口"和"回程"。**
今天用户能看到的唯一入口是**自选标的**：首页列出你关注的 3 个标的，每个标的给你**最新一版**。
于是 `600519.SH` 的 13 个版本里，8 份存活版本只有 1 份在首页，另外 7 份必须"记得住标的名 →
进工作台 → 打开版本切换器 → 认出 v11 是你要的那一版"。而"点进某一个进去以后要能显示之前生成好的"
这一句，在代码里被两处小缝隙同时破坏：列表链接不带 `?report=`（打开的是最新版），
切版本不写 URL（刷新就回最新版）。再加上误删无路可回（无 restore）、裁剪到 20 个就**硬删**
（茅台 13/20，E-121）——**这不是"缺功能"，这是"已存下来的东西正在被一条看不见的规则一点点弄丢"**。

**第三段：本轮的取舍。**
所以本轮把"复用"拆成七件事（U1–U7），全部围绕一条主线：**报告是可以被找到、被打开、
被更新、被找回、且不会被重复与裁剪意外弄丢的一等公民**。为了让这条主线可证伪，
本轮引入三个"不变量"而不是三个"功能"：①**同一份内容不会变成两个版本**（内容 sha1 去重，
成本照记）；②**任何一次裁剪都必须可逆**（硬删只走回收站，30 天窗口）；
③**报告的可读性不依赖任何外部目录**（`workspace/` 清空后 doc 字节完全相等）。
UI 侧同时补齐"顶级人机交互"的那一半：一次查询的首屏、按时间分组的列表、
"你正在看历史版本"的横幅与两个明确动作（回到最新 / 基于此版本更新）、键盘与无障碍、
以及生成过程中"这一份正在生成"的那一行。

---

## 23.2 范围分层：U 层（本轮必做）/ V 层（明确不做）

### 23.2.1 U 层

| 编号 | 内容 | 为什么现在做 | 里程碑 |
| --- | --- | --- | --- |
| **U1** | **报告库读路径**：`GET /api/v1/reports`（5 个 scope + `q` + `instrumentId` + keyset 游标 + 同源 counts）+ `/reports` 页面 + 首页「最近生成的研报」区块 | U-0a：可发现性挂在 `watchlist` 上（E-120）；首页 14 次 fetch（E-126） | M39 / M42 |
| **U2** | **深链与"我在看哪一版"只有一个真源**：列表链接带 `?report=`；`selectVersion` 写 URL；历史版本横幅（回到最新 / 基于此版本更新）；保留 `section/block/view/range/tf/win/adj` | U-0b/U-0c，且现在 URL 与 zustand 两个真源 | M41 / M43 |
| **U3** | **更新即复用**：`dedupe`（内容 sha1 相同不新增版本）、`baseReportId`（把上一版的结构摘要注入提示词，≤2048 B）、SSE `run.done.deduped`、成本照记 | U-0e + E-127 | M41 |
| **U4** | **内容不变量**：`doc_sha1` 列（0006）、损坏 doc 的 200 读路径（`integrity`，**五条读路径统一**）、裁剪失败不影响响应。回填脚本**不是交付项**（运维可选，见硬约束 5 / P1-16） | U-0c 的读侧防线 + E-122/E-128 | M38 |
| **U5** | **回收站**：裁剪改软删、`POST /reports/{id}/restore`、`TRASH_RETENTION_DAYS=30` 的 `purge_trash()`、恢复与 FTS 成对 | U-0d + E-121 | M40 |
| **U6** | **首页与生成中和解**：`includeActive=1` 返回进行中的 run；`run.done` 后失效 `['reports','library']` 与 `['reports',instrumentId]`；返回首页时无需整页刷新 | 生成完"看不见刚生成的"是本轮需求的原话背景 | M42 |
| **U7** | **交互与无障碍打磨**：按时间分组（今天/昨天/本周/更早）、`↑↓/Enter/P/E/Delete` 键盘、骨架屏、空态与错误态、`aria-live`、减少动画偏好、焦点返回 | "美观、优雅、顶级设计、符合人机交互最佳实践" | M44 |

### 23.2.2 V 层（明确不做，理由写死）

| 编号 | 不做 | 理由 |
| --- | --- | --- |
| V1 | 报告人工编辑 / 批注 | 一份不可变文档是全部信任链的基础（§19 "只标不改"）。要加就加到 `report_notes` 表，本轮不做 |
| V2 | 语义/向量检索 | FTS5 已经一块一行（§21 A2），向量库引入第二个索引真源与第二份运维 |
| V3 | 标签体系 / 文件夹 | "一个报告只能在一个文件夹里"的争论成本远大于收益；用 `pinned` + `q` + 标的过滤覆盖 90% 的检索需求 |
| V4 | 跨库导出/导入（备份包） | 导出 HTML/Markdown 已有；备份是运维问题（`.db` 文件 + `sqlite3 .backup`），不做成产品功能 |
| V5 | PDF 导出 | 已有 501 + "请用浏览器打印" 的明确指引（`ExportUnsupported`），不重复造轮子 |
| V6 | 多用户与共享链接签名 | 当前部署是单用户/单进程（`assert_single_worker`），引入身份会牵动全部端点 |
| V7 | 报告 diff 的语义化（LLM 生成"变化摘要"） | 结构 diff 已经是服务端算好 `tone` 的（§18.5.4），再加一次模型调用等于给比较功能加成本与幻觉面 |

---

## 23.3 技术设计

### 23.3.1 架构总览（本轮改动落在哪一层）

```
浏览器
  ├─ /                     首页（RSC）：一次 GET /reports?scope=latest&limit=6&includeActive=1&withCounts=1
  │                          + 一次 GET /watchlist（次要区块）    ← U1/U6，替掉 14 次 fetch
  ├─ /reports              报告库（RSC 壳 + Client 列表）：useInfiniteQuery(GET /reports)
  │                          5 个 scope / **4 个 tab（默认 all）** · q · 分组 · 键盘 · 收/排/删/恢复/导出
  └─ /{symbol}?report=…    工作台（已有）：URL 是"当前哪一版"的唯一真源 ← U2
        │                     └─ 历史版本横幅（回到最新 / 基于此版本更新）
        └─ SSE run.done {reportId, deduped, …} → 失效 ['reports','library']

Next.js 15 (web/)
  lib/api.ts            + reports() / reportLibrary() / restoreReport()
  lib/library.ts        新：scope 常量、cursor 编解码、分组、libraryHref（纯函数，可单测）；`relativeTime` 仍从 `lib/format.ts` 引，不新写（P1-9）
  lib/types.ts          + ReportLibraryItem / LibraryResponse / ActiveRunItem

FastAPI (api/)
  routers/reports.py    + GET /reports（列表）  + POST /reports/{id}/restore
  services/report_library.py   新：list_page() / counts() / cursor codec / active_runs()
  services/report_service.py   + create_or_reuse_version() / restore() / load_doc_safe()
                               + _trim_versions → 软删；purge_trash()
  agent/prompts/research_task.md.j2  + {{ base_digest }} 段（≤2048 B；这个模板里没有版本标记，也不要加一个）
  agent/prompts/research_system.md   只改第 1 行 `<!-- version: 7 -->` → 8（标记的唯一宿主，P0-3）

SQLite（api/data/app.db）
  reports         + doc_sha1（可空）+ 3 个部分索引（feed / starred / trash）
  reports_trash   = reports 自己的 deleted_at 列，不建新表
```

三层边界一条都没动：**web 不含 LLM/数据源依赖**、**api 不含 UI 逻辑**、**aragon 子进程只做一次性生成**。
本轮新增的全部是"读路径 + 一条写路径的收敛"，没有新增第四个数据源。

### 23.3.2 读路径 A：报告库列表（`GET /api/v1/reports`）

**为什么不能复用 `/instruments/{id}/reports`**：它按 `(pinned desc, version desc)` 排序
（`report_service.py:311`——v1 写成 `:312`，`:312` 其实是 `.limit(...)`，已改正），是"一个标的的版本切换器"的排序;而报告库要的是**跨标的时间序**。
两者混用一个端点会得到一个"既能按版本排又能按时间排"的万能端点——即两个真源。

**排序的唯一规则**：`ORDER BY generated_at DESC, id DESC`。**不做** pinned-first：
库页的置顶用「星标」徽章与 `scope=starred` 表达，不能让一条置顶把 4 小时前的新报告压到屏幕外。
（工作台的版本切换器保持 `pinned desc, version desc` 不变——那是两个不同的阅读场景。）

**五个 scope 的语义（写死）**：

| scope | 谓词 | 用在哪 |
| --- | --- | --- |
| `latest`（默认） | 每个标的中"UI 默认会打开的那一版"——**谓词与 `report_service.latest_of()` 逐字同形**：`deleted_at IS NULL`，**不筛 `archived`**（理由见下一条） | 首页主区块、命令面板的"最近报告"、工作台首屏 |
| `all` | `deleted_at IS NULL AND archived = 0`（所有版本，含 superseded） | 库的「全部版本」 |
| `starred` | `deleted_at IS NULL AND pinned = 1` | 库的「星标」 |
| `archived` | `deleted_at IS NULL AND archived = 1` | 库的「归档」 |
| `trash` | `deleted_at IS NOT NULL` | 库的「回收站」 |

`latest` 必须与 `latest_of()` **语义相等**（AC-139 直接比对两个实现）。`latest_of()` 的语义是
"有 `is_latest` 就给 `is_latest`，没有就退回最新版本"（`report_service.py:264-293`：**临时版**
永远不会抢 `is_latest`，所以一个只有失败尝试的标的必须能在首页看到那张临时卡片）。
★评审 P0-2：所以窗口函数的 `where` **不能**带 `archived = 0`。`set_flags(archived=True)`
会把 `is_latest` 清掉（`report_service.py:330-335`），于是"归档过最新版"的标的两边会给出
**不同答案**：带 `archived = 0` 的 CTE 退到"次新且未归档"，而 `latest_of()` 退到"最新但已归档"。
「归档最新版之后首页还显示它」是**既有行为**（`/instruments/{id}/reports/latest` 一直如此），
本轮不改它；如果要改，必须同时改 `latest_of()` 与工作台默认打开逻辑，那是另一个决策（R-75）。
批量版用窗口函数一次算完，不写 N 个 related subquery：

```python
# services/report_library.py
LATEST_CTE = """
with ranked as (
  select r.*, row_number() over (
           partition by r.instrument_id
           order by r.is_latest desc, r.version desc) as rn
  from reports r
  where r.deleted_at is null            -- 不过滤 archived：与 latest_of() 同谓词（P0-2）
)
select * from ranked where rn = 1
"""
```

`scope=all/starred/archived/trash` 则是直接对 `reports` 加谓词——**不用**窗口函数（它们本来就要全部行）。
注意 `all` 与 `latest` 的口径**故意不同**：`all` = `deleted_at IS NULL AND archived = 0`（"全部未归档版本"，
库页的默认视图），`latest` 的 `archived` 不设限（见 P0-2）。两个 scope 服务于两个不同问题
（"这个标的默认打开哪一份" vs "我有哪些没归档的版本"），不要为了"看起来一致"把它们对齐。

**keyset 游标（不许 OFFSET）**：游标是 `"{generated_at}|{id}"`，比较写成可移植的两段式
（不依赖 SQLite 的 row value 比较，Postgres 上也一样）：

```python
if cursor is not None:
    stmt = stmt.where(
        or_(
            Report.generated_at < cursor.at,
            and_(Report.generated_at == cursor.at, Report.id < cursor.id),
        )
    )
stmt = stmt.order_by(Report.generated_at.desc(), Report.id.desc()).limit(limit + 1)
```

**多取一条**判断 `has_more`；`nextCursor` 只在 `has_more` 时非 `null`。

**游标契约（★评审 P1-3，三条都是正确性而不是风格）**：

1. **游标是"原样透传的列文本"，不是时间对象**。`generated_at` 是 `Text` 列、由
   `report_service._iso()` 写成 `2026-11-18T03:23:57Z` 这种**固定形态、秒级、`Z` 结尾**的字符串
   （`report_service.py:26、:33-34`）。服务端**只做正则校验、不做任何时间解析/归一化**：
   一次 `datetime.fromisoformat(x).isoformat()` 往返就会把 `Z` 变成 `+00:00`，于是
   `generated_at < '2026-11-16T10:10:22+00:00'` 在整个 `T`–`+` 之间比较出错误结果，
   **整段漏行**且不报错。允许的形态只有一个：
   `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\|[0-9a-f]{32}$`。
2. **秒级精度 ⇒ `id` 的 tie-break 是正确性的一部分**。同一秒里生成的两份报告
   `generated_at` 完全相同，没有第二段比较就会漏行或重复行——所以游标**必须**带 `id`，
   且比较**必须**是上面那段两段式（不做 row-value 比较，Postgres 上也一样）。
3. **`|` 必须被 URL 编码**：前端 `encodeURIComponent`（`web/lib/library.ts` 的
   `encodeCursor`），服务端按解码后的原值比较。
   非法游标（形态不符、`id` 长度 ≠ 32）→ **422 `invalid_cursor`**，`detail={before}`，
   **不是**静默忽略——静默忽略会让"翻页翻不动"变成一个没人能诊断的幽灵 bug。

**counts 与列表同源，但"同源"不等于"同一个数"**（★评审 P1-4）：`counts` 的 5 个数字用**同一个**
CTE/谓词构造器算出来，不允许列表用一套 where、计数用另一套（这是本项目历史上最贵的一类漂移）。
三个量的定义写死，别再混：

| 量 | 定义 | 谁读它 |
| --- | --- | --- |
| `counts[scope]` | **不含** `q` / `instrumentId` 的全库计数（5 个 scope 各一个） | 库页 4 个 tab 的徽章、首页「查看全部 N 份」取 `counts.all` |
| `total` | **当前过滤条件**（scope + q + instrumentId）命中的总行数 | "共 N 份"的文案、空态判断；**不参与分页** |
| `nextCursor` | 只在 `has_more` 时非 `null` | 翻页 |

理由：tab 徽章如果跟着搜索变，用户一输入就看见"全部 1 / 星标 0 / 回收站 0"，
那是把过滤器当成了数据变更。所以 `counts` 恒定、`total` 联动。
`counts[scope] == total` 只在**无过滤**时成立——AC-140 就只在无过滤时断言三者相等。

**`q` 的边界**：只匹配 `title` / `headline` / 标的 `symbol` / 标的 `name`，`LIKE` + 转义 `%`/`_`，
长度 ≤ 80（超长 422 `validation_error`）。**不复用 FTS**：`search_index` 是**块级**索引，
它的语义是"报告内容里哪一段提到了某个词"，与"标题里有没有这个词"是两件事；
混用会让 `/reports?q=` 返回一堆聚合出来的块 id，反而不能直接渲染成一行。

### 23.3.3 深链与"我在看哪一版"（U2）

**问题**：今天 `?report=` 只在两个地方被**读**（`app/[symbol]/page.tsx:146-154`、`SearchDialog` 的跳转），
在**零个地方**被**写**（`selectVersion` 只写 zustand）。于是"当前版本"有两个真源，
而 URL 是唯一能被刷新、被收藏、被分享的那一个。

**设计（四句话——第 4 句是本轮评审补的；缺了它，前三条在「历史版」上也会判错）**：

1. **URL 是唯一真源**。`selectVersion(reportId)` 改成：`setActiveReport(reportId)` **并**
   `syncUrl({ report: reportId })`（复用 `WorkspaceClient` 已有的 `syncUrl`，`router.replace`，
   不污染后退栈）。
2. **`syncUrl` 只改自己那一个参数**——这是它现有的纪律（`WorkspaceClient.tsx:240-267`），
   本轮只增加 `report` 这一个键：`params.set('report', id)`；当 `id === latestReportId`（下一条的规则）时**删除**该参数
   （URL 只记录"与默认不同"的部分，与 `tf/win/adj` 的处理完全一致）。
3. **深链优先于 store**：RSC 已经把 `?report=` 那一份取回来当 `initialDetail`（`app/[symbol]/page.tsx:151-154`），
   客户端不需要再多一次往返；`selectedReportId` 的优先级保持既有顺序
   （`activeReportId ?? initialReportId ?? initialDetail?.report.id`，`WorkspaceClient.tsx:138`），
   但 `initialReportId` 从 URL 来 ⇒ 刷新/分享后仍然正确。
4. **"最新版 id"必须有一个权威来源**（★评审 P1-7）。深链时 `initialDetail` 已经被 `?report=` 那份
   **替换**（`app/[symbol]/page.tsx:151-154`），所以 `initialDetail.report.id` **不是**最新版；
   `initialReports` 又默认 `includeArchived=false`（`report_service.list_versions:303-305`）。
   因此取值顺序写死，且与 `latest_of()` 同序，不允许在组件里另发明一套：
   `latestReportId = ① initialReports 里 isLatest === true 的那一行` →
   `② 否则 initialReports 里 version 最大且 provisional === false 的那一行` →
   `③ 否则 initialReports 里 version 最大的那一行` → `④ 都没有则 null（不出横幅）`。
   版本数超过 `initialReports` 的 limit 时 ② 仍成立，因为后端按 `version desc` 排（最新的一定在第一页）。

**历史版本横幅（`HistoricalBanner`）**：当 `detail.report.id !== latestReportId`（上一条的规则）时，在报告头下方渲染一条
40px 高的横幅（不是 modal、不是 toast——它必须一直在，否则读者会忘了自己在看历史）：

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⏱ 正在查看历史版本 v12 · 生成于 22 小时前      最新为 v13（4 小时前）      │
│                              [回到最新 v13]  [基于此版本更新]  [版本比较]   │
└──────────────────────────────────────────────────────────────────────────┘
```

- **回到最新**：`selectVersion(latestReportId)`（于是 URL 的 `report` 参数被删掉，符合"只记录与默认不同"）。
- **基于此版本更新**：`requestGeneration({ baseReportId: detail.report.id })` ⇒ U3。
- **版本比较**：复用既有 `openCompare()`（它已经把"上一版 vs 当前版"算好了）。
- 三个按钮都**不是**装饰：每个都对应一条既有或本轮新增的路径（AC-141 断言"点了会变"）。

**首页/库的链接**：每一行必须写成 `/{symbol}?report={id}`（**用 symbol 不用 id**：
`app/[symbol]` 接受 id，但 symbol 是可读的、可分享的；`routers/reports.py:34-40` 已经两种都认）。
AC-143 直接断言 href 的形状。

**一个容易漏的细节**：`?report=` 指向一份**已删除**（`deleted_at` 非空）的报告时，
`report_service.get()` 会返回 `None` ⇒ `api.report()` 404 ⇒ RSC 走 `catch(() => detail)`
静默回退到 latest。这个行为**保持不变**（回退到 latest 好过白屏），
但必须在详情页给一句 `notify.info('该版本已删除，已为你打开最新版')`——本轮把它做成
`?report=` 的 **404 单次提示**（用 `sessionStorage` 去重，避免每次渲染都弹）。

### 23.3.4 写路径 A：更新 = 生成新版本（U3）

「可以更新」在 v13 已经存在（`POST /instruments/{id}/reports` → run → 新版本），
本轮补的是**更新的语义**：不重复、不丢上下、不吞成本。

#### (a) 内容去重（`dedupe`）

**定义**：新文档的**内容指纹**与"当前最新版"相同 ⇒ **不新增版本**，把这次 run 指向既有那一版。

**基线是"最新非临时、未删除版本"，不是 `latest_of()`**（★评审 P1-10）：`latest_of()` 在
"标的只有临时版"时会返回 **provisional** 行（`report_service.py:285-293`）。用它当基线，
去重命中的后果是"把这次成功生成的 `runs.report_id` 指向一份失败说明"——虽然两份内容同 sha1
几乎不可能，但把正确性寄托在"几乎不可能"上不叫设计。基线查询写死为：

```sql
select * from reports
where instrument_id = :id and deleted_at is null and status != 'provisional'
order by version desc limit 1
```

provisional 行照写 `doc_sha1`（列一致性、也便于排查），但**永不参与比较**。

**指纹**：`doc_sha1 = sha1(canonical_json(doc))`，其中
`canonical = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`。
落库时与 `doc_json`（`json.dumps(doc, ensure_ascii=False)`）**分开存**：一个给人/给前端，
一个给比较。**不**在模型返回的 doc 里加任何字段（`report_doc.schema.json` 顶层
`additionalProperties: false`，加一个键就是全灭）。

**为什么是 sha1(内容) 而不是"版本号 + 时间"**：`doc` 里已经有 `asOf`、`rating`、
`sections`、`sources`、`disclaimer`；同样的数据再生成一次，模型只要有任何一处用词不同，
sha 就不同 ⇒ 我们**只**去掉"完全一模一样"的重复。这是**故意保守**的：
误判"重复"会让用户以为没更新（危险），误判"不重复"只是多一个版本（无害）。

**实现（不动既有调用，只加一个入口）**：

```python
@dataclass(frozen=True)
class PersistOutcome:
    report: Report
    deduped: bool          # True ⇒ 没有新增版本，report 是既有那一版
    recorded_cost: bool    # 恒 True；字段存在的意义是让测试能断言"成本一定记了"

def create_version(...) -> Report:                 # 签名/返回值一字不改：api/ 里 28 处调用点、14 个文件在用它（测试占 24 处），改签名就是全红
    return _persist(..., dedupe=False).report

def create_or_reuse_version(..., dedupe: bool = True) -> PersistOutcome:
    ...
```

`_persist()` 的顺序**必须**是：

1. 算出 `payload` / `doc_sha1` / `canonical`；
2. 若 `dedupe` 且**基线行**（上一条的 SQL：最新非临时、未删除）存在且
   `baseline.doc_sha1 == 新 doc_sha1`（两者非 NULL）：
   - **仍然** `cost_service.record(kind="run", ref_id=run_id, report_id=baseline.id, …)`（E-127）；
   - **仍然** `update(Run).set(report_id=baseline.id)`；
   - **仍然** `session.commit()`；
   - 返回 `PersistOutcome(report=baseline, deduped=True, recorded_cost=True)`；
   - **不**写 `reports` 行、**不**动 `is_latest`、**不**重建 FTS（内容没变，索引没变）、**不**裁剪。
3. 否则走既有的插入路径（清 `is_latest` → `max(version)+1` → insert → FTS savepoint →
   cost record → run 指向 → commit），并把 `doc_sha1` 一起写入。

`run_manager` 的 persist 段（`run_manager.py:594`）改用 `create_or_reuse_version`，
并把 `deduped` 透出到 `run.done` 帧（`run_manager.py:620-641` 追加一个键）：

```json
{"event":"run.done","data":{"reportId":"…","version":13,"deduped":true, …}}
```

前端（`useGenerationStream.onDone(reportId, meta)`）拿到 `deduped` ⇒
`notify.info('内容与 v13 一致，未新增版本')`，并且**仍然**打开 v13（不要"什么都不发生"）。

**临时版（provisional）永不参与去重**：`run_manager.py:825` 那条路仍用 `create_version(dedupe=False)`。
理由：临时卡片的内容是"失败说明 + 一段散文"，两份失败说明文本相同**不代表**两次失败相同，
把第二次的失败复用成第一次的行会让 `runs.report_id` 指向一份不带这次错误信息的文档。
（同一条纪律也解释了为什么 provisional 行**照写** `doc_sha1`：为了列一致性，也为了让“这行为什么没参与比较”能用一条 SQL 自证。）

#### (b) 基于此版本更新（`baseReportId`）

**动机**："更新"不是"重来"。用户看着 v12 说"给我更新一下"，他要的是
**结构不变、结论按新材料改**。今天两次生成是两次独立的创作，章节标题都可能换，
版本比较因此噪声巨大。

**注入点（★评审 P0-3：这是本轮最容易搞错的一处，写死）**：`{{ base_digest }}` 进的是
**任务提示词** `api/app/agent/prompts/research_task.md.j2`，渲染后落在工作区的 `prompt.md`
（`services/snapshot.py:426-442`）。而 `quality.prompt` 记的是**系统提示词**：
`prompt_quality(system_path)`（`run_manager.py:551`），`system_path` 是 `research_system.md`
的工作区副本（`snapshot.py:443-449`）。两件事不要混——这是 v1 里指错的源头。

**做法**：请求体追加 `baseReportId`（可选）。服务端把它解析成一份**结构摘要**注入提示词的
`{{ base_digest }}` 段，**上限 2048 字节**，只含：

- 标的与 `asOf`（一行）；
- 评级四元组（stance / conviction / horizon / targetPrice）**标注"上一版的判断"**；
- 8 个章节的 id + 标题（`section.title`，不含正文）；
- 至多 10 个 `keyMetrics.label`（**只给标签，不给数值**——数值是"上一版的事实"，
  把它写进提示词就等于给模型一份可以照抄的旧数，那正是接地率崩掉的方式）；
- 一句硬指令：`以上是上一版的结构与判断，不是新证据；有新材料支持时改判，否则保持一致。`

**成本与预算（★评审 P0-3：v1 的算术对着错误的文件）**：`quality.prompt.bytes` 实测是
`research_system.md` 的 **3822 B**（`report_validator.prompt_quality` 的 `bytes` 就是
`len(raw)`，`:139-143`），它**不会**因为 task 模板多了 2048 B 而变大——所以
"2163 + 2048 = 4211 < 32KB"这段推理既不成立、也证明不了任何东西。正确的两条：

1. 预算的判定口径是 `prompt.md + system.md + schema.json ≤ DEFAULT_MAX_PROMPT_BYTES`
   （`agent/schema_budget.py:56`，判定器 `assert_prompt_budget`）。`schema` 18392 B +
   `system` 3822 B + `prompt.md` 渲染后约 2.5 KB ≈ 22 KB；再加 2048 B 仍 ≈ 24 KB，
   **余量 8 KB 以上**。
2. 断言不写心算，写成实测：AC-146 断言 `prompt.md` 里 base_digest 段 ≤ 2048 B，
   门禁里跑一次 `assert_prompt_budget` 不抛（不再新增第二个预算判定器）。

**留痕（两个键，且两个键都要有生产者）**：`quality.prompt` 追加 `baseReportId` / `baseVersion`
⇒ ①`PromptQuality`（`schemas/report.py:337-342`）加两个同名字段——否则
`test_every_quality_key_has_a_response_field` 盯着的那一侧会红（这正是要它红的地方）；
②**生产者必须一起改**（★评审 P1-11）：`prompt_quality()` 目前只产 `{version, sha1, bytes}`
（`report_validator.py:127-143`），所以要么给它加关键字参数
（`prompt_quality(path, *, base_report_id=None, base_version=None)`，两个键**恒在**、值可为 `None`），
要么在 `run_manager.py:551` 的调用处合并。只加响应字段 = 加两个永远为 `None` 的空键，
这正是 `cost` / `prompt` 丢了整整两个版本的那个坑（V10-P2-7）。

**校验**：`baseReportId` 必须属于该标的、且未被硬删。不属于 ⇒ **422 `base_report_mismatch`**，
`detail` 带 `{baseReportId, instrumentId}`。已软删的版本**允许**（它仍是素材）。
★评审 P1-15：**"取不到结构"不是 422，按无 base 处理**——provisional（`sections` 为空、内容是失败说明）
与 `integrity=corrupt` 的版本都走这条路：不报错、不崩，`run.log` 记一条
`info: base_digest 跳过（原因=provisional|corrupt）`，`quality.prompt.baseReportId` **仍记原值**
（事后能看出"用户确实选了它"），但摘要段渲染成空行。

**提示词版本标记（★评审 P0-3）**：标记的宿主是 **`research_system.md:1` 的 `<!-- version: 7 -->`**
（`ask_system.md:1` 同），由 `report_validator.PROMPT_VERSION_RE` 解析并进 `quality.prompt.version`
（`report_validator.py:123-143`）；`research_task.md.j2` **没有也不需要**标记——
**不要新增第二个标记**，`test_prompt_discipline` 只认这两个 system 文件。
本轮把 `research_system.md` 的标记 **7 → 8**，语义是"提示词契约升了一版（新增 base_digest 段与它的硬指令）"，
不是"system 正文改了"。必须同步改的既有断言两处：`test_prompt_discipline.py:100`（`version == 7` → 8）
与 `:110`（`quality["version"] == 7` → 8）。不改变 `quality.engine.fingerprint`
（指纹只含版本/provider/模型/allow/schema，§21 裁决不变）。

### 23.3.5 写路径 B：回收站（U5）

**现状**：软删已经写好了（`report_service.soft_delete`：打 `deleted_at` + 删 FTS + 移 latest 指针），
但**没有回去的路**。而裁剪走的是**硬删**（`session.delete`），踩的是同一块地。

**三件事**：

1. **裁剪改软删**。`_trim_versions()` 对超限的行改为 `soft_delete()` 的批量版（同一个事务里
   打 `deleted_at`、删索引行），**不再** `session.delete`。版本号继续单调（`max(version)` 看全部行，
   含已删），所以不会复用旧版本号，`uq_reports_instrument_id_version` 也不会炸。
   三条必须写死的细节：
   - **豁免口径照旧**：只有 `pinned = 0` 的行参与计数与裁剪（`report_service.py:230-242` 的
     `Report.pinned == 0` 一个字不改）；置顶行**既不算数也不会被裁**。
   - **被裁的行不复位 latest 指针**：它们本来就是 `is_latest = 0` 的老版本，批量软删**不调**
     `soft_delete()` 里那段"找后继"的逻辑（那是给"删的正好是 latest"准备的，批量路径不该走）。
   - **软删后行会进 `scope=trash`**（★评审 P2-7）：回收站从此混着"我删的"和"系统自动清掉的"。
     本轮**不新增列**去区分（硬约束 1：只加 `doc_sha1` + 3 个索引），所以在文案层与日志层区分：
     库页回收站 tab 的说明文案写"含自动清理（30 天后永久清除）"，
     `_trim_versions` 打一条 `logger.info("trimmed %s versions into trash instrument=%s", …)`。
2. **恢复**：`POST /api/v1/reports/{id}/restore`。
   - `deleted_at = NULL`；`archived` 保持原值（不替用户取消归档）；
   - **latest 指针**：若该标的当前**没有** `is_latest=1` 且这一份是存活行里版本最大且未归档的，
     则设 `is_latest=1`，`status` 从 `superseded` 回 `ready`；否则**不碰**指针
     （`ux_reports_latest` 只允许一个，这是数据库层的硬约束）；
   - **FTS 成对**：`reindex_report_payload()` 在**同一个事务**里、用 `begin_nested()`
     包住（与 `create_version` 完全同形）——索引失败只记 error，报告仍然恢复成功；
   - 未删除的 id 调 restore ⇒ **409 `report_not_deleted`**（不要说 200，那会让前端以为状态变了）。
3. **只留一个硬删入口**：`purge_trash(session)`，条件 `deleted_at < now - TRASH_RETENTION_DAYS(30)`。
   **调用点两个，都不在 ticker 上**（★评审 P1-14）：①`main.py` 的 `lifespan`
   （与 `cleanup_workspaces()` 并列，`main.py:165`）；②常驻的 `_sweep_loop`
   （`main.py:104-121`，每 `STALE_SWEEP_SECONDS` 秒一次）。**不能挂 scheduler tick**：
   ticker 只在 `SCHEDULER_ENABLED=1` 时启动（`main.py:186-189`），测试套件里就是 0，
   于是"关掉定时刷新"的部署永远不会清理回收站，`.db` 只增不减——这与 V11-P1-4 把
   `sweep_stale_runs()` 从 ticker 挪到常驻循环是**同一个理由**。

**`purge_trash` 的完整副作用清单（★评审 P1-8：v1 只写了半条，漏掉的那条会删掉用户数据）**：

| 连带对象 | 怎么处理 | 为什么 |
| --- | --- | --- |
| `reports` 行 | 硬删 | 本入口的唯一目的 |
| `report_asks`（追问历史） | **数据库 CASCADE 会一并删除**（`models/ask.py:30-32`，`ondelete="CASCADE"`） | 不接受也不行：这是 schema 里既有的外键，本轮不改表。所以**必须**写进回收站文案——"永久清理会连同该版本的追问记录一起删除"，且 purge 打一条 `logger.info("purged %s reports / %s asks", …)` 计数 |
| `reports_fts`（搜索索引） | 同事务 `search_index.delete_report_index()`（幂等，软删时已经删过一次） | 保持"搜不到已删报告"这条既有性质 |
| `runs.report_id` | 置 `NULL`（Core UPDATE，与今天的 `_trim_versions` 一致；FK 上也有 `ondelete="SET NULL"` 兜底，`models/run.py:27`） | 审计链的"这次 run 指向哪份报告"必须变成"指向已清理的报告"而不是孤儿 |
| `cost_ledger.report_id` | **不动**。它是**无外键**的 `String(36)`（`models/cost.py:36`） | 账本不允许因为清理报告而丢行/改行（成本纪律），于是它会留下一个指向不存在报告的 id——这是**可接受的**、也是**要写清**的：账本条目永远按 `ref_id`（runId）读，`report_id` 只是旁证 |

**为什么保留期是 30 天而不是"永久"**：软删的行会一直被 `scope=all` 排除，但会持续占用
`doc_json`（本机存活 10 行的平均 `doc_bytes` = 16074 B ≈ 16 KB，见 E-119 的 bytes 列）。
30 天足够"哎呀我删错了"，又不至于让库无限膨胀。`TRASH_RETENTION_DAYS=0` ⇒ 只保留到下一次 purge，
测试里就用它来验证硬删路径。

**裁剪上限同时从 20 提到 50**（`MAX_VERSIONS_PER_INSTRUMENT`）：因为裁剪现在是软删，
20 只会让用户在回收站里看到一堆"我不认识的旧版"。50 行 × 16 KB ≈ 800 KB/标的，
对本机（2 vCPU / 49 GB）与"点开是毫秒级"的目标都可接受；置顶行仍不受限。

### 23.3.6 不变量层：三条"保存"的可执行形式（U4）

这三条不是功能，是**属性**。它们各自对应一条 AC，且都能一句话说清违反时会发生什么。

#### 不变量 1：可读性不依赖任何外部目录

`reports.doc_json` 是唯一真源；`workspace/runs/<runId>/report.json` 只是审计副本。
`cleanup_workspaces()`（`run_manager.py:1105-1128`）只删目录，不碰 `reports`——这一点今天是对的，
但**没有任何用例证明它**。AC-149 把它钉住：删掉工作区（或 `RUN_RETENTION_DAYS=0` 跑一次清理）
之后，`GET /reports/{id}` 的 `doc` 与原字节**完全相等**。

#### 不变量 2：损坏的 doc 不是 500

`Report.doc` 今天裸 `json.loads`（`models/report.py:58-60`），`GET /reports/{id}` 直接用它
（`routers/reports.py:62`、`:72`）⇒ 一行坏数据 = 一个 500 页面，而列表里仍然列着它。
本轮引入**唯一的读入口**：

```python
# services/report_service.py
def load_doc_safe(report: Report) -> tuple[dict[str, Any], str]:
    """→ (doc, integrity)；integrity ∈ {"ok","corrupt"}。永不抛。"""
```

- `ok`：能 `json.loads` 且是 `dict`（**不**在这里做 schema 校验——校验发生在写入时，
  读时再校验一次等于给每次翻页加 10 ms，并且会把"v1 的旧文档"误判成损坏）；
- `corrupt`：返回一份**最小可渲染壳**。★评审 P0-1：v1 给的壳**自己过不了校验**
  （`summary: "…"` 只有 1 个字符，而 `summary` 有 `min_length=10`，`schemas/report.py:242`；
  `asOf` 也有 `min_length=10`，`:239`），而 `ReportDetail.doc` 的类型就是 `ReportDoc`
  （`:411-413`）⇒ 壳会在**序列化**时抛 `ResponseValidationError`，只是把 500 挪了个位置。
  合规壳逐字段写死如下（每一项都对着 `ReportDoc` / `Section` / `CalloutBlock` 的约束填）：

  ```python
  def corrupt_shell(report: Report, instrument: Instrument | None) -> dict[str, Any]:
      symbol = instrument.symbol if instrument else report.instrument_id
      name = (instrument.name if instrument else symbol)[:64]
      as_of = (report.as_of or report.generated_at or "1970-01-01")[:40]
      if len(as_of) < 10:                      # ReportDoc.asOf 有 min_length=10
          as_of = f"{as_of:<10}"
      return {
          "schema": "aragon.research.report/v1",
          "provisional": True,
          "instrument": {                       # ReportInstrument 四个字段都必填
              "symbol": symbol[:24] or "UNKNOWN",
              "name": name or "UNKNOWN",
              "market": (instrument.market if instrument else "CN"),
              "currency": (instrument.currency if instrument else "CNY"),
          },
          "asOf": as_of,
          "headline": f"报告内容损坏（v{report.version} · {report.id[:8]}）"[:160],
          "rating": {"stance": "watch", "conviction": 1, "horizon": "—"},
          "summary": "这份报告的正文无法解析，已按最小可渲染内容展示；"
                     "数据本身仍在库里，可先导出留档后再决定是否清理。",   # ≥10 字符
          "keyMetrics": [],
          "sections": [{
              "id": "integrity",
              "title": "内容损坏",
              "blocks": [{
                  "type": "callout",
                  "tone": "warn",
                  "title": "正文无法解析",
                  "text": (f"报告 {report.id}（v{report.version}）的 doc_json 不是合法 JSON 或不是对象。"
                           "列表、版本链与成本记录不受影响；建议先导出留档，再决定是否删除。"),
              }],
          }],
          "risks": [],
          "sources": [],
          "disclaimer": "本页为容错视图，不含投资建议。",
      }
  ```

  它满足 `ReportDoc` 的最小约束，因此 **12 种块渲染器一行不用改**，前端拿到的是
  "一份说明自己坏了的报告"而不是白屏。`market` / `currency` / `instrument` 一律从
  `instruments` 表取（报告行本身不存这些，见 `models/report.py`），取不到才退到上面的兜底值。

**五条读路径都必须走 `load_doc_safe`**（★评审 P0-1 的另一半）：`GET /reports/{id}`
（`routers/reports.py:62`）、`GET /reports/latest`（`:72`）、**导出**（`:164`）、
**区间反查**（`:223`）、**版本比较**（`:280-281`）。v1 只改了前两条，而后三条恰恰是
"损坏横幅"要用户去做的事（"请先导出/截图"）——`export` 用裸 `report.doc` 就会 500，
把容错做成了陷阱。`export` 的语义是"给出去一份能打开的附件"，所以它对 `corrupt` 的
报告**照常 200**，只是内容是那份壳（外加一行"原文已损坏"的注记）。
- `ReportDetail` 追加 `integrity: "ok" | "corrupt"`（默认 `ok`）；
  前端在 `integrity === 'corrupt'` 时把 `TrustBadge` 切到 `warn` 并显示一行提示 +
  「在回收站之前，请先导出/截图」的指引。
- `latest_of()` / 列表的 `summarize()` 不解析 doc 的深层结构（`summarize` 已经是 try/except），
  所以损坏行不会拖垮首页——但**必须**在 `integrity=corrupt` 时把该标的的"回退"路径跑通：
  `latest_of()` 只按行取，不做 doc 解析；`GET /reports/{id}` 命中损坏壳；
  AC-147 断言"同一标的的 latest 不变，但详情页 200 + corrupt"。

#### 不变量 3：裁剪失败不影响"保存成功"这个结论

`create_version()` 在 `session.commit()` **之后**调 `_trim_versions()`（`report_service.py:205→219`，
E-128）。裁剪抛异常时：报告已落库、run 的 `report_id` 已写、成本已记，但 API 500 ⇒
前端把一次成功的生成渲染成失败（用户会再点一次，然后撞上 `run_conflict` 或预算）。
修法：`_persist()` 里把裁剪包在 `try/except Exception: logger.exception(...)` 内，
**永不**让它影响返回值；并给 `trimmed` 数量一个 debug 日志。

### 23.3.7 首页与"生成中"（U6）

首页 = 两个区块 + 一个入口：

1. **最近生成的研报**（主区块，`RecentReports`）
   `GET /reports?scope=latest&limit=6&includeActive=1&withCounts=1`，一次请求拿齐：
   最多 6 行（标题级信息）+ `active`（进行中的 run）+ `counts`。
   ★评审 P1-5：**首页必须 `withCounts=1`**——下面第 3 条要的「查看全部 N 份」用的是
   `counts.all`，而 `counts` 是唯一能给出"全库总份数"的出口（`total` 在 `scope=latest` 下
   只等于"有报告的标的数"）。所以 R-66 里"把 `withCounts=0` 当首页逃生阀"的写法作废：
   4 个 COUNT 是毫秒级，相比被替掉的 14 次串行 fetch 不是同一个量级；
   首页的逃生阀改成 `staleTime` + 骨架屏 + `limit`。
   - 每行：stance 色条（`buy/hold/sell/watch` → 既有 4 个语义色 token）· 标的名 + `symbol`（mono）·
     `headline`（单行截断，`title` 属性给全文）· 元信息 `v13 · 4 小时前 · 可信 91% · 降级/示例/临时版` ·
     右侧 `→`。
   - **`headline` 用 `title`（`{name} · 投研报告`）之外的那一句**：这是列表里唯一"有信息量"的文本。
   - 相对时间：**复用既有唯一实现** `web/lib/format.ts:80-93` 的 `relativeTime()`
     （刚刚 / N 分钟前 / N 小时前 / N 天前（<30 天）/ 本地日期）。★评审 P1-9：v1 在这里
     又写了一套四档（`<7d`），那会造出第二真源，并且让同一份报告在首页与工作台
     （`ReportPanel.tsx:404`、`VersionSwitcher.tsx:174` 都用 `format.ts`）显示不同的话。
     E-125 证明"必须有小时档"这件事，既有实现已经满足了。**不引入时区库**。
   - 空态：`还没有生成的报告 · [搜索标的开始]`（主 CTA 就是既有的 `LandingSearch`）。
2. **最近标的**（次区块，保留既有 `loadRail()` 的 watchlist 列表，但**降级**：单列、最多 6 行、
   不再显示版本徽标）。理由：自选是"我关注的"，报告是"我做过的"，两者不能互相代表（U-0a）。
3. **`查看全部 N 份报告 →`** → `/reports?scope=all`（N 来自 `counts.all`）。
   注意 href 带 `scope=all`：库页默认就是 `all`（P1-6），把 scope 写进链接的好处是
   "查看全部" 这一句与落地页的 tab 一定一致，不靠两处默认值巧合相等。

**`active` 的形状与来源**：`{runId, instrumentId, symbol, name, stage, pct, startedAt}`，
来源是 `runs` 表里 `status in ('queued','running','cancelling')` 的行（E-124 证明这张表就是真源）。
★评审 P2-8：逐字段对齐既有列，别让实现者去猜——`stage` = `runs.stage`（可空，
`models/run.py:29`）、`pct` = `runs.pct`（非空默认 0，`:30`）、`startedAt` = `runs.started_at`
（Text，ISO `Z`，`:42`）、活跃三态用既有常量 `ACTIVE_STATUSES`（`:13`，与
`report_service.count_active_runs()` 同一份定义，不要新写一个字面量元组）。
`symbol` / `name` 来自 `instruments` 的 join。
上限 3 条（再多就不是"正在生成"而是"排队事故"）。前端渲染一行：
`⟳ 正在生成 贵州茅台 v14 · research 40% · [查看]`（`[查看]` → `/{symbol}` 打开工作台看实时进度）。

**生成完成后首页怎么变**（不需要整页刷新）：
- `useGenerationStream` 的 `onDone` **已经**在有 `reportId` 时调用 `selectVersion`；
  本轮在其中追加 `queryClient.invalidateQueries({queryKey:['reports','library']})` 与
  `['reports', instrumentId]`（工作台左栏的 hasReport 徽标也要跟着变）。
- 首页若在另一个标签页：`RecentReports` 是 client 组件，用 TanStack 默认的
  `refetchOnWindowFocus` + `staleTime: 30_000` ⇒ 切回来就看到新行（AC-152 断言 30 秒内出现）。
  ★评审 P2-10：用 `initialData` 时**必须**一起给 `initialDataUpdatedAt`（取 RSC 侧那一次
  fetch 的时间戳），否则 TanStack 把它当成 `0`（= 无限陈旧）⇒ 挂载瞬间立刻再请求一次，
  AC-137 的"只发一次 `/reports`"当场就不成立。同理 `staleTime` 要大于 0，否则回到页面
  每次都重取。

### 23.3.8 `/reports` 页面：结构与交互（U1 + U7）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ A  投研终端                              [⌘K] [搜索报告…]        [主题]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 报告库 · 10 份                    全部 10 │ 星标 0 │ 归档 0 │ 回收站 5       │
│ ┌── 今天 ──────────────────────────────────────────────────────────────────┐ │
│ │ ▎贵州茅台  600519.SH   白酒动销回暖，维持“买入”              v13 4h 91% │ │
│ │ ▎Apple     AAPL        服务业务增速与估值再平衡                v1  45h 88%│ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ┌── 11月16日 ──────────────────────────────────────────────────────────────┐ │
│ │ ▎宁德时代  300750.SZ   …                                      v1  45h   │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                        [加载更多]  ·  共 10 份                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **布局**：单列，最大宽 960（与首页 760 一致的语言，但库页更宽因为每行有 5 个信息块）。
  行高 52px，`border-b border-[var(--border-subtle)]`，hover 只改背景与右侧箭头位移
  （`translate-x-0.5`，与首页卡片同一套微动效）。
- **默认视图 = `all`（"全部版本"）**（★评审 P1-6）：`/reports` 打开时是 `?scope=all`
  （没有该参数时服务端的默认是 `latest`，那是给首页与命令面板用的）。理由：库页要回答
  "我做过哪些报告、有哪些版本"，答案必须包含 superseded 版本；`latest` 只回答"每个标的默认打开哪一份"。
  scope 取值与页面 tab 的映射写死：`all / starred / archived / trash` 四个 tab，
  **`latest` 不是一个 tab**（5 个 scope ≠ 5 个 tab）。
- **scope 切换**：分段控件（`Tabs` 已有），4 个 tab 各自带计数徽章（来自 `counts`）。
  `回收站` tab 的行右侧动作集不同：`[恢复]`（没有"永久删除"——回收站里的行已经是软删的，
  本轮**不做**单行 purge，避免"永久"按钮被误点；永久出去的路只有等 `purge_trash` 的 30 天窗口）。
  ★评审 P2-7/P1-8：回收站 tab 的说明文案必须写全两件事——"**含系统自动清理的旧版本
  （30 天后永久清除）**"与"**永久清理会连同该版本的追问记录一并删除**"。
  前者因为裁剪已改软删（回收站里会混进"不是我删的"行），后者因为
  `report_asks.report_id` 是 `ondelete="CASCADE"`（`models/ask.py:30-32`）。
- **搜索**：`q` 走 URL（`?q=`），输入 250 ms 防抖后 `router.replace`（可收藏/可分享）。
- **键盘**（与既有 `lib/keyboard.ts` 的纪律一致：**不抢** `j/k`，那是工作台切标的）：
  `↑/↓` 移动焦点（`aria-selected`），`Enter` 打开，`P` 切换星标，`E` 导出 HTML，
  `Delete` 归档（不是删除——删除必须走二次确认弹窗），`/` 聚焦搜索框，`Esc` 清空搜索。
  ★评审 P2-5：已逐个核对 `matchHotkey`（`web/lib/keyboard.ts:54-82`，既有键位是
  `j/k/g/r/t/s/\/?/Escape` 与 `⌘K`），`P / E / / / ↑ ↓ / Enter / Delete` **不冲突**。
  两条纪律必须遵守：①这些键注册在**页面级** `keydown`（不要挂到 `useHotkeys` 的全局表里，
  否则离开库页后 `P` 仍然是"星标"）；②命中 `isEditableTarget(target)`（既有函数名，
  不是 `isTypingTarget`）时**全部让路**，`Delete` 尤其不能吃掉输入框里的退格。
- **无障碍**：列表 `role="listbox"` + 行 `role="option"`，`aria-label` 形如
  `贵州茅台 v13，4 小时前，可信 91%，降级`；行内 stance **不能只靠颜色**（同时有文字徽章）；
  `aria-live="polite"` 播报"已加载 10 份报告"；尊重 `prefers-reduced-motion`（既有 `theme.ts` 的偏好读取）。
- **骨架屏**：`Skeleton` 已有组件；列表首屏 loading 时渲染 6 行骨架（行高一致，避免跳动）。
- **错误态**：`ApiError` 的 `message` + `requestId` 原样显示（既有约定，前端不拼文案），
  主按钮是「重试」（`refetch`）。

---

## 23.4 数据模型

### 23.4.1 迁移 `0006_report_library`（本轮唯一的新迁移）

```python
# api/app/db/migrations/versions/0006_report_library.py
revision = "0006_report_library"
down_revision = "0005_engine_probes"

def upgrade() -> None:
    op.add_column("reports", sa.Column("doc_sha1", sa.String(length=40), nullable=True))
    # ★评审 P0-2 的连带修正：`latest` scope 不筛 archived，所以这个索引的谓词只能是
    # `deleted_at IS NULL`。SQLite/PG 都能在"查询谓词更强"时使用部分索引
    # （`deleted_at IS NULL AND archived = 0` ⇒ `deleted_at IS NULL`），所以首页（latest）
    # 与库页（all，带 archived = 0）**共用这一个索引**，不需要第二个。
    op.create_index(
        "ix_reports_feed", "reports", ["generated_at", "id"],
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_reports_starred", "reports", ["generated_at"],
        sqlite_where=sa.text("pinned = 1 AND deleted_at IS NULL"),
        postgresql_where=sa.text("pinned = 1 AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_reports_trash", "reports", ["deleted_at"],
        sqlite_where=sa.text("deleted_at IS NOT NULL"),
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
```

- `nullable=True` 且**无默认值**：历史行是 NULL，语义是"不知道指纹"（硬约束 5）。
- **不**建唯一索引：同一内容可以是两个版本（用户明确 `dedupe=false` 时，或两次生成之间 `asOf` 相同
  但模型措辞不同时）；唯一约束会让一次正常生成变成 `IntegrityError`。
- **不**重建表：SQLite 的 `batch_alter_table` 会 copy 整表 + 换名，而 `reports` 里最大的一列是
  `doc_json`（15 行 ≈ 200 KB，但生产会更大）；nullable column 的 `add_column` 是 O(1)。
- 三个索引全部**部分索引**（`WHERE`）：`deleted_at IS NULL` 是几乎所有查询的公共前缀，
  全表索引会比它大一倍且没被用到。三者的谓词分别是 `deleted_at IS NULL`（feed）/
  `pinned = 1 AND deleted_at IS NULL`（starred）/ `deleted_at IS NOT NULL`（trash + purge）。
- **`downgrade()` 必须能回滚**（★评审 P2-9）：先 `drop_index` 三个，再
  `op.drop_column("reports", "doc_sha1")`。SQLite 的 `DROP COLUMN` 需要 **≥ 3.35**——
  本机 `sqlite3.sqlite_version = 3.41.2`（实测窗口函数与 `json_valid` 都可用），
  部署机同为 Ubuntu 24.04 / Python 3.12 自带版本，满足；但 `test_migration_0006` 里要
  **真的跑一次 upgrade→downgrade→upgrade**，而不是只断言列存在。

**`models/report.py` 同步**：`doc_sha1: Mapped[str | None] = mapped_column(String(40))`。
`index=True` **不要**加（索引在迁移里显式建，带 `WHERE`；`index=True` 会再建一个无 `WHERE` 的重复索引）。

### 23.4.2 不建新表的三件事（以及为什么）

| 诱惑 | 为什么不做 | 替代 |
| --- | --- | --- |
| `report_library` 表（"报告库"自己的表） | 报告库 = `reports` 的一个投影。建表就要同步两份状态（删/恢复/裁剪），必然漂移 | `GET /reports` 的 5 个 scope 直接投影 |
| `report_tags` 表（标签/文件夹） | V3 已明确不做；标签的真实需求（"这份是给老板看的"）无法用标签解决 | `pinned`（星标）+ `q` + 标的过滤 |
| `report_dedup` 表（指纹 → 版本） | 指纹是 `reports` 的一列，比较时只需要"当前最新版"这一行 | `latest_of()` + `doc_sha1` 等值比较 |

### 23.4.3 `ReportLibraryItem`（响应模型，不是表）

```python
class LibraryInstrument(BaseModel):
    id: str; symbol: str; name: str; market: Literal["CN","HK","US"]; currency: str

class ReportLibraryItem(BaseModel):
    report: ReportSummary          # 复用既有模型：一列都不改名、不加键
    instrument: LibraryInstrument  # 一次 join 拿齐，前端不再逐行查标的
    docSha1: str | None = None     # NULL = 早期版本（不回填）
    integrity: Literal["ok", "corrupt"] = "ok"
    # ★评审 P1-13：回收站要显示"删除于 X / 还剩 N 天"，而硬约束 2 不许往 ReportSummary
    # 加键 —— 所以它落在**新模型**的这一层（新增模型允许追加字段）。
    deletedAt: str | None = None   # 非删除行恒 None；trash scope 里非空

class ActiveRunItem(BaseModel):
    runId: str; instrumentId: str; symbol: str; name: str
    stage: str | None = None; pct: int = 0; startedAt: str

class LibraryResponse(BaseModel):
    items: list[ReportLibraryItem]
    nextCursor: str | None = None
    total: int = 0
    counts: dict[str, int] = Field(default_factory=dict)   # 键 = 5 个 scope 名
    active: list[ActiveRunItem] = Field(default_factory=list)
```

**`integrity` 是 SQL 算出来的，不是 Python 解析出来的**（★评审 P1-2）：列表与详情用的是
**同一份判定语义**、但**不同的实现成本**——

```sql
-- list_page(): 一次查询里顺手带出来，不额外解析任何 doc
case when json_valid(r.doc_json) then 'ok' else 'corrupt' end as integrity
```

`json_valid()` 只做语法检查（本机 3.41.2 实测 `select json_valid('{') → 0`），对 20 行
× 16 KB 是最便宜的做法；Python 侧只在**详情**路径调 `load_doc_safe()` 取完整壳。
之所以不允许在列表里 `json.loads`：那会让 p95 与行长（`doc_json` 平均 16 KB）线性相关，
同时把"列表不解析深层结构"这条纪律变成空话。

**为什么 `report` 是嵌套的 `ReportSummary` 而不是把字段平铺**：平铺会让 `ReportSummary` 的 23 个键
在库里出现第二份定义（`schemas/report.py` 与 `schemas/library.py`），这是本项目已经罚过的漂移模式
（`test_the_new_capabilities_fields_reach_the_wire_model` 就是为它写的）。嵌套的代价只是前端多一层
`item.report.version`。

**`counts` 的键**：`{"latest","all","starred","archived","trash"}`，**不**含中文标签（文案在前端
`copy.ts`，服务端不下发 UI 文案——既有纪律）。

---

## 23.5 接口设计

### 23.5.1 `GET /api/v1/reports`

| 参数 | 类型/范围 | 默认 | 说明 |
| --- | --- | --- | --- |
| `scope` | `latest｜all｜starred｜archived｜trash` | `latest` | §23.3.2 的五个语义；非法值 → 422 `validation_error`（pydantic Literal） |
| `q` | `str`，≤80 | `""` | 匹配 `title`/`headline`/`symbol`/`name`；`%`/`_` 转义 |
| `instrumentId` | `str`，≤32 | — | 精确过滤；不存在 → 200 空列表（**不** 404：过滤条件不匹配不是"资源不存在"） |
| `limit` | 1..50 | 20 | `limit+1` 取一条判 `has_more` |
| `before` | `"{iso}\|{id}"`，`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\|[0-9a-f]{32}$` | — | keyset 游标，**原样透传、不做时间解析**（P1-3）；客户端 `encodeURIComponent`；非法 → 422 `invalid_cursor`（`detail={before}`） |
| `withCounts` | 0/1 | 1 | 0 ⇒ `counts={}`（省 5 次 COUNT，其中 4 次是"别的 scope"）；`total` **仍给且仍准确**（它是当前 scope + 过滤条件的 count，与 `counts` 是两件事，见 §23.3.2）。★首页**不要**关掉它：首页需要 `counts.all` 才有「查看全部 N 份」（P1-5） |
| `includeActive` | 0/1 | 0 | 1 ⇒ 附带 `active`（进行中的 run，≤3） |

**响应 200**：

```json
{
  "items": [
    {
      "report": { "id": "d810aa2e…", "instrumentId": "…", "version": 13, "status": "degraded",
                  "title": "贵州茅台 · 投研报告", "headline": "白酒动销回暖，维持“买入”",
                  "stance": "buy", "asOf": "2026-11-17", "generatedAt": "2026-11-18T03:23:57Z",
                  "isLatest": true, "pinned": false, "archived": false, "provisional": false,
                  "groundedRatio": 0.91, "quality": { "…": "…" }, "costKnown": true, "docBytes": 17131 },
      "instrument": { "id": "…", "symbol": "600519.SH", "name": "贵州茅台",
                      "market": "CN", "currency": "CNY" },
      "docSha1": "9f2c…", "integrity": "ok", "deletedAt": null
    }
  ],
  "nextCursor": "2026-11-16T10:10:22Z|45c9281f15c349afbc0dbfff7796eef7",
  "total": 3,
  "counts": { "latest": 3, "all": 10, "starred": 0, "archived": 0, "trash": 5 },
  "active": [ { "runId": "…", "instrumentId": "…", "symbol": "600519.SH", "name": "贵州茅台",
                "stage": "research", "pct": 40, "startedAt": "2026-11-18T06:40:00Z" } ]
}
```

> 示例里省略了 `provider` / `model` / `costUsd` / `tokensIn` / `tokensOut` / `durationMs`
> 只为可读。它们属于 `ReportSummary`（`schemas/report.py:383-408`，23 个键）的一部分，
> 由同一个 `summarize()` 产出——实现时**不得**少发，也不要为列表另写一个"裁剪版"，
> 那会让同一份报告在库页与工作台长得不一样。

**消费者**：首页（`scope=latest&limit=6&includeActive=1&withCounts=1`）、
`/reports`（`scope=all`，`useInfiniteQuery`）、`CommandPalette`（`limit=5`，做"最近报告"的动态项）。

**NFR**：本机 1 万行报告下 `p95 < 30 ms`。`EXPLAIN QUERY PLAN` 的判据（★评审：与 P0-2 的
索引谓词修正保持一致）：`scope=latest` 的 CTE 与 `scope=all` 的列表**都**必须显示
`SEARCH reports USING INDEX ix_reports_feed`（谓词已改成 `deleted_at IS NULL`；查询谓词
比索引谓词更强时部分索引照样可用，所以一个索引同时服务 latest 与 all）；
`scope=trash` 必须显示 `ix_reports_trash`；`scope=starred` 必须显示 `ix_reports_starred`。
p95 用 `scripts/library-bench.py` 证明，**不放进 pytest**（pytest 里跑时序断言是 flakes 之源）；
但上面那三条"用了哪个索引"**可以**进 pytest——它们是确定性的，不是时序。

### 23.5.2 `POST /api/v1/reports/{report_id}/restore`

| 情况 | 响应 |
| --- | --- |
| 存在且已软删 | **200** `ReportSummary`（`deleted` 状态不在 summary 里；★P1-13 的 `deletedAt` 在**列表模型**上、不在这个响应里，所以"恢复成功"的证据就是 200 + 随后列表里重新出现） |
| 不存在（含从未存在） | **404** `report_not_found` |
| 存在但没删 | **409** `report_not_deleted`（`detail={reportId}`） |

副作用（同一事务）：`deleted_at=NULL`；必要时抢 `is_latest`（§23.3.5 第 2 条）；
FTS 重建（savepoint 保护）。**不**重算 grounding / 不回填 `quality`（老报告不回填，§19 裁决）。
★P1-8 的另一面：restore **不会**把"被 CASCADE 删掉的追问"变回来——那些行在 `purge_trash`
时就已经没了。30 天窗口内 restore 是完全无损的（含追问），只有硬清理之后才不可逆。

### 23.5.3 `POST /api/v1/instruments/{instrument_id}/reports`（追加两个请求字段）

```python
class CreateReportRequest(BaseModel):
    reason: Literal["manual", "refresh"] = "manual"
    force: bool = False
    dedupe: bool = True                 # 追加：内容相同 ⇒ 不新增版本（§23.3.4a）
    baseReportId: str | None = None     # 追加：把上一版结构摘要注入提示词（§23.3.4b）
```

- 202 响应体**不改名**，只追加 `"dedupe": true`（便于前端在 `run.done` 之前就能显示预期行为）。
  这个字段说的是"这次请求**请求了**去重"，不是"这次一定会去重"——最终结论只有一个来源：
  `run.done.deduped`。
- `baseReportId` 校验失败 → 422 `base_report_mismatch`（`detail={baseReportId, instrumentId}`）。
- 优先级：`409 run_conflict` / `429 too_many_runs` / `402 monthly_budget_exceeded` 的既有顺序
  **一个字不改**（§21 裁决 K-6）；`baseReportId` 校验在它们**之后**（一个开不了的请求不该先报 422）。

### 23.5.4 既有端点的追加（只加不改）

| 端点 | 追加 | 消费者 / 读不到会怎样 |
| --- | --- | --- |
| `GET /reports/{id}` | `ReportDetail.integrity` | 前端 TrustBadge；缺字段 ⇒ 前端按 `ok` 处理（向后兼容），损坏壳仍然可读 |
| `GET /instruments/{id}/reports/latest` | 同上 | 工作台首屏 |
| SSE `run.done` | `deduped: bool` | `useGenerationStream`；缺字段 ⇒ 不提示"未新增版本"（旧后端仍可用） |
| `GET /capabilities` | `features.reportLibrary: bool` | 首页/导航是否显示「报告库」入口；`false` 时前端**不渲染**入口（不留死链） |
| `quality.prompt` | `baseReportId` / `baseVersion` | 版本比较的"这次是基于哪一版更新的"；缺失 ⇒ 显示"独立生成" |

`features.reportLibrary` 的**实测判据**：`0006` 已应用（`reports.doc_sha1` 列存在）+ 一次
`select 1 from reports where doc_sha1 is not null limit 1` 不抛异常。
★评审（一致性）：**不是**"启动时测一次、缓存"，而是与既有 `features.*` **同一形状**——
`api/app/routers/capabilities.py` 每次请求实测一次（`_cost_ledger_available(db)` 在 `:224`，
`_grounding_available()` 在 `:220`，都是这个形状）。原因写在 `FeaturesOut` 的 docstring 里：
"每一个开关都来自一次**测量**"。所以新增 `_report_library_available(db)`，
形态照抄 `_cost_ledger_available`，一条 `select ... limit 1`，成本可忽略。
禁止硬编码 `True`。

### 23.5.5 新增错误码（3 条，全是 HTTP 码）

| 码 | HTTP | 触发 | `detail` | 前端动作 |
| --- | --- | --- | --- | --- |
| `invalid_cursor` | 422 | `before` 不是 `"{iso}\|{32 hex}"` | `{before}` | 「重新从第一页加载」（按钮 = `invalidateQueries`） |
| `report_not_deleted` | 409 | 对未删除的报告调 restore | `{reportId}` | 「刷新列表」（状态被别的标签页改过了） |
| `base_report_mismatch` | 422 | `baseReportId` 不属于该标的 / 已被硬删 | `{baseReportId, instrumentId}` | 「改为独立生成」（用 `dedupe` 重发，不带 base） |

同步点（**四处，一処不改就红**；★评审 P2：v1 把第三处写成"仅当…否则…"，那是个坑）：

1. `api/app/errors.py`：3 个 `AppError` 子类（照既有形状 `code = "…"` + `status_code`）。
2. `contracts/run_error_codes.json`：`version` 2 → 3，`httpCodes` 追加 3 条，
   **每条都要带 `action`**（既有 7 条都有这个键，`web/tests/runErrors.test.ts:45` 会逐行比对
   `action`）。三个 `action` 取值写死：`invalid_cursor` → `"retry"`、
   `report_not_deleted` → `"retry"`、`base_report_mismatch` → `"retry"`
   （三个都是"换个参数/刷新一下就能继续"，语义与既有 `schema_drift` 的 `retry` 同类）。
3. `web/lib/runErrors.ts` 的 **`HTTP_ERROR_ACTIONS`**（不是 `RUN_ERROR_ACTIONS`）：追加同样三条，
   带 `action` 与 `primary`。这一步**是必做项，不是"仅当"**——
   `web/tests/runErrors.test.ts:38` 断言 `HTTP_ERROR_ACTIONS ⊇ httpCodes`，
   少一条测试立刻红（这正是我们要的）。
4. `web/lib/copy.ts`：三条错误文案（前端不拼文案，服务端不下发 UI 文案——既有纪律）。

**不进** `RUN_ERROR_CODES`：它不是 run 的结局，是"这次请求参数不对"
（`api/tests/test_contract_codes.py:62` 会替我们盯着）。

---

## 23.6 文件 / 模块变更计划

> 规则：**新增**文件的"意图"列必须说清它替代/拆分了谁（O-39）；**修改**文件的意图列必须说清改哪一段。
> 全部路径相对仓库根，分隔符一律 `/`。

### 23.6.1 后端（`api/`）

| 文件 | 动作 | 意图（一句话） | 对应 |
| --- | --- | --- | --- |
| `api/app/db/migrations/versions/0006_report_library.py` | 新增 | 本轮唯一迁移：加 `doc_sha1` + 3 个部分索引 | U4 |
| `api/app/models/report.py` | 修改 | 加 `doc_sha1` 列（`String(40)`，可空）；`doc` property 保留（读路径走 `load_doc_safe`） | U4 |
| `api/app/services/report_library.py` | 新增 | 报告库读路径的唯一实现：`list_page/counts/cursor/active_runs`（替代首页 N+1 与前端筛选） | U1 |
| `api/app/services/report_service.py` | 修改 | +`create_or_reuse_version()` / `restore()` / `load_doc_safe()` / `purge_trash()`；`_trim_versions` 改软删 + 包 try/except；`MAX_VERSIONS_PER_INSTRUMENT` 20→50 | U3/U4/U5 |
| `api/app/services/run_manager.py` | 修改 | persist 段改用 `create_or_reuse_version`；`run.done` 追加 `deduped`；`baseReportId` → `base_digest` 注入 | U3 |
| `api/app/routers/reports.py` | 修改 | +`GET /reports`（列表）+`POST /reports/{id}/restore`；**五条读路径**全部改用 `load_doc_safe`（详情 `:62`、latest `:72`、导出 `:164`、区间反查 `:223`、版本比较 `:280-281`）——后三条不改，那句「请先导出留档」就是一句会 500 的指引（P0-1） | U1/U4/U5 |
| `api/app/schemas/library.py` | 新增 | `ReportLibraryItem / LibraryResponse / ActiveRunItem / LibraryScope` | U1 |
| `api/app/schemas/report.py` | 修改 | `ReportDetail.integrity`；`CreateReportRequest.dedupe/baseReportId`；`PromptQuality.baseReportId/baseVersion` | U3/U4 |
| `api/app/schemas/capabilities.py` | 修改 | `FeaturesOut.reportLibrary: bool = False`（实测推导） | U1 |
| `api/app/routers/capabilities.py` | 修改 | 填 `features.reportLibrary`（`0006` 已应用 + 列可查） | U1 |
| `api/app/errors.py` | 修改 | +`InvalidCursor(422)` / `ReportNotDeleted(409)` / `BaseReportMismatch(422)` | U5/U3 |
| `api/app/config.py` | 修改 | +`trash_retention_days: int = 30`（`.env` 同步 `.env.example`） | U5 |
| `api/app/main.py` | 修改 | ①lifespan 里加 `report_service.purge_trash()`（与 `cleanup_workspaces()` 并列，`main.py:165`）；②**把同一句挂进常驻 `_sweep_loop`**（`main.py:104-121`，每 `STALE_SWEEP_SECONDS` 秒）——**不挂 ticker**（P1-14） | U5 |
| `api/app/agent/prompts/research_task.md.j2` | 修改 | +`{{ base_digest }}` 段（无 base 时渲染为空行，**不留占位符痕迹**）。**这个文件没有版本标记，也不要加**（P0-3） | U3 |
| `api/app/agent/prompts/research_system.md` | 修改 | 只改第 1 行的 `<!-- version: 7 -->` → `8`（版本标记的唯一宿主，`prompt_quality()` 读的就是它，P0-3）。**正文不改** | U3 |
| `api/app/services/report_validator.py` | 修改 | `prompt_quality()` 扩展出 `baseReportId` / `baseVersion` 两个键（恒在、可为 `None`）——**这才是 `quality.prompt.*` 的生产者**（P1-11） | U3 |
| `scripts/backfill-report-sha1.py` | 新增（**运维可选，不属于任何里程碑判据**） | 回填 `doc_sha1`（幂等、默认 `--dry-run`、必须显式 `--apply`、按标的分批 commit、只碰 `doc_sha1 IS NULL`）。脚本头部必须写明"跑过它就等于推翻硬约束 5 / V11-P0-1，需要用户显式同意"（P1-16） | — |
| `scripts/library-bench.py` | 新增 | 1 万行合成数据的列表压测（证明 §23.5.1 的 p95）+ 三条 `EXPLAIN QUERY PLAN`，**不进 pytest** | U1 |

### 23.6.2 契约与配置

| 文件 | 动作 | 意图 | 对应 |
| --- | --- | --- | --- |
| `contracts/run_error_codes.json` | 修改 | `version` 2→3；`httpCodes` 追加 3 条，**每条带 `action`**（`RUN_ERROR_CODES` 保持 21 条） | U3/U5 |
| `web/lib/runErrors.ts` | 修改 | `HTTP_ERROR_ACTIONS` 追加 3 条（带 `action` / `primary`）——`web/tests/runErrors.test.ts:38` 会红（P2） | U5/U3 |
| `.env.example` | 修改 | `TRASH_RETENTION_DAYS=30` 与一段注释（"0 表示下一次 purge 就硬删，仅供测试"） | U5 |
| `README.md` | 修改 | 加一段「报告库（v14）」的入口与两条不变量（**不**改既有任何一节的结论） | 全部 |

### 23.6.3 前端（`web/`）

| 文件 | 动作 | 意图 | 对应 |
| --- | --- | --- | --- |
| `web/app/reports/page.tsx` | 新增 | `/reports` 的 RSC 壳：metadata + 首屏 `initialData`（一次 `api.reportLibrary()`） | U1 |
| `web/components/library/ReportLibraryClient.tsx` | 新增 | 列表客户端：`useInfiniteQuery`、scope/搜索/键盘、动作（星标/归档/恢复/导出） | U1/U7 |
| `web/components/library/ReportLibraryRow.tsx` | 新增 | 一行（52px）：stance 条 + 标题 + 元信息 + 动作；替代首页卡片里的版本徽标逻辑 | U1/U7 |
| `web/components/library/LibraryFilters.tsx` | 新增 | 4 个 scope 分段 + 计数徽章 + 搜索框（防抖 + `?q=`） | U1 |
| `web/components/library/ActiveRunsStrip.tsx` | 新增 | 「正在生成」最多 3 行（来自 `active`），点进去看实时进度 | U6 |
| `web/components/library/RecentReports.tsx` | 新增 | 首页主区块（client，`initialData` 来自 RSC，focus 时刷新） | U1/U6 |
| `web/components/library/useReportLibrary.ts` | 新增 | `useInfiniteQuery` + 游标拼接 + `getNextPageParam` 的唯一实现（三个消费者共用） | U1 |
| `web/components/report/HistoricalBanner.tsx` | 新增 | 历史版本横幅：回到最新 / 基于此版本更新 / 版本比较 | U2 |
| `web/lib/library.ts` | 新增 | 纯函数：`scopeLabels`、`encodeCursor/decodeCursor`（`encodeURIComponent` + 三段式校验）、`groupByDay`、`libraryHref`。**不含 `relativeTime`**：那份唯一实现在 `web/lib/format.ts:80-93`，这里只 import（P1-9） | U1/U7 |
| `web/lib/types.ts` | 修改 | +`ReportLibraryItem` / `LibraryResponse` / `ActiveRunItem` / `LibraryScope`；`ReportDetail.integrity` | U1 |
| `web/lib/api.ts` | 修改 | +`reportLibrary(params)` / `restoreReport(id)`；`createReport` 带 `dedupe/baseReportId` | U1/U3 |
| `web/lib/copy.ts` | 修改 | +`library.*` / `history.*` / `landing.recentReports` 文案（服务端不下发文案的纪律） | 全部 |
| `web/app/page.tsx` | 修改 | 删掉 N+1 的 `loadRail()` 报告部分（`page.tsx:16-39` 的 `Promise.all` 与 12 次 `latestReport`），改一次 `reportLibrary({scope:'latest',limit:6,includeActive:1,withCounts:1})`；主区块换成 `RecentReports`，自选降级为次区块（`/watchlist` 那一次保留，它自带 instrument，**不再需要** `/instruments` —— 所以首页是 2 次 fetch，不是 3 次） | U1 |
| `web/components/shell/AppShell.tsx` | 修改 | header 加「报告库」入口（`features.reportLibrary` 为真才渲染）+ 品牌区变可点回首页 | U1 |
| `web/components/shell/WorkspaceClient.tsx` | 修改 | `selectVersion` 写 URL（唯一真源）；生成完成后失效 `['reports','library']`；接线 `HistoricalBanner` | U2/U6 |
| `web/components/report/ReportPanel.tsx` | 修改 | 接收 `history` 信息（最新版 id/版本）+ 渲染横幅；`integrity=corrupt` 时 TrustBadge 转 warn | U2/U4 |
| `web/components/shell/CommandPalette.tsx` | 修改 | +「打开报告库」+ 最近 5 份报告（来自 `reportLibrary({limit:5})`） | U1 |
| `web/components/generation/useGenerationStream.ts` | 修改 | `done` 帧读 `deduped` → `onDeduped(version)` 回调（父组件弹 info 并打开该版本） | U3 |

### 23.6.4 测试

| 文件 | 动作 | 断言什么 |
| --- | --- | --- |
| `api/tests/test_report_library_api.py` | 新增 | 列表 200 形状、5 个 scope、游标翻页不重不漏、422 `invalid_cursor`、`withCounts=0` |
| `api/tests/test_report_library_service.py` | 新增 | `scope=latest` ≡ `latest_of()`（逐标的比对，属性测试）、counts 与列表同源 |
| `api/tests/test_report_integrity.py` | 新增 | 损坏 `doc_json` → 200 + `integrity=corrupt` + 最小壳可被 `ReportDoc` 校验；列表页不炸 |
| `api/tests/test_dedupe.py` | 新增 | 相同内容不新增行、run 指向既有版、`run.done.deduped=true`、成本仍记一行 |
| `api/tests/test_report_trash.py` | 新增 | 裁剪=软删、pinned 豁免、restore 200/409、`purge_trash` 窗口、FTS 成对 |
| `api/tests/test_migration_0006.py` | 新增 | 迁移后列存在、3 个索引存在且带 `WHERE`、`downgrade` 可回滚 |
| `api/tests/test_reports_api.py` | 修改 | 追加：`GET /reports/{id}` 的 `integrity` 字段；`GET /reports/abc`（32 位以外）仍 404 不被列表吞掉；**导出损坏报告返回 200 且是附件**（P0-1） |
| `api/tests/test_contract_codes.py` | 修改 | `httpCodes` 新增 3 条且仍不与 run 码相交（既有断言会自动覆盖，只需确认它绿）；**每条都要有 `action`**（`web/tests/runErrors.test.ts:45` 逐行比对） |
| `api/tests/test_report_service.py` | 修改 | ①追加：裁剪异常不影响 `create_version` 返回（AC-148 的第二半）；②**改既有** `test_hard_delete_nulls_the_run_link_instead_of_failing`（`:181-200`）——它断言"老版本被硬删"，裁剪改软删后必红；改成"裁剪把老版本软删进回收站 + `purge_trash` 之后 `runs.report_id` 才变 NULL"两段（P1-1） |
| **`api/tests/test_capabilities_api.py`** | **修改** | `:154` 的 `body["features"] == {…}` 是**精确相等**的 17 键字典，必须补 `"reportLibrary": True`；并加一条"`0006` 未应用 ⇒ False"的夹具（P1-1） |
| **`api/tests/test_prompt_discipline.py`** | **修改** | `:100` 与 `:110` 的 `version == 7` → `8`；`:109` 的 `bytes == len(research_system.md)` 保持（它证明 `quality.prompt.bytes` 量的是 system 而不是 task，P0-3/P1-1） |
| **`api/tests/test_prompt_quality_keys.py`**（新增） | 新增 | `quality.prompt` 的四个键（version/sha1/bytes/baseReportId/baseVersion）**既在 `PromptQuality` 上、也有生产者**（跑一次真实 `prompt_quality()` 断言键集合，P1-11） |
| `web/tests/report-library.test.tsx` | 新增 | 列表渲染/分组/相对时间/键盘/空态/错误态/骨架 |
| `web/tests/historical-banner.test.tsx` | 新增 | 横幅只在历史版本出现；三个按钮各自触发正确回调；「回到最新」删掉 `report` 参数 |
| `web/tests/library-link.test.ts` | 新增 | `libraryHref()` 100% 带 `?report=`；`encodeCursor/decodeCursor` 往返（含 `%7C` 编码与非法串返回 `null`，P1-3） |
| `web/tests/format-relative-time.test.ts` | 修改 | 断言库页与工作台**共用** `format.ts::relativeTime`（同一输入同一输出，含"刚刚"与 30 天档，P1-9） |
| `web/tests/workspace-url.test.tsx` | 新增 | `selectVersion` 后 `router.replace` 收到 `report=<id>`，且既有 `view/range/tf/win/adj` 不变 |
| `web/tests/e2e/library.spec.ts` | 新增 | 首页 → 点一行 → 打开的就是那一版 → 切版本 → 刷新仍是那一版 |
| `web/tests/e2e/fixtures/fake-api.mjs` | 修改 | 加 `GET /api/v1/reports`（两页游标）与 `POST /reports/{id}/restore` |

---

## 23.7 测试与验收标准

### 23.7.1 先红后绿清单（实现节点按顺序做，每步先看红）

| # | 用例（新） | 今天的红 | 修完之后 |
| --- | --- | --- | --- |
| 1 | `test_reports_api.py::test_get_report_with_corrupt_doc_returns_200` | 500（`json.decoder.JSONDecodeError`） | 200 + `integrity=corrupt` |
| 2 | `test_report_service.py::test_trim_failure_does_not_fail_the_persist` | 抛 `RuntimeError` 到调用方 | 返回 `Report`，日志有 exception |
| 3 | `test_report_trash.py::test_trim_soft_deletes_and_enters_trash` | 行被 `session.delete`（不可恢复） | `scope=trash` 命中 |
| 4 | `test_report_trash.py::test_restore_brings_it_back` | 404（端点不存在） | 200 |
| 5 | `test_report_library_api.py::test_list_reports_across_instruments` | 405/404 | 200 + 3 行 |
| 6 | `test_report_library_api.py::test_cursor_pagination_has_no_duplicates` | 不存在 | 45 行 3 页不重不漏 |
| 7 | `test_report_library_service.py::test_latest_scope_matches_latest_of` | 不存在 | 逐标的相等 |
| 8 | `test_dedupe.py::test_same_content_does_not_create_a_version` | 行数 +1 | 行数不变，`deduped=true` |
| 9 | `test_dedupe.py::test_dedupe_still_records_cost` | —（"跳过 create_version"的诱惑） | `cost_ledger` +1 |
| 10 | `test_dedupe.py::test_base_report_must_belong_to_the_instrument` | 422 不存在 | 422 `base_report_mismatch` |
| 11 | `web/tests/library-link.test.ts` | 首页 href 无 `?report=` | 100% 带 |
| 12 | `web/tests/workspace-url.test.tsx::test_select_version_writes_the_url` | `router.replace` 未被调用 | 调用一次且参数正确 |
| 13 | `test_reports_api.py::test_export_of_a_corrupt_report_still_200` | 500（`export` 用裸 `report.doc`） | 200 + 附件 + 壳内容 |
| 14 | `test_capabilities_api.py::test_features_dict_is_exact`（既有，改后应绿） | 加 `reportLibrary` 后 `==` 不等 ⇒ 红 | 17 → 18 键全绿 |
| 15 | `test_report_service.py::test_hard_delete_nulls_the_run_link_instead_of_failing`（既有，改语义） | 现在绿；改软删后红 | 拆成"软删进回收站"+"purge 后 SET NULL"两段 |
| 16 | `test_prompt_discipline.py::test_prompt_quality_matches_the_file_on_disk`（既有，改常量） | `version == 7` | `== 8`，`bytes` 仍等于 `research_system.md` |
| 17 | `test_prompt_quality_keys.py::test_both_new_keys_have_a_producer` | 键不存在 | 四个键都在，且 `baseReportId` 有值/为 `None` 都合法 |

### 23.7.2 验收标准 AC-137 ~ AC-152

| AC | 语句（可机器断言） |
| --- | --- |
| **AC-137** | 库里有 N 份 `scope=latest` 报告时，首页首屏渲染 `min(N, 6)` 行报告卡片；每行带 `data-report-id`。**断言分两种夹具**（★评审 P2-12）：`N ≤ 6` ⇒ 集合**相等**；`N > 6` ⇒ 是库内集合的**子集**且按 `generatedAt desc` 恰为前 6 个。RSC 只发 **1** 次 `/reports` + **1** 次 `/watchlist`，**不存在**任何 `/reports/latest` 调用，也**不存在**客户端挂载后再发一次 `/reports`（`initialDataUpdatedAt`，P2-10） |
| **AC-138** | 3 个标的共 45 份报告，`limit=20` 连续翻页得到 45 个**互不重复**的 id，第三页 `nextCursor=null`；翻页过程中插入一份新报告，已翻过的页不重复出现、总数不丢 |
| **AC-139** | 5 个 scope 的 id 集合分别等于用 SQL 直接算出的集合；且 `scope=latest` 对每个标的返回的 id **逐条等于** `report_service.latest_of()`。★评审 P0-2：夹具必须**同时**覆盖四种标的——①正常多版本 ②只有临时版 ③**最新版被归档**（`set_flags(archived=True)` 会清 `is_latest`，`report_service.py:330-335`）④只有归档过的版本。夹具缺 ③④ 时这条 AC 会"绿得没有说服力"，而实现者一旦给 CTE 加 `archived = 0` 就会在 ③ 上暴露 |
| **AC-140** | **无过滤**时 `counts[scope] == total == len(全量翻页结果)`，对 5 个 scope 各断言一次；`withCounts=0` 时 `counts={}` 但 `total` 仍等于该 scope 的全量行数。★评审 P1-4 追加一条：带上 `q` 后 `counts` **一个数字都不变**、`total` 变小（5 个 scope 各断言一次） |
| **AC-141** | `GET /{symbol}?report=<旧版id>` 首屏 `detail.report.id` 等于该 id；页面存在历史版本横幅且文案包含"历史版本"与最新版号；点「回到最新」后 URL 的 `report` 参数消失、`detail` 为最新版 |
| **AC-142** | `onSelectVersion(id)` 调用后 `router.replace` 恰好一次，参数含 `report=<id>`，且既有 `view/range/tf/win/adj` 的现值一个都没丢；选中的是"最新版"时 `report` 参数被删除 |
| **AC-143** | 首页与 `/reports` 的每一行 href 都匹配 `^/[A-Za-z0-9._-]+\?report=[0-9a-f]{32}$`（`libraryHref` 单测 + e2e 各一次） |
| **AC-144** | 同一份 doc 连续两次生成：第二次 `reports` 行数不变、`run.status=succeeded`、`run.report_id` 指向**既有**报告 id、`run.done.deduped=true`、`version` 与既有版相同 |
| **AC-145** | 上一条的第二次生成仍然新增**一行** `cost_ledger`（`kind=run`、`ref_id=<新 runId>`、`usd`/`source` 与第一次同源），`/cost/summary` 的 `spentUsd` 相应增加 |
| **AC-146** | `baseReportId` 属于别的标的 → 422 `base_report_mismatch`；指向 provisional 或损坏版 → **不报错**、按无 base 处理（`run.log` 有 `info`、`quality.prompt.baseReportId` 仍记原值）；合法时工作区 `prompt.md` 含 `base_digest` 段，且该段 **≤2048 字节**、含 8 个章节标题。★评审 P1-12 把"无数字"收窄到指标段：**只对 `keyMetrics` 标签段**断言 `re.search(r"\d", 该段) is None`（`asOf` 行与版本号行**豁免**——它们必然含数字）；`quality.prompt.baseReportId/baseVersion` 落库，且 `research_system.md` 的标记为 8（P0-3） |
| **AC-147** | 把一行 `doc_json` 改成 `'{'`：`GET /reports/{id}` → **200** + `integrity=corrupt` + 响应的 `doc` 能通过 `ReportDoc.model_validate`（★P0-1：壳的 `summary` ≥10 字符、`asOf` ≥10 字符、callout 是 `{type,tone,title,text}`）；同一标的 `/reports/latest` 仍 200；`GET /reports` 列表仍 200 且该行 `integrity=corrupt`（SQL `json_valid` 算的）；**`GET /reports/{id}/export?format=html` 仍 200 且带 `Content-Disposition`**；`/reports/{id}/blocks` 与 `/reports/a/diff/b` 也不 500（P0-1 的另外三条读路径） |
| **AC-148** | 置顶一版后连续生成到超过新上限（50）：置顶版仍在 `scope=all`；被裁的版本出现在 `scope=trash`（软删而非硬删）；monkeypatch `_trim_versions` 抛异常时生成仍 `succeeded` 且报告可读 |
| **AC-149** | 记录 `GET /reports/{id}` 的 `doc_bytes` 与 `doc` 的 sha1 → 用 `shutil.rmtree(workspace/"runs"/runId)`（★跨平台；不要在 Windows 上写 `rm -rf`）+ `RUN_RETENTION_DAYS=0` 跑一次 `cleanup_workspaces()` → 再取一次：两个数字**完全相等** |
| **AC-150** | `DELETE /reports/{id}` 后该 id 在 `scope=trash` 且不在 `latest/all`，且该行的 `deletedAt` 非空（P1-13）；`POST /reports/{id}/restore` → 200 且重新出现在 `all`；对未删除的调 restore → 409 `report_not_deleted`；恢复"唯一存活版本"后 `is_latest=1` 且不违反 `ux_reports_latest`。★评审 P2-11：抢指针的前提补一句"该版本**未归档**（`archived = 0`）"——归档版不抢 `is_latest`，否则与 `set_flags` 的既有规则冲突 |
| **AC-151** | 软删后 `/search?q=<该报告里的一个词>` 不再命中它的块；restore 后**同一次请求结束即可**再次命中（同事务重建，不是后台重建） |
| **AC-152** | 有 `status=running` 的 run 时，首页响应含 `active`（≤3 条，字段齐全）；该 run 结束后 30 秒内（或窗口 focus 时）首页列表出现新报告，**无需整页刷新**；`run.done` 之后 `['reports','library']` 与 `['reports',instrumentId]` 都被失效（用 TanStack 的 `queryClient.getQueryState(...).isInvalidated` 断言） |

### 23.7.3 门禁与配额

```text
cd api && python -m ruff check . && python -m pytest -q          # 基线以上 + 新增 ≥32（基线以最近一次全绿输出为准）
cd web && npm run typecheck && npm run lint && npm run vitest -- --run   # 基线以上 + 新增 ≥20
npm run build                                                    # next build 必须绿（首页/库页都是 RSC）
npm run test:e2e                                                 # 无 chromium 时显式 skip + 退出码 0（既有纪律）
python -c "import alembic..."                                    # alembic heads 必须只有 0006
grep -r "create_all" api/app                                     # 必须为空（既有铁律）
grep -r "json.loads" api/app/routers/reports.py                  # 只能命中 0 处（读路径必须走 load_doc_safe）
grep -r "scheduler.*purge\|ticker.*purge" api/app                # 必须为空（P1-14：purge 不挂 ticker）
```

★评审 P2-6：v1 里的 `972` / `260` 是某个时间点的实测值，会随实现漂移（P1-1 就要改 3 个既有用例）。
所以门禁的写法改成"**基线以最近一次全绿输出为准 + 新增下限**"，并且"既有用例被改语义"必须在
§23.7.4 里逐条登记——不然下一次评审分不清"新加的绿"和"改小了的旧断言"。

### 23.7.4 既有用例的语义变更清单（★评审 P1-1：v1 漏了这张表）

本轮有**五处**既有断言（分布在 4 个文件）会因为"把行为改对"而变红。它们不是回归，是**契约变更**，
必须在同一个里程碑里改成新语义，并且**不许顺手改弱**（把 `==` 改成 `⊆`、把 `pytest.raises` 删掉、
把 `assert x is None` 改成 `assert x is not None` 而不写理由，都算拆守卫）。

| 文件:行 | 现在的断言 | 为什么会红 | 改成什么（新语义） |
| --- | --- | --- | --- |
| `api/tests/test_capabilities_api.py:154` | `body["features"] == {…}`（**17 键精确相等**） | 新增 `features.reportLibrary`（18 键） | 补 `"reportLibrary": True` 一行；另加一条夹具：`0006` 未应用时该键为 `False`（证明它来自测量，不是常量） |
| `api/tests/test_report_service.py:181-200` | `session.get(Report, first.id) is None`（"老版本被硬删"）+ `Run.report_id is None` | 裁剪改**软删**（U5） | 拆成两段：①裁剪后行仍在，`deleted_at` 非空、`scope=trash` 能查到，`Run.report_id` **仍指向它**；②`purge_trash()` 跑过一次（`TRASH_RETENTION_DAYS=0`）之后，行才消失、`Run.report_id` 才变 `None`。函数名建议改成 `test_trim_soft_deletes_then_purge_nulls_the_run_link` |
| `api/tests/test_prompt_discipline.py:100` | `int(match.group(1)) == 7`（两个 system 提示词） | `research_system.md` 标记 7 → 8（P0-3） | 改 `== 8`，并保留"两个文件都要有标记"这条（`ask_system.md` 是否跟着 bump 由实现决定，但**两个文件必须一致**，否则 `test_two_prompts_have_different_fingerprints` 之外会多出一个不一致信号） |
| `api/tests/test_prompt_discipline.py:110` | `quality["version"] == 7`，`bytes == len(research_system.md)` | 同上 | `== 8`；`bytes` 那一条**保持原样**——它正是"`quality.prompt` 量的是 system 而不是 task"的证据（P0-3） |
| `web/tests/runErrors.test.ts:38 / :45` | `HTTP_ERROR_ACTIONS ⊇ httpCodes` + 逐行比对 `action` | `httpCodes` 追加 3 条（§23.5.5） | 在 `HTTP_ERROR_ACTIONS` 里补 3 条（`retry`，`primary` 按 §23.5.5 的语义给），**不要**改这条测试的形态 |

**登记纪律**：实现节点在这五处上花的时间，应当记进 findings 并说明"改语义 vs 改断言"的边界；
最终评审节点会拿这张表逐行核对——**表里没有的既有断言，一条都不许改**。

---

## 23.8 风险与缓解

### 23.8.1 风险表

| 编号 | 风险 | 触发信号 | 缓解 |
| --- | --- | --- | --- |
| **R-65** | 迁移在 SQLite 上阻塞写入 | `alembic upgrade` 超过 2 秒 / `SQLITE_BUSY` | 只 `add_column`（nullable，O(1)）+ 3 个 `CREATE INDEX`；索引建在空表上是毫秒级；迁移在 lifespan 里同步执行（既有行为），失败即拒绝启动（既有铁律） |
| **R-66** | 首页从 14 次 fetch 降到 **2** 次，但那 1 次 `/reports` 变慢 | 首屏 `p95 > 200 ms` | 部分索引 + keyset（无 OFFSET）+ `staleTime: 30_000` + `initialDataUpdatedAt` + 骨架屏。★评审 P1-5：**逃生阀不是 `withCounts=0`**——首页要用 `counts.all` 渲染「查看全部 N 份」，关掉计数就没有那个数字了。真正的阀是 `limit` + `staleTime` + 骨架 |
| **R-67** | 去重误判，用户以为"点了没反应" | 用户报告"内容没更新" | 只对**完全一致**的 canonical JSON 去重（保守）；`run.done` 必带 `deduped`；UI 明确说"内容与 v13 一致，未新增版本"；请求可用 `dedupe=false` 关掉 |
| **R-68** | 去重路径吞掉成本（E-127 的复发） | `/cost/summary` 与 `runs` 次数对不上 | AC-145 直接断言账本行；`PersistOutcome.recorded_cost` 让测试能断言"一定记了"；`cost_service.record` 在两条分支上都调用，不在 `create_version` 外层 |
| **R-69** | `baseReportId` 让模型照抄上一版的数字 | `groundedRatio` 明显下降 / 未接地数字增多 | 摘要里**只给章节标题与指标标签，不给数值**（AC-146 只对 `keyMetrics` 标签段做"无数字"正则，`asOf` 行豁免——P1-12）；提示词写明"这是上一版的判断，不是新证据"；`quality.prompt.baseReportId` 留痕，评审可逐份复核 |
| **R-70** | 恢复旧版本破坏 `ux_reports_latest`（部分唯一索引） | `IntegrityError: UNIQUE constraint failed` | 恢复时先查 `is_latest=1` 是否已存在，只在"一个都没有"时才抢占；抢占用 Core UPDATE，与 `create_version` 同形 |
| **R-71** | FTS 与报告状态不一致（恢复后搜不到） | `/search` 命中率下降 | `reindex_report_payload()` 在**同一事务**内、用 `begin_nested()` 包住（与 `create_version` 完全同形）；AC-151 断言"恢复即可搜到" |
| **R-72** | 新增 `GET /reports` 与 `GET /reports/{id}` 冲突 | `GET /reports/abc` 被列表吞掉返回 200 列表 | 路径段数不同（`/reports` vs `/reports/{id}`），Flask/FastAPI 都不会误匹配；额外用例断言 `/reports/abc` 仍是 404 `report_not_found` |
| **R-73** | 裁剪从 20 提到 50 使库膨胀 | `.db` 体积增长 | 50 × 16 KB ≈ 800 KB/标的；`doc_json` 已有 `doc_bytes` 列可监控；`purge_trash` 30 天窗口封顶；上限是配置项（`MAX_VERSIONS_PER_INSTRUMENT`） |
| **R-74** | 前端两个真源（URL 与 zustand）修复不彻底 | 刷新后版本跳回 latest | AC-142 用 `router.replace` 断言唯一写者；`selectedReportId` 的优先级保持既有顺序（`activeReportId ?? initialReportId ?? detail.report.id`），不新增第三个来源 |

### 23.8.2 与既有裁决的对照（不许回退）

| 既有裁决 | 本轮是否触碰 | 说明 |
| --- | --- | --- |
| K-6「`POST /runs` 不存在，生成只有一个入口」 | ⚠️ 触碰 | 仍在 `POST /instruments/{id}/reports` 上追加两个**可选**字段，**没有**新增第二个生成入口 |
| K-8「不让被审对象当审计员」 | ✅ 不碰 | 去重比较的是**我们自己**算的 sha1，不是模型自评 |
| V10-P0-1/P0-2「码表双向一致、`build_argv()` 返回真实 argv」 | ⚠️ 触碰（码表） | 3 条新码进 `httpCodes`、不进 `RUN_ERROR_CODES`；`argv` 一行未改 |
| V11-P0-1「不回填老报告」 | ✅ 遵守 | `doc_sha1` 与 `quality` 都不回填（硬约束 5） |
| §19「只标不改数」 | ✅ 遵守 | 损坏 doc 走"最小壳 + `integrity=corrupt`"，**不改**任何数字 |
| §22 S-4「clamp 只处理 schema 明说的上界」 | ✅ 不碰 | 本轮不动 `clamp_doc` |
| §7.1「生成只有单 worker + 内存订阅」 | ✅ 不碰 | 列表是纯读，无状态 |
| §8.2「run 工作区有保留期」 | ✅ 加强 | 报告与工作区解耦（AC-149），保留期只管目录 |
| §22 S-2「起不来的服务比能看不能生成的服务更糟」 | ✅ 遵守 | `purge_trash` / `_trim_versions` 出异常只记日志，不改任何响应结论（不变量 3） |
| §21 K-6 的优先级链（409 → 429 → 402） | ✅ 遵守 | `baseReportId` 的 422 **排在三个闸门之后**（§23.5.3） |

**v2 新增风险（来自本轮评审，逐条都有"触发信号"）**

| 编号 | 风险 | 触发信号 | 缓解 |
| --- | --- | --- | --- |
| **R-75** | `scope=latest` 与 `scope=all` 的 `archived` 口径**故意不同**，后来的人把它们"对齐" | 某人给 CTE 补上 `archived = 0`，随后"归档最新版"的标的在首页消失 | §23.3.2 写死两套谓词的理由；AC-139 的夹具含"最新版被归档"（P0-2）。真要统一口径，必须同时改 `latest_of()` 与 `/reports/latest`，那是另一个决策 |
| **R-76** | 游标字符串被前端或中间层归一化（`Z` → `+00:00`、去掉秒），翻页**静默丢行** | 全量翻页得到的行数 < `total`，且接口 200 | 服务端三段式正则 + 原值比较（P1-3）；AC-138 之外再加一条：把游标里的 `Z` 改成 `+00:00` ⇒ 必须 **422 `invalid_cursor`**，而不是"返回一个看起来正常的结果" |
| **R-77** | `quality.prompt.baseReportId/baseVersion` 变成永远为 `null` 的空字段（"声明了但没有生产者"第三次） | 抽查 3 份新报告的 `quality_json` 里这两个键恒为 `null`，而用户确实点过"基于此版本更新" | P1-11 的生产者改法 + `test_prompt_quality_keys.py`（键集合 + 真跑一次 `prompt_quality()`） |
| **R-78** | 回收站 30 天后硬清理，**连带删掉追问历史**（`report_asks` CASCADE） | 用户反馈"某份报告的追问记录不见了" | §23.3.8 回收站文案写明 + `purge_trash` 计数日志（`reports/asks` 各多少行）；30 天窗口是唯一缓冲 |
| **R-79** | 列表的 `integrity` 若用 Python 解析，p95 会随 `doc_json` 体积线性上升 | bench 里 p95 随平均 `doc_bytes` 上涨 | SQL `json_valid()`（P1-2）+ `EXPLAIN QUERY PLAN` 断言进 pytest |

---

## 23.9 实施顺序（里程碑 M38–M44）

| 里程碑 | 内容 | 完成判据 |
| --- | --- | --- |
| **M38** | 迁移 `0006`（谓词按 P0-2 修正）+ `doc_sha1` 写入 + `load_doc_safe` + **五条读路径全部改用它**（详情/latest/导出/区间/比较）+ 裁剪 try/except + 三条既有用例的语义更新（P1-1）。回填脚本**不在本里程碑判据里**（P1-16） | AC-147（含导出 200）/ AC-148（后半）/ `alembic heads` = `0006` / `test_migration_0006`（含 upgrade→downgrade→upgrade）绿 |
| **M39** | `services/report_library.py` + `GET /reports` + `schemas/library.py` | AC-137（后端计数）/ AC-138 / AC-139 / AC-140 |
| **M40** | 裁剪改软删 + `restore` + `purge_trash` + FTS 成对 + `TRASH_RETENTION_DAYS` | AC-148（前半）/ AC-150 / AC-151 |
| **M41** | `create_or_reuse_version`（基线=最新非临时版，P1-10）+ `run.done.deduped` + `baseReportId`/`base_digest` + `prompt_quality()` 的两个新键（P1-11）+ `research_system.md` 标记 7→8（P0-3）+ 契约与 `HTTP_ERROR_ACTIONS` 同步 | AC-144 / AC-145 / AC-146 / `test_prompt_discipline` 绿 |
| **M42** | `RecentReports` + `/reports` 页面 + `AppShell` 入口 + `active` 条 + 缓存失效 | AC-137（前端）/ AC-152 |
| **M43** | `?report=` 写入 + `HistoricalBanner` + `ReportPanel` 接线 + 命令面板项 | AC-141 / AC-142 / AC-143 |
| **M44** | 键盘/无障碍/骨架/空态/错误态/减少动画 + `npm run ci` 全绿 | AC 全部 + 门禁全绿 |

顺序理由：M38 先做（所有后续都依赖 `doc_sha1` 与"读不炸"）；M39 纯读、可独立验证；
M40 改的是裁剪（一个写路径，独立）；M41 改的是生成（最贵、最需要单独 review）；
M42/M43/M44 是前端三段，互不阻塞。

每个里程碑的"完成"都包含一件容易被跳过的事：**把该里程碑会碰到的既有用例先改成新语义**
（§23.7.4 的登记表）。否则实现者会在最后一个门禁上同时面对"新功能没写完"和"旧断言在喊",
而最容易发生的错事是**把旧断言改弱**而不是把语义改对。

---

## 23.10 自检（oracle 复核）

### 23.10.1 O-31 的 `file:line` 复核（v2 复核：13 条准确 / 2 条行号偏移已改正 / 1 条计数错误已改正）

> **★v2 复核说明**：v1 自称 15/15。本轮评审把 15 条逐条在本机代码上重打了一遍，结论是
> **13 条完全准确**（下表除带 ★ 的两行外全部命中）、**2 条行号偏移**（`report_service.py:312`
> 实为 `:311`；`FeaturesOut` 的布尔是 17 个不是 15 个）、**2 条内容需补注**
> （`routers/reports.py` 的导出/区间/比较同样裸解析，v1 只点了 `:62/:72`）。
> 已在下表就地改正并标 ★。这个复盘本身也是 O-31 的一部分：**"自称核过"不等于核过**。

| 引用 | 核实内容 |
| --- | --- |
| `web/app/page.tsx:16-39` | `loadRail()`：`watchlist` → `instruments` → 并发 `latestReport`（最多 12） |
| `web/app/page.tsx:25` | `await Promise.all(...)` —— N+1 的位置 |
| `web/app/page.tsx:100` | `href={`/${encodeURIComponent(instrument.id)}`}` —— 不带 `?report=` |
| `web/app/[symbol]/page.tsx:146-154` | 读 `?report=` 并按需 `api.report(reportId)` |
| `web/components/shell/WorkspaceClient.tsx:301-307` | `selectVersion` = `setActiveReport` + invalidate，**不写 URL** |
| `web/components/shell/WorkspaceClient.tsx:240-267` | `syncUrl()`：只改自己那一个参数，`router.replace` |
| `api/app/services/report_service.py:30` | `MAX_VERSIONS_PER_INSTRUMENT = 20` |
| `api/app/services/report_service.py:121-124` | 无条件 `max(version)+1` |
| `api/app/services/report_service.py:190` | `cost_service.record(...)` —— run 成本唯一写入点 |
| `api/app/services/report_service.py:205 / :219` | `session.commit()` 之后调用 `_trim_versions`，无保护 |
| `api/app/services/report_service.py:247` | `session.delete(row)` —— 硬删 |
| `api/app/services/report_service.py:264-293` | `latest_of()`：`is_latest` 优先，回退最新版本 |
| `api/app/models/report.py:60` | `return json.loads(self.doc_json)` —— 裸解析 |
| `api/app/routers/reports.py:62 / :72` | `doc=report.doc` —— 详情路径直接用裸解析结果 |
| `api/app/schemas/capabilities.py:57-94` | ★ `FeaturesOut` **17 个布尔**（`:73-94`），无 `reportLibrary`；同类断言在 `api/tests/test_capabilities_api.py:154` 是**精确相等**（P1-1） |
| `api/app/routers/reports.py:164 / :223 / :280-281` | ★ 导出 / 区间反查 / 版本比较**同样**直接吃 `report.doc`（P0-1 的另外三条读路径） |
| `api/app/services/report_service.py:311` | ★ `order_by(pinned.desc(), version.desc())`（v1 写成 `:312`，`:312` 是 `.limit(...)`） |
| `api/app/agent/prompts/research_system.md:1` | ★ `<!-- version: 7 -->` —— 版本标记的唯一宿主；`prompt_quality()` 读的就是它（P0-3） |

### 23.10.2 逐条自查

| oracle | 结论 |
| --- | --- |
| O-31 | ⚠️→✅ v1 的 15 条经 v2 复核为 **13 准确 + 2 行号偏移 + 1 计数错误**，已全部改正并补 3 行（上表） |
| **O-41** | ✅ 本轮 3 条 P0 + 16 条 P1 全部在正文有落点（§评审记录 A/B 的"修在哪"列可逐条跳转） |
| **O-42** | ✅ 每条新 AC 都能用"夹具 + 断言"写出来；凡涉及既有行为（归档、临时版、损坏壳、导出）都点名了夹具形态 |
| **O-43** | ✅ 新增/变更的每一个同步点都点名了**会红的那条测试**（`test_capabilities_api:154`、`runErrors.test.ts:38`、`test_prompt_discipline:100/:110`、`test_report_service:181-200`） |
| **O-44** | ✅ 索引谓词与 scope 谓词一致（P0-2 的连带修正：`ix_reports_feed` = `deleted_at IS NULL`，一个索引同时服务 latest 与 all） |
| O-32 | ✅ E-119~E-130，其中 E-119~E-125 由 `probe_db.py` 输出，E-126~E-130 是代码层证据（命令见各条） |
| O-33 | ✅ §23.5 的四张表都有"消费者 / 读不到会怎样"列 |
| O-34 | ✅ §23.8.2 八条逐条对照 |
| O-35 | ✅ 3 条新码全是 HTTP 码；`RUN_ERROR_CODES` 仍 21 条 |
| O-36 | ✅ 只有 `0006`，只 `add_column` + `create_index` |
| O-37 | ✅ AC-137~AC-152 每条都写了断言方式（数字/集合/正则/字段） |
| O-38 | ✅ 本轮不做的 7 条（V1–V7）逐条写了理由，不留"以后补" |
| O-39 | ✅ §23.6 每个新增文件都写了"替代/拆分了谁" |
| O-40 | ✅ 数字来源只有 `probe_db.py`（`mode=ro`）+ 代码行号 |

---

## 23.11 评审结论

**结论：有条件通过。**

这份设计的方向是对的，而且是我在这个项目里见过证据最扎实的一份增量设计：它先把
"能反复查看"翻译成五条可反驳的判据（U-0a~U-0e），再用只读探针给出否定结论（E-119~E-130），
最后把"复用"拆成三个不变量而不是三个功能。它的核心判断——**"保存"已经做了一半，
缺的是出口和回程**——在代码里站得住（`ux_reports_latest`、FTS 同事务、成本唯一写入点三条
我逐条核过）。这一轮的问题不在方向，在**三处会把目标做偏的细节**和**十六处会让你在门禁前返工的遗漏**，
所以是"有条件"而不是"通过"。

### 已在本版（v2）修掉的（3 条 P0）

1. **损坏 doc 的最小壳自身不合规**，且导出/区间/比较三条读路径仍是裸解析——不变量 2 按原文无法成立，而
   损坏横幅恰恰让用户去点"导出"。修法：逐字段合规的壳（`summary` ≥10 字、`asOf` ≥10 字、callout 四键）
   + 五条读路径统一走 `load_doc_safe` + 导出对损坏报告返回 200。
2. **`scope=latest` 与 `latest_of()` 在"归档过最新版"的标的上不等**（`set_flags` 会清 `is_latest`），
   而 AC-139 要求逐条相等。修法：`latest` 与 `latest_of()` 同谓词，`archived` 只留在 `all`；
   连带把 `ix_reports_feed` 的谓词改成 `deleted_at IS NULL`，否则 P0-2 的修法会打掉自己的索引。
3. **`base_digest`、版本标记、字节预算三件事都指错了文件**（`quality.prompt` 量的是
   `research_system.md` 的 3822 B，标记只在 `research_system.md:1`；`research_task.md.j2` 没有标记）。
   修法：注入点归 task 模板、标记归 system 文件、预算改成对 `prompt.md` 的实测断言。

### 通过的条件（实现节点必须满足，否则本设计的验收不成立）

1. **先红后绿，红要贴出来。** §23.7.1 的 17 条（v1 的 12 条 + 本轮新增 5 条）按顺序做，
   每条先看它红、把红输出记进实现节点的 findings。P0-1/P0-2/P0-3 的修法都必须有一条
   在修复前**真的失败**的用例——没有红过的绿不算证据。
2. **既有用例的语义变更要逐条登记。** §23.7.4 列的 4 个既有文件（`test_capabilities_api.py`、
   `test_report_service.py`、`test_prompt_discipline.py`、`web/tests/runErrors.test.ts`）
   必须先改语义、再谈"门禁全绿"。**禁止把旧断言改弱**（例如把 `features == {...}` 改成
   `⊆`）——那是把守卫拆掉而不是把语义搬过去。
3. **不新增第二个真源。** `relativeTime` 只有 `format.ts` 一份；`latestReportId` 只有
   §23.3.3 那一条取值规则；去重基线只有 §23.3.4(a) 那一条 SQL；列表计数只有 §23.3.2 那一套构造器。
4. **五条读路径 + 三个 scope 的 `EXPLAIN` 断言必须落地。** 读不炸（P0-1）与"用了哪个索引"
   （O-44）是这次改动的两条"沉默失败"通道，只能靠断言盯。
5. **不新增 run 错误码。** 3 条新码只进 `httpCodes`（`RUN_ERROR_CODES` 仍是 21 条），
   且四处（`errors.py` / `run_error_codes.json` / `HTTP_ERROR_ACTIONS` / `copy.ts`）一次改齐。
6. **`purge_trash` 的副作用写进 UI 文案。** 「永久清理会连同该版本的追问记录一起删除」这句话
   不是可选项——`report_asks` 是 CASCADE，用户有知情权。

### 明确保留的分歧（不影响通过，但要记账）

- **归档语义**：本轮选择"归档最新版后首页仍显示它"（保持 `/reports/latest` 既有行为）。
  这是一个**产品判断**，不是技术必然。如果要改成"归档即从首页消失"，请单独立项——它要动
  `latest_of()`、工作台的默认打开逻辑与 AC-139 三处，不能夹带在本轮里。
- **`MAX_VERSIONS_PER_INSTRUMENT` 20 → 50**：数字本身合理（软删之后回收站不该被"我不认识的旧版"塞满），
  但它把"裁剪"从"经常发生"变成了"几乎不发生"，于是**回收站里的自动清理行会更少、更容易被忽略**。
  这是可接受的取舍，前提是 P2-7 的文案真的写了。
- **`doc_sha1` 不回填**：等于历史上那 15 行永远不参与去重。这是纪律（V11-P0-1）不是缺陷，
  但**用户如果抱怨"明明一样却新增了版本"**，第一件事就是查这一行的 `doc_sha1` 是不是 `NULL`。

**（本文档结束。下一节点：实现节点按 M38→M44 施工，最终评审节点按 §23.7 的 AC 与 §23.8 的风险逐条核对；
本节的六条"条件"同时是实现节点的自查清单。）**

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · v2 → 实施后）

> 记录纪律（来自实现节点的约束）：**不静默偏离**设计。凡是"照着施工图做会卡住"或
> "照着做完但数字对不上"的地方，就地改正实现、并把判断写在这里，让最终评审节点拿这条
> 逐条核。本节只追加，不改上文任何一行的结论。

### D-1 · AC-138 的「翻页过程中插入一份新报告」在秒级时间戳下**不可断言**（已修正用例前提，断言未放松）

**现象**：`api/tests/test_report_library_api.py::test_a_report_inserted_between_pages_does_not_shift_the_window`
在实现完成后仍然红：

```text
E   AssertionError: assert '2a2e8178aae04afbaa861d296c1a20dd' not in (
      ['fd7ba660e08d4b87ac4f766bc4e191b8', 'e366826b44564bfdbca42a74dfd2356d', ...])
E    +  where '2a2e8178aae04afbaa861d296c1a20dd' = <Report 2a2e8178… v16 ready>.id
```

**根因**（不是实现 bug）：`generated_at` 是**秒级** `Text`（§23.0.1 / P1-3 的原话），而这份
夹具的 45 行**全部落在同一秒**（这正是 AC-138 前半段要的 `id` tie-break 场景）。于是
`create_version()` 插入的第 46 行与它们在 `generated_at` 上**完全并列**，排序退化成 `id DESC`；
当它的 id 落在游标（第 20 行）之下时，它出现在**第二页是键集分页的正确行为**——
"新行落在窗口之外"与"窗口不漂移"是两件事，AC-138 只写了后者，却用前者的断言去证。

**修法**（`api/tests/test_report_library_api.py`，断言一个字没改）：加一个
`_shift_generated_at(session, seconds=3600)` 辅助函数，**在取第一页之前**把夹具整体挪到
过去一小时，于是"插入的那一份"严格更新 ⇒ `newest.id not in first_ids + second_ids`
变成一个可被机器反驳的命题。用例的 docstring 里写明了原因与本节编号。

**对设计的建议**：AC-138 的措辞应在下一版补一句"**插入的行其 `generated_at` 必须严格大于
夹具的最大值**"，否则任何人照字面实现都会得到一条时红时绿的用例。

### D-2 · §23.5.1 的 p95 < 30 ms 在 1 万行上不成立（实测 76.5 / 87.4 ms，`counts()` 260 ms）

**证据**（`scripts/library-bench.py`，本机实跑；10000 行 / 200 标的 / 单行 doc ≈ 16 KB，临时库 166 MB）：

```text
临时库：…\library-bench-lrlogbae\bench.db
写入 10000 行：3.0 s（166 MB）
list_page(scope=all, limit=20)  p50  76.53 ms   p95  87.35 ms   max  87.35 ms
counts()                        p50 260.38 ms   p95 278.25 ms   max 278.25 ms
  counts = {'latest': 200, 'all': 9473, 'starred': 261, 'archived': 182, 'trash': 345}

EXPLAIN QUERY PLAN
[all]     SCAN reports USING INDEX ix_reports_feed        ← 三条部分索引都真的走了
[starred] SCAN reports USING INDEX ix_reports_starred
[trash]   SEARCH reports USING ix_reports_trash (deleted_at>?)
```

**结论**：O-44（索引确实被用）成立；**§23.5.1 的 30 ms 这个数不成立**。两条原因都可定位到代码：

1. `report_library._base_statement()` `select(Report, integrity, Instrument)` 取的是**整个 ORM 实体**，
   20 行 × 16 KB 的 `doc_json` 被读进 Python（**只用** `summarize()` 的列与 SQL 算的 `integrity`）。
   这与 P1-2 的精神（"列表不该解析 doc"）一致，但与它的**成本**不一致。
2. `counts()` 的 `latest` 一项要跑 §23.3.2 的窗口函数 CTE（`_latest_ids()`，全表扫描 + 排序），
   而它在**每次**带 `withCounts=1` 的列表请求里都跑一次（首页固定 `withCounts=1`，P1-5）。

**本节点没有静默改实现**，只记录建议（供评审节点裁决，任选其一）：

- (a) 列表路径改 `load_only`（不带 `doc_json` / `quality_json`）——预计把 87 ms 压回 10 ms 量级，
  但 `ReportLibraryItem.report.quality` 是 `ReportSummary` 的字段，需要确认它只来自 `quality_json`
  的一个小键集；这是一次**接口级**的取舍，不该由实现节点顺手做。
- (b) 把 §23.5.1 的指标改成"**@10k 行 p95 ≤ 150 ms**"并把 R-66 的 200 ms 上限写死为可接受值
  （首页只有 2 次 fetch，87 ms 远好于改动前的 14 次串行）。
- (c) `counts()` 加进程内缓存（30 s，与首页 `staleTime` 同窗）——但它是**又一个真源**，
  与"计数与列表同源"（§23.3.2）冲突，需评审节点明确同意。

### D-3 · `scripts/library-bench.py` 的"临时库"隔离在设计里没有写，写错会**静默迁移真库**

`api/app/db/migrations/env.py:19-20` 的 URL 来自 `get_settings().database_url`，**不是** alembic
`Config` 里的那个。第一版实现照 `Config.set_main_option("sqlalchemy.url", url)` 写，结果
`alembic upgrade head` 迁移的是**默认库路径**，创建临时库的那次 `_seed` 直接
`no such table: instruments`。危险之处在于：如果默认库里已经有表，这条命令会**成功**，
而脚本以为自己在压测一个空库。

已改正：显式 `settings.database_url = url` + 一句立即断言（指向失败就 `SystemExit`），
并在 `finally` 里还原。**对设计的建议**：§23.6.1 的 `library-bench.py` 一行里应加半句
"**必须把 `get_settings().database_url` 指向临时库**"，否则每个实现者都要踩一次（或更糟：踩了不知道）。

### D-4 · 文档型交付（`.env.example` / `README.md`）在源码实现阶段被整体漏掉

§23.6.2 的最后两行（`TRASH_RETENTION_DAYS=30` + README 的「报告库（v14）」）在第一轮实现里
**一行都没落地**：`config.trash_retention_days` 有了，`.env.example` 与 README 的变量表里没有——
于是"这个开关存在"这件事只在 `api/app/config.py:159` 的注释里可见。已补：

- `.env.example`：`TRASH_RETENTION_DAYS=30` + 6 行注释（含"硬删会连带删追问记录、并在
  `cost_ledger` 留下孤儿引用"这条 P1-8 的副作用）。
- `README.md`：环境变量表加一行；新增「本期包含（v14）」小节（6 条能力 + 2 条不变量），
  既有各节结论一个字未改。

**对设计的建议**：§23.6.2 / §23.6.4 的表格里，"文档型"与"代码型"交付应分开列，
并在 §23.7.3 的门禁里加一条真正可跑的检查（例如
`grep -q TRASH_RETENTION_DAYS .env.example`），否则这类遗漏只有在人肉对照施工图时才发现。

### D-5 · 门禁的"新增下限"（P2-6）在实现节点上是**无法自证**的

§23.7.3 把绝对数（972 / 260 / ≥28 / ≥18）换成"以最后一次全绿的输出为基线 + 新增下限"，
方向是对的，但实现节点拿不到那条"最后一次全绿"的基线输出（它在前一个节点的会话里）。
本节点的处理：**先把全部既有门禁跑一遍拿到基线，再跑新增用例**，两个数字都写进完成报告；
不声称"新增 ≥32"，只给出"从 N 到 N+k 的差"。**对设计的建议**：把基线数字写进
`plan / current` 的 `priorBaseline` 键（黑板是跨 run 的，正合适），而不是写在文档正文里。

### D-6 · 既有用例的语义变更：§23.7.4 的五处**全部**命中，没有第六处

按 §23.7.4 逐行核对，五处既有断言确如预测那样因"把行为改对"而变红，均已改成新语义
（`== {...}` 补 `reportLibrary`、硬删 → 软删两段、提示词版本 7 → 8 两处、`HTTP_ERROR_ACTIONS`
补 3 条）。**没有发现表外的第六处**——即 §23.7.4 这张表是完整的（这本身是一条正面结论，
写在这里是为了让下一次评审知道它被逐行核过）。

### D-7 · `libraryHref()` 用 symbol，但 `GET /instruments?ids=` **只认 id** ⇒ 报告库每一行都是死链（P0 级，已修）

**现象**（e2e 节点在做 `/reports → 点一行 → 看到那一版` 时撞出来）：
`libraryHref()` 输出 `/{symbol}?report=<id>`（§23.6.3 / AC-143），而
`web/app/[symbol]/page.tsx:24` 拿到那段 path 的第一步是
`api.instrument(decodeURIComponent(symbol))` → `web/lib/api.ts:194` → `GET /instruments?ids=600519.SH`；
后端 `routers/instruments.py:45-58` 只做 `Instrument.id.in_(wanted)` ⇒ `600519.SH` 落进
`missing` → **404 `instrument_not_found`** → `loadWorkspace()` 返回 `null` → `notFound()`。
也就是说：**AC-143 要求的"100% 带 `?report=`"成立，而那个链接打不开**。
这是本轮"结果可复用"最核心的一条（U-0b：点进哪一份就得看到哪一份）被一个未列的端点挡住的典型。

**设计为什么没抓到**：§23.6.3 给 `libraryHref` 的理由是"`app/[symbol]` 两种都认
（`routers/reports.py` 的 `_instrument()` 先按 id 再按 symbol）"——那句话说的是**报告**路由的
解析器，而首屏真正先跑的是**标的**路由的 `list_instruments`，两者不是同一个函数。

**修法**（偏离施工图一处，理由在上）：`list_instruments` 补上同一份宽容——
`or_(Instrument.id.in_(wanted), Instrument.symbol.in_(upper))`，并把返回顺序按 `ids=` 的**请求顺序**
（那本来就是该接口的契约）。这不是新发明的口径：同文件 `_load()`（`:34-41`）的注释原话就是
"Accept a bare symbol too, so links stay readable"，quote/bars 两条路径一直在这么干。

**配套用例**（`api/tests/test_reports_api.py`）：
`test_instruments_batch_lookup_accepts_a_bare_symbol`（`?ids=600519.SH` → 200 且 `id=CN:600519.SH`）与
`test_instruments_batch_lookup_mixes_ids_and_symbols_in_order`（id 与 symbol 混用且顺序不变）。
两条都绿（`pytest tests/test_reports_api.py` 全绿）。

**对设计的建议**：§23.6.1 的变更表应把 `api/app/routers/instruments.py` 列进去（"追加裸 symbol 宽容"），
否则实现节点要么踩坑、要么（更糟）为了让 e2e 变绿去改 **fake**——那正是"用假数据掩盖真缺陷"。

### D-8 · 切版本只改了 URL，**界面停在旧版**（`initialData` + `staleTime: Infinity`，已修）

**现象**（e2e 节点在"切到别的版本再刷新"这条上撞出来，且被我从源头核对）：
`WorkspaceClient.tsx:140-153` 的 `detailQuery` 是
`queryKey: ['report', selectedReportId]` + `initialData: initialDetail` + `staleTime: Infinity`。
`initialData` 在这里**是数据、不是函数**，而 key 里含 `selectedReportId`
⇒ **任何**新 key 首次挂载都会拿到同一份 RSC 首屏 detail；叠加 `staleTime: Infinity`，
新 key 既不缺数据也永不陈旧 ⇒ **永不发请求**。于是"从深链（v1）切到 v2"：URL 写着 `report=v2`，
面板仍是 v1，横幅仍显示"历史版本 v1"。这正是 U-0c 的原话
（"切到 v12，按 F5"之前那一步就已经错），也是 R-74 说的"两个真源"的第三种形态：
**URL 是一个真源，缓存里的首屏数据是另一个**。

**修法**（`web/components/shell/WorkspaceClient.tsx`，本就在 §23.6.3 的变更表里）：

```ts
initialData:
  initialDetail && initialDetail.report.id === selectedReportId ? initialDetail : undefined,
```

只在"这份 detail 真的属于当前 id"时才把它当首屏数据。`staleTime: Infinity` 保持不动
（它是对的：同一版重开不该重取），被修掉的是"用 A 的首屏数据冒充 B"。

**先红后绿（真的红过）**：`web/tests/workspace-url.test.tsx` 新增第 4 条用例
「选旧版本后面板真的去取那一版（不是继承首屏数据）」，断言 ① 真的发了一次 `/reports/<旧 id>`
② 面板出现旧版那句 headline ③ 出现「正在查看历史版本 v2」。把修复**临时改回**原样再跑，
该用例**红**（`1 failed | 3 skipped`，`rc=1`），恢复后绿（`4 passed`）——
红绿都是我本机实跑，不是推断（脚本：`.agentmesh/repro/prove_d8_red.py`）。

**对设计的建议**：AC-141/AC-142 只测了"URL 里有什么"，没测"面板里显示什么"。
下一版应把 AC-141 补一句："**切版本后面板的 `detail.report.id` 等于 URL 的 `report`**"
——否则"URL 正确 + 内容错误"这种组合会一路绿到底。

### D-9 · 既有 e2e 里有 4 条**与本次改动无关**的红（已用 HEAD 基线实测证明，未修）

**先说结论**：这 4 条在本轮改动**之前**就是红的。证明方式不是"看 git status 没改它"，
而是**把整个工作树 stash 掉、在干净的 HEAD 上跑同一组用例**：

```text
# 工作树全量备份（.agentmesh/repro/worktree_backup.py backup …v14.zip，1496 个文件 / 8.3 MB）
python .agentmesh/repro/baseline_stash.py stash          # git stash push --include-untracked
cmd /c "set NODE_ENV= & npx playwright test tests/e2e/ask.spec.ts tests/e2e/grounding.spec.ts --reporter=line"
  → 4 failed, 3 passed (2.3m)
python .agentmesh/repro/baseline_stash.py pop            # 再按字节还原（校验 0 处差异）
```

同一组用例在**本轮工作树**上：

```text
cmd /c "set NODE_ENV= & npx playwright test --reporter=line"
  → 17 passed, 1 skipped, 4 failed (3.3m)
  失败的正是同样 4 条：
    ask.spec.ts:32   cite 追问给出命中块，Esc 收起并把焦点还给触发按钮
    grounding.spec.ts:59   展开徽标列出未接地样本，点击后定位到该块
    grounding.spec.ts:110  框选 K 线区间后，报告里出现区间联动条
    grounding.spec.ts:147  命令面板能打开成本面板
```

**新增的 `library.spec.ts` 4 条全绿**（AC-143 href 形状 / U-0b 点旧版开旧版 / U-0c 刷新后仍是那一版 /
游标翻页到底），`workspace.spec.ts` 的 8 条也全绿 ⇒ **本轮没有让任何一条既有 e2e 从绿变红**。

**为什么不修这 4 条**：`grounding.spec.ts` 那三条与接地率/区间联动的夹具口径有关
（例如 `factClaims 12 / factMissing 1` → 11/12 = 0.917 ≥ `GROUNDED_THRESHOLD 0.9`，
于是徽标显示「已核验」而断言期待"1 个数字未找到依据"），`ask.spec.ts` 那条与追问抽屉的焦点归还有关
——这两条都是**别的层**的既有账，修它们要动接地率阈值/夹具或追问面板的焦点管理，超出
本设计的范围（约束：不引入设计之外的改动）。写在这里是为了让最终评审节点不要把这几条红算到本轮头上，
也为了让下一个节点知道：**跑全量 e2e 时，"4 failed" 是本项目的当前基线，不是回归**。

---

## 评审记录（Iteration 7 代码评审 · 最终评审节点 · 6 条缺陷已就地修复）

**评审方法与边界**：按 §23.6 的变更表逐行核对落地情况（57 行、缺 0；唯一没动的是
`api/tests/test_contract_codes.py`，见 R-3），再按「回归风险 / 命名与风格 / 注释是否与既有代码同形」
三面读 `git diff`（37 个修改文件 + 21 个新增文件）。**不引用实现节点的自述**：下面每一条都自己跑过、
自己看过输出。边界照旧——不引入设计之外的改动，不修本轮之前就红的账（见 D-9）。

**门禁（本机实跑，评审后）**：`ruff check .` 全清；`pytest` **1067 收集 / 1063 passed / 4 skipped /
0 failed**（rc=0）；`npm run typecheck` + `npm run lint` 全清；`vitest` **30 files / 325 passed / 1 skipped**；
`next build` rc=0 且产出 `ƒ /reports 6.64 kB / 145 kB`；`alembic heads` 只有 `0006_report_library`；
`grep -r create_all api/app` 为空；`tests/e2e/library.spec.ts` **4/4 绿**（改动后实跑 53.8s）。

| 编号 | 维度 | 结论与证据（本机实跑） | 修在哪 |
| --- | --- | --- | --- |
| **R-1** | **健壮性（P1，会 500）** | `GET /reports?q=` 在库里有**一条**坏 `doc_json` 时整页 500。根因：`_headline_expr()` 的 `json_extract()` 在语法坏掉的行上抛 `OperationalError: malformed JSON`（本机 SQLite **3.41.2** 实测，**不是**返回 NULL），异常直冒到路由层，而 `total_for()` 用的是同一个谓词，一起炸。AC-147 只断言了**不带 `q`** 的列表，所以它是绿的 —— 一条坏数据让"搜索报告"整页崩，而这恰好是本轮 U4「坏数据不炸」的另一半。修法：`CASE WHEN json_valid(doc_json) = 1 THEN json_extract(...) ELSE '' END`（同一实测：`CASE` 短路，坏行取空串）。**先红后绿**：把修复临时改回原样 → 新用例红（`sqlalchemy.exc.OperationalError: malformed JSON`，栈顶 `routers/reports.py:80 → report_library.py:257`），恢复后绿 | `api/app/services/report_library.py::_headline_expr`；新用例 `api/tests/test_report_integrity.py::test_searching_the_library_with_a_corrupt_row_is_not_500`（关键词刻意选**只在 headline 里**出现的词，否则 `OR` 会先命中 title 而短路，用例又变成"绿得没说服力"） |
| **R-2** | **完整性（P2，能力不存在）** | 完成报告写"命令面板入口 + 最近 5 份报告"已落地，实际 `CommandPalette` 新增的 `onOpenLibrary` / `recentReports` **没有任何调用方**（`git grep` 只命中它自己）：`tsc` 不红（prop 可选）、页面不报错，只是这个能力根本不存在 —— 空 prop 是最难发现的一种未实现。而且 `recentItems.run` 原来调的是 `onSelectInstrument(report.symbol)`，等于"点 v12 打开最新版"，正是 U-0b 的原话。修法：新增 `onOpenReport(symbol, reportId)`；`AppShell` 转发并按 `features.reportLibrary` 门控；`WorkspaceClient` **只在面板打开时**才取最近 5 份（`enabled`，不给工作台首屏加请求）；`LandingSearch` 也接上（首页广告了 ⌘K，入口就得真能打开） | `web/components/shell/{CommandPalette,AppShell,WorkspaceClient,LandingSearch}.tsx`；新用例 `web/tests/command-palette-library.test.tsx`（5 条：入口在/不在、点开的是**那一版**、无消费者时不出死项、上限 5 条） |
| **R-3** | **一致性（P2，契约少一道闸）** | §23.6.4 把 `api/tests/test_contract_codes.py` 列为**修改**，实际一个字节没动：`httpCodes` 的 `action` 字段只有 TS 侧逐行比对，服务端不校验；`action` 缺失或拼错时后端全绿、前端回落到 `openLog`（V11-P0-4 同型事故）。修法：加一条"每一行都带 `action`、且在 `RunErrorAction` 词汇表里"的断言（词汇表从 `runErrors.ts` 的联合类型读取，两边同源），并钉住 v14 三条新码的状态码与 `retry` 动作 | `api/tests/test_contract_codes.py::test_every_code_row_carries_an_action_from_the_shared_vocabulary` |
| **R-4** | **注释与代码不一致（P2，会误导下一个人）** | `web/tests/e2e/library.spec.ts` 的文档块仍写着"从深链用切换器换版，面板仍显示原文档……缺陷另行上报"，并引用旧行号 —— 而 D-8 已经把这个缺陷**修掉**了（`initialData` 加 id 守卫，`web/tests/workspace-url.test.tsx` 第 4 条守着）。一条"说明缺陷仍然存在"的注释比没有注释更糟：它会让下一个人跳过一段**已经被覆盖**的路径，或者反过来去"修"已经正确的代码 | `web/tests/e2e/library.spec.ts` 顶部注释改为「e2e 只钉 URL 契约，切换版本由 `workspace-url.test.tsx` 守」，并点名 D-8 |
| **R-5** | **夹具失真（P2，假绿风险）** | `fake-api.mjs` 的 `libraryCursor()` 先 `encodeURIComponent()`，而真服务端的 `encode_cursor()` 返回**列文本原样**；于是这份 fake 只在"再解码一次"的情况下才认得出游标。这种不保真会让"前端多编一次码"（真服务端会回 422 `invalid_cursor`）在 e2e 里保持绿色。修法：与真服务端同形（原样返回），进来时仍容错解码（`%7C` 与 `|` 都认）。`library.spec.ts` 4/4 仍绿 | `web/tests/e2e/fixtures/fake-api.mjs::libraryCursor` |
| **R-6** | **风格（P3，行尾）** | 三个 `.tsx` 的工作树是 **CRLF**，而 `.gitattributes` 是 `* text=auto eol=lf`（注释原话 "LF in the working tree"）⇒ 每次 `git diff` 都吐 `warning: CRLF will be replaced by LF`。评审期把这三个文件归一成 LF；索引里本来就是 LF，所以**提交内容零变化**（`git diff --stat` 前后一致：33 / 42 / 74 行） | `web/components/shell/{CommandPalette,AppShell,WorkspaceClient}.tsx` 的行尾 |

**逐项核对过、确认没有问题的三处**（写在这里是为了让下一次评审不必重核）：

1. `scope=latest` 与 `latest_of()` 的四类夹具（正常多版本 / 只有临时版 / 最新版被归档 / 只有归档版本）
   逐条相等，`archived` 只留在 `all` / `archived` 两个 scope 里（D-2 的口径没错）。
2. `_persist()` 的去重分支**照记成本**且把 run 指向既有版（`recorded_cost=True` 是字段而不是注释），
   旧入口 `create_version()` 的签名与 28 处调用点一个字没改。
3. `purge_trash()` 是 `api/app` 里**唯一**的 `session.delete(Report)`
   （`test_the_only_hard_delete_entry_point_is_purge_trash` 用源码扫描守着），`runs.report_id` 的
   SET NULL 与 `report_asks` 的 CASCADE 都在同一事务里。
