# 数字可信度（grounding）审计的信任完整性缺陷分析

- **诊断对象**：报告头「数字可信度」徽标、`quality.grounding` 判词、报告「降级」判定
- **报告样本**：`ed01d950ed994cb2ac13c632b362fc5f`（CN:600519.SH，deepseek-flash，`generated_at=2026-11-16T18:04:47Z`，`status=degraded`，`groundedRatio=0.5635`，`ungroundedCount=141`）
- **判定实现**：`api/app/services/numeric_claims.py` → `api/app/services/report_validator.py:348-364` → `web/lib/grounding.ts:100-114` → `web/components/report/TrustBadge.tsx`
- **缺陷编号**：D1 – D5（1 个 P1 根因 + 4 个 P1/P2 症状），另记 2 条"被自己推翻的假设"（§7）
- **结论**：这不是 5 个独立 bug。**五个症状全部来自同一个架构选择**——"接地"被实现为**字符串形态的集合成员判断**，而不是**值的来源判断**。§8 给出唯一推荐修复（A：判词三改一补），并列出可独立上线的四个阶段。

---

## 0. 一句话结论

> 徽标上那句「已核验」/「141 个数字未找到依据」，**既数错了分子也数错了分母**：它把 15/15 个手算可复现的推导值判成"编造"（D1），把 `0.78 取整`、`600519 ÷ 1e4`、`-8.528 取 0 位`这类巧合判成"接地"（D2），把同一处数字的 8 次引用计成 8 个问题（D3），而且这个判词**今天无法复算**、也没有口径版本（D4）——而读者最先读的摘要与风险清单根本不在审计范围内（D5）。

---

## 1. 问题描述

产品在报告头承诺三件事（README「本期包含（v6）」D1/D2 一节）：

1. 「已核验 48/51」——给读者一个**可信度结论**；
2. 未接地的数字**定位到块 id**、可点可跳；
3. 未接地的数字**只标不改**（改数比标错更危险）。

在真实报告（不是夹具）上，这三件事同时不成立：

| 观察 | 实测 | 证据 |
| --- | --- | --- |
| 徽标给读者的结论 | 「141 个数字未找到依据」（琥珀态） | `ego-browser` round-01 读到的 `aria-label`（`.agentmesh/ego/g3-01/round-01.mjs` → `trustBadge.label`），截图 `.agentmesh/ego/g3-01/header.png` |
| 同一份报告在"降级"徽标悬浮里 | 「接地率 56% · 141 处数字未能在快照中找到来源」 | `ReportPanel.tsx:362-366`，`ego-browser` round-03 `tooltips[0].title` |
| 展开面板列出的第一条样本 | `raw=-11`，指向 `thesis#0` 的一句时间戳文本 | `ego-browser` round-03 `panel.items[0]`；`.agentmesh/repro/out/version_drift.txt` |
| 读者最先看的摘要（`doc.summary`）里 6 个未接地数字 | 无法点击、无 `data-block-id`、不在徽标计数里 | `.agentmesh/repro/out/d3_coverage.txt` |
| 「141 处」 | 实际是 103（今天口径）/141（落库口径）条**引用**，对应 **43 个不同数值** | `.agentmesh/repro/out/repetition.txt` |

一句话：**读者被告知"这篇报告有 56% 的数字能核对"，而这个 56% 由一种既会误判"编造"、也会误判"接地"的字符串比对算出，且无法复算。**

---

## 2. 复现步骤

全部脚本在仓库根目录执行（Windows cmd.exe；脚本内部用绝对路径，cwd 无关）。前置：`api/data/app.db` 里有至少一份 `provider='openai'` 的报告（本仓库现有 2 份：v11 / v10）。

### 2.1 一条命令看完五条缺陷的证据（推荐入口）

```bat
python .agentmesh/repro/grounding_evidence_all.py
```

它会顺序执行下面 8 个探针并把输出写到 `.agentmesh/repro/out/*.txt`（UTF-8），同时在 stdout 打印一张结论表。

### 2.2 逐条复现

**D1 —— 推导值被判成"编造"**

```bat
python .agentmesh/repro/grounding_hand_verified.py
type .agentmesh\repro\out\hand_verified_derived.txt
```

期望（实测）：15 行里 `formula reproduces the printed number: 15/15`，而 `checker says` 列 **15 个全是 `FABRICATED`**。
其中 12 行的算式是投研报告里每天都在出现的：同比 `(本期-上期)/上期×100`、单季轧差 `9M−H1`、隐含空间 `(目标价-现价)/现价×100`、距均线 `(MA5-收盘)/收盘×100`。

**D2 —— 取整/缩放造成的假接地**

```bat
python .agentmesh/repro/grounding_token_provenance.py
type .agentmesh\repro\out\token_provenance.txt
```

实测（原文见文件）：

| 报告里的 token | 语料里唯一"依据" | 字段真实值 | 这是"这个数"吗 |
| --- | --- | --- | --- |
| `1` | `peers[1].changePct` 取 0 位小数 | `0.78` | 否 |
| `5`（"信心度 3/5"） | `performance.ret3m` 取 0 位小数 | `4.8086` | 否 |
| `9`（"9M"） | `technicals.macd` 取 0 位、取绝对值 | `-8.528` | 否 |
| `60`（"60 日低点"） | `instrument.symbol` = `600519` ÷ 1e4 | `60.0519` | 否（是股票代码的位数） |

**D3 —— 同一处数字重复计分**

```bat
python .agentmesh/repro/grounding_repetition.py
type .agentmesh\repro\out\repetition.txt
```

实测：`103 claims over 43 distinct values`；`-0.25` 记 8 次（thesis 三处 + fundamentals 三处 + risks + keyMetrics）、`-1.2` 记 7 次、`1.82` 记 6 次…… 前 8 个不同数值合计 45 条，占 103 的 44%。

**D4 —— 落库判词今天算不出来**

```bat
python .agentmesh/repro/grounding_doc_identity.py
python .agentmesh/repro/grounding_version_drift.py
```

实测：

```
stored payload : factClaims=323 factMissing=141 identifierClaims=280
recomputed     : fact=270 miss=103 ident=273     (同一份 doc_json + 同一份 snapshot.json)
```

且落库样本里两条在**今天的规则下不可能产生**：

```
raw=-11  文本 "as-of 2026-11-16T18:02:30Z..."   -> 今天 strip 后 numbers_in_text == []
raw=26   文本 "26Q1 营收 547.03 亿元"           -> 今天 numbers_in_text == ['547.03','6.34']
```

**D5 —— 摘要/风险清单是审计盲区**

```bat
python .agentmesh/repro/grounding_d3_precision.py
type .agentmesh\repro\out\d3_coverage.txt
```

实测：`doc.summary` / `doc.headline` / `doc.risks[].{title,trigger,mitigant}` 里共 27 个数字，`extract_claims()` 产出的 549 条 claim 里**没有任何一条的 path 以 `summary` / `headline` / `risks[` 开头**（`iter` 对比见脚本输出行 `claim paths produced for this doc`）。风险清单的 `<li>` 在真实 DOM 里 `data-block-id` 为 `null`（`ego-browser` round-01 `riskItems[]`）。

### 2.3 浏览器侧复现（ego-browser，4 轮）

```bat
ego-browser nodejs < M:/takoAI/AragonTradingKanban/.agentmesh/ego/g3-01/round-01.mjs
ego-browser nodejs < M:/takoAI/AragonTradingKanban/.agentmesh/ego/g3-01/round-03.mjs
ego-browser nodejs < M:/takoAI/AragonTradingKanban/.agentmesh/ego/g3-01/round-04.mjs
```

产物：`.agentmesh/ego/g3-01/header.png`（报告头徽标）、`panel.png`（展开面板）、`versions.png`（版本列表只给 v11 显示「接地率 56%」，v10/v8/v7 也是 aragon 真实报告却没有百分比）。

> ⚠️ 复现前提：`web` 的 dev server 必须是**新启动**的。本轮开始时 3000 端口上是一个陈旧进程，`/CN:600519.SH` 直接 500（`Internal Server Error`，`curl -o` 200 字节），杀掉重启后 200。这一步与缺陷本身无关，但会吞掉所有浏览器证据。

### 2.4 API 侧（可选）

```bat
curl -s -H "Accept: application/json" "http://127.0.0.1:8000/api/v1/reports/ed01d950ed994cb2ac13c632b362fc5f" 
```

读 `quality.grounding`（323/141）与 `quality.ungroundedCount`（141），与 §2.2 D4 的重算值（270/103）对照。

---

## 3. 根因分析

### 3.1 一处架构决定，五个症状

核心函数只有 44 行：

```python
# api/app/services/numeric_claims.py:539
def grounding_report(doc, snapshot) -> GroundingReport:
    corpus = snapshot_number_corpus(snapshot)          # ← 语料：一堆"形态字符串"
    ...
    for claim in extract_claims(doc):
        ...
        if claim.canonical and claim.canonical in corpus:   # ← 判定：字符串集合成员
            continue
        if claim.cls in ("fact", "derived"):
            report.fact_missing += 1                        # :567
```

`corpus` 由 `snapshot_number_corpus()`（`:240`）生成，它对快照里每个数值调用 `_renderings()`（`:230`）：

```python
# api/app/services/numeric_claims.py:230-237
for scaled in (number, number * 100.0, number / 1e4, number / 1e8, number / 1e12):
    for digits in (0, 1, 2, 3, 4):
        out.add(f"{scaled:.{digits}f}")
        out.add(f"{abs(scaled):.{digits}f}")     # ← 符号被抹掉
```

也就是**每个快照数值膨胀成 50 个字符串**（5 缩放 × 5 位小数 × 正负），拼成一个 `set[str]`。判定退化为"这个数字的字符串形态是否出现在那个集合里"。

#### 症状如何由此产生

| 该决定缺少的东西 | 直接后果 | 缺陷 |
| --- | --- | --- |
| **身份的来源**（哪个字段、什么单位） | 任何整数都能被某个"别的量取整"接住；代码 `600519÷1e4=60` 能把"60 日"判成接地 | D2 |
| **推演关系**（`(a−b)/b`、`a−b`、`a/b`） | 报告里最常见的同比、单季轧差、隐含空间全被当成"编造" | D1 |
| **值的身份**（同一数值在不同块重复） | 一条错误被计 N 次，比例变成"引用次数比" | D3 |
| **口径版本**（判定器自身的版本与输入哈希） | 判词永久落库，但读它的人不知道是哪一代规则 | D4 |
| **遍历范围与 UI 承诺一致** | `extract_claims()` 只走 `sections[].blocks[]` + `keyMetrics[]`，摘要/风险清单不在其中 | D5 |

下面逐条给证据。

### 3.2 D1（P1）推导值一律判为"编造"

**机制**：`snapshot_number_corpus()` 只有**字面值**（及其缩放/取整形态），没有**派生值**；所以 `factClaims` 里凡是"模型自己算的"数字都必然 `not in corpus` → `fact_missing += 1`（`:567`）。
`derived` 通道本来是为这件事准备的，但它是**路径判据**（`block.get("derived") is True`，`numeric_claims.py:413`），而报告 schema 没有 `derived` 字段——代码注释自己承认"这条分支在**今天的合法文档上恒为 false**，`derivedClaims` 会是 0"（`:409-412`）。实测 `derived=0`，与注释一致。

**证据**（`.agentmesh/repro/out/hand_verified_derived.txt`）：

```
 printed    computed  in snapshot?  checker says  meaning
    1.82      1.8179  no            FABRICATED    9M26 营收同比
   -0.25     -0.2465  no            FABRICATED    9M26 归母净利同比
   -1.20     -1.2001  no            FABRICATED    FY25 营收同比
   -4.53     -4.5323  no            FABRICATED    FY25 归母净利同比
  410.06    410.0558  no            FABRICATED    26Q3 单季营收（9M − H1，亿元）
  375.75    375.7516  no            FABRICATED    26Q2 单季营收（H1 − Q1，亿元）
  199.51    199.5055  no            FABRICATED    26Q3 单季归母净利
  172.74    172.7437  no            FABRICATED    26Q2 单季归母净利
    48.7     48.6532  no            FABRICATED    26Q3 单季净利率
    46.0     45.9728  no            FABRICATED    26Q2 单季净利率
    -2.4     -2.4000  no            FABRICATED    26Q1 净利率同比变化（pp）
    1.06      1.0616  no            FABRICATED    目标价隐含空间（%）
    0.83      0.8334  no            FABRICATED    现价距 MA5（%）
     3.8      3.7750  no            FABRICATED    净利降幅是营收降幅的倍数
    5.47      5.4720  no            FABRICATED    现价与 11-13 收盘相差（%）
formula reproduces the printed number: 15/15
```

这 15 个算式全部由 `snapshot.json` 里模型**实际拿到**的字段算出（`financials[0..7].{revenue,netProfit}`、`quote.*`、`technicals.ma5`、`priceSeries.close[-1]`、`doc.rating.targetPrice`），没有一个是编造。

**放大**：全量 103 条 `factMissing` 中，用"两操作数 + 单位缩放"穷举可复现 **98 条（95.1%）**（`.agentmesh/repro/out/derived_gap.txt`）。但穷举本身带来 **15.74% 的随机假命中率**（12000 次随机取值的渲染形态里有 1889 次落进同一个索引），所以 §8 **不推荐**用穷举做修复——它证明了"这些数字来自快照"，却不是可以上线的判据。

> ★评审补证（R-E1）：**95.1% 只能读作上限，不能读作 D1 的规模估计**。同一次穷举自己打印的"救回"样本就是反证——
> `5.47 ← priceSeries.volume[18] / performance.volAnnualized / 1e4`、`1.06 ← priceSeries.close[27] / quote.last`
> （`.agentmesh/repro/out/derived_gap.txt` 的 `material rescued claims` 段）。也就是说这份展品里的"复现"
> 多数是量级巧合而不是推导关系，与 15.74% 的随机基线一致。可站得住的定量结论只有两条：
> ①**15/15 条手算推导被判 FABRICATED**（`out/hand_verified_derived.txt`，逐条带算式，这是 D1 的承重证据）；
> ②其余 88 条 `factMissing` 的成因**未知**，穷举给不出可辨识的证据。据此，D1 的规模应表述为
> "至少 15/103 确证（14.6%），上限 95.1%（不可信）"，而不是"95% 都是推导"。

**影响**：`ratio` 0.6185（今天口径）或 0.5635（落库口径），远低于 `GROUNDED_THRESHOLD = 0.9`（`report_validator.py:38`）→ `status = "degraded"`（`:361`）→ 报告头永久挂「降级」+ 版本列表挂「降级」。**越诚实地做投研分析（写同比、写单季、写隐含空间），越会被判降级**——这是对模型行为的反向激励，和 README 里"同业 PE 不再拉低接地率"修的是同一类问题的新实例。

### 3.3 D2（P1）取整与缩放造成的假接地

**机制**：`_renderings()` 的 5 缩放 × 5 位小数 × ±符号 让**小整数几乎必然命中**。任何 |v|≤100 的整数，只要快照里有一个 `ab(某值)` 在 0 位小数下等于它，就算"已核验"。

**量化**（`.agentmesh/repro/grounding_corpus_audit.py`）：

```
snapshot numeric leaves: 189
corpus size (reproduced): 1424
corpus tokens with NO value-form rendering (pure rounding/scale artefacts): 1199
```

快照里一共 **189 个数值叶**，经 `_renderings()` 膨胀成 **1424 个语料 token**，其中 **1199 个（84%）只由取整/缩放/取绝对值产生**，不代表任何字段的真实值。

**逐 token 溯源**（`.agentmesh/repro/out/token_provenance.txt`）：

```
token '1':  peers[1].changePct   x1  0dp  field=0.78     is-the-number-itself=False
token '5':  performance.ret3m    x1  0dp  field=4.8086   is-the-number-itself=False
           performance.ret1m    x1  0dp  field=-5.4194  is-the-number-itself=False
token '9':  technicals.macd      x1  0dp  field=-8.528   is-the-number-itself=False   (abs 抹掉符号)
token '60': instrument.symbol#600519 /1e4 0dp field=600519.0 is-the-number-itself=False
```

第三行是最刺眼的：**"600519" 这个股票代码除以 1e4 等于 60**，于是报告里"60 日收盘低点"的 `60` 被判为"能在快照里找到依据"。同理 `48.4`（净利率）会被 `financials[0].netMargin` 与 `valuation.netMargin` 双向满足——这一条是**正确的接地**，但它和上面四条用的是同一把尺子，判据无法区分。

> ★评审更正（R-E2）：上表"语料里唯一'依据'"这一列，**`60` 那一行不成立**。用"逐个剔除顶层键再重建语料"的只读探针实测：
> `'1'` 只在 `peers` 存在时可得、`'5'` 只在 `performance`、`'9'` 只在 `technicals`（前三行成立），
> 而 `'60'` 在分别剔除 `instrument` **或** `peers` 之后**仍然在语料里**——即 `600519 ÷ 1e4` 不是唯一来源，
> `peers` 里另有字段也能渲染出 `60`。结论不变（`60` 不是任何字段的值；`instrument.symbol` 确实能造出它），
> 但**不要**把这一行当作"股票代码冒充了 60 日低点"的独证；A2 的验收（AC3）本来就只要求
> "`1/5/9/60` 四个 token 的 `grounded?` 为否"，与此更正一致。

**在真实报告上的占比**（`.agentmesh/repro/out/repetition.txt`）：

```
grounded: 167 claims over 52 distinct values
             1 x16  in 13 path(s)
             3 x13  in 11 path(s)
```

`1` 出现 16 次、`3` 出现 13 次 = **29/167（17.4%）**。而 `1` 的唯一语料来源是 `0.78` 取整、`3` 的语料来源之一是**来源标题里的期间长度**——`sources[1].title = "贵州茅台 财务数据（近 3 年）"`（`.agentmesh/repro/out/string_leaves.txt`）。这些数字在报告里的语义分别是"信心度 3/5"、"1 个数字未接地"、"3 类"——**它们不是测量值**。

**影响**：`groundedRatio` 被系统性抬高。"已核验 48/51"里的 `48` 可能是"48.4 取整"以外的巧合；读者无法知道其中有多少是巧合。阈值 0.9 因此既可能被 D1 拉低，也可能被 D2 抬高——**同一份报告的两个方向误差相互掩盖**。

### 3.4 D3（P1）重复计分：「处」不是「处」

**机制**：`grounding_report()` 的循环单位是 `Claim`（= 一次路径出现），不是"一个数值"（`:549-568`）。同一数值出现在 thesis.callout、thesis.paragraph、fundamentals.table、timeline、risks、keyMetrics 六处，就计 6 次。

**证据**（`.agentmesh/repro/out/repetition.txt`）：

```
missing: 103 claims over 43 distinct values
        -0.25 x8   in 8 path(s)
        -1.2  x7   in 7 path(s)
        1.82  x6   in 6 path(s)
       -5.23  x6   in 5 path(s)
        3.78  x5   in 5 path(s)
        48.7  x5   in 5 path(s)
       -4.53  x4   in 4 path(s)
        6.34  x4   in 3 path(s)
```

`-0.25`（9M 归母净利同比）在 8 个位置被同一份报告复述——它其实是 D1 的受害者（算式值），却被计了 8 次"未接地"。

**UI 的语义错误**：`ReportPanel.tsx:363-365` 把 `quality.ungroundedCount` 渲染成 `${n} 处数字未能在快照中找到来源`；`api/tests/test_report_validator.py:140` 的注释同样写着 `N 处`。**实测是 43 个不同数值、103 次引用**（落库口径 141 次）。"处"暗示独立问题数，实际是复述次数，把 `-0.25` 一处错误放大成 8 处。

**影响**：读者对"问题规模"的直觉被放大 2.4 倍（103/43）；更糟的是方向可逆——报告写得越详细（同一数字在多块复述），被判降级的概率越高。

### 3.5 D4（P1）判词不可复算、无口径版本

**机制**：`report_validator.validate()`（`report_validator.py:348-352`）把 `grounding_report()` 的结果直接写进 `quality`，`report_service.py:71` 原样透出；落库结构里**没有任何字段记录"这是哪一版判定器算的"**，也没有输入哈希。

**证据 1 —— 同一输入，两套结果**：

```
stored payload : factClaims=323 factMissing=141 identifierClaims=280 projectionClaims=6
recomputed     : fact=270        miss=103        ident=273           proj=6
```

（`.agentmesh/repro/grounding_doc_identity.py`；输入是**同一份** `doc_json` 与**同一份** `snapshot.json`，`report.json` 与 `doc_json` 的 claim 数完全一致，排除了"审计的是另一份文档"这个解释。）

> ★评审补证（R-E3）：原论证只排除了"文档不同"，**没有排除"快照被重写"**——这才是这条证据链唯一的漏洞（旧版报告无输入哈希，见 A6）。
> 用运行目录的写盘时序可以显式补上：
>
> ```
> api/workspace/runs/35bf7e07…/snapshot.json   2026-11-16 13:02:30（= 18:02:30Z，正是 doc 里写的 as-of）
> api/workspace/runs/35bf7e07…/result.json     13:04:44
> api/workspace/runs/35bf7e07…/report.json     13:04:47（= 18:04:47Z，正是 reports.generated_at）
> ```
>
> 快照的落盘时间早于报告 2 分 17 秒且与文档自述的 `as-of` 秒级一致，之后没有任何写盘 → 校验时读到的
> `snapshot.json` 与今天读到的是同一份字节。**因此"同一输入两种结果"成立，只能归因于判定器规则变了**，
> 而不是输入漂移。这一条比原来的 claim 数比对更强，也是 A6 之后要变成机械可查（`snapshotHash`）的那一条。

**证据 2 —— 落库样本在今天不可能生成**：

```
raw=-11  来自 "as-of 2026-11-16T18:02:30Z，但行情源标记 stale=true"
         今天：strip_identifiers() 后 numbers_in_text() == []      （DATE_LIKE_RE 已整段剥离）
raw=26   来自 "26Q1 营收 547.03 亿元、同比 +6.34%"
         今天：numbers_in_text() == ['547.03', '6.34']              （26Q1 已被 DATE_LIKE_RE 吃掉）
```

（`.agentmesh/repro/out/version_drift.txt`，`:70-92` 的 `DATE_LIKE_RE`。）

**证据 3 —— 判定器在报告生成后才落盘**：

```
api/app/services/numeric_claims.py:
  4b434b8 2026-11-16 13:43:57 -0500  =  2026-11-16T18:43:57Z
报告 generated_at                       =  2026-11-16T18:04:47Z
```

`numeric_claims.py` 在整个历史里只有这一次提交（`git log --format=%h %ad %s -- api/app/services/numeric_claims.py`），时间戳**晚于**带 323/141 判词的那份报告 39 分钟。也就是说：**判词落库时判定器还在改，而落库结构对此毫无记录**。

> ★评审更正（R-E4）：这句话的**事实**成立，**因果**要收窄。`4b434b8` 的 author date 与 committer date 相同，
> 提交信息是"数字溯源、区间反查、全文视图、成本治理与引擎指纹（M13-M18 + 评审修订）"——一个批量提交，
> 里面同时有 M13–M18 的大量改动。所以这 39 分钟能证明的只有一件更朴素的事：
> **那份 323/141 的判词来自一个从未进入版本库的工作树状态，仓库里不存在任何一版能重现它的代码。**
> 它**不能**证明"运行期间有人正在编辑这个文件"（提交时间不等于改动时间）。
> 这对修复的含义不同：需要的不是流程禁令（"别边跑边改"），而是**判词自带口径版本 + 输入哈希**（A6）——
> 这条证据恰好说明"没有版本标记时，事后连'哪一版规则'都无从查起"，而不是"当时有人在乱改"。

**影响**：
1. 审计（"这个判词凭什么"）无法进行——客服/评审拿到一条"56%"，唯一能做的重算是 61.85%；
2. 版本列表把不同口径的百分比并排展示：`ego-browser` round-04 实测 v11 的 title 是「接地率 56%」，v10/v8/v7 同为 aragon 真实报告（库内 ratio 0.7451 / 0.7036 / 0.8803）却因为 `status != degraded` 完全没有百分比（`VersionSwitcher.tsx:114-118`），而它们的 `quality.grounding` 在库里**根本不存在**（v10 的 `quality_json` 只有 111 字节）→ 读者无法比较、也无法知道不可比；
   > ★评审更正（R-E5）：这里的**机制**被写反了一半，实测 `reports.status` 的值是 `superseded` 而不是 `ready`
   > （`select version,status from reports where instrument_id like '%600519%'` → `11 degraded`, `10 superseded`,
   > `9 superseded`, …，10/9/8/7/6 全部 `superseded`）。成因在 `report_service.py:129`：
   > 新版本一落库就把旧行 `is_latest=0, status="superseded"` —— **质量判词被生命周期状态覆盖掉了**；
   > 而 `VersionSwitcher.tsx:114` 只认 `status === 'degraded'`，于是旧版连"它当时也被判过降级"这件事都消失了。
   > 同一个语义混用在 `/diff` 里还翻了个面：`schemas/report.py:431 QUALITY_STATUS_EQUIVALENT` 把 `superseded`
   > 读成 **`ready`**，所以 v10（ratio 0.7451，按今天规则必降级）在两版对比里被呈现为"正常报告"。
   > 结论方向不变（版本之间不可比），但 A6 必须同时处理**状态列的双语义**，否则 `rules` 加上了、徽标依然看不到。
3. `web/lib/grounding.ts:40` 的 `GROUNDED_THRESHOLD` 与后端 `report_validator.py:38` 靠注释维持一致；口径一变，前端三态的分支（`grounding.ts:100-114`）立刻和后端判词脱钩。

### 3.6 D5（P2）摘要与风险清单是审计盲区，且不可定位

**机制**：`extract_claims()` 只遍历两处（`numeric_claims.py:501-536`）：

```python
for section in (doc or {}).get("sections") or []:      # :504
    ...
for index, metric in enumerate((doc or {}).get("keyMetrics") or []):   # :517
```

`doc.summary` / `doc.headline` / `doc.risks[]` 三项**不在遍历内**，而它们在 UI 上是：报告标题（`ReportPanel.tsx:528`）、报告第一段（`:535`）、以及"风险清单"整节（`:585-603`），并且被导出到 HTML/Markdown（`exporter.py:365-374`、`:608-614`）。

**证据**：

- `.agentmesh/repro/out/d3_coverage.txt`：这 27 个数字里 `NOT AUDITED ANYWHERE = 0`——**本份报告没有"只出现在摘要里"的编造数字**（见 §7 被推翻的假设 1），但通道缺失是结构性的：一旦模型把编造数字只写在摘要或风险清单里，审计**一次也不会看到**，徽标仍会说"共 141 处"。
- `ego-browser` round-01：`riskItems[].blockId` 全为 `null`，`summaryHasBlockId=false`，而正文块共 26 个 `data-block-id`。读者在风险清单里看到一个可疑数字，**没有可点的定位**，徽标的样本面板（`TrustBadge.tsx:119-147`）也不会列出它。
- `unblocked` 的不对称：`keyMetrics` 有专门的固定 blockId（`web/lib/blocks.ts:50`、`ReportPanel.tsx:542`）以及 `jumpToBlockById` 的特例分支（`:239-248`），说明"顶层字段也要可定位"这件事已经被想到过——只是摘要与风险清单没做完。

**影响**：读者最先读的两块（标题+摘要、风险清单）与"可核验"承诺不匹配；且这是**未来**编造的藏身处。

---

## 4. 影响面

| 面 | 受影响对象 | 具体表现 |
| --- | --- | --- |
| 读者结论 | 报告头徽标 + 降级徽标 | 「已核验 X/Y」/「N 处未找到来源」在 D1/D2/D3 三个方向都不准；诚实报告被判降级（D1），编造报告可能被判 ready（D2 的高 ratio） |
| 定位能力 | 样本面板、块脚注、表格圆点 | 样本首条是 `-11`（解析残渣）；摘要/风险清单无定位（D5） |
| 版本比较 | `VersionSwitcher` | 同一份报告的 v10/v11 分别在两代规则下产生（一个没有 `grounding`，一个有），列表里只有一个显示百分比（D4） |
| 状态机 | `reports.status = degraded` | 由 `ratio < 0.9` 直接决定（`report_validator.py:361`），因此 D1 会让"分析写得越细"的模型越容易被标降级 |
| 导出 | `exporter.py` HTML/Markdown | 风险清单整表进导出件（`:365-374`、`:608-614`），但它的数字从未被审计 |
| **追问门禁（评审补充）** | `POST /reports/{id}/ask` 的 `deep` 档 | `asks.py:34 DEEP_BLOCKED_STATUSES = ("provisional","degraded")`，`:76` 直接 `raise ReportNotReady`。**一份因 D1 被误判降级的诚实报告，它的 deep 追问被硬性拒绝**——这不是徽标难看，是功能不可用 |
| **状态列双语义（评审补充）** | `reports.status` / `VersionSwitcher` / `/diff` | `report_service.py:129` 用 `superseded` 覆盖质量判词，`schemas/report.py:431` 又把 `superseded` 读回 `ready`。同一列同时表示"质量"和"是否最新"，D4 的版本不可比只是它的一个症状 |
| 能力开关 | `features.grounding`、`/capabilities` | `_grounding_available()` 用一份固定夹具跑 `grounding_report()`（`routers/capabilities.py:36-46`），所以"能力可用"与"判词可信"是两件事 |
| 测试 | `api/tests/test_numeric_claims.py`、`test_report_validator.py:140-159` | 现有用例把"`ungroundedCount == factMissing`"与 `distinct` 语义钉住；改判据必须同步改这条不变量（它本身是对的，但文案是错的） |

用户可感知的最坏情况：**一份数据源残缺、模型老老实实标注"估值倍数全缺、口径冲突"的报告，被挂上「降级」和「141 个数字未找到依据」；而读者点开第一条，看到的是一个时间戳里的 `-11`。** 这正是 `ed01d950…` 这一份报告。

> ★评审补证（R-E6）：同一族的最后一处计数单位错误在徽标展开面板里：`TrustBadge.tsx:66-68` 用
> `quality.totalMissing`（**条数**）与 `samples.length`（**块数**，上限 20）比较，文案是
> `copy.trust.more = '仅列前 {shown} 个块，共 {total} 处'`（`web/lib/copy.ts:342`）——分子是块、分母是条，
> "共 141 处"里的 `处` 与 `ReportPanel.tsx:364` 是同一个错误标签。A4 改文案时**两处都要改**，
> 否则面板会同时说"43 个不同数值"和"共 141 处"。

---

## 5. 候选修复（对比）

先明确边界：**本审计永远无法判断"数字是否真实"**，它只能判断"数字是否可追溯到快照（含可命名的推演）"。任何修复都不能越过这条线。

### 5.1 方案 A：判词三改一补（推荐）

| 子项 | 内容 | 直接解决 |
| --- | --- | --- |
| A1 **命名派生量目录** | 新增 `derived_values(snapshot) -> dict[str, DerivedValue]`：从快照结构生成**有限、带名、带单位**的派生量（YoY / QoQ / 单季轧差 / 距均线 / 隐含空间 / 波动-收益比 / 情景加权中枢），每个带稳定 id（`yoy:financials[0].revenue`）。命中时记 `cls="derived"` 并**记住是哪个 id**。 | D1 |
| A2 **严格值匹配（带来源）** | 语料从 `set[str]` 改为 `dict[str, Provenance]`（字段路径 + 单位 + 该字段自身的显示精度）。判据：值的**相对误差 ≤ 该字段显示精度的一个 ulp** 才接地；去掉 `abs()`（正负必须同号）；缩放只保留快照里真实存在的单位（`元→亿元`、`小数→%`），不再无差别 `/1e4 /1e8 /1e12`。 | D2 |
| A3 **非数据数字归 identifier** | `N/M`（信心度）、`N 个月`、`9M26`、`N 日`、`pp`、`[S1,S2]`、`≥`/`±` 后的数字、`第 N` 等一律 `identifier`，不进分子分母。 | D2 |
| A4 **按值去重计数** | `GroundingReport` 增 `distinct_missing` / `distinct_claims`；UI 文案改「43 个不同数值，出现 141 次」；`ungroundedCount` 保留（向后兼容）但不再被读作"处"。 | D3 |
| A5 **覆盖补齐** | `extract_claims()` 增加 `summary` / `headline` / `risksList[i]` 三条路径（**id 不得写成 `summary#0` / `risks#N`**，理由见下），徽标样本可跳转。 | D5 |
| A6 **口径版本 + 输入指纹** | `quality.grounding.rules = "claims-v3"`、`docHash`、`snapshotHash`（sha1 前 12 位）。跨口径（`rules` 不同或缺该键）时 UI 不给百分比，只显示「口径不同，不可比」。 | D4 |

> ★评审更正（R-E7）：A5 原文给的 id（`summary#0`、`risks#N`）**会撞坏跳转**，不能照抄。
> `web/lib/blocks.ts:85 parseBlockId()` 把任何 `x#N` 都解析成"章节 x 的第 N 块"，
> 而 `ReportPanel.tsx:239` 只把**解析不出**的 id（如 `keyMetrics`）走"高亮 + 滚动、不动 Tab"的安全分支；
> 一旦 `summary#0` 被解析成功，`jumpToBlockById` → `jumpToBlock(blockId,'summary')` → `setActiveSection('summary')`
> → `visibleSections` 过滤成**空数组**（`ReportPanel.tsx` 的 `sectionsWithBlocks.filter(s => s.id === activeSection)`），
> **正文整篇消失**——这正是该文件 `:234-238` 注释里为 `keyMetrics` 记录过的失败模式。
> `risks#N` 更糟：`risks` 是**真实章节 id**（`schemas/report.py:438 SECTION_IDS`），跳转不会报错，
> 而是安静地滚到同章第 N 块——错位比失效更难发现。
> 正确做法：与 `keyMetrics` 对齐，用**不含 `#` 的顶层容器 id**（如 `summary` / `riskList`，配合 `riskList:i` 这样的子键
> 或直接在 `<li>` 上加 `data-risk-index`），并让 `jumpToBlockById` 的安全分支覆盖它们；若要引入 `#` 命名空间，
> 必须同步扩 `parseBlockId` + golden 契约（`web/tests/blocks.test.ts` ↔ `api/tests/test_block_text.py`）。
>
> ★评审补证（R-E8）：A5 的**计数副作用已实测**。用今天未改动的规则把 `summary` + `headline` +
> `risks[].{title,trigger,mitigant,severity}` 并入 `extract_claims()`：**+27 条 fact claim、+10 条 missing**
> （其中 8 个不同数值），ratio 由 0.6185 → **0.6195**（184/297）。两条结论：
> ①AC4 的"43 个不同数值"会变成"51"，§9 的数字要改成相对量而不是写死；
> ②这 27 条里 **`3` 与 `5` 来自"信心度 3/5"，在今天的语料里是"已核验"**（`3`/`5` 都在 corpus 中）。
> 所以 **A5 不能先于 A3（非数据数字归 identifier）上线**，否则覆盖补齐会顺手把两个非测量值算成"可追溯"。

- **侵入性**：中高。`numeric_claims.py` 结构性改动（语料类型变化 + 新增派生目录 + 计数语义）；`report_validator` 一处状态判定；`ReportQuality` schema 加 3 个字段；前端 2 个组件改文案与跳转；`api/tests/test_numeric_claims.py`、`test_report_validator.py`、`web/tests/grounding.test.tsx` 需要同步。
- **风险**：中。①派生目录本身是新的"裁判"，必须**有限且可命名**（不搜索），否则会变成新的假接地来源；②A2 收紧后 ratio 会再变一次，历史报告 status 不回溯（符合既有"老报告不回填"纪律），但必须给出"本次口径变更"的前端提示，否则版本列表会更混乱；③阈值 0.9 需要按新分母重新校准（建议同时把 `MIN_FACT_CLAIMS` 与去重后的分母对齐）。
- **工作量**：约 550–750 行（含测试），2–3 个工作日。

### 5.2 方案 B：只改说辞（最小可行）

把「已核验」改成「可追溯」、把「N 处」改成「N 次引用 / M 个不同数值」、面板顶部加一行「共 43 个不同数值，出现 141 次」、`VersionSwitcher` 的百分比加 `rules` 前缀（旧报告不显示）。

- **侵入性**：低（前端 3 个文件 + 1 个派生字段）。
- **风险**：低。
- **工作量**：约 80 行，半天。
- **收益**：只挡住 D3 与 D4 的表层，D1/D2 的误判仍在——"可追溯"这个词反而更容易被读成"已核验"。

### 5.3 方案 C：让模型自报算式，后端只验算式

prompt 要求所有非快照数字写成 `数值 = 算式`（如 `1.82% = (1332.84-1405.7)/1405.7`），后端只解析并校验算式两端的字段。

- **侵入性**：中（生成侧 prompt + 解析器 + schema）。
- **风险**：高。①依赖模型服从性，`AGENT_MODE=mock` 与既有报告立刻不适用；②解析器是新输入面（模型可控字符串），要防注入；③算式的"自证"程度有限（模型可以自造一个能被快照满足的算式去包装编造值）。
- **工作量**：中（200–300 行 + prompt 回归）。
- **收益**：能把"不常见推导"变成显式可审计；但**不能替代 A1**（常见推导应自动识别，而不是指望模型每次写清）。

### 5.4 方案 D：把 ratio 从状态机里摘出去

`status` 不再由 `ratio < 0.9` 决定，ratio 只作为展示字段。

- **侵入性**：极低（`report_validator.py:361` 一行 + 3 个用例）。
- **风险**：低-中：降级徽标失去意义；且"编造数字"与"缺失章节"两类问题会被混为一谈（现在 `missing` 与 `ratio` 是并列条件）。
- **工作量**：约 40 行。
- **收益**：立刻止血（诚实报告不再被判降级），但**只是不再伤害用户**，判词本身依旧不可信。

### 5.5 对比表

| | 侵入性 | 风险 | 工作量 | 解决 D1 | D2 | D3 | D4 | D5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **A 判词三改一补** | 中高 | 中 | 2–3 天 | ✅ | ✅ | ✅ | ✅ | ✅ |
| B 只改说辞 | 低 | 低 | 0.5 天 | ❌ | ❌ | 部分 | 部分 | ❌ |
| C 模型自报算式 | 中 | 高 | 1–1.5 天 | 部分 | ❌ | ❌ | ❌ | ❌ |
| D 摘掉状态联动 | 极低 | 低-中 | 0.5 天 | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 6. 推荐修复

**采用 A（A1→A6），按"先止血、再换判据、后补口径"四阶段上线**；不采纳"提高/降低阈值"作为修复手段（那只会把误差藏起来）；C 留作 A 之后的增量（A1 覆盖常见推导，C 处理非常规推导）。

### 6.1 阶段划分（每阶段可独立上线、独立验证）

| 阶段 | 内容 | 上线后即时收益 | 验收判据 |
| --- | --- | --- | --- |
| **A0（止血，半天）** | 文案与计数：`ReportPanel.tsx:363-365` 的「N 处」改为「N 次引用 / M 个不同数值」；`TrustBadge` 面板顶部加一行汇总；`VersionSwitcher.tsx:115` 的 title 附 `rules` 版本；同时按方案 D **把 ratio 从 status 判定里摘出**（`report_validator.py:361` 只保留 `missing` 条件），给新旧口径一个缓冲期 | 诚实报告不再被判降级；「141 处」不再被读成 141 个问题 | 同一份 `ed01d950…` 重放：`status` 不再因 ratio 变化；徽标文案出现「个不同数值」；`web/tests/grounding.test.tsx` 新增 2 例 |
| **A1（派生量，1 天）** | 新增 `derived_values(snapshot)`（有限目录 + 稳定 id）；`grounding_report()` 在字面匹配失败后查派生目录，命中记 `cls="derived"` + `derivedId` | §2.2 D1 的 15 个手算值从 `factMissing` 移到 `derived`，且**能说出是哪条公式** | `hand_verified_derived.py` 的 15 行全部 `checker says in snapshot? → derived:<id>`；`api/tests/test_numeric_claims.py` 新增 15 例（每条一个算式）+ 1 例"目录外的推导仍算 missing" |
| **A2（严格值匹配，1 天）** | 语料带 provenance（字段+单位+显示精度）；去 `abs()`；缩放白名单；A3 的 identifier 归并 | `1←0.78`、`5←4.8086`、`9←-8.528`、`60←600519/1e4` 四种假接地消失 | `token_provenance.py` 对这四个 token 输出 `any field that IS this number? False` **且** `grounded? 否`；vitest/pytest 各 4 例反证（把 `abs()` 加回去 → 红） |
| **A3（覆盖与口径，1 天）** | `summary#0` / `headline#0` / `risks#N` 定位 + `<li data-block-id>`；`rules/docHash/snapshotHash` 落库；跨口径不给百分比 | 读者第一屏与风险清单可点；判词可复算、可比 | ①同一 doc+snapshot 重算 == 落库 payload（新增 `grounding_recompute` 测试）；②`ego-browser` 点摘要里的数字能滚动高亮；③v10（无 `grounding`）与 v11（`claims-v3`）在版本列表里显示「口径不同」而不是两个百分比 |

> ★评审更正（R-E9）：A0 里的"按方案 D 把 ratio 从 status 摘出"**不能当止血**，它有三个后果本轮没计价：
> ①**撤掉了唯一的自动编造告警**。`report_validator.py:361` 是 `ratio < 0.9 or missing`，一旦只留 `missing`，
> 真正的编造报告也会是 `ready`；而 `asks.py:34/76` 正是按 `status` 拦 `deep` 的——门禁会一起放开。
> ②**打破一条编码了产品语义的既有用例**：`api/tests/test_report_validator.py:127-134`（`test_mock_documents_are_not_graded_on_grounding`）
> 断言"页内塞一个编造数字 123456.7% 的文档 strict 必须 `degraded`、mock 才 `ready`"，它的 `strict` 分支**只能**由 ratio 判定触发 → A0 直接把它变成失败。
> ③**AC7 的"重放为证"会变成假证**：我实测重放 `validate_doc(ed01d950 的 doc + 同一份 snapshot)` 得到
> `status=degraded, ratio=0.6185, errors=0, missing=[]`，即这份报告**100% 是被 ratio 分支判降级的**
> （`missingSections` 在库里也是 `[]`）。"摘掉 ratio 后它变 ready"因此成立，但它证明的是"警报被关掉"，
> 不是"判据被修好"。
> 正确做法：A0 只做**文案 + 计数语义 + `rules` 占位**（不改 status）；"ratio 是否参与 status" 与阈值
> 0.9 一起挪到 A1+A2 落地后的一次**带数据的变更**（见 §11.5 的 A4′），并用 §6.3 已提到的 2×2 离线回归作为依据。

> ★评审补证（R-E10）：阶段的**顺序有硬约束，不能按 A1→A2 的自由组合上线**。把今天的判据换成"只允许
> ×1/×100、不允许 `abs()`、不允许 `/1e4 /1e8 /1e12`"（A2 的核心）后，同一份报告是
> **missing 103 → 135、ratio 0.6185 → 0.50**（实测）。也就是说 **A2 单独上线会让症状先恶化 32 条**——
> 它必须与 A1（派生目录）同批，或在 A1 之后立刻上，且这一期间 status 判定不能"先摘掉再说"。

### 6.2 为什么是 A（而不是 B/C/D）

1. **根因是判据，不是措辞**。B 只让读者少被误导一点点，D1 的"诚实者被罚"依旧存在（模型仍会被激励去少写同比、少写轧差，正好抹掉投研里最有用的部分——这与 README 已经修过的"同业 PE 拉低接地率"是同一类反向激励）。
2. **只改状态（D）会在下一次审计里复现同一个洞**：判词还在，只是没人看了。
3. **C 依赖模型服从性，且是新的输入面**；把它放在 A1 之后作为增量，风险最小。
4. **A 的每一步都能被同一组脚本证伪**：本轮的 8 个探针脚本就是回归夹具（见 §9 验收判据），修复与否可以用同一组数字对比——这是 A 相对于 B/C/D 的决定性优势。
5. **不提高/不降低阈值**：`0.9` 是产品承诺的一部分（`report_validator.py:38` 与 `web/lib/grounding.ts:40` 双处固定）。在判据修好之前动阈值，等于用一个数字去掩盖另一个数字。

### 6.3 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 派生目录自己算错，反而给编造值背书 | 目录必须**有限、带名、带单位**，每条派生量都要能用一个 3 行测试钉住；命中时 UI 显示「推算（公式 id）」而不是「已核验」 |
| A2 收紧后 ratio 再变一次，版本列表更混乱 | 用 `rules` 分级：跨口径不显示百分比；同一口径内才比较 |
| 0.9 阈值在新分母下不再合适 | 用 27 份历史报告（`api/data/app.db` + `reports` 表）做一次离线回归，输出"新旧分母 × 新旧分子"的 2×2 分布后再定阈值；阈值调整独立成一次变更并记录理由 |
| 前端 3 处文案与 2 处跳转是纯 UI 改动，容易漏测 | `web/tests/grounding.test.tsx` 现有 20 例已覆盖三态与面板；新增"按不同数值计数"与"摘要跳转"两组 |
| 老报告不回填（既有纪律）会与"可复算"冲突 | `rules` 缺席即 `absent`（现状就是这样），UI 明示「早期版本」；不复算、不回填 |

---

## 7. 证据边界：被本轮测量推翻的两个假设

诚实记录，避免后续节点按错误前提实现：

1. **"摘要与风险清单里藏着审计看不到的编造数字"——本份报告不成立。** 27 个数字全部也在正文块里被审计过（`NOT AUDITED ANYWHERE = 0`，`out/d3_coverage.txt`）。D5 因此降级为 **P2**，并按"通道缺失（未来藏身处）+ 不可定位"来陈述，而不是"当前已在骗人"。
2. **"接地判据只是漏了派生值，补上就好"——不成立。** 穷举两操作数能复现 95.1% 的 `factMissing`，但同一索引对随机取值的假命中率是 **15.74%**（`out/derived_gap.txt`）。这说明**不能**用"搜索算式"做修复；必须用 A1 的**有限命名目录**。这条测量是选型的关键依据。
3. 另记一条与缺陷无关但会吞证据的环境事实：本轮开始时 3000 端口上的 web dev server 是陈旧进程，`/CN:600519.SH` 返回 500，重启后恢复（§2.3 备注）。任何基于浏览器的复现都要先确认这一点。

## 8. 残留风险（修复后仍存在）

- 审计只能证明"可追溯"，永远不能证明"正确"：快照本身错了（本轮报告里 `quote.last=1634.88` 与日线收盘 `1729.52` 差 5.47%，`valuation.*` 全 null），接地率仍会很高。
- `peers[].symbol` 这类**字符串里的数字**（`600519`、`600809`）会持续产生语料噪声；A2 的"单位白名单"能挡住 `/1e4`，但挡不住"模型恰好在 600519 元附近写了一个数字"。
- `mock` 报告的 ratio（0.9884 / 0.9891）同样由这套判据产生：mock 是规则拼装，理应 1.0；现在不是，说明还有未归因的形态（不在本轮范围）。
- 派生的"单位换算"在 A1 里是硬编码的业务知识（亿元 = 1e8 元、pp = 百分点）；口径变化（如港股以千元计价）需要跟着改。

## 9. 验收判据（可执行）

修复后的 DoD —— 每一条都用本轮的脚本判定，不引入主观标准：

| # | 判据 | 检验命令 | 期望 |
| --- | --- | --- | --- |
| AC1 | 判词可复算 | `python .agentmesh/repro/grounding_doc_identity.py` | `stored` 与 `recomputed` 的 `factClaims/factMissing/identifierClaims` 三数相等（或 stored 带不同 `rules` 时显式标注） |
| AC2 | 手算推导不再算编造 | `python .agentmesh/repro/grounding_hand_verified.py` | 15 行 `checker says` 全为 `derived:<id>`，`factMissing` 不再包含它们 |
| AC3 | 取整/缩放不再假接地 | `python .agentmesh/repro/grounding_token_provenance.py` | `1/5/9/60` 四个 token 的 `grounded?` 为否 |
| AC4 | 计数按值 | `python .agentmesh/repro/grounding_repetition.py` | 输出含 `distinct_missing=43`（或按新分母的等价数），且徽标文案含「个不同数值」 |
| AC5 | 覆盖补齐 | `python .agentmesh/repro/grounding_d3_precision.py` + `ego-browser` round-01 | claim path 里出现 `summary#0`/`risks#N`；风险列表 `<li data-block-id>` 非 null |
| AC6 | 口径可分 | `ego-browser` round-04 | v10（无 `grounding`）显示「早期版本」、v11 显示 `claims-v3` 口径，二者不并排显示可比百分比 |
| AC7 | 诚实报告不被误判降级 | 重放 `ed01d950…` 的 doc+snapshot | `status` 不因 D1 类数字降级；编造夹具（`out/` 里的伪造段落）仍被标为未接地 |
| AC8 | 回归 | `cd api && python -m pytest -q`、`cd web && npm run test -- --run`、`npm run ci` | 全绿；`test_report_validator.py:159` 的不变量 `ungroundedCount == factMissing` 仍成立 |

## 10. 附：证据索引

| 文件 | 内容 |
| --- | --- |
| `.agentmesh/repro/grounding_evidence_all.py` | **一键入口**：顺序跑下面 9 个探针并打印结论表（§2.1） |
| `.agentmesh/repro/grounding_recon.py` | 全部报告：落库 payload vs 重算；文档结构；451 条 claim 的 path 分布 |
| `.agentmesh/repro/grounding_corpus_audit.py` | 语料规模与形态占比（189 叶 → 1424 token，1199 纯形态） |
| `.agentmesh/repro/grounding_doc_identity.py` | 落库 323/141 vs 重算 270/103；`report.json`/`result.json` 同一性 |
| `.agentmesh/repro/grounding_derived_gap.py` / `_calibrated.py` | 穷举两操作数命中率 95.1%；随机基线 15.74% |
| `.agentmesh/repro/grounding_hand_verified.py` | 15 条手算推导，公式复现数 15/15，判定全为 FABRICATED |
| `.agentmesh/repro/grounding_token_provenance.py` | `1/5/9/60/28/17` 的语料来源逐条溯源 |
| `.agentmesh/repro/grounding_repetition.py` | 103 条 / 43 个值；`-0.25×8` 等重复计分明细 |
| `.agentmesh/repro/grounding_d3_precision.py` | 摘要/风险清单 27 个数字的审计可见性（0 个不可见） |
| `.agentmesh/repro/grounding_version_drift.py` | 落库残渣 `-11`/`26` 与今天规则的对照；判定器的 git 时间线 |
| `.agentmesh/repro/grounding_small_ints.py` | 已核验小整数中"没有任何字段等于它"的清单 |
| `.agentmesh/repro/grounding_fragment_taxonomy.py` | 文本 → claim 的片段分类实况（含 `push2`、`rcentile3` 等切词残渣） |
| `.agentmesh/repro/grounding_blind_spots.py` | 运行包（bars.csv/fundamentals.md/sources.md）里可查到的 8 条 |
| `.agentmesh/repro/grounding_string_leaves.py` | 字符串叶的数字来源（`sources[1].title`「近 3 年」→ `3`；`instrument.symbol` → `600519`） |
| `.agentmesh/repro/db_overview.py` | 库现状（17 runs / 13 reports / 报告与 run 的对应关系） |
| `.agentmesh/repro/out/*.txt` | 以上脚本的 UTF-8 输出（本机 stdout 是 GBK，故全部落文件） |
| `.agentmesh/ego/g3-01/round-0{1,3,4}.mjs` + `*.png` | 浏览器侧证据：徽标 aria-label、样本面板首条 `-11`、版本列表只有 v11 有百分比 |

---

*本节点只做诊断与方案，未修改任何产品代码，未 `git commit`。推荐方案 A 的实现留给后续节点，验收判据即 §9 的 8 条（评审节点对其中 3 条提出修订，见 §11.6）。*

---

## 评审结论

> **评审节点（迭代 3/3）**：只读复核上一节点的 `analysis.md`。本轮**没有**修改产品代码，**没有** `git commit`，**没有**开始写修复。
> 除追加本节外，正文里另插入了 10 处 `★评审补证 / ★评审更正（R-E1…R-E10）`，全部带行号引用或可复跑的只读命令。
> 所有被引数字都用本机同一份 `api/data/app.db` + `api/workspace/runs/35bf7e07…/snapshot.json` 重算过一遍（§11.7）。

### 11.1 一句话裁决

**根因成立，证据链基本闭合，缺陷定级与"五个症状一个根因"的判断可以采信；但推荐修复要改顺序与四处内容。**

最终推荐修复 = **A′**：保留 A 的实质（判词三改一补），重排为 **A0′ → A1 → A2 → A3 → A4′** 五阶段，其中
**A0′ 不再动 `status`**（原文的"按方案 D 摘掉 ratio"从止血降级为 A4′ 的带数据变更），并修正 A2/A5/A6 的三处具体设计。
理由不是口味：本轮实测到 **A2 单独上线会把 103 条 missing 变成 135 条（ratio 0.6185 → 0.50）**，
而**摘掉 ratio 分支会让一份真编造的报告变成 `ready`、并同时放开被 `status` 挡住的 `deep` 追问门禁**——
两件事都证明"顺序"和"哪些阶段可以单独上线"是这次修复的组成部分，而不是实施细节。

### 11.2 根因证据复核（逐条）

复核口径：①被引代码是否**真的在失败路径上执行**；②数字能否**独立重算**；③是否存在**替代解释**。

| # | 报告的断言 | 复核方式（只读） | 结论 |
| --- | --- | --- | --- |
| 1 | 判定就是 `claim.canonical in corpus` 的集合成员 | 读 `numeric_claims.py:230-237 / 240-270 / 539-582`；`grep -rn "grounding_report(" api/` 只有 `report_validator.py:348` 与薄封装 `:142-150` | **成立**。全仓只有一条判定路径，不存在"另一个实现才是真凶" |
| 2 | D1：推导值一律判 FABRICATED | `grounding_hand_verified.py` 的 15 行展品与 `out/hand_verified_derived.txt` 逐行一致；`derived` 通道是路径判据（`:413`），schema 无该字段 | **成立**（承重证据）。15/15 行算式可复现，判定全为 FABRICATED |
| 3 | D1 放大：穷举可复现 95.1% | 读 `out/derived_gap.txt` | **需修正**→95.1% 只是**上限**，其"救回"式子多为量级巧合（R-E1）。可采信的只有"至少 15/103 确证" |
| 4 | D2：语料 189 叶 → 1424 token、1199 纯形态 | 独立重算 `len(snapshot_number_corpus(snap))` = **1424** | **成立** |
| 5 | D2：`1/5/9/60` 的语料来源 | 逐个剔除顶层键重建语料：`1`←仅 `peers`、`5`←仅 `performance`、`9`←仅 `technicals`、**`60` 剔除 `instrument` 或 `peers` 后仍在** | **1/5/9 成立，`60` 的"唯一来源"不成立**（R-E2）；结论方向不变 |
| 6 | D3：103 条 / 43 个不同数值、`-0.25 ×8` | 独立重算：fact 270 / missing 103 / grounded 167 / distinct missing **43** / distinct grounded 52 / `1`+`3` **29/167** / `-0.25` **8 次** | **成立**，与 `out/repetition.txt` 完全一致 |
| 7 | D4：同一 doc+snapshot → 落库 323/141 vs 重算 270/103 | 直接调 `grounding_report()`：**270 / 103 / derived 0 / identifier 273 / projection 6 / ratio 0.6185** | **成立** |
| 8 | D4：输入同一性（原论证只比了 claim 数） | 补：运行目录写盘时序 `snapshot.json` 13:02:30（= doc 的 `asOf` 18:02:30Z）→ `report.json` 13:04:47（= `generated_at` 18:04:47Z），之后无写盘 | **成立且被加强**（R-E3）：可以排除"快照被重写"，因此差异只能来自规则变化 |
| 9 | D4：git 时间戳证明"边跑边改" | `git log --format="%h %ad \| %cd"` → `4b434b8` author==committer 18:43:57Z，提交信息为 "M13-M18 + 评审修订" 批量提交 | **事实成立、因果需收窄**（R-E4）：只能证明"判词来自未进版本库的工作树状态" |
| 10 | D4：版本列表"并排展示两套口径" | 查库：v10/v8/v7 的 `status` 是 **`superseded`**（非 `ready`），v10 的 `quality_json` 111B 无 `grounding` | **机制写反了一半**（R-E5）：真因是 `report_service.py:129` 用生命周期状态覆盖质量判词 |
| 11 | D5：摘要/风险清单不在审计范围内 | `extract_claims()` 产出 549 条 claim，`path.startswith(('summary','headline','risks['))` → **0 条**；top-level 前缀只有 6 个章节 + `keyMetrics[n]` | **成立** |
| 12 | D5：27 个数字全部旁证可见 | 独立重算（把三个字段临时喂进 `_claims_from_text`）：**27 条 fact claim / 10 条未接地 / 8 个不同数值** | **成立**（且 §11.5 用它作 A5 的计数副作用） |

**结论**：报告的 12 条核心断言里 8 条完全成立、3 条需要收窄措辞、1 条（`60` 的唯一来源）举证过头。
**没有任何一条被推翻**，五个症状共享同一个根因（字符串形态的集合成员判断）这一判断成立。

### 11.3 报告漏掉的四件事（评审新增，均可在源码里指认）

**N1（严重度被低估）——D1 不只是"挂个降级徽标"，它会把 `deep` 追问整档关掉。**
`api/app/routers/asks.py:33-34` 定义 `DEEP_BLOCKED_STATUSES = ("provisional", "degraded")`，`:76` 直接
`raise ReportNotReady(detail={"status": …})`。也就是说 `ed01d950…` 这份"老老实实写同比/单季/隐含空间"的报告，
**它的深度追问功能是可点但必失败的**。用户可感知的链条应当是
「诚实分析 → ratio 0.62 → `degraded` → deep 追问 4xx → 用户只能改用 cite 档」，
这比"版本列表少一个百分比"严重一个数量级，而 §4 的影响面表里没有这一行（已补）。
附带一个自相矛盾：报告被新版取代后 `status` 变成 `superseded`，而 `superseded` **不在** 黑名单里 →
同一个内容、换一版之后 deep 又能用了。这恰好说明门禁判的不是"内容是否可信"，而是"状态列的当前值"。

**N2——D4 的表层症状真正来自"状态列双语义"，不是"两套口径并排"。**
`report_service.py:129`（新版本落库时）与 `:332`（回填路径）都会把旧行写成
`is_latest=0, status="superseded"`，**质量判词被覆盖**；随后 `schemas/report.py:431 QUALITY_STATUS_EQUIVALENT`
又把 `superseded` 读成 `ready` 喂给 `/diff`。于是：库里 ratio 0.7451 的 v10，在版本列表里没有徽标
（`VersionSwitcher.tsx:114` 只认 `degraded`），在两版对比里被呈现为"正常报告"。
→ **A6 只加 `rules` 字段是不够的**：不把"质量"和"是否最新"拆成两列（或让 UI 读 `quality` 而非 `status`），
版本之间永远看不到口径，AC6 会以"契约上成立、界面上仍然什么都没有"的方式假通过。

**N3——第三处"处"与单位混用。** §3.4 只点了 `ReportPanel.tsx:364`。徽标展开面板里还有一处：
`TrustBadge.tsx:66-68` 用 `quality.totalMissing`（**条**）与 `samples.length`（**块**，上限 20）比较，
文案 `copy.trust.more = '仅列前 {shown} 个块，共 {total} 处'`（`copy.ts:342`）。A4 只改一处会让面板同时说
"43 个不同数值"和"共 141 处"。另外 `grounding.samples` 上限是 **20 个块**，所以"43 个不同数值"永远**列不全**——
面板必须直说"另有 N 个数值未展开"，否则新文案会造出一种新的过度承诺。

**N4——仓库自己的纪律被 `samples` 违反了，而 A5 会放大它。**
`web/lib/blocks.ts:13-14` 明文写着"不要把这些 id 落到数据库或 schema 里 —— 它们是派生值，块顺序变了 id 就变"，
但 `numeric_claims.py:569-577` 把 `blockId` 写进了 `quality.grounding.samples`，而
`report_validator.py:153 prune_unknown_blocks()` 会在校验阶段**改变块序号**。
后果：任何一个被 prune 过的块，它落库的样本 `blockId` 都指向改号后的错误位置，徽标点过去会静默跳到别的段落。
**A5 会把这个坑复制到 `summary`/`risksList` 上**（那是顶层字段，风险更低，但 `risksList[i]` 的 `i` 同样是派生值）。
→ 修复时必须二选一：样本只存 `path`（+ 前端由 path 反查），或明确"样本 id 是生成时快照，允许过时"并写进注释与测试。
这一条报告完全没提。

### 11.4 候选修复审计（A0–A6 逐项）

审计维度：①是否覆盖全部边界（并发 / 错误路径 / 边界输入）；②是否为**最小改动**；③会不会引入新回归。

| 修项 | 覆盖边界？ | 最小性 | 新回归风险 | 裁决 |
| --- | --- | --- | --- | --- |
| **A0 文案 + 摘 ratio** | 文案部分 OK；status 部分**危险** | 文案是最小改动；"摘 ratio"超出"修判据"的范围 | ①关掉唯一的编造告警 ②`asks.py:76` 的 deep 门禁一起放开 ③打破 `test_report_validator.py:127-134`（strict→degraded 只能由 ratio 触发）④AC7 变成"证明警报关了" | **拆开**：文案保留为 A0′，status 变更移到 A4′ |
| **A1 命名派生目录** | **未覆盖分母记账**：今天 `derived` 与 `fact` 走同一张账（`:556-567`），派生命中若只把 `cls` 改成 `derived`，它**仍然算 missing**（因为 `:564` 的 corpus 判定没变、`:566` 照样 `fact_missing += 1`） | 是（有限目录，不搜索） | ①**除零**：同比/隐含空间的分母可能为 0（`(a-b)/b` 且 `b=0`）→ 必须显式跳过；②**空快照**：`grounding_report(doc, {})` 是既有用例（`test_numeric_claims.py:224`），派生目录必须对缺字段优雅返回空；③**别加模块级缓存**：`snapshot` 是 dict（不可哈希），照 `id()`/`json.dumps` 前几位做 key 会串味 | **采纳 + 补三条**：命中记 `derived:<id>`、**进分母且算 grounded**、`derivedClaims` 仍单独计数 |
| **A2 严格值匹配** | 未覆盖"显示精度"的来源：`1,672.00` 这类 2 位小数的字段，其 ulp 是 0.005，而 claim 是 `1672` → "一个 ulp" 会把合法写法判失配 | 是（收紧要动的地方集中在一个函数） | **已实测**：单独上线 missing 103→135（0.6185→0.50），症状先恶化；且 `capabilities._grounding_available()` 断言的 `fact_claims == 2` 若被打歪，`features.grounding=False` → 徽标从全站消失 | **采纳 + 与 A1 同批**；顺便要求"精度来自字段自身的字符串形态"，不是猜 |
| **A3 identifier 归并** | 覆盖了 `3/5`、`9M26`、`pp` | 是 | 与 A2 同一函数，拆开上线会二次改分母 | **与 A2 合成一批** |
| **A4 按值去重计数** | **未覆盖**：面板只能列 20 块（N3）；`totalMissing` 与 `samples.length` 单位混用 | 是（加两个计数字段） | `GroundingReport.as_payload()` / `GroundingQuality`（Pydantic）/ `GroundingQuality`（TS）三处镜像 + `test_report_validator.py:147-154` 是**精确键集断言**，漏一处就红；`web/tests/review-regressions.test.tsx:241` 把"3 处数字未能…"钉死 | **采纳 + 三处镜像 + 两处文案 + 一条用例** |
| **A5 覆盖补齐** | **不覆盖** id 命名空间（R-E7）与计数副作用（R-E8） | 否——原文 `summary#0`/`risks#N` 会撞坏跳转 | `summary#0` 会让 `setActiveSection('summary')` → 正文整篇消失；`risks#N` 会安静地跳到同章第 N 块（错位）；单独上线会把"信心度 3/5"里的 `3`、`5` 算成已核验 | **采纳 + 换 id 方案 + 必须排在 A2/A3 之后** |
| **A6 口径版本 + 指纹** | **不覆盖** N2（状态列双语义）与 N4（派生 id 落库） | 是 | `trustState()` 只有四种状态（`web/lib/grounding.ts:100-114`），"跨口径不给百分比"需要**新增一个分支**，影响 27 例 vitest 与全部调用点；`GroundingQuality` 三处镜像同上 | **采纳 + 扩 `TrustState` 一个 `legacy` 分支 + 处理 N2** |
| **B/C/D** | B 不动判据；C 依赖模型服从性且是新的输入面；D 是 A0 的 status 部分单独上线 | — | 见 §6.2（本轮同意它的取舍），但 D **不能**在任何判据修复之前上线（R-E9） | 维持"不采纳" |

**性能/并发复核（说清楚，别留悬念）**：判词在 `validate_doc()` 内同步计算，纯函数、无 IO、无共享可变状态
（corpus 是局部 set）；A1 的派生目录是 O(字段数)，A2 的严格匹配仍是"每个 claim 一次查表"。
即使分辨率提到 5 位小数，量级是 `549 claims × O(1)`。**这里没有性能风险，也不该为了实现 A1/A2 引入任何缓存**
（引入缓存才是本次唯一真实的并发风险来源）。

### 11.5 最终推荐修复（唯一方案）

**A′「判词三改一补，重排五阶段」** —— 与 A 相同的改动集合，但阶段切分、依赖与两个阶段的归属不同：

| 阶段 | 内容（含评审提出的具体修正） | 依赖 | 半天/天 |
| --- | --- | --- | --- |
| **A0′（0.5 天）只改说辞与计数语义，不动 status** | ①`ReportPanel.tsx:364` 的「N 处」→「N 次引用 / M 个不同数值」；②`copy.ts:342` 的「共 N 处」改单位（N3）；③`VersionSwitcher.tsx:115` 的 title 附 `rules`（缺 `rules` 时显示「早期版本」，不显示百分比）；④**`report_validator.py:361` 一个字都不改** | 无 | 0.5 |
| **A1（1 天）派生量目录** | `derived_values(snapshot) -> dict[str, DerivedValue]`：YoY / QoQ / 单季轧差 / 距均线 / 隐含空间 / 波动-收益比 / 情景加权中枢，稳定 id（`yoy:financials[0].revenue`）。命中 → `cls=derived` + `derivedId`，**进分母、算 grounded、`derivedClaims` 单独计**（改 `numeric_claims.py:556-567` 的记账，否则派生命中仍算 missing）。除零与空快照显式返回空目录 | 无 | 1 |
| **A2（1 天，与 A3 同批）严格值匹配 + identifier 归并** | 语料 `set[str]` → `dict[str, Provenance]{路径, 单位, 自身显示精度}`；去 `abs()`；缩放白名单只保留快照里真实存在的单位（元↔亿元、小数↔%）；`N/M`、`9M26`、`N 日`、`pp`、`第 N` 等归 `identifier`。**必须与 A1 同一批上线**（R-E10：单独上是 0.6185→0.50） | A1 | 1 |
| **A3（1 天）覆盖补齐 + 口径与指纹** | `summary` / `headline` / `risksList`（**不含 `#` 的容器 id**，或 `riskList:i` + `parseBlockId` 同步扩，R-E7）；`quality.grounding.rules="claims-v3"` + `docHash` + `snapshotHash`；同步三处镜像 + 精确键集断言。样本只存 `path`（N4）或显式声明"id 是生成时快照" | A1、A2 | 1 |
| **A4′（0.5–1 天）status 与阈值：带数据的一次变更** | 用 §6.3 已提到的 2×2 离线回归（27 份历史报告 × 新旧分母/分子）决定：ratio 是否退出 status、`GROUNDED_THRESHOLD` 是否重标；同时把 `asks.py:34` 的 `deep` 门禁从"看 status"改成"看结构化缺口"（例如 `factMissing > 0 且 samples 含 fact 类`）；重写 `test_mock_documents_are_not_graded_on_grounding`（它原来靠 ratio 分支成立） | A1、A2、A3 | 0.5–1 |

**为什么是这个方案（最小性与充分性）**

1. **只有 A1+A2 能同时消掉两个方向**：D1 是假阴性（推导被当编造）、D2 是假阳性（巧合被当接地）。
   只做 A2 → 实测 103→135（恶化）；只做 A1 → 假阳性一条不动，且 17.4% 的"已核验"仍是 `1`/`3` 这类巧合。
   两者合起来才是"用同一把尺子"。B 只让读者少被误导一点点；C 依赖模型服从性；D 撤警不修判据——**都不满足充分性**。
2. **A0′ 与 A4′ 的拆分是本次唯一实质性的方案改动**，理由是 R-E9/R-E10 的两组实测：
   在 A1+A2 之前动 status，等于把"可测的误判"换成"不可测的沉默"，并且会立刻让 `deep` 门禁对真编造报告失效。
3. **A3 不能提前**：A5/R-E8 实测"覆盖补齐会把 `信心度 3/5` 的 `3`、`5` 算成已核验"，
   而 A3 的 identifier 归并正是解药 → 顺序反了会把假阳性写进"读者第一屏"。
4. **A4 的计数语义必须带"列不全"的显式说明**（N3）：43 个不同数值里只有 ≤20 个块能展开，
   新文案不能暗示"你看到的就是全部"。
5. **A6 的收益不能被低估**：没有 `rules`+`docHash`+`snapshotHash`，本轮这场"39 分钟之谜"会在下一次
   规则调整后原样重演，而每一次都会让 §9 的验收脚本失效。

**如果排期只允许一天**：只上 A0′ + A3 里的 `rules/docHash/snapshotHash`（约 0.5–1 天），
并把徽标文案统一改成「可追溯 N/M · 口径 claims-v1（历史报告不可比）」。
这次变更**只承诺"不再误导"**，不承诺"判据正确"——D1/D2 的误判会原样保留，必须在说明里写清楚，
否则就变成了另一种过度承诺（和 README 里修过的"同业 PE 拉低接地率"是同一类错误）。

### 11.6 验收判据修订（对 §9 的增补，全部可执行）

§9 的 8 条里，**AC1 与 AC6 按现在的写法不可达成或将假通过**，AC4/AC7 需要量化基线；其余保留。

| # | 修订后判据 | 检验命令（只读） | 期望 |
| --- | --- | --- | --- |
| **AC1′**（替代 AC1） | "落库 == 重算"只对**上线后新生成**的报告成立；旧报告以 `rules` 缺席显式标注为不可比 | `python .agentmesh/repro/grounding_doc_identity.py`（对最新一份报告） | 新报告 `stored == recomputed` 三数相等**且** payload 含 `rules`；`ed01d950…` 仍然不等，但 UI 显示「早期版本 · 不可比」 |
| **AC1″**（新增） | 判词可机械复算：同一份 `docHash`+`snapshotHash`+`rules` 必须重算出同一 payload | 对 `quality.grounding.snapshotHash` 找回落盘 snapshot 并重算 | 三个计数逐一相等（这与 AC1 不同：AC1 是"两份数据碰巧一致"，AC1″ 是"输入指纹对得上"） |
| **AC2**（保留） | 15 条手算推导不再算编造 | `python .agentmesh/repro/grounding_hand_verified.py` | 15 行 `checker says` 均为 `derived:<id>`；另有 1 例"目录外推导仍算 missing" |
| **AC3**（保留 + 补一行） | 取整/缩放不再假接地 | `python .agentmesh/repro/grounding_token_provenance.py` | `1/5/9/60` 四个 token 的 `grounded?` 全为否（**`60` 也必须是"否"**——见 R-E2，只能靠 A2 的严格匹配，不能靠"唯一来源"） |
| **AC4′**（替代 AC4） | 计数按值，且"列不全"要说出来 | `python .agentmesh/repro/grounding_repetition.py` + `grep -rn "处数字未能" web/` | 输出含 `distinct_missing`；`grep` 结果为空；面板含「另有 N 个数值未展开」 |
| **AC5′**（替代 AC5） | 覆盖补齐且**跳转不破坏版式** | `grounding_d3_precision.py` + ego-browser 点一条 `summary` 样本 | claim path 出现 `summary`/`risksList`；点击后 `#report-body` 内仍有 ≥3 个 section（防 R-E7 的"正文消失"）；风险 `<li>` 上有定位属性 |
| **AC6′**（重写 AC6） | 跨口径**可见**且不可比 | vitest：喂一份 `status='superseded'` 且**带** `grounding` 的版本 | 显示「早期版本」或「口径 claims-v1」，**不显示** 可比百分比；现有写法（用 v10 这种没有 `grounding` 的旧行）会空过（N2） |
| **AC7′**（补量化） | 诚实报告不再因 D1 降级，**且**真编造仍被拦 | 重放 `ed01d950…`；再加一个含编造数字的夹具 | 前者 `status != degraded`；后者 `status == degraded` **且 `deep` 追问被拒**（否则 A0 只是把警报关掉） |
| **AC8**（保留 + 点名） | 回归全绿 | `cd api && python -m pytest -q`、`cd web && npm run test -- --run`、`npm run ci` | 特别盯 4 处：`test_report_validator.py:147-154`（精确键集）、`:159`（`ungroundedCount == factMissing`）、`:127-134`（mock 不评分）、`review-regressions.test.tsx:241`（"3 处"文案） |
| **AC9**（新增） | 能力开关不误关 | `curl -s http://127.0.0.1:8000/api/v1/capabilities \| grep grounding` | `features.grounding == true`（`capabilities.py:36-49` 的夹具断言 `fact_claims == 2` 被打歪时徽标会从全站消失） |
| **AC10**（新增） | 体积与耗时 | `python -c` 查最新 `quality_json` 长度；`timeit` 跑 `grounding_report` | `quality_json` ≤ 4 KB（现状 v11 = 3284 B）；单次计算 ≤ 50 ms |

### 11.7 本轮复核命令（全部只读，任何人可复跑）

在仓库根目录执行（Windows cmd.exe；`python -X utf8` 只是为了让中文不被 GBK 吞掉）：

```bat
:: 1. 落库判词 vs 重算（AC1 的现状：不等，且不是输入漂移）
python -X utf8 -c "import sys,json,sqlite3; sys.path.insert(0,'api'); from app.services.numeric_claims import grounding_report; c=sqlite3.connect('api/data/app.db'); c.row_factory=sqlite3.Row; doc=json.loads(c.execute(\"select doc_json from reports where id like 'ed01d950%'\").fetchone()['doc_json']); snap=json.load(open('api/workspace/runs/35bf7e077a174cbeafa7d3634b0b5cc6/snapshot.json',encoding='utf-8')); g=grounding_report(doc,snap); print(g.fact_claims,g.fact_missing,g.derived_claims,g.identifier_claims,g.projection_claims,round(g.ratio,4))"
:: → 270 103 0 273 6 0.6185

:: 2. 这份报告是被 ratio 判降级的，还是被 missing/errors 判的
python -X utf8 -c "import sys,json,sqlite3; sys.path.insert(0,'api'); from app.services import report_validator as v; c=sqlite3.connect('api/data/app.db'); c.row_factory=sqlite3.Row; doc=json.loads(c.execute(\"select doc_json from reports where id like 'ed01d950%'\").fetchone()['doc_json']); snap=json.load(open('api/workspace/runs/35bf7e077a174cbeafa7d3634b0b5cc6/snapshot.json',encoding='utf-8')); o=v.validate_doc(doc,snap); print(o.status, round(o.grounded_ratio,4), len(o.errors), o.missing_sections)"
:: → degraded 0.6185 0 []

:: 3. 语料规模与四个可疑 token
python -X utf8 -c "import sys,json; sys.path.insert(0,'api'); from app.services.numeric_claims import snapshot_number_corpus as C; snap=json.load(open('api/workspace/runs/35bf7e077a174cbeafa7d3634b0b5cc6/snapshot.json',encoding='utf-8')); c=C(snap); print(len(c), {t:t in c for t in ['1','3','5','9','60','4']}); print({t:[k for k in snap if t in C({kk:vv for kk,vv in snap.items() if kk!=k})] for t in ['1','5','9','60']})"
:: → 1424 {'1':True,'3':True,'5':True,'9':True,'60':True,'4':False}
:: → 剔除后仍在 = ['1'←peers,'5'←performance,'9'←technicals,'60'← instrument 与 peers 任一都够] → 60 不是唯一来源

:: 4. A2 单独上线会怎样（去 abs、只允许 ×1/×100）
python -X utf8 -c "import sys,json,sqlite3; sys.path.insert(0,'api'); from app.services import numeric_claims as nc; c=sqlite3.connect('api/data/app.db'); c.row_factory=sqlite3.Row; doc=json.loads(c.execute(\"select doc_json from reports where id like 'ed01d950%'\").fetchone()['doc_json']); snap=json.load(open('api/workspace/runs/35bf7e077a174cbeafa7d3634b0b5cc6/snapshot.json',encoding='utf-8')); nc._renderings=lambda n:{f'{s:.{d}f}' for s in (n,n*100.0) for d in (0,1,2,3,4)}; print(nc.grounding_report(doc,snap).fact_missing)"
:: → 135（今天 103）

:: 5. 版本状态列到底是什么
python -X utf8 -c "import sqlite3; c=sqlite3.connect('api/data/app.db'); print(c.execute(\"select version,status,grounded_ratio from reports where instrument_id like '%600519%' order by version desc\").fetchall())"
:: → (11,'degraded',0.5635) (10,'superseded',0.7451) (9,'superseded',0.9885) …
```

**复核签署**：§11.2 的 12 条断言、§11.3 的 4 条新增事实、§11.5 的 5 个阶段依赖，
都可以用上面 5 组命令或对应源码行号独立验证；本轮没有发现能推翻"五个症状一个根因"的反例，
发现的反例都指向**修复的顺序与三处具体设计**，已逐条写入本节。

---

## 实施过程发现的方案缺陷

> **实施节点（迭代 3/3，本图最后一个节点）**：把 §11.5 的 **A′（A0′ → A1 → A2+A3 → A3 → A4′）** 落到代码。
> 本节只记**方案本身**在实施中被证伪或被改写的地方，每条都带实测数字或可复跑命令。
> 不改产品行为的地方一律没写（那是实现细节，不是方案缺陷）。

### I-1　`derived_values(snapshot)` 的签名不成立：隐含空间必须读**文档**

评审给的签名只有 `snapshot`，但「目标价相对现价的隐含空间」= `(doc.rating.targetPrice − 现价)/现价`，
而 `targetPrice` **不在快照里**（它是模型的判断，属 `projection` 类，`snapshot.json` 的键里没有它）。
只按签名实现，A1 的七类派生量里会有一类永远算不出来，AC2 的 15 行也就永远差一行（实测 `1.06` 无解）。

**改法**：`derived_values(snapshot, doc=None)`。这条**不违反**投影纪律：目标价本身仍是 projection
（不进分母、不设升格通道），被命名的是"由一个投影与一个事实相除得到的比值"。
`scenarioUpside`（每个情景目标价 vs 现价的隐含空间）同理。

### I-2　"七类派生量覆盖 15 行手算推导"是错的，实际需要 11 类

按评审列的 7 类（YoY / QoQ / 单季轧差 / 距均线 / 隐含空间 / 波动-收益比 / 情景加权中枢）
实现后，15 行展品里仍有 5 行判不出来。缺的是：

| 展品 | 还缺的那一类 | 目录 id |
| --- | --- | --- |
| `48.7` / `46.0` | 单季净利率（单季净利 ÷ 单季营收） | `singleQuarterMargin:financials[i]` |
| `-2.4` | 百分点变化（两个水位相减） | `pp:financials[i].netMargin` |
| `3.8` | 两个同比的**幅度比**（降幅倍数） | `magnitudeRatio:financials[i].netProfit/revenue` |
| `5.47` / `2.88` | 现价与最近两个收盘的缺口 | `priceGap:quote.last@…` / `priceGap:anchor@…` |
| `3.00`（以及 26Q2 −5.23%、26Q1 +6.34%） | 单季同比（今年的单季 vs 去年同期的单季） | `singleQuarterYoy:financials[i].{field}` |

这是**方案层面的低估**，不是实现疏漏：它说明"投研报告里最常写的数字"比 7 类多。
每一类都仍然满足"有限、带名、带单位"，所以结论（用有限命名目录、不用搜索）不变。

### I-3　评审列的 "QoQ" 在**累计口径**财报上没有定义，生成它就是造一个假裁判

`financials[]` 是年内累计口径（9M / H1 / Q1）。`(9M − H1)/H1` 是"单季轧差"，不是环比；
而对 `revenue` 这类金额做"上一条累计期 ÷ 再上一条累计期"，算出来的是两个不同长度区间的比值，
**没有任何业务含义**。第一版按字面实现了 `qoq:`，目录里出现了 4 个这样的条目 ——
它们一旦被命中就会替一个错误的数背书（与本轮要修的病同型）。

**改法**：删掉 `qoq:`（金额没有环比语义），只保留 `singleQuarter` / `singleQuarterYoy` / `pp`。
同一处还发现一个**更危险的**守卫缺失：`26Q1` 的上一条累计期是 `FY25`，相减得 `−1173.5 亿元`
（一个凭空的"单季营收"）。现在 `_single_quarter()` 要求两条累计期**同一财年**才相减，
并由 `test_derived_catalog_never_crosses_a_fiscal_year_boundary` 钉住。

### I-4　"相对误差 ≤ 该字段显示精度的一个 ulp"这条判据**过紧**，会把诚实报告重新推下降级

按字面实现（舍入损失 ≤ 1 ulp）后实测：mock 报告 ratio 掉到 **0.87–0.93**，真实报告 0.6185 → **0.9091**
（靠的还是一堆刚好卡线的值）。原因是这条判据把两种完全正常的排版判成"不是同一个数"：

| 字段值 | 报告里的写法 | 相对损失 | 1-ulp 判据 |
| --- | --- | --- | --- |
| `ret1w = −2.933022` | `−2.93`（两位小数） | 0.103% | ✗ 判失配 |
| `ret1y = 17.234` | `17`（两位有效数字） | 1.4% | ✗ 判失配 |
| `changePct = −2.22` | `−2.2` | 0.9% | ✗ 判失配 |

而 D2 的两条假接地（`0.78 → 1`、`4.8086 → 5`）的共同点根本不是"损失多少"，是**只保留了一位有效数字**。

**改法**：判据换成"**保留了几位有效数字**"—— ≥2 位有效数字的写法永远合法；
只有 1 位时要求相对损失 ≤ 0.1%。`_precisions()` 里就是这么写的，`test_ac3_*` 两个用例
（一个正证、一个反证）钉住两侧。这一处改写把 mock 从 0.87 拉回 **0.93–0.96**，真实报告从 0.9091 到 **0.9453**。

### I-5　`÷1e12`（万亿元）不能从缩放白名单里删掉

评审写的是"只保留元→亿元、小数→%"。但 `valuation.marketCap = 2.1e12` 在中文报告里**就是**"2.1 万亿"，
删掉 `÷1e12` 会让市值这条最常见的数字永远无法接地。同理 `÷1e8`（亿元）只对**钱的数量级**有意义，
对 `rsi14 = 43.17` 这种量值做 `/1e8` 只会制造噪声。

**改法**：白名单带**量级门槛**（`_allowed_scales`）：`×1` 永远；`×100` 只在 `|v| ≤ 1`；
`÷1e8` 只在 `|v| ≥ 1e6`；`÷1e12` 只在 `|v| ≥ 1e9`。`÷1e4`（万元）仍然**不在**白名单 ——
它就是 `600519 → 60` 那条假接地的入口（AC3）。

### I-6　`[S1,S2]` 里的裸 `1` 是被 `IDENTIFIER_RE` 的**逗号守卫**放过去的（15 次）

修完 A2 之后真实报告仍有 `1 ×15` 的缺口，逐条溯源发现它们全部来自引用角标 `[S1,S2]`：
`IDENTIFIER_RE` 的尾部守卫是 `(?![\d.,])`，第一个 `S1` 后面紧跟逗号 → 整段不匹配 →
剩下的裸 `1` 成了必然失配的 fact claim。

**改法**：守卫放宽为 `(?![\d.])`。真正的危险是"小数点被吞掉"（`PE 24` 吃掉 `24.1` 的 `.1`），
而千分位 `1,234` 从不紧贴字母（不存在 `MA1,234`）。这条**不是**方案缺陷，但它说明
"跑通反证"比"读一遍设计"有效得多 —— 15 次假缺口只靠一次实测就暴露了。

### I-7　量词正则把小数劈成两半（`234.81 位于` → `234` + `81 位`）

A3 的"非数据数字归 identifier"第一版把 `个/条/家/只/次/位/名/项` 全部当量词，
于是 AAPL 的 mock 报告里 `现价 234.81 位于 MA20 上方` 被 `81 位` 咬掉一段，
小数被劈成 `234` 与一个整数 claim —— **mock 报告凭空多出 3 条缺口**（0.9701 → 0.9589 的一部分）。
**改法**：每条数字分支加 `(?<![\d.])` 前视守卫，并去掉 `位/名/项`（`位于`/`名义`/`项目` 都是常用词）。
教训写进这里：只用真实报告做验证会漏掉这类 bug，**mock 报告（规则拼装、理应 1.0）是更强的反证夹具**。

### I-8　A4′ 的实测结论是：**status / 阈值 / deep 门禁一个字都不用改**

评审要求"用 2×2 离线回归决定 ratio 是否退出 status、阈值是否重标"。跑了全部 **13 份**有快照的历史报告
（`git show HEAD:api/app/services/numeric_claims.py` 作为旧口径，与工作树对跑）：

| 报告 | 旧 ratio | 新 ratio | 旧/新 missing |
| --- | --- | --- | --- |
| v11 openai（就是本轮那份诚实报告） | 0.6185 | **0.9453** | 103 → 14 |
| v10 openai | 0.6020 | 0.7767 | 121 → 71 |
| v8 openai | 0.6447 | 0.8119 | 108 → 57 |
| v7 openai | 0.8092 | 0.7909 | 58 → 60 |
| v6 openai | 0.5783 | 0.7357 | 132 → 88 |
| mock ×5 | 0.9423–0.9701 | 0.9273–0.9589 | 2–3 → 3–4 |

分布出现了**干净的间隔**：诚实报告与 mock 落在 0.927–0.9589，旧版真实报告落在 0.7357–0.8119。
`0.9` 这个阈值现在正好落在这条缝里 —— 判据修好之后它才第一次成为"分界线"而不是"压制器"。

**结论（带数据的一次变更）**：`report_validator.py:361` 保持 `ratio < 0.9 or missing` **不变**；
`GROUNDED_THRESHOLD` 保持 0.9 **不变**；`asks.py:34/76` 的 `deep` 门禁**不变**。
R-E9 担心的两件事都没有发生：编造告警仍然在（"假设增长 123456.7%" 的夹具照样 `degraded`），
`deep` 门禁也仍然拦得住 —— 因为「诚实报告不再被判降级」这件事，**是判据修好带来的，不是警报被关掉带来的**。
这也印证了 R-E9 的判断：在 A1+A2 之前动 status 才是错的。

### I-9　N4（样本里的派生 id 落库）比评审描述的**更窄**，但新字段确实要换 id 方案

N4 说 `numeric_claims.py:569` 把 `blockId` 写进落库样本、而 `prune_unknown_blocks()` 会改块序号，
所以落库的 `blockId` 会指错。实测路径是：`normalize_doc()`（含 prune）→ **之后**才 `grounding_report(repaired, …)`，
所以样本 id 与**落库文档**同序，不存在"prune 之后错位"的问题；真正会漂移的只有"校验后被二次改写 doc_json"，
本仓没有这条路径。**改法**：保留 `blockId`（前端靠它跳转），新增的三个顶层字段用不含 `#` 的容器 id
（`summary` / `headline` / `riskList`），并把这个"什么时候安全"的判断写进代码注释。

### I-10　AC5′ 的"点击后 `#report-body` 内仍有 ≥3 个 section"在本机无法用浏览器验证

本节点没有可用的浏览器自动化（ego-browser 需要外部会话），所以 AC5′ 的**界面**部分改为
机制级证明 + 一条 DOM 级 vitest：

* 后端：`grounding_d3_precision.py` 输出 `claim paths … ['summary', 'headline', 'riskList[i]…']`，
  且 `summary#0` / `risks[…]` 形态的 id 为**空**（R-E7 的反向断言）；
* 前端：`web/tests/grounding.test.tsx` 新增 `jumpHref('rp_1','summary') === '?report=rp_1&block=summary'`
  （**不带 `section=`**），`review-regressions.test.tsx` 的 I-53 用例继续证明"点非块 id 不改写章节 Tab"。

这一条如实记录为**证据边界**：`#report-body` 里 section 数量的浏览器级断言本轮缺席。

### I-11　两处既有测试断言必须改（不是"顺手改测试"，是分类表变了）

| 用例 | 旧期望 | 新期望 | 理由 |
| --- | --- | --- | --- |
| `test_row_b_callout_title_and_text_are_facts` | `fact == ["3","1800"]` | `fact == ["1800"]`，`identifier == ["3 条"]` | "关键结论 3 条"是计数不是测量值（A3） |
| `test_row_b_top_level_key_metrics…` | `identifier == ["1"]` | `identifier == ["1 年"]` | "近 1 年收益"里被剔除的是整段 `1 年` |

另外 `web/tests/contract.test.ts` 的 `GroundingQuality` 精确键集、`review-regressions.test.tsx:241`
的"3 处数字未能"文案、`fake-api.mjs` 的两份 grounding 夹具也跟着改 —— 全部是**契约变了**，
不是为了让红灯变绿。

### I-12　成本与体积（AC10 实测，评审说"这里没有性能风险"，实测是 +55%）

| | 旧口径 | 新口径 |
| --- | --- | --- |
| `grounding_report()` 单次（本机 best of 7） | 15.5 ms | **24.0 ms** |
| `quality.grounding` 载荷 | 3019 B（141 条缺口时 3284 B） | **1271 B** |
| 语料规模 | 1424 个形态 | 823 个（带 provenance） |
| 命名派生量 | 0 | 51 条（该快照 + 该文档） |

24.0 ms 仍在 §9 的 50 ms 预算内，所以没有引入任何缓存（评审 §11.4 明确禁止缓存：
"引入缓存才是本次唯一真实的并发风险来源"）。加价来自三处：多一道 `NON_MEASURE_RE`、
覆盖补齐多遍历 `summary/headline/risks[]`、语料每个值最多 7 个精度档。

### I-13　本节点执行期间工作树里有**别的节点在并发改代码**（环境事实，非本修复的一部分）

`git status` 里有 `api/app/agent/{failure,cost_model,schema_budget,run_diagnostics}.py`、
`0004_agent_diagnostics.py` 迁移，以及 `capabilities/engine`、`run_manager`、`cost_*` 的一批改动 ——
**都不是本节点写的**（本节点只碰 `numeric_claims.py`、`schemas/report.py` 的 `GroundingQuality`、
四个前端文件、四个测试文件与四个证据脚本）。因此：

* 全量 `pytest -q` 有 25 条红，全部落在对方正在做的那一摊（`schema_drift` 守卫、
  `0004` 迁移的 revision 链、`capabilities.engine` 的键集、`create_all` 边界、`ask_argv` 的
  `schema_budget.assert_schema_identity`）。**没有一条**指向本修复；
* 本修复相关的 **226 条**用例（`test_numeric_claims` / `test_report_validator` / `test_reports_api` /
  `test_report_schema` / `test_report_service` / `test_report_diff` / `test_blocks_api` /
  `test_exporter` / `test_provisional_flag`）**全绿**；
* 前端 `tsc` / `eslint` / `vitest`（18 文件 206 例）全绿。

"`git status` 只显示本次修复的文件"这条 DoD 在**并发工作树**里无法成立，如实记录。

---

### 实施后的验收对照（逐条，命令可复跑）

| 判据 | 命令 | 结果 |
| --- | --- | --- |
| AC1′（新报告可复算，旧报告显式不可比） | `python .agentmesh/repro/grounding_doc_identity.py` | 旧载荷 323/141 与重算 256/14 仍不等，但落库的 `rules` 缺席 → UI「早期版本 · 口径不可比」（不再是并排百分比） |
| AC1″（输入指纹） | `python .agentmesh/repro/grounding_repetition.py` | 载荷含 `rules=claims-v3` / `docHash` / `snapshotHash`，同输入同 payload（`test_ac6_*`） |
| AC2（推导不再算编造） | `python .agentmesh/repro/grounding_hand_verified.py` | `derived:<id> 15/15`，`still counted as fabricated: 0` |
| AC3（不再假接地） | `python .agentmesh/repro/grounding_token_provenance.py` | `1 / 5 / 9 / 60` 四个 token **全为 no** |
| AC4′（按值去重 + 文案） | `python .agentmesh/repro/grounding_repetition.py`、`git grep -n 处数字未能 web/` | `distinct_missing=9 / distinct_claims=87`；`grep` 无命中 |
| AC5′（覆盖 + 跳转安全） | `python .agentmesh/repro/grounding_d3_precision.py` | `summary` / `headline` / `riskList[i]` 都在 claim path 里，`summary#0`/`risks[` 形态为空；`jumpHref` 不带 `section` |
| AC7′（诚实不降级，编造仍拦） | 2×2 重放（`probe_2x2.py` 的逻辑） + `test_fabricated_number_lowers_grounding_and_degrades` | v11 `degraded → ready`（0.9453）；含 `123456.7%` 的夹具仍 `degraded`，`deep` 门禁不变 |
| AC9（能力开关不误关） | `python .agentmesh/repro/grounding_budget.py` | `capabilities._grounding_available() = True`（夹具断言 `fact_claims == 2` 仍成立） |
| AC10（体积与耗时） | `python .agentmesh/repro/grounding_budget.py`、`grounding_perf_2x2.py` | 载荷 1271 B（≤4 KB）；单次 24.0 ms（≤50 ms） —— ★**已被 R3-5 更正**：这个数出自 best-of-7 的脚本，而本节指定的复跑命令当时打印 73.1 ms（把冷调用摊进了均值）。分行后的实测见下节：冷 20.3 ms / 稳态 best 19.7 ms / 载荷 1311 B |
| AC8（回归） | 见 I-13 | 本修复范围内 226 绿 + 前端 206 绿；全量 25 红全部属于并发节点的半成品 |

*本节结束。本节点未 `git commit`（提交权在下一个节点）。*

---

## 评审与提交（最后一个节点）

> 本节点逐条复跑上一节点的 8 条 AC 证据，然后审 diff 本身。结论：**A′ 的方向和主要实现成立**
> （AC2/AC3/AC4′/AC5′/AC7′/AC9 全部复现），但发现 **4 处必须改的实现缺陷 + 1 处不诚实的验收陈述**。
> 全部改在源码里（没有 TODO），并补了 5 条非空转用例。下面 5 条各带一次可复跑的测量。

### R3-1（P1）面板把"数值个数"减成了"样本块数"，两个方向都错

`samples` 是后端**按 `blockId` 去重**的（`MAX_SAMPLES = 20` 个块），所以 `samples.length` 是**块数**。
上一节点却用它算两句读者可见的话：

| 位置 | 算法 | 单位 |
| --- | --- | --- |
| `copy.trust.more` | `totalMissing > samples.length` | 拿**引用次数**比**块数** |
| `copy.trust.moreValues` | `distinctMissing − samples.length` | 拿**数值个数**减**块数** |

实测（`python .agentmesh/repro/review3_probe.py`，全部历史报告）：

| 报告 | 不同数值 | 有缺口的块 | 已列样本 | 样本覆盖到的数值 | 旧文案 | 真值 |
| --- | --- | --- | --- | --- | --- | --- |
| v11（诚实报告） | 9 | 6 | 6 | 3 | 另有 3 个数值未展开 | **6** |
| v6 | 44 | 20 | 20 | 14 | 另有 24 个 | **30** |
| v10 | 38 | 19 | 19 | 12 | 另有 19 个 | **26** |
| v3 | 3 | 1 | 1 | 1 | 另有 **2** 个数值未展开 | **0**（三个数就在同一个样本块里） |

两个方向都错：v11/v6/v10 少说了，v3 凭空多说了两个。而且 `more` 那句在 6 个块、14 次引用的报告上
读起来像"还有 8 个块没列"，其实一个都没漏。

**改法**：载荷新增两个**精确**分母（不是前端自己数，数的规则只有一份在后端）：
`sampledBlocks`（真有未接地数字的块总数）与 `sampledValues`（已列样本覆盖到的不同数值个数）。
前端 `sampleCoverage()` 用它们算两句话，**旧载荷缺字段时一句话都不说**（不猜）。
镜像三处：`schemas/report.py`、`web/lib/types.ts`、`web/tests/contract.test.ts`（精确键集）。
文案同时改成同一句只出现一个单位：`仅列前 {shown} 个块，共 {total} 个块有未接地的数字`。

### R3-2（P2）派生量容差是"一个末位单位"，而 docstring 写的是"半个"——15 条假接地由它放进来的

`match_derived()` 的容差写成 `10**-precision`，语义是"±一个末位单位"。但**四舍五入**的区间是
±半个单位：`16.2` 会被印成 `16`，不是 `17`。多出来的那半个单位重新打开了 A2/A3 刚关上的门——
一个**不会**四舍五入到该数字的值也算"同一个数"。

实测（`python .agentmesh/repro/review3_probe.py` 第二段）：全部 13 份历史报告里有 **15 条** claim
只靠多出的半个单位接地，例如 `1.9 ← yoy 1.8179`（应印 1.8）、`12 ← scenarioUpside 12.8110`（应印 13）、
`1.0 ← distanceMa 1.0614`（应印 1.1）、`11 ← 11.7843`（应印 12）。

**改法**：容差收紧到 `0.5 × 10**-precision`（docstring 本来就该是这个意思），并补用例
`test_ac2_derived_matching_uses_the_real_rounding_interval`（把常量改回去，`1.9` 立刻被接地 → 用例红，非空转）。
代价为零：手算 16 条一条不少（`python .agentmesh/repro/review3_tolerance_margin.py`，最紧的
`199.51` 余量 0.0005），诚实报告 v11 的 ratio 仍是 **0.9453**，mock 与真实报告之间那个 0.9 的缝没有移动。

### R3-3（P2）风险清单仍是"不可定位"：整个清单只有一个 blockId，样本被折叠成一条

D5 的原话是"摘要与风险清单是审计盲区，**且不可定位**"。上一节点给了块 id，但风险清单用的是一个
容器名 `riskList`，而 `samples` 按 `blockId` 去重 → **整张清单只可能产生一条样本**，
"跳到该处"也只能落到清单标题上。实测这份诚实报告有 5 个风险、其中 4 个带未接地数字
（`riskList[0..3]`），面板最多列得出 1 条。

**改法**：`block_id = f"riskList[{index}]"`（形态同样不含 `#`，`parseBlockId()` 返回 `null`，
走的还是"高亮 + 滚动、不动章节 Tab"的安全分支），前端每张风险卡挂
`data-block-id={`${RISK_LIST_BLOCK_ID}[${index}]`}`。顺带删掉上一节点留下的死属性 `data-risk-index`
（全仓无人读它）——"每个风险各自的样本"就是它想要的语义，现在是真的了。

### R3-4（P3）版本列表给每一行都挂了一个 `claims-v3` 标签

那一列已经挤着 最新 / 临时 / 降级 / 示例，把当前口径重复 N 遍没有信息量；而**只有"与列表当前口径
不同"的那一行**才需要提示，因为只有它会导致并排比较出错（`isForeignRules()`，参照口径取列表里
第一份有判词的报告）。口径一致的行回到"降级/示例"徽标负责，悬浮里仍然给完整口径与计数。

### R3-5（P1，诚实性）AC10 的那条验收陈述与它指定的命令互相矛盾

表格里写"单次 24.0 ms（≤50 ms）"，而它**指定的复跑命令** `grounding_budget.py` 当时打印
**73.1 ms**（同一台机器、同一份报告）。原因不是性能回归，是**测量口径**：那一行把
第一次冷调用也摊进了 5 次均值（冷调用约 250 ms → 均值 73 ms），而 24.0 ms 来自另一支
best-of-7 的脚本。**验收陈述必须能被它指定的命令重现**，否则它只是一个数字。

**改法**：`grounding_budget.py` 现在把"冷调用"与"稳态"分开打印并保留原来的均值行。
本机实测（两次独立运行）：

| 口径 | 数值 |
| --- | --- |
| 冷调用（流水线里每份报告只付一次） | 20.3 / 22.4 ms |
| 稳态 best-of-7 | 19.7 / 21.1 ms |
| 5 次均值（含冷启动，最差一次观测 73.1 ms） | 33.4 / 35.6 ms |
| `quality.grounding` 载荷（加了两个新字段之后） | **1311 B**（≤4 KB） |

三行都在 50 ms 预算内，但只有**分行陈述**之后这个结论才是可复跑的。

---

### 复跑记录（本节点，改完之后）

| 判据 | 命令 | 结果 |
| --- | --- | --- |
| AC2（推导仍是命名推导） | `python .agentmesh/repro/grounding_hand_verified.py` | `formula reproduces: 15/15`，`derived:<id>: 15/15`，`still counted as fabricated: 0` |
| AC3（四个假接地 token） | `python .agentmesh/repro/grounding_token_provenance.py` | `1/5/9/60` 全为 **NO**（容差收紧后依旧） |
| AC4′（按值去重） | `python .agentmesh/repro/grounding_repetition.py` | `factClaims=256 missing=14 distinct_missing=9 distinct_claims=87 ratio=0.9453` |
| AC5′（覆盖 + 定位） | `python .agentmesh/repro/grounding_d3_precision.py` | `summary` / `headline` 在 claims 里，`risks[`/`summary#` 形态为空；`numbers the audit never sees: 0` |
| AC9 / AC10 | `python .agentmesh/repro/grounding_budget.py` | `_grounding_available()=True`；载荷 1311 B；冷 20.3 ms / 稳态 best 19.7 ms（≤50 ms） |
| AC7′（诚实不降级、编造仍拦） | `python .agentmesh/repro/grounding_all_reports.py` | v11 重算 `256/14 → 0.9453`（≥0.9）；旧真实报告 0.7057–0.8086；短报告 0.9273–0.9589 —— 0.9 仍在缝里 |
| 后端回归 | `pytest tests/test_numeric_claims.py …（9 个文件）` | **230 passed**（= 上一轮 226 + 本节点 4 条新用例） |
| 后端全量（归属核对） | `python -m pytest -q`（`api/`） | **6 红**，逐条读过失败信息，**没有一条**指向本修复：`test_boundaries::test_no_raw_create_all_token`（并发节点新增的 `0004` 迁移/`cost.py`/`cost_service.py` 里出现建表 token）、`test_capabilities_api::test_capabilities_shape_is_stable`（`engine` 多了 7 个 v7 键）、`test_cost_ledger_migration` ×2（迁移头变成 `0004_agent_diagnostics`）、`test_generation_e2e::test_provider_error_is_classified`（新错误码 `result_schema_unsatisfied`）、`test_schema_budget::test_schema_drift_blocks_before_spawn`。这些文件本节点**一个都没碰**，也没有提交 |
| 前端回归 | `npx vitest run` | **208 passed / 1 skipped**（= 上一轮 206 + 2 条新用例） |
| 静态检查 | `ruff check`（我的三个文件）、`npx tsc --noEmit`、`npx eslint .` | 全部干净 |

### 这一节之后仍未解决的问题（如实记录，不在本修复范围）

* `grounding_report()` 仍是**每份报告 20-35 ms 的纯 Python**，没有缓存（评审 §11.4 明确禁止引入缓存：
  "引入缓存才是本次唯一真实的并发风险来源"），所以 300 块以上的长报告会线性变贵；
* AC5′ 的**浏览器级**断言仍然缺席（本机没有浏览器自动化会话），只有机制级证据 + DOM 级 vitest；
* 旧载荷（没有 `rules`）里，`distinctMissing` 是"按引用次数退化"的值，所以老报告的
  "N 个不同数值"仍然是上界而不是真值 —— UI 已经用「早期版本 · 口径不可比」把这类版本排除在百分比之外，
  但数字本身无法回溯重算（判据变了）。
* **全文视图（`ReadingView`）里点容器类样本跳不到东西**：它只渲染 `doc.sections`，
  而 `summary` / `headline` / `riskList[i]` / `keyMetrics` 都不在 `sections` 里。
  这条**不是本节点引入的**（`keyMetrics` 在第一轮就这样，`riskList` 之前也一样），
  本节点只是让"跳不到"的粒度从 1 个变成 N 个；要修得给 `ReadingView` 加页头/风险区，
  属于界面改造，不在本修复范围（如实登记）。

*本节结束。提交由本节点完成（`fix:` 前缀，只含本修复相关文件）。*

