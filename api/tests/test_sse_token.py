"""SSE 令牌：签名密钥来源、`open` 与 `enforced` 的行为（spec §24.3.11，M46）。

四处既有用例（`test_ask_api.py` / `test_reports_api.py`）已按"有意的语义迁移"改用
`auth_mode=enforced + session_secret`；这里补的是迁移本身的单元级断言。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.services import settings_service, sse_token
from app.services.sse_token import (
    OPEN_SUFFIX,
    TOKEN_TTL_SEC,
    issue_sse_token,
    load_or_create_secret,
    sign_sse_token,
    signing_secret,
    verify_sse_token,
)


@pytest.fixture()
def enforced(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(get_settings(), "auth_mode", "enforced", raising=False)
    monkeypatch.setattr(get_settings(), "session_secret", "unit-test-secret", raising=False)
    return get_settings()


def test_open_mode_keeps_the_placeholder_token_and_verifies_anything() -> None:
    token, expires_in = issue_sse_token("run-1")
    assert token.endswith(f".{OPEN_SUFFIX}") and expires_in == TOKEN_TTL_SEC
    assert verify_sse_token("run-1", token) is True
    assert verify_sse_token("run-1", "") is True, "open 模式恒真（旧的本地优先行为）"


def test_enforced_mode_issues_a_real_signature_and_verifies_it(enforced) -> None:
    token, expires_in = issue_sse_token("run-1")
    assert expires_in == 60
    assert not token.endswith(OPEN_SUFFIX)
    assert verify_sse_token("run-1", token) is True


def test_enforced_mode_rejects_missing_forged_expired_and_rebound_tokens(enforced) -> None:
    good, _ = issue_sse_token("run-1")
    assert verify_sse_token("run-1", "") is False
    assert verify_sse_token("run-1", "garbage") is False
    assert verify_sse_token("run-1", "x.y") is False
    assert verify_sse_token("run-1", f"{int(time.time()) + 60}.{OPEN_SUFFIX}") is False, "占位令牌在 enforced 下无效"
    assert verify_sse_token("run-2", good) is False, "令牌绑定 id"
    expires, signature = good.split(".")
    assert verify_sse_token("run-1", f"{int(expires) + 1}.{signature}") is False, "到期时间被篡改"
    past = int(time.time()) - 1
    assert verify_sse_token("run-1", f"{past}.{sign_sse_token('run-1', past)}") is False, "已过期"


def test_the_signing_secret_is_the_session_secret_not_the_api_token(enforced, monkeypatch: pytest.MonkeyPatch) -> None:
    token, _ = issue_sse_token("run-1")
    monkeypatch.setattr(get_settings(), "api_token", "now-the-api-token-changes", raising=False)
    assert verify_sse_token("run-1", token) is True, "API_TOKEN 不再参与签名"
    monkeypatch.setattr(get_settings(), "session_secret", "a-different-secret", raising=False)
    assert verify_sse_token("run-1", token) is False
    assert signing_secret() == "a-different-secret"


def test_without_any_configured_secret_a_per_process_random_one_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_mode", "enforced", raising=False)
    monkeypatch.setattr(get_settings(), "session_secret", "", raising=False)
    sse_token.reset_secret_cache()
    secret = signing_secret()
    assert len(secret) >= 32
    token, _ = issue_sse_token("run-1")
    assert verify_sse_token("run-1", token) is True


def test_the_generated_secret_is_persisted_and_stable_across_restarts(engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "session_secret", "", raising=False)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        first = load_or_create_secret(db)
        db.commit()
    assert len(first) == 64
    sse_token.reset_secret_cache()  # "重启"
    with factory() as db:
        second = load_or_create_secret(db)
        assert settings_service.get_str(db, settings_service.KEY_SESSION_SECRET) == first
    assert second == first
    assert signing_secret() == first


def test_an_environment_secret_wins_over_the_stored_one(engine, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(get_settings(), "session_secret", "", raising=False)
    with factory() as db:
        stored = load_or_create_secret(db)
        db.commit()
    monkeypatch.setattr(get_settings(), "session_secret", "from-the-environment", raising=False)
    with factory() as db:
        assert load_or_create_secret(db) == "from-the-environment"
    assert signing_secret() == "from-the-environment" != stored


def test_signature_comparison_survives_non_ascii_garbage(enforced) -> None:
    assert verify_sse_token("run-1", f"{int(time.time()) + 60}.签名") is False
