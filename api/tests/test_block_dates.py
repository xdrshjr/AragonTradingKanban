"""日期区间抽取（spec §19.3.3，AC-53）。

七种模式各至少一个用例，并且刻意覆盖三种"会被人猜错"的边界：闰年 2 月 29 日、
跨年季度 `2024Q4`、以及相对期间的锚点（`asOf=2026-02-10` 的 `近一年` 必须是
`[2025-02-10, 2026-02-10]`，不是"从今天往前推一年"）。
"""

from __future__ import annotations

from datetime import date

from app.services import block_dates as bd

AS_OF = date(2026, 2, 10)


def _one(text: str) -> bd.Interval:
    intervals = bd.extract_intervals(text, AS_OF)
    assert intervals, f"没有抽出区间：{text!r}"
    return intervals[0]


def test_iso_day() -> None:
    interval = _one("2025-03-14 公告了年报")
    assert (interval.kind, interval.start, interval.end) == ("day", date(2025, 3, 14), date(2025, 3, 14))


def test_chinese_day() -> None:
    interval = _one("2025 年 3 月 14 日发布")
    assert (interval.kind, interval.start) == ("day", date(2025, 3, 14))


def test_year_month_covers_the_whole_month() -> None:
    interval = _one("2025年3月 的销量回升")
    assert (interval.kind, interval.start, interval.end) == ("month", date(2025, 3, 1), date(2025, 3, 31))
    iso = _one("2025-03 的销量回升")
    assert (iso.kind, iso.start, iso.end) == ("month", date(2025, 3, 1), date(2025, 3, 31))


def test_quarter_in_all_four_spellings() -> None:
    for text in ("2025Q3", "Q3 2025", "2025 年三季度", "2025 第三季度"):
        interval = _one(text)
        assert (interval.kind, interval.start, interval.end) == (
            "quarter",
            date(2025, 7, 1),
            date(2025, 9, 30),
        ), text


def test_quarter_across_the_year_boundary_is_not_fiscal() -> None:
    """`2024Q4` → `[2024-10-01, 2024-12-31]`：本产品不做财年日历。"""
    interval = _one("2024Q4 毛利率下滑")
    assert (interval.start, interval.end) == (date(2024, 10, 1), date(2024, 12, 31))


def test_half_in_two_spellings() -> None:
    first = _one("2025H1 营收")
    assert (first.kind, first.start, first.end) == ("half", date(2025, 1, 1), date(2025, 6, 30))
    second = _one("25H2 展望")
    assert (second.kind, second.start, second.end) == ("half", date(2025, 7, 1), date(2025, 12, 31))


def test_year_in_three_spellings() -> None:
    for text in ("2025 年 的表现", "FY2025 指引", "2025"):
        interval = _one(text)
        assert (interval.kind, interval.start, interval.end) == (
            "year",
            date(2025, 1, 1),
            date(2025, 12, 31),
        ), text


def test_relative_year_is_anchored_on_as_of() -> None:
    """AC-53：`近一年` + `asOf=2026-02-10` → `[2025-02-10, 2026-02-10]`。"""
    interval = _one("近一年涨幅 12.3%")
    assert (interval.kind, interval.start, interval.end) == (
        "relative",
        date(2025, 2, 10),
        date(2026, 2, 10),
    )


def test_relative_months_quarters_and_ttm() -> None:
    assert _one("过去 6 个月").start == date(2025, 8, 10)
    assert _one("近 3 个季度").start == date(2025, 5, 10)
    assert _one("TTM 口径").start == date(2025, 2, 10)


def test_leap_day_is_parsed_and_kept() -> None:
    interval = _one("2024-02-29 是闰日")
    assert (interval.start, interval.end) == (date(2024, 2, 29), date(2024, 2, 29))


def test_february_month_bounds_respect_leap_years() -> None:
    interval = _one("2024年2月")
    assert interval.end == date(2024, 2, 29)
    assert _one("2025年2月").end == date(2025, 2, 28)


def test_relative_anchor_across_leap_day_does_not_drift() -> None:
    """`asOf` 落在 2 月 29 日时，回退一年不能因为 3 月 1 日不存在而炸。"""
    intervals = bd.extract_intervals("近一年", date(2024, 2, 29))
    assert intervals[0].start == date(2023, 2, 28)


def test_extraction_is_deterministic() -> None:
    """O-8：同一输入两次调用结果必须完全相等（相对期间不许读时钟）。"""
    text = "2025Q3 与近一年、2024H2、2025 年 3 月"
    assert bd.extract_intervals(text, AS_OF) == bd.extract_intervals(text, AS_OF)


def test_intervals_are_sorted_and_capped() -> None:
    text = " ".join(f"20{20 + index}-01-0{(index % 9) + 1}" for index in range(20))
    intervals = bd.extract_intervals(text, AS_OF)
    assert len(intervals) == bd.MAX_INTERVALS
    assert [item.start for item in intervals] == sorted(item.start for item in intervals)


def test_overlaps_counts_calendar_days_inclusively() -> None:
    day = bd.Interval(date(2025, 3, 14), date(2025, 3, 14), "day")
    assert bd.overlaps([day], date(2025, 3, 1), date(2025, 3, 31)) == 1
    quarter = bd.Interval(date(2025, 7, 1), date(2025, 9, 30), "quarter")
    assert bd.overlaps([quarter], date(2025, 8, 1), date(2025, 8, 31)) == 31
    assert bd.overlaps([quarter], date(2026, 1, 1), date(2026, 1, 31)) == 0


def test_year_intervals_are_discounted_when_ranking() -> None:
    """R-23：`2025 年` 会命中几乎所有块，排序必须打折，否则联动条永远"关联 40 段"。"""
    year = [bd.Interval(date(2025, 1, 1), date(2025, 12, 31), "year")]
    day = [bd.Interval(date(2025, 3, 1), date(2025, 3, 31), "month")]
    # 窗口比 month 区间更宽：年度区间重叠天数更多，但加权分更低。
    window = (date(2025, 2, 15), date(2025, 4, 15))
    assert bd.overlaps(year, *window) > bd.overlaps(day, *window)
    assert bd.score_overlaps(year, *window) < bd.score_overlaps(day, *window)


def test_range_limit_is_calendar_based_not_day_based() -> None:
    """★v6 V6-P2-9：5 年含一个闰年是 1826 天，按天数判会自相矛盾。"""
    assert not bd.max_range_years(date(2024, 2, 29), date(2029, 2, 28))
    assert bd.max_range_years(date(2020, 1, 1), date(2025, 1, 2))


def test_parse_as_of_reads_the_document_timestamp() -> None:
    assert bd.parse_as_of("2026-02-10T01:00:00Z") == date(2026, 2, 10)
    assert bd.parse_as_of("") is None
    assert bd.parse_as_of("昨天") is None


def test_block_intervals_reads_a_real_block(sample_report: dict) -> None:
    blocks = [block for section in sample_report["sections"] for block in section["blocks"]]
    hits = [block for block in blocks if bd.block_intervals(block, AS_OF)]
    assert hits, "样例报告里至少有一个块提到了时间"
    assert all(len(bd.block_intervals(block, AS_OF)) <= bd.MAX_INTERVALS for block in blocks)


def test_absolute_dates_still_match_when_as_of_is_missing() -> None:
    """★I-56：`doc.asOf` 缺失只能废掉**相对**期间。

    绝对日期（`2025-03-14` / `2025Q3`）本来就不需要锚点，但旧实现在
    `iter_block_hits` 里写了 `block_intervals(block, as_of) if as_of else []`，于是
    “拿不到锚点”变成了“这段话里没有时间”——一份没有时间锚点
    的报告反查出来永远是空的，而读者只会以为“没提到这段行情”。
    """
    # 相对期间仍然需要锚点：没有锚点就不猜。
    assert bd.extract_intervals("近一年", None) == []
    # 绝对日期照常：
    interval = bd.extract_intervals("2025Q3 营收回升", None)[0]
    assert (interval.kind, interval.start, interval.end) == ("quarter", date(2025, 7, 1), date(2025, 9, 30))
    # 端到端：一份没有 `asOf` 的文档仍能反查出绝对日期段落。
    doc = {
        "sections": [
            {"id": "news", "title": "消息面", "blocks": [{"type": "paragraph", "text": "2025Q3 营收回升"}]}
        ]
    }
    hits = list(bd.iter_block_hits(doc, date(2025, 8, 1), date(2025, 8, 31)))
    assert [hit[0] for hit in hits] == ["news#0"]
