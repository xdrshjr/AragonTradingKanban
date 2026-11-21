# 26. Iteration 9 设计：数据源完整性与研报可决策性（Z1–Z8）

> **文档版本：v2**（Iteration 9 设计评审后修订；v1 = 设计节点初稿）。
> v1 → v2 的每一处改动都由「评审记录」驱动，逐条给出落点；结论见文末「评审结论」。

> 本轮针对的是**生产环境上真实发生的事**：用户在 `aragontrading.online` 上真实生成研报，
> 发现报告里有些数据「对接不上 / 查不到」，拿去决策用不了；看生成日志又看到数据源对接报错。
>
> 本节点只产出设计。§26.0 的每一条都是本轮在生产机上**实测**得到的，不是推测；
> 每条都给了可复跑的脚本（`docs/plans/data-source-integrity/evidence/`）与 `path:line`。

---

## 评审记录

> 评审对象：本文件 **v1**（§26，926 行 / 9.1k 汉字）。
> 评审维度：**可行性**（能否用现有技术栈真的做出来）/ **完整性**（边界、错误路径、上线与回滚）/
> **一致性**（与 `CLAUDE.md` 三条不变量、既有诊断结论、代码里已存在的纪律是否冲突）/
> **范围适当性**（是否过度设计或欠定义）。
> 严重度：**P0** = 照此实施会做出错误行为、或根本实施不了；**P1** = 会留下缺口、与既有纪律冲突、
> 或验收判据本身不成立；**P2** = 表述、计数、可复现性瑕疵。
>
> 共 **25 条：4 P0 / 11 P1 / 10 P2**。**P0 与 P1 全部已在正文就地修正**（每条给落点）；
> P2 除 P2-9（只标「仅说明」）外也已修正。修正后版本标记为 **v2**，结论见文末。

### R-0 评审时逐条核对过的代码事实（不是转述 v1 的结论）

| v1 的断言 | 实测（本仓库 `02260d0`） | 结论 |
| --- | --- | --- |
| `snapshot.py:194` 是 `"stale": bool(warnings)` | 逐字一致 | 准确 |
| `akshare_provider.py:80-88` 旧接口名 + `except Exception → network` | 逐字一致 | 准确 |
| `registry.py:96-97` 把失败消息截到 120 字 | 逐字一致 | 准确 |
| `registry.py:59-67` `available_providers()` 只回 `{id, available}` | 一致 | 准确 |
| `base.py:16-31` 四个 kind，`retryable = {network, ratelimit}` | 一致 | 准确（v1 的「扩为」措辞有误，见 P2-1） |
| `base.py:87-109` 偏移注释 + 只在周/月重采样里用偏移 | 一致 | 准确 |
| `snapshot.py:272-303` `_derive_valuation`，`pe = None` 是死分支 | 一致 | 准确；但返回 **7** 个键，不是 8（P2-2） |
| `snapshot.py:306-334` `_derive_peers` 直接 `SyntheticProvider()` | 一致 | 准确 |
| `snapshot.py:44-46` `SnapshotBundle.stale` 靠告警文本里有没有「陈旧」判 | 一致 | 与本轮的新语义冲突（P2-3） |
| `instruments.py:186` `stale = last_bar_date < latest_session_due()` | 一致 | 与 Z4 的「丢弃当日半成品行」直接打架（**P0-4**） |
| `cache.py:244-279`（`:272` 裸 UTC 日期）判缓存新鲜 | 一致 | 准确 |
| `report_validator.py:713-714` 有一条显式分支 `elif mock: status = "ready"` | 一致 | v1 的「合成即降级」会踩它（**P0-1**） |
| `web/components/report/ReportPanel.tsx:391-417` 是现有徽标宿主 | 存在 | 新徽标应放这里 |
| `capabilities.py:210-215` 处理器是**同步 `def`**，匿名身份走 `_redact` | 一致 | 三处要改（**P0-3** / P2-8） |
| `capabilities.py:191-207` `_redact` 是「**同形状**脱敏」，不动 `dataProviders` | 一致 | 新键会原样漏给匿名（**P0-3**） |
| `schemas/report.py:373-393` `ReportQuality` 现有 10 个键；`test_contract_codes.py:123` 反射守卫存在 | 一致 | 准确（追加键必须同步声明） |
| `numeric_claims.py:52-58` 对 `RULES_VERSION` 的自我说明：**口径一改就必须换版本号** | 一致 | v1 的「不动 claims-v4」与它矛盾（**P1-5**） |
| `web/components/report/ReportMeta.tsx` | **不存在** | 实际文件是 `web/components/shell/ReportMeta.tsx`，而且它是 `return null` 的**占位**（**P1-6**） |
| `web/lib/types.ts:836` 的 `dataProviders` 是**内联**字面量类型 | 一致 | 镜像点要写清楚（P2-9） |
| 门禁基线「pytest 2232 passed / 10 skipped」 | `pytest tests --collect-only` = **2277** | 2232 是上一版（M53–M58）的 passed 数（**P1-7**） |

### R-1 P0（4 条，全部已修）

| ID | 维度 | 问题 | 落点 |
| --- | --- | --- | --- |
| **P0-1** | 一致性 | **「合成数据 ⇒ `status = degraded`」会踩掉既有的 `elif mock: status = "ready"` 分支**。`validate_doc()` 里 `mock` 是**显式**分支，位置在接地率判定之前；示例/演示模式（`features.mock` 默认 `true`、`agentMode=mock`）的每一份报告都由规则从合成快照装配，按 v1 的规则会**全部**变成 `degraded`，而前端 `ReportPanel.tsx:392` 把 `status === 'degraded'` 直接渲染成黄色「降级」徽标 —— 演示态的每次生成都会挂一个假警报，`degraded` 这个词也就此贬值。 | §26.3.8(c)、AC-285 |
| **P0-2** | 可行性 | **`canonical_session_ts` 的 US 规则自相矛盾**：docstring 写「US 保持 00:00 UTC」，紧接着的括注写「现有缓存行是 05:00 UTC 的 ET 午夜，落在同一天，不必迁移」。两句话给出两个不同的规范戳，实施者无论选哪个都会破一处：改成 00:00 UTC ⇒ 库里既有的 05:00 UTC 行不再规范，26.3.5(b) 的写侧校验会把它们当坏行拒写；保持 05:00 UTC ⇒ docstring 与 AC-253 的表驱动用例对不上，函数也不再幂等。 | §26.3.1、AC-253 |
| **P0-3** | 一致性 | **`/capabilities` 的新键会漏给匿名用户**。该路由用 `deps.get_principal`（不是 `enforce`），匿名请求走 `_redact`；而 `_redact` 的契约是「**同形状**脱敏」，它只清 `aragon/engine/runtime/limits`，**一个字都不动 `dataProviders`**。把 `domains`（每个上游域 ok/失败码/延迟）、`breaker`（哪个源被限流、到几点）、`note`（「push2 当前被限流」）挂上去，等于把运维面公开。这与 §26.4.1 自己写的「health 公开路由不放运维信息」是同一条纪律，只是 v1 漏在了 capabilities 上。 | §26.3.8(a)、§26.5.1、AC-282 |
| **P0-4** | 完整性 | **Z4 与「`stale` 语义收窄」互相拆台**：`sanitize_bars` 会丢掉「当日未收完」的那一行，于是盘中最后一根完整 bar 是**上一个已收盘会话**；而 `instruments.py:186` 的判据是 `last < latest_session_due()`，`latest_session_due()` 在盘中返回**今天** ⇒ 每个交易日盘中，CN 标的的 `meta.stale` 恒为 `true`，`snapshot.dataQuality.stale` 亦然，`run_manager` 再把这条翻译成「资料自检：数据存在陈旧警告」。**这正是 E12 要消灭的那类假话**（用一个能算的日期去断言一件不成立的事），v1 只修了跨源那一半，漏了丢行这一半。 | §26.3.5(d)、AC-273、AC-293 |

### R-2 P1（11 条，全部已修）

| ID | 维度 | 问题 | 落点 |
| --- | --- | --- | --- |
| **P1-1** | 完整性 | **存量清理是一次「记得跑」的运维动作，不是自愈**。v1 只给了手工 `normalize-bars`；一旦某台实例（或下次部署）忘了跑，`write_bars` 的下发只按 **ts** upsert，旧口径行（16:00 UTC）与新口径行（07:00 UTC）**同时留在库里**，同一交易日两根 bar 一起进 `bars.csv` 与 MA 计算 —— 比现在的坏行更糟。上线路径必须能在**第一次刷新时自己收敛**。 | §26.3.5(b)(c)、AC-294 |
| **P1-2** | 完整性 | **`normalize-bars` 只管 CN/HK 的戳，不管 US 的坏行**，于是 AC-270「生产库 OHLC 不自洽行数 == 0」在 US 侧不可达：E5 的半成品行是 yfinance 的通病，`NVDA` 一样会有（`close=NaN` 或 `open=前收、high<open`），而 US 行按 v1 的规则**一根都不删**。判断「坏行」与判断「戳口径」是两件事，清理命令必须两件事都做。 | §26.3.5(c)、AC-270 |
| **P1-3** | 完整性 | **AC-255 与 Z4 矛盾**：Z4 明确要丢「当日未收完」那一行，AC-255 却要求「`bars.csv` 尾行日期 == **当日会话**」。盘中生成时这两条不可能同时成立，验收会变成「谁后改谁对」。 | AC-255、§26.3.9(a) |
| **P1-4** | 完整性 | **12 期取样与 8 期展示的落地形状没钉住**。v1 说「取样 12 期、`snapshot` 只用前 8 期」，却没说不许把 12 期写进落盘文件。而 `write_fundamentals_md()` 渲染的是**传进来的整张表**：若 `bundle.fundamentals` 是 12 期，`fundamentals.md` 就有 12 行真数字，模型会引用第 9–12 期，而接地判定只读 `snapshot` ⇒ 这些**真实存在的数字**被判「未接地」。这是本轮最不该新增的噪声。 | §26.3.3(c) 第 6 条、AC-265 |
| **P1-5** | 一致性 | **`RULES_VERSION` 必须换，不能继续 `claims-v4`**。`derived_values()` 新增 3 条合法推导 = **判据本身变了**（原来判 missing 的写法，现在能对上派生量）。`numeric_claims.py:52-58` 的原文就是为这一刻写的：「判词是**永久落库**的，而判据会变…… 跨口径只能各自陈述，不能比较」。若版本号不动，库里 0.799 的旧报告与 0.85 的新报告会顶着**同一个** `claims-v4` 被并排比较，AC-280「不得下降」也就失去意义。 | §26.3.7(b)、AC-281 |
| **P1-6** | 可行性 | **前端改动的落点写错了文件**：`web/components/report/ReportMeta.tsx` 不存在；`web/components/shell/ReportMeta.tsx` 里只有一个 `return null`，且文件头写明「本文件现在**不写任何东西**」。往占位组件里塞徽标，既违反它自己的说明，也让「报告质量徽标」这一族分裂到两个目录。 | §26.4.2、§26.7.1 |
| **P1-7** | 一致性 | **门禁门槛低于现状 45 条**：v1 的基线（2232 passed / 10 skipped）取自 M53–M58 那次 finding，之后 `02260d0` 又加了一版；本机实测 `--collect-only` = **2277**。`CLAUDE.md` 的纪律是「`--collect-only` 的数量不得低于改动前」——拿 2232 当门槛等于**允许删掉 45 条用例**。 | §26.0 A、AC-289 |
| **P1-8** | 可行性 | **可选域会被 `_try_chain` 判成「全链失败」**。`_try_chain` 把 `None`/空列表当作一次失败，一路走完就 `raise ProviderError("所有数据源都失败了: …")`。而 `peers` / `valuation` 的「没有」是**合法答案**（v1 自己也说 `peers=None` + issue）。不改的话，每个没有同业报价的标的都会在 `warnings` 里多一句吓人的「所有数据源都失败了」，而这句话会被原样塞进提示词。 | §26.3.2(b)、§26.3.6(e) |
| **P1-9** | 范围 | **Z1 把「必须」和「想要」捆在一个里程碑里**。改名的根因只需「错误分类学 + 能力矩阵 + 断路器 + 不截断」四件事；**单飞**与**钉源**是限流优化，与 E1–E14 任何一条的修复都不构成依赖，却各自引入一份新的进程内状态（锁、TTL、解钉规则），还要在 `reset_registry()` 里联动清理。把它们放进 M59 的验收，会让「A 股日线能不能拿到」这件事被两个优化拖住。 | §26.3.2(d)(e)、§26.8、AC-260 |
| **P1-10** | 可行性 | **`?deep=1` 的探测写在同步处理器里跑不起来**。`/capabilities` 是 `def`（FastAPI 放进线程池），v1 却要求「全在 `asyncio.to_thread` 里带 5s 超时」——线程池里没有 running loop，`asyncio.to_thread` 直接 `RuntimeError`。 | §26.3.8(a)、§26.5.1 |
| **P1-11** | 完整性 | **synthetic 的降级没有同时定义「谁负责说」**。`quality.dataQuality` 的生产者、`status` 的判定、前端横幅三处若各判一次，就会出现「徽标说合成、`status` 说 ready」这类自相矛盾（本项目历史上已发生过三次同类：`cost`/`prompt` 两键丢了两个版本）。必须钉成**一个生产者 + 一处判定**。 | §26.3.8(b)(c)、AC-284、AC-285 |

### R-3 P2（10 条）

| ID | 问题 | 处置 |
| --- | --- | --- |
| P2-1 | 「`retryable` **扩为** `{network, ratelimit}`」：这两个 kind 本来就在里面，本轮真正变的是 `api_drift` **不在**该集合 | 已改措辞（§26.3.2(a)） |
| P2-2 | 「`_derive_valuation` 输出字段保持现有 **8** 个键」：实测是 **7** 个（`peTtm/pePercentile3y/pb/dividendYield/marketCap/netMargin/proxy`） | 已改为 7 + 追加键清单（§26.3.6(d)） |
| P2-3 | `SnapshotBundle.stale`（`snapshot.py:44-46`）用「告警文本里有没有『陈旧』」判定 —— 本轮收回 `stale` 语义后它是一个**会撒谎的第二定义**，且全仓无人使用 | 已要求删除（§26.3.6(b)） |
| P2-4 | `issuesTruncated` 只在 §26.3.7(c) 出现过一次，没进 §26.3.6(b) 的形状，也没算进「追加 7 个键」 | 已并入形状与计数（§26.3.6(b)、§26.6.2） |
| P2-5 | `_derive_valuation` 的新签名把 `closes` 丢了，而 `percentile_rank(closes[-750:], pe)` 还要用它 | 已补回签名（§26.3.6(d)） |
| P2-6 | `sanitize_bars` 的 OHLC 断言没给容差；yfinance 的浮点四价在 1e-9 量级上会自伤 | 已给 ε 规则（§26.3.5(a)） |
| P2-7 | AC-252 的正则只抓 `fromtimestamp(...).strftime("%Y-%m-%d")`，抓不到 `.date().isoformat()` 这类等价写法（`yfinance_provider.py:78` 就是），「唯一真源」守不住 | 已改为「枚举允许模块 + 两种写法都禁」（AC-252） |
| P2-8 | 前端 `dataProviders` 是内联字面量类型（`web/lib/types.ts:836`），不是命名接口；v1 说「镜像 `DataProviderCapability.domains`」会让人去找一个不存在的类型 | 已写明落点（§26.4.2） |
| P2-9 | 三份既有诊断的引用只到 `§6` / `§6.1` / `§6.3`，没给标题行或行号，复跑者要翻 9 万字 | **仅说明**：实施时按标题搜索即可（`## 6` / `### 6.1` / `### 6.3`），不改正文 |
| P2-10 | §26.4.2 的标题写「共 4 个文件 + 1 个类型镜像」，表里却是 5 行（含 `copy.ts`） | 已改计数（§26.4.2） |

### R-4 评审没有推翻的部分（明确记录，避免下游顺手重做）

- §26.0 的 14 条根因**全部成立**，且每条都有 `path:line` 或可复跑脚本；本轮的工作量估算合理。
- 「会话日期是唯一真源、`stored ≠ displayed`」的地基选型正确：改前端只修好一个面板，改服务端才修好事实底座。
- 「零 DDL + `normalize-bars` 只删不建」的方向正确（`bars` 表已够用，写侧真源在 `write_bars`）。
- 「不用价格分位冒充 PE 分位」「不放宽 `read_bars` 的混源拒绝」「不改合成 walk 的日历锚点」三条克制，必须保留。
- 与三份既有诊断的继承关系（尤其「不改」清单）清楚，**不重复劳动**这一点做到了。

---

## 26.0 已核实事实（本轮实测取证）

### A. 环境与仓库基线（不改）

| 项 | 值 | 取证方式 |
| --- | --- | --- |
| 生产机 | `root@47.82.166.177`，`/opt/aragon-trading`，`aragon-trading-api.service` active | `systemctl is-active aragon-trading-api` → `active` |
| 生产 venv | `akshare 1.20.65` · `yfinance 1.8.0` · `Python 3.12.3` | `/opt/aragon-trading/api/.venv/bin/python -c "..."` |
| 仓库 master | `02260d0`（Iteration 8 收口） | `git log --oneline -1` |
| 门禁基线（改动前，必须不低于） | pytest **`--collect-only` = 2277**（★v2 评审实测，仓库 `02260d0`）；vitest **669 passed / 1 skipped**；首屏 JS 209.0kB ≤ 245kB | pytest：`cd api && python -m pytest tests --collect-only` 的逐文件计数求和；vitest/首屏：黑板 F-IMPL-Y。**★v2（P1-7）**：v1 抄的 2232 是 M53–M58 当时的 *passed* 数，之后 `02260d0` 又加了一版用例；拿它当门槛等于允许删掉 45 条。**门槛一律取「实施节点开工时实测的 `--collect-only` 数」**，写进 M59 的第一条 AC |
| 架构不变量 | API 单 worker；schema 真源只有 Alembic；依赖方向后端 → 前端 | `CLAUDE.md:22-41` |

### B. 生产实测：报告「数据对不上」的 11 条根因

每条都标注了**观察到的现象**、**代码位置**与**量级**。

| # | 事实 | 证据 |
| --- | --- | --- |
| **E1** | **akshare 的日线接口名已漂移**：`ak.stock_zh_a_hist` 在 1.20.46 被改名为 `stock_zh_a_hist_em`。项目仍调旧名，`AttributeError` 被 `except Exception` 归成 `network`，链表直接落到 yfinance。生产日志原文（被截断在 120 字）：`akshare: network akshare: stock_zh_a_hist 已在 1.20.46 更名为 stock_zh_a_hist_em，请改用 ak.stock_zh_a_hist_em；参数与返回的列也可能有调整，用 ak.interface_info(` | `akshare_provider.py:80-88`；`registry.py:96-97`；生产 run `170db15a…` 的 `run.log` 事件 seq 1857 |
| **E2** | **同类漂移还有第二处**：`stock_zh_a_daily` 也已改名 `stock_zh_a_hist_sina`（实测报错原文同形）。也就是说 CN 的 sina 兜底路径也是断的 | `ak_endpoints_probe.py` → `sina_daily_300750: AttributeError('stock_zh_a_daily 已在 1.20.46 更名…')` |
| **E3** | **东方财富 push2 端点在生产 IP 上被限流**（`Push2RateLimitError`，原文自述「首次约 5 分钟、反复触发 40 分钟以上」）。实测同时失败的：`stock_zh_a_hist_em`、`stock_individual_info_em`、`stock_board_industry_name_em`、`stock_board_industry_cons_em`。**同一时刻可用的**：`stock_zh_a_hist_tx`（腾讯，213 行）、`stock_financial_abstract`（80 行指标）、`stock_financial_analysis_indicator` | `ak_endpoints_probe.py` 全部 9 个端点的一次性实测 |
| **E4** | **CN 日线的日期整体错一天**。yfinance 的日线索引是 «交易所本地 00:00 +08:00»，落到 UTC 是**前一天 16:00**；而项目所有日期渲染都用 UTC。实测同一条序列：缓存里的 `2026-11-19`（ts=1795104000，UTC 16:00）= yfinance 的**会话 2026-11-20**；`2026-11-17` 那根 = 会话 `2026-11-18`（open 238.24 / high 242.66 / close 241.01 逐值对上） | `yf_rows_probe` 的 index/tz/utc 三列；`bars.csv` 尾行；`base.py:87-109` 的注释已承认这个偏移，但只在**周/月重采样**里加了偏移，日线没加 |
| **E5** | **yfinance 的「当日未收完」行会被当成完整 K 线入库**。同一时刻同一标的，`auto_adjust=False` 给出 `open=240.0（=前收）、high=231.99、low=225.19、close=NaN`；`auto_adjust=True` 给出**全 NaN + volume 有值**。项目把它逐字段 `float()` 后写入缓存 | `yf_rows_probe` 2026-11-20 行；`yfinance_provider.py:86-99` |
| **E6** | **缓存里已存在 3 根 OHLC 不自洽的 bar**（生产库实测）：`001232.SZ` open 176.34 / high 161.20；`300750.SZ` open 240.00 / high 231.99；`688018.SH` 2024-03-29 close 33.34 > high 33.15。而 `indicators.py` 全部函数**没有 NaN/异常值守卫**，一根坏 bar 会污染 MA/MACD/RSI/ATR 的全部后续值 | `db_probe3.py` 的「OHLC-invalid cached bars」；`indicators.py:21-223` |
| **E7** | **实测报告真的被这根坏 bar 坑了**：`001232.SZ` 那份研报的风险清单写着「11-19 那根 K 线的最高价 161.20 元还低于开盘价 176.34 元，属于明显的 OHLC 异常……这意味着所有技术位都需重算」——模型自己做了一次数据质检，而系统没做 | 生产报告 `3f968c2c…` 的 `risks` 块 + `quality.grounding.samples` |
| **E8** | **估值永远是 null，是代码写死的**：`_derive_valuation()` 里 `pe = None`，随后 `if pe is not None:` 是**死分支**；`pb / dividendYield / marketCap` 直接 `None`。于是研报只能写「PE(TTM) 缺失(proxy)」「peTtm/pb/marketCap 全为 null……无法用倍数判断便宜」 | `snapshot.py:272-303`；生产报告 `75ba2722…` 的 `keyMetrics[5]` 与 `summary` |
| **E9** | **估值其实拿得到**：yfinance `.info` 对 `300750.SZ` 返回 `trailingPE 11.83 / priceToBook 2.71 / marketCap 1.053994909696e12 / dividendYield 0.6 / trailingEps 19.25 / sector Industrials / industry "Electrical Equipment & Parts"`；akshare 的 `stock_financial_abstract` 里也有 `基本每股收益 / 每股净资产 / 毛利率 / 销售净利率` 可自算 | `market_probe.py` 的 `yf_info` 与 `abstract_rows` |
| **E10** | **财务摘要 8 列里有 4 列恒为 n/a**：解析器只取 `营业总收入` 与 `归母净利润`；而同一张表里 `经营现金流量净额 / 毛利率 / 销售净利率 / 营业总收入增长率 / 归属母公司净利润增长率` 全都在。生产 `fundamentals.md` 实测：`营收同比 / 净利同比 / 经营现金流 / 毛利率` 四列逐一 `n/a` | `akshare_provider.py:138-168`；`170db15a…/fundamentals.md` |
| **E11** | **同业对比用的是合成价格**：`_derive_peers()` 直接 `from .market_data.synthetic_provider import ... SyntheticProvider` 造 6 家「同业」，价格来自合成 walk；且 `instruments.sector` 为空时直接返回 `[]`（生产库里 `001232.SZ`、`688018.SH` 的 sector 就是 `None`） | `snapshot.py:306-334`；`db_probe3.py` 的 instruments 表 |

配套的三条**次生事实**（本轮一并处理，因为它们同属「读者看到的东西不可信」）：

| # | 事实 | 证据 |
| --- | --- | --- |
| **E12** | **跨源告警被误标成「陈旧」**：`dataQuality.stale = bool(warnings)`，于是一条「财务 akshare / 行情 yfinance」的**口径**提示会让 `stale=true`，`run_manager` 再把它翻译成「资料自检：数据存在陈旧警告」。生产三份 CN 报告全部命中 | `snapshot.py:194`；`run_manager.py:543-551`；`170db15a…` run.log seq 1860 |
| **E13** | **告警文本被腰斩**：链上失败信息 `str(exc)[:120]`，于是提示词里留给模型的是半句 `…用 ak.interface_info(`——模型没法据此判断「到底该装什么、该改什么」 | `registry.py:97`；`170db15a…/prompt.md` |
| **E14** | **「数据源健康」对运维不可见**：`/capabilities.dataProviders` 只有 `{id, available}`，`available` 仅表示**依赖能 import**。akshare 报 `available: true`，而它的日线接口是坏的——这正是用户「看日志才发现数据源对接出问题」的原因 | `registry.py:59-67`；`schemas/capabilities.py:52-54`；`capabilities.py:222-223` |

### C. 三份既有诊断的关系（不重复劳动）

仓库里已有三份 9 万字的诊断，本轮**站在它们的结论上**，不推翻、不重做：

| 既有诊断 | 已修 | 与本轮的关系 |
| --- | --- | --- |
| `docs/diagnoses/chart-price-integrity/analysis.md` | 合成 walk 的固定日历锚点、一对数字同源、缓存覆盖率/新鲜度谓词、混源代次 | 其 §6 的落点清单里 **两条至今未做**：①`snapshot.py` 自算 `revenueYoY/netProfitYoY`（要取样到 12 期）；②`snapshot.py` **清除 pe 永假分支**（BUG-5）。本轮 Z5 直接接走 |
| `docs/diagnoses/provider-chain-divergence/analysis.md` | 周/月重采样、跨源告警、`meta.reasonCode`、写入侧单生产者 | 其 §6.1 第 6 条「provider 钉源（pinning）」未做——★v2：本轮把它**降级为可选的 M59b**（见 P1-9），不做也不影响「报告读得懂」；§6.3「**不要**放宽 `read_bars` 的混源拒绝」本轮继续遵守 |
| `docs/diagnoses/grounding-trust-integrity/analysis.md` | 派生量目录（A1）、严格值匹配（A2）、非测量数字归 identifier（A3）、按值去重（A4）、覆盖补齐（A5）、`rules` 口径（A6）→ 现为 `claims-v4` | 本轮**不动判据的结构与 0.9 阈值**（但派生目录一变，`RULES_VERSION` 必须随之升到 `claims-v5`——★v2 / P1-5：`rules` 标签是给读者判断"能不能比"用的，不换版本号就是让两个口径冒充一个），只做两件事：①把模型真实会用到的推导**先算成事实**（Z5），让它天然可追溯；②给派生目录补 3 条由新事实引出的公式（Z6）。实测现状：三份 CN/US 报告的 `groundedRatio` = 0.799 / 0.824 / 0.826，样本中既有合法推导（环比 +11.8%、距高点 −51.2%），也有残留误判 |

---

## 26.1 Overview

这一版要解决的不是「页面某个数字摆错了」，而是**研报的事实底座本身不完整、不可信**。
生产上真实生成的报告暴露了一条完整的失败链：A 股主数据源（akshare 东方财富）接口改名后
整条链静默滑到 yfinance → yfinance 的 A 股序列日期整体早一天、当日行是半成品 →
半成品被当成完整 K 线入库 → 那根坏 K 线污染全部技术指标、还把日期标签带偏一天 →
估值/同业/财务同比这三块**本来能拿到**的数据一直缺席或来自合成源 →
模型被提示词约束「只能用资料里的数字」，于是只能在报告里反复写「缺失 / n/a / 无法用倍数判断」。
一份这样的研报，读者当然没法拿它做决策。

因此本轮的主线是「**让事实底座重新完整，并且让每一处缺口都说得出名字**」，而不是「再对齐一次数字」。
设计上做四件事：（1）把「会话日期」变成唯一真源，从 provider 边界一路到 `bars.csv`、K 线轴、
报告里引用的日期都只经过一个函数；（2）给采集层补上错误分类学、能力矩阵与断路器
（单飞/钉源留到可选的 M59b），
让「改名的接口」「被限流的源」变成**可命名、可跳过、可诊断**的状态，而不是一条被截断的日志；
（3）把估值、同业、财务同比/现金流/毛利率这些**能拿到但没取**的数据补齐，并把拿不到的写成
结构化缺口（`issues[]`），而不再是 `null` 与 `n/a` 混在一起；（4）把「数据质量」抬到读者能看见的地方
——报告 `quality.dataQuality`、`/capabilities` 的按域健康、以及一份使用合成数据的报告必须显式降级。

范围与边界：不改报告文档 schema（`report_doc.schema.json` 的 12 个键一个字都不加），
不改六阶段编排与 SSE，不改接地判据与 0.9 阈值，不新增数据库列（零 DDL），
不新增路由（避免动 `security/policy.py` 的策略表）。所有改动集中在
`api/app/services/market_data/`、`api/app/services/{snapshot,indicators,numeric_claims,run_manager}.py`、
`api/app/{routers,services}/capabilities` 一侧、`api/app/agent/prompts/`、以及前端 5 个文件（含类型镜像与文案）。

---

## 26.2 问题分解与验收口径（Z1–Z8）

| ID | 一句话 | 根治的缺陷 | 不做会怎样 |
| --- | --- | --- | --- |
| **Z1** | 采集层骨架：错误分类学 + 能力矩阵 + 断路器 + `allow_empty`（**单飞/钉源降级为可选的 M59b**，★v2 / P1-9） | E1/E2/E3/E13/E14 | 每修一个 endpoint 就要再等下一次改名；限流会变成常态故障 |
| **Z2** | akshare 真源修复：改名解析 + CN 三端点降级链 + 财务 12 期 + 估值自算 | E1/E2/E3/E10/E9 | A 股永远吃 yfinance 的二手数据；财务永远 4 列 n/a |
| **Z3** | yfinance 归一化与完整性闸门：会话日期、半成品行、OHLC、估值、sector | E4/E5/E6/E9/E11 | 日期错一天、坏 K 线继续入库 |
| **Z4** | 入库/缓存完整性：写侧校验 + 存量坏行清理 + 序列一致性 | E5/E6 + 存量 | 修好采集层，库里的坏行还在继续污染 |
| **Z5** | 事实底座补全：估值/同业/同比/现金流/毛利率/覆盖率/`issues[]` | E8/E9/E10/E11/E12 | 报告继续写「缺失」，读者继续用不了 |
| **Z6** | 提示词与接地适配：事实清单、缺口话术、`derived` 目录补 3 条 | E12/E13 + 接地残留 | 新增事实接不进判词；模型仍会为缺口写空话 |
| **Z7** | 诊断面：`/capabilities` 按域健康、`quality.dataQuality`、前端徽标、合成报告降级 | E14/E12 | 下一次同类故障仍然只能靠翻日志 |
| **Z8** | 端到端验收：生产真实生成 3 个标的 + ego-browser 走查 + 断言脚本 | 全部 | 「修完了」无法被证伪 |

---

## 26.3 技术设计

### 26.3.1 会话日期契约（Z2/Z3/Z4 的公共地基）

**问题**：三个 provider 给日线打的时间戳口径不同，而下游 13 处日期渲染全用 UTC：
akshare/synthetic 用 `07:00 UTC`（收盘时刻），yfinance 用交易所本地 `00:00`（= UTC 前一日 16:00）。
`base.py:87-97` 已经把这个差异写进注释，却只在周/月重采样里用了偏移（`_session_date`），
**日线本身没归一**。

**设计（唯一真源 + 两处调用）**：

在 `api/app/services/market_data/base.py` 新增三个纯函数，并让它们成为「日线日期」的唯一真源：

```python
SESSION_UTC_OFFSET_HOURS = {"CN": 8, "HK": 8, "US": 0}   # 复用现有 _SESSION_UTC_OFFSET_HOURS

def session_date(ts: int, market: str | None) -> date:
    """一根日线所属的**交易日**（交易所本地日历日）。"""

def session_date_str(ts: int, market: str | None) -> str:
    """`YYYY-MM-DD`；bars.csv / priceSeries / 报告脚注 / 前端轴标签都只经这里。"""

def canonical_session_ts(ts: int, market: str | None) -> int:
    """把 provider 的日线戳归一到该交易日的**规范戳**（★v2 修正 P0-2）。

    * **CN / HK**：该交易日 **07:00 UTC**（收盘时刻；与 akshare / synthetic 的现有口径一致）；
    * **US**：**原样返回**。yfinance 的 US 索引是 `00:00 America/New_York` = 04:00/05:00 UTC，
      与本项目的 `_CLOSE_HOUR_UTC["US"] = 21`、`latest_closed_session()` 落在同一天，
      强行改成 00:00 UTC 只会让同一天的两种戳互相冒充。

    纪律：**幂等** —— `canonical_session_ts(canonical_session_ts(ts, m), m) == canonical_session_ts(ts, m)`。
    「US 原样返回」不是例外，而是幂等的**必要条件**：一旦 US 也被重算成 00:00 UTC，
    库里既有的 05:00 UTC 行就不再规范，写侧校验（26.3.5(b)）会把它们当坏行拒写，
    而 US 侧并没有任何口径问题需要修。
    """
```

三条纪律：

1. **存储归存储、展示归展示。** `canonical_session_ts()` 只在 **provider 边界**
   （`registry._try_chain` 拿到 `bars` 后）与 **CLI 一次性迁移**（`z4`）里调用；
   `session_date_str()` 是**所有**输出日期的地方（`snapshot.write_bars_csv`、
   `_derive_price_series`、`_bars_warnings`/覆盖率、`instruments.py` 的 `meta.stale`、
   `cache.is_bars_cache_fresh`、`block_dates` 的反查）。
2. **不动 ts 的物理含义之外的东西**：`ts` 仍是 epoch 秒；周/月重采样继续用
   `_session_date`（现在它可以改为调 `session_date`，行为等价）。
3. **`bars` 表零 DDL**：主键 `(instrument_id, timeframe, ts, adjust)` 不变；
   归一后同一交易日的旧行与新行会**同时存在**，因此必须有一次迁移把 CN/HK 的旧行删掉
   （见 26.3.5 的 `normalize-bars`）。US 行不动。

**为什么不是「前端改时区」**：`web/lib/format.ts::epochToDate` 已经在用
`instrument.tz` 格式化，看起来是对的；但 `bars.csv`、`priceSeries`、模型引用的日期、
`block_dates` 的区间反查全在服务端，改前端只能修好「一个面板」，修不好「事实底座」。

### 26.3.2 采集层骨架（Z1）

**a) 错误分类学**（`base.py:16-31`）

新增一个 kind 与一个分类器，替换现在「什么都当 `network`」的做法：

```python
class ProviderErrorKind(StrEnum):
    network = "network"
    ratelimit = "ratelimit"        # 已有，但没人产出（E3 实测就是它）
    notfound = "notfound"
    unavailable = "unavailable"
    api_drift = "api_drift"        # 新增：客户端/库版本与上游接口不再匹配
    payload = "payload"            # 新增：返回了数据但形状/值不可用（NaN、空列、缺列）

def classify_provider_exception(exc: BaseException, provider: str) -> ProviderError:
    """把底噪异常翻成 ProviderError。

    判定顺序（先精确后宽泛）：
      1. `AttributeError` 且消息含 `更名为` / `has no attribute` → api_drift
         （保留**完整**消息，含「请改用 ak.xxx」与 changelog 链接，不截断到 120 字）；
      2. 类名或消息含 `RateLimit` / `限流` / `429` → ratelimit；
      3. 类名含 `Payload` / `Empty` / `JSONDecode` → payload；
      4. `ConnectionError`/`Timeout`/`urllib*` → network；
      5. 其余 → unavailable。
    """
```

`ProviderError.retryable` **保持** `{network, ratelimit}` 不动（★v2 修正 P2-1：这两个 kind
本来就在集合里，v1 的「扩为」是笔误）。本轮真正改变的是 `api_drift` / `payload` /
`unavailable` **不在**该集合——尤其 `api_drift` **不可重试**：同一进程里重试只会再拿到
同一个 `AttributeError`，白烧一次限流额度。

**b) 能力矩阵**（`registry.py`）

```python
DOMAINS = ("search", "bars", "quote", "fundamentals", "valuation", "peers")

def supports(self, provider: str, domain: str) -> bool:
    """按 provider 的 `domains` 声明判定（不是 try/except 猜）。"""
```

每个 provider 类加一个类属性：

| provider | search | bars | quote | fundamentals | valuation | peers |
| --- | --- | --- | --- | --- | --- | --- |
| akshare | ✅ | ✅（em/tx/sina） | ✅（由日线派生） | ✅ | ✅（由财务自算） | ✅（行业成分，受断路器保护） |
| yfinance | ✅ | ✅ | ✅（由日线派生） | ✅（5 季） | ✅（`.info`） | ❌（无可靠名单，返回 `None`） |
| synthetic | ✅ | ✅ | ✅ | ✅ | ❌ | ✅（仅离线演示，**不得**用在真实标的的报告里） |

不支持某域时**不进链**（不再用「空结果」冒充「不支持」，这是既有诊断 PCD-1 的第 3 条）。

**可选域允许「空」**（★v2 新增，修正 P1-8）。`valuation` 与 `peers` 的「没有」是**合法答案**，
不是失败。而 `_try_chain` 现在把 `None` / 空列表都当作一次失败，一路走完就
`raise ProviderError("所有数据源都失败了: …")`——这句话会原样进 `warnings`、再进提示词，
于是「这家公司没有同业报价」在模型眼里会读成「全链崩了」。所以：

```python
async def _try_chain(self, method, *args, symbol_hint: str = "", allow_empty: bool = False, **kwargs):
    """`allow_empty=True`：链走完仍为空是**答案**，回 ChainResult(value=[] , source="", warnings=[...])，不抛。"""
```

- `valuation` / `peers` 传 `True`，由调用方写具名缺口（`valuation_missing` / `peers_unavailable`）；
- `search` / `quote` / `fundamentals` / `bars` 保持 `False`（真的空 = 真的失败）；
- 空答案的 `source` 是 `""`，**不许**写成 `"synthetic"` 之类冒充（见 AC-277）。

**c) 断路器**（`registry.py`，内存态，单 worker 天然安全）

```python
@dataclass
class Breaker:
    failures: int = 0
    opened_at: float | None = None
    last_code: str = ""

_BREAKERS: dict[tuple[str, str], Breaker] = {}      # (provider, domain)
BREAKER_THRESHOLD = 2          # 连续 2 次 ratelimit/api_drift 就打开
BREAKER_COOLDOWN = {"ratelimit": 300.0, "api_drift": 3600.0, "network": 60.0}
```

- 命中 `ratelimit` / `api_drift` → 计数 + 打开，冷却期内**直接跳过**（不再打上游）；
- 冷却到点后「半开」：只允许一次探测，成功则清零；
- `breaker_state()` 供 diagnosis 与 `/capabilities` 读；
- 进程重启即清零（与 `_FETCHED_AT` 同样的取舍：单 worker、崩溃重启就一次额外请求）。

**d) 单飞（single-flight）— ★v2 降级为「M59b 可选」**（修正 P1-9）

同一 `(domain, symbol, 关键参数)` 的并发调用合并成一次。生产是单 worker，
页面开一个 3 年图会打 `/bars`，研报又会打一次同参数的 `bars`——两次都走满一条链，
正是 E3 里限流被触发的现实路径。

**为什么降级**：它与 E1–E14 任何一条的修复都不构成依赖。它是限流优化，收益要在
「同一 (provider, domain) 在数秒内被并发打两次」时才兑现；而本轮的限流根因是**接口改名**
与**免费端点被 IP 限流**，断路器 + 三端点降级链已经把伤害挡住。它却要引入第三份进程内状态
（`_INFLIGHT` 锁表 + 生命周期 + 与 `reset_registry()` 的联动），一旦漏清就是测试互相污染。
**因此：M59 的门禁不含单飞；若 M59 之后仍有实测的并发重复请求（≥2 次/秒同参），
再按 M59b 单独实施并单独验收。**

**e) 钉源（pinning）— ★v2 降级为「M59b 可选」**（修正 P1-9）

```python
_PINNED: dict[str, tuple[str, float]] = {}   # symbol → (provider, expires_at)
PIN_TTL_SEC = 300.0
```

`_try_chain(..., prefer=pinned)` 先试钉住的 provider；它失败则**解钉并照常走链**。
钉源的收益是「一次 run 内 bars/quote/fundamentals 尽量同源」，E12 的口径噪声随之下降；
它**不替代** `read_bars` 的混源拒绝（既有诊断 §6.3 第 3 条）。

**为什么也降级**：钉源与 `read_bars` 的「一次只允许一个生产者」是两套同源机制，
把它们同时落地，出问题时分不清是谁在改归属；而**没有钉源也能满足「报告里读得懂」**——
只要 `dataQuality.domainSources` 把每个域的来源写清楚、跨源就给 `cross_source_*` 具名条目。
**因此 AC-260 改成二选一（钉源同源 **或** 具名跨源缺口），不再是「必须 ≥3 个域同源」。**

**f) 完整性校验挂在返回路径上**（见 26.3.4 的 `sanitize_bars`）

`_try_chain("bars")` 拿到 rows 后必过 `sanitize_bars()`，把「丢了几根、为什么」写进
`ChainResult.integrity`，随 `warnings` 一起进快照。

### 26.3.3 Z2 — akshare 真源修复（CN）

**a) 改名解析**（解决 E1/E2）

```python
_AK_API_ALIASES = {
    "bars_em":   ("stock_zh_a_hist_em", "stock_zh_a_hist"),
    "bars_sina": ("stock_zh_a_hist_sina", "stock_zh_a_daily"),
    "bars_tx":   ("stock_zh_a_hist_tx",),
    "abstract":  ("stock_financial_abstract",),
    "ratios":    ("stock_financial_analysis_indicator", "stock_financial_analysis_indicator_em"),
}

def _resolve_api(self, key: str) -> Callable[..., Any]:
    """按候选名逐个 `getattr`；首个命中即用。

    全都缺失 → ProviderError(api_drift, "akshare {ver} 里找不到 {names}；"
    "最近一次改名可从 `ak.interface_info({old})` 或 changelog 查（本轮实测："
    "stock_zh_a_hist → stock_zh_a_hist_em，stock_zh_a_daily → stock_zh_a_hist_sina）")
    """
```

要点：`AttributeError` 必须走 `classify_provider_exception`，**不许**再被
`except Exception: ProviderError(network)` 吞掉（`akshare_provider.py:87-88` 就是原病灶）。

**b) CN 日线三端点降级链（同一个 provider 内部的三个失败域）**

| 顺序 | 端点 | 主机 | 列归一 | 单位 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | `stock_zh_a_hist_em` | 东方财富 push2 | `日期/开盘/收盘/最高/最低/成交量/成交额` | 成交量单位=手（×100 → 股） | **最权威**，但 E3 实测会被限流 |
| 2 | `stock_zh_a_hist_tx` | 腾讯 | `date/open/close/high/low/volume/amount` | volume 已是股 | 实测可用（213 行，qfq 生效） |
| 3 | `stock_zh_a_hist_sina` | 新浪 | 待实施时确认 | 待确认 | 名字漂移已修，作最后兜底 |

三者中任一成功即返回，并把「实际用的端点」记进 `BarsResult.attrs` → 进
`dataQuality.domainSources.bars = "akshare:tx"`。三端点全失败才抛 `ProviderError`，
错误消息里带**每个端点各自的失败原因**（不截断）。

**c) 财务摘要：12 期 + 5 个新字段**（解决 E10）

`_ak.stock_financial_abstract()` 的同一张 frame 里，指标行实测包含：
`营业总收入 / 归母净利润 / 经营现金流量净额 / 毛利率 / 销售净利率 /
营业总收入增长率 / 归属母公司净利润增长率 / 基本每股收益 / 每股净资产`。

实施口径：

1. 取样 **12 期**（`periods` 由 8 → 12；`snapshot` 只用前 8 期展示，多出的 4 期用于自算同比，
   这是既有诊断 R-4(4) 的结论）；
2. 逐行按 `指标` 前缀匹配（不是 `contains` 全表碰运气），缺失行 → 字段 `None`；
3. `revenue_yoy` / `net_profit_yoy`：**优先用源给的增长率行**，源没有则自算
   `(本期 − 去年同报告期) / |去年同报告期|`，并在 `FundamentalPeriod.yoy_source` 标注
   `reported / computed`；
4. `ocf` = 经营现金流量净额；`gross_margin` = 毛利率；`net_margin` = 销售净利率
   （优先用源给值，源缺失才用 `净利/营收` 自算，同样标注来源）；
5. **单季轧差**不在 provider 里做（属于事实派生，放 Z5 的 snapshot）；
6. **★v2（修正 P1-4）：12 期只在进程内，永不落盘。** 取样 12 期的唯一目的是给「前 8 期」
   算出「去年同报告期」；写进 `snapshot.financials`、`fundamentals.md`、
   `sources.md` 的**只有前 8 期**，且 `write_fundamentals_md()` 渲染的行数必须
   **等于** `snapshot.financials` 的长度（同一个数，不是两份清单）。
   否则模型会在 md 里读到第 9–12 期的真数字并引用，而接地判定只认 `snapshot`——
   **真实存在的数字被判成「未接地」**，正是本轮要消灭的那类假噪声。
   验证：AC-265 追加一条「`fundamentals.md` 的期间行集合 == `snapshot.financials[*].period`」。

**d) 估值自算（akshare 路径）**（解决 E8 的一半）

akshare 的 PE/PB 端点（`stock_individual_info_em` / `stock_a_indicator_lg`）实测一个被限流、
一个已不存在。所以 akshare 路径的估值只能自算，且必须**诚实地标成自算**：

```
TTM 净利 = 最近年报归属净利 + 本期累计 − 去年同期累计
EPS_ttm  = TTM 净利 / 总股本（总股本 = 归母净利 / 基本每股收益，同期）
peTtm    = quote.last / EPS_ttm
pb       = quote.last / 每股净资产（最新一期）
```

产出 `ValuationSnapshot(source="akshare:computed", proxy=False, components={ttmNetProfit, epsTtm, bvps})`；
任何一环缺值 → 该字段 `None` + `issues[]` 记一条 `valuation_partial`（不是静默）。

**e) 同业（akshare 路径）**

`stock_board_industry_cons_em(symbol=<行业名>)` 拿同行业成分 → 取前 5 个**非本标的**代码 →
用同一条链取真实报价。被断路器拦下或返回空 → `peers=None` + `issues[]`
`peers_unavailable`，**绝不**退回合成价格（E11）。

### 26.3.4 Z3 — yfinance 归一化与完整性闸门

**a) `bars()` 四件事**

1. **会话日期归一**：`ts = canonical_session_ts(int(index.timestamp()), market)`；
   `market` 由 `to_yahoo_symbol()` 之后的后缀判定（`.SS/.SZ` → CN，`.HK` → HK，其余 US）。
2. **半成品行识别**（解决 E5）：`close` 为 NaN 或 `volume` 有值但 OHLC 全 NaN
   → **不是一根 K 线**，丢弃并在 `integrity.sessionIncomplete = date` 里记名。
   同日若 `open` 等于前一根 `close` 而 `high < open`（E5 实测形态）→ 同样丢弃。
3. **单位归一**：`volume` 已是股；`amount` yfinance 不给 → `None`（不编造）。
4. **不吞 NaN**：`float()` 之前先 `math.isfinite()` 判断，失败即走 `payload` 分类。

**b) `fundamentals()` 扩到 5 季 + 现金流 + 自算同比**

`quarterly_income_stmt` 已有 `Total Revenue / Gross Profit / Net Income /
Net Income Common Stockholders`；`quarterly_cashflow` 有 `Operating Cash Flow`
（实施时用 `frame.index` 精确匹配，实测该索引 40+ 行里包含它）。
同比用「同季去年」自算（yfinance 只给 5 季，够 2 期）并标 `yoy_source="computed"`。

**c) `valuation()`（解决 E8/E9）**

`.info` 取 7 个字段 → `ValuationSnapshot(source="yfinance:info",
trailingPE, priceToBook, marketCap, dividendYield, trailingEps, sector, industry)`。
纪律：

- `.info` 是**最贵的一次调用**（一次抓整页），进程内按 `symbol` 缓存 6 小时
  （`_INFO_CACHE`，上限 256 条），失败不缓存；
- 任一字段为 `None` → 保持 `None`，**不用 PE = price/EPS 反推去补 `trailingPE`**（那是另一个口径）；
- `pePercentile3y` 本轮**仍然给 `None`**，并在 `valuation.coverage` 里明说
  「无 3 年 PE 序列来源」——现在它是**假 null**（永假分支），改完是**真答案**。
  若实施时能拿到 PE 历史序列（akshare 的 `stock_a_indicator_lg` 已不存在，需要另找），
  再填它，不允许用价格分位冒充 PE 分位；**价格分位**作为独立事实放 `technicals.pricePercentile*`。

**d) `search()` 顺手补全 `sector/industry`**

搜索结果现在不带行业，导致新建 instrument 的 `sector=None` → 同业永久为空（E11）。
`.info` 一次调用即可带回 `sector/industry`，写进 `SymbolHit.attrs` →
`symbol_resolver` 落库时填 `instruments.sector`（缺字段不写，不造）。

### 26.3.5 Z4 — 入库与缓存完整性

**a) `sanitize_bars()`（唯一实现，挂 registry 返回路径）**

```python
@dataclass(slots=True)
class BarIntegrity:
    received: int = 0
    kept: int = 0
    dropped_nonfinite: int = 0
    dropped_ohlc: int = 0
    dropped_duplicate: int = 0
    dropped_incomplete: int = 0
    samples: list[dict] = field(default_factory=list)   # 最多 3 条，含日期与四价
    session_incomplete: str | None = None

def sanitize_bars(rows: list[BarRow], market: str, *, now: datetime | None = None) -> tuple[list[BarRow], BarIntegrity]:
    """逐根判定：①四价与量必须 finite 且 > 0（close）；②high >= max(open, close)、
    low <= min(open, close)（**只断言块内自洽**——既有诊断 R-5 已证明「high >= prevClose」
    是伪不变量，不得写成逐日断言）；③同一 ts 去重（保留后到者）；
    ④未收完的当日行丢弃并记名；⑤最后按 ts 升序。

    ★v2（修正 P2-6）：②的比较一律带容差 **_EPS = max(1e-6, 1e-6 * |price|)**。
    yfinance 的四价是浮点，`high` 与 `max(open, close)` 在 1e-9 量级上本来就会互相摩擦；
    不给容差，`sanitize_bars` 会把自己造出来的浮点噪声当成坏行丢掉（实测 `cross_source_probe.py`
    用的是绝对 1e-6，评审认可这个量级）。容差只放宽「判等」，不放宽「值必须 finite」。
    """
```

**b) 写侧二次校验 + 非规范行清场**（`cache.write_bars`）

入库前对每根 bar 做 `isfinite` 与 `canonical_session_ts` 复算，不一致的**拒写并计数返回**
（`write_bars` 返回 `WriteReport(written, rejected, purged)`，见下）。理由：
`sanitize_bars` 在未来某次重构里被绕过时，库仍然是干净的；这与既有「一个日期只有一个价格」
的纪律同源。

**★v2（修正 P1-1）：同一次写入必须把「非规范戳」的旧行清掉，不许等运维跑 CLI。**
`write_bars` 现在按 **ts** upsert、并按 `source != stored_source` 清场；如果某个实例上还有
旧口径的 CN 行（16:00 UTC，来自 yfinance）而新行是 07:00 UTC，两者 `source` 相同、
`ts` 不同 ⇒ **同一交易日两根 bar 同时留在库里**，双双进 `bars.csv` 与 MA 计算。
这比现在的坏行更糟，而它只在「忘了跑 `normalize-bars`」时出现——把正确性押在
「有人记得跑一条命令」上，就是 v1 最大的上线风险。所以：

```python
def purge_noncanonical(session, instrument_id, *, timeframe, adjust, market) -> int:
    """删掉本序列里 `ts != canonical_session_ts(ts, market)` 的行，返回删除条数。

    在 `write_bars` 里、upsert **之前**调用一次（同 market 幂等，US 恒为 0 条）。
    """
```

- `write_bars` 的返回值改为 `WriteReport(written: int, rejected: int, purged: int)`（dataclass，
  `int` 语义不够用了；现有两处调用点忽略返回值，不破）；
- `purged > 0` 时记一条 `run.log` / `logger.info`，让「这次刷新顺手收敛了旧口径行」可追溯；
- 于是**运维忘记跑 `normalize-bars` 也能自愈**：任意一次刷新都会收敛该序列；
  CLI 只是「一次性批量 + 可 `--dry-run` 预演」的工具，不是正确性的前提。

**c) 存量清理（一次性迁移，零 DDL）**

新增 CLI 子命令（`api/app/cli.py`，模式沿用现有 `reset-root-password` 等）：

```text
python -m app.cli normalize-bars --market CN,HK [--dry-run]
python -m app.cli normalize-bars --drop-invalid --market CN,HK,US [--dry-run]   # ★v2
```

行为：

1. **默认模式**：删掉指定的 CN/HK 所有 `bars` 行（它们的时间戳是「交易所午夜」口径，与归一后的
   `07:00 UTC` 冲突），让下一次读取自然重取；`--dry-run` 只打印每个标的会被删几行。
   **US 行不删**（ET 午夜落在同一天，`session_date` 一致）；
2. **★v2（修正 P1-2）`--drop-invalid`：清「坏行」，与「戳口径」是两件事，必须分开做。**
   按 `sanitize_bars` 的同一套判据（非 finite、`high < max(open,close)`、`low > min(open,close)`）
   扫**所有** market 并删掉违规行——E5/E6 的坏 bar 是 **yfinance 的通病**，`US:NVDA`
   一样会有 `close=NaN` / `open=前收、high<open`；v1 只清理 CN/HK 的戳，
   等于默认「US 侧不会有坏行」，而 AC-270 要的是「全库 OHLC 不自洽行数 == 0」。
   `--dry-run` 打印 `(instrument, session, 四价, 违规类型)`，与 `BarIntegrity.samples` 同形；
3. 两个模式都幂等、只删不建；产出报告写进 `docs/plans/data-source-integrity/evidence/normalize-report.md`；
4. **上线顺序**：先 `--dry-run` 看规模 → 跑默认模式 → 跑 `--drop-invalid` → 预热 3 个标的
   （`300750.SZ` / `600519.SH` / `NVDA`）→ 用 `db_probe3.py` 的对应查询证明违规数归零。

**d) 缓存与 `latest_session_due` 的对齐 + `stale` 不再对「盘中」撒谎**（★v2 修正 P0-4）

`is_bars_cache_fresh()` 与 `/bars.meta.stale` 都改用 `session_date()`，
于是「昨天收盘的数据」不再被误判成「落后一个会话」——E4 的另一半（缓存永不吃 TTL 之后的免费命中）。

**但只换日期函数不够**：`sanitize_bars` 会丢掉「当日未收完」的那一行（Z3(a) 第 2 条），
于是盘中最后一根**完整** bar 是**上一个已收盘会话**，而 `latest_session_due()` 在盘中返回
**今天**。若判据仍是 `last_session < latest_session_due()`，那么每个交易日盘中，
CN 标的的 `stale` 恒为 `true`，`run_manager` 再把这条翻译成「资料自检：数据存在陈旧警告」——
**这就是 E12 要消灭的那类假话**（v1 只修了跨源那一半，漏了丢行这一半）。因此：

```python
def bars_stale(last_session: date, market: str, now: datetime) -> bool:
    """`stale` 的**唯一**判据：序列是否落后于「最近一个**已收盘**会话」。

    - 盘中（`is_market_open`）：期望的最后完整会话 = `latest_closed_session()`（今天不算，
      因为今天那根正在形成、按 Z3(a) 已经被丢弃）；
    - 收盘后 / 周末：同样是 `latest_closed_session()`（周五收盘后到周日都是周五）。
    于是「故意丢弃未收完的当日行」永远不会被翻译成「数据陈旧」；
    真正的滞后（缺了上一个已收盘会话）仍然会 `true`。
    """
```

三处调用点统一走它：`routers/instruments.py` 的 `meta.stale`、`cache.is_bars_cache_fresh()`
的「缺会话」分支、`snapshot.dataQuality.stale`。**`latest_session_due()` 保留但只用于
「要不要打上游去拿今天那根」的缓存决策，不再参与任何 `stale` 判定**（两件事，两个函数）。

### 26.3.6 Z5 — 事实底座补全（`snapshot.py` 为主）

**a) `build_snapshot()` 的新顺序**

```text
1. bars   : 缓存优先（覆盖 + 新鲜度）→ 链（M59b 若做钉源则优先）→ sanitize → 写缓存（含 purge_noncanonical）
2. quote  : 缓存 60s → 链 → 与 bars[-1] 做**锚点一致性**判定
3. fundamentals: 链（12 期取样，落盘只前 8 期，见 26.3.3(c)6）
4. valuation   : provider.valuation()（yfinance .info / akshare 自算），`allow_empty=True`；
                 空 → 退回「用财务自算」→ 仍空则全 null + issue
5. peers       : provider.peers()（真实报价），`allow_empty=True`；空 → [] + issue
6. 派生        : 同比/环比/单季、技术指标、区间表现、价格分位
7. 组装        : dataQuality{domainSources, issues, coverage, stale(bars_stale 唯一判据), synthetic}
```

**b) `dataQuality` 新结构**（旧键**全部保留**，只追加——老报告与老读者不破）

```jsonc
{
  "source": "yfinance",             // 行情主源（不变）
  "quoteSource": "yfinance",        // 不变
  "domainSources": {                // ★新增：每个域各自是谁
    "bars": "yfinance", "quote": "yfinance",
    "fundamentals": "akshare", "valuation": "yfinance:info", "peers": "yfinance"
  },
  "synthetic": false,               // ★新增：任一域落到 synthetic 即为 true
  "barsCount": 728,                 // 不变
  "lastBarTs": 1795104000,          // 不变（归一后的 07:00 UTC）
  "lastSession": "2026-11-20",      // ★新增：会话日期（唯一真源函数）
  "stale": false,                   // ◆语义变更：只表示「最新会话缺失/滞后」
  "cached": false,                  // 不变
  "coverage": {                     // ★新增
    "requestedBars": 730, "deliveredBars": 728,
    "firstSession": "2023-11-20", "lastSession": "2026-11-20",
    "sinceListing": false, "sufficientFor": ["ma60", "ret1y"]
  },
  "integrity": {                    // ★新增：sanitize_bars 的账
    "received": 730, "kept": 728, "droppedOhlc": 1,
    "droppedIncomplete": 1, "samples": [{"session": "2026-11-20", "open": 240.0, "high": 231.99}]
  },
  "issues": [                       // ★新增：结构化缺口（替代「一句中文 + 不知道怎么办」）
    {"code": "cross_source_fundamentals", "domain": "fundamentals",
     "provider": "akshare", "severity": "info",
     "message": "财务数据来自 akshare，行情来自 yfinance；两者口径不同。",
     "hint": "已在报告脚注标注；如需单一来源可设 DATA_PROVIDERS=yfinance,akshare"}
  ],
  "issuesTruncated": false,         // ★新增（v2 / P2-4）：issues 超过 8 条被按 severity 截断过
  "warnings": ["…"]                 // 保留：仍由 issues 渲染成中文句子（向后兼容）
}
```

**`stale` 的语义收窄是关键**（E12）：它只允许表示「序列落后于**最近一个已收盘会话**」——
判据是 §26.3.5(d) 的 `bars_stale()`，不是 `bool(warnings)`，也不是 `latest_session_due()`
（盘中它返回今天，会把「故意丢弃未收完的当日行」翻译成「数据陈旧」，见 P0-4）。
口径不同、缺字段、断点丢行、丢弃坏 bar 全部属于 `issues`。
`run_manager` 的「资料自检」改成按 `issues[].severity` 逐条记录（`warn`/`info`），并把
`events` 里的文案改为 `资料自检：数据缺口 {code}（{domain}）`——读日志的人一眼能看出
**哪一块**出了问题。

**★v2（修正 P2-3）：删掉 `SnapshotBundle.stale`（`snapshot.py:44-46`）。**
它靠「告警文本里有没有『陈旧』两个字」判定，是本轮收回 `stale` 语义后**会撒谎的第二定义**，
且全仓无人使用（`grep -rn "bundle.stale" api` 为空）。要么删掉，要么改成读
`snapshot["dataQuality"]["stale"]`——**不留两份判定**。

**c) 财务派生（12 期）**

`FundamentalPeriod` 增 3 个可选字段（`yoy_source`、`qoq_revenue`、`qoq_net_profit`），
`snapshot.financials[i]` 追加 `revenueQoQ` / `netProfitQoQ` / `singleQuarter*` 四组值：

- **同比**：源给则用源，源没有用「去年同报告期」自算（`revenueYoYSource`）；
- **环比**：A 股财报是**累计口径**，环比必须先轧单季（`singleQuarter = 本期累计 − 上期累计`），
  再算 `(本期单季 − 上期单季)/上期单季`；跨年边界用「上年年报」作上年同期基准。
  这一步既有的 `numeric_claims._single_quarter()` 已有实现，**把它提升为公共函数**
  （`services/financials.py` 新建，snapshot 与 numeric_claims 共用一份，避免第二份实现）。

**d) 估值**（解决 E8）

删除 `_derive_valuation` 里的 `pe = None` 死分支，改为：

```python
def _derive_valuation(
    instrument: Instrument,
    closes: list[float],
    fundamentals: list[FundamentalPeriod],
    quote_row: QuoteRow,
    valuation: ValuationSnapshot | None,   # ★v2（P2-5）：参数只增，`closes` 保留（分位备用）
) -> dict:
    """有 provider 估值 → 原样落；没有 → 用财务自算（标 computed）；再没有 → 全 null + issue。"""
```

输出字段：**现有 7 个键一个字都不删**（`peTtm` / `pePercentile3y` / `pb` / `dividendYield` /
`marketCap` / `netMargin` / `proxy`；★v2 修正 P2-2：v1 写的「8 个键」是笔误，实测 `snapshot.py:295-303`
是 7 个），追加 `marketCapSource` / `peSource` / `components` / `coverage`。
`proxy` 的语义收紧为「**是否用了价格/净利率代口径**」：真倍数（`.info` 或自算 PE）→ `false`，
只有实在拿不到时才 `true`。**不许**在有真值时保留 `proxy: true`。

**e) 同业**（解决 E11）

删掉 `_derive_peers` 里的 `SyntheticProvider()` 依赖（这是本轮唯一要「删除功能」的地方，
理由见 §26.10）。替换为 `provider.peers(symbol, limit=5)`，每家的 `last/changePct`
来自同一条链；拿不到就 `[]` + `peers_unavailable`，并在提示词里显式告知
「本标的无同业报价，不得写同业对比数字」。

**f) 技术指标与区间表现**

`indicators.snapshot_technicals()` 追加（全部由已有序列算出，零新依赖）：
`pricePercentile60`、`pricePercentile252`、`distanceFromHigh1y`、`distanceFromLow1y`、
`atrPct`、`avgVolume20`、`volumeRatio20`。
`snapshot.performance` 追加 `ret5d`、`ret6m`。
入口处加 `_finite_filter()`：把非 finite 的 bar **整根剔除**再算指标（E6 的防线）。

### 26.3.7 Z6 — 提示词与接地适配

**a) `research_task.md.j2`（三处改动）**

| 位置 | 现在 | 改成 |
| --- | --- | --- |
| 「数据质量警告」段（`:17-23`） | 逐条中文句子 | 逐条 `[code] 句子（建议：hint）`，**完整**（不截断） |
| 「硬性约束」第 1 条 | 「只能用上述文件中的数字」 | 追加一句：「文件里没有的维度（如估值、同业）**必须**在对应章节写明『该维度数据不可用』，不得用相近数字顶替，也不得整段省略」 |
| 新增「数据覆盖」段 | — | 渲染 `coverage` 与 `domainSources`：`行情覆盖 2023-11-20 ~ 2026-11-20（728 根，=上市以来）`、`估值来源 yfinance:info` |

`research_system.md` 追加一条：「当资料标注某维度缺失时，正确做法是写明缺口与其对结论的影响，
错误做法是给出一个『看起来合理』的数字」。

**b) `numeric_claims.derived_values()` 补 3 条**

新事实会引出新的合法推导，派生目录必须同步，否则「诚实推导」又会被判未接地：

| 新增 id | 公式 | 为什么必须有 |
| --- | --- | --- |
| `qoq:singleQuarter.<field>` | 单季轧差的环比 | 实测样本里 `+11.8%`（Q3 营收环比）被判 missing；现在快照里已有 `singleQuarter`，公式可命名 |
| `distance:priceSeries.<min\|max>@quote.last` | 距区间极值 | 实测 `−51.2%`（距高点）被判 missing |
| `percentile:priceSeries.close` | 收盘价在近 60/252 日的分位 | 与 `technicals.pricePercentile*` 同源；数值在快照里已存在，目录只是给出命名 |

**★v2（修正 P1-5）：`RULES_VERSION` 必须从 `claims-v4` 升到 `claims-v5`。**

上一行原本写的是「不动 `RULES_VERSION`（仍 `claims-v4`）…… 口径没变，所以老报告与新报告的
百分比可比」。这句话在本轮是**不成立**的：新增 3 条合法推导 = **判据本身变了**（原来判
missing 的写法，现在能对上派生量），于是同一份 doc 在新旧两代规则下会得出不同的
`factClaims/factMissing`。而 `numeric_claims.py:52-58` 的原文就是为这一刻写的：
「判词是**永久落库**的，而判据会变…… 跨口径只能『各自陈述』，不能比较」。

保持 `claims-v4` 的后果是具体的：库里 0.799 的旧报告与 0.85 的新报告顶着**同一个版本号**
被并排展示，读者会以为"接地率提升了 5 个点"，而实际发生的是"判据放宽了 3 条公式"。
AC-280「不得下降」也会失去锚点。所以：

- `RULES_VERSION = "claims-v5"`；`quality.grounding.rules` 随之上移；
- **不变**：`0.9` 阈值、`_allowed_scales`、`_precisions`、`MIN_FACT_CLAIMS`、
  `APPROX_TOLERANCE` —— 放宽的只有派生目录，验收里逐条断言这些常量没动（AC-281）；
- 前端 `VersionSwitcher` 已经把「连判词都没有」的旧字段与「本代口径」分开渲染，
  多一个 `claims-v5` 只是多一个**可读**的标签，不需要改渲染逻辑；
- 若要跨版本比较，只允许在同一 `rules` 标签内比较（既有纪律，不新增）。

**c) 告警文本不再腰斩**（E13）

`registry._try_chain` 的 `str(exc)[:120]` 全部去掉；改名提示、限流提示都是**行动指引**，
必须完整。真正的长度风险由 `assert_prompt_budget`（`ARAGON_MAX_PROMPT_BYTES=32768`）兜底，
而 `issues` 最多 8 条（实施时按 **severity desc, code asc** 排序截断，并在
`dataQuality.issuesTruncated = true` 里记名——★v2 修正 P2-4：这个键必须同时出现在
§26.3.6(b) 的形状、§26.6.2 的追加清单与 `ReportQuality.dataQuality` 的镜像里，
三个地方少一个，前端就永远读不到「被截过」这件事）。
**截断不许静默**：`issuesTruncated=true` 时额外在 `warnings` 末尾追加一句
「另有 N 条数据缺口因长度预算未列出」。

### 26.3.8 Z7 — 诊断面与可见性

**a) `/capabilities.dataProviders` 从「能不能 import」升级为「每个域能不能用」**

```jsonc
{"id": "akshare", "available": true,
 "domains": {
   "bars":        {"ok": false, "code": "ratelimit", "latencyMs": 990, "checkedAt": "2026-11-20T21:04:11Z"},
   "fundamentals":{"ok": true,  "code": "ok",       "latencyMs": 1700, "checkedAt": "…"},
   "valuation":   {"ok": false, "code": "api_drift", ...}},
 "breaker": {"open": ["bars"], "until": "2026-11-20T21:09:11Z"},
 "note": "stock_zh_a_hist → stock_zh_a_hist_em（1.20.46 改名）；push2 当前被限流"}
```

- 探测**只在 `?deep=1` 或 admin/service 身份下发起**，结果缓存 10 分钟；
  默认请求只读缓存（不因打开页面而打上游——E3 的限流教训）；
- 现有 `{id, available}` 两键保留（`web/lib/types.ts` 与既有测试不破）；
- `registry.available_providers()` 的返回结构扩字段，**不改语义**。

**★v2（修正 P0-3）：新键必须进脱敏，且保持「同形状」。**
`/capabilities` 用的是 `deps.get_principal` 而**不是** `enforce`，匿名身份走
`_redact()`；而 `_redact()` 的契约是「**键集合与完整响应相同**，只是敏感值变 null/false/[]」。
v1 把 `domains`（每个上游域的 ok / 失败码 / 延迟）、`breaker`（哪个源被限流、到几点）、
`note`（「push2 当前被限流」）挂在 `dataProviders` 上却**没有动 `_redact`**，
匿名访客就能从首页的端点徽标里读到运维面。这与 §26.4.1 给 `health.py` 定的纪律
（公开路由不放运维信息）同一条，只是漏在了 capabilities 上。

规则：

| 身份 | `dataProviders[i]` 的形状与内容 |
| --- | --- |
| `engine.probe`（admin/service） | 全量：`{id, available, domains, breaker, note}` |
| 匿名 / 普通用户（`_redact`） | **同形状、值全空**：`{id, available, domains: {}, breaker: {open: [], until: null}, note: null}` |

- 所以 `_redact()` 必须重建 `dataProviders`（就地 copy + 逐行清空），
  且**保留键**——删键会破「同形状」契约，而 SSR 兜底字面量依赖它；
- `test_contract_codes.py::test_the_new_capabilities_fields_reach_the_wire_model` 那条
  反射断言的「顶层形状」不受影响（`dataProviders` 仍是同一个顶层键）。

**★v2（修正 P1-10）：`?deep=1` 必须跑在能 await 的地方。**
`/capabilities` 现在是**同步** `def`（FastAPI 把它放进线程池），线程池里没有 running loop，
`asyncio.to_thread` 会直接 `RuntimeError`。实施口径（二选一，选一就别选二）：

1. 把处理器改成 `async def capabilities(...)` + `await asyncio.to_thread(probe, ...)`，
   每 provider×域一个 `asyncio.wait_for(..., timeout=5.0)`；**同步阻塞的探测函数本体不变**；
2. 或保持 `def`，改用 `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=5)`。

推荐 1（与 `_try_chain` 的既有写法一致）。**无论选哪个，都不许在请求线程里裸调 akshare**
（一次 `.info` / 一次 push2 就是秒级阻塞，会把事件循环或线程池占死）。

**b) 报告行 `quality.dataQuality`（读者能看到的那一层）**

`ReportQuality` 追加一个可选字段（`schemas/report.py:373-393`），生产者是
`report_validator`（它本来就拿得到 snapshot）：

```python
class DataQualityQuality(BaseModel):
    source: str = ""                      # 行情主源
    domainSources: dict[str, str] = {}
    synthetic: bool = False
    stale: bool = False                   # 只由 bars_stale() 决定（P0-4）
    coverage: dict[str, Any] = {}
    issues: list[DataQualityIssue] = []   # ≤8 条，按 severity desc 截断
    issuesTruncated: bool = False         # ★v2（P2-4）：截断必须自己说出来
```

三条纪律：
1. **一个生产者**：`report_validator.data_quality_quality(snapshot)`，`snapshot.py` 不写第二份；
2. 反射守卫 `test_every_quality_key_has_a_response_field` 会强制它同时出现在响应模型里
   （这是既有纪律，写新键必须同步声明，否则 pydantic 静默丢弃）；
3. `web/lib/types.ts` 的 `ReportQuality` 镜像同步（依赖方向仍是后端 → 前端）。

**c) 合成数据生成的报告必须降级 —— 但只降「真实报告」（★v2 修正 P0-1）**

v1 写的是「`dataQuality.synthetic === true` → `ValidationOutcome.status = "degraded"`」，
无条件。这踩了 `validate_doc()` 里一条**显式**分支：

```python
elif mock:                     # report_validator.py:713-714
    status = "ready"
```

示例 / 演示模式（`features.mock` 默认 `true`、`agentMode=mock`）的每份报告都是规则从
**合成快照**装配出来的，按 v1 的规则它们在演示态**全部**变成 `degraded`；而前端
`ReportPanel.tsx:392` 把 `status === 'degraded'` 直接渲染成黄色「降级」徽标 ——
演示态的每一次生成都会挂一个**假警报**，`degraded` 这个词也就此贬值（历史教训同型：
`salvage` 一上来就降级时，"抢救"与"低接地"曾一度不可区分）。

**正确规则（一个生产者，一处判定）**：

```python
synthetic = bool(snapshot.get("dataQuality", {}).get("synthetic"))
# ① mock/演示报告：status 不动（既有分支保持"ready"），但 dataQuality.synthetic=true 照样上报；
# ② 真实报告（source == "aragon"）用了合成数据：这不是"演示"，是"用假价讲真故事"→ degraded。
elif synthetic and source != "mock":
    status = "degraded"
```

三条纪律（★v2 新增，修正 P1-11）：

1. **`quality.dataQuality` 只有一个生产者**：`report_validator.data_quality_quality(snapshot)`
   ——它拿到的是同一份 snapshot，因此 `synthetic` 只有一个来源，`status` 只读它一次；
   `snapshot.py` 不写第二份（前端只渲染，从不自己判）。
2. `quality.dataQuality.issues` 里加一条 `synthetic_backed`（severity=high）**两种情况都加**，
   于是"演示"与"误导"在数据层是同一条 code，差别只在 `status`。
3. 前端横幅**两种情况都显示**（演示态也需要告诉读者"这是演示数据"）：
   复用现有 `HistoricalBanner` / `SalvageNotice` 的同款样式槽位，
   文案放 `web/lib/copy.ts`（`copy.report.syntheticData`），组件里不写中文。
   **渲染位置：`web/components/report/ReportPanel.tsx` 的徽标行（`:391-417`）**，
   不是 `shell/ReportMeta.tsx`（见 P1-6）。

这一条**优先级最高**：现在一份合成价格的报告和一个真实报告在 UI 上完全无法区分。

**d) `run_diagnostics`** 追加 `data_source_summary(run)`：返回该 run 的
`{domainSources, issues, integrity, coverage}`，供 `/runs/{id}` 的运维视图与
`deploy/ops/status.sh` 打印。

### 26.3.9 Z8 — 端到端验收（生产真实生成 + ego-browser）

**a) 生成侧断言脚本**（`docs/plans/data-source-integrity/evidence/e2e_generate.py`，实施节点写成正式夹具）

```text
1. 生产 POST /api/v1/instruments/{CN:300750.SZ, CN:600519.SH, US:NVDA}/reports
   Authorization: Bearer $API_TOKEN（service 身份，见 README「用户与权限」）
2. 轮询 GET /api/v1/runs/{id} 至 succeeded/failed
3. 读 /opt/aragon-trading/api/workspace/runs/{id}/{snapshot.json,bars.csv,fundamentals.md,prompt.md}
4. 断言（见 AC-282…AC-290、AC-293/AC-294）：
   - `bars.csv` 尾行日期 == **最后一个已收盘会话**（★v2 修正 P1-3：盘中当日那根按
     Z3(a) 已被丢弃，所以「== 当日」与「丢弃未收完行」不可能同时成立；
     真值取自 akshare TX 的最后一个**完整**交易日，逐值比对）
   - 无 OHLC 不自洽行（逐根，带 1e-6 容差）
   - `valuation.peTtm` 非 null（或 issues 里有具名缺口）
   - `financials[0].revenueYoy` 非 null
   - `financials` 长度 == 8，且 `fundamentals.md` 的期间行集合与它逐项相同（★v2 / P1-4）
   - `dataQuality.synthetic == false`
   - `dataQuality.stale == false`（当日在盘中生成时也必须为 false；★v2 / P0-4）
   - `issues` 里没有 `api_drift`（出现即失败），ratelimit 允许但必须记名
5. 读 DB reports.quality_json：groundedRatio ≥ 0.85、dataQuality.synthetic == false
```

**b) 浏览器走查**（ego-browser，实施节点执行并落盘）

对 `https://aragontrading.online/CN:300750.SZ`：
① 打开工作台 → K 线最后一天的日期与报告里 `asOf`、正文最后日期**三处一致**；
② 报告估值章节出现真实倍数（不再是「缺失(proxy)」）；
③ 点来源抽屉 `[S1]…[S4]` 都能打开且有标题/发布方/日期；
④ 报告顶部出现「数据缺口」提示（若 `issues` 非空）或**没有**；
⑤ 截图存 `docs/plans/data-source-integrity/walkthrough/`（3 视口：1440×900 / 1024×768 / 390×844）。

---

## 26.4 文件 / 模块变更计划

### 26.4.1 `api/`（Z1–Z7）

| 文件 | 动作 | 意图（一句话） |
| --- | --- | --- |
| `api/app/services/market_data/base.py` | 改 | 加 `api_drift/payload` 两个 kind、`classify_provider_exception`、`session_date/canonical_session_ts`、`sanitize_bars`+`BarIntegrity`、`ValuationSnapshot`/`PeerRow` DTO、`FundamentalPeriod` 三个新字段 |
| `api/app/services/market_data/registry.py` | 改 | 能力矩阵、断路器、`allow_empty`（可选域）、sanitize 挂钩、`breaker_state()`、`available_providers()` 扩字段、去掉 120 字截断；**单飞与钉源降级为 M59b（可选）** |
| `api/app/services/market_data/akshare_provider.py` | 改 | 改名解析 + CN 三端点降级链 + 财务 12 期/5 字段 + 估值自算 + 同业（受断路器保护）+ 错误分类 |
| `api/app/services/market_data/yfinance_provider.py` | 改 | 会话日期归一、半成品行丢弃、`.info` 估值缓存、现金流与自算同比、search 带 sector/industry |
| `api/app/services/market_data/cache.py` | 改 | `write_bars` 写侧校验 + `purge_noncanonical()`（返回 `WriteReport`）、`bars_stale()` 判据、`normalize-bars --drop-invalid` 的读侧支撑、`is_bars_cache_fresh` 改用 `session_date`/`bars_stale` |
| `api/app/services/market_data/synthetic_provider.py` | 改（小） | 声明 `domains` 不含 `valuation`；`peers()` 保留但只在 `ALLOW_SYNTHETIC=1` 且**演示模式**下被调用 |
| `api/app/services/financials.py` | **新建** | 单季轧差 / 同比 / 环比 / 利润率的唯一实现（snapshot 与 numeric_claims 共用） |
| `api/app/services/snapshot.py` | 改 | 重写 `build_snapshot` 的域解析与 `dataQuality`；删 `pe=None` 死分支与 `SnapshotBundle.stale`（P2-3）；`_derive_peers` 去合成依赖；`write_bars_csv/_derive_price_series` 用 `session_date_str`；`write_sources_md` 渲染 issues；`write_fundamentals_md` 补 4 列与口径脚注（**行数 == `snapshot.financials` 长度**） |
| `api/app/services/indicators.py` | 改 | `_finite_filter` + 7 个新技术事实 |
| `api/app/services/run_manager.py` | 改 | 「资料自检」按 `issues` 逐条记录（不再用 `stale` 概括），`run.log` 文案带 code/domain |
| `api/app/services/report_validator.py` | 改 | `data_quality_quality(snapshot)`（唯一生产者）+ `ValidationOutcome.data_quality` 字段 + `quality` 属性发键；`synthetic_backed` → **仅非 mock** 降级 |
| `api/app/services/numeric_claims.py` | 改 | `derived_values` 补 3 条公式；`_single_quarter` 改为调用 `financials.py`；`RULES_VERSION` → `claims-v5` |
| `api/app/services/symbol_resolver.py` | 改 | 落库时用 `SymbolHit.attrs` 补 `sector/exchange`（缺则不写） |
| `api/app/routers/capabilities.py` | 改 | `dataProviders[].domains` + `breaker` + `note`（**并同步 `_redact()` 同形状清空**）+ `?deep=1`（admin/service 才真探测；处理器改 `async def`） |
| `api/app/routers/instruments.py` | 改 | `meta` 增 `sessionDate`；`stale` 改用 `bars_stale()`（不是 `latest_session_due`）；`reasonCode` 增 `integrity_dropped` |
| `api/app/routers/health.py` | 改 | 增 `dataSource: {providers, degraded}`（不含错误细节，公开路由不放运维信息） |
| `api/app/schemas/capabilities.py` | 改 | `DataProviderCapability` 扩 `domains/breaker/note`（全部可选，老响应形状不变） |
| `api/app/schemas/report.py` | 改 | `ReportQuality.dataQuality` + `DataQualityQuality`/`DataQualityIssue` |
| `api/app/schemas/bars.py` | 改 | `BarsMeta` 增 `sessionDate`、`integrity`（可选） |
| `api/app/services/run_diagnostics.py` | 改 | `data_source_summary(run)` |
| `api/app/config.py` | 改 | 新增 `provider_breaker_threshold`、`provider_pin_ttl_sec`、`valuation_cache_ttl_sec`（全部有默认值，`.env.example` 同步注释） |
| `api/app/cli.py` | 改 | 新增 `normalize-bars --market CN,HK [--drop-invalid] [--dry-run]` |
| `api/app/agent/prompts/research_task.md.j2` | 改 | 缺口话术 + 数据覆盖段 + code 化警告 |
| `api/app/agent/prompts/research_system.md` | 改 | 「缺失就写缺失」的一条纪律 |

### 26.4.2 `web/`（Z7 可见性）★v2：**6 个文件**（4 改 + 1 新建 + 1 类型镜像）

> ★v2 修正 P1-6 / P2-8 / P2-10：v1 的标题写「共 4 个文件 + 1 个类型镜像」但表里 5 行；
> 更要紧的是 `web/components/report/ReportMeta.tsx` **不存在**——那个名字的真身在
> `web/components/shell/ReportMeta.tsx`，而它是一个 `return null` 的**占位**，文件头写着
> 「本文件现在**不写任何东西**」。报告质量的徽标族（接地、抢救、历史版本）一直在
> `web/components/report/ReportPanel.tsx:391-417`，新徽标必须落在同一族里。

| 文件 | 动作 | 意图 |
| --- | --- | --- |
| `web/lib/types.ts` | 改 | 镜像 `DataQualityQuality`、`ReportQuality.dataQuality`；`BarsMeta.sessionDate`；`dataProviders` 是**内联字面量**（`:836`），就地扩 `domains?/breaker?/note?` |
| `web/lib/copy.ts` | 改 | `copy.report` 组加三条文案：`syntheticData` / `dataGap` / `crossSource`（集中管理，组件里不写中文） |
| `web/components/report/DataQualityBadge.tsx` | **新建** | 「数据质量」徽标：`synthetic`（红）/ `issues.length>0`（黄）/ 干净（**不渲染**），点击展开 issues 列表（`code` + `hint`） |
| `web/components/report/ReportPanel.tsx` | 改 | 在既有徽标行（`:391-417`，`degraded` / `mock` / `TrustBadge` / `SalvageNotice` 旁边）挂上 `<DataQualityBadge>`；`dynamic()` 懒加载，不进首屏预算 |
| `web/components/chart/CandleChart.tsx` | 改 | 轴标签与 crosshair 用 `instrument.tz` 渲染日期（不再用 UTC），`chartFocus` 的 `fromTs/toTs` 按会话日边界计算 |
| `web/components/shell/EndpointBadge.tsx` | 改 | 展开后多一段「数据源按域健康」，直接消费 `capabilities.dataProviders[].domains`；**空对象 = 脱敏/无数据，显示「未探测」而不是「全部不可用」** |

**`web/components/shell/ReportMeta.tsx` 不动**（它现在是、也继续是一个显式占位；
若将来要把「离线副本时间」以外的东西放进去，先按它文件头的说明判断归属）。

### 26.4.3 契约与文档

| 文件 | 动作 | 意图 |
| --- | --- | --- |
| `contracts/data_source_codes.json` | **新建** | 数据缺口码表（code/domain/severity/hint），后端产、前端读、测试校验「产出码 ⊆ 码表」 |
| `docs/plans/data-source-integrity/spec.md` | **本文件** | 设计 |
| `docs/plans/data-source-integrity/evidence/*` | 已建 | 取证脚本与说明（只读） |
| `docs/plans/data-source-integrity/walkthrough/*` | 实施节点产出 | ego-browser 截图证据 |
| `README.md` | 改 | 「数据源」一节补：改名漂移/限流/断路器/会话日期口径/`normalize-bars` 运维命令 |
| `api/README.md` | 改 | market_data 模块职责加 `financials.py` 与「一个域一个来源」纪律 |
| `.env.example` | 改 | 三个新配置项 + `DATA_PROVIDERS` 的推荐值说明 |

---

## 26.5 接口设计

### 26.5.1 `GET /api/v1/capabilities`（只追加）

```jsonc
{
  "dataProviders": [
    {
      "id": "akshare",
      "available": true,
      "domains": {                                  // ★新增，可缺失（老客户端忽略）
        "bars":         {"ok": false, "code": "ratelimit", "latencyMs": 990, "checkedAt": "…"},
        "quote":        {"ok": false, "code": "ratelimit", "latencyMs": 610, "checkedAt": "…"},
        "fundamentals": {"ok": true,  "code": "ok",        "latencyMs": 1700, "checkedAt": "…"},
        "valuation":    {"ok": true,  "code": "ok",        "latencyMs": 0,    "checkedAt": "…"}
      },
      "breaker": {"open": ["bars", "quote"], "until": "2026-11-20T21:09:11Z"},   // ★新增
      "note": "stock_zh_a_hist → stock_zh_a_hist_em（1.20.46 改名）"             // ★新增
    },
    {"id": "yfinance", "available": true, "domains": {"bars": {"ok": true, "...": "..."}}},
    {"id": "synthetic", "available": true, "domains": {"bars": {"ok": true, "...": "..."}}}
  ]
}
```

- `?deep=1`：admin（`engine.probe` 权限）或 service 身份才**真探测**；
  其他身份 / 无该参数 → 只回缓存（10 分钟 TTL）；
- 探测动作：每 provider × 每域一次**最小调用**（`bars`：`range=5d` 的已知标的；
  `fundamentals`：1 期；`valuation`：一次 `.info`），**在能 await 的地方**带 5s 超时
  （★v2 / P1-10：处理器要么改成 `async def` + `asyncio.to_thread`，要么用
  `ThreadPoolExecutor.result(timeout=5)`；同步 `def` 里调 `asyncio.to_thread` 会 `RuntimeError`）；
- 严格禁止：探测本身触发 push2 限流升级 → `akshare` 探测结果缓存 10 分钟且断路器打开时**直接跳过**；
- **★v2（P0-3）：无 `engine.probe` 的主体拿到的必须是「同形状、值全空」**：

```jsonc
// 匿名 / 普通用户（_redact 之后）：键一个不少，值全部为空
{"dataProviders": [
  {"id": "akshare",  "available": true, "domains": {}, "breaker": {"open": [], "until": null}, "note": null},
  {"id": "yfinance", "available": true, "domains": {}, "breaker": {"open": [], "until": null}, "note": null},
  {"id": "synthetic","available": true, "domains": {}, "breaker": {"open": [], "until": null}, "note": null}
]}
```

前端读到 `domains: {}` 时显示「未探测」，**不许**渲染成「全部不可用」（空 ≠ 坏）。

### 26.5.2 `GET /api/v1/instruments/{id}/bars`（`meta` 追加 2 键）

```jsonc
"meta": {
  "...": "现有 10 键全部保留",
  "sessionDate": "2026-11-20",                       // ★新增：最后一根 bar 的会话日
  "integrity": {"droppedOhlc": 1, "droppedIncomplete": 1, "samples": [...]}  // ★新增，可缺失
}
```

`reasonCode` 追加一个值 `integrity_dropped`（丢弃过异常 bar，且当前序列仍可用）。

### 26.5.3 `GET /api/v1/reports/{id}`（`quality` 追加 1 键）

```jsonc
"quality": {
  "...": "现有 10 键全部保留",
  "dataQuality": {                                   // ★新增，老报告为 null
    "source": "yfinance",
    "domainSources": {"bars": "yfinance", "fundamentals": "akshare",
                      "valuation": "yfinance:info", "peers": "yfinance"},
    "synthetic": false,
    "stale": false,
    "coverage": {"deliveredBars": 728, "firstSession": "2023-11-20",
                 "lastSession": "2026-11-20", "sinceListing": false},
    "issues": [{"code": "cross_source_fundamentals", "domain": "fundamentals",
                "severity": "info", "message": "…", "hint": "…"}]
  }
}
```

### 26.5.4 内部契约（不是 HTTP，但必须钉住）

| 契约 | 形状 | 桩 |
| --- | --- | --- |
| `sanitize_bars(rows, market, now) -> (rows, BarIntegrity)` | 见 26.3.5，OHLC 断言带 `_EPS` 容差 | `api/tests/test_data_integrity.py` |
| `session_date(ts, market) -> date` | 唯一真源 | 表驱动：CN 16:00 UTC → +1 天；US 05:00 UTC → 同日 |
| `canonical_session_ts(ts, market) -> int` | CN/HK → 该会话 07:00 UTC；**US → 原样**；幂等 | `api/tests/test_session_dates.py`（★v2 / P0-2） |
| `bars_stale(last_session, market, now) -> bool` | 只比「最近已收盘会话」，盘中不撒谎 | `api/tests/test_session_dates.py`（★v2 / P0-4） |
| `cache.write_bars(...) -> WriteReport(written, rejected, purged)` | 写侧校验 + 非规范行清场 | `api/tests/test_market_data.py`（★v2 / P1-1） |
| `cache.purge_noncanonical(...) -> int` | 删本序列非规范戳行 | 同上 |
| `_try_chain(..., allow_empty=)` | 可选域的空是答案 | `api/tests/test_provider_chain.py`（★v2 / P1-8） |
| `financials.single_quarter(periods) -> list[SingleQuarter]` | 累计→单季 | 用 `300750` 的 8 期真实数字做金标（442,137,424,000 等） |
| `financials.yoy(periods) -> dict[str, float\|None]` | 同报告期对上年 | 表驱动 + `reported/computed` 两态 |
| `contracts/data_source_codes.json` | `{codes: [{code, domain, severity, hint}]}` | 产出码 ⊆ 码表（反射守卫） |

---

## 26.6 数据模型

### 26.6.1 数据库

**零 DDL。** 理由：`bars`/`quotes` 的 `source`、`reports.quality_json`、`runs.*` 都已够用；
`normalize-bars` 只做 `DELETE`（CN/HK 的旧口径行）。若实施时发现确实需要一个列，
必须走 Alembic 新迁移（`0009_*`），不允许 `create_all`。

### 26.6.2 快照（`snapshot.json`）—— 事实底座的唯一形状

只追加，不删改（老报告重放要用旧形状）。追加字段清单：

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `valuation.peSource` / `marketCapSource` | `str` | `reported / computed / none` |
| `valuation.components` | `object` | `{ttmNetProfit, epsTtm, bvps}`（自算时给出算式输入） |
| `valuation.coverage` | `object` | `{pe: "ok" 或 "missing"; pePercentile3y: "no_series"; pb 同上}` |
| `financials[i].revenueYoYSource` / `netProfitYoYSource` | `str` | `reported / computed / none` |
| `financials[i].revenueQoQ` / `netProfitQoQ` | `float\|None` | 单季环比（%） |
| `financials[i].singleQuarterRevenue` / `singleQuarterNetProfit` | `float\|None` | 单季还原值（元） |
| `financials[i].ocf` / `grossMargin` | 已有键，**本轮到真值** | 不再恒 n/a |
| `technicals.pricePercentile60/252`、`distanceFromHigh1y`、`distanceFromLow1y`、`atrPct`、`avgVolume20`、`volumeRatio20` | `float\|None` | 新增技术事实 |
| `performance.ret5d` / `ret6m` | `float\|None` | 新增区间表现 |
| `peers[i].source` | `str` | 真实来源，或 `peers` 整体为 `[]` |
| `dataQuality.*` | 见 26.3.6(b) | **追加 8 个键**（`domainSources` / `synthetic` / `lastSession` / `coverage` / `integrity` / `issues` / `issuesTruncated` + 收窄语义的 `stale`），★v2 修正 P2-4：v1 数成 7 个，漏了 `issuesTruncated` |

**★v2（P1-4 的配套纪律）：`financials` 固定 8 期。**
`snapshot.financials` 的长度恒为 `min(8, 可取期数)`；12 期取样只活在 `build_snapshot` 的
栈上。任何新的落盘文件（含 `fundamentals.md`）都必须是 `snapshot` 的**投影**，
不是第二份数据源。

### 26.6.3 码表（`contracts/data_source_codes.json`，新建）

```jsonc
{"version": 1, "codes": [
  {"code": "api_drift",                  "domain": "any",          "severity": "high",   "hint": "数据源库已改名/改签名，升级 provider 适配层或锁定版本"},
  {"code": "ratelimit",                  "domain": "any",          "severity": "warn",   "hint": "源限流，冷却期内已跳过；稍后自动重试"},
  {"code": "payload_invalid",            "domain": "any",          "severity": "warn",   "hint": "返回数据形状/值不可用，已丢弃"},
  {"code": "ohlc_dropped",               "domain": "bars",         "severity": "warn",   "hint": "丢弃了 OHLC 不自洽的 K 线，技术指标基于剩余序列"},
  {"code": "session_incomplete",         "domain": "bars",         "severity": "info",   "hint": "当日行未收完，已用上一完整交易日"},
  {"code": "coverage_gap",               "domain": "bars",         "severity": "warn",   "hint": "历史不足请求窗口；若是上市以来则属正常"},
  {"code": "insufficient_history",       "domain": "bars",         "severity": "warn",   "hint": "不足 60/252 根，MA60/年度指标不可用"},
  {"code": "cross_source_bars_quote",    "domain": "quote",        "severity": "warn",   "hint": "报价与 K 线不同源或不同复权口径"},
  {"code": "cross_source_fundamentals",  "domain": "fundamentals", "severity": "info",   "hint": "财务与行情不同源，口径可能不同"},
  {"code": "fundamentals_partial",       "domain": "fundamentals", "severity": "warn",   "hint": "财务字段有缺口（同比/现金流/毛利率）"},
  {"code": "valuation_missing",          "domain": "valuation",    "severity": "warn",   "hint": "无倍数来源，报告中不得写估值结论"},
  {"code": "valuation_partial",          "domain": "valuation",    "severity": "info",   "hint": "估值部分字段缺失（如 PB）"},
  {"code": "peers_unavailable",          "domain": "peers",        "severity": "info",   "hint": "无同业报价，不得写同业对比数字"},
  {"code": "synthetic_backed",           "domain": "any",          "severity": "high",   "hint": "本报告使用了合成演示数据，不能用于决策"},
  {"code": "provider_unavailable",       "domain": "any",          "severity": "high",   "hint": "该数据源不可用，已降级到链上后位"},
  {"code": "mixed_generation_purged",    "domain": "bars",         "severity": "info",   "hint": "本次写入顺手清掉了旧口径行（会话日期归一），序列已单一世代"},
  {"code": "issues_truncated",           "domain": "any",          "severity": "info",   "hint": "数据缺口过多，已按严重度截断到 8 条"}
]}
```

`hint` 直接进提示词的「建议：」与 `run.log`。

**★v2（修正 P0-1 / P0-3 / P1-1）：三处语义钉住，别再让 `severity` 一个人背两个含义。**

1. **`severity=high` 只对「非 mock 报告」意味着 `degraded`**。`synthetic_backed` 在
   mock/演示报告里照样写进 `issues`，但 `status` 保持既有分支的 `"ready"`；
   用假价讲**真实**故事才是 `degraded`（见 §26.3.8(c)）。
2. `provider_unavailable` / `payload_invalid` 等 **provider 层**的码**不进** `quality.dataQuality.issues`
   的对外面（它们可能是运维口径），报告里只出现**域级**缺口（`valuation_missing` /
   `peers_unavailable` / `cross_source_*` / `ohlc_dropped` / `session_incomplete` /
   `coverage_gap` / `insufficient_history` / `fundamentals_partial` / `synthetic_backed`）。
   两套码同表不同域，靠 `domain` 区分，测试按 `domain` 断言。
3. 新增两个码（★v2）：`mixed_generation_purged`（本次写入顺手清掉了旧口径行，`domain=bars`，
   `severity=info`）与 `issues_truncated`（缺口列表被长度预算截断，`domain=any`，`severity=info`）
   ——它们让 P1-1 / P2-4 的两件事在日志与报告里**可查**。

---

## 26.7 测试与验收标准

### 26.7.1 新增/改动的测试文件

| 文件 | 覆盖 |
| --- | --- |
| `api/tests/test_session_dates.py`（新） | `session_date` 表驱动：CN 16:00 UTC → 次日、CN 07:00 UTC → 同日、US 05:00 UTC → 同日、HK 08:00 UTC；`canonical_session_ts` 幂等（**US 原样返回**）；`bars_stale` 盘中/收盘后/周末三态 |
| `api/tests/test_data_integrity.py`（新） | `sanitize_bars`：NaN、inf、high<open、low>close、重复 ts、未收完当日行；**浮点在 `_EPS` 内不算违规**；`BarIntegrity` 计数与 samples 上限 |
| `api/tests/test_market_data.py`（改） | `write_bars` 拒写计数 + `purged` 计数（旧口径行一次写入即清）；`normalize-bars --dry-run` 只读不写；`--drop-invalid` 在 US 标的也生效 |
| `api/tests/test_provider_errors.py`（新） | `classify_provider_exception` 五类；改名消息**不被截断**；`api_drift` 不 retryable；断路器 2 次打开 + 冷却后半开 |
| `api/tests/test_provider_chain.py`（改） | 能力矩阵：不支持的域不进链；`allow_empty=True` 时空不抛、`source == ""`；**（若做 M59b）** 钉源命中/解钉、单飞只调一次 |
| `api/tests/test_akshare_provider.py`（新，全部用 monkeypatch 假 frame） | 改名解析（只有 `_em` 名字时用 em；只有 `_tx` 时用 tx）；成交量单位 ×100；12 期解析；4 个新财务字段；估值自算三态 |
| `api/tests/test_yfinance_provider.py`（新，假 frame） | 索引带 `Asia/Shanghai` 时 ts 归一；全 NaN 行丢弃；`.info` 缓存 6h；现金流字段 |
| `api/tests/test_snapshot_data_quality.py`（新） | 新 `dataQuality` 形状（含 `issuesTruncated`）；`stale` 只由 `bars_stale` 触发（口径告警与「丢弃当日半成品行」都**不得**置 stale）；`valuation.peTtm` 非 null 路径；`peers` 不再来自 synthetic；`fundamentals.md` 期间行 == `snapshot.financials` 期间 |
| `api/tests/test_price_consistency.py`（改） | 既有「一对数字同源」断言在日期归一后仍成立（末根 close == quote.last 等） |
| `api/tests/test_financials.py`（新） | 单季轧差与同比（用生产 8 期真实数字做金标）；跨年边界；`reported / computed` 两态 |
| `api/tests/test_indicators.py`（改） | 7 个新事实；含 NaN 输入时整根剔除、后续指标仍正确 |
| `api/tests/test_numeric_claims.py`（改） | 3 条新派生公式各自命中/未命中；`RULES_VERSION == "claims-v5"`，`0.9`/`MIN_FACT_CLAIMS`/`_precisions` 未变 |
| `api/tests/test_report_validator.py`（改） | `quality.dataQuality` 生产者；`synthetic_backed` → **非 mock** 才 `degraded`、**mock 仍 `ready`**；反射守卫 `test_every_quality_key_has_a_response_field` 仍绿 |
| `api/tests/test_capabilities_api.py`（改） | `domains/breaker/note` 形状；非 admin 请求不打上游（计数器桩）**且三键同形状全空** |
| `api/tests/test_data_source_codes.py`（新） | 产出 code ⊆ `contracts/data_source_codes.json`；两套码按 `domain` 分流（provider 层不进报告面） |
| `web/tests/data-quality-badge.test.tsx`（新） | 三种徽标态渲染（synthetic 红 / issues 黄 / 干净不渲染）+ 文案来自 `copy.ts` |
| `web/tests/chart-dates.test.tsx`（新） | 给定 CN bar ts，轴标签与 `epochToDate` 一致（不再早一天） |

### 26.7.2 验收标准（AC-252 – AC-292）

| ID | 判据 | 判据的判据（怎么测） |
| --- | --- | --- |
| AC-252 | `session_date()` 是唯一日期真源：**两种写法都禁**（`fromtimestamp(...).strftime("%Y-%m-%d")` 与 `.date().isoformat()`），只允许在 `base.py` 与白名单模块（`run_manager.py`/`ask_service.py` 的文件 mtime）出现 | 反射测试：遍历 `api/app` 的 `.py`，按白名单断言（★v2 修正 P2-7：只抓一种写法的 grep 守不住，`yfinance_provider.py:78` 就是另一种） |
| AC-253 | 给定 yfinance 的 `2026-11-20 00:00 +08:00` 行，`canonical_session_ts` 后 `session_date_str` 得 `2026-11-20`；**US 的 05:00 UTC 行归一后 `ts` 不变**（`canonical_session_ts` 幂等） | 单测表驱动（★v2 / P0-2：US 原样返回是断言的一部分，不是注释） |
| AC-254 | `bars.csv`、`priceSeries.dates`、`meta.sessionDate` 三处对同一根 bar 给出**同一个** `YYYY-MM-DD` | 单测：构造 snapshot + 写文件后逐处比对 |
| AC-255 | 生产 CN 标的的 `bars.csv` 尾行日期 == **最后一个已收盘会话**（与 akshare TX 的最后一个完整交易日同值；盘中生成时**不是**「今天」） | Z8 脚本断言（★v2 修正 P1-3：与 Z3(a) 丢弃未收完行一致） |
| AC-256 | `stock_zh_a_hist` 不存在时仍能出 A 股日线（走 `_em` 或 `_tx`） | 单测：monkeypatch 掉 `_em` 只留 `_tx` |
| AC-257 | 改名/限流消息**完整**进入 `prompt.md`（不被 120 字截断，含「请改用」与 changelog 片段） | 单测：断言 `len(message) > 200` 且包含固定子串 |
| AC-258 | `api_drift` 错误**不重试**（断路器打开，冷却期内不再调该 provider） | 单测：调用计数 == 1 |
| AC-259 | 同一 (provider, domain) 连续 2 次 ratelimit 后断路器打开，冷却后半开且只允许 1 次探测 | 单测：注入时钟 |
| AC-260 | `dataQuality.domainSources` 覆盖 ≥4 个域，且**二选一**成立：①（若做 M59b 钉源）bars/quote/fundamentals 同源；②issues 里给出 `cross_source_*` 具名条目 | 单测 + Z8 断言（★v2 修正 P1-9：钉源降级为可选，判据不再强依赖它） |
| AC-261 | 能力矩阵：`synthetic` 不参与 `valuation`；`yfinance` 不参与 `peers`；未声明的域**不进链**；`allow_empty=True` 的域空结果**不抛**、`source == ""` | 单测：注入只支持 bars 的假 provider |
| AC-262 | 全链失败时报错消息含**每个 provider 各自的失败原因**（不截断、不合并成一句） | 单测：三 provider 全抛不同错 |
| AC-263 | `stock_zh_a_hist_em` 返回的 `成交量`（手）×100 = 股，且 `BarsResult.attrs.unit == "shares"` | 单测 + 与 TX 的同日 volume 比对（比例 1.0） |
| AC-264 | 财务解析出 `ocf/grossMargin/netMargin/revenueYoy/netProfitYoy` 五字段（akshare 真 frame 的 80 行样本作金标） | 单测（离线 frame 夹具，不打网络） |
| AC-265 | 8 期展示 + 12 期取样：`financials` 长度 8，`revenueYoY` 最近 4 期非 null；**且 `fundamentals.md` 的期间行集合 == `snapshot.financials[*].period`（12 期绝不落盘）** | 单测（12 期夹具）（★v2 / P1-4） |
| AC-266 | 单季轧差金标：`300750/001232` 的 `singleQuarterRevenue` 与人工计算逐值一致（±1 元） | 单测 |
| AC-267 | 同比 `reported / computed` 两态可区分，且 `computed` 的算式在 `components` 里可复算 | 单测 |
| AC-268 | yfinance 半成品行（close=NaN 或 open=前收且 high<open）**不入库**，且 `integrity.sessionIncomplete` 记名 | 单测 + 生产 `bars.csv` 无此类行 |
| AC-269 | `sanitize_bars` 丢弃的每一根都能在 `integrity.samples` 里找到（≤3 条，含日期与四价） | 单测 |
| AC-270 | 生产库 `bars` 表 OHLC 不自洽行数 == 0（`normalize-bars` 默认模式 + **`--drop-invalid` 全市场**跑完；含 US 标的） | Z8 脚本跑 `db_probe3.py` 的对应查询（★v2 修正 P1-2：只清 CN/HK 的戳到不了这条） |
| AC-271 | `indicators` 对含 NaN 的输入序列仍给出正确 MA/MACD（等于「剔除该根后」的结果） | 单测（两种输入对比） |
| AC-272 | `write_bars` 拒写非 finite / 非规范 ts 的行并返回计数；**同一次写入清掉本序列的非规范戳旧行（`purged` 计数可验证）** | 单测（★v2 / P1-1） |
| AC-273 | `stale` 只由 `bars_stale()` 触发：口径告警（`cross_source_fundamentals`）**不得**置 `stale=true`；**盘中丢弃当日半成品行也不得置 `stale=true`** | 单测（现有三份 CN 报告的快照形状 + 一个盘中时刻做夹具）（★v2 / P0-4） |
| AC-274 | `run.log` 的「资料自检」按 issue 逐条记录，每条含 `code` 与 `domain` | 单测：跑一次假 run |
| AC-275 | 生产 CN 标的 `snapshot.valuation.peTtm` 非 null（或 issues 有 `valuation_missing` 具名） | Z8 断言 |
| AC-276 | `valuation.pePercentile3y` 为 null 且 `valuation.coverage.pePercentile3y == "no_series"`（诚实空值） | 单测 |
| AC-277 | `peers` 的报价**不含**合成价格：`snapshot_peers_sources ∌ "synthetic"` | 单测：把合成 provider 强制放进链也应被拒 |
| AC-278 | `instruments.sector` 在搜索命中后非空（`300750/001232/688018` 三个真实标的） | Z8 脚本查库 |
| AC-279 | 派生目录新 3 条：`qoq:singleQuarter.revenue`、`distance:priceSeries.max@quote.last`、`percentile:priceSeries.close` 各自命中对应样本 | 单测（用生产报告里那 3 个被判 missing 的原值做夹具） |
| AC-280 | `groundedRatio` 不因本轮改动下降；同一份生产报告重放后 ≥ 0.85 | Z8：读 `reports.quality_json` |
| AC-281 | `RULES_VERSION == "claims-v5"`（★v2 修正 P1-5：改判据必须换版本号），且 `0.9` 阈值 / `MIN_FACT_CLAIMS` / `_precisions` / `APPROX_TOLERANCE` **未动** | `git diff` + 单测逐个常量断言 |
| AC-282 | `capabilities.dataProviders[]` 含 `domains/breaker/note`；非 admin 请求**不**触发上游探测（调用计数==0）；**匿名响应里三键同形状但全空（`domains == {}`、`breaker.until == null`、`note == null`）** | 单测 + Z8 生产实测（★v2 / P0-3） |
| AC-283 | `?deep=1` + admin 才真探测，且 akshare 探测结果缓存 10 分钟；**处理器是 `async def`（或 `ThreadPoolExecutor`），同步 `def + asyncio.to_thread` 的组合必须被否掉** | 单测（计数 + 时钟）+ 代码形状断言（★v2 / P1-10） |
| AC-284 | `quality.dataQuality` 出现在 `GET /reports/{id}`（不是只在库里）；老报告为 `null` 不报错；`ValidationOutcome.data_quality` 是**唯一**生产者 | 单测 + 反射守卫 |
| AC-285 | `synthetic_backed` → **仅非 mock** 报告 `status == "degraded"` 且 `issues` 含该 code；**mock/演示报告的 `status` 仍为 `"ready"`**（既有分支不许被踩） | 单测（两条用例成对，★v2 / P0-1） |
| AC-286 | 前端 `DataQualityBadge` 三态徽标：synthetic（红）/ issues（黄）/ 无（不渲染），挂在 `ReportPanel` 徽标行 | vitest（★v2 / P1-6：不是 `ReportMeta`） |
| AC-287 | 前端 K 线轴标签与 `epochToDate` 对同一根 bar 给出同一日期（CN 标的） | vitest |
| AC-288 | `contracts/data_source_codes.json` 覆盖后端产出的**全部** code | 单测（反射） |
| AC-289 | 门禁：`pytest --collect-only` **≥ 实施节点开工时实测的基线（评审实测 = 2277 @ `02260d0`）**、`vitest` ≥ **669 passed / 1 skipped**、`tsc`+`eslint`+`ruff` clean、`next build` ok、首屏 JS ≤ 245kB | `npm run ci` 全绿（★v2 修正 P1-7：2232 是上一版的 passed 数，当门槛等于允许删 45 条） |
| AC-290 | 生产真实生成 3 个标的（`CN:300750.SZ`、`CN:600519.SH`、`US:NVDA`）全部 `succeeded`，且 `dataQuality.synthetic == false`、`dataQuality.stale == false` | Z8 脚本 |
| AC-291 | ego-browser 走查 5 步（日期三处一致 / 估值非空 / 来源可点 / 缺口提示 / 无控制台错误）在 3 个视口通过，截图落盘 | 走查清单 + 截图 |
| AC-292 | `README.md` / `api/README.md` / `.env.example` 三处同步了新配置项、口径与运维命令（含 `normalize-bars --drop-invalid`） | 人工复核 + `grep` |
| AC-293 | **（★v2 新增 / P0-4）** 给定「今日盘中 + 序列末根为上一已收盘会话」的夹具，`bars_stale()` 返回 `false`；缺上一已收盘会话时返回 `true`；周末返回 `false` | 单测表驱动（注入 `now`） |
| AC-294 | **（★v2 新增 / P1-1）** 库里同时存在 16:00 UTC（旧口径）与 07:00 UTC（新口径）同 source 行时，**一次 `write_bars` 之后只剩新口径行**（`purged > 0`），且 `bars.csv` 无重复交易日 | 单测 + Z8 生产抽查一个 CN 标的 |
| AC-295 | **（★v2 新增 / P0-2）** `canonical_session_ts` 对 `(CN, 16:00 UTC)`、`(CN, 07:00 UTC)`、`(US, 05:00 UTC)`、`(HK, 08:00 UTC)` 四组输入**幂等**，且 US 组返回值 == 输入 | 单测表驱动 |
| AC-296 | **（★v2 新增 / P1-4）** 无任一落盘文件包含 `snapshot.financials` 之外的期间：`grep -c "^| 20" fundamentals.md == len(snapshot.financials)` | Z8 脚本 + 单测 |
| AC-297 | **（★v2 新增 / P0-1）** mock 报告的 `status` 与 `dataQuality.synthetic` 同时存在且**不互相矛盾**（`ready` + `synthetic=true` 是合法组合） | 单测 + 前端 vitest 渲染断言 |

### 26.7.3 门禁纪律（沿用）

- `pytest --collect-only` 的数量**不得低于**改动前实测值（★v2 / P1-7：评审实测 = **2277** @ `02260d0`；
  实施节点开工时必须自己再测一次并把这个数写进 M59 的报告，`2232` 不再作为门槛）；
- 新账本必须能被同一条命令证伪：本轮的「证伪命令」是
  `db_probe3.py`（OHLC 违规数，含 US）+ `e2e_generate.py`（7 条断言）+
  `pytest -k "session or integrity or financials or data_quality"`；
- 合成 provider 的既有不变量（固定日历锚点）**一个字都不改**。

---

## 26.8 里程碑

| 里程碑 | 内容 | 依赖 | 完成判据 |
| --- | --- | --- | --- |
| **M59** | Z1 采集层骨架（kind / 分类器 / 能力矩阵 / 断路器 / `allow_empty` / sanitize 挂钩 / 完整消息）；**单飞与钉源不在本里程碑** | — | AC-256…AC-262、AC-289 的基线实测 |
| **M59b**（可选，不阻塞） | 单飞 + 钉源（★v2 / P1-9：仅当 M66 之后仍实测到同参并发重复请求时再做） | M59 | 若做：AC-260① + 单飞单测；若不做：写进 `api/README.md` 的「已知未做」 |
| **M60** | Z2 akshare：改名解析 + CN 三端点 + 财务 12 期/5 字段 + 估值自算 + 同业 | M59 | AC-263…AC-267、AC-296 |
| **M61** | Z3 yfinance 归一化（会话日期/半成品/OHLC/`.info`/sector） | M59 | AC-253、AC-268、AC-295 |
| **M62** | Z4 入库与缓存完整性（`purge_noncanonical` + `--drop-invalid`）+ `normalize-bars` 迁移 + `bars_stale` | M60/M61 | AC-269、AC-270、AC-272、AC-293、AC-294 |
| **M63** | Z5 事实底座补全（`financials.py`、snapshot 重写、indicators） | M60–M62 | AC-273…AC-278 |
| **M64** | Z6 提示词与接地（3 条派生公式、缺口话术、完整告警、`claims-v5`） | M63 | AC-257、AC-279…AC-281 |
| **M65** | Z7 诊断面与可见性（capabilities 含脱敏 / `quality.dataQuality` / 前端 6 文件 / 合成降级） | M63 | AC-282…AC-288、AC-297 |
| **M66** | Z8 端到端验收（生产 3 标的 + ego-browser + 文档） | 全部 | AC-289…AC-292 |

实施顺序的硬约束：**M59 → M61 必须早于 M62**（没有日期归一，迁移就是白删）；
**M60 可以与 M61 并行**（两个 provider 互不依赖）；**M64 必须在 M63 之后**
（派生公式要引用新事实）；**M62 的 `bars_stale` 必须先于 M65 的 `quality.dataQuality`**
（否则报告行会把盘中的假 `stale` 抄进 `quality`，前端再忠实渲染一遍）。

---

## 26.9 风险与缓解

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R1 | **akshare push2 在生产 IP 上长期限流**，改完名字仍拿不到 CN 日线 | A 股仍走 yfinance（但已是归一 + 干净数据） | 三端点降级链（em→tx→sina）+ 断路器（若做了 M59b 的钉源再叠加）；Z8 断言只要求「无 api_drift」，允许 ratelimit 具名 |
| R2 | `normalize-bars` 删 CN/HK 缓存行后，首次请求变慢 | 一次 3–5s 抖动 | 只删不建；`--dry-run` 先看规模；删完由自然读取重取（既有 `write_bars` 会重建整条序列）；上线后手动预热 3 个标的 |
| R3 | 会话日期归一改变 `ts`，**且** `RULES_VERSION` 升到 `claims-v5`，旧报告的 `snapshotHash` 与接地百分比都不再可比 | 老报告的 0.799 不能与新的 0.85 并排比较 | **★v2 修正**：`rules` 变了就**明说不可比**（这正是 `claims-v4`→`claims-v5` 存在的意义）。`quality.grounding.rules` 变成读者判断「能不能比」的唯一依据；`VersionSwitcher` 已按 `rules` 分组渲染；不回溯老报告 |
| R4 | `.info` 一次调用很贵，可能拖慢检索/生成 | `/search` 从 0.3s 变 1.5s | `.info` 只在 `valuation()` 与 `search()` 命中**已存在标的**时才取；6h 进程内缓存（若做 M59b 再加单飞）；`search()` 的 sector 补全放在**落库**路径（用户点进工作台时），不放在联想路径 |
| R5 | 估值自算（PE/PB）口径与卖方口径不同 | 读者误读 | 全部标 `peSource: computed` + `components`；`valuation.coverage` 写明；报告里 `[S1]` 引用来自快照（接地仍成立） |
| R6 | 新增 `quality.dataQuality` 忘记声明响应字段 → pydantic 静默丢弃（历史上犯过三次） | 前端永远拿不到 | 反射守卫 `test_every_quality_key_has_a_response_field` 已存在，新键会先红；M65 的判据里显式包含它 |
| R7 | 提示词加入「缺口话术」后，模型偷懒整段写「数据不可用」 | 报告变空洞 | 硬性约束里保留「每章至少一个块」，并要求「缺口 + 对结论的影响」成对出现；Z8 断言五章节齐全（`missingSections == []`） |
| R8 | 前端 K 线轴改用 `instrument.tz` 后，与 lightweight-charts 的 `UTCTimestamp` 语义打架 | 轴标签与 brush 不一致 | 只在渲染层加 `tickMarkFormatter`/`localization.timeFormatter`，**不改**喂给图的 `ts`；`chartFocus` 的边界用 `session_date_str` 反算（与本轮服务端口径同一个函数语义） |
| R9 | 断路器引入新的进程内状态，测试之间互相污染 | 偶发红 | 与 `_FETCHED_AT` 同一模式：`reset_registry()` 顺带清 `_BREAKERS`（若做 M59b 再加 `_PINNED`/`_INFLIGHT`）；`conftest` 已有调用点 |
| **R10**（★v2） | **合成降级踩掉 mock/demo 的 `ready` 分支**，演示态全挂「降级」徽标 | 演示功能看起来坏了，`degraded` 贬值 | 已从设计上收窄为「仅非 mock 报告降级」（§26.3.8(c)），并用 AC-285/AC-297 成对断言 |
| **R11**（★v2） | **忘了跑 `normalize-bars` ⇒ 同一交易日两根 bar**（旧 16:00 UTC + 新 07:00 UTC，同 source） | 比现在的坏行更糟，且会静默进指标 | 写侧自愈 `purge_noncanonical()`（§26.3.5(b)）+ AC-294 断言「一次写入即收敛」+ `mixed_generation_purged` 码可查 |
| **R12**（★v2） | **`/capabilities` 新键漏给匿名用户** | 公开运维面 | `_redact()` 同形状清空 + AC-282 断言匿名响应三键全空 |

---

## 26.10 明确不改（防止下游顺手改回去）

1. **不放宽 `read_bars` 的混源拒绝**（既有诊断 §6.3 第 3 条）：接缝处的假 K 线会立刻回来。
2. **不改合成 provider 的固定日历锚点**（`_WALK_ANCHOR/_KAPPA`）：那是 BUG-1 的复发路径。
3. **不动接地判据的结构与阈值 0.9**：本轮只补事实与 3 条命名公式；
   **但 `RULES_VERSION` 必须升到 `claims-v5`**（★v2 修正 P1-5：判据内容变了，
   版本号不跟着变就是骗读者；`0.9` / `MIN_FACT_CLAIMS` / `_precisions` 一个字不动）。
4. **不加报告文档字段**（`report_doc.schema.json` 12 键不变）：数据质量进 `quality` 信封。
5. **不新增 HTTP 路由**：避免动 `security/policy.py` 的策略表（漏登记进程起不来）。
6. **不用价格分位冒充 PE 分位**：`pePercentile3y` 诚实为 null + `coverage` 说明。
7. **不允许「为过测试而造数」**：既有诊断 R-5 已证明「`high >= prevClose`」是伪不变量；
   `sanitize_bars` 只断言**块内自洽**（`high >= max(open,close)`、`low <= min(open,close)`），
   且比较带 `_EPS` 容差（★v2 / P2-6）。
8. **不在前端修日期**：日期真源在服务端 `base.py`，前端只渲染。
9. **（★v2）不许把「合成降级」套到 mock/演示报告上**：`elif mock: status = "ready"` 是
   既有显式分支，`synthetic=true` 与 `status="ready"` 是**合法组合**（AC-297）。
10. **（★v2）不许让 `stale` 参与「盘中」的判断**：`stale` 只由 `bars_stale()` 决定，
    `latest_session_due()` 只用于缓存决策（§26.3.5(d)）。这两个问题问的不是一件事。
11. **（★v2）不许把 12 期取样落进任何文件**：`snapshot.financials` 与
    `fundamentals.md` 必须是同一个 8 期投影（§26.3.3(c)6、AC-296）。

---

## 26.11 自检清单（v2 评审后）

- [x] 每条根因都有 `path:line` 或实测命令/输出（§26.0 的 E1–E14）
- [x] 每个 Z 都有可机器判定的 AC（AC-252–AC-297），且 AC 都能用同一条命令证伪
- [x] 文件变更计划覆盖 api / web / contracts / docs 四类，且每个文件一句话意图
- [x] 接口只追加不改语义；老响应形状与老报告读取路径都不破；**匿名版同形状且值全空**
- [x] 数据模型零 DDL；清理给了 `normalize-bars [--drop-invalid] [--dry-run]`，且**写侧能自愈**
- [x] 与三份既有诊断的结论对齐，并显式继承其「不改」清单
- [x] 基线数字改为「实施开工时实测」（评审实测 2277 / 669 / 209.0kB），要求「不得低于」
- [x] 端到端验收落在**生产**上（真实生成 3 个标的 + ego-browser 走查），不是本地 mock
- [x] 明确了「不做」的 11 条，避免下游把已修好的东西改回去
- [x] 评审的 4 P0 + 11 P1 **全部就地修正**，并由 AC-293…AC-297 逐条钉住
- [x] 范围收敛：单飞与钉源降级为 M59b（可选、不阻塞），本轮主线仍是「让事实底座完整」

---

## 评审结论

**有条件通过。**

### 通过的理由

1. **根因是真的**：§26.0 的 14 条全部带 `path:line` 或可复跑脚本，评审逐条回到代码核对，
   **没有一条是转述或推测**（见 R-0 表）。用户报的现象（「数据对不上」「日志里数据源报错」）
   与 E1–E14 的链条闭合：接口改名 → 整链滑到 yfinance → 日期错一天 + 半成品行入库 →
   坏 bar 污染指标 → 估值/同业/同比缺席 → 报告只能写「缺失」。
2. **地基选对了**：会话日期作为唯一真源（服务端）、错误分类学、能力矩阵、结构化缺口
   `issues[]`、读者可见的 `quality.dataQuality` —— 这五件事都是在修「底座」，
   不是在数字上对齐。改前端只修好一个面板，这一点认得对。
3. **克制到位**：零 DDL、不新增路由、不加报告文档字段、不改接地判据结构、不放宽混源拒绝、
   不用价格分位冒充 PE 分位 —— 与三份既有诊断的「不改」清单一致，没有把上一轮修好的东西打开。
4. **可验收**：41→46 条 AC 全部是机器可判定的，且给了唯一的证伪命令
   （`db_probe3.py` + `e2e_generate.py` + 定向 `pytest -k`），并落在**生产**上。

### 通过所附带的条件（全部已落入 v2 正文，实施节点必须遵守）

| # | 条件 | 对应评审项 | 判据 |
| --- | --- | --- | --- |
| C1 | 合成数据降级**只降非 mock 报告**，`elif mock: status="ready"` 保持原样 | P0-1 | AC-285、AC-297 |
| C2 | `canonical_session_ts` **US 原样返回**并幂等；不许把 US 重算成 00:00 UTC | P0-2 | AC-253、AC-295 |
| C3 | `/capabilities` 新键必须进 `_redact()`：**同形状、值全空** | P0-3 | AC-282 |
| C4 | `stale` 只由 `bars_stale()` 决定；盘中丢弃当日半成品行**不得**置 `stale=true` | P0-4 | AC-273、AC-293 |
| C5 | `write_bars` 必须在同一次写入里清掉非规范戳旧行（不许依赖运维记得跑 CLI） | P1-1 | AC-272、AC-294 |
| C6 | `normalize-bars --drop-invalid` 覆盖**所有** market（US 的坏 bar 一样要清） | P1-2 | AC-270 |
| C7 | `bars.csv` 尾行 == **最后一个已收盘会话**（不是「今天」） | P1-3 | AC-255 |
| C8 | 12 期取样只在栈上；`snapshot.financials` 与 `fundamentals.md` 是同一个 8 期投影 | P1-4 | AC-265、AC-296 |
| C9 | `RULES_VERSION` 升到 `claims-v5`；`0.9`/`MIN_FACT_CLAIMS`/`_precisions` 不动 | P1-5 | AC-281 |
| C10 | 前端徽标落在 `web/components/report/ReportPanel.tsx` + 新建 `DataQualityBadge.tsx`；`shell/ReportMeta.tsx` 不动 | P1-6 | AC-286 |
| C11 | 门禁门槛用**实施开工时实测的 `--collect-only`**（评审实测 2277），不用 2232 | P1-7 | AC-289 |
| C12 | `_try_chain` 增 `allow_empty`：可选域的空是答案，不是「全链失败」 | P1-8 | AC-261 |
| C13 | 单飞与钉源降级为 M59b（可选、不阻塞 M59）；AC-260 改成二选一 | P1-9 | AC-260 |
| C14 | `?deep=1` 跑在 `async def`（或 `ThreadPoolExecutor`）里，不许同步 `def + asyncio.to_thread` | P1-10 | AC-283 |
| C15 | `quality.dataQuality` 只有 `report_validator` 一个生产者，`status` 只读一次 | P1-11 | AC-284、AC-285 |

### 留给实施的判断（不阻塞通过，但需要显式回答）

1. **R1（push2 生产 IP 限流）本轮可能修不掉**：改完名字后 A 股仍可能走腾讯或 yfinance。
   这正是设计允许的（三端点降级 + 具名 `ratelimit`），但 M66 的验收报告必须**写出实际用的是
   哪个端点**，不能只说「通过了」。
2. **`.info` 的 6h 缓存要不要落库**：现在设计是进程内（单 worker 成立）。若将来改多 worker，
   这里会变成一条未声明的假设——建议在 `api/README.md` 的「已知未做」里写一句。
3. **`financials.single_quarter` 的金标数字**（`442,137,424,000` 等）来自生产 8 期快照；
   实施时要重新抄一次并注明抓取日期，避免把「某个时点的报表」当成恒等式。

### 结论

v1 的方向正确、证据充分；但若按 v1 原文实施，会产出四个**行为级错误**
（演示态全量误报降级、US 时间戳规则自相矛盾、运维面漏给匿名访客、盘中恒报「数据陈旧」），
以及十一处**会在上线后才被发现**的缺口（漏跑迁移就产生重复交易日、US 坏行清不掉、
12 期数字被判未接地、`claims-v4` 标签撒谎、门禁门槛低于现状等）。
这些已在 v2 正文逐条修正并配了 AC。**修完之后可以进入实施。**

> 评审节点不改源码、不提交；本轮只修改本文件（`docs/plans/data-source-integrity/spec.md`）。
> 附带的一处格式动作：把本文件的换行统一为 LF，与仓库 `.gitattributes` 的
> `* text=auto eol=lf` 一致（原文件是 CRLF，提交时本就会被归一，提前对齐以免 diff 噪声）。
