"""数字分类与接地的唯一真源（spec §19.3.2）。

为什么必须分类：一份报告里的数字不是同一种东西。营收是快照事实，同业 PE 是模型的
估计，`RSI(14)` 是方法参数，`2025-03-14` 是坐标。把它们塞进同一个分母，等于用
"有没有预测"去度量"有没有撒谎"。

★v6：本模块**拥有** `NUMBER_RE` / `IDENTIFIER_RE` / `DATE_LIKE_RE`；
`CITATION_RE` 不在这里（它的所有者是 `block_text`，见 §19.3.1 规则 1）。
依赖方向固定：`numeric_claims → block_text`（单向）、`block_dates → numeric_claims`（单向）。

两阶段抽取（★v6 V6-P1-4，本机实测 E-29）：

* **阶段 A（token 级）** 先把标识符与日期片段从文本里剔除 —— 它们**不产生 claim**，
  剔除的次数以 `identifier` 类 claim 记录，只为让 UI 能说"另有 18 处是方法参数/日期"，
  **永不进分母**。不做这一步时 `'2025-03-14'` 会被 `NUMBER_RE` 拆成
  `'2025'` / `'-03'` / `'-14'`，一段 10 个日期的正文足以把诚实报告压成 `degraded`。
* **阶段 B（路径级）** 剩下每个数字按**所在路径**定性。路径判据永远产不出 token 结论，
  所以两张表是两件事，不能写在同一个 if 里。

分类表（唯一裁决，逐行有测试，见 `tests/test_numeric_claims.py`）：

| 阶段 | 来源 | 归类 |
| --- | --- | --- |
| A | `RSI(14)` / `MA20` / `2025-03-14` / `2025 年 3 月` / `2025Q3` / `Q3` / 年份 | 剔除（计 `identifierClaims`） |
| B | `paragraph/heading/bullets/quote/timeline` 文本、`callout.title/text` | `fact` |
| B | `metrics.items[].value`（`n/a`/`—`/`-` 跳过）、`table.rows[].*`、`table.caption` | `fact` |
| B | `chart.title/note` | `fact` |
| B | `metrics.items[].label`、`chart.xAxis.labels`、`doc.keyMetrics[].label` | `identifier` |
| B | `comparables.peers[].pe/pb/growthPct` | `projection`（无条件，无升格通道） |
| B | `scenario.scenarios[].targetPrice/probability` | `projection` |
| B | `scenario.scenarios[].assumptions` | `fact` |
| B | `doc.keyMetrics[].value` | `fact` |
| B | 块上带 `"derived": true` 的文本 | `derived` |

**分母集合（写死）**：`sections[].blocks[]` 的全部 `fact` 路径 ∪ `doc.keyMetrics[]`。
少算一路就会让 `ungroundedCount ≠ grounding.factMissing`（AC-61 专门守这一条）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from . import block_text

Cls = Literal["fact", "projection", "identifier", "derived"]

#: 判定器自身的口径版本（A6）。落进 `quality.grounding.rules`。
#:
#: 为什么必须有一个字符串：判词是**永久落库**的，而判据会变。没有版本号时，
#: 两个不同规则算出来的 `factClaims/factMissing` 在版本列表里被并排展示，
#: 读者无法知道它们不可比（本轮实测：同一份 doc + 同一份 snapshot 在
#: 2026-11 的两代规则下得到 323/141 与 270/103）。跨口径只能"各自陈述"，不能比较。
RULES_VERSION = "claims-v3"

#: 一个数值可以被写成更短的小数（`1,634.9` 对 `1634.88`），但舍入损失一旦超过
#: 这个**相对**量，它已经不是"同一个数"了 —— `0.78 → "1"` 损失 28%，
#: `4.8086 → "5"` 损失 4%。这两条正是 D2 的假接地（AC3）。
_MAX_ROUNDING_LOSS = 0.001

#: 一位有效数字的整数（`1`/`5`/`9`）几乎总是计数、档位或序号，不是测量值：
#: 只有当它落在派生量的 0.1% 以内时才认。这条把"信心度 3/5"这类误判挡在门外（R-E8）。
_SINGLE_DIGIT_RELATIVE = 0.001

#: 一个写法至少保留几位有效数字才算"同一个数"（见 `_precisions`）。
_MIN_SIGNIFICANT_DIGITS = 2

#: 测量的"形状"：`1234.5` / `-3.4` / `1,234`。**唯一定义处**（§19.3.1 规则 1）。
NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)*")

#: 方法参数与指标代号：`RSI(14)` / `BOLL(20,2)` / `MA60` / `PE(TTM)` / `Q3`。
#: 尾部的 `(?![\d.,])` 很关键：没有它，散文里的 `PE 24.1` 会被吞成标识符 `PE 24`，
#: 剩下的 `.1` 不是一个数字，于是"标了价的数字"整条消失（O-6 的漏检方向）。
#:
#: ★评审修正（I-49）：数字**必须**与字母贴合，或落在括号里。第一版写成
#: `[A-Za-z_]{1,8}\s*...`，于是 `"PE 24 倍，PB 4 倍"` 里的 `PE 24` 整段被当成方法参数
#: 剔除 —— `24` 既不进 `factClaims` 也不进任何分母，**彻底消失**。真实投研报告里
#: "PE 24 倍"比"PE 24.1 倍"更常见，而尾部的 `(?![\d.,])` 只保住了带小数的那种。
#: 现在的判据：`MA20`（贴合）与 `RSI(14)`（括号）是参数，`PE 24`（空格分隔）是测量值。
#:
#: ★A3：尾部守卫从 `(?![\d.,])` 放宽到 `(?![\d.])`。真实报告里的引用角标是
#: `[S1,S2]` —— 第一个 `S1` 后面紧跟逗号，旧守卫于是把它**整个放过去**，
#: 剩下的裸 `1` 变成一个必然失配的 fact claim（实测一份真实报告里出现 15 次）。
#: 逗号不再参与守卫的理由：真危险的是"小数点被吞掉"（`PE 24` 吃掉 `24.1` 的 `.1`），
#: 而千分位 `1,234` 从来不会紧贴字母（`MA1,234` 不存在）。
IDENTIFIER_RE = re.compile(
    r"[A-Za-z_]{1,8}(?:[（(]\s*\d{1,4}(?:\s*[，,]\s*\d{1,3})?\s*[)）]|\d{1,4})(?![\d.])"
    r"|(?<![A-Za-z])[A-Za-z]{2,6}\d{1,4}(?![\d.])"
)

#: 日期 / 期间标签的形状。它们描述**坐标**，不是**测量值**。
#: 长形态必须排在短形态之前：`2025-03-14T18:02:30Z` 不能只吃掉日期部分，
#: `2025-03-14` 也不能先被裸年份抢走。
DATE_LIKE_RE = re.compile(
    r"""
    (?<![\d.])
    (?:
        \d{4}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?
                                                        # ISO 时间戳（含时间部分）
      | \d{1,2}:\d{2}(?::\d{2})?                        # 裸时刻 18:02:30（同日线时间戳的残留）
      | \d{4}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}        # 2025-03-14 / 2025/3/14
      | \d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?      # 2025 年 3 月 14 日
      | \d{4}\s*年\s*(?:第)?\s*[一二三四1-4]\s*季度     # 2025 年三季度
      | (?:19|20)\d{2}\s*(?:第)?\s*[一二三四1-4]\s*季度  # 2025 第三季度（无"年"）
      | \d{4}\s*年\s*\d{1,2}\s*月                      # 2025 年 3 月
      | \d{4}\s*[-/]\s*\d{1,2}                         # 2025-03
      | (?:19|20)\d{2}\s*[QH]\s*[1-4]                  # 2025Q3 / 2025H2
      | (?:19|20)\d{2}\s*年(?:度|末|初|内|中|報|报)?    # 2025 年 / 2025 年度
      | FY\s*(?:19|20)?\d{2}                           # FY2025 / FY25
      | \d{1,2}\s*M\s*\d{2}                            # 9M26（前三季度口径）
      | M\s*\d{2}                                      # M26
      | \d{2}\s*[QH]\s*[1-4]                           # 26Q1 / 25H2
      | [QH]\s*[1-4]                                   # Q3 / H1
      | \d{1,2}\s*(?:个月|个季度|个交易日)              # 12 个月 / 3 个季度（期间长度）
      | (?:19|20)\d{2}                                 # 裸年份
    )
    (?![\d])
    """,
    re.VERBOSE,
)

#: 非测量值（★A3：「非数据数字归 identifier」）。这些形态**不是**快照里的量：
#:
#: | 形态 | 例子 | 为什么不是测量值 |
#: | --- | --- | --- |
#: | `N/M` | 信心度 `3/5` | 档位/比例，不是比值计算 |
#: | `N 日 / N 年 / N 周 / N 月` | `60 日低点`、`近 3 年`、`11 月` | 期间长度与月份 |
#: | `9M`（不带年份） | `9M 累计净利` | 期间标签（`9M26` 由 `DATE_LIKE_RE` 接管） |
#: | `N 个 / 条 / 家 / 只 / 次 / 位` | `5 个收盘`、`3 条业务线` | 计数，不是观测值 |
#: | `Npp` / `N 个百分点` | `净利率 -2.4pp` | 单位本身不是快照字段，快照里只有水位 |
#: | `NeM` | `1e8` | 单位换算写法 |
#: | `第 N` | `第 3 位` | 序号 |
#: | `≥/≤/±` 后的数字 | `毛利率 ≥ 90%`、`±2.3%` | 阈值与误差，不是观测值 |
#:
#: 它们**不进分母**，只记成 `identifier`（面板里那句「另有 N 处是方法参数或日期」）。
#: 少了这一步，覆盖补齐（A5）会把「信心度 3/5」里的 `3`、`5` 当成"可追溯"（评审 R-E8 实测）。
#:
#: ★每条数字分支都带 `(?<![\d.])`：没有它，`234.81 位于` 会被 `81 位` 咬掉一段，
#: 小数被劈成 `234` + 一个整数 claim（实测过，AAPL 的 mock 报告因此多出 3 条假缺口）。
NON_MEASURE_RE = re.compile(
    r"""
    (?<![\d.])\d{1,3}\s*/\s*\d{1,3}(?![\d.,])                        # 3/5
      | (?<![\d.])\d{1,4}\s*(?:日|年|周|月)(?![\d.,])                 # 60 日 / 近 3 年 / 11 月
      | (?<![\d.])\d{1,4}\s*(?:个|条|家|只|次)(?![\d.,])              # 5 个收盘 / 3 条业务线
      | (?<![\d.])\d{1,2}\s*M(?![\w])                                # 9M（不带年份的期间标签）
      | (?<![\d.])\d{1,4}(?:\.\d+)?\s*(?:pp|PP|个百分点)              # -2.4pp
      | (?<![\d.])\d+(?:\.\d+)?[eE][+-]?\d+                          # 1e8（单位换算写法）
      | 第\s*\d{1,4}(?![\d.,])                                       # 第 3 位
      | (?<=[≥≤±])\s{0,3}[+\-−]?\d{1,4}(?:\.\d+)?(?![\d.,])          # ≥ +3.0 / ±2.3
    """,
    re.VERBOSE,
)

#: 3 个数字里错 1 个就是 0.67，判 `degraded` 会让所有短报告全变黄条（AC-50 第一条分支）。
MIN_FACT_CLAIMS = 5
#: `quality.grounding.samples` 的硬上限，按 `blockId` 去重后取前 20 个块（AC-48）。
MAX_SAMPLES = 20

#: 指标卡上"没有值"的三种写法——它们不是数字，不该被计成缺失。
_EMPTY_VALUE_TOKENS = frozenset({"n/a", "na", "—", "-", "–", "暂无", ""})


@dataclass(slots=True, frozen=True)
class Provenance:
    """一个语料条目的来源：哪个字段、什么单位、乘过哪个缩放。

    ★A2：判据从"这个形态在不在集合里"变成"这个值是谁"。`path` 是
    `quote.prevClose` 这样的快照字段路径（字符串叶用 `·` 接原 token），
    `unit` 是真实单位（`亿元` / `%` / 空），`scale` 是乘过的缩放倍数。
    """

    path: str = ""
    unit: str = ""
    scale: float = 1.0

    def label(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.path}{unit}"


@dataclass(slots=True, frozen=True)
class DerivedValue:
    """一条**有名字**的派生量（★A1）。

    目录必须有限、带名、带单位：命中时 UI 说的是「推算（`yoy:financials[0].revenue`）」，
    而不是「已核验」。反过来，允许"搜索算式"会把假接地率从 0% 推到 15.74%
    （`grounding_derived_gap.py` 的随机基线），所以这里**只列不搜**。
    """

    id: str
    value: float
    unit: str = ""
    note: str = ""


@dataclass(slots=True, frozen=True)
class Claim:
    """一个"需要核"的数字及其定位。

    ``block_id`` 来自 `block_text.block_id_for(sectionId, index)`——不是自己拼的
    （★V6-P2-1：两套拼接规则就是两套跳转语义）。
    """

    block_id: str
    path: str
    raw: str
    canonical: str
    cls: Cls
    value: float | None = None
    #: 数字所在的原文片段，供 UI 在悬浮里显示上下文（不进 `quality` 落库体积预算之外）。
    context: str = ""


@dataclass(slots=True)
class GroundingReport:
    fact_claims: int = 0
    fact_missing: int = 0
    projection_claims: int = 0
    derived_claims: int = 0
    identifier_claims: int = 0
    #: 全量未接地数（`samples` 只放前 20 条）——R-22 承诺过这个字段。
    total_missing: int = 0
    #: ★A4：按**值**去重的计数。`fact_missing` 是"引用次数"，140 条缺口常常只有 43 个
    #: 不同数值（`-0.25` 一个数被 8 个块引用）。徽标与面板必须能说清这个差别，
    #: 否则读者会把"141 处"读成"141 个错误"。
    distinct_missing: int = 0
    distinct_claims: int = 0
    #: ★R3-1：`samples` 是**按 `blockId` 去重**的，所以 `len(samples)` 是"块数"而不是
    #: "缺失次数"。面板要说的两句话各需要一个块级/值级分母，缺了它们前端只能拿块数
    #: 去近似值数（实测一份报告因此说"另有 3 个数值未展开"，真值是 6；另一份说
    #: "另有 2 个数值未展开"，而那两个数值就在已列出的**同一个块**里）。
    #: `sampled_blocks`：真有未接地数字的块**总数**（截断判据）。
    #: `sampled_values`：已展开样本**覆盖到的不同数值个数**（未展开 = distinct_missing − 它）。
    sampled_blocks: int = 0
    sampled_values: int = 0
    #: 样本不足时的显式标记。**优先于** `ratio >= 0.9`（★V6-P2-8）。
    skipped: str | None = None
    samples: list[dict[str, Any]] = field(default_factory=list)
    #: ★A6：口径版本与输入指纹。跨口径（或缺少 `rules`）时 UI 不给可比百分比。
    rules: str = RULES_VERSION
    doc_hash: str = ""
    snapshot_hash: str = ""

    @property
    def ratio(self) -> float:
        if self.skipped:
            return 1.0
        if self.fact_claims <= 0:
            return 1.0
        return max(0.0, 1.0 - self.fact_missing / self.fact_claims)

    def as_payload(self) -> dict[str, Any]:
        return {
            "factClaims": self.fact_claims,
            "factMissing": self.fact_missing,
            "totalMissing": self.total_missing,
            "distinctMissing": self.distinct_missing,
            "distinctClaims": self.distinct_claims,
            "sampledBlocks": self.sampled_blocks,
            "sampledValues": self.sampled_values,
            "projectionClaims": self.projection_claims,
            "derivedClaims": self.derived_claims,
            "identifierClaims": self.identifier_claims,
            "skipped": self.skipped,
            "samples": list(self.samples),
            "rules": self.rules,
            "docHash": self.doc_hash,
            "snapshotHash": self.snapshot_hash,
        }


# ── 阶段 A：token 级剔除 ─────────────────────────────────────────────────────


def _strip_with_removals(text: str) -> tuple[str, list[str]]:
    """剔除日期与标识符片段，返回 ``(剩余文本, 被剔除的片段)``。

    用等长空格替换而不是删除：偏移不变，后续摘要切片才能对得上原文。

    ★实施修正（见 spec.md「实施过程发现的方案缺陷」I-44）：**日期先于标识符**。
    v6 原文写的是"先 IDENTIFIER_RE 再 DATE_LIKE_RE"，真实报告上会连环出错——
    `26Q1` 里的 `Q1` 先被标识符吃掉，剩下的 `26` 变成一个必然失配的 fact claim
    （实测一份真实报告里 `26` 出现 28 次）；`2026-11-16T18:02:30Z` 同理，
    标识符先啃掉 `of 2026` 之后，日期形状已不存在，`-11` / `-16` 成了两个 claim。
    日期/期间是**更长、更具体**的形状，先剥它才剥得干净；反过来不会损坏标识符
    （`MA20` / `RSI(14)` 都不含日期形状）。
    """
    removed: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        removed.append(match.group(0))
        return " " * len(match.group(0))

    partial = DATE_LIKE_RE.sub(_replace, text or "")
    # ★A3：非测量值的形状（`3/5` / `60 日` / `-2.4pp` / `第 3` / `≥ 90`）先归 identifier，
    # 否则「信心度 3/5」里的两个数字会以 fact 身份进分母（R-E8 实测）。
    partial = NON_MEASURE_RE.sub(_replace, partial)
    cleaned = IDENTIFIER_RE.sub(_replace, partial)
    return cleaned, removed


def strip_identifiers(text: str) -> str:
    """阶段 A —— 把标识符与日期片段从文本里剔除，返回"只剩可核数字"的串。

    顺序很重要：先 `DATE_LIKE_RE`（`2025-03-14` / `2025 年 3 月` / `2025Q3` / `FY2025` /
    `26Q1` / `2026-11-16T18:02:30Z`），再 `IDENTIFIER_RE`（`RSI(14)` / `MA20`），
    最后才走 `NUMBER_RE`。理由见 `_strip_with_removals`（I-44）。
    """
    return _strip_with_removals(text)[0]


def numbers_in_text(text: str) -> list[str]:
    """散文里剩下的"可核数字"（阶段 A 之后）。

    与改造前的 `report_validator._numbers_in_text` 行为一致（`tests/test_report_validator.py`
    依赖它），只是多剔除了日期形态。
    """
    found: list[str] = []
    for match in NUMBER_RE.finditer(strip_identifiers(text)):
        raw = match.group(0)
        if not raw.replace("-", "").replace(",", "").replace(".", ""):
            continue
        found.append(raw)
    return found


# ── 数字规范化与快照语料 ─────────────────────────────────────────────────────


def canonical_number(token: str) -> str:
    """`"1,234.50"` → `"1234.5"`；无法解析时原样返回（不猜）。"""
    cleaned = (token or "").replace(",", "").replace("−", "-").strip()
    if not cleaned or cleaned in ("-", "."):
        return ""
    try:
        number = float(cleaned)
    except ValueError:
        return cleaned
    # 尾部无意义的零是最大的假失配源。
    if number == int(number) and abs(number) < 1e12:
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _renderings(number: float, *, path: str = "", unit: str = "") -> dict[str, Provenance]:
    """一个数值在中文报告里所有**合法的**写法，每个写法都带来源。

    ★A2：这是与 v1 版本最大的区别。旧实现是"每个数值膨胀成 50 个字符串"
    （5 缩放 × 5 位小数 × 正负），于是判定退化成形态匹配：
    `peers[1].changePct = 0.78` 会因为取 0 位小数而"接地"了报告里的 `1`，
    `macd = -8.528` 会因为抹掉符号而"接地"了 `9`，`instrument.symbol = 600519`
    除以 `1e4` 之后"接地"了"60 日低点"。三条都实测过（AC3）。

    现在只保留**真实存在的单位**，并且要求舍入损失不超过 `_MAX_ROUNDING_LOSS`：

    | 缩放 | 何时允许 | 单位 |
    | --- | --- | --- |
    | ×1 | 永远 | 字段自身 |
    | ×100 | 只有 `|v| ≤ 1`（才可能是"小数形式写的百分比"） | % |
    | ÷1e8 | 只有 `|v| ≥ 1e6`（钱的数量级） | 亿元 |
    | ÷1e12 | 只有 `|v| ≥ 1e9` | 万亿元 |

    `÷1e4`（万元）**不在白名单**：它就是 `600519 → 60` 那条假接地的入口。
    """
    out: dict[str, Provenance] = {}
    if isinstance(number, bool) or number is None:
        return out
    magnitude = abs(float(number))
    for scale, scale_unit in _allowed_scales(magnitude):
        scaled = float(number) * scale
        for precision in _precisions(scaled):
            key = canonical_number(f"{scaled:.{precision}f}")
            if key:
                out.setdefault(key, Provenance(path=path, unit=unit or scale_unit, scale=scale))
    return out


def _allowed_scales(magnitude: float) -> tuple[tuple[float, str], ...]:
    """`_renderings` 的缩放白名单（单位来自快照本身，不是猜的）。"""
    scales: list[tuple[float, str]] = [(1.0, "")]
    if 0.0 < magnitude <= 1.0:
        scales.append((100.0, "%"))
    if magnitude >= 1e6:
        scales.append((1e-8, "亿元"))
    if magnitude >= 1e9:
        scales.append((1e-12, "万亿元"))
    return tuple(scales)


def _precisions(scaled: float) -> tuple[int, ...]:
    """该数值可以被写成的小数位数。

    判据是**保留了几位有效数字**，而不是一律用相对损失：真实报告写 `-2.93`
    （对 `-2.933022`）是标准的两位小数排版，相对损失 0.103% —— 只卡 0.1% 会把它判成
    "不是同一个数"；写 `17`（对 `17.234`）同理（2 位有效数字，损失 1.4%）。
    而 D2 的两条假接地（`0.78 → 1`、`4.8086 → 5`）共同点是**只保留了一位有效数字**。

    所以：≥2 位有效数字的写法永远合法；只有 1 位时，相对损失必须 ≤ 0.1%。
    """
    magnitude = abs(scaled)
    allowed: list[int] = []
    for precision in range(0, 7):
        rounded = round(scaled, precision)
        loss = abs(rounded - scaled)
        keeps_digits = _significant_digits(f"{rounded:.{precision}f}") >= _MIN_SIGNIFICANT_DIGITS
        if loss == 0 or keeps_digits or loss <= _MAX_ROUNDING_LOSS * magnitude + 1e-12:
            allowed.append(precision)
    return tuple(allowed)


def snapshot_number_corpus(snapshot: Mapping[str, Any]) -> dict[str, Provenance]:
    """快照里出现过的每个数字的每一种**合法**写法 → 它来自哪个字段。

    返回映射而不是集合：`in` 判据完全兼容（`"1672" in corpus` 仍可用），
    但命中时能说出"这是 `quote.prevClose`"，而不再只是"某个地方有这个形状"。
    """
    corpus: dict[str, Provenance] = {}

    def add(value: Any, path: str) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            for key, provenance in _renderings(float(value), path=path).items():
                corpus.setdefault(key, provenance)
        elif isinstance(value, str):
            for token in numbers_in_text(value):
                cleaned = token.replace(",", "")
                try:
                    number = float(cleaned)
                except ValueError:
                    continue
                for key, provenance in _renderings(number, path=f"{path}·{cleaned}").items():
                    corpus.setdefault(key, provenance)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                add(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                add(item, f"{path}[{index}]")

    add(snapshot, "")
    return corpus


def _value_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None


# ── 派生量目录（★A1）─────────────────────────────────────────────────────────

#: 财报字段里**金额**（元）与**水位**（%）两类。单位决定能不能换算成亿元。
_MONEY_FIELDS = ("revenue", "netProfit", "ocf")
_MARGIN_FIELDS = ("grossMargin", "netMargin")

#: 同比/环比的对照期距。A 股财报是**年内累计**口径，同一「9M」两年之间隔 4 个季度。
_YOY_LAG = 4


def _finite(value: Any) -> float | None:
    """只接受有限数值：`None` / `NaN` / 布尔一律跳过（快照里这三样都真实存在）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


def _pct_change(new: float, old: float) -> float | None:
    """`(新-旧)/旧×100`。**分母为 0 时显式返回 None** —— 除零是这张目录唯一的硬边界。"""
    if old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def _period_year(period: str) -> str | None:
    """`20260930` / `2025Q3` / `FY25` → 财年标签（无财年信息时 None）。"""
    digits = "".join(char for char in (period or "") if char.isdigit())
    if len(digits) >= 4:
        return digits[:4]
    return None


def _has_period(periods: list[Any], index: int) -> bool:
    return 0 <= index < len(periods) and isinstance(periods[index], Mapping)


def _single_quarter(periods: list[Any], index: int) -> dict[str, float] | None:
    """累计期 `index` 对应的**单季金额**（本年累计 − 本年更早累计）。

    只有同一财年的相邻两条才可相减：`26Q3 = 9M26 − H126`（加起来仍在 2026 年），
    而 `26Q1 − FY25` 是一个负数，不是任何季度的营收。这道守卫是"目录不能给编造值背书"
    的具体实现。
    """
    if not _has_period(periods, index) or index + 1 >= len(periods):
        return None
    row, previous = periods[index], periods[index + 1]
    if not isinstance(previous, Mapping):
        return None
    year, previous_year = _period_year(str(row.get("period") or "")), _period_year(
        str(previous.get("period") or "")
    )
    if year is None or year != previous_year:
        return None
    out: dict[str, float] = {}
    for name in _MONEY_FIELDS:
        current, before = _finite(row.get(name)), _finite(previous.get(name))
        if current is not None and before is not None:
            out[name] = current - before
    return out


def derived_values(
    snapshot: Mapping[str, Any],
    doc: Mapping[str, Any] | None = None,
) -> dict[str, DerivedValue]:
    """快照（+ 本文档的两处投影）里所有**有名字**的派生量（★A1）。

    为什么必须有一份目录：投研报告里最常见的数字**不是**快照里的字段，而是
    `(本期-上期)/上期`（同比）、`9M−H1`（单季轧差）、`(目标价-现价)/现价`（隐含空间）。
    旧判据把它们一律判成"编造"，于是模型学到的最优策略是"少写同比"——
    正好抹掉投研里最有用的一块（与 README 里修过的"同业 PE 拉低接地率"同型）。

    目录**有限且不搜索**：`grounding_derived_gap.py` 实测"穷举两操作数"的
    随机假命中率是 15.74%，所以这里宁可漏（漏了只是标"未接地"，不会误判为已核验）。

    每一类的 id 都写死了字段路径或期次，命中时 UI 能说出是哪条公式。
    """

    out: dict[str, DerivedValue] = {}
    source = snapshot if isinstance(snapshot, Mapping) else {}

    def put(identifier: str, value: float | None, unit: str, note: str) -> None:
        if value is None:
            return
        if value != value:  # pragma: no cover - 防御 NaN
            return
        out.setdefault(identifier, DerivedValue(id=identifier, value=value, unit=unit, note=note))

    # ── 财报口径：同比 / 单季轧差 / 单季同比 / 单季净利率 / 百分点变化 ─────────
    financials = source.get("financials")
    periods = financials if isinstance(financials, list) else []
    for index, row in enumerate(periods):
        if not isinstance(row, Mapping):
            continue
        period = str(row.get("period") or f"#{index}")
        # 同一财年才谈得上"累计轧差"：`26Q1` 的上一条累计期是 `FY25`，两者相减是负数，
        # 而那不是一个单季值。少了这道守卫，目录会给一个**编造的**数当裁判。
        same_year_previous = (
            index + 1 < len(periods)
            and isinstance(periods[index + 1], Mapping)
            and _period_year(period) is not None
            and _period_year(period) == _period_year(str(periods[index + 1].get("period") or ""))
        )
        for name in _MONEY_FIELDS + _MARGIN_FIELDS:
            current = _finite(row.get(name))
            if current is None:
                continue
            old_value = _finite(periods[index + _YOY_LAG].get(name)) if _has_period(periods, index + _YOY_LAG) else None
            prev_value = _finite(periods[index + 1].get(name)) if same_year_previous else None
            if old_value is not None:
                put(f"yoy:financials[{index}].{name}", _pct_change(current, old_value), "%", f"{period} 同比")
                if name in _MARGIN_FIELDS:
                    put(f"pp:financials[{index}].{name}", current - old_value, "pp", f"{period} 百分点变化")
            if name in _MONEY_FIELDS and prev_value is not None:
                single = current - prev_value
                put(f"singleQuarter:financials[{index}].{name}", single / 1e8, "亿元", f"{period} 单季轧差")
                # 单季同比：今年的单季 vs 去年同期的单季（两份轧差再取变化率）
                base_single = _single_quarter(periods, index + _YOY_LAG)
                if base_single and base_single.get(name):
                    put(
                        f"singleQuarterYoy:financials[{index}].{name}",
                        _pct_change(single, base_single[name]),
                        "%",
                        f"{period} 单季同比",
                    )
        # 单季净利率 = 单季净利 / 单季营收（两份单季轧差相除，不读累计口径的水位）
        if same_year_previous:
            single = _single_quarter(periods, index)
            if single is not None:
                q_np, q_rev = single.get("netProfit"), single.get("revenue")
                if q_np is not None and q_rev:
                    put(f"singleQuarterMargin:financials[{index}]", q_np / q_rev * 100.0, "%", f"{period} 单季净利率")
        # 降幅/增幅倍数：两个同比的绝对值之比（`4.53 / 1.20 = 3.8`）
        if _has_period(periods, index + _YOY_LAG):
            base = periods[index + _YOY_LAG]
            np_now, np_then = _finite(row.get("netProfit")), _finite(base.get("netProfit"))
            rev_now, rev_then = _finite(row.get("revenue")), _finite(base.get("revenue"))
            np_yoy = _pct_change(np_now, np_then) if np_now is not None and np_then is not None else None
            rev_yoy = _pct_change(rev_now, rev_then) if rev_now is not None and rev_then is not None else None
            if np_yoy is not None and rev_yoy:
                put(
                    f"magnitudeRatio:financials[{index}].netProfit/revenue",
                    abs(np_yoy) / abs(rev_yoy),
                    "倍",
                    f"{period} 降幅倍数",
                )

    # ── 价格锚：`quote.last` 与日线最后一根收盘都可能是"现价" ────────────────
    quote = source.get("quote") if isinstance(source.get("quote"), Mapping) else {}
    last = _finite(quote.get("last")) if isinstance(quote, Mapping) else None
    series = source.get("priceSeries") if isinstance(source.get("priceSeries"), Mapping) else {}
    closes = series.get("close") if isinstance(series, Mapping) else None
    last_close = _finite(closes[-1]) if isinstance(closes, list) and closes else None
    anchors: list[tuple[str, float]] = []
    if last is not None:
        anchors.append(("quote.last", last))
    if last_close is not None and (last is None or abs(last_close - last) > 1e-9):
        anchors.append(("priceSeries.close[-1]", last_close))

    technicals = source.get("technicals") if isinstance(source.get("technicals"), Mapping) else {}
    for anchor_name, anchor in anchors:
        if not anchor:
            continue
        for key in ("ma5", "ma20", "ma60"):
            ma = _finite(technicals.get(key)) if isinstance(technicals, Mapping) else None
            if ma is not None:
                put(f"distanceMa:{key}@{anchor_name}", (ma - anchor) / anchor * 100.0, "%", f"距 {key.upper()}")
        if last is not None and anchor != last:
            put(f"priceGap:quote.last@{anchor_name}", abs(last - anchor) / anchor * 100.0, "%", "现价与最新收盘差")

    # 最近两个交易日的锚（`prevClose` 的日线版）：跨源缺口与当日涨跌幅都用它
    closes_list = closes if isinstance(closes, list) else []
    if len(closes_list) >= 2:
        previous_close = _finite(closes_list[-2])
        if previous_close:
            for anchor_name, anchor in anchors:
                if anchor and abs(anchor - previous_close) > 1e-9:
                    put(
                        f"priceGap:anchor@{anchor_name}",
                        abs(anchor - previous_close) / previous_close * 100.0,
                        "%",
                        "与上一交易日收盘差",
                    )
            put(
                "closeChange:priceSeries.close[-1]",
                (closes_list[-1] - previous_close) / previous_close * 100.0,
                "%",
                "最新交易日涨跌幅",
            )

    # ── 收益/波动比（夏普式）──────────────────────────────────────────────────
    performance = source.get("performance") if isinstance(source.get("performance"), Mapping) else {}
    ret1y = _finite(performance.get("ret1y")) if isinstance(performance, Mapping) else None
    vol = _finite(performance.get("volAnnualized")) if isinstance(performance, Mapping) else None
    if ret1y is not None and vol:
        put("volReturn:ret1y", ret1y / vol, "倍", "近一年收益/年化波动")
        put("volReturn:volAnnualized", vol / abs(ret1y), "倍", "年化波动/近一年收益")

    # ── 本文档的两处投影：目标价隐含空间、情景加权中枢 ────────────────────────
    # 这两条**必然**要读 `doc`：快照里没有 targetPrice（它是模型的判断，属 projection 类），
    # 但"目标价相对现价的隐含空间"是把一个投影与一个事实相除，是可命名的推导。
    ratings = doc.get("rating") if isinstance(doc, Mapping) and isinstance(doc.get("rating"), Mapping) else {}
    target = _finite(ratings.get("targetPrice")) if isinstance(ratings, Mapping) else None
    if target is not None:
        for anchor_name, anchor in anchors:
            if anchor:
                put(
                    f"upside:rating.targetPrice@{anchor_name}",
                    (target - anchor) / anchor * 100.0,
                    "%",
                    "目标价隐含空间",
                )

    weighted = _scenario_center(doc)
    if weighted is not None:
        put("scenarioCenter:probabilityWeighted", weighted, "元", "情景加权中枢")

    # 每个情景的"目标价 vs 现价"也是同一类推导（隐含空间），只是目标价来自文档
    for index, price in enumerate(_scenario_prices(doc)):
        for anchor_name, anchor in anchors:
            if anchor:
                put(
                    f"scenarioUpside:scenarios[{index}]@{anchor_name}",
                    (price - anchor) / anchor * 100.0,
                    "%",
                    f"情景 {index} 隐含空间",
                )
    return out


def _scenario_prices(doc: Mapping[str, Any] | None) -> list[float]:
    """文档里每个情景的目标价（按出现顺序；非数值跳过）。"""
    prices: list[float] = []
    if not isinstance(doc, Mapping):
        return prices
    for section in doc.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        for block in section.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("type") != "scenario":
                continue
            for scenario in block.get("scenarios") or []:
                if not isinstance(scenario, Mapping):
                    continue
                price = _finite(scenario.get("targetPrice"))
                if price is not None:
                    prices.append(price)
    return prices


def _scenario_center(doc: Mapping[str, Any] | None) -> float | None:
    """情景加权中枢：`Σ(目标价 × 概率) / Σ概率`（概率之和不为 1 也照样归一）。"""
    if not isinstance(doc, Mapping):
        return None
    total_weight = 0.0
    total_value = 0.0
    for section in doc.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        for block in section.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("type") != "scenario":
                continue
            for scenario in block.get("scenarios") or []:
                if not isinstance(scenario, Mapping):
                    continue
                price = _finite(scenario.get("targetPrice"))
                weight = _finite(scenario.get("probability"))
                if price is None or weight is None or weight <= 0:
                    continue
                total_weight += weight
                total_value += price * weight
    if not total_weight:
        return None
    return total_value / total_weight


def _claim_precision(raw: str) -> int:
    """claim 原文的小数位数（`1,634.90` → 2，`-2.4` → 1，`1` → 0）。"""
    cleaned = (raw or "").replace(",", "").replace("−", "-").strip()
    if "." not in cleaned:
        return 0
    return len(cleaned.split(".", 1)[1].rstrip())


def _significant_digits(raw: str) -> int:
    """有效数字位数（`1` → 1，`3.8` → 2，`410.06` → 5，`0.25` → 2）。"""
    cleaned = (raw or "").replace(",", "").replace("−", "-").replace("-", "").strip()
    digits = cleaned.replace(".", "").lstrip("0")
    return max(1, len(digits))


def match_derived(claim: Claim, derived: Mapping[str, DerivedValue]) -> DerivedValue | None:
    """claim 是否**就是**某条命名派生量（★A1 的判据）。

    容差 = claim 自身末位的**半个**单位（`3.8` 对 `3.7750`、`-0.81` 对 `-0.8156` 都成立）：
    这正是"四舍五入到这一位"的区间定义 —— `17` 只可能来自 `[16.5, 17.5)`，而 `16.2`
    会被印成 `16`，不是 `17`。

    ★评审修正（R3-2）：v1 落地时写的是 `10**-precision`（**一个**单位，上面那段 docstring
    却一直写着"半个单位"），于是承认了**不会**四舍五入到该数字的值 —— 全部历史报告里
    有 15 条 claim 只靠多出来的那半个单位接地，例如 `1.9 ← yoy 1.8179`（应印 1.8）、
    `12 ← scenarioUpside 12.8110`（应印 13）、`1.0 ← distanceMa 1.0614`（应印 1.1）。
    那正是 A2/A3 要消灭的"假接地"换了个入口回来。收紧到半单位后：
    手算的 16 条推导一条不少（最紧的 `199.51` 余量 0.0005），诚实报告 v11 的 ratio
    仍是 0.9453，mock 与真实报告的分界线（0.9）没有移动。

    再加一道门槛：**一位有效数字**的写法（`1` / `5` / `9`）只有在相对误差
    ≤ 0.1% 时才认 —— 否则"信心度 3/5"的 `3` 会撞上某条恰好等于 3 的派生量（R-E8）。
    """
    value = _value_number(claim.raw)
    if value is None:
        return None
    precision = _claim_precision(claim.raw)
    tolerance = 0.5 * 10.0**-precision + 1e-9
    significant = _significant_digits(claim.raw)
    best: DerivedValue | None = None
    best_gap = tolerance
    for item in derived.values():
        difference = abs(value - item.value)
        if difference > tolerance:
            continue
        if significant <= 1 and item.value and difference > _SINGLE_DIGIT_RELATIVE * abs(item.value):
            continue
        # 命中多条时给**最接近**的那条：读者要知道的是"这个数就是那条公式"，
        # 而不是"它碰巧也接近另一条"。容差内并列则保留目录顺序（确定性）。
        if best is None or difference < best_gap - 1e-12:
            best, best_gap = item, difference
    return best


def provenance_of(
    token: str,
    corpus: Mapping[str, Provenance],
    derived: Mapping[str, DerivedValue] | None = None,
) -> str | None:
    """一个数值原文的来源标签：`fact:quote.prevClose` / `derived:yoy:financials[0].revenue`。

    这是给**证据脚本与人**看的（AC2 的 `checker says` 列），判定本身仍然在
    `grounding_report()` 里只有一处实现。
    """
    canonical = canonical_number(token)
    if canonical and canonical in corpus:
        origin = corpus[canonical]
        return f"fact:{origin.path}" if origin.path else "fact"
    if derived:
        found = match_derived(
            Claim(block_id="", path="", raw=token, canonical=canonical, cls="fact"),
            derived,
        )
        if found is not None:
            return f"derived:{found.id}"
    return None


def _snippet(text: str, raw: str, *, radius: int = 36) -> str:
    """数字周边的原文片段（≤ 100 字），供 UI 悬浮与样本落库。"""
    cleaned = " ".join((text or "").split())
    position = cleaned.find(raw)
    if position < 0:
        return cleaned[: radius * 2]
    start = max(0, position - radius)
    end = min(len(cleaned), position + len(raw) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


# ── 阶段 B：路径级定性 ───────────────────────────────────────────────────────


def _claims_from_text(
    *,
    block_id: str,
    path: str,
    text: Any,
    cls: Cls,
    out: list[Claim],
) -> None:
    """把一个文本字段按 `cls` 定性后追加 claim。

    `fact` / `derived` 走阶段 A（先剔除日期与标识符，并把剔除次数记成 identifier claim）；
    `projection` 与 `identifier` 是**路径**判据，不需要阶段 A —— 它们本来就不进分母，
    剔除反而会把"同业 PE 24.1"这种明确要展示为预测的数字藏起来。
    """
    if text is None:
        return
    raw_text = str(text)
    if not raw_text.strip():
        return

    if cls in ("fact", "derived"):
        cleaned, removed = _strip_with_removals(raw_text)
        for snippet in removed:
            out.append(
                Claim(
                    block_id=block_id,
                    path=path,
                    raw=snippet,
                    canonical="",
                    cls="identifier",
                    context=_snippet(raw_text, snippet),
                )
            )
        for token in numbers_in_text_from_clean(cleaned):
            canonical = canonical_number(token)
            if not canonical:
                continue
            out.append(
                Claim(
                    block_id=block_id,
                    path=path,
                    raw=token,
                    canonical=canonical,
                    cls=cls,
                    context=_snippet(raw_text, token),
                )
            )
        return

    if cls == "identifier":
        # 标签是方法名/刻度：先把 `RSI(14)` / `Q3` / `2025` 这样**整段**当标识符记账
        # （UI 要显示"另有 18 处是方法参数/日期"），剩下的裸数字同样只计不计入分母。
        cleaned, removed = _strip_with_removals(raw_text)
        for snippet in removed:
            out.append(
                Claim(
                    block_id=block_id,
                    path=path,
                    raw=snippet,
                    canonical="",
                    cls="identifier",
                    context=_snippet(raw_text, snippet),
                )
            )
        for token in numbers_in_text_from_clean(cleaned):
            out.append(
                Claim(
                    block_id=block_id,
                    path=path,
                    raw=token,
                    canonical=canonical_number(token),
                    cls="identifier",
                    context=_snippet(raw_text, token),
                )
            )
        return

    for token in numbers_in_text_from_clean(raw_text):
        canonical = canonical_number(token)
        if not canonical:
            continue
        out.append(
            Claim(
                block_id=block_id,
                path=path,
                raw=token,
                canonical=canonical,
                cls=cls,
                value=_value_number(token),
                context=_snippet(raw_text, token),
            )
        )


def numbers_in_text_from_clean(text: str) -> list[str]:
    """`NUMBER_RE` 的 token 化（输入已是阶段 A 之后的文本）。

    ★A2：先把排版用的减号（U+2212 `−`、U+2013 `–`）折成 ASCII `-`。去掉 `abs()`
    之后**符号是身份的一部分**：`最大回撤 −18.2%` 里的 `−18.2` 若被切成 `18.2`，
    快照里的 `-18.2` 就永远匹配不上，诚实报告会平白多一条缺口。
    """
    found: list[str] = []
    normalized = (text or "").replace("\u2212", "-").replace("\u2013", "-")
    for match in NUMBER_RE.finditer(normalized):
        raw = match.group(0)
        if not raw.replace("-", "").replace(",", "").replace(".", ""):
            continue
        found.append(raw)
    return found


def _claims_for_block(block: Mapping[str, Any], block_id: str, prefix: str) -> list[Claim]:
    """一个块的 claim 集合。`prefix` 是人类可读定位的前缀（`valuation.table[2]`）。"""
    out: list[Claim] = []
    if not isinstance(block, Mapping):
        return out
    kind = str(block.get("type") or "")
    # ★v6：`derived: true` 的块，其散文数字是推算值。**计入分母**、另外单独计数
    # （spec 分类表第 16 行）。
    # ★评审记录（I-50）：块 schema（`agent/schemas/report_doc.schema.json`）目前
    # `additionalProperties: false` 且未声明 `derived`，所以这条分支在**今天的合法文档上
    # 恒为 false** —— `derivedClaims` 会是 0。这里刻意保留而不是删除：它不产生任何副作用，
    # 而一旦 schema 补上该字段就能立刻生效；删掉则等于把分类表的第 16 行永久作废。
    text_cls: Cls = "derived" if block.get("derived") is True else "fact"

    def push(field_name: str, value: Any, cls: Cls) -> None:
        _claims_from_text(
            block_id=block_id,
            path=f"{prefix}.{field_name}",
            text=value,
            cls=cls,
            out=out,
        )

    if kind in ("paragraph", "heading"):
        push("text", block.get("text"), text_cls)
    elif kind == "bullets":
        for index, item in enumerate(block.get("items") or []):
            if isinstance(item, Mapping):
                push(f"items[{index}].text", item.get("text"), text_cls)
    elif kind == "callout":
        push("title", block.get("title"), text_cls)
        push("text", block.get("text"), text_cls)
    elif kind == "metrics":
        for index, item in enumerate(block.get("items") or []):
            if not isinstance(item, Mapping):
                continue
            value = item.get("value")
            if str(value or "").strip().lower() in _EMPTY_VALUE_TOKENS:
                continue
            push(f"items[{index}].value", value, text_cls)
            # 标签里的数字是方法名（`RSI(14)`），不是数据。
            push(f"items[{index}].label", item.get("label"), "identifier")
    elif kind == "table":
        for index, row in enumerate(block.get("rows") or []):
            if not isinstance(row, Mapping):
                continue
            for key, value in row.items():
                push(f"rows[{index}].{key}", value, text_cls)
        push("caption", block.get("caption"), text_cls)
    elif kind == "chart":
        chart = block.get("chart") or {}
        if isinstance(chart, Mapping):
            push("chart.title", chart.get("title"), text_cls)
            push("chart.note", chart.get("note"), text_cls)
            for index, label in enumerate((chart.get("xAxis") or {}).get("labels") or []):
                push(f"chart.xAxis.labels[{index}]", label, "identifier")
    elif kind == "quote":
        push("text", block.get("text"), text_cls)
        push("attribution", block.get("attribution"), text_cls)
    elif kind == "timeline":
        for index, event in enumerate(block.get("events") or []):
            if not isinstance(event, Mapping):
                continue
            push(f"events[{index}].title", event.get("title"), text_cls)
            push(f"events[{index}].detail", event.get("detail"), text_cls)
    elif kind == "comparables":
        # 同业估值是模型的估计，快照里不可能有；**无升格通道**（V6-P1-1）。
        for index, peer in enumerate(block.get("peers") or []):
            if not isinstance(peer, Mapping):
                continue
            for key in ("pe", "pb", "growthPct"):
                if peer.get(key) is None:
                    continue
                push(f"peers[{index}].{key}", peer.get(key), "projection")
    elif kind == "scenario":
        for index, scenario in enumerate(block.get("scenarios") or []):
            if not isinstance(scenario, Mapping):
                continue
            for key in ("targetPrice", "probability"):
                if scenario.get(key) is None:
                    continue
                push(f"scenarios[{index}].{key}", scenario.get(key), "projection")
            push(f"scenarios[{index}].assumptions", scenario.get("assumptions"), text_cls)
    return out


def classify(path: str, kind: str | None = None) -> Cls:
    """路径 → 类别。**唯一裁决**，与 `_claims_for_block` 的字段遍历一一对应。"""
    lowered = (path or "").lower()
    if ".comparables" in lowered and any(key in lowered for key in (".pe", ".pb", ".growthpct")):
        return "projection"
    if ".scenario" in lowered and ("targetprice" in lowered or "probability" in lowered):
        return "projection"
    if lowered.endswith(".label") or "xaxis.labels" in lowered:
        return "identifier"
    if kind == "derived":
        return "derived"
    return "fact"


def extract_claims(doc: Mapping[str, Any]) -> list[Claim]:
    """一份报告文档里所有需要核的数字（含标识符与预测，按类别分开）。"""
    claims: list[Claim] = []
    for section in (doc or {}).get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        section_id = str(section.get("id") or "")
        for index, block in enumerate(section.get("blocks") or []):
            if not isinstance(block, Mapping):
                continue
            block_id = block_text.block_id_for(section_id, index)
            prefix = f"{section_id}.{block.get('type')}[{index}]"
            claims.extend(_claims_for_block(block, block_id, prefix))

    # ★v6 V6-P1-5：顶层 `keyMetrics[]` 不在 `sections[]` 内，但它是报告门面的两个大数字，
    # 恰恰最该核。它没有块 id，用固定 id `keyMetrics` 定位（前端已在指标卡上渲染它）。
    for index, metric in enumerate((doc or {}).get("keyMetrics") or []):
        if not isinstance(metric, Mapping):
            continue
        value = metric.get("value")
        if str(value or "").strip().lower() not in _EMPTY_VALUE_TOKENS:
            _claims_from_text(
                block_id="keyMetrics",
                path=f"keyMetrics[{index}].value",
                text=value,
                cls="fact",
                out=claims,
            )
        _claims_from_text(
            block_id="keyMetrics",
            path=f"keyMetrics[{index}].label",
            text=metric.get("label"),
            cls="identifier",
            out=claims,
        )

    # ★A5（评审 R-E7 修订）：**读者第一屏**也在审计范围内。
    #
    # 摘要、标题与风险清单都不在 `sections[]` 里，所以旧实现完全看不见它们 —— 27 个数字
    # 全靠"恰好也在正文块里出现"才被旁证覆盖（`grounding_d3_precision.py`）。通道缺失
    # 意味着**下一份报告可以在这里藏一个编造数字而无人过问**。
    #
    # id 一律用**不含 `#` 的顶层容器名**（`summary` / `headline` / `riskList[i]`）：
    # `parseBlockId()` 会把 `summary#0` 解析成"章节 summary 的第 0 块"，于是
    # `setActiveSection('summary')` 把 `visibleSections` 过滤成空数组——读者点一下样本，
    # 正文整篇消失（`ReportPanel.tsx:234-238` 已经为 `keyMetrics` 记过这个失败模式）。
    # `risks#N` 更糟：`risks` 是真实章节 id，跳转不报错，只是安静地滚到同章第 N 块。
    #
    # ★评审修正（R3-3）：风险清单用 `riskList[i]` 而不是整个清单的 `riskList`。
    # 用容器名时 `samples` 的按块去重会把**整张清单折叠成一条样本**（实测：三个风险
    # 各有一个编造数字，面板只列得出一条，"跳到该处"也只能落到清单标题上）——
    # D5 的原话是"风险清单是审计盲区，**且不可定位**"，折叠等于只修了一半。
    # `riskList[i]` 的形态同样不含 `#`，`parseBlockId()` 返回 `null`，走的还是那条
    # "高亮 + 滚动、不动章节 Tab"的安全分支。
    for field_name in ("summary", "headline"):
        _claims_from_text(
            block_id=field_name,
            path=field_name,
            text=(doc or {}).get(field_name),
            cls="fact",
            out=claims,
        )
    for index, risk in enumerate((doc or {}).get("risks") or []):
        if not isinstance(risk, Mapping):
            continue
        for key in ("severity", "title", "trigger", "mitigant"):
            _claims_from_text(
                block_id=f"riskList[{index}]",
                path=f"riskList[{index}].{key}",
                text=risk.get(key),
                cls="fact",
                out=claims,
            )
    return claims


def _digest(payload: Any) -> str:
    """输入指纹（sha1 前 12 位）。★A6：没有它，"判词能不能复算"永远只能靠猜。"""
    if payload is None:
        return ""
    try:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):  # pragma: no cover - 快照是纯 JSON
        return ""
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def grounding_report(doc: Mapping[str, Any], snapshot: Mapping[str, Any]) -> GroundingReport:
    """把"这份报告有多少数字能回溯到快照"算成一份**可定位**的报告。

    分母 = `sections[].blocks[]` 的全部 `fact` 路径 ∪ `doc.keyMetrics[]` ∪
    `summary` / `headline` / `risksList[]`（★A5 补齐，见 `extract_claims`）。
    `projection` / `identifier` 一个都不进分母。

    判定顺序（★A1/A2）：
    1. **字面命中**快照语料（带 provenance 的严格值匹配）→ 接地，能说出是哪个字段；
    2. **命名派生量目录**命中（同比/单季轧差/距均线/隐含空间…）→ 接地，能说出是哪条公式，
       并计入 `derivedClaims`（读者看到的是「推算」而不是「已核验」）；
    3. 都不命中 → 缺口。
    """
    corpus = snapshot_number_corpus(snapshot)
    derived = derived_values(snapshot, doc)
    report = GroundingReport(
        doc_hash=_digest(doc),
        snapshot_hash=_digest(snapshot),
    )
    sampled_block_ids: set[str] = set()
    distinct_claims: set[str] = set()
    distinct_missing: set[str] = set()
    sampled_value_keys: set[str] = set()
    missing_block_ids: set[str] = set()

    for claim in extract_claims(doc):
        if claim.cls == "identifier":
            report.identifier_claims += 1
            continue
        if claim.cls == "projection":
            report.projection_claims += 1
            continue
        if claim.cls == "derived":
            report.derived_claims += 1
        # ★评审修正（I-50）：spec 分类表第 16 行写的是 derived「**计入分母**但单独计数」，
        # 落地时只加了 `derived_claims`，于是推算值既不进分母也不进 `factMissing` ——
        # 「单独计数」成了「免于核查」。现在它与 fact 走同一张账：进分母，未命中进分母的
        # 缺口，只是另外多记一个 `derivedClaims` 供 UI 标「推算」。
        report.fact_claims += 1
        if claim.canonical:
            distinct_claims.add(claim.canonical)
        if claim.canonical and claim.canonical in corpus:
            continue
        hit = match_derived(claim, derived)
        if hit is not None:
            report.derived_claims += 1
            continue
        report.fact_missing += 1
        if claim.canonical:
            distinct_missing.add(claim.canonical)
        report.total_missing += 1
        missing_block_ids.add(claim.block_id)
        if len(report.samples) < MAX_SAMPLES and claim.block_id not in sampled_block_ids:
            sampled_block_ids.add(claim.block_id)
            if claim.canonical:
                sampled_value_keys.add(claim.canonical)
            report.samples.append(
                {
                    "blockId": claim.block_id,
                    "path": claim.path,
                    "raw": claim.raw,
                    "text": claim.context,
                }
            )

    report.distinct_claims = len(distinct_claims)
    report.distinct_missing = len(distinct_missing)
    report.sampled_blocks = len(missing_block_ids)
    report.sampled_values = len(sampled_value_keys)
    if report.fact_claims < MIN_FACT_CLAIMS:
        report.skipped = "insufficient_sample"
    return report


def iter_texts(doc: Mapping[str, Any]) -> Iterable[str]:
    """文档里所有被分类过的文本（调试与测试用，不参与判定）。"""
    for claim in extract_claims(doc):
        if claim.context:
            yield claim.context


__all__ = [
    "DATE_LIKE_RE",
    "IDENTIFIER_RE",
    "MAX_SAMPLES",
    "MIN_FACT_CLAIMS",
    "NON_MEASURE_RE",
    "NUMBER_RE",
    "RULES_VERSION",
    "Claim",
    "DerivedValue",
    "GroundingReport",
    "Provenance",
    "canonical_number",
    "classify",
    "derived_values",
    "extract_claims",
    "grounding_report",
    "match_derived",
    "numbers_in_text",
    "provenance_of",
    "snapshot_number_corpus",
    "strip_identifiers",
]
