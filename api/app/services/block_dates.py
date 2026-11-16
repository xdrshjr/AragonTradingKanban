"""日期 / 期间区间抽取的唯一真源（spec §19.3.3，M15）。

一张 K 线上的框选要能反查到"报告里哪几段讲的是这段时间"，前提是每个块能说出
**自己谈论的是哪段时间**。这个模块就干这件事，而且刻意只有一家实现：

* 形状表复用 `numeric_claims.DATE_LIKE_RE`（**一处定义、两处使用**：分类侧用它
  抑制坐标，本模块用它取值）。依赖方向 `block_dates → numeric_claims`，无环。
* `as_of` 由调用方传入（`doc.asOf`），**禁止** `date.today()` —— 同一份报告的区间
  反查必须永久可复现（O-8 / AC-53 的"两次调用相等"）。
* 区间一律**闭区间**、按自然日算重叠；**不做财年偏移**：A 股/港股/美股财年不同，
  猜错比不给更糟（"2025 财年 Q1" 只当 `year` 处理）。

区间不落库、不建索引表（§19.11 第 8 条）：输入是"一份已加载的报告 doc"，块数 ~40，
正则 + 日期运算在微秒级；建表只会引入失效同步问题（I-27 的直接教训）。
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from . import block_text
from .numeric_claims import DATE_LIKE_RE

IntervalKind = Literal["day", "month", "quarter", "half", "year", "relative"]

#: 一个块最多返回的区间数（★ 上限而不是截断信号：块级文本本来就很短）。
MAX_INTERVALS = 12

#: R-23：`year` 类区间几乎命中所有块，排序时按 1/12 打折，UI 默认折叠。
YEAR_WEIGHT = 0.08


@dataclass(slots=True, frozen=True)
class Interval:
    start: date
    end: date
    kind: IntervalKind
    #: 命中的原文片段，UI 可以给它加下划线。
    source_text: str = ""

    def as_payload(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "kind": self.kind}


# ── 解析单个候选片段 ─────────────────────────────────────────────────────────

_DAY_ISO_RE = re.compile(r"^(?P<y>\d{4})\s*[-/]\s*(?P<m>\d{1,2})\s*[-/]\s*(?P<d>\d{1,2})$")
_DAY_CN_RE = re.compile(r"^(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?$")
_QUARTER_CN_RE = re.compile(r"^(?P<y>\d{4})\s*年\s*(?:第)?\s*(?P<q>[一二三四1-4])\s*季度$")
_QUARTER_CN_NO_YEAR_RE = re.compile(r"^(?P<y>(?:19|20)\d{2})\s*(?:第)?\s*(?P<q>[一二三四1-4])\s*季度$")
_MONTH_CN_RE = re.compile(r"^(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月$")
_MONTH_ISO_RE = re.compile(r"^(?P<y>\d{4})\s*[-/]\s*(?P<m>\d{1,2})$")
_YEAR_QH_RE = re.compile(r"^(?P<y>\d{4})\s*(?P<qh>[QH])\s*(?P<n>[1-4])$")
_YEAR_CN_RE = re.compile(r"^(?P<y>\d{4})\s*年(?:度|末|初|内|中|报|報)?$")
_FY_RE = re.compile(r"^FY\s*(?P<y>\d{4}|\d{2})$")
_SHORT_QH_RE = re.compile(r"^(?P<y>\d{2})\s*(?P<qh>[QH])\s*(?P<n>[1-4])$")
_BARE_YEAR_RE = re.compile(r"^(?P<y>(?:19|20)\d{2})$")

#: `Q3 2025` / `H2 2024` —— 年份在后的写法，`DATE_LIKE_RE` 会拆成两段，单独先吃掉。
_QH_BEFORE_YEAR_RE = re.compile(r"(?<![\dA-Za-z])(?P<qh>[QH])\s*(?P<n>[1-4])\s*(?P<y>(?:19|20)\d{2})(?![\d])")

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4}

#: 相对期间：`近一年` / `过去 6 个月` / `近 3 个季度` / `TTM`。
_RELATIVE_RE = re.compile(
    r"(?:近|过去|最近|未来)\s*(?P<n>\d{1,2}|[一二三四五六七八九十]{1,2})\s*"
    r"(?P<unit>天|日|周|个季度|季度|个月|月|半年|年)"
    r"|(?<![A-Za-z])TTM(?![A-Za-z])"
)

_CN_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = 3 * (quarter - 1) + 1
    return date(year, start_month, 1), _month_bounds(year, start_month + 2)[1]


def _half_bounds(year: int, half: int) -> tuple[date, date]:
    if half == 1:
        return date(year, 1, 1), date(year, 6, 30)
    return date(year, 7, 1), date(year, 12, 31)


def _add_months(anchor: date, months: int) -> date:
    """按日历回退 N 个月（`近一年` 在 2026-02-10 上得到 2025-02-10，闰年不走样）。"""
    total = anchor.year * 12 + (anchor.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _parse_absolute(span: str) -> tuple[IntervalKind, date, date] | None:
    """把一个绝对日期/期间片段解析成 `(kind, start, end)`；无法定位年份 → None。"""
    text = span.strip()
    if not text:
        return None

    match = _DAY_ISO_RE.match(text) or _DAY_CN_RE.match(text)
    if match:
        try:
            moment = date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        except ValueError:
            return None
        return "day", moment, moment

    match = _QUARTER_CN_RE.match(text) or _QUARTER_CN_NO_YEAR_RE.match(text)
    if match:
        quarter = _CN_DIGITS.get(match.group("q")) or int(match.group("q"))
        start, end = _quarter_bounds(int(match.group("y")), quarter)
        return "quarter", start, end

    match = _MONTH_CN_RE.match(text) or _MONTH_ISO_RE.match(text)
    if match:
        month = int(match.group("m"))
        if not 1 <= month <= 12:
            return None
        start, end = _month_bounds(int(match.group("y")), month)
        return "month", start, end

    match = _YEAR_QH_RE.match(text) or _SHORT_QH_RE.match(text)
    if match:
        year = int(match.group("y"))
        if year < 100:
            year += 2000
        number = int(match.group("n"))
        if match.group("qh") == "Q":
            start, end = _quarter_bounds(year, number)
            return "quarter", start, end
        start, end = _half_bounds(year, number)
        return "half", start, end

    match = _FY_RE.match(text)
    if match:
        year = int(match.group("y"))
        if year < 100:
            year += 2000
        return "year", date(year, 1, 1), date(year, 12, 31)

    match = _YEAR_CN_RE.match(text) or _BARE_YEAR_RE.match(text)
    if match:
        year = int(match.group("y"))
        return "year", date(year, 1, 1), date(year, 12, 31)

    return None


def _relative_interval(match: re.Match[str], as_of: date | None) -> Interval | None:
    if as_of is None:
        # 相对期间没有锚点就不猜（O-8）。
        return None
    if match.group(0).upper() == "TTM":
        return Interval(start=as_of - timedelta(days=365), end=as_of, kind="relative", source_text=match.group(0))

    raw = match.group("n") or ""
    amount = _CN_NUMERALS.get(raw) if not raw.isdigit() else int(raw)
    if not amount:
        return None
    unit = match.group("unit") or ""
    if unit in ("天", "日"):
        start = as_of - timedelta(days=amount)
    elif unit == "周":
        start = as_of - timedelta(days=7 * amount)
    elif unit in ("个月", "月"):
        start = _add_months(as_of, amount)
    elif unit in ("个季度", "季度"):
        start = _add_months(as_of, 3 * amount)
    elif unit == "半年":
        start = _add_months(as_of, 6 * amount)
    else:  # 年
        start = _add_months(as_of, 12 * amount)
    return Interval(start=start, end=as_of, kind="relative", source_text=match.group(0))


# ── 公共 API ────────────────────────────────────────────────────────────────


def extract_intervals(text: str, as_of: date | None) -> list[Interval]:
    """一段文本里所有可定位的时间区间，按 `start` 升序、去重、限 12 条。

    `as_of` 是**参数**而不是 `date.today()`：相对期间（`近一年`）必须锚在报告自己的
    时点上，否则同一份报告昨天与今天反查出的段落不同（O-8 / AC-53）。

    `as_of=None` 只影响相对期间（跳过），绝对日期仍然可用
    ——“拿不到锚点”不等于“这段话里没有时间”。
    """
    source = text or ""
    spans: list[tuple[int, int, Interval]] = []
    consumed: list[tuple[int, int]] = []

    def _free(start: int, end: int) -> bool:
        return all(end <= left or start >= right for left, right in consumed)

    # 1) `Q3 2025` 这类年份在后的写法先吃掉，避免被拆成两个片段。
    for match in _QH_BEFORE_YEAR_RE.finditer(source):
        if not _free(match.start(), match.end()):
            continue
        number = int(match.group("n"))
        year = int(match.group("y"))
        if match.group("qh") == "Q":
            start, end = _quarter_bounds(year, number)
            kind: IntervalKind = "quarter"
        else:
            start, end = _half_bounds(year, number)
            kind = "half"
        spans.append((match.start(), match.end(), Interval(start, end, kind, match.group(0))))
        consumed.append((match.start(), match.end()))

    # 2) 绝对日期/期间：形状表来自 numeric_claims（一处定义，两处使用）。
    for match in DATE_LIKE_RE.finditer(source):
        if not _free(match.start(), match.end()):
            continue
        parsed = _parse_absolute(match.group(0))
        if parsed is None:
            continue
        kind, start, end = parsed
        spans.append((match.start(), match.end(), Interval(start, end, kind, match.group(0))))
        consumed.append((match.start(), match.end()))

    # 3) 相对期间：锚在 as_of 上回溯。
    for match in _RELATIVE_RE.finditer(source):
        if not _free(match.start(), match.end()):
            continue
        interval = _relative_interval(match, as_of)
        if interval is None:
            continue
        spans.append((match.start(), match.end(), interval))
        consumed.append((match.start(), match.end()))

    spans.sort(key=lambda item: (item[2].start, item[0]))
    unique: list[Interval] = []
    seen: set[tuple[date, date, str]] = set()
    for _, _, interval in spans:
        key = (interval.start, interval.end, interval.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(interval)
    return unique[:MAX_INTERVALS]


def block_texts_for_dates(block: Mapping[str, Any]) -> list[str]:
    """一个块里"会提到时间"的文本，按块的类型取。

    复用 `block_text.block_to_text`（单一投影），所以新增块类型不需要改这里。
    """
    if not isinstance(block, Mapping):
        return []
    projected = block_text.block_to_text(dict(block))
    return [projected] if projected else []


def block_intervals(block: Mapping[str, Any], as_of: date | None) -> list[Interval]:
    """一个块的区间集合（多个文本字段合并去重后限 12 条）。"""
    found: list[Interval] = []
    seen: set[tuple[date, date, str]] = set()
    for text in block_texts_for_dates(block):
        for interval in extract_intervals(text, as_of):
            key = (interval.start, interval.end, interval.kind)
            if key in seen:
                continue
            seen.add(key)
            found.append(interval)
    found.sort(key=lambda item: (item.start, item.end))
    return found[:MAX_INTERVALS]


def overlaps(intervals: Sequence[Interval], lo: date, hi: date) -> int:
    """与 `[lo, hi]` 重叠的**自然日天数**（闭区间，跨区间求和）。"""
    if hi < lo:
        return 0
    total = 0
    for interval in intervals:
        start = max(lo, interval.start)
        end = min(hi, interval.end)
        if end >= start:
            total += (end - start).days + 1
    return total


def score_overlaps(intervals: Sequence[Interval], lo: date, hi: date) -> float:
    """排序用的加权重叠分：`year` 类按 1/12 折扣（R-23）。

    `2025 年` 这种年度区间会命中几乎所有块；不打折的话联动条永远显示"关联 40 段"，
    等于什么都没说。折扣只影响**排序**，`overlapDays` 仍然报原始天数。
    """
    score = 0.0
    for interval in intervals:
        start = max(lo, interval.start)
        end = min(hi, interval.end)
        if end < start:
            continue
        days = (end - start).days + 1
        score += days * (YEAR_WEIGHT if interval.kind == "year" else 1.0)
    return round(score, 3)


def parse_as_of(value: str | None) -> date | None:
    """`doc.asOf` (`2026-02-10T01:00:00Z`) → `date`；无法解析返回 None（不猜）。"""
    if not value or not isinstance(value, str):
        return None
    head = value.strip()[:10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


MAX_RANGE_YEARS = 5


def max_range_years(lo: date, hi: date) -> bool:
    """跨度是否超过 5 个**日历年**（★v6 V6-P2-9：按日历判，不按天数判）。

    `1825 天` 在跨闰年会与"5 年"打架：从 2024-02-29 起的五年是 1826 天。
    """
    try:
        limit = lo.replace(year=lo.year + MAX_RANGE_YEARS)
    except ValueError:  # 2 月 29 日 + 5 年不是闰年
        limit = lo.replace(year=lo.year + MAX_RANGE_YEARS, day=28)
    return hi > limit


def iter_block_hits(
    doc: Mapping[str, Any],
    lo: date,
    hi: date,
) -> Iterable[tuple[str, str, str, str, int, str, str]]:
    """`[(blockId, sectionId, sectionTitle, kind, overlapDays, matchKind, text)]`，按权重降序。

    只做纯计算，不碰 HTTP、不碰数据库：端点只负责参数校验与截断。
    """
    as_of = parse_as_of(str((doc or {}).get("asOf") or ""))
    titles = block_text.section_titles(dict(doc))
    rows: list[tuple[float, str, str, str, int, str, str]] = []
    for block_id, section_id, block in block_text.block_ids(dict(doc)):
        # ★评审修正（I-56）：`as_of` 缺失时不能把**所有**区间一起丢掉。
        # 绝对日期（2025-03-14 / 2025Q3）本来就不需要锚点。
        intervals = block_intervals(block, as_of)
        if not intervals:
            continue
        days = overlaps(intervals, lo, hi)
        if days <= 0:
            continue
        weighted = score_overlaps(intervals, lo, hi)
        text = block_text.block_to_text(block)
        kind = str(block.get("type") or "")
        matched = _dominant_kind(intervals)
        rows.append((weighted, section_id, block_id, kind, days, matched, text[:160]))

    rows.sort(key=lambda item: (-item[0], item[1], item[2]))
    for _, section_id, block_id, block_kind, days, matched, text in rows:
        yield block_id, section_id, titles.get(section_id, ""), block_kind, days, matched, text


def _dominant_kind(intervals: Sequence[Interval]) -> str:
    """重叠段的"代表类型"：优先精确度高的（day > month > quarter > half > year > relative）。"""
    order = ("day", "month", "quarter", "half", "year", "relative")
    present = {interval.kind for interval in intervals}
    for kind in order:
        if kind in present:
            return kind
    return "relative"


__all__ = [
    "MAX_INTERVALS",
    "MAX_RANGE_YEARS",
    "YEAR_WEIGHT",
    "Interval",
    "block_intervals",
    "block_texts_for_dates",
    "extract_intervals",
    "iter_block_hits",
    "max_range_years",
    "overlaps",
    "parse_as_of",
    "score_overlaps",
]
