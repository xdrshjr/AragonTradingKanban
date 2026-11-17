# 数据源链不同源：同一屏的两个价格可以来自两个 provider，而没有任何一致性判据

> 本轮（Iteration 2/3 · 第 5 次诊断）不再看"一个 provider 内部"的价格一致性（BUG-1/2/3/9/10/11 已修），
> 只看**provider 与 provider 的接缝**：`/quote`、`/bars`、`snapshot` 各自独立走链，
> 前端把三条结果拼成一块屏幕，而代码里没有任何一处校验"它们是不是同一个来源、同一个口径"。
>
> 本轮发现 5 个缺陷（1 个 P1 组合、3 个 P1、1 个 P2），全部给出可复跑的脚本与 `path:line` 引用。
> 本节点**不改代码**；§6 给出可直接照做的落点清单和可机器判定的验收判据。

## 1. 问题描述

产品对读者的承诺是"**一次生成，反复查看**"，屏幕上任意一处价格都能追到它的来源（README「数字溯源」一节）。
上一轮把 `synthetic` 的两个价格体系合并成一个（固定日历锚点 + 共享 walk），解决了"同一 provider 内部
两个价"。**但没有解决"两个 provider 之间两个价"**：

| # | 缺陷 | 严重度 | 一句话 |
|---|---|---|---|
| PCD-1 | 周/月周期在整条数据源链上都不存在 | **P1** | 三个 provider 的 `bars()` 第一行都是 `if timeframe != "1d": return []`，用户点「周」「月」必得 503，且错误被翻译成"所有数据源都失败了" |
| PCD-2 | `/quote` 与 `/bars` 各自独立走链，无来源一致性判据 | **P1** | 一次网络抖动就能让左栏与 K 线分属两个 provider（实测两种口径相差 21.7%），两个响应里都没有任何"来源不一致"的提示 |
| PCD-3 | 复权维度只存在于 `/bars`，`/quote` 没有这个维度 | **P1** | 点开「指标 → 后复权」两次点击，左栏 1,439.78 与图表 8,840.24 同屏并排，差 **+514%** |
| PCD-4 | 混合来源窗口 = 永久缓存未命中，且幽灵行永不清理 | **P1** | 只要窗口里留下**一条**另一来源的行，`read_bars` 之后每次都返回空 ⇒ 每次看 3 年都重打数据源（实测 3/3 次，1.9–2.9 s） |
| PCD-5 | 标列表的合成报价没有任何合成标记 | **P2** | AAPL 的 330.46 是 100% 合成价格，左栏与 K 线头部都当真实价格展示（"合成数据"徽标只存在于图表底部的 footer） |

这 5 条是同一个结构性根因的五个出口，§3 逐条给出证据。§5 对比三种修法，§6 给出推荐方案。

### 1.1 为什么这不是"再修一次上一轮的 BUG-2"

上一轮的修法是 **`pairSource`：价格、涨跌幅、日期三者取同一个来源**（`web/components/chart/PricePanel.tsx:113-125`），
它解决的是"**同一个面板内部**两个数字拼错"。本轮的破口在**面板之间**：

```
        ┌─────────────┐   GET /quote（有 60 s 自己的缓存）
 左栏 ──┤ useRailQuotes├──→  provider 可以是 A
        └─────────────┘
        ┌─────────────┐   GET /bars?adjust=hfq（有 15 min 自己的缓存、有自己的复权维度）
K 线 ───┤  PricePanel ├──→  provider 可以是 B
        └─────────────┘
```

`pairSource` 对这两条链路各管一段，它管不到"左栏这一条是 A、K 线这一条是 B"。

## 2. 复现步骤

### 2.1 前置（一次性）

```text
cd M:/takoAI/AragonTradingKanban
npm run setup
```

数据库与依赖就绪后，`.env` 保持默认（`DATA_PROVIDERS=akshare,yfinance,synthetic`、`ALLOW_SYNTHETIC=1`）。
本轮所有实测都在默认链上完成；`yfinance` 在本机未安装（`依赖不可用`），这正好构成"链上部分成员永久缺席"的真实条件。

### 2.2 启动（两个终端；API 必须单 worker）

```text
cd api
python -m uvicorn app.main:app --port 8000 --workers 1
```

```text
cd web
npx next dev --port 3100
```

> ⚠️ 用 3100 端口时必须把 `ALLOW_ORIGINS` 一起改，否则浏览器跨源被拦、K 线恒为 `0 根`（本轮踩过）：
>
> ```text
> cd api
> set ALLOW_ORIGINS=http://127.0.0.1:3100,http://localhost:3000,http://127.0.0.1:3000
> python -m uvicorn app.main:app --port 8000 --workers 1
> ```

浏览器打开 `http://127.0.0.1:3100/CN:600519.SH`。

### 2.3 复现 PCD-1：周/月周期必失败（10 秒，两次点击）

浏览器里：**「周期」→ 点「周」**（或「月」）。K 线区立刻变成：

```
行情加载失败
所有数据源都失败了: akshare: 空结果; yfinance: 依赖不可用; synthetic: 空结果
[重试]
```

而面板头部仍在显示上一个周期的价格（例如 `1,753.06 ▲ 2.4%`），footer 变成 `数据来源：— · 0 根`。
命令行版本（含耗时与状态码）：

```text
set PYTHONIOENCODING=utf-8
python .agentmesh/repro/timeframe_chain_probe.py
```

实测输出（04:22 UTC，CN 盘中）：

```text
== CN:600519.SH ==
  1d: 200   243 根  来源=akshare     1758 ms  cached=False
  1w: 503  code=data_provider_unavailable      11 ms
      message = 所有数据源都失败了: akshare: 空结果; yfinance: 依赖不可用; synthetic: 空结果
  1m: 503  code=data_provider_unavailable      11 ms
      message = 所有数据源都失败了: akshare: 空结果; yfinance: 依赖不可用; synthetic: 空结果

== US:AAPL ==
  1d: 200   262 根  来源=synthetic     21 ms  cached=True
  1w: 503  code=data_provider_unavailable      11 ms
  1m: 503  code=data_provider_unavailable      10 ms
```

> 注意 `1w` / `1m` 是「周」「月」周期（`api/app/schemas/bars.py` 的 `Timeframe`），**不是** 1 分钟。
> 11 ms 就返回 503 说明根本没有发网络请求 —— 三个 provider 都在第一行自行返回了空列表。

### 2.4 复现 PCD-2：同一屏两个来源、两个价格，且无人报警（注入脚本，离线可跑）

```text
set PYTHONIOENCODING=utf-8
python .agentmesh/repro/provider_chain_divergence.py
```

脚本用**真实的 FastAPI 应用 + 真实的 `/quote`、`/bars` 处理器**，只把 provider 链换成一个只答报价的
`alpha` 和一个只答 K 线的 `beta`（这正是"某 provider 对某一端点不可用"的真实拓扑）：

```text
GET /quote -> 200
   {'instrumentId': 'CN:600519.SH', 'last': 1000.0, 'changePct': 1.0, 'prevClose': 990.0, ...
    'updatedAt': '2026-11-17T04:21:45Z', 'source': 'alpha'}
GET /bars  -> 200
   meta= {'source': 'beta', 'barsCount': 31, ..., 'cached': False, 'stale': True,
          'warnings': ['alpha: notfound alpha: alpha has no bars']}

同一屏两个价格: 报价 1000.0（alpha） vs 图表 2000.0（beta） = +100.00%
meta.warnings = ['alpha: notfound alpha: alpha has no bars']   ← 只有"alpha 没有 K 线"，没有"来源不一致"
meta.stale     = True                                          ← 但这个 true 与跨源无关，是 provider 回退留下的
第二次请求: quote.source= alpha  bars.source= beta  bars.cached= True
```

三件事同时成立：①两个价格差 100%；②唯一的 warning 只是"alpha 没有 K 线"，**不含"两个端点来源不同"**；
③第二次请求 `bars.cached=True`，也就是说这个分叉会被缓存固化，不会自愈。

线上版本（只观测、不注入）可以直接看来源翻转的瞬间：

```text
set PYTHONIOENCODING=utf-8
python .agentmesh/repro/live_split_sampler.py 14 6
```

实测（04:24:54 → 04:26:39，14 次采样）：akshare 全程健康时 `来源不同的次数 = 0` —— 这是**正确**的结果，
也说明分叉不是恒定的，而是发生在**两个端点各自解析失败/恢复的时刻**。同一晚 3 分钟内确实观察到了这次翻转：

```text
04:16:47  GET /quote            -> akshare   last=1439.78   updatedAt=2026-11-17T04:16:47Z
04:18:22  GET /bars?range=1y    -> akshare   1439.78        cached=true
04:19:38  GET /bars?range=1y    -> synthetic 1753.06        cached=true   ← akshare 掉了
04:19:38  GET /quote            -> synthetic 1753.06
```

`/quote` 的缓存 TTL 是 60 秒（`api/app/services/market_data/cache.py:306` 的默认 `ttl=60`），
`/bars` 盘中 TTL 是 900 秒（`cache.py:64-67` + `.env` 的 `BAR_CACHE_TTL_SEC=900`）。
两条缓存按不同时钟过期，所以"翻转"必然不是原子的：谁先过期谁先换源，另一个还能继续用旧来源答 60 s / 15 min。

### 2.5 复现 PCD-3：两次点击让左栏与图表差 514%

浏览器里：**「指标」（齿轮）→ 点「后复权」**。同一屏：

```text
左栏  贵州茅台 600519.SH   1,439.78  ▼ 1.0%
头部                         8,840.24  ▼ 0.8%   CNY      ← 后复权末根收盘
footer 数据来源：akshare · 243 根 · 缓存 · 2026-11-17
```

接口对照：

```text
GET /quote                      -> last=1439.78  source=akshare
GET /bars?adjust=hfq&range=1y   -> bars[-1].c=8840.24  meta={source:akshare, adjust:hfq, cached:true, stale:false, warnings:[]}
```

量级用 `.agentmesh/repro/adjust_mismatch_probe.py` 复核：

```text
quote.last = 1439.78  source=akshare
  adjust=qfq  bars[-1].c=1439.78    vs quote.last 差异 = +0.00%
  adjust=none bars[-1].c=1439.78    vs quote.last 差异 = +0.00%
  adjust=hfq  bars[-1].c=8840.24    vs quote.last 差异 = +514.00%
```

> 一个反直觉但重要的实测结论：**qfq 与 none 在当前最新一根 bar 上完全相同**（前复权把历史重定基，不动最新一根），
> 所以"用 adjust=none 取报价"（`yfinance_provider.py:103-108`）并不会立刻坏；真正会坏的是 **hfq**，
> 而 hfq 正是 UI 提供的第三个选项（`PricePanel.tsx:40-44`）。这条必须写清楚，否则修复轮会去修错地方。

### 2.6 复现 PCD-4：一条幽灵行让 3 年窗口永远不命中

线上（只读，看 `meta.cached` 与耗时）：

```text
set PYTHONIOENCODING=utf-8
python .agentmesh/repro/holiday_ghost_rows.py
```

```text
== Part 1: three sequential /bars?range=3y calls ==
  #1 cached=False source=akshare    bars=726  stale=False    2423 ms
  #2 cached=False source=akshare    bars=726  stale=False    2944 ms
  #3 cached=False source=akshare    bars=726  stale=False    1883 ms
```

三次连续请求，三次都 `cached=False`、每次 1.9–2.9 s 打一次 akshare。库里现在就有混合窗口：

```text
== bars: rows per (instrument, timeframe, adjust, source) ==
CN:600519.SH     1d  qfq  akshare        n=726
CN:600519.SH     1d  qfq  synthetic-v2   n=57     ← 幽灵行

== series with more than one source (permanent cache miss) ==
{('CN:600519.SH', '1d', 'qfq'): [('akshare', 726), ('synthetic-v2', 57)]}
```

离线机制版（`--offline`，一次跑完，不依赖网络）：

```text
  after the synthetic fallback:   287 rows, single producer = synthetic
  after the real refill:         rows per producer = [('akshare', 286), ('synthetic-v2', 1)]
  read #1: 0 rows -> cached=False (source=None)
  read #2: 0 rows -> cached=False (source=None)
  read #3: 0 rows -> cached=False (source=None)
  ghost rows left behind: ['2025-12-08']
```

**一条**幽灵行（合成 walk 走了工作日、真实源没有该交易日的那天 —— 中国的法定假日）就足够了。

### 2.7 复现 PCD-5：左栏的合成价格没有标记

浏览器打开 `http://127.0.0.1:3100/US:AAPL`（本机 yfinance 缺席 ⇒ 行情整条链只有 synthetic）：

```text
左栏  Apple Inc. AAPL  330.46 ▲ 1.3%          ← 无任何"合成"标记
头部             330.46 ▲ 1.3%  USD
footer  数据来源：synthetic · 262 根 · 缓存 · 合成数据 · 2026-11-17   ← 徽标只在图表 footer
```

## 3. 根因分析（含证据）

五个出口共享一个根因：**"来源"(source) 是一个写入时的事实，不是一个请求路径上的约束**。

### 3.1 三个端点各自解析一次链，解析结果不共享、不约束

`api/app/services/market_data/registry.py:143-147` 的注册表是进程级单例，但每次调用仍然从头逐个试：

```python
# registry.py:133-137
async def quote(self, symbol: str) -> ChainResult[QuoteRow]:
    return await self._try_chain("quote", symbol, symbol_hint=symbol)

# registry.py:121-131
async def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> ChainResult[BarsResult]:
    result = await self._try_chain("bars", symbol, timeframe=timeframe, ...)
```

`_try_chain`（`registry.py:70-115`）没有任何"上一轮是谁答的"记忆，也没有"这个标的钉在哪个源"的概念。
`/quote`（`api/app/routers/instruments.py:138-183`）与 `/bars`（`instruments.py:59-135`）各调一次，
于是两个端点各自可能选中不同的 provider —— §2.4 的注入复现就是这个拓扑的最纯粹形态。

**并且没有任何一处把两次结果拿来比：**

```text
$ grep -rn "chain_quote\|chain_fund" api/app | sort
snapshot.py:130   chain_quote = await registry.quote(instrument.symbol)      ← 取到了来源，之后没再比较
snapshot.py:141   chain_fund  = await registry.fundamentals(instrument.symbol, 8)
snapshot.py:143   if chain_fund.source != source:
snapshot.py:144       warnings.append(f"财务数据来源为 {chain_fund.source}，与行情来源 {source} 不同，请注意口径。")
```

`snapshot.py:143-144` 恰好证明团队**知道**跨源必须提示：财务与行情不同源时会写一条 warning。
**唯独 `quote` 与 `bars` 这一对（唯一会同屏并排出现的两个数字）没有这条检查** —— `chain_quote.source`
取到之后直接被丢掉。这是本轮最便宜、收益最高的一个缺口。

> **R-2（评审新增证据 · 修根因表述）**：上面这句"两个端点各自可能选中不同的 provider"作为**接线事实**成立，
> 但作为**可达路径**被高估了：三家 provider 的 `quote()` 全部由自己的 `bars()` 派生，没有任何一家是"只答报价的源"。
>
> ```text
> api/app/services/market_data/akshare_provider.py:110-116   def quote(...): bars = self.bars(..., start_ts=now-14d, adjust="qfq")
> api/app/services/market_data/yfinance_provider.py:102-108   def quote(...): bars = self.bars(..., start_ts=now-14d, adjust="none")
> api/app/services/market_data/synthetic_provider.py:213-222  _QUOTE_TIMEFRAME/_QUOTE_ADJUST 写死 "1d"/"qfq"，quote() 取 _cached_walk 的尾部
> ```
>
> 也就是说：**同一个 provider 内部，quote 与 bars 只可能因为"窗口/时钟"或"复权口径"不同而不同**，
> 不可能因为"能力不同"而不同。因此 §2.4 的 alpha/beta 拓扑（只答报价的源 + 只答 K 线的源）在
> `DATA_PROVIDERS=akshare,yfinance,synthetic` 上**不可达** —— 它是注入构造出来的最坏情况，不是本机的现状。
>
> 默认链上真正可达的分叉机制只有两条，而 §5/§6 的方案 A（钉住）**两条都治不了**：
>
> 1. **超时不对称**：`PROVIDER_TIMEOUT_SEC=10`（`.env`），而 `/bars?range=3y` 实测 1.9–2.9 s、
>    akshare 抖动时很容易破 10 s，`/quote` 只取 14 天窗口则几乎不会。于是 `/bars` 回退到 synthetic、
>    `/quote` 仍是 akshare —— 这正是 §2.4 时间线（04:19:38）的形状。钉住 akshare 后 `/bars` 仍会超时、
>    仍会解钉回落，两个端点照样各说各话。
> 2. **两个缓存时钟**：`/quote` 60 s、`/bars` 盘中 900 s（§2.4 已给出）。钉住是"选源记忆"，
>    管不到"一条 15 分钟的旧行还能不能答"。**当前库里的对照就是这条**：`CN:600519.SH`
>    `quotes.source=akshare last=1439.78` 与 `bars(1d,qfq) 末根 close=1439.78` 完全相等（同源同值），
>    而 `bars(1d,hfq) 末根=8840.24` 与报价差 **+514.00%** —— 现网唯一稳定复现的两个数字，是复权口径（PCD-3），
>    不是 provider 分叉（PCD-2）。完整审计见 §7。

### 3.2 "空结果"被当成"故障"：不支持的表情被翻译成"全线宕机"

```python
# registry.py:108-110
if value is None or (isinstance(value, list) and len(value) == 0):
    warnings.append(f"{name}: 空结果")
    continue
# registry.py:112-115
raise ProviderError(
    ProviderErrorKind.unavailable,
    "所有数据源都失败了: " + "; ".join(warnings) if warnings else "数据源链为空",
)
```

三个 provider 对 `1w`/`1m` 的第一反应完全一致：

```python
# api/app/services/market_data/akshare_provider.py:70-72
def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
    if timeframe != "1d":
        return []
# api/app/services/market_data/yfinance_provider.py:74-76   同一行逻辑
# api/app/services/market_data/synthetic_provider.py:247-253 同一行逻辑
```

于是"这个周期没人支持"在链尾变成了 `ProviderErrorKind.unavailable`，被 `Unavailable`（`api/app/errors.py:150-153`）
映射成 **HTTP 503 `data_provider_unavailable`**，消息里还带着内部链路细节：

```text
{"code":"data_provider_unavailable","message":"所有数据源都失败了: akshare: 空结果; yfinance: 依赖不可用; synthetic: 空结果"}
```

读者看到的是"数据源全挂了"，而这与事实相反：数据源很好，是**产品不支持这个周期**。
前端的错误态直接把 `Error.message` 打到屏幕上（`PricePanel.tsx:183-196`），所以这条内部消息就是用户看到的文案。

连带问题：`registry.py:97` 的 `f"{name}: {exc.kind.value} {str(exc)}"` 与 `ProviderError.__str__`
（`base.py:24-27`，已经带 `provider` 前缀）叠加，产生 `alpha: notfound alpha: alpha has no bars` 这种双前缀警告
（§2.4 的实测输出里可见）。

### 3.3 写入只 upsert、不删除，而"代次"只给 synthetic 发了

```python
# cache.py:236-287（write_bars 的关键部分）
existing = {row.ts: row for row in session.execute(select(Bar).where(..., Bar.ts >= bars[0].ts)).scalars()}
for bar in bars:
    row = existing.get(bar.ts)
    if row is None: ... session.add(row)
    else:
        row.open = ...; row.close = ...; row.source = stored_source     # ← 覆盖"已知的那几天"
session.flush()
```

`session` 里 `ts >= bars[0].ts` 的行会被逐根覆盖，但**provider 这次没返回的那几天保持原样**。
真实源（akshare/yfinance）没有中国的法定假日：合成 walk 走了工作日（`synthetic_provider.py:172-195`，只跳过周末），
真实源没有那一根 ⇒ 那一根**永远不会被覆盖**。

代次退休只覆盖了合成数据这一种：

```python
# cache.py:119-124
def _storage_tag(source: str) -> str:
    return SYNTHETIC_TAG if source == _LEGACY_SYNTHETIC else source     # ← 只有 synthetic 有代次
# cache.py:147-160  读时退休：遇到旧代次 synthetic 行 → delete + 返回 []
```

真实源的"旧行"没有任何退休路径。而唯一能打扫的钩子是死代码：

```text
$ grep -rn "purge_instrument_bars" api
api/app/services/market_data/cache.py:344  def purge_instrument_bars(session, instrument_id) -> None:   ← 定义后无人调用
```

### 3.4 混源窗口在读取侧被判死，在写入侧没人负责

```python
# cache.py:161-168
if len({row.source for row in rows}) > 1:
    # Two producers spliced into one series ... Miss instead, so one producer refills the whole window.
    return [], None
```

上一轮加这条是为了挡住"接缝处的假 K 线"（BUG-9），方向正确；但它把**修复责任**留给了调用方，而调用方
只是"重打一次源"：

```python
# instruments.py:89-105
if not used_cache:
    chain = await get_registry().bars(...)
    cache.write_bars(...)      # 写了同一个窗口 → 幽灵行照旧活着 → 下次仍然是混源
```

于是状态机是稳定的死循环：`混源 → read_bars 返回 [] → 重打源 → 写入覆盖已知日期 → 幽灵行仍在 → 混源`。
代价是每次页面浏览 1.9–2.9 s 的同步外呼（§2.6 实测），以及每次都在向数据源计费/计流量。

### 3.5 前端：同一屏三个价格，三个真源，而且只有一个真源懂"复权"

```text
左栏   web/lib/useRailQuotes.ts:52    last: quote.last, changePct: quote.changePct     ← /quote，无复权维度
K 线   PricePanel.tsx:94-99           api.bars(instrumentId, timeframe, range, adjust)  ← /bars，有复权维度
报告   snapshot.py:102,116,120        adjust="qfq" 固定                                      ← 又一套
```

> **引用修正（评审）**：原文此处写 `snapshot.py:120,165`。`120` 是 `write_bars` 的 `adjust="qfq"`，
> 但 `165` 是 `valuation = _derive_valuation(...)`，与复权无关。三处 `adjust="qfq"` 的真实位置是
> `snapshot.py:102`（`read_bars`）、`116`（`registry.bars`）、`120`（`write_bars`）。

`RailItem` 的类型里连 `source` 都没有位置：

```ts
// web/components/shell/SymbolRail.tsx:15-22
export interface RailItem {
  instrument: Instrument;
  last?: number | null;
  changePct?: number | null;
  hasReport?: boolean;
  scheduled?: boolean;
}
```

`/quote` 的响应里也没有 `adjust`（`QuoteOut`），因为 `QuoteRow`（`base.py:77-87`）根本没有这个字段。
所以 hfq 视图下"左栏与图表差 514%"不是前端 bug，是**接口少了一个维度**。

另外两条时钟也不一致：rail 的 `staleTime: 30_000`（`useRailQuotes.ts:25-30`）比 K 线的 `staleTime: 60_000`
（`PricePanel.tsx:94-99`）快一倍，即使同一个 provider，两次刷新之间也存在结构性偏差窗口。

### 3.6 PCD-5 的根因：来源标签只挂在"图表"这个容器上

```tsx
// web/components/chart/PricePanel.tsx:231
{barsQuery.data?.meta.source === 'synthetic' ? <Badge tone="warn">{copy.chart.synthetic}</Badge> : null}
```

`grep -rn "合成|synthetic" web/components` 在 `.tsx` 里只命中这一行。左栏没有任何等价判断，
因此 README 里"过期数据不许冒充最新（R-19）"的同一条原则，在"伪造数据不许冒充真实"上没有被贯彻到左栏。

## 4. 影响面

| 缺陷 | 受影响的用户动作 | 直接后果 | 谁最先发现 |
|---|---|---|---|
| PCD-1 | 任何人在 K 线上点「周」「月」 | 两个按钮 100% 不可用；文案误导为"数据源全挂"；同屏头部还留着一个价 | 所有人，第一次点击 |
| PCD-2 | 数据源抖动/降级期间浏览任意标的 | 左栏与 K 线两个价（实测 21.7%），无提示；报告会把两个价同时写进去 | 细心的投研读者；报告自身已记录过一次（见下） |
| PCD-3 | 切到「后复权」（港股/美股/长期对比的常用口径） | 同屏两个价，差 514%；涨跌幅也不同（-1.0% vs -0.8%） | 任何做长期对比的人 |
| PCD-4 | 任何一次打开 3 年区间 / 生成报告（`snapshot` 同样判命中，`snapshot.py:106-111`） | 每次浏览都同步外呼 1.9–2.9 s；数据源限流风险 ↑；缓存形同虚设 | 运维/成本视角；用户感觉"图表慢" |
| PCD-5 | 打开任何本机数据源缺席的标的（本机全部美股） | 合成价格以真实价格的外观展示；`真实生成引擎` 横幅下更危险 | 采信价格的人 |

**报告侧的证据（产品自己写下的）**：`docs/diagnoses/chart-price-integrity/analysis.md` 之前那版报告在"数据质量"块里
自己写了这样一段（04:16 UTC 在 `http://127.0.0.1:3100/CN:600519.SH` 的「核心逻辑」首块可见）：

> 「快照 quote.last 1634.88 元、prevClose 1672.00 元，与 2026-11-16 日线收盘 1729.52 元、
> 2026-11-13 日线收盘 1721.63 元分别相差 5.47% 与 2.88%」

一个投研产品最贵的地方（报告正文）在替用户解释"为什么这两处价格不一样"。这正是 PCD-2 的用户可见形态：
模型拿到了跨源的 `quote` 与 `bars`，它唯一能做的就是把这个矛盾写进报告。**修 UI 修不掉它**。

> **R-3（评审新增证据 · 这条证据已过期，不能用来证明当前版本）**：`1634.88 / 1672.00` 与共享黑板上
> `BUG-3` 的**修复前**证据是同一组数（`600519.SH quote last=1634.88 prevClose=1672.0 dayHigh=1654.5`）。
> 也就是说这段报告文本是在 A-prime（固定锚点 + 共享 walk）**上线之前**生成的，它记录的是**旧合成代次**与 akshare 的差，
> 不是两个 provider 的分叉。当前库的对照（`api/data/app.db`，04:32 UTC 只读查询）：
>
> ```text
> quotes  CN:600519.SH  last=1439.78  source=akshare  updated_at=2026-11-17T04:26:01Z
> bars    CN:600519.SH  1d qfq 末三根 = 1439.78 / 1453.90 / 1435.26   source=akshare（726 行）
> bars    CN:600519.SH  1d hfq 末两根 = 8840.24 / 8911.19
> => quote.last == bars(1d,qfq) 末根  （同源同值，差 0.00%）
> => quote.last vs bars(1d,hfq) 末根  = +514.00%（PCD-3，不是 PCD-2）
> ```
>
> 所以 §4 这张表里的 PCD-2 一行**当前没有现网证据**：它的用户可见形态是"陈旧报告"，不是"今天的屏幕"。
> 修复轮不能用这条去论证优先级（PCD-3 相反，库里随手可查）。要么重新生成一份报告取证，
> 要么把 PCD-2 降级为"潜在缺陷（需注入或需要 10 s 超时才能复现）"。

## 5. 候选修复方案对比

只针对 PCD-2/PCD-3（同源与口径），PCD-1/4/5 的方案单列在 §6。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 评价 |
|---|---|---|---|---|---|
| **A. 每次请求钉住一个 provider（source pinning）+ 跨端点来源校验** | 注册表按 `(symbol, market)` 记住最近的赢家，`/quote` 与 `/bars` 在同一窗口内复用；`/quote` 响应加 `adjust`（或显式 `adjustApplied:false`），两侧都回 `sourceId`；来源不一致时把它写进 `meta.warnings` 并让 UI 显式提示 | 中（registry + 两个端点 + `QuoteOut`） | 中：钉住的源若整体失效，需有"解钉"出口，否则一个源挂了会拖住另一个端点 | 2–3 天 | **推荐**。它把"同源"变成接口事实，而不是前端约定 |
| **B. 服务端合成一个 `/instruments/{id}/screen` 端点** | 一次返回 `quote + bars + source + 一致性结论`，前端只渲染 | 高（新端点 + 两处调用方 + OpenAPI + 测试） | 低（单一真源），但会打破"每块面板自给自足"的现有结构（`useRailQuotes` 的注释明确写了这个取舍） | 4–5 天 | 最干净，但超出本轮余量；留作 v7 的候选 |
| **C. 只在 UI 层对齐（rail 改用 bars 末值）** | `useRailQuotes` 改成读 `/bars` 末根 | 低 | **高**：会重演 BUG-2 —— 把矛盾从"左栏 vs 图表"搬到"左栏 vs 报告"；而且 rail 有 12 行，一次 12 个 `/bars` 请求 | 半天 | **拒绝** |

针对 PCD-3 的三条独立选择：

| 方案 | 做法 | 评价 |
|---|---|---|
| C1 | `/quote` 增加 `adjust`，provider 用同一口径回答 | 正确，但 akshare/yfinance 的实时报价只有"不复权"口径，hfq 只能由 K 线序列算出来 |
| C2 | rail 在 `adjust != qfq` 时改显示 `/bars` 末根并标注"后复权" | **推荐**：口径由"当前图表"决定，而不是由"报价接口"决定；不与 C1 冲突，可先做 |
| C3 | 删掉「后复权」选项 | 拒绝：长期对比是投研刚需，删选项是产品降级 |

## 6. 推荐修复方案

### 6.0 为什么是这一套（推荐理由）

1. **它修的是"身份"，不是"数字"。** 五条缺陷里没有一条能靠"再对齐一次数字"消掉：PCD-2/3 是接口缺维度，
   PCD-4 是写入策略缺删除，PCD-1 是能力矩阵缺声明。把 `source` 从"写入时的事实"提升为"请求路径上的约束"
   （§6.1 第 6–9 条），这五条才会同时收敛。
2. **它复用了这个项目已经证明有效的两个模式。** 上一轮的成功经验是"单一真源 + 一处判定"：
   `pairSource`（面板内）与 `_public_source`（来源标号）。本轮把同一模式抬到"端点之间"，
   不引入新的架构概念，评审成本低。
3. **它不改数据语义。** `1w/1m` 由 `1d` 重采样而来（而不是引入分钟线/周线数据源），
   所以"同一日期永远同一价"这条刚建立起来的性质不会被打破（`_WALK_ANCHOR` 的注释就是防止这件事）。
4. **它把"看不见的失败"变成"看得见的提示"。** 现在跨源、混源、缓存脏三种状态在 UI 上完全不可见
   （§2.4 实测 warnings 里没有一条说来源不同）。方案 A 要求三者都进 `meta.warnings` / 徽标，
   符合项目既定原则："宁可说没找到，不装作找到了"（README「常见的『看起来像坏了』其实是设计」）。
5. **代价可控。** 落点集中在 4 个后端文件（`registry.py`、`instruments.py`、`snapshot.py`、`cache.py`）
   与 3 个前端文件（`useRailQuotes.ts`、`SymbolRail.tsx`、`PricePanel.tsx`），没有 schema 迁移
   （`bars.source` 与 `quotes.source` 已存在），符合"零 DDL"的既有约束。

### 6.1 五条缺陷的落点清单（供实施节点直接照做）

**PCD-1（周/月）—— 用重采样实现，而不是删按钮**

1. `api/app/services/market_data/base.py`：新增 `resample_daily(rows: list[BarRow], timeframe: str) -> list[BarRow]`，
   `"1w"` 按 ISO 周（周一为锚）、`"1m"` 按自然月聚合；`open=首根.open, high=max(high), low=min(low), close=末根.close,
   volume=sum, ts=该周/月最后一根 bar 的 ts`（与 `latest_session_due` 的日期语义保持一致）。
2. `registry.bars()`（`registry.py:121-131`）：当年份周期不被支持时，先取 `"1d"` 再重采样；**重采样失败才降级报错**。
   这样 `synthetic` 也自动获得周/月（离线演示同样完整）。
3. `registry._try_chain`（`registry.py:108-110`）：不要再用"空结果"表示"不支持"。改为在调用前就看
   `provider.supports(timeframe)` 的能力矩阵，不支持则**不进链**；全链都不支持 → `422 unsupported_timeframe`
   （新错误码，`detail.supported=["1d","1w","1m"]`），绝不 503。
4. `api/app/routers/capabilities.py`：把 `timeframes: ["1d","1w","1m"]` 加进 `features`/`limits`（前端据此决定按钮是否禁用）。
5. 顺手修 `registry.py:97` 的双前缀（`ProviderError.__str__` 已带 provider，外层不要再拼 `name` 之外的前缀两次）。

**PCD-2（跨端点不同源）—— provider pinning + 显式告警**

6. `registry.py`：新增 `_PINNED: dict[str, tuple[str, float]]`（`symbol → (provider, expires_at)`），
   在 `_try_chain` 成功后钉住赢家（建议 300 s，盘中可短）；`_try_chain` 允许传 `pinned_only=True`
   —— 钉住的源失败时**解钉并重试一次**，然后正常走链。
7. `instruments.py:138-183`：`/quote` 与 `/bars` 走同一个 `resolve_source(symbol)`；响应里回
   `meta.sourceId`（与 `meta.source` 同值，命名统一留给 v7）。
8. `snapshot.py:127-138`：照抄 `snapshot.py:143-144` 的写法，加上

   ```python
   if quote_row.source and quote_row.source != source:
       warnings.append(f"报价来源为 {quote_row.source}，与行情来源 {source} 不同，请注意口径。")
   ```

   并把 `dataQuality.quoteSource` 单独记一个字段（现在只有 `dataQuality.source`）。
9. `web/components/chart/PricePanel.tsx` + `web/lib/useRailQuotes.ts`：两个来源不一致时，头部显示一个
   warn 徽标（文案进 `web/lib/copy.ts`，例如"该标的的报价与 K 线来自不同数据源"），而不是静默。

**PCD-3（复权口径）—— 口径跟随图表**

10. `useRailQuotes.ts`：接受一个 `adjust` 参数；`adjust === 'qfq'` 时继续用 `/quote`（快、60 s TTL），
    `adjust !== 'qfq'` 时用 `/bars?adjust=hfq&range=1m` 的末根，并在该行加"后复权"角标。
    `PricePanel` 把当前 `adjust` 上抛给 `WorkspaceClient`（它已经在管 `view`/`range` 的 URL 状态，`WorkspaceClient.tsx:228-259`）。
11. `web/lib/useRailQuotes.ts:25-30` 的 `staleTime` 与 `PricePanel.tsx:94-99` 对齐（都用 60 s），
    否则同一 provider 下也存在结构性偏差。

**PCD-4（幽灵行）—— 同窗口单生产者**

12. `cache.write_bars`（`cache.py:236-287`）：写入前删除"同 `(instrument, timeframe, adjust)` 且
    `ts >= bars[0].ts` 且 `source != 本次来源`"的行。语义是"这段窗口从此由本次来源负责"，
    正好与 `read_bars` 的混源拒绝（`cache.py:161-168`）配对，让 `[]` 变成一次性事件而不是永久状态。
13. `cache.read_bars`（`cache.py:161-168`）保持"混源即未命中"，但返回原因（`return [], None, "mixed_source"`），
    由 `/bars` 写进 `meta.warnings` —— 现在读者完全看不到"你的缓存脏了"。
14. `purge_instrument_bars`（`cache.py:344-345`）：要么接上调用（`/instruments/{id}?force` 或一个维护任务），
    要么删除。死代码比没有更糟：下一个人会以为已经有清理路径。
15. 迁移/维护脚本：一次性清掉现存的混源窗口（本机 `CN:600519.SH` 的 57 条 `synthetic-v2` 幽灵行）。
    **不能"顺手"删除所有 synthetic-v2 行** —— 只删"同一 ts 上存在其他来源"的那些（§2.6 的离线脚本就是判据）。

**PCD-5（合成价格无标记）**

16. `SymbolRail.tsx:15-22` 的 `RailItem` 增加 `source?: string`；`useRailQuotes.ts:52` 把它带上来；
    rail 行内 `source === 'synthetic'` 时显示与 `PricePanel.tsx:231` 同一个徽标组件。
17. 顺手：`/quote` 在 provider 全挂时返回的 `source: "akshare (cached)"`（`instruments.py:167`）是一个
    把状态塞进来源字段的写法，建议改成 `source: "akshare"` + `cached: true`（与 `/bars` 的 `meta.cached` 一致）。

### 6.2 可机器判定的验收判据（建议直接写成测试）

| # | 判据 | 判据的判据（怎么测） |
|---|---|---|
| AC-P1a | `GET /bars?timeframe=1w` 与 `?timeframe=1m` 恒为 200，且 `barsCount` 等于 `1d` 序列按周/月的桶数 | 表驱动测试：同一 3 年窗口，`1w` 桶数 = ISO 周数，`1m` 桶数 = 月份数 |
| AC-P1b | 任何 `timeframe` 都不会返回 503 `data_provider_unavailable` | 逐枚举值断言 `status != 503`；不支持的组合只能是 422 `unsupported_timeframe` |
| AC-P2a | 在"只答 quote 的 A + 只答 bars 的 B"注入下，`/quote` 与 `/bars` 的 `source` 必须相同 | 把 `.agentmesh/repro/provider_chain_divergence.py` 改写成 pytest，断言 `quote.source == bars.meta.source` |
| AC-P2b | 来源不一致时，`/bars.meta.warnings` 或 `/quote` 必须带一条可读的"来源不一致"（不许只有 provider 内部消息） | 断言 warning 文本包含"来源"且不以 provider 名开头 |
| AC-P3a | `adjust=hfq` 下，rail 与 header 的价差 = 0.00% | Playwright：切 hfq → 读 DOM 两个数字，断言相等（现有 `web/tests/price-panel.test.tsx` 同款写法） |
| AC-P4a | 混源兜底后再回真源，(instrument, timeframe, adjust) 上只剩一个 `source` | 离线脚本断言 `select source,count(*) group by source` 只有一行 |
| AC-P4b | 连续 3 次 `/bars?range=3y`，第 2、3 次 `cached=true` 且 < 50 ms | 复用 `.agentmesh/repro/holiday_ghost_rows.py` Part 1，写进 API 测试（需 monkeypatch provider 计数） |
| AC-P5a | rail 每一行 `source==='synthetic'` 时必须出现合成徽标 | vitest：给 `useRailQuotes` 输入 synthetic quote，断言 DOM 有徽标 |

### 6.3 明确**不改**的四项（防止下游"顺手"改回去）

1. **不要**把 synthetic 的 `_WALK_ANCHOR` / `_KAPPA` 改回去 —— 那是 BUG-1 的复发路径（`synthetic_provider.py:137-145`）。
2. **不要**把 `pairSource` 改回 "价格取 quote、涨跌取 bars" —— 那是 BUG-2（`PricePanel.tsx:105-125`）。
3. **不要**为了消除"混源未命中"而**放宽** `read_bars` 的混源拒绝 —— 接缝处的假 K 线（-15.33%）会立刻回来。
   要修的是写入侧（§6.1 第 12 条），不是读取侧的守门人。
4. **不要**用"给低复权/高复权数字加个 `~` 号"来处理 PCD-3 —— 514% 的差不是精度问题，是口径问题，必须换源。

## 7. 评审复核（评审节点新增证据与修正）

本节由评审节点补写。**没有改动上一节点的任何结论文字**，只做三件事：把结论重新测一遍、把缺失的
`path:line` 与实测输出补上、把"修法本身会不会带来新缺陷"逐条压测。全部探针可复跑、全部离线：

```text
python .agentmesh/repro/provider_chain_divergence.py      # PCD-2 注入拓扑（本次重跑，输出见 7.1-R11）
python .agentmesh/repro/holiday_ghost_rows.py --offline    # PCD-4 幽灵行机制（本次重跑）
python .agentmesh/scratch/review_pcd_probe.py              # 本节 A/B/C/D/E 五组（本轮新增）
```

复核结论一句话：**五个缺陷的机制全部成立，但优先级排序、两条验收判据、四个落点条目需要修正**；
其中两处会直接把新缺陷带进来（R-1、R-5），一处让判据永远无法通过（R-6）。

> **编号说明**：`R-2`（§3.1 内联）与 `R-3`（§4 内联）是就地补的证据块；`R-1`/`R-4`…`R-9` 是
> §7.2 的"反例与修法缺陷"；`R10`…`R15` 是 §7.1 的"重测证据"。三类都不改上一节点的任何结论文字。

### 7.1 根因证据链逐条审计

| # | 被审的说法 | 是否"代码在失败路径上" | 复核结论 |
|---|---|---|---|
| PCD-1 | 三个 `bars()` 对 `1w/1m` 返回 `[]` → 链尾 `unavailable` → 503 | ✅ 成立 | 200/503 实测复现，且**只用 synthetic 也复现**（见 R10）|
| PCD-2 | `/quote` 与 `/bars` 各自走链 → 可为不同源 | ⚠️ 接线成立，**可达性被高估** | 三家 provider 的 `quote()` 都派生自 `bars()`（R-2），注入拓扑不可达 |
| PCD-3 | 复权维度只在 `/bars` 上 | ✅ 成立且现网可查 | `+514.00%` 从库里直接复算（R-3/R14）|
| PCD-4 | 混源窗口 = 永久未命中 | ✅ 成立，且比报告更广 | 5 个 range 里 4 个命中混源；57/57 幽灵行无对手方（R-5）|
| PCD-5 | 左栏合成价无标记 | ✅ 成立 | `grep` 只在 `PricePanel.tsx:231` 命中，`RailItem` 无 `source` 字段 |

**R10 · PCD-1 的离线形态（报告没有给）**：用真实 app + `DATA_PROVIDERS=synthetic` 单跑，
连"离线演示"这条最该完整的路径也是坏的。这很重要，因为修复轮如果只改真源，演示模式仍然是 503：

```text
$ python .agentmesh/scratch/review_pcd_probe.py     # A 段
  timeframe=1d: 200 code=-                          bars=22
  timeframe=1w: 503 code=data_provider_unavailable  bars=-
       msg=所有数据源都失败了: synthetic: 空结果
  timeframe=1m: 503 code=data_provider_unavailable  bars=-
       msg=所有数据源都失败了: synthetic: 空结果
```

错误信封是 `{"error": {"code", "message", "requestId", "detail"}}`（`api/app/errors.py:44-50`）、
`Unavailable` 映射 `503 data_provider_unavailable`（`errors.py:150-153`）、前端把这个 `message`
原样打到屏幕上（`PricePanel.tsx:185-188`）—— 三处引用全部核对无误。

**R11 · PCD-2 的注入拓扑（本次重跑，与报告一致）**：

```text
GET /quote -> 200  last=1000.0  source='alpha'
GET /bars  -> 200  meta.source='beta'  barsCount=31  cached=False  stale=True
                   warnings=['alpha: notfound alpha: alpha has no bars']
同一屏两个价格: 报价 1000.0（alpha） vs 图表 2000.0（beta） = +100.00%
第二次请求:      quote.source=alpha  bars.source=beta  bars.cached=True
```

双前缀 `alpha: notfound alpha: alpha has no bars` 也逐字复现 —— `registry.py:97` 的
`f"{name}: {exc.kind.value} {str(exc)}"` 叠在 `ProviderError.__str__`（`base.py:24-27`）的
`f"{provider}: {message}"` 之上，`§3.2` 的判断成立。

**R12 · PCD-2 的可达路径被漏掉了（新增）**：报告说"一次网络抖动"就够，但没有给出机制。
本机的两个机制是具体的，而且都**不是选源问题，所以钉住（方案 A）治不了**（详见 R-2）：

1. `PROVIDER_TIMEOUT_SEC=10`（`.env`）对上 `/bars?range=3y` 实测 1.9–2.9 s（§2.6 Part 1），
   akshare 抖动时破 10 s 很常见；`/quote` 只取 14 天（`akshare_provider.py:114`），几乎不会超时。
2. 两条缓存 60 s / 900 s（`cache.py:306`、`cache.py:64-67`），钉住记录的是"谁赢过"，
   不是"这条 15 分钟前的行还能不能答"。**现网证据就在库里**：`quote.last=1439.78` 与
   `bars(1d,qfq)` 末根 `1439.78` 完全相同（同源同值），而 `bars(1d,hfq)` 末根 `8840.24` 差 `+514.00%`。

**R13 · PCD-3 的根因比报告写的更硬（新增三行引用）**：不是"`/quote` 少一个维度"这么笼统，
而是**三家 provider 各自把复权口径写死了**，而 `/bars` 给用户三个选项：

```text
akshare_provider.py:114-115    bars(..., adjust="qfq")     ← 报价永远前复权
yfinance_provider.py:107       bars(..., adjust="none")    ← 报价永远不复权
synthetic_provider.py:213-214  _QUOTE_TIMEFRAME="1d"; _QUOTE_ADJUST="qfq"
synthetic_provider.py:159      _rng(symbol, f"bars-{timeframe}-{adjust}")  ← walk 按 adjust 分 RNG 流
```

最后一行说明：对合成标的（本机全部美股）切到 `hfq`，左栏与图表的差**不是固定的 514% 而是随机量**，
因为三条复权序列来自三条独立随机流。这进一步说明 C1（给 `/quote` 加 `adjust`）不可能便宜实现 ——
真实报价源只有一种口径，hfq 只能由 K 线序列算出来（§5 的判断正确，但理由必须写成这一条）。

**R14 · PCD-3 的现网账（库里只读可查）**：

```text
quotes  CN:600519.SH  last=1439.78  prev_close=1453.90  source=akshare
bars    CN:600519.SH  1d qfq  末三根 1439.78 / 1453.90 / 1435.26   (akshare, 726 行)
bars    CN:600519.SH  1d hfq  末两根 8840.24 / 8911.19            (akshare, 243 行)
bars    CN:600519.SH  1d none 末根 1439.78                         (akshare, 243 行)
=> qfq 与 none 在最新一根上相同（§2.5 的反直觉结论，实测确认）
=> hfq 相差 +514.00%（PCD-3 唯一稳定复现的两个数字）
```

**R15 · PCD-4 的机制与"永久"二字都成立，但影响面被低估（新增）**：混源窗口不是"3 年才会中"，
而是**除 1 个月外的所有 range 全中**（当前库，57 条幽灵行分布在 3 年里）：

```text
range=1m   窗口含  0 条幽灵行 -> 命中缓存
range=3m   窗口含  6 条幽灵行 -> read_bars 返回 0 行（MISS）
range=6m   窗口含  7 条幽灵行 -> MISS
range=1y   窗口含 19 条幽灵行 -> MISS
range=3y   窗口含 57 条幽灵行 -> MISS
```

`holiday_ghost_rows.py --offline` 本次重跑与报告逐字一致（287 → `[('akshare', 286), ('synthetic-v2', 1)]`
→ 三次 read 全 0 行 → `ghost rows left behind: ['2025-12-08']`）。

### 7.2 反例与替代解释（报告漏掉的七条）

**R-1（P0 · 修 PCD-1 会把 BUG-7 搬进月线）**：`§6.1` 第 1–2 条的"`1d` 重采样"没有考虑
`bars_cover` 的 `COVERAGE_TOLERANCE_DAYS = 10`（`cache.py:31`）。重采样后的首桶 `ts` 是**该桶最后一根 bar**，
所以首桶起点越晚、覆盖率判定越容易失败：

```text
1w  buckets=158  bars_cover(3y)=True   is_bars_cache_fresh=True  首桶 ts 距窗口起点 +0.1 天
1m  buckets=37   bars_cover(3y)=False  is_bars_cache_fresh=True  首桶 ts 距窗口起点 +13.1 天  ← > 10
    bars_cover(range=1m/3m/1y) = True/True/True    ← 换成别的 range 又通过：判据随日历漂移
```

也就是说：**修完之后 `?timeframe=1m&range=3y` 每次都 `used_cache=False`，每次打一次 akshare** ——
正是 PCD-4/BUG-7 的症状换了个月线外壳，而且它是"哪个月开始"决定的，测试会时红时绿。
必须同时做：`COVERAGE_TOLERANCE_DAYS` 按 timeframe 取值（`1w` ≥ 7、`1m` ≥ 31），
或改用"桶的覆盖跨度"而不是"首桶 ts 与窗口起点之差"来判定。

**R-4（P1 · `§6.1` 第 10 条复刻了 §5 拒绝方案 C 的理由）**：`§5` 拒绝 C 的第二个理由原文是
"rail 有 12 行，一次 12 个 `/bars` 请求"（`useRailQuotes.ts:17` `MAX_QUOTE_TARGETS = 12`）。
第 10 条要求 `adjust != 'qfq'` 时 rail 全部改读 `/bars` 末根 —— **同一句话，同一个成本**。
而且落点比报告写的多两个文件：

```text
web/components/chart/PricePanel.tsx:79     const [adjust, setAdjust] = useState<AdjustValue>('qfq')  ← 组件内 state，从不出圈
web/components/shell/AppShell.tsx:299,357  <PricePanel .../>                                          ← PricePanel 的父组件是 AppShell，不是 WorkspaceClient
web/components/shell/WorkspaceClient.tsx:220  const railItems = useRailQuotes(baseRailItems)          ← hook 在这里，拿不到 adjust
```

所以"PricePanel 把 adjust 上抛给 WorkspaceClient"实际是 **PricePanel → AppShell → WorkspaceClient**
三段接力（还得进 URL 才不丢，`WorkspaceClient.tsx:228-243` 的 `syncUrl` 目前只认 `view`/`range`），
共 **5 个前端文件**，而 `§6.0` 第 5 条承诺的是 3 个。

最小可行替代（成本 0 次额外外呼）：**只有"当前选中行"跟随图表的 (source, adjust)**，其余 11 行继续用
`/quote` + 一个口径/来源角标。理由是缓存键就是 `(instrument, timeframe, adjust)`，而 `read_bars` 先按
`ts >= start_ts` 过滤，所以图表那条 1 年序列**已经覆盖** rail 想看的 1 个月窗口：

```text
3y 缓存填 783 行；1m 请求读到 22 行 source=akshare
bars_cover(1m 窗口)=True  is_bars_cache_fresh=True   (bars[0].ts - start_ts = +181362 s)
```

`AC-P3a`（hfq 下 rail 与 header 价差 0.00%）用这个方案同样可满足，且不产生 12 个请求。

**R-5（P0 · 窗口级删除不收敛；`§6.1` 第 15 条的迁移规则一条幽灵行都清不掉）**：
第 12 条把删除范围定为"同 `(instrument, timeframe, adjust)` 且 `ts >= bars[0].ts` 且 `source != 本次来源`"。
只要 provider 这次**返回的窗口比请求的窗口窄**（akshare 少几天、yfinance 少几个交易日、退市边缘标的），
窗口外的幽灵行就活着，而 `read_bars` 的判据是**序列级**的，于是又回到 0 行：

```text
today(BUG-9 状态机):                read -> 0 行
after #12（同窗口重写）:            read -> 261 行 source=akshare      ← #12 确实修好了同窗口
after #12 + 一次"窄窗口换源"重写:   rows per producer=[('akshare',281),('synthetic-v2',5)]
                                    read -> 0 行                      ← 又不收敛
```

更要命的是第 15 条的迁移判据"只删同一 ts 上存在其他来源的那些"。**实测 57/57 条幽灵行都没有对手方**
（合成 walk 走了工作日、真实源那天是法定假日，本来就没有对手方）：

```text
synthetic-v2 ghost rows total=57; of those with NO same-ts counterpart=57
=> the #15 rule（只删有对手方的行）leaves 57/57
```

所以按第 15 条去写迁移脚本，跑完以后 PCD-4 一点没变 —— 而它会让人以为已经清了。

正确的语义只有一条：**一条序列（`instrument, timeframe, adjust`）同时只能有一个生产者**。
把它做成**写入时的不变量**（写入即接管整条序列，删掉其他生产者的全部行），而不是窗口级补丁；
读取侧的混源守门人（`cache.py:161-168`）**保持不动**（`§6.3` 第 3 条正确）。代价要在代码注释里写明：
真实源抖动一次会清掉真实历史，所以必须同时写 `meta.warnings`（第 13 条的意图）并在下一次请求重填。

**R-6（P0 · `AC-P2a` 在它自己的判据场景里不可满足）**：AC-P2a 要求"在只答 quote 的 A + 只答 bars 的 B
注入下，`/quote` 与 `/bars` 的 `source` 必须相同"。可是这个拓扑里**没有任何一个 provider 能同时回答两个端点**
（`alpha.bars` 抛 notfound、`beta.quote` 抛 notfound，见 repro 第 60–121 行）。于是：

- 钉住的源若选 alpha → `/bars` 必须 503；
- 钉住的源若选 beta → `/quote` 必须 503。

**两个源相等只能靠把一个端点打挂来实现**，这比缺陷本身更糟。AC-P2a 应拆成两条可达的判据：
①同源链（两个端点都能被同一个源服务）下必须相等；②分叉拓扑下必须出可读告警（已有 AC-P2b）。
R-2 已经说明：拆开之后①在默认链上是平凡成立的，所以**判据的重心应当是 ② + `AC-P3a`**。

**R-7（P2 · `meta.stale` 不是"陈旧"）**：`instruments.py:132` 是 `stale=bool(warnings)`。
注入复现里 `cached=False, stale=True`，可那个窗口一点都不陈旧 —— 它只是"alpha 没有 K 线"。
第 13 条打算把 `mixed_source` 也写进 `warnings`，那么"缓存混源"会被 UI 显示成"陈旧数据"，
与 README「过期数据不许冒充最新」那条判据冲突（这次是反过来的：不陈旧却被标陈旧）。
建议 `meta` 增加独立的 `reasonCode`（`ok / mixed_source / provider_fallback / uncovered`），
`stale` 只表达"最新一根落后于应有会话"。

**R-8（P2 · 现有测试已经把"provider 级 `[]`"钉成契约）**：`api/tests/test_market_data.py:43` 断言

```python
assert synthetic.bars("AAPL", timeframe="1w", start_ts=0, adjust="qfq") == []
```

第 3 条"不要再用空结果表示不支持"如果被理解成"删掉 provider 里的 `if timeframe != '1d': return []`"，
这条测试就会红，而它代表的正是"provider 只承诺日线、周月由上层合成"的契约。**保留 provider 级 `[]`，
在 registry 层重采样**才是与现有测试兼容的落法。

同时补一条报告没讨论的替代方案：**akshare 原生支持周/月**（`ak.stock_zh_a_hist` 的
`period ∈ {'daily','weekly','monthly'}`，本机 akshare 1.18.30 已核对签名与 docstring）。
原生周线的收盘价由 akshare 自己聚合、复权基与日线不同，会重新制造"日线图与周线图两个价"的接缝，
所以**不采用**是对的 —— 但这个理由必须写进 `§6.3` 的"不要改"清单，否则下一个人会把它当优化做掉。

**R-9（P2 · 第 3/4 条的落点与"死代码"自相矛盾）**：一旦 registry 层重采样，`1w/1m` 恒有数据，
第 3 条的 `422 unsupported_timeframe` 分支**永不可达** —— 而第 14 条的正确内容正是
"死代码比没有更糟"。二者只能选一个：要么让这个 422 只由"`Timeframe` 枚举新增值但没有重采样器"
触发（可达、且是真错误），要么干脆不引入。第 4 条还要多两个文件：
`api/app/schemas/capabilities.py:32` 的 `FeaturesOut` 加字段 + `web/lib/types.ts` + 前端消费
（现在 web 侧只消费 `askDeep/schedule/grounding/rangeQuery`，**没有任何代码读 timeframe 能力**，
所以"前端据此决定按钮是否禁用"这句话后面还藏着一个没人写的 UI 判据）。

### 7.3 候选修复方案边界审计

| 对象 | 并发 | 错误路径 | 边界输入 | 新回归 | 结论 |
|---|---|---|---|---|---|
| A 钉住源 | 单 worker 内存 map，无锁；`_PINNED` 与 `_FETCHED_AT` 同类问题（重启即丢，可接受） | 解钉后重走链，会把"一个源挂了"变成"两个端点各走一遍"（3 倍耗时） | 无 | **把跨源缺陷变成局部 503**（R-6） | **降级为"偏好提示"，不做硬约束** |
| B `/screen` 端点 | 无状态，最干净 | 单点失败即整屏失败 | 无 | 打破"每块面板自给自足" | 留 v7，判断正确 |
| C rail 改读 bars | — | — | — | 把矛盾搬到"左栏 vs 报告" + 12 请求 | 拒绝正确 |
| #1/#2 重采样 | 纯函数 | 无 | 首/末桶是**部分桶**（首桶只含 1 根日线） | 月线覆盖率永久未命中（R-1） | **接受，但必须配 R-1 的容差修正** |
| #3 能力矩阵 + 422 | — | — | 未知 timeframe | 与 #14 冲突；可能删掉 provider 级 `[]` 契约（R-8） | 只做"不进链"，422 仅在枚举扩展时可达 |
| #6 钉住 300 s | 内存态 | 解钉重试一次 | 多实例部署 | 同 A | 见 R-6 |
| #7 `resolve_source(symbol)` | — | — | — | 只解决"选源"，解决不了超时/时钟不对称（R-2/R-12） | 可做，但不能当成 PCD-2 的修复 |
| #8 `snapshot` 跨源 warning | — | provider 失败时 `quote_row` 可能是 `bars[-1].close` 兜底（`snapshot.py:136-138`），此时它与 `source` 必然相同，不会误报 | — | 无 | **接受**（这是本轮性价比最高的一条） |
| #10 rail 跟随 adjust | — | — | 12 行 × `/bars` | 复刻被拒绝的 C 的成本（R-4） | **改为"只有选中行跟随"** |
| #11 staleTime 对齐 | — | — | — | 无 | 接受 |
| #12 窗口级删除 | 事务内 | provider 失败则不写 | **窄窗口换源不收敛**（R-5） | 无 | **升级为序列级单生产者** |
| #13 写 `warnings` | — | — | — | `stale` 语义被污染（R-7） | 接受 + 配 `reasonCode` |
| #14 `purge_instrument_bars` | — | — | — | 若接上 `/bars?force` 会把整条序列清空（含 hfq/none），需按 `(tf, adjust)` 收窄 | 接受（收窄后） |
| #15 迁移脚本 | — | — | **判据自相矛盾**（R-5） | 跑完等于没跑 | **改为"对被污染的序列发一次 `force=1` 全窗口重写"** |
| #16 rail `source` 字段 | — | — | — | 无 | 接受 |
| #17 `source: "akshare (cached)"` | — | 这个字符串会被 `dataQuality` 与报告引用 | — | 改成 `cached: true` 是**接口破坏性变更**（`QuoteOut` 无此字段），需要同步 web 类型 | 接受，但要标注为"加字段"，不是"改字符串" |

### 7.4 验收判据审计

| 判据 | 可机器判定？ | 问题 | 建议 |
|---|---|---|---|
| AC-P1a | ✅ | **同义反复**：桶数就是重采样器自己的分组结果，写成"= ISO 周数"永远成立 | 改成断言"1w 末根 close == 1d 末根 close"（价格同一性，本次实测 `1w last ts == 1d last ts → True`）+ 桶数落在 `154..158` |
| AC-P1b | ✅ | 重采样后 `422` 不可达（R-9）；而"永不 503"本身是好判据 | 保留"逐枚举值 `status != 503`"，删掉"只能是 422" |
| AC-P2a | ❌ | **在判据场景里不可满足**（R-6） | 拆成"同源链必须相等"+"分叉链必须告警" |
| AC-P2b | ✅ | 可满足，但 `stale` 语义会污染它（R-7） | 断言 `reasonCode == "divergent_source"` 比断言中文文案稳 |
| AC-P3a | ✅ | 可达（R-4 的缓存复用已实测） | 保留；再加"其余 11 行不得因此多发 `/bars`"（请求计数） |
| AC-P4a | ⚠️ | 与 #12 的窗口语义冲突（R-5：窄窗口换源后 281+5 仍混源） | 只有在 #12 升级为序列级之后才成立；否则改成"请求窗口内单生产者" |
| AC-P4b | ⚠️ | "<50 ms" 是墙钟阈值，而第 2 次请求仍要反序列化 783 根 + 算指标包；且 `is_bars_cache_fresh` 依赖**进程内** `_FETCHED_AT` 戳（`cache.py:100-116`），跨进程/重启就失效 | 改成"第 2 次 `cached=true` 且 provider 调用计数不再增加（monkeypatch 计数）"，去掉墙钟阈值 |
| AC-P5a | ✅ | 无问题 | 保留 |

### 7.5 引用与事实修正清单（本轮核对）

| 位置 | 原文 | 实际 | 处理 |
|---|---|---|---|
| §3.5 | `snapshot.py:120,165` 的 `adjust="qfq"` | `102` / `116` / `120`；`165` 是 `_derive_valuation` | 已在正文就地修正 |
| §3.1 | "两个端点各自可能选中不同 provider"（当作可达） | 三家 `quote()` 均派生自 `bars()`，注入拓扑不可达 | 已加 R-2 |
| §4 | 报告自述 1634.88 / 1672.00 当作现网跨源证据 | 与 BUG-3 **修复前**证据同值；当前库同源同值 1439.78 | 已加 R-3 |
| §3.5 | "`/quote` 少一个维度" | 更准确：三家 provider 各自写死一种 `adjust` | 已加 R-13 |
| §6.0-5 | 前端 3 个文件 | 实为 5 个（`AppShell.tsx`、`WorkspaceClient.tsx` 还需接力） | 见 R-4 |
| §6.2 | AC-P2a"必须相同" | 不可满足 | 见 R-6 / 7.4 |

---

## 附录 A：本轮脚本清单（全部可复跑）

| 脚本 | 作用 | 依赖 |
|---|---|---|
| `.agentmesh/repro/timeframe_chain_probe.py` | `1d/1w/1m` × 两个市场的状态码、根数、耗时（PCD-1） | API :8000 |
| `.agentmesh/repro/provider_chain_divergence.py` | 注入"只答 quote / 只答 bars"两个 provider，跑真实端点（PCD-2，离线） | 无 |
| `.agentmesh/repro/live_split_sampler.py` | 逐次采样 `/quote` 与 `/bars` 的来源，记录分叉时刻（PCD-2，观测） | API :8000 |
| `.agentmesh/repro/adjust_mismatch_probe.py` | `qfq/none/hfq` 三口径与 `quote.last` 的差（PCD-3） | API :8000 |
| `.agentmesh/repro/holiday_ghost_rows.py` | Part 1 线上三次 3 年请求的 `cached` 与耗时；Part 2 离线复现幽灵行（PCD-4） | API :8000（Part 1 可选） |
| `.agentmesh/repro/cache_source_audit.py` | 库内每条序列的来源分布，列出混源窗口（PCD-4） | `api/data/app.db` |
| `C:/Users/jdqqj/ego/provider-divergence-20261117-1219/round-0{1..4}.mjs` | ego-browser 四轮探索脚本（含 hfq 前后 DOM 对拍、截图 `shot-daily.png` / `shot-hfq.png`） | web :3100 + API :8000 |
| `.agentmesh/scratch/review_pcd_probe.py` | **评审节点新增**：离线跑真实 app 的 `1w/1m`（R10）、重采样后能否过 `bars_cover/is_bars_cache_fresh`（R-1）、`#12` 与 `#15` 的收敛性（R-5）、库内混源分布（R15） | 无 |

## 附录 B：留给修复轮的开放问题（本轮不下结论）

1. **钉住的源该不该跨进程/跨重启保持？** 现在是单 worker 内存态（`registry.py:140-147`）。若改成 DB 持久，
   多实例部署时就会多一个一致性面；若保持内存，重启后第一次请求会抖一下（可接受）。
2. **`1w`/`1m` 的锚点应当取"该周/月最后一根 bar 的 ts"还是"该周/月首个交易日"？** 二者会改变
   `is_bars_cache_fresh` 的判定与 `block_dates` 的区间反查结果（`api/app/services/block_dates.py`），需要一起看。
3. **`BAR_CACHE_TTL_SEC=900` 与 `/quote` 的 60 s 是否应该统一成一个"屏幕刷新节奏"？** 统一会让盘中 K 线更贵，
   不统一则两端点永远有最多 15 min 的结构性偏差（§2.4）。这属于产品节奏决策，不属于缺陷修复。
4. **akshare 对 CN 假日的处理是"没有这一根"还是"有但成交量为 0"？** 本轮只观测到前者（57 条幽灵行的存在即证据），
   但没验证 `成交量=0` 的那一种，修复 §6.1 第 12 条时要用真实数据再确认一次。

---

## 评审结论

**最终推荐修复：A′ ——"一条序列一个数（one series, one number）"，而不是原方案 A（钉住 provider）。**

原文档的方向（把 `source` 从"写入时的事实"提升为"请求路径上的约束"）是对的，但**它选择的那个约束（钉住）
恰好治不了它自己测到的那两条可达路径，并且在它自己的判据场景里无法满足**（R-2、R-6）。A′ 保留原文档
四条正确落点（重采样、单生产者写入、跨源告警、合成标记），去掉硬钉住，把"屏幕上的两个数字"交给
"同一条序列"来保证。

### A′ 的四条改动（按依赖顺序，可直接照做）

1. **PCD-1：registry 层重采样，并同时修正覆盖率容差**（采纳 `§6.1` #1/#2，但必须带上 R-1）
   `resample_daily()` 放 `api/app/services/market_data/base.py`（ISO 周 / 自然月，`ts` = 桶内最后一根）；
   `registry.bars()` 对 `1w/1m` 先取 `1d` 再聚合；**同时**把 `bars_cover` 的
   `COVERAGE_TOLERANCE_DAYS`（`cache.py:31`）改成按 timeframe 取值（`1d` 10 / `1w` ≥ 7 / `1m` ≥ 31），
   否则 `?timeframe=1m&range=3y` 永远不是缓存命中（实测首桶距窗口起点 +13.1 天 > 10）。
   provider 级 `if timeframe != "1d": return []` **保留**（`api/tests/test_market_data.py:43` 已把这条钉成契约，
   R-8）；不引入 `422 unsupported_timeframe`（重采样后不可达，与第 14 条"死代码比没有更糟"冲突，R-9）。

2. **PCD-4：写入即接管整条序列**（把 `§6.1` #12 从窗口级升级为序列级）
   `write_bars` 删除同 `(instrument, timeframe, adjust)` 下**所有** `source != 本次来源` 的行；
   `read_bars` 的混源守门人（`cache.py:161-168`）**保持不动**。迁移不要用第 15 条的"ts 交集"判据
   （实测 57/57 条幽灵行没有对手方，跑完等于没跑），改为对已被污染的序列发一次**全窗口 `force=1` 重写** ——
   收敛性由本条的序列级删除保证（R-5 实测：窗口级删除在窄窗口换源后仍是 281+5 → 0 行）。

3. **PCD-3（+ PCD-2 的屏幕层）：只有"选中行"跟随图表的那条序列**
   `useRailQuotes` 对**当前选中标的**改用 `/bars?adjust=<图表当前口径>&range=1m` 的末根 ——
   缓存键就是 `(instrument, timeframe, adjust)`，图表那条更宽的序列已经覆盖它，实测
   `bars_cover=True / is_bars_cache_fresh=True`、**0 次额外外呼**；其余 11 行继续用 `/quote` + 来源/口径角标。
   `adjust` 由 `PricePanel → AppShell → WorkspaceClient` 接力并在 URL 里存（`syncUrl` 现在只认 `view`/`range`）。
   **不做硬钉住**：`_PINNED` 在注入拓扑里必然解钉，只会把"跨源缺陷"变成"某个端点 503"（R-6），
   而且钉住记录的是"谁赢过"，管不了超时不对称与 60 s/900 s 两条缓存时钟（R-2 / R12）。

4. **可见性：跨源、复权、合成三件事都要有出口**
   `snapshot` 照 `snapshot.py:143-144` 的写法补 `chain_quote.source != source` 的 warning 并把
   `dataQuality.quoteSource` 单列（`§6.1` #8 —— **本轮性价比最高的一条，也是唯一能治"报告替用户解释差价"的**）；
   `RailItem.source` + 复用 `PricePanel.tsx:231` 的合成徽标（#16）；`/quote` 的 `source: "akshare (cached)"`
   改为 `source` + 新增 `cached` 字段（#17，注意这是**加字段**的接口变更，要同步 `web/lib/types.ts`）。

### 为什么是 A′（每条结论都有本轮证据）

1. **它对准了唯一有现网证据的那两个数字。** `quotes.last=1439.78` 与 `bars(1d,hfq)` 末根 `8840.24`
   差 `+514.00%`（当前库直接复算），而 `quote.last` 与 `bars(1d,qfq)` 末根**完全相同**。
   报告 §4 拿来当"跨源铁证"的 1634.88/1672.00 与 BUG-3 的**修复前**证据同值（R-3），
   也就是说原方案 A 的主战场（provider 分叉）当前**没有**用户可见证据，而复权口径（PCD-3）有。
2. **钉住解决不了报告自己测到的时间线。** 三家 provider 的 `quote()` 全部派生自 `bars()`，
   所以"A 答 quote / B 答 bars"在默认链上不可达；可达的是超时不对称（`PROVIDER_TIMEOUT_SEC=10`
   vs 3 年请求 1.9–2.9 s）与两条缓存时钟（60 s / 900 s）（R-2/R12）。这两条都不是"选源"问题，
   钉住之后再失败只会解钉回落到原样。
3. **它把所有判据改成可达的。** AC-P2a（注入拓扑下两个 `source` 必须相同）唯一能通过的办法是把一个端点打挂
   503（R-6）；A′ 把它换成"同源链必须相等 + 分叉链必须告警"，两条都能测（7.4 表）。
4. **它不制造新的"永久未命中"。** 原 #1/#2 会把 BUG-7 搬进月线（R-1），原 #12/#15 清不掉幽灵行（R-5）；
   A′ 的容差修正与序列级删除把这两个洞都堵上了。
5. **代价可控且零 DDL。** 后端 5 个文件（`base.py`/`registry.py`/`cache.py`/`snapshot.py`/`instruments.py`），
   前端 5 个文件（`PricePanel.tsx`/`AppShell.tsx`/`WorkspaceClient.tsx`/`useRailQuotes.ts`/`SymbolRail.tsx`）。
   `bars.source` 与 `quotes.source` 已存在，不需要迁移脚本改表。

### 明确不做（含对 `§6.3` 的补充）

- **不做硬钉住**（`_PINNED` / `pinned_only` / 解钉重试）—— 理由见上第 2、3 条。
- **不用 akshare 原生 `period="weekly"/"monthly"`**：本机 akshare 1.18.30 的 `stock_zh_a_hist`
  确实支持（已核对签名与 docstring，R-8），但它的复权基与日线不同，会重新制造"日线图与周线图两个价"，
  属于 BUG-1/BUG-3 的复发路径。**这一条要直接加进 `§6.3` 的"不要改"清单。**
- **不放宽** `read_bars` 的混源拒绝，**不删**「后复权」，**不改** `_WALK_ANCHOR`/`_KAPPA`/`pairSource`
  （`§6.3` 原有四条全部保留，评审复核未发现需要推翻的地方）。
- **不要**按 `§6.1` #15 的"ts 交集"写迁移脚本（实测清不掉任何一行）。

### 替换用的验收判据（8 条，全部可机器判定、全部可达）

| # | 判据 | 怎么测 |
|---|---|---|
| AC1 | `1d/1w/1m` × 三市场，任何组合 `status != 503`；且 `1w` 末根 `close == 1d` 末根 `close` | 表驱动 pytest（本次实测 `1w last ts == 1d last ts → True`） |
| AC2 | `?timeframe=1m&range=3y` 第二次请求 `cached=true`，且 provider 调用计数不增 | monkeypatch provider 计数（堵 R-1） |
| AC3 | 注入 alpha/beta：两端点各自 200，`/bars.meta.reasonCode == "divergent_source"` 且 warning 含"来源" | 把 `provider_chain_divergence.py` 改写成 pytest |
| AC4 | 同源链下 `quote.source == bars.meta.source`（当前 1439.78 == 1439.78） | 平凡但要留回归网 |
| AC5 | `hfq` 下选中行 rail 与 header 差 `0.00%`，其余 rail 行不新增 `/bars` | vitest 请求计数 + DOM 两数相等 |
| AC6 | 混源后一次全窗口重写 → `group by source` 只剩一行；**窄窗口换源后仍然只剩一行** | 离线脚本（堵 R-5） |
| AC7 | rail 每行 `source=='synthetic'` 必现合成徽标 | vitest |
| AC8 | `meta.stale` 只在"最新一根落后于应有会话"为 true；原因走 `reasonCode` | 构造"只有 provider 告警、数据不陈旧"的用例（堵 R-7） |

### 遗留（不在本轮修复范围，但要写进 v7 的第一项）

R-2 的超时不对称本身没有被 A′ 消掉：`/quote` 与 `/bars` 仍然可能来自两个源，只是"屏幕上不会并排出现两个数"
且"分叉会被说出来"。要真正消掉它，需要 `§5` 的方案 B（一个 `/screen` 端点，一次解析、一次返回）。
评审建议：**把 B 作为 v7 的第一项**，而不是在本轮用钉住 + 12 个请求去打补丁。
另外 `§4` 的"报告侧证据"必须重新取证（R-3），否则它会以"产品自己写下的证据"的身份被下游当成现网事实引用。

---

## 实施过程发现的方案缺陷

本节由**实施节点**补写，只记录"照 A′ 做下去会出问题、因此实际做法与文档不同"的地方。
每一条都附实测输出，均可复跑。**§1–§7 与「评审结论」的原文一个字未改。**

### I-1（P0 · 会复发整轮缺陷）：重采样放在 provider 链里，会把 1w/1m 变成**独立的缓存序列**

「评审结论」第 1 条把 `resample_daily()` 放在 `registry.bars()`（缓存之下）。照做之后的实测
（本机，`range=1y`，同一窗口连续三次请求）：

```text
== CN:600519.SH ==
  1d: 200   262 根  来源=synthetic   2195 ms  cached=False
  1w: 200    52 根  来源=akshare     2895 ms  cached=False      ← 另一个 provider
  1m: 200    13 根  来源=akshare     1614 ms  cached=False
```

`1d` 与 `1w` 对**同一个窗口**给出了两个价（1,753.06 vs 1,439.78，+21.26%）——
这正是本轮要消掉的"同屏两个价"，只是从"端点之间"搬到了"周期之间"。原因很直接：
缓存键是 `(instrument, timeframe, adjust)`，`1w`/`1m` 各自成为一条序列，各自走链，
akshare 抖动一次就足以让两个周期分属两个 provider。

**实际做法（与文档不同）**：把派生放到**缓存之上** —— `/bars` 永远只读/只写 `1d` 序列
（`series_timeframe = "1d"`），取到日线之后在路由层 `resample_daily()` 出周/月；`registry.bars()`
恢复原样（provider 仍然只承诺日线）。这样"同一场、同一价"是**结构性**成立的：
没有第二条序列可以跟它不一致。实测修复后：

```text
== CN:600519.SH ==
  1d: 200   262 根  来源=synthetic  cached=True  reason=ok  末根 c=1753.06
  1w: 200    53 根  来源=synthetic  cached=True  reason=ok  末根 c=1753.06   (28 ms)
  1m: 200    13 根  来源=synthetic  cached=True  reason=ok  末根 c=1753.06   (26 ms)
```

副作用是好的：`1w`/`1m` 从"每次 2.9 s 打数据源"变成 28 ms 命中。

**连带结论：R-1 的按周期容差不需要了，已删除。** R-1 的前提是"月线序列被存下来、
首桶 ts 距窗口起点 +13.1 天"。现在没有任何桶序列被存进库，`bars_cover` 永远只看日线，
10 天的原容差恢复了它唯一正确的含义。保留一个按 timeframe 的容差反而会变成死代码
（`1d` 之外的分支永远不被调用），与 §6.1 #14 的原则冲突。缓存里此前由中间版本写入的
`1w`/`1m` 行因此变成惰性数据（不再被任何读路径读到），本机 `api/data/app.db` 里还有
13/52 行这样的残留，属可清可不清。

### I-2（P1 · 判据的覆盖面）：混源检测是**窗口级**的，窗口外的脏数据看不见

`read_bars` 先按 `ts >= start_ts` 过滤再判混源。所以一条在窗口外混源的序列，
在窗口内的读取里**完全不可见**。实测（把 3 条 akshare + 2 条 synthetic-v2 写在 40 天前，
请求 `range=1m`）：

```text
read_reason = empty        ← 不是 mixed_source
reasonCode  = uncovered    ← 于是"缓存脏了"这件事没有被说出口
```

影响有限：窗口外的脏行不会被这一读返回，因此不会出现接缝假 K 线；它只在**下一次更宽的
读**里暴露，而那一次读会走到修复分支。验证用的测试把脏行放在窗口内
（`api/tests/test_provider_chain.py::test_a_dirty_series_is_repaired_by_the_refill`），
因为窗口外的那一半在当前判据下**不可观测**，写成断言等于自欺。

### I-3（P2 · `stale` 的新语义仍然粗糙）：没有节假日历，休市日会被判"落后"

`stale` 改成 `最新一根的日期 < latest_session_due(market)`，而 `latest_session_due`
是**粗日历**（只用收盘小时与周末，没有节假日表）。所以长假期间（例如春节）真实源不给新
bar，这个字段会一直为 `true` —— 与修复前的 `bool(warnings)` 一样不精确，只是错的方向从
"当前数据被叫陈旧"变成"休市日被叫落后"。彻底修需要交易日历，不在 A′ 范围；`meta.warnings`
里的"最新一根为 …"仍然给出可核对的具体日期，读者能自己判断。

### I-4（P2 · 接口面）：`read_bars` 的二元组不能改，于是加了一个兄弟函数

`read_bars` 返回 `(bars, source)`，被 5 处测试与 2 个 repro 脚本按位置解包。
为了把"为什么没命中"带出来而不炸这些调用方，新增 `read_bars_detail()`（三元组），
`read_bars()` 变成它的薄包装。这比改返回类型侵入性小，代价是同一段读逻辑有两个入口
（一个只是另一个的投影，不存在第二份实现）。

### I-5（P2 · AC3 的可达性依赖请求顺序）：只有在"报价是此刻屏幕上那一个"时才报分叉

`/bars` 判断跨源分叉时读的是**缓存里的 quote 行**，并要求它仍然 fresh（60 s）。
因此：先 `/quote` 再 `/bars` 一定能报；反过来、或 quote 已过期，就不报——那时左栏会自己
重新取一次报价，还没到"同屏两个价"的状态。这是有意的（不把陈旧报价当成当前分叉），
但它意味着 AC3 是一个**顺序敏感**的判据，测试必须按"报价在前"写。
顺序无关的版本仍然需要 §5 的方案 B（一个 `/screen` 端点），§7 把它留给 v7 的判断不变。

### I-6（P2 · 死代码）：`purge_instrument_bars` 删除，不接线

§6.1 #14 给了两个选项（接线或删除）。序列级写入接管之后，它没有任何调用场景
（脏序列不需要专门的清理入口：一次 `force=1` 重写就收敛），而"留着等接线"正是 #14 自己
批评的状态，所以取**删除**分支。全仓库除定义外无引用。

### I-7（P2 · 前端代价）："0 次额外外呼"只在 K 线面板挂载时成立

「评审结论」第 3 条说选中行改读 `/bars` 的成本是 0，因为缓存键与图表相同。这成立
（实测 `1m` 窗口：`bars_cover=True`、`is_bars_cache_fresh=True`，命中缓存 26 ms），但
前提是 `PricePanel` **已经挂载**并填过这个键。K 线收起（`klineCollapsed` + 抽屉关闭）时
`PricePanel` 不渲染，左栏的选中行会自己发**一次** `/bars`。仍远好于方案 C 的 12 次，
但"0 次"应当读作"0 次（图表可见时）/ 1 次（图表收起时）"。

### I-8（新证据 · 把 R-3 缺的那块补上了）：PCD-2 在本机**真实复现**了，21.26%

§4 说 PCD-2 缺现网证据（R-3：1634.88/1672.00 是 BUG-3 修复前的数）。实施期间本机
akshare 抖动，真实链自然分叉了一次，两个端点的响应原文：

```text
/quote  last=1445.67  source=akshare   cached=True
/bars   last=1753.06  source=synthetic cached=True
        reasonCode=divergent_source  stale=False
        warnings=['报价来源为 akshare，与行情来源 synthetic 不同，请注意口径。']
同屏两个价: 1445.67 vs 1753.06 = +21.26%
```

也就是说 §4 里"实测 21.7%"这个数不是注入出来的：它在默认链上会出现，而且现在会被
**说出来**（改前这里 `warnings=[]`、`stale=True`，`stale` 还说了假话）。

### I-9（P2 · 复权序列之间的分叉仍然存在，属 §7「遗留」）

同一台机器、同一时刻，`adjust` 三个值各是一条序列，各走各的链：

```text
qfq  末根=1753.06（synthetic，缓存）
none 末根=1445.67（akshare，刚落库）
hfq  末根=8871.99（akshare，刚落库）
```

左栏现在跟随图表口径（PCD-3 的用户可见形态已消），但**在图表内部**切复权仍可能同时
看到两个 provider 的价。这不是 A′ 能修的（它需要一次解析、一次返回 = 方案 B），
与 §7「遗留」一致，此处只补一条实测账，作为 v7 的第一手证据。

### I-10（P2 · 分析文档的复现脚本会打印过期结论）

`.agentmesh/repro/provider_chain_divergence.py` 最后一行硬编码了
"两个响应里都没有任何'来源不一致'的提示"。修复后这行字与实际输出相反（现在有
`divergent_source` 与中文告警）。该脚本是诊断节点的产物，实施节点**没有改写它的叙述**；
可机器判定的版本在 `api/tests/test_provider_chain.py::test_a_split_chain_is_named_not_hidden`。

### I-11（作业失误 · 需向下游披露）：清理临时文件时删掉了上一节点的 `.agentmesh/scratch/review_pcd_probe.py`

实施节点在收尾时清空了自己的 `.agentmesh/scratch/` 临时目录，那个目录里**还有**评审节点留下的
`review_pcd_probe.py`（§7 与本附录 A 引用了它）。`.agentmesh/` 在 `.gitignore` 里，该文件
**已无法恢复**。后果与替代：

- 附录 A 那一行"`python .agentmesh/scratch/review_pcd_probe.py`"现在跑不起来；
- 它当年用来判定的五组（R10 的离线 1w/1m、R-1 的重采样覆盖率、R-5 的 #12/#15 收敛性、
  R15 的库内混源分布）分别由以下**仍在仓库里**的东西承担：
  `api/tests/test_provider_chain.py`（AC1 三周期、单生产者写入、混源点名）、
  `.agentmesh/repro/holiday_ghost_rows.py --offline`、`.agentmesh/repro/cache_source_audit.py`；
- R-1 那一组更是随 I-1 一起**失去对象**：桶序列不再入库，覆盖率只判日线。

教训：清理临时目录前先确认里面只有自己的文件 —— `.agentmesh/scratch/` 是**跨节点共用**的。

### 实施结论（与「评审结论」的差异，只有一处方向性差异）

A′ 的四条改动全部实施，其中第 1 条的**落点由"registry（缓存之下）"改为"路由（缓存之上）"**
（I-1），并连带删掉 R-1 的按周期容差；第 2 条（序列级单生产者）与第 4 条（可见性）按原方案
落地；第 3 条按 §R-4 的"只有选中行跟随"实现，`adjust` 经
`PricePanel → AppShell → WorkspaceClient` 接力并写进 URL（`?tf=&win=&adj=`）。
不做硬钉住、不用 akshare 原生周月、不放宽读侧守门人这三条约束都遵守了。
