"""A 5-field cron subset — no third-party dependency (§18.3.3, AC-31).

Supported field syntax: `*`, `n`, `a-b`, `*/n`, `a-b/n`, `a,b,c`.

Two deliberate decisions, both written down because they are the kind of thing a
maintainer will otherwise argue about later:

* **DOM and DOW are OR-ed when both are restricted** — POSIX semantics, not
  Quartz's AND. `0 0 1 * 1` means "the 1st OR every Monday".
* **UTC only, no DST inference.** The display layer converts; the scheduler never
  guesses a local time zone (§7.1: every timestamp is ISO-8601 UTC).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..errors import InvalidCron

#: (low, high) per field: minute, hour, day-of-month, month, day-of-week.
FIELD_BOUNDS: tuple[tuple[int, int], ...] = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))

FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")

#: How far ahead `next_fire` will look. 4 years covers the worst legal case,
#: `0 0 29 2 *`, with room to spare.
MAX_SEARCH_DAYS = 1500


def parse(field: str, lo: int, hi: int) -> frozenset[int]:
    """Parse one field into the exact set of allowed values."""
    if not field:
        raise InvalidCron("cron 字段为空")
    values: set[int] = set()
    for chunk in field.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise InvalidCron(f"cron 字段「{field}」存在空片段")
        step = 1
        has_step = False
        if "/" in chunk:
            base, _, step_raw = chunk.partition("/")
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise InvalidCron(f"步长「{step_raw}」不是整数") from exc
            if step <= 0:
                raise InvalidCron(f"步长必须为正整数（收到 {step}）")
            has_step = True
            chunk = base or "*"
        if chunk == "*":
            start, end = lo, hi
        elif "-" in chunk:
            start_raw, _, end_raw = chunk.partition("-")
            try:
                start, end = int(start_raw), int(end_raw)
            except ValueError as exc:
                raise InvalidCron(f"区间「{chunk}」不是整数区间") from exc
        else:
            try:
                start = end = int(chunk)
            except ValueError as exc:
                raise InvalidCron(f"「{chunk}」不是整数") from exc
            if has_step:
                # `5/15` means "from 5, every 15" — POSIX, not "the value 5 only".
                end = hi
        if start > end:
            raise InvalidCron(f"区间「{chunk}」起点大于终点")
        if start < lo or end > hi:
            raise InvalidCron(f"「{chunk}」超出取值范围 {lo}-{hi}")
        values.update(range(start, end + 1, step))
    if not values:
        raise InvalidCron(f"「{field}」没有解析出任何值")
    return frozenset(values)


def parse_expression(expression: str) -> tuple[frozenset[int], ...]:
    """`"0 9 * * 1-5"` → five value sets. Raises `InvalidCron` (→ 422)."""
    fields = (expression or "").split()
    if len(fields) != 5:
        raise InvalidCron(f"cron 需要 5 个字段，收到 {len(fields)} 个")
    return tuple(
        parse(field, bounds[0], bounds[1]) for field, bounds in zip(fields, FIELD_BOUNDS, strict=True)
    )


def validate(expression: str) -> str:
    """Validate and return the normalised expression (whitespace-collapsed)."""
    parse_expression(expression)
    return " ".join((expression or "").split())


def _day_matches(parsed: tuple[frozenset[int], ...], moment: datetime) -> bool:
    minutes, hours, doms, months, dows = parsed
    if moment.month not in months:
        return False
    dom_restricted = doms != frozenset(range(1, 32))
    # cron: 0 = Sunday, Python: 0 = Monday.
    weekday = (moment.weekday() + 1) % 7
    dow_restricted = dows != frozenset(range(0, 7))
    dom_ok = moment.day in doms
    dow_ok = weekday in dows
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok  # POSIX OR
    if dom_restricted:
        return dom_ok
    if dow_restricted:
        return dow_ok
    return True


def matches(moment: datetime, expression: str) -> bool:
    parsed = parse_expression(expression)
    local = moment.astimezone(UTC)
    return local.minute in parsed[0] and local.hour in parsed[1] and _day_matches(parsed, local)


def next_fire(expression: str, after: datetime | None = None) -> datetime:
    """The first UTC instant strictly later than `after` that matches.

    Two-level walk (day first, then hour/minute) keeps `0 0 29 2 *` cheap: ~1500
    date checks instead of two million minute checks.
    """
    parsed = parse_expression(expression)
    minutes, hours, _doms, _months, _dows = parsed
    moment = (after or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    moment += timedelta(minutes=1)
    for day_offset in range(MAX_SEARCH_DAYS):
        day = moment + timedelta(days=day_offset)
        if not _day_matches(parsed, day):
            continue
        for hour in sorted(hours):
            if day_offset == 0 and hour < moment.hour:
                continue
            for minute in sorted(minutes):
                if day_offset == 0 and hour == moment.hour and minute < moment.minute:
                    continue
                return day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    raise InvalidCron(f"cron「{expression}」在 {MAX_SEARCH_DAYS} 天内没有触发点")


def explain(expression: str) -> str:
    """A short human sentence — used by the schedule dialog's preview."""
    parsed = parse_expression(expression)
    minutes, hours, doms, months, dows = parsed
    parts = []
    parts.append("每小时" if len(hours) == 24 else f"每天 {','.join(str(h) for h in sorted(hours))} 时")
    if len(minutes) <= 6:
        parts.append(f"第 {','.join(str(m) for m in sorted(minutes))} 分")
    else:
        parts.append(f"共 {len(minutes)} 个分钟点")
    if doms != frozenset(range(1, 32)):
        parts.append(f"每月 {','.join(str(d) for d in sorted(doms))} 日")
    if dows != frozenset(range(0, 7)):
        names = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
        parts.append("、".join(names[d] for d in sorted(dows)))
    return " · ".join(parts)


__all__ = [
    "FIELD_BOUNDS",
    "MAX_SEARCH_DAYS",
    "explain",
    "matches",
    "next_fire",
    "parse",
    "parse_expression",
    "validate",
]
