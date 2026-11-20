"""One injectable clock for everything that has an expiry (spec §24.7: "注入时钟").

Sessions, lockouts, captcha TTLs, rate-limit windows and quota day keys all read the
time through here, so a test can freeze it and move it forward deterministically
(`frozen_now` in `tests/conftest.py`) instead of sleeping.

Only code **introduced by §24** reads this clock. The pre-existing generation
pipeline keeps using `db.base.utcnow` — freezing this clock must not change how a
run's timestamps are written.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_override: Callable[[], datetime] | None = None


def now() -> datetime:
    """Timezone-aware UTC "now"."""
    if _override is not None:
        moment = _override()
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return datetime.now(UTC)


def epoch() -> float:
    """Seconds since the epoch, from the same (possibly frozen) clock."""
    if _override is not None:
        return now().timestamp()
    return time.time()


def set_clock(fn: Callable[[], datetime] | None) -> None:
    """Install (or, with `None`, remove) the clock override. Test-only."""
    global _override
    _override = fn


def local_zone(tz_name: str) -> timezone | ZoneInfo:
    """`ZoneInfo(tz_name)`; falls back to UTC+8 for Asia/Shanghai, else UTC.

    Windows has no system tz database; `tzdata` normally covers it, but a missing
    package must not turn into a 500 on every quota read.
    """
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        if tz_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), "CST")
        return UTC


def local_day_key(moment: datetime, tz_name: str) -> str:
    """`YYYY-MM-DD` of `moment` in `tz_name` (the quota "natural day", §24.3.8(g))."""
    return moment.astimezone(local_zone(tz_name)).strftime("%Y-%m-%d")


def next_local_midnight(moment: datetime, tz_name: str) -> datetime:
    """The next 00:00 in `tz_name`, as an aware UTC datetime (`resetAt`)."""
    zone = local_zone(tz_name)
    local = moment.astimezone(zone)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(UTC)
