"""In-process sliding-window rate limiter and the shadow throttle (spec §24.3.7).

Everything here lives in memory and is therefore tied to the single-worker invariant
(`assert_single_worker()`, spec E-8 / R-A7). What must survive a restart — account
lockout counters, sessions, the quota ledger — is in the database; only captcha
challenges and these buckets are allowed to be volatile. Moving to several workers
means moving *both* to the database/Redis first.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass

from ..config import get_settings
from ..errors import RateLimited
from . import clock


@dataclass(frozen=True, slots=True)
class Limit:
    window_sec: float
    limit: int


#: The limiter table of spec §24.3.7. `auth.login` is keyed by IP and `auth.login.user`
#: by username ("两个维度各自计"); `auth.password` is an addition of this implementation
#: (changing a password is a guess-the-current-password oracle for a stolen session).
RATE_LIMITS: dict[str, Limit] = {
    "auth.login": Limit(60, 10),
    "auth.login.user": Limit(60, 10),
    "auth.login.global": Limit(60, 120),
    "auth.register": Limit(600, 5),
    "auth.register.global": Limit(3600, 60),
    "auth.captcha": Limit(60, 30),
    "auth.invite_probe": Limit(600, 5),
    "auth.password": Limit(600, 10),
    # §25.3.3 找回密码：IP / 全站 / 用户名三维；`auth.recover.user` 只在验证码通过后才计数（RV-Y11）。
    "auth.recover": Limit(600, 5),
    "auth.recover.global": Limit(3600, 30),
    "auth.recover.user": Limit(3600, 5),
    # 登录态轮换恢复码：按用户 3 次/分钟（RV-Y17）。
    "auth.recovery_rotate": Limit(60, 3),
    # §25.3.5 管理员导出：按用户 6 次/分钟。
    "admin.export": Limit(60, 6),
    "guest.resolve": Limit(60, 30),
    "guest.ask_cite": Limit(60, 20),
    "guest.export": Limit(60, 20),
}

GLOBAL_KEY = "*"
MAX_KEYS = 20_000


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    retry_after_ms: int = 0


def enabled() -> bool:
    return bool(get_settings().rate_limit_enabled)


class SlidingWindowLimiter:
    """`(scope, key)` → timestamps inside the window. Capped at `MAX_KEYS` buckets."""

    def __init__(self, max_keys: int = MAX_KEYS) -> None:
        self._buckets: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def _live(self, bucket: deque[float], now: float, window: float) -> None:
        horizon = now - window
        while bucket and bucket[0] <= horizon:
            bucket.popleft()

    def _evict(self) -> None:
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)

    def peek(self, scope: str, key: str) -> Decision:
        """Would one more hit be allowed? Does **not** record anything."""
        rule = RATE_LIMITS[scope]
        now = clock.epoch()
        with self._lock:
            bucket = self._buckets.get((scope, key))
            if bucket is None:
                return Decision(True)
            self._live(bucket, now, rule.window_sec)
            if len(bucket) < rule.limit:
                return Decision(True)
            return Decision(False, self._retry_ms(bucket, now, rule))

    def hit(self, scope: str, key: str) -> Decision:
        """Record one event if the budget allows; a denied attempt is **not** recorded."""
        rule = RATE_LIMITS[scope]
        now = clock.epoch()
        with self._lock:
            slot = (scope, key)
            bucket = self._buckets.get(slot)
            if bucket is None:
                bucket = deque()
                self._buckets[slot] = bucket
            else:
                self._buckets.move_to_end(slot)
            self._live(bucket, now, rule.window_sec)
            if len(bucket) >= rule.limit:
                return Decision(False, self._retry_ms(bucket, now, rule))
            bucket.append(now)
            self._evict()
            return Decision(True)

    def record(self, scope: str, key: str) -> None:
        """Record an event unconditionally (used for *failures*, after a `peek`)."""
        rule = RATE_LIMITS[scope]
        now = clock.epoch()
        with self._lock:
            slot = (scope, key)
            bucket = self._buckets.setdefault(slot, deque())
            self._buckets.move_to_end(slot)
            self._live(bucket, now, rule.window_sec)
            bucket.append(now)
            self._evict()

    @staticmethod
    def _retry_ms(bucket: deque[float], now: float, rule: Limit) -> int:
        oldest = bucket[0]
        return max(1, int(math.ceil((oldest + rule.window_sec - now) * 1000)))

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def __len__(self) -> int:
        return len(self._buckets)


_limiter = SlidingWindowLimiter()


def get_limiter() -> SlidingWindowLimiter:
    return _limiter


def check(scope: str, key: str) -> None:
    """Hit `scope` for `key`; raise `429 rate_limited` when the budget is spent."""
    if not enabled():
        return
    decision = _limiter.hit(scope, key)
    if not decision.allowed:
        raise RateLimited(scope=scope, retry_after_ms=decision.retry_after_ms)


def guard(scope: str, key: str) -> None:
    """Raise when `scope`/`key` is already exhausted, **without** consuming budget."""
    if not enabled():
        return
    decision = _limiter.peek(scope, key)
    if not decision.allowed:
        raise RateLimited(scope=scope, retry_after_ms=decision.retry_after_ms)


def record(scope: str, key: str) -> None:
    if enabled():
        _limiter.record(scope, key)


def reset_all() -> None:
    """Test hygiene: forget every bucket and every shadow counter."""
    _limiter.reset()
    shadow_throttle.reset()


# ── shadow throttle (RV-3) ───────────────────────────────────────────────────

LOCK_AFTER = 5
CAPTCHA_AFTER = 3
LOCK_SECONDS = 15 * 60
SHADOW_TTL_SECONDS = 30 * 60
SHADOW_CAPACITY = 20_000


@dataclass(frozen=True, slots=True)
class ThrottleState:
    failed: int
    #: Epoch seconds until which the account is locked, or `None`.
    locked_until: float | None

    @property
    def captcha_required(self) -> bool:
        return self.failed >= CAPTCHA_AFTER

    def locked(self, now: float) -> bool:
        return self.locked_until is not None and self.locked_until > now


class ShadowThrottle:
    """Failure counters for usernames that **do not exist** (RV-3).

    Real accounts keep `failed_logins` / `locked_until` on the `users` row; a name with no
    row has nowhere to put them, so without this the 3rd/5th wrong guess would change the
    response for real accounts only — an enumeration oracle that also breaks AC-170.
    Same thresholds, same response shape, entries expire after 30 minutes of silence.
    Registration never consults it (`username_taken` answers that question itself).
    """

    def __init__(self, capacity: int = SHADOW_CAPACITY, ttl: float = SHADOW_TTL_SECONDS) -> None:
        self._entries: OrderedDict[str, list[float]] = OrderedDict()  # key → [failed, locked_until, touched]
        self._lock = threading.Lock()
        self._capacity = capacity
        self._ttl = ttl

    def _fresh(self, key: str, now: float) -> list[float] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if now - entry[2] > self._ttl:
            del self._entries[key]
            return None
        return entry

    def state(self, username_lc: str) -> ThrottleState:
        now = clock.epoch()
        with self._lock:
            entry = self._fresh(username_lc, now)
            if entry is None:
                return ThrottleState(0, None)
            locked_until = entry[1] if entry[1] > 0 else None
            return ThrottleState(int(entry[0]), locked_until)

    def record_failure(self, username_lc: str) -> ThrottleState:
        now = clock.epoch()
        with self._lock:
            entry = self._fresh(username_lc, now)
            if entry is None:
                entry = [0.0, 0.0, now]
                self._entries[username_lc] = entry
            entry[0] += 1
            entry[2] = now
            if entry[0] >= LOCK_AFTER:
                entry[1] = now + LOCK_SECONDS
            self._entries.move_to_end(username_lc)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return ThrottleState(int(entry[0]), entry[1] if entry[1] > 0 else None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


shadow_throttle = ShadowThrottle()
