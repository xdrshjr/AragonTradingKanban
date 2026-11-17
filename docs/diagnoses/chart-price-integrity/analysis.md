# K 线价格不可信：区间决定收盘价，头部价格与涨跌幅跨源拼接

- **诊断节点**：Subtask #0 · Bug Localization & Analysis
- **被测版本**：工作区当前 HEAD（api 0.1.0 / web 1.0.0，Next.js 15.5.29，FastAPI 0.136.1）
- **运行环境**：Windows 10.0.26200 · Python 3.11.7 · Node ≥ 20 · SQLite `api/data/app.db`（WAL）· `AGENT_MODE=auto`
- **发现时间**：2026-11-17
- **严重级**：**P0 —— 产品第一屏的核心数字不可信**。不是样式问题，是数据契约问题：同一个标的、同一天，同一屏上出现三个不同的"现价"，且都自称是当天的。
- **本轮共定位 6 个问题**（本文档以 BUG-1 + BUG-2 为主线，其余见 §附录 A）
- **评审状态**：已由 Subtask #1（Senior Review）审计并补证 → 新增 §5.3（候选方案修正 R-4）、正文 8 处「评审补充」（R-1/R-2/R-3/R-5/R-6/R-7/R-8/R-9，共 9 条）、以及文末 `## 评审结论`（唯一推荐修复 = **方案 A′**）。被评审推翻/修正的原始主张共 4 处：`write_bars` 落点无效（R-2c）、BUG-2 的"优先 quote"会搬家（R-3）、BUG-3 的 clamp 与判据 3 不可采用（R-5/R-6）、BUG-6 的"UI 已支持按块跳转"不成立（R-8）

---

## 1. 问题描述

在 `600519.SH`（贵州茅台）工作台上，**同一屏幕**同时出现：

| 位置 | 显示 | 来源 |
| --- | --- | --- |
| 左栏标列表的现价 | `1,634.88` `▼ 2.2%` | `GET /instruments/{id}/quote` |
| K 线面板头部 | `1,729.52` `▼ 2.2%` | 价格取自 `GET /instruments/{id}/bars`，涨跌幅取自 `quote` |
| 报告正文（同一标的） | `收盘价（元，2026-11-16）1729.52`，并专门写了两段解释"快照 quote.last 1634.88 与日线收盘 1729.52 相差 5.47%" | 快照 `quote` + `priceSeries` |

这里有三个彼此独立的缺陷，可在同一屏一次性观察：

1. **头部那一对数字是拼出来的**：`1,729.52` 与 `-2.2%` 不属于同一个数据点。若收盘价真是 1,729.52 且跌幅 2.2%，则前收必须是 1,768.8；而日线序列自己的前收是 1,721.63（对应 **+0.46%**）。UI 从未渲染过任何一天同时满足这两个数的行情。
2. **左栏与头部互相矛盾**：同一时刻、同一标的，一边 1,634.88 / ▼2.2%，一边 1,729.52 / ▼2.2%。跌得一样多，价格差 5.5%。
3. **"今天的收盘价"取决于你先看了哪个区间**（下面 §2 的 HTTP 实测）：把区间刷成 `1m` 后，同一标的当日收盘从 **1,729.52 变成 1,421.10（−17.83%）**；再刷回 `3y` 又变回来。这不是行情波动，是随机数种子的作用域问题。

产品把自己的数据层缺陷写进了研报正文——模型花了整整两段论证"日线与快照谁更可信"，而这纯粹是后端两个生成器各算各的造成的假象。也就是说，这个 bug 不只污染图形，它**污染了交付给读者的分析内容**，并把一份诚实报告压成 `degraded`（`quality.groundedRatio=0.5635`）。

---

## 2. 复现步骤

### 2.1 前置（一次性）

```text
cd M:/takoAI/AragonTradingKanban
python -c "import fastapi, sqlalchemy; print('deps ok')"
```

若 `api/data/app.db` 不存在：

```text
cd api
python -m alembic upgrade head
```

### 2.2 启动（两个终端；API 必须单 worker）

```text
cd M:/takoAI/AragonTradingKanban/api
python -m uvicorn app.main:app --port 8000 --workers 1
```

```text
cd M:/takoAI/AragonTradingKanban/web
npm run dev
```

> `npm run dev` 会打印 `⚠ You are using a non-standard "NODE_ENV" value`——本机 `NODE_ENV=production` 是外部注入的，与本文缺陷无关，可忽略。

### 2.3 复现 A：头部价格与涨跌幅跨源拼接（最直观，10 秒）

1. 浏览器打开 `http://localhost:3000/CN%3A600519.SH`。
2. 等约 8 秒（`bars` 与 `quote` 两个 query 都 resolve 之后）。
3. 观察左栏第一行与 K 线面板头部：

```text
左栏：  贵州茅台  1,634.88  ▼ 2.2%
头部：  贵州茅台 600519.SH   1,729.52 ▼ 2.2%
```

4. 打开 DevTools → Network，分别看这两个响应：

```text
GET /api/v1/instruments/CN:600519.SH/quote
{"instrumentId":"CN:600519.SH","last":1634.88,"changePct":-2.22,"prevClose":1672.0,
 "dayHigh":1654.5,"dayLow":1615.26,...}

GET /api/v1/instruments/CN:600519.SH/bars?range=1y&interval=1d
→ bars 末两根 close = 1721.63 (2026-11-13), 1729.52 (2026-11-16)
```

**判据**：头部 1,729.52 的涨跌幅应当是 `1729.52/1721.63-1 = +0.46%`（应为 `▲0.5%`），实际渲染 `▼2.2%`。同时 `quote.dayHigh=1654.5 < quote.prevClose=1672.0`——一根不可能存在的日 K（最高价低于前收却收在 1634.88）。

### 2.4 复现 B：区间决定当日收盘价（命令行，可脚本化）

```text
python .agentmesh/repro/bars_cache_probe.py
```

实测输出（本轮真实抓取，`600519.SH`）：

```text
before    {"range": "1m", "barsCount": 21,  "lastClose": 1729.52, "metaCached": true}
force 1m  {"range": "1m", "barsCount": 22,  "lastClose": 1421.10, "metaCached": false, "metaStale": true}
after 1m  {"range": "1m", "barsCount": 22,  "lastClose": 1421.10, "metaCached": true}
after 3y  {"range": "3y", "barsCount": 783, "lastClose": 1421.10, "metaCached": true}
force 3y  {"range": "3y", "barsCount": 783, "lastClose": 1729.52, "metaCached": false, "metaStale": true}
restored  {"range": "3y", "barsCount": 783, "lastClose": 1729.52, "metaCached": true}
```

- 第 2 行：只是用 `?force=1` 以 `1m` 窗口刷新，**当日收盘价变化 −17.83%**。
- 第 3–4 行：此后 `3y` 也返回同一个错误尾部——缓存尾部已被 1m 窗口写过，**整个标的的历史尾部从此错位**。
- 第 5 行：再以 `3y` 刷新才恢复。

### 2.5 复现 C：同一 provider 的两个价格体系（不启动服务也能跑）

```text
python .agentmesh/repro/range_drift.py
```

```text
== 600519.SH
   1M bars=  23 lastClose=   1412.35 change= -0.62%
   3M bars=  69 lastClose=   1365.21 change=  0.51%
   6M bars= 137 lastClose=   1544.36 change=  0.19%
   1Y bars= 265 lastClose=   1635.34 change= -1.62%
   3Y bars= 787 lastClose=   1688.38 change=  2.44%
```

同一天、同一标的，"最新收盘价"随请求窗口在 1,365.21 ~ 1,688.38 之间漂移（跨度 23.7%），而 `quote()` 又给出第四个值 1,634.88。`synthetic_provider.py` 模块 docstring 的承诺是 *"the same symbol always produces the same prices"*——实测不成立。

### 2.6 复现 D：`/bars` 的缓存永不失效

```text
GET /api/v1/instruments/CN:600519.SH/bars?range=1y&interval=1d
```

连续两次（间隔任意长）均返回 `meta.cached=true`、`meta.stale=false`，即使 `quote.updatedAt` 已经变化。代码里存在 `cache.is_bars_cache_fresh()`，但 `/bars` 路由没有调用它（见 §3.3）。

---

## 3. 根因分析（含证据）

根因是一条**三段式契约破坏链**，不是单点笔误。

### 3.1 第一段：`quote()` 与 `bars()` 是两个互不相干的随机数体系

`api/app/services/market_data/synthetic_provider.py:142-176`（bars）与 `:179-193`（quote）用**不同的 salt** 各自初始化 RNG，且都从同一个常量锚点 `base_price(symbol)` 出发：

```python
# synthetic_provider.py:142-157  bars
def bars(self, symbol, *, timeframe, start_ts, adjust):
    rng = _rng(symbol, f"bars-{timeframe}-{adjust}")   # ← salt 含 timeframe/adjust，不含 start_ts
    price = base_price(symbol)                          # ← 每次都从常量锚点重新起走
    start = datetime.fromtimestamp(start_ts, tz=UTC).date()
    ...
    while cursor <= today:
        price *= math.exp(drift + vol * shock)          # ← 终点值由"走了多少步"决定

# synthetic_provider.py:179-193  quote
def quote(self, symbol):
    rng = _rng(symbol, "quote")                         # ← 另一个 RNG
    price = base_price(symbol)
    change = round(rng.uniform(-2.8, 3.1), 2)
    last = round(price * (1 + change / 100), 2)         # ← 与 bars 的路径毫无关系
    return QuoteRow(last=last, change_pct=change, prev_close=price,   # ← prev_close 是常量锚点
                    day_high=round(last * 1.012, 2),                  # ← 以 last 为中心对称构造
                    day_low=round(last * 0.988, 2), ...)
```

证据：`python .agentmesh/repro/findings_evidence.py` 实测

```text
600519.SH  quote.last= 1634.88 quote.change=-2.22% quote.prevClose= 1672.00 | bars[-1].close= 1729.52 ...
           dayHigh<prevClose=True     ← 1654.5 < 1672.0，物理上不可能的日 K
```

三个直接后果：
- `quote.last` 与 `bars[-1].close` 永远差一个固定的"漂移量"（本轮 5.47%）；
- `quote.day_high/day_low` 是围绕 `last` 反推的对称区间，因此频繁产生 `dayHigh < prevClose`；
- `quote.prev_close` 恒等于 `base_price`，**从不等于任何一天的 `bars[-2].close`**，所以 `change_pct` 与 K 线序列的涨跌幅天然不自洽。

### 3.2 第二段：`bars()` 的随机游走起点由 `start_ts` 决定，而 RNG 的 seed 不含 `start_ts`

`_rng(symbol, f"bars-{timeframe}-{adjust}")`（`:145`）——seed 里没有 `start_ts`，但循环**从 `start_ts` 那天开始**向今天走（`:148-154`）。于是：

> 同一个 seed、同一个起点常量、**不同的步数** ⇒ 终点落在完全不同的价位。

`repro/range_drift.py` 的实测（1M→1412.35，3M→1365.21，6M→1544.36，1Y→1635.34，3Y→1688.38）就是这条公式的直接后果。这不是"随机行情"，是**路径长度泄漏到价格水平**。

> **【评审补充 R-1】上面的描述还不够锋利，而且漏掉了更严重的一半。**
>
> 这条 bug 不是"路径长度影响价格水平"，而是 **日历日期根本没有进入随机游走**：`bars()` 输出的第 k 根 K 线，价格取的是 RNG 序列的第 k 个位置，而 k = （该日期 − `start_ts`）。所以"某一天的价格"实际是"窗口起点的函数"，不是"日期的函数"。
>
> 证据（两条均可直接复跑）：
>
> 1. **平移等价性**：同一天、同一标的，两个相差 1 天的窗口产出的序列**逐根错位一格**。
>
> ```text
> $ python -c "...bars(now-1096d)[-2] vs bars(now-1095d)[-1]..."
> lenA 783 lenB 782
> A[-2] 2026-11-16 1721.63
> B[-1] 2026-11-17 1721.63
> shift-equal(second-to-last A == last B): True
> ```
>
> 2. **历史每天被重写**（这一条本文档原本没有，它比"区间漂移"更严重）：把 3 年窗口的起点按"明天"重算一次（`start_ts = (now+1d) - 1096d`，与 `/bars` 每天自动做的一样），**全部 782 个共同日期的收盘价都变了**。
>
> ```text
> $ python -c "...bars(start=now-1096d) 与 bars(start=(now+1d)-1096d) 按 ts 对齐..."
> bars_today 783 bars_tomorrow 782
> common dates 782  dates whose close CHANGES tomorrow 782
> sample [('2026-11-12', 1747.20, 1767.13), ('2026-11-13', 1754.19, 1747.20),
>         ('2026-11-16', 1721.63, 1754.19), ('2026-11-17', 1729.52, 1721.63)]
> ```
>
> 即：**同一根 2026-11-13 的日线，今天读到 1754.19，明天读到 1747.20**。屏幕上"2026-11-13 收盘 1754.19"这句话，明天就变成假的了；`bars.csv` 与 `snapshot.priceSeries` 里过去 3 年的每一个收盘价都会随"运行日"整体平移。严重级判断不受影响（仍是 P0），但**"只要把缓存锁死就能挡住"这句话不成立**——研报快照路径（`snapshot.py:103`）本来就带新鲜度检查，它每天都会重取 3 年 bars，于是每天生成同一标的的研报，"2026-11-13 收盘"都可能不同。
>
> 也因此，§5.1 方案 A 里"每根 K 线的值由 `seed(symbol, date)` 独立决定"这半句**不能照做**：按日期独立取值会消掉随机游走的自相关（K 线变成白噪声），而"累积漂移"必须基于**一个固定日历锚点**才成立。正确的最小形式见 §5.3 R-4。
>
> 计数器与判据（供 Subtask #2 直接写成测试）：
> - `bars(start_ts=T)` 与 `bars(start_ts=T-1d)`：重叠日期的 `close` 逐根相等；
> - 以 `today+1` 重算同一窗口：所有 `ts < today` 的 `close` 不变（当前实测 **0/782 通过**）。

### 3.3 第三段：`/bars` 的缓存键里没有 `start_ts`，且根本不做新鲜度检查

`api/app/routers/instruments.py:69-101`：

```python
start_ts = range_to_epoch(range.value)
rows, cached_source = cache.read_bars(db, instrument.id, timeframe=..., adjust=..., start_ts=start_ts)
used_cache = bool(rows) and not force          # ← :73 只判断"有没有行"，不判断"是否新鲜"
...
if not used_cache:
    chain = await get_registry().bars(...)     # ← 以本次 start_ts 重新起走随机游走
    cache.write_bars(db, instrument.id, timeframe=..., adjust=..., bars=chain.value.bars, source=source)
```

- `cache.read_bars`（`api/app/services/market_data/cache.py:43-76`）的 where 条件是 `ts >= start_ts`，**缓存身份 = (instrument, timeframe, adjust)**，与生成这条缓存的窗口无关；
- 缓存里存的是**某一次窗口的完整行集**，所以 `1m` 窗口写进去的 22 根会**覆盖 `3y` 的尾部**（实测第 4 行：`3y` 也只有 1421.10）；
- `cache.is_bars_cache_fresh()`（`cache.py:79-88`）存在，但**只在研报快照路径被调用**：

```text
$ grep -rn "is_bars_cache_fresh" api
api/app/services/snapshot.py:103:    used_cache = bool(bars) and cache.is_bars_cache_fresh(bars, instrument.market) and not force
api/app/services/market_data/cache.py:79:def is_bars_cache_fresh(...)
api/tests/test_market_data.py:159,163
```

图表这条路（`/bars`）永远命中、永不刷新、也永远不报 `stale`。**同一个标的的"图表价"与"研报价"来自两条不同的新鲜度规则**，这也解释了为什么报告快照里的 1729.52 与图表曾一度一致、刷新后又分叉。

> **【评审补充 R-2】§3.3 的结论成立，但漏了同一段代码里的两个洞，且对 `write_bars` 的判断是错的。**
>
> **（a）缓存命中只看"有没有行"，不看"覆盖没覆盖这个窗口"——"先看的区间"因此成为永久上限。** `used_cache = bool(rows) and not force`（`instruments.py:73`）里的 `rows` 已经按 `ts >= start_ts` 过滤过，于是一个由 `1m` 请求填出来的 22 行缓存，会让**任何更宽的区间**直接命中：
>
> ```text
> $ python -c "...全新 SQLite：写入 1m 窗口 → 再请求 range=3y..."
> fresh db: read 3y -> 0 used_cache False
> after only a 1m write, request range=3y -> rows 22 used_cache True meta.barsCount would be 22
> requested window starts 2023-11-17 but first cached row is 2026-10-19
> ```
>
> 用户点"3年"，得到的是 22 根（2026-10-19 起）、`meta.cached=true`、`meta.stale=false`、HTTP 200、无任何告警；前端的区间按钮仍高亮"3年"，页脚只写"22 根"。**冷缓存里的第一次浏览，决定此后所有区间的上限**，而且没有任何一处会报错。这一条与"1m 覆盖 3y 尾部"是两个不同的缺陷（前者发生在缓存**窄于**请求时，后者发生在缓存**宽于**请求时），修法也不同：前者要求"覆盖率校验"，后者要求"生产者对同一日期给出同一个值"。
>
> **（b）`is_bars_cache_fresh()` 里的 `window_end` 是死变量，而且谓词的语义与本项目 spec 不一致。** `cache.py:84-88`：
>
> ```python
> last_ts = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
> window_end = datetime.fromtimestamp(bars[-1].ts, tz=UTC)   # ← 与 last_ts 完全同一个表达式
> if window_end < moment - timedelta(days=HISTORICAL_FLOOR_DAYS):
>     return True
> return (moment - last_ts) < timedelta(seconds=bars_ttl_seconds(market, moment))
> ```
>
> `HISTORICAL_FLOOR_DAYS` 的那一支按 spec §9 应当判"这次**请求的窗口末端**是否落在 7 天以前"，这里却拿"最后一根 bar 的时间"当窗口末端——而 `/bars` 的窗口末端永远约等于"现在"。实测这个谓词在真实缓存上的行为：
>
> ```text
> last_ts 2026-11-17 07:00:00+00:00   now 2026-11-17 02:40:08   fresh_now True
> fresh_plus7h True   fresh_plus1d False   fresh_sat_nov21 False   fresh_sun_nov22 False
> ```
>
> 也就是说：`/bars` 一旦接上这个谓词（方案 A 的一半），**周末与休市日永远不新鲜** → 每次打开都重取、`meta.cached` 恒为 false、缓存恰好在用户最常看盘的时段失效；对 `akshare` 这种真源，这就是每次浏览一次网络往返（且 `snapshot.py:103` 现在就已经是这个行为：周末每生成一份研报都重取 3 年 bars 并整段 upsert）。这不是"接上线就行"的既有能力，**谓词本身要先修**：判据必须是"缓存里有没有到最近一个已收盘交易日的 bar"，而不是"最后一根 bar 距现在多久"。
>
> **（c）`write_bars` 已经按 `ts` upsert，"不再整段覆盖"是无效动作。** `cache.py:91-138` 先 `select ... where Bar.ts >= bars[0].ts` 建 `existing` 字典，再逐根 `existing.get(bar.ts)` → 有则更新、无则插入。§落点清单里"`write_bars()`：同 (instrument, timeframe, adjust) 的旧行按 ts upsert，不再整段覆盖尾部"描述的**就是这个函数现在的实现**，改了等于没改。真正的问题是同一个 `ts` 会被写入**不同的值**（不同窗口的 walk 不一样），而 upsert 忠实执行了"覆盖"：
>
> ```text
> $ python -c "...把 1m 窗口的 22 根 upsert 进一份 784 行的 3y 缓存，再按 ts 排序看相邻涨跌..."
> total rows after 1m write: 784  new-walk rows written: 22
> largest close-to-close jump after mixing: [(1792393200, 1789.11, 1680.55, -6.07), ...]
> last close now: 1421.1
> ```
>
> 缓存里留下的是**两条不同 walk 拼起来的混合序列**，接缝处一根 −6.07% 的日线（本文档 §2.4 只报了"末根变成 1421.10"，没报这个新伪影）。所以修复落点不是 `write_bars`，而是**生产者对同一日期必须给出同一个值**（§5.3 R-4），外加**一次性的旧缓存迁移**（见 §5.3 R-4 第 4 条）。
>
> **（d）`read_bars` 的 `source` 取最后一行的来源**（`cache.py:63-76` 循环里 `source = row.source` 每行覆盖），混合序列（旧行 akshare + 新行 synthetic）会对外报一个单一来源，`meta.source` 因此是一句会被打赢的谎——`BarsMeta.source` 是 UI 上"数据来源：synthetic / 合成数据"徽标的唯一依据（`PricePanel.tsx:214,218`）。

### 3.4 前端的放大器：`lastClose` 取 bars，`changePct` 取 quote

`web/components/chart/PricePanel.tsx:103-112`：

```tsx
const latest = bars[bars.length - 1];
const lastClose = latest?.c ?? lastPrice ?? null;        // ← 优先 bars
const change = useMemo(() => {
  if (changePct !== null && changePct !== undefined) return changePct;   // ← 优先 quote
  if (bars.length < 2 || !latest) return null;
  const previous = bars[bars.length - 2];
  return (latest.c / previous.c - 1) * 100;              // ← 只有 quote 缺席时才算 bars 的涨跌幅
}, [bars, changePct, latest]);
```

调用方两个值都传（`web/components/shell/AppShell.tsx:305-306`，`:363-364`）：

```tsx
lastPrice={quoteQuery.data?.last}
changePct={quoteQuery.data?.changePct}
```

于是渲染出 `1,729.52 ▼2.2%`：**价格用 A 源、涨跌幅用 B 源**。即使后端两个源彼此自洽（比如接真实行情），只要 `quote` 比 `bars` 新一根（`bars` 走 6 小时/15 分钟 TTL，`quote` 走自己的 TTL），头部这一对数字仍会不自洽。这是**前端契约缺陷，独立于后端的 synthetic 问题**，必须单独修。

> **【评审补充 R-3】"必须单独修"成立，但方案 B 写的规则会把这个矛盾**搬家**而不是消掉，落点清单里那句"`quote` 在场时用 `quote.last` + `quote.changePct` 并标注实时"不能照做。**
>
> 依据在 `CandleChart.tsx:125-132`：蜡烛序列只关了 `priceLineVisible`，**没有关 `lastValueVisible`**（对比 `:137-141` 的成交量与 `:149-179` 的 MA/BOLL 都显式写了 `lastValueVisible: false`）。所以 lightweight-charts 的价格轴右侧**自己就印着 `bars[-1].c`**——`.agentmesh/ego/diag-20261117/price-mismatch.png` 里 1729.52 那个轴标签就是它。
>
> 也就是说：若按 B 的写法让头部改取 `quote.last = 1634.88`，同一块面板上会同时出现**头部 1,634.88** 与**右侧价格轴 1,729.52**，两者相距不到 300px，且没有任何文案解释它们是两个口径。矛盾从"头部 vs 左栏"变成了"头部 vs 图表本体"——而后者更糟：K 线面板的头部数字，读者默认就是这张图的最后一根。
>
> 另外两处同样黏在 `bars` 上、却被 B 的规则漏掉的东西：
> - `PricePanel.tsx:219` 的日期 `Tag` 取 `epochToDate(latest.ts)`（bars 的最后一根）；
> - `PricePanel.tsx:216` 的"`barsCount` 根"。
>
> 所以 B 的正确形式不是"优先 quote"，而是**"一对数字同源 + 同源的标签跟着走"**：
> 1. 图表面板头部默认展示 **bars 口径**（价格 `bars[-1].c`、涨跌 `bars[-1].c / bars[-2].c − 1`、日期取 `bars[-1].ts`）——因为这块面板的轴、日历、`barsCount` 全部是 bars 口径，头部跟随它，屏幕上只剩一个数；
> 2. 只有在**明确把这一对数标成"现价/实时"**（并给出 `quote.updatedAt` 的时刻）时才用 `quote.last` + `quote.changePct`，同时**必须**把轴标签与日期 Tag 一并切到 quote 口径（即 `lastValueVisible` 交给一个与模式一致的开关），否则就回到"一个面板两个价"；
> 3. `bars.length === 0` 时整对回落到 `quote`（此时图里没有轴标签，不存在冲突）；
> 4. 左栏的现价（`1,634.88`）是另一个面板，可以各自表态，但两边必须能解释彼此：一旦后端修好（`quote.last === bars[-1].c`），两个面板显示同一个数，解释成本归零。
>
> 判据可写成一条 vitest：给定 `quote.last=1634.88 / changePct=-2.22` 与 `bars[-1].c=1729.52 / bars[-2].c=1721.63`，头部渲染出的**价格与涨跌幅必须来自同一源**，且当价格来自 quote 时，头部日期与价格轴标签也必须来自 quote（现有 `web/tests/` 下没有任何 PricePanel 用例，`grep -n "PricePanel\|lastPrice" web/tests/*` 为空，所以这条测试是新增而非改造）。

### 3.5 旁证：产品自己把缺陷写进了最贵的地方

同一份报告（`reports.id=ed01d950ed994cb2ac13c632b362fc5f`, v11）的正文里：

```text
"as-of 2026-11-16T18:02:30Z，但行情源标记 stale=true ... 此外快照 quote.last 1634.88 元、
 prevClose 1672.00 元，与 2026-11-16 日线收盘 1729.52 元、2026-11-13 日线收盘 1721.63 元
 分别相差 5.47% 与 2.88% ... 本报告以日线（S2）与技术指标（S1）为唯一定价依据。"
```

模型被迫用两段正文去调和后端的自相矛盾，最后判定该报告 `status=degraded`、`groundedRatio=0.5635`。**一次数据层 bug，被放大成一份降级报告 + 一次多花的 89k in / 28k out token。**

---

## 4. 影响面

| 维度 | 受影响的用户可见能力 | 说明 |
| --- | --- | --- |
| 第一屏全部价格 | 左栏现价/涨跌、K 线头部现价/涨跌、报告头"目标价隐含空间" | 三个价格并存；任何一个都可能是错的 |
| 区间切换 | 日/周/月 × 1月/3月/6月/1年/3年 | 缓存为空时，**先看哪个区间决定此后所有价格**（实测 −17.83%） |
| 技术指标 | MA5/MA20/MA60/RSI/MACD/BOLL/ATR（`indicators` 由同一 `closes` 计算） | 价格水平被平移，指标全部跟着错；报告的"定价"论据引用同一批指标 |
| 研报生成 | 快照 `quote` / `priceSeries` / `technicals` / `valuation.proxy` | 模型在错误事实底座上写分析，并把矛盾写进正文（§3.5） |
| 接地判分 | `quality.grounding` / `groundedRatio` / 状态 `degraded` | 报告被自己的数据层伪影判成降级（详见附录 A · BUG-4，实测 33/103 条未接地来自"同比") |
| 数字溯源可信度 | 报告头徽标"141 个数字未找到依据"、块脚注 | `doc.risks[]` 里的数字（风险清单是读者可见的一整块）完全不在分母里（附录 A · BUG-6）；且徽标的 141 与当前代码复算的 103 不一致（附录 C） |
| 版本比较 | `blocksChanged` 33 处、`statusChanged 降级` | 差异的一部分来自数据源抖动而非观点变化，却没有任何提示 |
| 影响范围（标的） | **所有走 synthetic 兜底的标的**（离线演示、网络受限、`akshare`/`yfinance` 双双失败时）+ A 股真实降级链路 | `DATA_PROVIDERS=akshare,yfinance,synthetic` 的最后一跳就是它；`quote` 使用 `synthetic` 时全量中招 |
| 信任 | "合成数据"徽标 + 报告自我解释 | 用户看到的是一个会自证的错误：它告诉你"数据有矛盾，我以日线为准"，而矛盾本是自家 bug |

**哪些没有受影响**（避免下游误改）：`quote` 的 `volume/updatedAt/source` 字段结构正确；`/bars` 的 `meta` 字段结构正确；导出 HTML/Markdown 不含价格；`numeric_claims` 对日期的剔除正确（`26Q1`、`2026-11-16T18:02:30Z` 均被正确剔除，实测 `numbers_in_text('26Q1 营收 547.03 亿元、同比 +6.34%') == ['547.03','6.34']`）。

---

## 5. 候选修复方案对比

### 5.1 主线（BUG-1 + BUG-2：图表价格不可信）

| # | 方案 | 改动面 | 风险 | 工作量 | 彻底性 |
| --- | --- | --- | --- | --- | --- |
| **A** | **把 synthetic 改成"按日历日定值 + 纯函数"，并为 `/bars` 补新鲜度**：`bars()` 用 `date.toordinal()` 作为步进锚，每根 K 线的值由 `seed(symbol, date)` 独立决定（随机游走改成"按日 ordinal 的确定性函数 + 累积漂移"），使任意窗口的**重叠区间逐根相等**、末根与 `quote.last` 同源；`/bars` 路由接入 `cache.is_bars_cache_fresh()`。 | `synthetic_provider.py`（bars/quote 各 ~30 行）、`routers/instruments.py:73-101`、`cache.py`（缓存键加 `bars_end_ts` 或改为按窗口合并） | 中：会改变既有合成价位，`api/tests/test_market_data.py`、`test_bars*.py` 里凡是写死数值的断言需同步更新；演示截图价位会变一次 | 3–4 h | **高**：窗口无关性、quote/bars 一致性、缓存新鲜度三件事一次解决，且对真实数据源同样有约束力 |
| **B** | **只修前端契约**：`PricePanel` 不再混源——要么全用 `bars`（`lastClose` 与 `change` 都从 `bars[-1]/bars[-2]` 算），要么全用 `quote`（`last` + `changePct` 同源，并显式标注"实时报价"）。 | `web/components/chart/PricePanel.tsx:103-112`（~10 行）+ 一条 vitest 用例 | 低 | 30–40 min | 中：头部自洽了，但左栏仍 1,634.88 vs 图表 1,729.52，"区间决定收盘价"仍在 |
| **C** | **在 Gateway 层做"单一价格真源"**：新增 `services/market_data/reference.py`，所有对外价格（rail / header / snapshot / report）统一走 `latest_price(instrument)`，优先真实源、其次 quote、最后合成；`bars` 只负责历史。 | 新增 1 个模块 + 4 处调用点（`instruments.py` quote/bars、`snapshot.py`、前端 rail） | 中高：引入第五个抽象层，容易变成"又一处可以不同步的地方" | 6–8 h | 高，但**过度**：当前只有一个真问题（synthetic 不守契约），不需要新抽象 |

### 5.2 附录项（BUG-3/4/5，详见附录 A）

| # | 问题 | 候选修法 | 建议 |
| --- | --- | --- | --- |
| BUG-3 | `quote.day_high/day_low` 围绕 `last` 对称构造，产生 `dayHigh < prevClose` | (a) 由 `bars[-1]` 的 `high/low` 派生；(b) 校验 `high ≥ max(open,close,prevClose)`、`low ≤ min(...)` 后 clamp | 采 (a)，随方案 A 一起落地 |
| BUG-4 | 接地判分把"模型自算同比"判成未接地（33/103） | (a) 快照为 8 期财务**自算** `revenueYoY/netProfitYoY`（数据本来就在同一份快照里，可精确复现）；(b) 把同比写进 prompt 白名单；(c) 降低阈值 0.9 | 采 (a) + (b)，实测 `ratio 0.6185 → 0.7407` |
| BUG-5 | `_derive_valuation` 死分支：`pe = None` 后又 `if pe is not None`，`pePercentile3y` 恒 None | 删除死分支并让 `pe=None` 成为**显式返回值**（不写 percentile），或接市值后真算 | 清理 + 注释说明"这里将来接市值"，不留永假分支 |
| BUG-6 | 读者可见的 `doc.risks[]` 数字从不进溯源分母 | (a) `extract_claims()` 增加 `doc.risks[].{title,trigger,mitigant}` → `fact` 路径（含 `block_id="risks#N"`，UI 已支持按块跳转）；(b) 反向：UI 不渲染风险清单 | 采 (a)：读者能看到的数字必须进分母，且成本仅 ~10 行 |

### 5.3 评审对候选方案的修正（R-4）：A 的方向对，但三处细节会让实现者踩坑

> 这一节是**评审新增**。结论先说：**方案 A 的方向正确、方案 C 仍然应当否决**，但 A 里有三条不能照抄，另有一件 A 完全没提而必须做的事。以下每条都附实测数字。

**（1）"每根 K 线的值由 `seed(symbol, date)` 独立决定"是错的方向；正确的最小形式是"固定日历锚点 + 弱均值回归"。**

按日期独立取值 = 相邻两根 K 线的收益互不相关，K 线立刻变成白噪声（趋势、MA、BOLL 全都失去意义，报告里的"均线"章节会对着噪声写分析）。保持随机游走而让窗口无关，只需要一个改动：**循环的起点不是 `start_ts`，而是一个写死的日历锚点**，最后再按 `start_ts` 切片。这样"日期 → RNG 序列位置"是永久固定的函数，②③④三个症状（区间漂移 / 历史每天重写 / 末根随窗口变）一次性消失。

但仅此还不够，评审实测了这个最朴素的版本（锚点 `date(2000,1,1)`，其余代码照抄）：

```text
600519.SH days 7012 build_ms 47.1 last 2026-11-17 close 25.71
   overlap_equal_3y_vs_1m True
   last_close_1m_equals_3y True
   past_closes_stable_tomorrow True
AAPL days 7012 build_ms 54.6 last 2026-11-17 close 14755.15
   overlap_equal_3y_vs_1m True
   last_close_1m_equals_3y True
   past_closes_stable_tomorrow True
```

三条判据全绿，**但茅台变成 25.71 元、苹果变成 14,755 元**。原因：`drift = rng.uniform(-0.0004, 0.0009)` 是每日漂移，乘上 7000 个交易日就是 `e^(±1.75)` 的量级，锚点里那个 `BASE_PRICE_BY_SYMBOL`（`synthetic_provider.py:81-98`，注释写明是为了"offline screenshots look plausible"）在终点已经不存在了。**这是方案 A 会引入的第一号回归，而且是产品级回归**：演示首页价格变成 25.71，而现有测试抓不到它（`test_known_symbols_are_covered_by_anchored_prices` 只断言 `> 0`）。

评审实测的修正版（同一锚点 + 一个弱均值回归项 `kappa=0.003`，即向 `log(base_price)` 拉回、半衰期约 230 个交易日；开盘价改成锚定前收）：

```text
600519.SH p0 1672.0 last 1058.96 min 812.01 max 2228.7 last_over_p0 0.63 mean_gap 0.24% mean_body 1.42%
AAPL      p0  228.5 last  364.05 min 153.26 max 683.8 last_over_p0 1.59 mean_gap 0.27% mean_body 1.64%
0700.HK   p0  412.8 last  361.39 min 244.46 max 707.8 last_over_p0 0.88 mean_gap 0.22% mean_body 1.33%
```

价位回到可信区间（茅台 812~2,229、苹果 153~684），三条判据仍然全绿，代价是每标的每次构造 ~7,000 步（实测 47ms/标的，`bars()` 与 `quote()` 各调一次可接受；若在意，用 `functools.lru_cache` 按 (symbol, timeframe, adjust, today) 缓存即可）。**注意 `quote()` 必须复用同一条 walk 的末两根**，否则第二次调用会把 47ms 付两遍，且两条 walk 之间又可能出现极小概率的不一致。

**关键取舍必须写进代码注释**（否则下一个人会把它改回去）：`BASE_PRICE_BY_SYMBOL` 的语义从"今天的股价"变成"锚点日的股价 + 均值回归中枢"。三者（窗口无关、历史稳定、今日价位 ≈ base_price）**不可能同时精确成立**——今日价位精确等于 `base_price` 只能靠"每天重新锚定"，而那正是当前这个 bug。选"弱均值回归"就是显式放弃第三条的"精确"，保留"可信"。

**（2）`open` 必须锚定前收，否则 §6.4 判据 3 永远无法成立（见 R-5）。** 现在 `open_price = price * (1 + rng.gauss(0, vol / 4))`（`synthetic_provider.py:159`），是围绕**今天自己的 close** 抖动，不是从**昨天的 close** 跳空。实测后果（`api/data/app.db` 里真实的 784 行 600519.SH 日线，用前一根 close 当 prevClose）：

```text
n 783
body（|open/close−1|）  mean 0.351%  median 0.304%  max 2.00%
gap （|open/prevClose−1|）mean 1.469%  median 1.260%  max 5.36%
gap>2pct 212 天   gap>3pct 82 天   body<0.5pct 579 天
```

**跳空幅度是实体幅度的 4 倍**，方向完全是反的：真实日线的隔夜跳空远小于日内振幅。视觉上就是"每根蜡烛都从昨天收盘价旁边飘着长"，也正是 BUG-3（`dayHigh < prevClose`）在 783 天里出现 **297 次（37.9%）**的真正来源——BUG-3 把账记在 `quote.day_high/day_low` 头上，但那两个字段只是"围绕 last 对称构造"的第二层错误，**第一层在 `bars()` 的 `open`**。修法：`open = prev_close * (1 + gauss(0, vol/6))`，`close` 仍由 walk 决定，则 `mean_gap ≈ 0.24% << mean_body ≈ 1.42%`（见上面 R-4(1) 的实测），且不再需要给 `high` 做任何 clamp。

**（3）`write_bars` 那条落点无效（R-2(c)），必须换成"一次性的旧缓存迁移"。** 这一点 A 完全没提，而它会让修好的代码在**存量库上看起来没修**：新 `bars()` 对同一 `ts` 给出新值，但 `read_bars` 会先把旧行读出来；只要窗口命中缓存（详见 R-2(b)：历史窗口永久新鲜），旧 walk 的行就一直被服务，`quote.last`（新 walk）与 `bars[-1].close`（旧 walk）继续不一致——**Bug 修完了，屏幕上的两个数还是一样对不上**。做法（任选一，都是小改动）：
- 在 `bars`/`quotes` 表上加 `generator_version`，读缓存时版本不符视为未命中；或
- 部署脚本里对两张表做一次 `DELETE`（合成数据可丢弃，不需要迁移精度）；或
- 最省事：把 `SyntheticProvider.name` 升为 `synthetic-v2`，`cache.read_bars` 返回的 `source` 与当前 provider 名不符即视为未命中——**利用已经存在的 `source` 列，零 DDL**。评审倾向这一条（无需迁移脚本，且对 akshare/yfinance 混入的行同样安全）。

**（4）BUG-4 的"为 8 期财务自算"做不到 8 期，实测只填得出 4 期。** 快照只请求 8 期（`snapshot.py:133`），同季同比需要"去年同季"，因此只有最近 4 期有对照。`repro/grounding_yoy_experiment.py` 自己打印的就是 `derived YoY values written into the snapshot copy: 8`——**8 个值 = 4 期 × 2 字段**，不是 8 期。所以：要么把 `fundamentals(symbol, 8)` 提到 12 期（快照体积、prompt 长度、`fundamentals.md` 表格长度都跟着变，需同步 §8.4 与快照契约测试），要么明确"后 4 期仍是 n/a"并在 `fundamentals.md` 里把 `n/a` 换成可区分的一句话（`数据源未提供` vs `仅最近 4 期可推算`）。顺带：新增的 `revenueYoY` 是被**算出来**的，必须与数据源给的同比在字段上可区分（例如 `revenueYoYSource: "computed"`），否则下一个人会以为数据源给过这个数——这正是 README「老报告不回填（回填会伪造数据）」那条原则的同源风险。

**（5）BUG-6 的 `block_id="risks#N"` 今天是一个死链接，落点清单里"UI 已支持按块跳转"不成立。** `ReportPanel.tsx:585-604` 渲染风险清单时，`<li>` 上**没有** `data-block-id`；而跳转实现（`:219-222`、`:242-246`）是 `scrollRef.current?.querySelector('[data-block-id="…"]')`，且 `parseBlockId("risks#0")` 不成立 → 走 `:239-247` 的"非章节定位"分支 → `target?.scrollIntoView(...)`，`target` 为 `null`，**静默什么都不发生**（没有报错、没有高亮、没有滚动）。同时 `BlockFootnote` 是挂在 `ReportRenderer` 的块外层渲染的（`ReportRenderer.tsx:180-184`），`risks#N` 的样本连脚注都不会出现。所以 BUG-6 的完整修法是两处：`numeric_claims.extract_claims()` 增收 `doc.risks[]`（后端分母）+ `ReportPanel.tsx:586-589` 的 `<li>` 补 `data-block-id={\`risks#${index}\`}`（前端可点）。只做前者，用户会看到一个"未找到依据 · 1 个数字"却点不动的徽标条目——比不进分母更糟。


---

## 6. 推荐修复方案

**推荐：方案 A + 方案 B 同时落地（后端修契约，前端停止混源），BUG-3 随 A 一起做；BUG-4/BUG-5 作为独立小 patch 在同一分支内跟进。**

> **评审裁定（详见文末「## 评审结论」）：方向采纳，但本节的方案 A/B 表述已由评审修正为 A′（固定日历锚点 + 弱均值回归 + 开盘价锚定前收 + 缓存覆盖率/代次校验 + 前端"一对数字同源、标签随源"）。下面 6 条理由中，第 3 条与第 5 条的后半句**已被证伪**，改动处已用「评审修正」标出；以文末结论为准。**

理由（按"为什么不是别的方案"组织）：

1. **必须修后端，因为错误价格的源头在 provider 的契约里**。`synthetic_provider.py` 的 docstring 明确承诺"same symbol always produces the same prices"，而 seed 不含 `start_ts`、quote 用另一套 RNG——这是实现违约，不是设计取舍。只做方案 B 会让头部自洽，但左栏与图表仍然各说各话，产品依旧不可信。
2. **不能引入方案 C 那一层**。"价格真源"看起来最干净，但本项目的边界原则写得很清楚（README §架构：三层边界清楚、`/reports` 只有一个创建入口因为"第二个入口等于第二个并发真源"）。再加一层 gateway，等于给出第五个可以不同步的地方，与项目既有取舍相悖。
3. **必须同时补 `/bars` 的新鲜度**，否则方案 A 的确定性会被缓存永久钉死在"某次窗口"上：`cache.py:79` 的 `is_bars_cache_fresh()` 已经写好了，`snapshot.py:103` 也在用，`/bars` 只是漏了调用。**（评审修正，见 R-2(b)：这半句"只是漏了调用"不成立——谓词本身要先修：它拿"最后一根 bar 的时间"当窗口末端，周末永远判不新鲜；且缓存命中还缺"覆盖住请求窗口"这一条校验。）**
4. **方案 A 的正确性判据可写成测试**（也是下游 Subtask #2 的验收方式）：
   - 同一 symbol，任意两个 `start_ts` 的 bars，**重叠日期逐根 `close` 相等**；
   - `bars()[-1].close` 与 `quote().last` 相等（同一天、同一标的）；
   - `quote().day_high >= max(bars()[-1].high, bars()[-1].open, quote().prev_close)`，`day_low <= min(...)`；
   - `/bars` 两次调用，第二次在 TTL 内 `meta.cached=true`、TTL 外 `meta.cached=false` 且 `meta.stale` 反映真实刷新结果；
   - `bars(range="1m")` 与 `bars(range="3y")` 的**末根 close 相等**（这是本次 bug 的一句话判据）。
   > **【评审补充 R-5】上面 5 条判据里，第 3 条是伪不变量，必须改写；第 1 条要加一条"跨日稳定"，否则修完仍会复发。**
   >
   > **判据 3 不能用。** `quote().day_high >= max(bars()[-1].high, bars()[-1].open, quote().prev_close)` 隐含的假设是"今天的最高价不可能低于昨天收盘价"。这个假设在真实市场里是**假的**：跳空低开、全天不回补的日子，`high < prev_close` 完全合法。用现有 784 行 600519.SH 实测这条"不变量"：
   >
   > ```text
   > days where high < max(open,close,prevClose): 297 of 783
   >   of which open<prevClose (legit gap-down):  297
   >   open>prevClose but high<prevClose (impossible): 0
   > ```
   >
   > 297 次违规**全部**是"开盘就低于昨收"的合法低开，一次真正的非法样本都没有。所以这条判据如果落成"逐日断言"，测试要么一直红，要么逼着实现者去伪造一个高于 `prevClose` 的 `high`（**为了过测试而造数据，比 bug 更危险**）；如果落成"只断言最后一天"（本文档当前的写法），它今天恰好通过（实测 `bars[-1].high=1747.98 > quote.prev_close=1721.63`），于是给出**假绿**——测试通过、不变量却在那 38% 的日子里被违反。
   >
   > 正确的判据分成两条，各管一层：
   > - **块内自洽（可以逐日断言）**：`high >= max(open, close)`、`low <= min(open, close)`、`volume > 0`（现有 `test_synthetic_bars_are_ohlc_consistent` 已经在断言，保留）；
   > - **跨源同源（这才是 bug 的判据）**：`quote().last == bars()[-1].close`、`quote().prev_close == bars()[-2].close`、`quote().day_high == bars()[-1].high`、`quote().day_low == bars()[-1].low`、`quote().volume == bars()[-1].volume`（**逐字段相等**，而不是不等式）。不等式只该用来表达"这天的 k 线像不像真的"，那就换成分布判据：`mean(|open/prevClose−1|) < mean(|open/close−1|)`（当前实测 1.469% vs 0.351%，**方向是反的**，见 R-4(2)）。
   >
   > **判据 1 要补一条跨日稳定性**，否则"区间无关"修完，"历史每天被重写"仍会活着（R-1 的 782/782）：`bars(today=D)` 与 `bars(today=D+1)` 在 `ts < D` 的所有日期上 `close` 必须逐根相等。这条只有把 walk 锚到固定日历日才可能通过，是本轮真正的一句话判据。
   >
   > **回归面纠正（替换 item 5 的第一句）**：`api/tests` 里**没有**任何写死的合成价位断言——`grep -n "\.close ==\|\.close==\|last ==" api/tests/*.py` 为空，`test_bars*.py` 这个文件不存在，`test_market_data.py:159-163` 是新鲜度用例、不含价格字面量。所以本次改动的后端回归面是**空的**（好消息：不需要改任何既有断言），代价是**没有任何既有测试会挡住 R-4(1) 那个 25.71 元的回归**，必须新写。基线供对照：`cd api && pytest --collect-only -q` 实测 **626 条**（改动后不得下降）；`pytest tests/test_market_data.py tests/test_numeric_claims.py tests/test_indicators.py -q` 改动前 **68 条全绿**。
5. **回归面已探明（已由评审修正，见 R-5）**：`api/tests` 中**不存在**写死的合成价位断言（`test_bars*.py` 这个文件也不存在，`test_market_data.py:159-163` 是新鲜度用例），所以后端既有断言不需要改；`pytest --collect-only` 的数量**不得下降**（改动前实测 626 条，README 的既有约定）。**真正的回归风险是没有测试会拦住它**——必须新写 R-4(1)/R-5 的判据（尤其"茅台不能变成 25.71"）。前端侧补一条 `PricePanel` 单测：给定 `quote.last≠bars[-1].c` 时，头部价格与涨跌幅必须来自同一个源，且头部标签与价格轴同口径。
6. **不属于本节点的动作**：本文档不含任何代码修改（Subtask #0 的边界），也不做 `git commit`。复现脚本与截图放在 `.agentmesh/repro/`、`.agentmesh/ego/diag-20261117/`，被测仓库代码零改动。

### 落点清单（供 Subtask #2 直接照做）

| 文件 | 动作 |
| --- | --- |
| `api/app/services/market_data/synthetic_provider.py:142-176` | `bars()`：**循环起点改为写死的日历锚点**（不是 `start_ts`，也不是"按日期独立取值"），末尾按 `start_ts` 切片；漂移项加**弱均值回归** `+ kappa*(log(base_price)-log(price))`，`kappa≈0.003`；`open` 改为锚定**前一根 close**（`prev_close*(1+gauss(0,vol/6))`）——见 R-4(1)(2) |
| `api/app/services/market_data/synthetic_provider.py:179-193` | `quote()`：五项（`last/prev_close/day_high/day_low/volume`）**全部**取自同一条 walk 的末两根，不再用第二个 RNG 与 `last*1.012/0.988` 对称构造 |
| `api/app/services/market_data/synthetic_provider.py:81-98` | 注释语义修正：`BASE_PRICE_BY_SYMBOL` 从"今天的股价"改述为"锚点日股价 + 均值回归中枢"（R-4(1) 的取舍必须写在代码里） |
| `api/app/routers/instruments.py:73` | `used_cache` 必须同时满足**新鲜度**与**窗口覆盖率**：`bool(rows) and not force and cache.bars_cover(rows, start_ts) and cache.is_bars_cache_fresh_from(rows, instrument.market, now)`（R-2(a)(b)，谓词需先按"最近一个已收盘交易日"重写） |
| `api/app/services/market_data/cache.py:79-88` | 重写 `is_bars_cache_fresh()`：删掉与 `last_ts` 同表达式的死变量 `window_end`；判据改为"最后一根 bar 是否已到最近一个已收盘交易日"，而不是"距现在多久"（R-2(b)）。周末不得判为永不新鲜 |
| `api/app/services/market_data/cache.py:91-138` | **本条替换原"upsert"落点（无效，R-2(c)）**：`write_bars` 已经是按 `ts` upsert，不需要改；改为新增**代次校验**——`read_bars` 返回的 `source` 与当前 provider 名不符即视为未命中（provider 名升为 `synthetic-v2`），使存量旧 walk 的行一次性失效（R-4(3)） |
| `web/components/chart/PricePanel.tsx:103-112` | 一对数字同源，且**标签跟着同源走**：默认用 `bars[-1].c` + `bars[-1]/bars[-2]-1` + `epochToDate(bars[-1].ts)`；只有整对切到"现价/实时"（并显示 `quote.updatedAt`）时才用 `quote.last` + `quote.changePct`，此时日期 Tag 与价格轴标签的 `lastValueVisible` 也必须同口径（R-3） |
| `web/components/chart/CandleChart.tsx:125-132` | 蜡烛序列的 `lastValueVisible` 改由外部传入（跟随 PricePanel 的取源模式），默认 `true` 以保持"轴标签 = 头部数字" |
| `api/app/services/snapshot.py:192-204` + `write_fundamentals_md` (`:331-362`) | 为 8 期财务自算 `revenueYoY/netProfitYoY`（BUG-4）；**注意 8 期只能算出最近 4 期**，要么把取样提到 12 期，要么明说后 4 期不可推（R-4(4)）；算出来的值要能与数据源给的同比区分（`revenueYoYSource: "computed"`） |
| `api/app/services/snapshot.py:257-276` | 清除 `pe` 永假分支（BUG-5） |
| `api/app/services/numeric_claims.py:501-536` | `extract_claims()` 纳入 `doc.risks[].{title,trigger,mitigant}`（BUG-6） |
| `web/components/report/ReportPanel.tsx:585-604` | **新增**：风险清单的 `<li>` 补 `data-block-id={\`risks#${index}\`}`，否则 `risks#N` 样本在徽标里点不动（今日 `ReportPanel.tsx:219-222` 的 `querySelector` 命中不到任何节点，静默无动作，见 R-4(5)） |
| `api/tests/test_market_data.py` / `api/tests/test_price_consistency.py`（新增） | **新增**判据：重叠区间逐根相等、跨日稳定、`quote` 五字段与 `bars` 末两根逐值相等、窗口覆盖率、`mean_gap < mean_body`（R-4/R-5）。后端既有断言无需修改 |
| `web/tests/`（新增 `price-panel.test.tsx`） | 一对数字同源 + 标签跟随同源的 vitest（现有 `web/tests` 无任何 PricePanel 用例） |
| `api/tests/*` / `web/tests/*` | 按 §6.4 的 5 条判据补/改用例 |

---

## 附录 A：本轮 5 项发现台账

> 每条都带可执行复现与实测证据；供 Subtask #1/#2/#3 直接消费。

### BUG-1（P0）：`/bars` 缓存永不失效 + provider 随机游走起点由 `start_ts` 决定 ⇒ 当日收盘价随区间漂移

- 证据：§2.4 的 6 行 HTTP 实测（1729.52 → 1421.10 → 1729.52）、`repro/range_drift.py` 的 5 行漂移表（1365.21~1688.38）
- 代码：`synthetic_provider.py:145-157`、`routers/instruments.py:73`、`cache.py:43-76`
- 复现：`python .agentmesh/repro/bars_cache_probe.py`
- 影响：任何区间切换 / 冷缓存首次访问；技术指标与研报快照同源被污染

### BUG-2（P0）：PricePanel 头部价格取 `bars`、涨跌幅取 `quote`，渲染出不存在的数据点

- 证据：`1,729.52 ▼2.2%`，而 bars 自身涨跌幅为 `+0.46%`（`1729.52/1721.63-1`）
- 代码：`web/components/chart/PricePanel.tsx:103-112`、`web/components/shell/AppShell.tsx:305-306`
- 复现：§2.3
- 影响：所有标的、所有数据源（真实源同样会在 quote 比 bars 新一根时发作）

### BUG-3（P1）：`quote.day_high/day_low` 围绕 `last` 对称构造，产生 `dayHigh < prevClose` 的非法日 K

- 证据：`quote` 返回 `last=1634.88, prevClose=1672.0, dayHigh=1654.5`——最高价低于前收，却收在 1634.88
- 代码：`synthetic_provider.py:188-189`
- 复现：`python .agentmesh/repro/findings_evidence.py`（`dayHigh<prevClose=True`）
- 影响：任何"今日振幅/区间"读数；若将来接入 K 线标记会直接误导
- **【评审补充 R-6】归因要下移一层，且本节标题里的"非法"一词是误判。** `quote.day_high/day_low` 确实错（围绕 `last` 对称构造，`:188-189`），但"最高价低于前收"这个**现象**在 `api/data/app.db` 的 784 行 600519.SH 日线上出现 297 次（37.9%），**297 次全部**是"开盘就低于昨收"的**合法跳空低开**（`open<prevClose` 且 `high<prevClose`），"开盘高于前收却最高价低于前收"的非法样本 **0 次**：

```text
days where high < max(open,close,prevClose): 297 of 783
  of which open<prevClose (legit gap-down):  297
  open>prevClose but high<prevClose (impossible): 0
```

真正该改的第一层是 `bars()` 的开盘价（围绕**今日 close** 抖动，而不是从**昨收**跳空），实测隔夜跳空均值 **1.469%** 反而是日内实体均值 **0.351%** 的 4 倍，方向与真实市场相反（§5.3 R-4(2)）。所以：按原建议"采 (a) 由 `bars[-1]` 派生"修完后，`quote.day_high < quote.prev_close` 仍会在 37.9% 的日子里成立——只是这次它**是对的**（真实的低开日就是如此）。§5.2 表里 (b) 的"校验 `high ≥ max(open,close,prevClose)` 后 clamp"**不可采用**：它会把合法的低开日硬拉成"最高价不低于昨收"，等于为过测试而改数据。

### BUG-4（P1）：接地判分把"模型自算的同比"判成编造，诚实报告被压成 `degraded`

- 证据：v11 报告 `factClaims=270 / factMissing=103`（`ratio=0.6185`）；其中 **33 条**来自"同比"（`-0.25`×8、`1.82`×6、`-1.20`×4、`-4.53`×4、`6.34`×4、`1.47`×3…）。快照 8 期都带 `revenue/netProfit`，但 `revenueYoY/netProfitYoY` 全为 `null`（`akshare_provider` 未给），`fundamentals.md` 逐行印 `n/a`。把 8 条 YoY 写回快照后实测 **`factMissing 103 → 70`，`ratio 0.6185 → 0.7407`**
- 代码：`api/app/services/snapshot.py:192-204`、`:331-362`；判分处 `numeric_claims.py:539-582`、`report_validator.py:38,348-364`
- 复现：`python .agentmesh/repro/grounding_yoy_experiment.py`
- 影响：全部真实研报（只要数据源不提供同比）；直接影响 `status` 与 UI 黄条
- **【评审补充 R-7】这个证据链经复核成立，而且是本轮最扎实的一条，但有两点必须钉住。**
  1. **恢复的 33 条确实是"真数字"，不是判据松动的巧合。** 评审把实验补进快照的 8 个值与模型原文逐个对照，全部相等（同季同比，不是环比）：
     - 2026Q3 营收 `133,283,652,508.95 / 130,903,889,634.88 − 1 = +1.8169%` → 文中 `+1.82%`（6 次）
     - 2026Q1 营收 `54,702,912,385.23 / 51,443,450,583.77 − 1 = +6.336%` → 文中 `+6.34%`（4 次）
     - 2025Q4 营收 `172,054,171,890.91 / 174,144,069,958.25 − 1 = −1.2003%` → 文中 `−1.20%`（4 次）
     - 2026Q3 净利 `64,467,425,981.95 / 64,626,746,712.18 − 1 = −0.2464%` → 文中 `−0.25%`（8 次）
     - 2026Q1 净利 `27,242,512,886.45 / 26,847,474,238.76 − 1 = +1.471%` → 文中 `+1.47%`（3 次）
     - 2025Q4 净利 `82,320,067,101.68 / 86,228,146,421.62 − 1 = −4.533%` → 文中 `−4.53%`（4 次）
     所以这是**快照缺字段**（模型该引用却只能自己算），不是"模型编造"、也不是"阈值定太高"。把阈值从 0.9 调低只会掩盖问题，而"把同比写进 prompt 白名单"是另一回事（见下）。
  2. **"为 8 期自算"做不到 8 期，实测只能填 4 期。** 实验脚本自己打印的就是 `derived YoY values written into the snapshot copy: 8`——8 = 4 期 × 2 字段；同季同比需要"去年同季"，而快照只取 8 期（`snapshot.py:133` 的 `registry.fundamentals(instrument.symbol, 8)`）。所以落点必须二选一并写清楚：取样提到 12 期，或明确"只有最近 4 期可推、后 4 期仍是 `n/a`"。另外算出来的同比要能与数据源给的同比区分（例：`revenueYoYSource: "computed"`），否则未来没人知道这个数是算的还是上游给的——与 README「不回填（回填会伪造数据）」是同一条原则。

### BUG-5（P2）：`_derive_valuation` 里的永假分支，`pePercentile3y` 恒为 `None`

- 证据：`inspect.getsource` 实测 `"pe = None"` 出现 2 次、`"pe is not None"` 1 次；`if latest and ...: pe = None` 是空操作，随后 `if pe is not None:` 永不进入
- 代码：`api/app/services/snapshot.py:257-276`
- 复现：`python .agentmesh/repro/findings_evidence.py`
- 影响：无声（不报错、不缺字段），但会让人误以为"估值分位曾经算过、只是数据缺失"

### BUG-6（P1）：读者可见的「风险清单」数字完全不在溯源分母里

> **【评审补充 R-8】这条发现成立（分母确实漏了一整块），但它的候选修法"UI 已支持按块跳转"是错的：`risks#N` 今天是一个死链接。** `ReportPanel.tsx:585-604` 渲染风险清单时 `<li>` 上没有任何 `data-block-id`，而两处跳转实现（`:219-222`、`:242-246`）都靠 `querySelector('[data-block-id="…"]')`；`parseBlockId("risks#0")` 不成立 → 走 `:239-247` 的"非章节定位"分支 → `target` 为 `null` → `scrollIntoView` 静默不执行（无报错、无高亮、无滚动）。同时风险清单**不是** `ReportRenderer` 的块（脚注挂在 `ReportRenderer.tsx:180-184` 的块外层），所以 `risks#N` 的样本连脚注都不会出现。修法必须是两处：后端 `extract_claims()` 增收 `doc.risks[]` + 前端 `<li>` 补 `data-block-id`。只做后端，用户会看到一个点不动的"1 个数字未找到依据"，比不进分母更糟。详见 §5.3 R-4(5)。

报告里 `doc.risks[]`（5 条：`title` / `trigger` / `mitigant`）由 `ReportPanel` 直接渲染成「风险清单」（`web/components/report/ReportPanel.tsx:579-601`），但 `numeric_claims.extract_claims()` 只遍历 `doc.sections[].blocks[]` 与 `doc.keyMetrics[]`（`api/app/services/numeric_claims.py:501-536`），**`doc.risks[]` 一个数字都不审计**。

- 证据（从浏览器 DOM 抓取的真实渲染文本）：

```text
风险清单
高  行情快照与日线口径冲突，估值字段全空
    触发：quote.last 与 2026-11-16 日线收盘偏差 ≥5%（当前 5.47%），且 peTtm / pb / marketCap 仍为 null
高  单季营收再度转负，零增长固化为连续两年
    触发：任一单季营收同比低于 -1.2%（FY25 全年水平），26Q2 已录得 -5.23% · 应对：以 26Q4 单季同比 ≥ +3.0% 作为情景升级的唯一门槛
中  净利率下行扩散至全年口径
    触发：FY26 净利率低于 FY25 的 47.8%，或再现 26Q1 式 -2.4pp 的单季压缩
中  技术面破位引发趋势资金离场
    触发：连续两日收盘低于布林下轨 1688.95 元，或跌破 60 个交易日收盘低点 1563.17 元 · 应对：以 MA20 1775.79 元收复…
```

- 量化（`python .agentmesh/repro/risks_claims_gap.py`）：

```text
claims found in doc.risks[]:              20  Counter({'identifier': 10, 'fact': 10})
claims found in sections[]+keyMetrics:   549  Counter({'identifier': 273, 'fact': 270, 'projection': 6})
combined:                                569  Counter({'identifier': 283, 'fact': 280, 'projection': 6})
→ 10 条 fact claim（其中 4 条未接地）、10 条 identifier 从未进入任何分母与样本
```

- 代码：`api/app/services/numeric_claims.py:501-536`（分母定义）、`web/components/report/ReportPanel.tsx:579-601`（渲染）
- 复现：打开 `http://localhost:3000/CN%3A600519.SH` → 报告正文末尾「风险清单」；再跑上面的脚本
- 影响：报告头徽标"已核验/未找到依据"的分母漏掉一整块读者可见内容——恰好是最该被核的那块（触发条件里全是阈值数字）。这与模块 docstring 自己写的"分母集合（写死）"和产品的诚实原则冲突：**展示给读者的数字必须进分母，否则"已核验"就是一句不完整的承诺**。

---

## 附录 B：证据与脚本清单（均可复跑）

| 路径 | 内容 |
| --- | --- |
| `.agentmesh/repro/bars_cache_probe.py` | BUG-1 的 HTTP 级复现（1729.52 ↔ 1421.10） |
| `.agentmesh/repro/range_drift.py` | BUG-1 的 provider 级漂移表（1M…3Y） |
| `.agentmesh/repro/findings_evidence.py` | BUG-3 / BUG-5 的代码级判定 |
| `.agentmesh/repro/grounding_probe.py` | BUG-4 的报告实测（`factClaims/factMissing/samples`） |
| `.agentmesh/repro/grounding_yoy_experiment.py` | BUG-4 的反事实实验（补 YoY 后 ratio 0.6185→0.7407） |
| `.agentmesh/repro/risks_claims_gap.py` | BUG-6 的分母缺口量化（20 条 claim 在审计之外） |
| `.agentmesh/repro/grounding_reproducibility.py` / `grounding_all_reports.py` | 附录 C 的"持久化指标不可复现"实测 |
| `.agentmesh/repro/api_probe.py` | 21 个端点的状态码契约巡检（全部符合文档，无 5xx） |
| `.agentmesh/ego/diag-20261117/round-01..09.mjs` | ego-browser 探索脚本（落地页 → 工作台 → 区间 → 命令面板 → 版本比较 → 风险清单） |
| `.agentmesh/ego/diag-20261117/*.png` | 截图：`landing` / `workspace` / `price-mismatch` / `range-drift` / `compare` / `risk-list` |
| `docs/diagnoses/chart-price-integrity/analysis.md` | 本文档 |

---

## 附录 C：一处**未归因**的观察（留给 Subtask #1 判定，不作结论使用）

报告头徽标当前渲染的是 **"141 个数字未找到依据"**（浏览器实测），但用**同一份** `doc_json` 与 **同一份** `snapshot.json` 在当前代码上复算得到的是 **103**：

```text
$ python .agentmesh/repro/grounding_reproducibility.py
stored quality.grounding: {"factClaims": 323, "factMissing": 141, "identifierClaims": 280, ...}
stored ungroundedCount: 141 groundedRatio: 0.5635
recomputed:               {"factClaims": 270, "factMissing": 103, "identifierClaims": 273, ...}
recomputed ratio: 0.6185
DB 的 doc_json 与 workspace/runs/<id>/report.json 解析后完全相等（仅缩进不同，23231 vs 33559 字节）
```

- 差值 53 fact / 7 identifier。已验证**不是** `doc.risks[]` 造成的（那只能解释 +10/+10，见 BUG-6）。
- 全库横向对比（`grounding_all_reports.py`）显示：只有 v11 存了 `quality.grounding`，其余 12 份为 `null`（符合 README"老报告不回填"）；且多份历史报告的 `groundedRatio` 与当前代码复算值不同（如 v7 存 0.8803 / 复算 0.8092）。
- **结论状态：未归因。** 最可能是"生成时的代码与当前 HEAD 不一致"（这些报告是开发过程中的产物，最后 3 个 commit 之后未再生成过 v11）。因此本节**不作为 bug 上报**，只作为下游注意项：
  1. 任何修改 `numeric_claims` 分类规则的补丁，都会让**存量** `quality.grounding` 与代码永久分叉，UI 上的 141/103 差异会继续存在；
  2. 若 Subtask #1 决定收口，建议的方向是"读取时按当前代码复算并标注 `quality.grounding.recomputedAt`"，而不是回填历史数据（回填会伪造数据，与 README 的既有原则冲突）；
  3. 在归因前**不要**依据 141/103 的差异去改判分逻辑——那是把版本漂移当成回归。

> **【评审补充 R-9】附录 C 的"未归因"保持不动是对的，但归因范围可以再收窄一格。**
>
> `factClaims/identifierClaims` 的**总和**与分类无关（`classify()` 只在 fact / identifier / projection 之间搬运，不增删数字）。HEAD 复算的三类合计是 `270 + 273 + 6 = 549`，而存量记录是 `323 + 280 + ≥0 ≥ 603`——**多出至少 54 个数字**。所以差值**不可能**是"分类规则（`classify()`）改动造成的"，只能是**被枚举的 claim 位点变多了**（`_claims_for_block()` 的字段遍历被扩充，例如 v6 把 `comparables/scenario/metrics` 的结构化字段纳入）或者**文档本身数字更多**。
>
> 这条收窄有直接的操作意义：下游若要收口，该比较的是**历史上 `_claims_for_block()` 的字段清单**，不是 `classify()`；同时也再次印证"不要按 141/103 去改判分"——那个差值里至少有一部分是位点扩充的**正当结果**，不是任何人的 bug。

---

## 评审结论

> Subtask #1 · Senior Review。本节只做两件事：给证据链定性，给出**唯一的推荐修复**。本节不含代码修改（那是 Subtask #2 的边界），也不做 `git commit`。

### 一、证据链裁定

| 主张 | 裁定 | 依据（本轮评审实测/代码引用） |
| --- | --- | --- |
| BUG-1 根因：`/bars` 无新鲜度 + walk 起点由 `start_ts` 决定 → 区间决定当日收盘 | **成立**，且比原文更严重 | spec §9 明确要求 `bars` 走服务端 TTL（15min/6h/历史永久），`/bars` 未接线是**违约**而非设计取舍；根因表述应改为"日历日期未进入 walk"（R-1）；新增实测：跨日重算时 **782/782** 个历史收盘价全变 |
| BUG-1 的缓存机制描述（`write_bars` 整段覆盖、1m 覆盖 3y 尾部） | **部分不成立** | `cache.py:91-138` 已按 `ts` upsert；真实伪影是**两条 walk 拼接**（接缝 −6.07%），R-2(c) |
| BUG-1 未覆盖的第二个洞：缓存窄于请求时静默返回截断窗口 | **新发现，P1** | 全新库先取 `1m` 再取 `3y` → 22 根、`cached=true`、`stale=false`、无告警（R-2(a)） |
| BUG-2 根因：`PricePanel` 价格取 bars、涨跌幅取 quote | **成立** | `PricePanel.tsx:103-112` + `AppShell.tsx:305-306,363-364`；`1,729.52 ▼2.2%` 与 bars 自洽值 `+0.46%` 矛盾 |
| BUG-2 的修法（"quote 在场时优先 quote"） | **不成立/会搬家** | 蜡烛序列 `lastValueVisible` 未关（`CandleChart.tsx:125-132`），价格轴自己印 1729.52；按原修法会变成"头部 1634.88 vs 轴 1729.52"（R-3） |
| BUG-3 `quote.day_high/day_low` 围绕 last 对称构造 | **成立，但归因偏低一层** | 37.9%（297/783）的 `high<prevClose` **全部**是合法跳空低开；第一层错在 `bars()` 的 `open` 未锚定前收（gap 1.469% > body 0.351%，方向相反）（R-6） |
| BUG-3 的 clamp 修法 (b) 与 §6.4 判据 3 | **不可采用** | 会把合法低开日改成"最高价不低于昨收"= 造数据；判据 3 逐日断言必红、单日断言是假绿（R-5） |
| BUG-4 快照缺同比 → 33 条真数字被误判 | **成立，且本轮最扎实** | 6 个恢复 token 与同季同比**逐位相等**（1.82 / 6.34 / −1.20 / −0.25 / 1.47 / −4.53）；不是阈值问题，不要调阈值（R-7） |
| BUG-4 的"为 8 期自算" | **不可实现 8 期** | 8 期只能算出最近 4 期（实验脚本自己打印 `8` 个值 = 4×2）（R-7） |
| BUG-5 死分支 | **成立** | `snapshot.py:257-276`：`pe = None` → `if pe is not None:` 永假；属清理类，不影响用户可见数字 |
| BUG-6 `doc.risks[]` 不进分母 | **成立** | `numeric_claims.py:501-536` 只遍历 `sections[]`/`keyMetrics[]`；20 条 risk claim（10 fact / 10 identifier）在审计之外 |
| BUG-6 的"UI 已支持按块跳转" | **不成立（死链接）** | 风险清单 `<li>` 无 `data-block-id`；跳转 `querySelector` 命中 `null` 后静默无动作（R-8） |
| **R-1（附录 C）141 vs 103** | **保持未归因**，但可收窄 | 分类器不可能解释（claim 总数 549 → ≥603，多 54 个位点），方向是 `_claims_for_block()` 的字段扩张（R-9） |
| 附录 C 的"不要按 141/103 改判分" | **采纳** | 同上 |

**未被证伪、也未改动的部分**（下游可直接沿用）：§1 的三价格并存现象与截图；§2 的四条复现（`bars_cache_probe.py` 的 6 行输出、`range_drift.py` 的 5 行漂移表复跑一致）；§3.1 的 `quote()` 另一套 RNG；§3.5 研报正文自我解释的原文；§4 影响面；§5.1 对方案 C（新增 gateway 层）的否决理由——**评审同意否决 C**，本项目 README 的"不允许第二个并发真源"原则比"单一价格真源"这个抽象更值钱。

### 二、最终推荐修复（唯一方案）

**方案 A′：让价格只有一个生产者，让日期只有一个含义。**

一句话：**`SyntheticProvider` 从一个写死的日历锚点出发、用带弱均值回归的单条随机游走生成整段历史；`quote()` 的五个字段全部取自这条游走的末两根；`/bars` 在"新鲜 **且** 覆盖住请求窗口"时才允许命中缓存；前端一对数字同源、标签随源。** 这是一套改动，不是菜单——五处必须同批落地，缺任一处，屏幕上仍会出现两个价（R-4(3) 的存量缓存尤其容易让人误判"没修好"）。

落地顺序与最小改动量（详细落点见 §6 表格，已按评审修正）：

1. **`synthetic_provider.py:142-176`（约 10 行）**：`cursor` 起点改为模块常量 `_WALK_ANCHOR`（例 `date(2015, 1, 1)`）；`price` 的迭代加 `kappa * (math.log(base) - math.log(price))`，`kappa≈0.003`；`open` 改为 `prev_close * (1 + gauss(0, vol/6))`；循环结束后按 `start_ts` 切片。**不要**改成"按日期独立取值"（K 线会变白噪声）。
2. **`synthetic_provider.py:179-193`（约 10 行）**：`quote()` 复用同一条游走的末两根，`last/prev_close/day_high/day_low/volume` 全部派生；删掉第二个 RNG 与 `last*1.012/0.988`。
3. **`cache.py:79-88` + `instruments.py:73`（约 15 行）**：重写新鲜度谓词（判"最后一根 bar 是否已到最近一个已收盘交易日"，不是"距现在多久"），并加上**窗口覆盖率**校验；两者任一不满足才回源。
4. **代次失效（约 5 行 + 1 个常量）**：provider 名升 `synthetic-v2`，`read_bars` 的 `source` 与当前 provider 名不符即视为未命中，让存量旧 walk 的行一次性作废（零 DDL）。
5. **`PricePanel.tsx:103-112` + `CandleChart.tsx:125-132`（约 20 行 + 1 个 vitest）**：一对数字同源，轴标签与日期 Tag 跟随同源；`bars` 为空时整对回落 `quote`。
6. **测试（新增，不改既有断言）**：§5.3 R-4/R-5 列出的 6 条判据；基线 `pytest --collect-only` = **626**，`test_market_data + test_numeric_claims + test_indicators` = **68 全绿**，改动后条数与绿度都不得下降。

**为什么是它，而不是别的**：

1. **它修的是违约，不是取舍。** 模块 docstring 承诺"same symbol always produces the same prices"，spec §9 承诺 `bars` 走 TTL——两条契约都被实现违反。任何只动前端的方案（原方案 B）都留着违约。
2. **它是唯一能同时消灭三个症状的最小改动**：区间漂移、历史每天被重写（R-1 新发现）、末根随窗口变。三者共享同一个根因，分三次修等于三次回归。
3. **它不引入新抽象。** 方案 C 的"gateway 单一价格真源"更漂亮，但会让"第 5 个可以不同步的地方"出现，且本项目已用"缓存 + TTL + `meta.source`"表达同源，不需要再叠一层。**评审维持对 C 的否决。**
4. **它的取舍被显式记录**：`BASE_PRICE_BY_SYMBOL` 从"今天的股价"降级为"锚点价 + 均值回归中枢"。三者（窗口无关 / 历史稳定 / 今日精确等于 base_price）不可兼得，而当前 bug 正是"每天重新锚定"换来的第三条——用可复现换精确，方向正确。
5. **它顺手修掉两处被误判的证据**：`write_bars` 落点无效（省掉一次无效改动）、`risks#N` 死链接（避免改出一个点不动的徽标）。

**明确不要做的五件事**（防止下游"顺手"引入回归）：

- ❌ 不要按 §6.4 判据 3 逐日断言 `dayHigh ≥ prev_close`，更不要 clamp——`high < prev_close` 在低开日合法（297/783，全部合法）；
- ❌ 不要把"按日期独立取值"当成 walk 的修法（白噪声）；
- ❌ 不要把 `GROUNDED_THRESHOLD` 从 0.9 调低来消 BUG-4（恢复的 33 条是真数字，降阈值只是让假数字也过关）；
- ❌ 不要回填存量报告的 `quality.grounding`（R-1 未归因 + README 原则）；
- ❌ 不要因为 141 vs 103 去改 `classify()`（R-9：分类器解释不了这个差值）。

### 三、残留风险（交给 Subtask #2 决策）

| 风险 | 影响 | 处置建议 |
| --- | --- | --- |
| 固定锚点的构造成本 | `/bars` 每次冷启动 + 研报快照 | 按 (symbol, timeframe, adjust, today) 做 `lru_cache`；实测构造 7,012 步（2000→今天）约 **47ms/标的**，属可接受量级 |
| 锚点日期一改，所有合成价位变一次 | 演示截图、历史 run 的 `bars.csv` 与 doc 里的数字会与新价不符 | 只影响演示与旧 run，不改历史数据；在 commit message 与 README 的"合成数据"段落里写明"本轮合成价位整体变更一次" |
| `akshare` 真源的行与新 `synthetic` 行混存 | `meta.source` 仍只报最后一行（R-2(d)） | 代次校验（第 4 步）已能挡住 synthetic 的旧行；若要更严，可在 `read_bars` 里对 `source` 做众数或"混合即视为未命中" |
| BUG-4 取样提到 12 期 | 快照体积 / prompt 长度 / `fundamentals.md` 表格行数 / 快照契约测试 | 评审倾向"提到 12 期"（这样 8 期都有同季同比可引，报告里写出的同比才全可核）；若不动，必须在 `fundamentals.md` 区分"数据源未提供"与"仅最近 4 期可推" |
| BUG-6 的前端补 `data-block-id` | 触碰 `ReportPanel` 的 DOM 结构，`ReviewRegressions` 类测试可能受影响 | 只加属性不改结构；补一条 `blocks.test.ts`/`grounding.test.tsx` 断言 `[data-block-id^="risks#"]` 存在 |

### 四、交给 Subtask #2 的一句话验收

> **同一个标的、任意两次请求、任意两天：`bars(range="1m")[-1].c == bars(range="3y")[-1].c == quote().last`，且 `ts < today` 的历史收盘价永不改变；午休/周末打开页面时 `meta.cached` 仍为 `true`；头部显示的价格、涨跌幅、日期与图表价格轴来自同一个源。**

一句话判据（若只能留一条）：**"今天看到的 2026-11-13 收盘价，明天还是同一个数"——当前实现 0/782 通过。**

---

## 实施过程发现的方案缺陷

> Subtask #2 · Fix Implementation。本节记录**落地 A′ 时实测到的、方案本身没有说清或说错的地方**。
> 每一条都附改动前后的实测输出。方案的主线（固定日历锚点 + 弱均值回归 + 开盘锚定前收 +
> 缓存覆盖/代次 + 前端一对数字同源）**照做可行**，不需要推翻；下面五条是照做时会踩到的坑。

### I-1（**方案会回归**，已按修正落地）`retest` 前必须先清缓存代次，否则"修好了"在存量库上仍然显示两个价

方案 R-4(3) 只说了"代次校验让存量旧行一次性作废"，没说**作废发生在哪一步**。落地时发现：
`read_bars()` 返回旧行 → 路由判 `used_cache=False` → 回源写新行 **按 `ts` upsert 覆盖窗口内每一天**。
本机 `api/data/app.db` 的 `bars` 表里 `synthetic` 旧行与 `synthetic-v2` 新行**按标的分居**：
先被访问的标的（`CN:600519.SH` 783 行）被完整覆盖、旧行归零；后访问的标的
（`US:AAPL`，旧行原本 784 行而窗口只覆盖 262 行）留下 `synthetic` 784 + `synthetic-v2` 21，
**窗口外那 500+ 行旧值永久不动**（`read_bars` 的 `ts >= start_ts` 永远不会再读到它们，所以不可见，
但它们是下一个 bug 的温床）。

处置：在 `cache.read_bars()` 里做**读时退休**——只要本次读取命中任一行 `source == "synthetic"`
（v1 生成物），就把该 (instrument, timeframe, adjust) 的 v1 行全部 `DELETE` 并返回空，让调用方回源。
合成数据可丢弃，`akshare`/`yfinance` 的行**永不触碰**（有专门用例断言）。
`Bar.source` 里写的是 `synthetic-v2`，对外仍报 `synthetic`（`_public_source`），否则前端
"合成数据"徽标（`PricePanel.tsx:218` 比 `=== 'synthetic'`）会静默消失。

### I-2（**方案的两条判据互斥**，必须按"落在哪根 bar 上"分开写）新鲜度谓词与 `_bars_warnings` 用的是同一个阈值的两种语义

R-2(b) 把"新鲜"定义为"缓存里有到最近一个已收盘交易日的 bar"。按字面实现后，一个
**已退市/长停牌**的标的（最后一根 30 天前）每天都会被判"不新鲜"→ 每次请求都回源 →
`/bars` 变成不再缓存。处置：保留一条"历史地板"分支（最后一根 bar 早于 7 天前即视为新鲜，
`HISTORICAL_FLOOR_DAYS` 原样复用），把"这根 bar 旧了"这类告警留在它该在的地方
（`snapshot._bars_warnings` 的 `lag_days > 3`）。**这不是妥协，是两层语义**：
缓存新鲜度回答"我还缺不缺今天"，数据陈旧度回答"这个标的还活着吗"。

### I-3（**方案的判据会假绿，已换成更强的写法**）"`quote` 五字段与 `bars` 末两根逐值相等"在真实数据源上不成立，只能对 synthetic 断言

方案 §5.3 R-5 把"`quote().last == bars()[-1].close`"写成跨源同源判据。落地时确认这条**只在
synthetic 上成立**：`akshare_provider.quote()` 自己的实现是"取 14 天 bars 的末两根"
（`akshare_provider.py:110-130`），但 `bars()` 与 `quote()` 是**两次独立的网络往返**，
中间可能跨越收盘。所以该判据被落成三档：
① 供应商层（`SyntheticProvider`）：**逐字段相等**，`14 个标的 × 5 个字段` 全断言；
② HTTP 层：`/quote` 与 `/bars?range=1m` 的 `last/dayHigh/dayLow` 相等（conftest 固定
`DATA_PROVIDERS=synthetic`，所以这一档是确定的）；
③ 真实源：不写死相等，改由"缓存 + TTL"表达。
**另外**：`quote()` 必须读**与 `/bars` 默认参数同一串行**的 walk（`1d` + `qfq`）。这条约束在方案里
是隐含的（"复用同一条游走的末两根"），但实现里如果让 `quote` 用 `none` 复权，两侧立刻分叉——
代码里已用 `_QUOTE_TIMEFRAME`/`_QUOTE_ADJUST` 两个常量把这一致性写成显式的。

### I-4（**方案低估了一个数量级**）锚点构造成本不是 47ms/标的，冷启动是 114ms，且**必须**按天缓存

R-4(1) 实测 7,012 步 47ms（`date(2000,1,1)` 起）。落地后同一份代码在本机测得 **114ms**
（多了均值回归的一次 `log`/`exp` 与开盘价序列）。`/bars` 与 `/quote` 各建一次 = 228ms/标的，
对 3 个标的的左栏就是 700ms 的首屏。处置：`functools.lru_cache(maxsize=32)` 按
`(symbol, timeframe, adjust, today)` 缓存，**`bars()` 与 `quote()` 共用同一个缓存条目**——
这正是"`quote` 必须复用同一条 walk"那条约束的第二个理由（第一个理由是数值一致性）。
`today` 进 key 意味着跨天自动失效，不需要任何失效钩子。

### I-5（**方案漏掉的第四处混源，同类但是独立缺陷**）左栏与头部在"数据源链不同步"时仍会各说各话

`PricePanel` 修好之后，屏幕上仍可能出现"左栏 1,439.89 / 头部 1,753.06"。实测原因**不是**本次
要修的两个源，而是**同一条链在不同时间给出了不同供应商**：

```text
$ curl /capabilities            → dataProviders: akshare(available) yfinance(unavailable) synthetic(available)
$ sqlite3 app.db "select instrument_id,last,source,updated_at from quotes"
('CN:600519.SH', 1439.89, 'akshare',  '2026-11-17T07:00:00Z')   ← updated_at 在未来（bar 的 07:00 收盘戳）
('US:AAPL',      330.46,  'synthetic','2026-11-17T03:08:06Z')
$ curl /instruments/CN:600519.SH/bars → meta.source = synthetic, last close 1753.06
```

`akshare` 的 `quote()` 把 `updated_at` 填成**当根 bar 的收盘时刻**（07:00），而不是"取数时刻"；
于是 `cache.quote_is_fresh()` 拿它和 `now`（03:10）比，`now - stamped` 为负、永远 `< ttl`，
**这条 akshare 报价再也不会过期**——即使 `bars` 那条链已经掉到 `synthetic`。
处置建议（**未在本轮修改**，因为超出 A′ 范围且需要单独判据）：
① `akshare.quote()` 的 `updated_at` 改为取数时刻，bar 时刻另开字段；
② `quote_is_fresh()` 增加 `stamped > now + 5min` 的"未来时间戳"拒绝分支；
③ `/bars` 与 `/quote` 在 `meta.source` 之上再加一层"同一标的两次响应来源是否一致"的对账。
本轮只把证据留给下一轮，避免把"一个 bug 的修复"扩成"三个 bug 的重构"。
（上面那条 akshare 报价行已在本机 `api/data/app.db` 里删除，以便复现 A 在 `DATA_PROVIDERS=synthetic` 下干净复现。）

### 落地后的实测（同一脚本，改动前 → 改动后）

`.agentmesh/repro/before_after.py` 只用 provider 的公开 API，所以两边跑的是同一段代码：

```text
                                   改动前                     改动后
600519.SH 1m[-1].c vs 3y[-1].c     1421.10 != 1729.52        1753.06 == 1753.06
600519.SH quote.last vs bars[-1]   1634.88 != 1729.52        1753.06 == 1753.06
600519.SH quote.prevClose vs [-2]  1672.00 != 1721.63        1711.62 == 1711.62
600519.SH quote.dayHigh vs [-1].h  1654.50 != 1747.98        1766.10 == 1766.10
历史收盘价随"运行日+1"被改写        782/782                    0/782
mean_gap vs mean_body              1.47% vs 0.35%（反了）      0.24% vs 1.53%
AAPL 同上四项                      全不等                     全相等
```

`high < prevClose` 的 297/782 → **60/782**，且这 60 天**全部**是 `open < prevClose` 的合法低开
（与 §5.3 R-5 的裁定一致：这不是要清零的量，是要回到"跳空低开"这一唯一合法成因上）。

界面复现 A（`.agentmesh/repro/screen_pair.mjs`，Playwright 实拍）：

```text
左栏： 贵州茅台  1,753.06  ▲ 2.4%
头部： 贵州茅台 600519.SH  1,753.06  ▲ 2.4%  CNY
价格轴标签：                 1753.06
日期 Tag：                   2026-11-17
```

三者同源、同值、同标签。截图：`.agentmesh/repro/screen_pair.png`（报告正文仍是上一轮生成的旧版本，
其中"quote.last 1634.88 与日线 1729.52 相差 5.47%"那两段属于**存量报告**，按 README"老报告不回填"
原则不追溯——重新生成一份即不会再出现该段）。


---

## 评审与收口（Subtask #3 · Senior Review）

> 本节是**最后一个节点**：逐项核对 A′ 的落地、直接改源码、只把这一个 bug 的文件放进暂存区，然后一次 `fix:` 提交。
> 结论：**A′ 的六件事都真的落地了，根因（日历日期没进入随机游走 / 缓存只看"有没有行"）被修掉，不是症状级打补丁**；
> 但仍有四处同族缺陷会在真实数据源下把两个价重新放回同一块屏，本轮直接改掉：新鲜度谓词只覆盖了收盘后（§R3-1）、
> 新鲜度没有缓存身份（§R3-2）、混合生产者的接缝被当成缓存命中（§R3-3）、真实源报价的时间戳让它永不失效（§R3-4）。

### 一、逐项核对（diff vs §6 落点清单）

| 落点 | 实际改动 | 判定 |
| --- | --- | --- |
| `synthetic_provider.py` `bars()` | 循环起点改为模块常量 `_WALK_ANCHOR = date(2000,1,1)`，漂移项加弱均值回归 `_KAPPA=0.003`，`open` 改为从 `prev_close` 跳空（`vol/6`），末尾按 `start_ts` 切片 | **成立，命中根因**。评审独立复测（§二）：`1m[-1] == 3y[-1] == quote.last`（5 个标的全部相等）、历史随运行日推进被改写 `0/783`（原 `782/782`）、`mean_gap 0.24% < mean_body 1.53%`（原 `1.47% vs 0.35%`，方向是反的） |
| `synthetic_provider.py` `quote()` | 删掉第二个 RNG 与 `last*1.012/0.988`，五个字段全部取自同一条 walk 的末两根；`_QUOTE_TIMEFRAME/_QUOTE_ADJUST` 显式对齐 `/bars` 默认参数；`lru_cache(maxsize=32)` 让 `bars()`/`quote()` 共用一次构造 | **成立**。五字段逐值相等，冷建 89–135ms，`0.67MB`/标的 × 32 ≈ 21MB 上界，可接受 |
| `cache.py` `read_bars` | 读时退休：本次读到任一行 `source='synthetic'` 就把该 (instrument, timeframe, adjust) 的旧行 `DELETE` 并返回空 | **成立且必需**（I-1）。实测存量库：`600519.SH` 783 行已全部重写、`AAPL` 旧行归零、`300750.SZ` 784 行旧行仍在（尚未被读，首次读时退休）；`akshare/yfinance` 行有用例保证不碰 |
| `cache.py` `bars_cover` | 缓存首行比请求窗口晚超过 `COVERAGE_TOLERANCE_DAYS=10` 即视为未覆盖 | **成立**。10 天是为了 A 股长假（国庆/春节连休 7–9 天无成交），短于这个数会把合法长假判成"缓存窄" |
| 新鲜度（`cache.py` / `instruments.py` / `snapshot.py`） | `is_bars_cache_fresh` 重写成"是否持有最近的会话"；`/bars` 与研报快照都要求"覆盖 **且** 新鲜" | **只对了一半 → 本轮修掉，见 §R3-1**。同一条规则也被加进了 `snapshot.py`（前驱节点的 `--output` 没提这个文件，评审确认它在范围内：研报的 3y 底座同样会被窄缓存截断） |
| `PricePanel.tsx` | `pairSource` 三态：有 bar 就整对用 bars（价格/涨跌/日期 Tag 全跟随），只有图里没有 bar 时才整对回落 quote | **成立，且比 R-3 原稿更简单**。R-3 原本要给 `CandleChart` 加一个 `lastValueVisible` 开关；既然默认口径永远是 bars，"轴标签 = 头部数字"就自动成立，不需要新开关——**少一个可以不同步的地方**，这正是 R-3 自己想要的结论。`CandleChart.tsx` 因此零改动 |
| `test_price_consistency.py`、`price-panel.test.tsx` | 新增 33 + 4 例（前驱 30 + 评审 3） | 见 §三；既有用例只动了 3 处，全部由 §R3-1 的语义变更引起 |

### 二、评审独立复测（`.agentmesh/repro/review_verify.py`）

```text
symbol         1m[-1]    3y[-1]     quote  prevClose   dayHigh  window==quote
600519.SH     1753.06   1753.06   1753.06    1711.62   1766.10  True
000858.SZ      186.29    186.29    186.29     183.60    188.18  True
300750.SZ      233.02    233.02    233.02     237.39    240.24  True
AAPL           330.46    330.46    330.46     326.24    333.23  True
0700.HK        467.83    467.83    467.83     461.43    468.81  True

mean_gap 0.24%  mean_body 1.53%  (old walk: gap 1.47% > body 0.35%, inverted)
high<prevClose 60/782  of which legit gap-downs 60
historical closes rewritten when the day advances: 0/783 (was 782/782)
last/anchor band 0.94x .. 1.45x (R-4(1) regression was 0.015x)
cold walk 7012 bars in 89ms
```

`prev_close == bars[-2].close`、`day_low/volume == bars[-1]`、`window==quote`、跨日稳定、价位带宽，逐条通过，
且**与前驱节点报告的数字一致**——不是"跑在某次特殊缓存状态上"的一次性结果。
`api/data/app.db` 里三条 `quotes` 的价（1753.06 / 233.02 / 330.46）与当前 provider 逐值相等，
说明屏幕上那块左栏确实是新 walk 的产物。

### 三、本轮改掉的四个缺陷（直接改源码，无 TODO 残留）

#### R3-1（P1）新鲜度判据只覆盖了"收盘后"，盘中把"昨天的 bar"判成新鲜 —— 屏幕又会分成两个价

`is_bars_cache_fresh` 被重写成"最后一根 bar 是否 ≥ 最近一个**已收盘**交易日"。周末/长假这一半是对的（I-2 的诉求），
但盘中那一半是错的：CN 盘中（UTC 02:00 = 上海 10:00）`latest_closed_session` 返回**昨天**，于是任何停在昨天的
缓存都算"新鲜"——`/bars` 一整个交易日不会再取今天那根 bar，而 `/quote` 的 TTL 只有 60 秒，它早就是今天的价了。
两者叠起来就是本次要消灭的那块屏：

```text
GET /instruments/CN:600519.SH/quote   -> 今日现价（TTL 60s，一直在刷新）
GET /instruments/CN:600519.SH/bars    -> cached=true, stale=false，最后一根仍是昨天
```

这不是"修完又回归"（HEAD 那版 `/bars` **根本不检查新鲜度**，同样停在昨天），而是**这次修复宣称解决、
实际没解决**的那一半：修复把新鲜度接上了线，却让它问了一个盘中永远回答"新鲜"的问题。
第二个症状更能说明问题：`bars_ttl_seconds()` / `BAR_CACHE_TTL_SEC` 因此失去全部调用者——
spec §9 的"盘中 15 分钟、收盘后 6 小时"被静默丢弃，而 `ruff` 不会对没人调用的模块级函数报错。

修法（`api/app/services/market_data/cache.py` + 两个调用点）：

1. 新增 `latest_session_due()`：收盘后/周末 = 最近一个已收盘交易日（原语义保留），**盘中 = 今天**。
   缺这一条就必须在"我还没拿到今天"与"这个标的已经退市"之间二选一，而粗粒度日历根本分不开两者。
2. 新增进程内 refill stamp（`record_bars_fetch`，在 `write_bars` 落戳）：**零 DDL** 拿到"上次取数时刻"，
   §9 的 TTL 因此重新有意义——它是"缺席窗口的复核节流"，不是"数据陈旧度"（I-2 的两层语义各归其位）：
   - 收盘后且窗口完整 → **不需要 stamp**，多旧都算新鲜（周末不再每次回源）；
   - 正在交易、今天那根 bar 还在形成 → 每 TTL（默认 900s）复核一次，K 线的"今日蜡烛"会更新；
   - 应该有会话却缺席（慢源 / 长假，本模块没有节假日表）→ 也按 TTL 复核，**不会变成"每看一次页面就一次网络往返"**。
3. 单 worker 是 README 的硬契约（启动时 `assert_single_worker()` 直接拒跑多 worker），所以进程内 stamp 安全；
   重启的代价只是多取一次。stamp 有上限（超过 1024 个 key 整体清空），不随运行时长膨胀。

判据已落成用例：周六无 stamp 仍判新鲜、盘中昨收无 stamp 立刻回源、有 stamp 时被 TTL 节流、收盘后完整窗口无需 stamp。
**被改的既有用例共 3 处**：`test_market_data.py` 里两条各补一个 `key=`（历史地板/空缓存，语义没变），
新文件里 1 条把"新鲜 = 最近已收盘会话"按新语义重写（`test_a_running_session_must_not_sit_on_yesterdays_bar`）。
这是本轮唯一触碰既有断言的地方，理由写在用例 docstring 里。

#### R3-2（P3）`is_bars_cache_fresh` 没有缓存身份，两个调用点各自猜

原签名只有 `(bars, market)`，无法区分"这个标的的窗口"和"别的标的的窗口"。R3-1 的修法把它变成
**必填的关键字参数** `key=(instrument_id, timeframe, adjust)`；`/bars` 与研报快照传同一个 key，
因此**共用一次 refill 与一次网络往返**，而不是各付一次。没有"忘了传就静默降级"的可选分支——
那种宽容正是这类 bug 最喜欢藏身的地方。

#### R3-3（P1）缓存里"两个生产者的接缝"被当成一次缓存命中：783 根里有一根 −15.33% 的假 K 线

评审在本机起了单 worker API 用真实 `api/data/app.db` 复现 A（`GET /bars?range=3y`），得到：

```text
n 783  source akshare  cached True
jumps>10% [(761, -15.33)]  count 1
```

第 761 根就是"22 行 akshare（我上一次 `range=1m` 探测写进去的）+ 761 行 synthetic-v2"的接缝：
`write_bars` 按 `ts` upsert，谁最后写进窗口谁就盖住那一天，于是**同一个序列里混了两个生产者**——
两个复权基准、两个价位体系，接缝处因此凭空出现一根 −15.33% 的日线，而 `meta` 还理直气壮地报
`cached=true, source=akshare`（R-2(c)/R-2(d) 的原样复现，只是这次是**修完之后**在真实库上看到的）。

修法（`cache.read_bars`）：一次读取里出现**多于一个 `source`** 即视为未命中并返回空，由调用方用单一生产者
重填整个窗口。不删行（akshare/yfinance 的数据是花钱换来的，只有 `synthetic` 是可弃的），也不拼接。
代价是"混合期"每次请求回源一次，直到窗口被同一个生产者写满；一次 3y 重填之后实测即收敛：

```text
seams in 3y series (|move| > 10%): []
3y cached False -> 3y' cached True, 783 bars, source synthetic
1m last 1753.06 == 3y last 1753.06 == quote last 1753.06
```

（证据脚本：`.agentmesh/repro/review_live_probe.py`。评审探测期间写进库的 akshare 行已被这次重填覆盖，
`api/data/app.db` 回到纯 `synthetic-v2`。）

#### R3-4（P1）真实源的报价 `updated_at` 写的是**bar 的收盘时刻**，于是这条报价永不失效

`akshare_provider.quote()` / `yfinance_provider.quote()` 都把 `last_row.ts`（日线的 07:00/收盘戳）写进
`updated_at`。盘中（UTC 03:00）这个时间戳在**未来**，`quote_is_fresh` 里 `now - stamped` 是负数，
恒小于 60 秒 TTL——**这条报价永远不会过期**。I-5 早就实测到这一行（`('CN:600519.SH', 1439.89, 'akshare',
'2026-11-17T07:00:00Z')`，实测时刻 03:10），本轮把它按"同一类缺陷"处理掉了，因为它和 BUG-1 是同一个形状：
**一个永远不会失效的缓存**，只不过这次失效的不是 bars 而是 quote，症状仍然是"屏幕上两个价"。

修法两处、各自独立可证：

1. 两个真实源的 `quote()` 把 `updated_at` 写成**取数时刻**（与 `SyntheticProvider`、与
   `snapshot.py:138` 的 `_iso()` 同一约定）——字段语义从此只有一个："什么时候读到的"；
2. `cache.quote_is_fresh()` 增加"**未来时间戳不可作为新鲜度证据**"分支（容忍 5 分钟时钟漂移），
   这样即便将来第三个 provider 又把 bar 时刻写进来，最坏结果是"多取一次"，而不是"永远不取"。

`QuoteOut.updatedAt` 不在快照契约里（快照的 `quote` 块只有 last/changePct/prevClose/volume/dayHigh/dayLow），
前端也没有任何地方渲染它，所以这次语义修正没有下游连带。

### 四、明确不改的四项（附理由，防止下游"顺手"改回去）

| 项 | 判定 | 理由 |
| --- | --- | --- |
| `quotes` 表不做代次退休 | **不改** | `quotes` 主键是 `instrument_id`，`write_quote` 原地覆盖，不存在"旧行永久残留"；`quote_is_fresh` 默认 60 秒，旧 walk 的报价最多活 60 秒就被覆盖。实测库里三条报价已全部等于新 walk。加代次只会多一处可以不同步的地方 |
| `lru_cache(maxsize=32)` 裁尾到 5y | **不改** | 32 × 0.67MB ≈ 21MB 上界，冷建 89–135ms。裁尾会把"任意区间可查"变成隐式约束（下一个人加 10y 区间时会静默拿到空历史），省 15MB 不值这个风险 |
| `bars_cover` 对次新股恒不满足 | **记录为残留风险** | 只有 2 年历史的新股请求 3y 时覆盖率永远不满足 → 每次请求回源一次。比"用 22 根回答 3y"好得多，且合成源不受影响；要收口需要 provider 报告"我最早能给到哪天"，那是另一个契约 |
| BUG-8（provider 链在不同时间降级到不同源） | **仍然 open（时间戳那一半已修，见 §R3-4）** | 真正的跨源一致性（同一标的的 `/bars` 与 `/quote` 必须来自同一个生产者）需要一次 provider 选择的对账，属于新抽象，分析已明确否决网关层方案。本轮把它的两个可修半边修掉了：报价不再永不失效（§R3-4）、混合序列不再被当成缓存命中（§R3-3）；剩下的“同一时刻链上给出不同源”仍由 `meta.source` 如实暴露给用户 |

### 五、门槛与提交

```text
后端 pytest        657 passed, 2 skipped in 307.19s（基线 626 collected；本轮 659 collected）
后端 ruff          All checks passed（app + tests）
前端 tsc           clean（npx tsc --noEmit）
前端 eslint        clean（npx eslint . --max-warnings=0）
前端 vitest        195 passed, 1 skipped（17/17 文件）
```

两点如实记录：

1. `vitest run` 用默认 5 秒超时会红 1 例：`tests/a11y.test.tsx > AskPanel > 渲染后的 DOM 满足结构契约`
   （本机单独跑 3.9 秒通过，全量并行时跑到 10.7 秒超时）。该文件与本 bug 无关、本轮零改动，
   属于既有的负载敏感用例；`--testTimeout=20000` 下全量 195 passed。**不作为本 bug 的修复内容**，
   也不塞进这次提交。
2. 同样地，`pytest` 在与本地 uvicorn 实例并发跑时，`test_generation_e2e.py` 的取消/超时用例会
   被 CPU 抢占推到 9 分钟并超时；停掉服务器单跑 42 秒全绿。

暂存区只含本 bug 的文件：6 个后端改动（`synthetic_provider.py`、`cache.py`、`instruments.py`、`snapshot.py`、
`akshare_provider.py`、`yfinance_provider.py`）、1 个前端改动（`PricePanel.tsx`）、
2 个新测试（`test_price_consistency.py`、`price-panel.test.tsx`）、1 个测试签名跟进（`test_market_data.py`）、
本文档与 README 的一行说明。`dist/`、`.next/`、`desktop/build-staging/`、`.agentmesh/`、`.task-prompt-*`
一律不入库。
