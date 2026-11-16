"""cron 子集解析与推进（AC-31，§18.3.3）。

AC-31 要求：12 个表驱动用例给出正确 `next_run_at`，越界表达式必须被拒（路由层 422），
且全程 UTC、不做夏令时推断。

两条被本文件钉死的语义（都属于"以后会被争论"的决策）：

* **DOM 与 DOW 同时受限时取 OR**（POSIX，不是 Quartz 的 AND）；
* **cron 的 0 = 周日**，而 Python 的 `weekday()` 0 = 周一——两边差一位，
  所以所有"周几"期望值都用 `datetime.weekday()` 现算，不手写常量。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.errors import InvalidCron
from app.services import cron

# ── next_fire：AC-31 的表驱动主表 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("case_id", "expression", "start", "expected"),
    [
        (
            "每 15 分钟",
            "*/15 * * * *",
            datetime(2026, 2, 10, 10, 7, tzinfo=UTC),
            datetime(2026, 2, 10, 10, 15, tzinfo=UTC),
        ),
        (
            "工作日 09:00（跨周末顺延到下周一）",
            "0 9 * * 1-5",
            datetime(2026, 2, 13, 10, 0, tzinfo=UTC),
            datetime(2026, 2, 16, 9, 0, tzinfo=UTC),
        ),
        (
            "下一天零点",
            "0 0 * * *",
            datetime(2026, 2, 10, 10, 7, tzinfo=UTC),
            datetime(2026, 2, 11, 0, 0, tzinfo=UTC),
        ),
        (
            "下个月 1 日 06:30",
            "30 6 1 * *",
            datetime(2026, 2, 10, 10, 7, tzinfo=UTC),
            datetime(2026, 3, 1, 6, 30, tzinfo=UTC),
        ),
        (
            "闰日：2 月内没有下一个 2/29 时跳到 2028",
            "0 0 29 2 *",
            datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
            datetime(2028, 2, 29, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_next_fire_table(case_id: str, expression: str, start: datetime, expected: datetime) -> None:
    """AC-31：表驱动推进。每个用例都是一个"看起来对、写错就错一年"的陷阱。"""
    assert cron.next_fire(expression, start) == expected, case_id


def test_next_fire_weekday_rule_respects_cron_zero_is_sunday() -> None:
    """AC-31：`0 9 * * 1-5` 的期望值用 `datetime.weekday()` 现算，不手写常量。

    cron 的 0 是周日，Python 的 `weekday()` 0 是周一。差一位的实现会在
    周日/周六悄悄触发——所以这里既断言结果，也断言起点确实是周五、落点确实是周一。
    """
    friday = datetime(2026, 2, 13, 10, 0, tzinfo=UTC)
    assert friday.weekday() == 4  # 2026-02-13 是周五
    nxt = cron.next_fire("0 9 * * 1-5", friday)
    assert nxt.weekday() == 0  # 周一
    assert nxt == datetime(2026, 2, 16, 9, 0, tzinfo=UTC)
    # 中间那天（周六）必须被跳过：从周六算起同样落到周一。
    saturday = datetime(2026, 2, 14, 10, 0, tzinfo=UTC)
    assert saturday.weekday() == 5
    assert cron.next_fire("0 9 * * 1-5", saturday) == datetime(2026, 2, 16, 9, 0, tzinfo=UTC)


def test_next_fire_is_strictly_after_the_given_instant() -> None:
    """AC-31：恰好命中触发点的时刻要往后走一分钟，不得原地返回（否则 tick 会死循环）。"""
    exact = datetime(2026, 2, 10, 10, 15, tzinfo=UTC)
    assert cron.next_fire("*/15 * * * *", exact) == datetime(2026, 2, 10, 10, 30, tzinfo=UTC)


def test_next_fire_skips_non_leap_year_for_feb_29() -> None:
    """AC-31：2026-03-01 不能返回自己，也不能返回 2027（非闰年）。"""
    nxt = cron.next_fire("0 0 29 2 *", datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
    assert nxt.year == 2028
    assert nxt.day == 29
    assert nxt.month == 2


# ── parse：取值范围 ─────────────────────────────────────────────────────────


def test_step_on_a_single_value_means_from_there_to_the_end() -> None:
    """AC-31：`5/15` 是 POSIX 的"从 5 起每 15"，不是"只要 5"→ 5,20,35,50。"""
    assert cron.parse("5/15", 0, 59) == frozenset({5, 20, 35, 50})


@pytest.mark.parametrize(
    ("case_id", "field", "bounds"),
    [
        ("分钟 60 越界", "60", (0, 59)),
        ("小时 24 越界", "24", (0, 23)),
        ("日 0 越界", "0", (1, 31)),
        ("月 13 越界", "13", (1, 12)),
        ("周 7 越界", "7", (0, 6)),
        ("区间终点越界", "10-60", (0, 59)),
    ],
)
def test_parse_rejects_out_of_range(case_id: str, field: str, bounds: tuple[int, int]) -> None:
    """AC-31：越界字段抛 `InvalidCron`（路由层落成 422 `invalid_cron`）。"""
    with pytest.raises(InvalidCron):
        cron.parse(field, *bounds)


@pytest.mark.parametrize(
    ("case_id", "field", "bounds"),
    [
        ("起点大于终点", "30-10", (0, 59)),
        ("步长为 0", "*/0", (0, 59)),
        ("步长为负", "*/-3", (0, 59)),
        ("步长不是整数", "*/x", (0, 59)),
        ("非数字 token", "abc", (0, 59)),
        ("区间端点非数字", "a-b", (0, 59)),
        ("空片段", "1,,2", (0, 59)),
        ("空字段", "", (0, 59)),
    ],
)
def test_parse_rejects_malformed(case_id: str, field: str, bounds: tuple[int, int]) -> None:
    """AC-31：`*/0`、`a-b` 起点大于终点、非数字 token 都是 422 而不是 500。"""
    with pytest.raises(InvalidCron):
        cron.parse(field, *bounds)


# ── parse_expression / validate：字段个数 ────────────────────────────────────


@pytest.mark.parametrize(
    ("case_id", "expression"),
    [
        ("只有 4 个字段", "* * * *"),
        ("有 6 个字段", "* * * * * *"),
        ("空串", ""),
        ("全空白", "   "),
        ("越界的分钟字段", "60 * * * *"),
    ],
)
def test_parse_expression_and_validate_reject_bad_field_counts(case_id: str, expression: str) -> None:
    """AC-31：5 个字段是硬契约；`60 * * * *` 这类越界同样在解析期被拒。"""
    with pytest.raises(InvalidCron):
        cron.parse_expression(expression)
    with pytest.raises(InvalidCron):
        cron.validate(expression)


def test_validate_normalises_whitespace() -> None:
    """AC-31：存库前把多余空白折叠掉，避免"同一个表达式两种字符串"。"""
    assert cron.validate("  0   9 * * 1-5 ") == "0 9 * * 1-5"


# ── matches：与 next_fire 一致性 ────────────────────────────────────────────


@pytest.mark.parametrize("expression", ["*/15 * * * *", "0 9 * * 1-5", "30 6 1 * *", "0 0 29 2 *"])
def test_matches_agrees_with_next_fire(expression: str) -> None:
    """AC-31：`matches()` 与 `next_fire()` 必须一致——否则"算出来的点"自己不被承认。"""
    start = datetime(2026, 2, 10, 10, 7, tzinfo=UTC)
    nxt = cron.next_fire(expression, start)
    assert cron.matches(nxt, expression)
    # 触发点前一分钟不应命中（对分钟粒度表达式而言成立）。
    assert not cron.matches(nxt - timedelta(minutes=1), expression)


# ── DOM/DOW：POSIX OR 语义 ──────────────────────────────────────────────────


def test_dom_and_dow_are_ored_when_both_restricted() -> None:
    """AC-31：`0 0 1 * 1` = "每月 1 日 **或** 每周一"（POSIX OR，不是 Quartz AND）。

    从 2026-02-15（周日）起两个月内，必须能分别找到"1 日"和"周一"两类落点：
    2026-02-16 是周一，2026-03-01 是 1 日。取 AND 的实现只会返回 2026-06-01
    （那一天既是 1 日又是周一），在两个月窗口里一个都找不到。
    """
    expression = "0 0 1 * 1"
    start = datetime(2026, 2, 15, 12, 0, tzinfo=UTC)
    window_end = start + timedelta(days=62)  # 约两个月

    fires: list[datetime] = []
    moment = start
    while True:
        moment = cron.next_fire(expression, moment)
        if moment >= window_end:
            break
        fires.append(moment)

    mondays = [m for m in fires if m.weekday() == 0]
    first_of_month = [m for m in fires if m.day == 1]
    # OR：两个集合都必须非空；AND 实现会返回空列表（下一次"既 1 日又周一"是 2026-06-01）。
    assert mondays, "周一的触发点一个都没有：DOM/DOW 不是 OR"
    assert first_of_month, "1 日的触发点一个都没有：DOM/DOW 不是 OR"
    assert fires[0] == datetime(2026, 2, 16, 0, 0, tzinfo=UTC)  # 周一
    assert datetime(2026, 3, 1, 0, 0, tzinfo=UTC) in first_of_month  # 1 日（周日）
    # 每个落点都至少满足其中一条，且没有多余落点。
    assert all(m.weekday() == 0 or m.day == 1 for m in fires)


def test_dom_only_expression_ignores_weekday() -> None:
    """AC-31：只限 DOM 时不再看周几——`0 0 1 * *` 就是每月 1 日。"""
    nxt = cron.next_fire("0 0 1 * *", datetime(2026, 2, 15, 12, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    assert cron.matches(datetime(2026, 3, 1, 0, 0, tzinfo=UTC), "0 0 1 * *")


# ── 时区：全程 UTC ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("expression", ["*/15 * * * *", "0 9 * * 1-5", "30 6 1 * *", "0 0 29 2 *"])
def test_next_fire_is_timezone_aware_utc(expression: str) -> None:
    """AC-31：返回值的 tzinfo 不为 None，且偏移为 0——不做夏令时推断。"""
    nxt = cron.next_fire(expression, datetime(2026, 2, 10, 10, 7, tzinfo=UTC))
    assert nxt.tzinfo is not None
    assert nxt.utcoffset() == timedelta(0)
    assert nxt.tzinfo == UTC


def test_next_fire_converts_other_timezones_to_utc() -> None:
    """AC-31：传入带偏移的时刻先换算到 UTC 再推进（展示层负责本地化，调度器只认 UTC）。"""
    shanghai = timezone(timedelta(hours=8))
    aware_local = datetime(2026, 2, 10, 18, 7, tzinfo=shanghai)  # == 10:07Z
    nxt = cron.next_fire("*/15 * * * *", aware_local)
    assert nxt == datetime(2026, 2, 10, 10, 15, tzinfo=UTC)
    assert nxt.utcoffset() == timedelta(0)


# ── explain / 搜索上限 ──────────────────────────────────────────────────────


def test_explain_mentions_the_fields_it_understands() -> None:
    """AC-31：对话框里的预览句子必须来自同一次解析（解析不了就抛，不静默降级）。"""
    text = cron.explain("0 9 * * 1-5")
    assert "9" in text
    assert "周一" in text
    with pytest.raises(InvalidCron):
        cron.explain("not a cron")


def test_max_search_days_covers_the_worst_legal_case() -> None:
    """AC-31 / §18.3.3：`0 0 29 2 *` 最坏要跨 ~1461 天，`MAX_SEARCH_DAYS` 必须够。"""
    assert cron.MAX_SEARCH_DAYS >= 1461
