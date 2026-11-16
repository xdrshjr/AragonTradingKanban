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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from . import block_text

Cls = Literal["fact", "projection", "identifier", "derived"]

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
IDENTIFIER_RE = re.compile(
    r"[A-Za-z_]{1,8}(?:[（(]\s*\d{1,4}(?:\s*[，,]\s*\d{1,3})?\s*[)）]|\d{1,4})(?![\d.,])"
    r"|(?<![A-Za-z])[A-Za-z]{2,6}\d{1,4}(?![\d.,])"
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

#: 3 个数字里错 1 个就是 0.67，判 `degraded` 会让所有短报告全变黄条（AC-50 第一条分支）。
MIN_FACT_CLAIMS = 5

#: `quality.grounding.samples` 的硬上限，按 `blockId` 去重后取前 20 个块（AC-48）。
MAX_SAMPLES = 20

#: 指标卡上"没有值"的三种写法——它们不是数字，不该被计成缺失。
_EMPTY_VALUE_TOKENS = frozenset({"n/a", "na", "—", "-", "–", "暂无", ""})


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
    #: 样本不足时的显式标记。**优先于** `ratio >= 0.9`（★V6-P2-8）。
    skipped: str | None = None
    samples: list[dict[str, Any]] = field(default_factory=list)

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
            "projectionClaims": self.projection_claims,
            "derivedClaims": self.derived_claims,
            "identifierClaims": self.identifier_claims,
            "skipped": self.skipped,
            "samples": list(self.samples),
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


def _renderings(number: float) -> set[str]:
    """一个数值在中文报告里所有合理的写法（含 %、万、亿、万亿缩放）。"""
    out: set[str] = set()
    for scaled in (number, number * 100.0, number / 1e4, number / 1e8, number / 1e12):
        for digits in (0, 1, 2, 3, 4):
            out.add(f"{scaled:.{digits}f}")
            out.add(f"{abs(scaled):.{digits}f}")
    return out


def snapshot_number_corpus(snapshot: Mapping[str, Any]) -> set[str]:
    """快照里出现过的每个数字的每一种合理写法。

    接地校验要抓的是**编造**的数字，不是排版选择：原始 `1234567890` 合法地写成
    `"1,234,567,890"`、`"12.3 亿"`、`"+15.2%"`（对 15.2）或 `"55%"`（对 0.55）。
    """
    corpus: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            for rendering in _renderings(float(value)):
                corpus.add(canonical_number(rendering))
        elif isinstance(value, str):
            for token in numbers_in_text(value):
                corpus.add(canonical_number(token))
                try:
                    for rendering in _renderings(float(token.replace(",", ""))):
                        corpus.add(canonical_number(rendering))
                except ValueError:
                    continue
        elif isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)

    add(snapshot)
    return {item for item in corpus if item}


def _value_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
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
    """`NUMBER_RE` 的 token 化（输入已是阶段 A 之后的文本）。"""
    found: list[str] = []
    for match in NUMBER_RE.finditer(text or ""):
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
    return claims


def grounding_report(doc: Mapping[str, Any], snapshot: Mapping[str, Any]) -> GroundingReport:
    """把"这份报告有多少数字能回溯到快照"算成一份**可定位**的报告。

    分母 = `sections[].blocks[]` 的全部 `fact` 路径 ∪ `doc.keyMetrics[]`（写死，见模块
    docstring）。`projection` / `identifier` / `derived` 一个都不进分母。
    """
    corpus = snapshot_number_corpus(snapshot)
    report = GroundingReport()
    sampled_blocks: set[str] = set()

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
        if claim.cls in ("fact", "derived"):
            report.fact_claims += 1
        if claim.canonical and claim.canonical in corpus:
            continue
        if claim.cls in ("fact", "derived"):
            report.fact_missing += 1
        report.total_missing += 1
        if len(report.samples) < MAX_SAMPLES and claim.block_id not in sampled_blocks:
            sampled_blocks.add(claim.block_id)
            report.samples.append(
                {
                    "blockId": claim.block_id,
                    "path": claim.path,
                    "raw": claim.raw,
                    "text": claim.context,
                }
            )

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
    "NUMBER_RE",
    "Claim",
    "GroundingReport",
    "canonical_number",
    "classify",
    "extract_claims",
    "grounding_report",
    "numbers_in_text",
    "snapshot_number_corpus",
    "strip_identifiers",
]
