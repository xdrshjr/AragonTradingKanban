"""滑动窗口限速器、影子节流器的单元行为，以及 IP 的三种形态（spec §24.3.7，RV-13）。

限速表本身（每个 scope 的窗口/上限）与经 HTTP 的 429 行为在 `test_auth_login.py` /
`test_auth_register.py` 里；这里钉的是机制：窗口怎么滑、被拒的尝试记不记、容量怎么封顶、键怎么取。
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.errors import RateLimited
from app.security import ipaddr, ratelimit
from app.security.ratelimit import RATE_LIMITS, Limit, SlidingWindowLimiter, ThrottleState


@pytest.fixture()
def limiter(frozen_now) -> SlidingWindowLimiter:  # noqa: ANN001
    return SlidingWindowLimiter()


def test_the_table_matches_the_spec() -> None:
    assert RATE_LIMITS["auth.login"] == Limit(60, 10)
    assert RATE_LIMITS["auth.login.user"] == Limit(60, 10)
    assert RATE_LIMITS["auth.login.global"] == Limit(60, 120)
    assert RATE_LIMITS["auth.register"] == Limit(600, 5)
    assert RATE_LIMITS["auth.register.global"] == Limit(3600, 60)
    assert RATE_LIMITS["auth.captcha"] == Limit(60, 30)
    assert RATE_LIMITS["auth.invite_probe"] == Limit(600, 5)
    assert RATE_LIMITS["guest.resolve"] == Limit(60, 30)
    assert RATE_LIMITS["guest.ask_cite"] == Limit(60, 20)
    assert RATE_LIMITS["guest.export"] == Limit(60, 20)


def test_allows_up_to_the_limit_then_refuses(limiter: SlidingWindowLimiter) -> None:
    decisions = [limiter.hit("auth.login", "1.2.3.4").allowed for _ in range(11)]
    assert decisions == [True] * 10 + [False]


def test_the_window_slides_it_does_not_reset_in_blocks(limiter: SlidingWindowLimiter, frozen_now) -> None:  # noqa: ANN001
    for _ in range(5):
        limiter.hit("auth.login", "k")
        frozen_now.advance(seconds=10)  # 命中发生在 t=0,10,20,30,40
    for _ in range(5):
        assert limiter.hit("auth.login", "k").allowed  # t=50 起又塞满 10 个
    assert not limiter.hit("auth.login", "k").allowed
    frozen_now.advance(seconds=11)  # t=61：t=0 的那个滑出窗口
    assert limiter.hit("auth.login", "k").allowed
    assert not limiter.hit("auth.login", "k").allowed, "只滑出了一个"


def test_a_refused_attempt_is_not_recorded_and_retry_after_counts_down(limiter, frozen_now) -> None:  # noqa: ANN001
    for _ in range(10):
        limiter.hit("auth.login", "k")
    refused = limiter.hit("auth.login", "k")
    assert refused.allowed is False and refused.retry_after_ms == 60_000
    frozen_now.advance(seconds=30)
    assert limiter.hit("auth.login", "k").retry_after_ms == 30_000, "被拒的尝试不延长窗口"
    frozen_now.advance(seconds=30)
    assert limiter.hit("auth.login", "k").allowed


def test_keys_and_scopes_are_independent(limiter: SlidingWindowLimiter) -> None:
    for _ in range(10):
        limiter.hit("auth.login", "a")
    assert not limiter.hit("auth.login", "a").allowed
    assert limiter.hit("auth.login", "b").allowed, "另一个 IP"
    assert limiter.hit("auth.captcha", "a").allowed, "另一个 scope"


def test_peek_does_not_consume_and_record_consumes_unconditionally(limiter: SlidingWindowLimiter) -> None:
    for _ in range(100):
        assert limiter.peek("auth.invite_probe", "k").allowed
    for _ in range(5):
        limiter.record("auth.invite_probe", "k")
    assert not limiter.peek("auth.invite_probe", "k").allowed
    limiter.record("auth.invite_probe", "k")  # `record` 不拒绝，只是继续记
    assert not limiter.hit("auth.invite_probe", "k").allowed


def test_the_bucket_count_is_capped(frozen_now) -> None:  # noqa: ANN001
    small = SlidingWindowLimiter(max_keys=100)
    for index in range(1000):
        small.hit("auth.login", f"ip-{index}")
    assert len(small) == 100


def test_check_raises_a_429_with_a_retry_after_header(frozen_now) -> None:  # noqa: ANN001
    for _ in range(10):
        ratelimit.check("auth.login", "9.9.9.9")
    with pytest.raises(RateLimited) as caught:
        ratelimit.check("auth.login", "9.9.9.9")
    error = caught.value
    assert error.status_code == 429 and error.code == "rate_limited"
    assert error.detail == {"scope": "auth.login", "retryAfterMs": 60_000}
    assert error.headers == {"Retry-After": "60"}


def test_guard_checks_without_consuming(frozen_now) -> None:  # noqa: ANN001
    for _ in range(5):
        ratelimit.guard("auth.invite_probe", "k")
    for _ in range(5):
        ratelimit.record("auth.invite_probe", "k")
    with pytest.raises(RateLimited):
        ratelimit.guard("auth.invite_probe", "k")


def test_rate_limit_enabled_false_switches_everything_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False, raising=False)
    for _ in range(500):
        ratelimit.check("auth.login", "k")
        ratelimit.guard("auth.login", "k")
        ratelimit.record("auth.login", "k")


# ── 影子节流器 ──────────────────────────────────────────────────────────────


def test_shadow_state_follows_the_same_thresholds_as_a_real_account(frozen_now) -> None:  # noqa: ANN001
    shadow = ratelimit.ShadowThrottle()
    states = [shadow.record_failure("ghost") for _ in range(5)]
    assert [s.captcha_required for s in states] == [False, False, True, True, True]
    assert [s.locked(frozen_now().timestamp()) for s in states] == [False, False, False, False, True]
    locked = states[-1]
    assert locked.locked_until is not None and locked.locked_until - frozen_now().timestamp() == 15 * 60


def test_throttle_state_semantics() -> None:
    assert ThrottleState(2, None).captcha_required is False
    assert ThrottleState(3, None).captcha_required is True
    assert ThrottleState(5, 100.0).locked(99.0) and not ThrottleState(5, 100.0).locked(100.0)
    assert not ThrottleState(0, None).locked(0.0)


# ── IP 的三种形态 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "stored", "key", "bucket"),
    [
        ("203.0.113.77", "203.0.113.0/24", "203.0.113.77", "203.0.113.0/24"),
        ("2001:db8:abcd:1234:5678::1", "2001:db8:abcd::/48", "2001:db8:abcd:1234::/64", "2001:db8:abcd::/48"),
        ("[2001:db8::1]", "2001:db8::/48", "2001:db8::/64", "2001:db8::/48"),
        ("testclient", "testclient", "testclient", "testclient"),
        ("unknown", "unknown", "unknown", "unknown"),
    ],
)
def test_ip_shapes(raw: str, stored: str, key: str, bucket: str) -> None:
    """RV-13：存储截断（隐私）；限速键 IPv4 精确 / IPv6 /64；验证码绑定同存储值（允许 /24 内漂移）。"""
    assert ipaddr.stored_prefix(raw) == stored
    assert ipaddr.rate_key(raw) == key
    assert ipaddr.bind_bucket(raw) == bucket


def test_two_hosts_in_one_slash_24_have_different_rate_keys_but_the_same_bucket() -> None:
    assert ipaddr.rate_key("203.0.113.1") != ipaddr.rate_key("203.0.113.2")
    assert ipaddr.bind_bucket("203.0.113.1") == ipaddr.bind_bucket("203.0.113.2")


def test_stored_prefix_always_fits_the_varchar_48_column() -> None:
    assert len(ipaddr.stored_prefix("ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff")) <= 48
    assert len(ipaddr.stored_prefix("x" * 100)) <= 48
