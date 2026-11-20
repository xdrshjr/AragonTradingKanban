"""注册与验证码（spec §24.3.6；AC-154、AC-165–168、AC-171/172、AC-177 部分）。

验证码答案取自测试专用的明文侧表（`solve_captcha` 夹具），不去 OCR 图片；
`frozen_now` 冻结 §24 时钟，让"过期 / 1.2 秒下限 / 限速窗口"都是确定性的而不是 `sleep`。
"""

from __future__ import annotations

import base64
import io
import sys
import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, InviteCode, QuotaLedger, User, UserSession
from app.services import captcha as captcha_module
from app.services import invite_service, settings_service

URL = "/api/v1/auth"
GOOD_PASSWORD = "Aragon-Test-9x"


@pytest.fixture()
def frozen(frozen_now):  # noqa: ANN001, ANN201
    return frozen_now


def register_body(username: str = "alice", **extra) -> dict:  # noqa: ANN003
    return {"username": username, "password": GOOD_PASSWORD, **extra}


def register(client, solve_captcha, username: str = "alice", **extra):  # noqa: ANN001, ANN201
    challenge_id, answer = solve_captcha(client, "register")
    body = register_body(username, captchaId=challenge_id, captchaAnswer=answer, **extra)
    return client.post(f"{URL}/register", json=body)


def error_of(response) -> dict:  # noqa: ANN001
    return response.json()["error"]


# ── 成功路径与 Cookie（AC-154 / AC-177）────────────────────────────────────


def test_registration_signs_the_new_user_in(auth_client, solve_captcha, frozen) -> None:
    response = register(auth_client, solve_captcha)
    assert response.status_code == 201, response.text
    me = response.json()
    assert me["authenticated"] is True
    assert me["user"]["username"] == "alice"
    assert me["user"]["role"] == "user"
    assert me["user"]["mustChangePassword"] is False
    assert "report.generate" in me["permissions"] and "report.manage_any" not in me["permissions"]

    # 已登录：下一次 /auth/me 凭 Cookie 认出此人。
    assert auth_client.get(f"{URL}/me").json()["user"]["username"] == "alice"


def test_cookie_attributes_for_a_browser_session(auth_client, solve_captcha, frozen) -> None:
    """AC-154：`HttpOnly; SameSite=Lax; Path=/`，`remember=false` 不带 `Max-Age`；http 下不带 `Secure`。"""
    response = register(auth_client, solve_captcha)
    header = response.headers["set-cookie"]
    lowered = header.lower()
    assert header.startswith("aragon_sid=")
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert "max-age" not in lowered
    assert "secure" not in lowered.replace("samesite", "")


def test_cookie_is_secure_behind_a_trusted_https_proxy(
    auth_client, solve_captcha, frozen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "trust_proxy", True, raising=False)
    challenge_id, answer = solve_captcha(auth_client, "register")
    response = auth_client.post(
        f"{URL}/register",
        json=register_body(captchaId=challenge_id, captchaAnswer=answer),
        headers={"X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 201
    assert "secure" in response.headers["set-cookie"].lower().replace("samesite", "")


def test_a_registration_can_never_produce_an_admin(auth_client, solve_captcha, frozen) -> None:
    challenge_id, answer = solve_captcha(auth_client, "register")
    response = auth_client.post(
        f"{URL}/register",
        json={**register_body(captchaId=challenge_id, captchaAnswer=answer), "role": "admin", "status": "root"},
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "user"


# ── 验证码（AC-165 / 166 / 167）───────────────────────────────────────────


def test_captcha_image_contract(auth_client) -> None:
    """AC-167：PNG、no-store、5 位、字符集无 `0 O 1 I L`、答案大小写不敏感。"""
    from PIL import Image

    response = auth_client.get(f"{URL}/captcha", params={"purpose": "register"})
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    body = response.json()
    assert (body["width"], body["height"], body["expiresIn"]) == (160, 56, 300)
    assert body["image"].startswith("data:image/png;base64,")
    picture = Image.open(io.BytesIO(base64.b64decode(body["image"].split(",", 1)[1])))
    assert picture.format == "PNG"
    assert picture.size == (320, 112), "@2x 出图，前端按 160×56 CSS 像素渲染"

    answer = captcha_module.get_captcha_provider().debug_answer(body["challengeId"])
    assert answer is not None and len(answer) == 5
    assert not (set(answer) & set("0O1IL"))
    assert set(answer) <= set(captcha_module.ALPHABET)


def test_two_hundred_challenges_have_unique_ids() -> None:
    provider = captcha_module.get_captcha_provider()
    ids = {provider.issue("register", "203.0.113.5").challenge_id for _ in range(200)}
    assert len(ids) == 200


def test_the_answer_is_case_insensitive(auth_client, solve_captcha, frozen) -> None:
    challenge_id, answer = solve_captcha(auth_client, "register")
    response = auth_client.post(
        f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer.lower())
    )
    assert response.status_code == 201


def test_the_answer_is_not_stored_in_clear() -> None:
    provider = captcha_module.get_captcha_provider()
    provider.set_debug_answers(False)  # 测试侧表关掉后，仓库里只剩哈希
    issued = provider.issue("register", "203.0.113.5")
    entry = provider._entries[issued.challenge_id]  # noqa: SLF001
    assert len(entry.answer_hash) == 64
    assert not hasattr(entry, "answer")


def test_every_bad_captcha_is_the_same_422(auth_client, solve_captcha, frozen) -> None:
    """AC-165：缺失 / 错误 / 过期 / 重放 / 换 IP 桶，**全部** `422 captcha_invalid`。"""
    # 缺失
    assert error_of(auth_client.post(f"{URL}/register", json=register_body()))["code"] == "captcha_invalid"
    # 错误
    challenge_id, answer = solve_captcha(auth_client, "register")
    wrong = "AAAAA" if answer != "AAAAA" else "BBBBB"
    bad = auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=wrong))
    assert (bad.status_code, error_of(bad)["code"]) == (422, "captcha_invalid")
    # 过期（TTL 300 s）
    challenge_id, answer = solve_captcha(auth_client, "register")
    frozen.advance(seconds=301)
    expired = auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert error_of(expired)["code"] == "captcha_invalid"
    # 重放：正确一次后立即作废
    challenge_id, answer = solve_captcha(auth_client, "register")
    first = auth_client.post(
        f"{URL}/register", json=register_body("replay_a", captchaId=challenge_id, captchaAnswer=answer)
    )
    assert first.status_code == 201
    auth_client.post(f"{URL}/logout")
    replay = auth_client.post(
        f"{URL}/register", json=register_body("replay_b", captchaId=challenge_id, captchaAnswer=answer)
    )
    assert error_of(replay)["code"] == "captcha_invalid"


def test_a_challenge_from_another_ip_bucket_is_refused(client_from, solve_captcha, frozen) -> None:
    owner = client_from("203.0.113.10")
    stranger = client_from("198.51.100.20")
    challenge_id, answer = solve_captcha(owner, "register")
    response = stranger.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert (response.status_code, error_of(response)["code"]) == (422, "captcha_invalid")
    # 它没有烧掉主人的次数：主人自己仍可用。
    mine = owner.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert mine.status_code == 201


def test_a_challenge_may_drift_inside_its_slash_24(client_from, solve_captcha, frozen) -> None:
    """移动网络换基站：绑定值是 /24，不是精确 IP（限速才用精确键）。"""
    first = client_from("203.0.113.10")
    second = client_from("203.0.113.99")
    challenge_id, answer = solve_captcha(first, "register")
    response = second.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert response.status_code == 201


def test_the_sixth_attempt_voids_the_challenge_even_if_correct(frozen_now) -> None:
    """AC-166：同一 `challengeId` 第 6 次尝试（无论对错）作废。

    在提供者层断言：经 HTTP 打 6 次会先撞上 `auth.register` 的 5 次/10 分钟（那是另一条守卫）。
    """
    provider = captcha_module.get_captcha_provider()
    issued = provider.issue("login", "203.0.113.5")
    answer = provider.debug_answer(issued.challenge_id)
    wrong = "AAAAA" if answer != "AAAAA" else "BBBBB"
    for _ in range(5):
        assert provider.verify(issued.challenge_id, wrong, "login", "203.0.113.5") is False
    assert provider.verify(issued.challenge_id, answer, "login", "203.0.113.5") is False
    assert issued.challenge_id not in provider._entries  # noqa: SLF001


def test_a_challenge_expires_after_300_seconds(frozen_now) -> None:
    provider = captcha_module.get_captcha_provider()
    issued = provider.issue("login", "203.0.113.5")
    answer = provider.debug_answer(issued.challenge_id)
    frozen_now.advance(seconds=299)
    assert provider.verify(issued.challenge_id, answer, "login", "203.0.113.5") is True
    other = provider.issue("login", "203.0.113.5")
    other_answer = provider.debug_answer(other.challenge_id)
    frozen_now.advance(seconds=301)
    assert provider.verify(other.challenge_id, other_answer, "login", "203.0.113.5") is False


def test_the_fifth_attempt_can_still_succeed(auth_client, solve_captcha, frozen) -> None:
    challenge_id, answer = solve_captcha(auth_client, "register")
    wrong = "AAAAA" if answer != "AAAAA" else "BBBBB"
    for _ in range(4):
        auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=wrong))
    fifth = auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert fifth.status_code == 201


def test_honeypot_and_too_fast_look_exactly_like_a_wrong_captcha(auth_client, solve_captcha, frozen) -> None:
    """AC-168：蜜罐非空 / `issuedAt` 过快 / 验证码错，响应**字节级相同**（除 requestId）。"""

    def comparable(response) -> dict:  # noqa: ANN001
        body = response.json()
        body["error"].pop("requestId")
        return {"status": response.status_code, "body": body}

    challenge_id, answer = solve_captcha(auth_client, "register")
    wrong = "AAAAA" if answer != "AAAAA" else "BBBBB"
    wrong_captcha = auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=wrong))

    challenge_id, answer = solve_captcha(auth_client, "register")
    honeypot = auth_client.post(
        f"{URL}/register",
        json=register_body(captchaId=challenge_id, captchaAnswer=answer, website="http://spam.example"),
    )
    challenge_id, answer = solve_captcha(auth_client, "register")
    now_ms = frozen().timestamp() * 1000
    too_fast = auth_client.post(
        f"{URL}/register",
        json=register_body(captchaId=challenge_id, captchaAnswer=answer, issuedAt=now_ms - 300),
    )
    assert comparable(honeypot) == comparable(wrong_captcha)
    assert comparable(too_fast) == comparable(wrong_captcha)
    assert wrong_captcha.status_code == 422


def test_a_client_clock_running_ahead_is_not_mistaken_for_a_script(auth_client, solve_captcha, frozen) -> None:
    """`issuedAt` 是**客户端**的钟：它比服务端快时差值为负，那是对方机器的事实，不是脚本的证据。
    权威的"过快"判定是服务端自己量的（见下一条），所以这里放行而不是把快钟用户锁在注册之外。"""
    challenge_id, answer = solve_captcha(auth_client, "register")
    ahead_ms = (frozen().timestamp() + 30) * 1000
    response = auth_client.post(
        f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer, issuedAt=ahead_ms)
    )
    assert response.status_code == 201


def test_answering_inside_the_minimum_solve_time_is_refused(auth_client, solve_captcha, frozen) -> None:
    """服务端自己也量：从签发到提交不足 1.2 s 的验证码答案，哪怕是对的也算脚本。"""
    challenge_id, answer = solve_captcha(auth_client, "register", settle=False)
    response = auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert error_of(response)["code"] == "captcha_invalid"
    frozen.advance(seconds=2)
    # 这张已经烧了一次尝试，但还没作废：2 秒后同一个答案可用。
    again = auth_client.post(f"{URL}/register", json=register_body(captchaId=challenge_id, captchaAnswer=answer))
    assert again.status_code == 201


# ── 用户名与密码 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("username", "rule"),
    [
        ("ab", "length"),
        ("a" * 21, "length"),
        ("has space", "charset"),
        ("名字很长的用户", "charset"),
        ("a-b-c", "charset"),
        ("admin", "reserved"),
        ("Admin", "reserved"),
        ("ROOT", "reserved"),
        ("admin01", "reserved"),
        ("root2", "reserved"),
        ("system", "reserved"),
        ("jdqqjr", "reserved"),
        ("Aragon123", "reserved"),
        ("null", "reserved"),
        ("undefined", "reserved"),
        ("support9", "reserved"),
        ("official", "reserved"),
        ("api", "reserved"),
    ],
)
def test_invalid_and_reserved_usernames(auth_client, solve_captcha, frozen, username: str, rule: str) -> None:
    response = register(auth_client, solve_captcha, username)
    assert response.status_code == 422, response.text
    assert error_of(response)["code"] == "invalid_username"
    assert error_of(response)["detail"] == {"rule": rule}


def test_words_merely_containing_a_reserved_word_are_fine(auth_client, solve_captcha, frozen) -> None:
    for name in ("administrator_x", "myroot", "api_user"):
        assert register(auth_client, solve_captcha, name).status_code == 201
        auth_client.post(f"{URL}/logout")


def test_taken_username_is_409_and_case_insensitive(auth_client, solve_captcha, frozen) -> None:
    assert register(auth_client, solve_captcha, "Alice").status_code == 201
    auth_client.post(f"{URL}/logout")
    response = register(auth_client, solve_captcha, "alice")
    assert (response.status_code, error_of(response)["code"]) == (409, "username_taken")


def test_weak_password_lists_the_unmet_rules(auth_client, solve_captcha, frozen) -> None:
    challenge_id, answer = solve_captcha(auth_client, "register")
    response = auth_client.post(
        f"{URL}/register",
        json={"username": "carol", "password": "12345678", "captchaId": challenge_id, "captchaAnswer": answer},
    )
    assert response.status_code == 422
    assert error_of(response)["code"] == "weak_password"
    assert error_of(response)["detail"]["rules"] == ["char_classes", "common_password", "sequential_chars"]


def test_a_rejected_registration_leaves_no_user_behind(auth_client, solve_captcha, frozen, engine) -> None:
    challenge_id, answer = solve_captcha(auth_client, "register")
    auth_client.post(
        f"{URL}/register",
        json={"username": "dave", "password": "weak", "captchaId": challenge_id, "captchaAnswer": answer},
    )
    with sessionmaker(bind=engine)() as db:
        assert db.execute(select(User).where(User.username_lc == "dave")).first() is None


# ── 注册模式与邀请码 ────────────────────────────────────────────────────────


def _set_mode(engine, mode: str) -> None:  # noqa: ANN001
    with sessionmaker(bind=engine)() as db:
        settings_service.set_str(db, settings_service.KEY_REGISTRATION_MODE, mode)
        db.commit()


def _make_invite(engine, **kwargs) -> str:  # noqa: ANN001, ANN003
    with sessionmaker(bind=engine)() as db:
        invite = invite_service.create_invite(db, created_by=None, **kwargs)
        db.commit()
        return invite.code


def test_closed_mode_refuses_everyone(auth_client, solve_captcha, frozen, engine) -> None:
    _set_mode(engine, "closed")
    response = register(auth_client, solve_captcha)
    assert (response.status_code, error_of(response)["code"]) == (403, "registration_closed")
    config = auth_client.get(f"{URL}/config").json()
    assert config["registrationMode"] == "closed" and config["registrationOpen"] is False


def test_invite_mode_requires_a_code_and_a_code_waives_the_captcha(auth_client, frozen, engine) -> None:
    _set_mode(engine, "invite")
    code = _make_invite(engine)
    without = auth_client.post(f"{URL}/register", json=register_body())
    assert (without.status_code, error_of(without)["code"]) == (422, "invite_required")

    # 邀请码注册**免图形验证码**（§24.3.6）。
    with_code = auth_client.post(f"{URL}/register", json=register_body(inviteCode=code))
    assert with_code.status_code == 201, with_code.text
    assert with_code.json()["user"]["role"] == "user"


def test_invite_code_is_normalised_and_single_use(auth_client, frozen, engine) -> None:
    code = _make_invite(engine)
    lowered = code.lower().replace("-", "")
    assert auth_client.post(f"{URL}/register", json=register_body("inv_a", inviteCode=lowered)).status_code == 201
    auth_client.post(f"{URL}/logout")
    spent = auth_client.post(f"{URL}/register", json=register_body("inv_b", inviteCode=code))
    assert (spent.status_code, error_of(spent)["code"]) == (422, "invite_invalid")


@pytest.mark.parametrize("state", ["unknown", "expired", "revoked"])
def test_unusable_invites_are_invite_invalid(auth_client, frozen, engine, state: str) -> None:
    if state == "unknown":
        code = "ZZZZ-ZZZZ"
    else:
        code = _make_invite(engine, expires_in_days=1)
        with sessionmaker(bind=engine)() as db:
            invite = db.execute(select(InviteCode).where(InviteCode.code == code)).scalar_one()
            if state == "expired":
                frozen.advance(days=2)
            else:
                invite_service.revoke(db, invite)
                db.commit()
    response = auth_client.post(f"{URL}/register", json=register_body(inviteCode=code))
    assert (response.status_code, error_of(response)["code"]) == (422, "invite_invalid")


def test_invite_and_signup_bonuses_become_ledger_rows(auth_client, frozen, engine) -> None:
    with sessionmaker(bind=engine)() as db:
        settings_service.set_str(db, settings_service.KEY_SIGNUP_BONUS, "2")
        db.commit()
    code = _make_invite(engine, bonus_credits=3)
    response = auth_client.post(f"{URL}/register", json=register_body("bonus_user", inviteCode=code))
    assert response.status_code == 201
    with sessionmaker(bind=engine)() as db:
        user = db.execute(select(User).where(User.username_lc == "bonus_user")).scalar_one()
        rows = db.execute(select(QuotaLedger).where(QuotaLedger.user_id == user.id)).scalars().all()
        assert sorted((row.reason, row.delta, row.source, row.resource) for row in rows) == [
            ("invite_bonus", 3, "bonus", "report"),
            ("signup_bonus", 2, "bonus", "report"),
        ]
        assert user.invite_id is not None


def test_eight_threads_racing_for_a_three_use_invite_yield_exactly_three_users(engine) -> None:
    """邀请码并发核销的唯一正确写法是带谓词的 `UPDATE`；读后写会放过 8 个。"""
    from app.security import ratelimit
    from app.services import auth_service
    from app.services.auth_service import ClientInfo

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        invite = invite_service.create_invite(db, created_by=None, max_uses=3)
        db.commit()
        code = invite.code

    outcomes: list[str] = []
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def attempt(index: int) -> None:
        db = factory()
        try:
            barrier.wait()
            result = auth_service.register(
                db,
                username=f"racer_{index}",
                password=GOOD_PASSWORD,
                captcha_id=None,
                captcha_answer=None,
                invite_code=code,
                display_name=None,
                honeypot=None,
                issued_at=None,
                client=ClientInfo(ip="203.0.113.0/24", ip_key=f"203.0.113.{index}"),
            )
            outcome = "ok" if result.user.id else "?"
        except Exception as exc:  # noqa: BLE001
            outcome = getattr(exc, "code", type(exc).__name__)
        finally:
            db.close()
        with guard:
            outcomes.append(outcome)

    ratelimit.reset_all()
    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("ok") == 3, outcomes
    assert outcomes.count("invite_invalid") == 5, outcomes
    with factory() as db:
        assert db.execute(select(InviteCode).where(InviteCode.code == code)).scalar_one().used_count == 3
        assert len(db.execute(select(User).where(User.username_lc.like("racer_%"))).scalars().all()) == 3


def test_wrong_invite_codes_are_rate_limited_per_ip(auth_client, frozen) -> None:
    """邀请码试错：5 次 / 10 分钟 / IP，第 6 次 429。"""
    statuses = [
        auth_client.post(f"{URL}/register", json=register_body(f"probe_{i}", inviteCode="ZZZZ-ZZZZ")).status_code
        for i in range(5)
    ]
    assert statuses == [422] * 5
    sixth = auth_client.post(f"{URL}/register", json=register_body("probe_x", inviteCode="ZZZZ-ZZZZ"))
    assert sixth.status_code == 429
    assert error_of(sixth)["code"] == "rate_limited"
    assert error_of(sixth)["detail"]["scope"] in {"auth.invite_probe", "auth.register"}


# ── 限速（AC-171）──────────────────────────────────────────────────────────


def test_sixth_registration_in_ten_minutes_is_429_with_retry_after(auth_client, frozen) -> None:
    for index in range(5):
        response = auth_client.post(f"{URL}/register", json=register_body(f"spam_{index}"))
        assert response.status_code == 422  # 验证码缺失，但每次都占一格额度
    sixth = auth_client.post(f"{URL}/register", json=register_body("spam_5"))
    assert sixth.status_code == 429
    assert int(sixth.headers["retry-after"]) >= 1
    assert error_of(sixth)["code"] == "rate_limited"
    # 窗口过去之后恢复。
    frozen.advance(seconds=601)
    assert auth_client.post(f"{URL}/register", json=register_body("spam_6")).status_code == 422


def test_thirty_first_captcha_in_a_minute_is_429(auth_client, frozen) -> None:
    for _ in range(30):
        assert auth_client.get(f"{URL}/captcha").status_code == 200
    blocked = auth_client.get(f"{URL}/captcha")
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers


# ── 验证码不可用（AC-172）──────────────────────────────────────────────────


@pytest.fixture()
def no_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "PIL.Image", None)


def test_without_pillow_open_registration_closes_but_login_and_invites_survive(
    auth_client, make_user, login_as, frozen, engine, no_pillow
) -> None:
    config = auth_client.get(f"{URL}/config").json()
    assert config["registrationOpen"] is False
    assert config["captchaAvailable"] is False
    assert config["inviteRegistrationOpen"] is True

    assert auth_client.get(f"{URL}/captcha").status_code == 503
    assert error_of(auth_client.get(f"{URL}/captcha"))["code"] == "captcha_unavailable"
    refused = auth_client.post(f"{URL}/register", json=register_body())
    assert (refused.status_code, error_of(refused)["code"]) == (503, "captcha_unavailable")

    # 登录不受影响……
    user = make_user("existing")
    login_as(auth_client, user)
    auth_client.post(f"{URL}/logout")
    # ……邀请码注册也不需要验证码，仍可用。
    code = _make_invite(engine)
    assert auth_client.post(f"{URL}/register", json=register_body("via_invite", inviteCode=code)).status_code == 201


def test_captcha_startup_status_names_the_failure(no_pillow) -> None:
    assert captcha_module.startup_status().startswith("captcha: unavailable(")


def test_captcha_startup_status_when_ready() -> None:
    assert captcha_module.startup_status() == "captcha: ready"


# ── 审计与同步性 ────────────────────────────────────────────────────────────


def test_registration_is_audited_without_secrets(auth_client, solve_captcha, frozen, engine) -> None:
    register(auth_client, solve_captcha)
    with sessionmaker(bind=engine)() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action == "auth.register")).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_name == "alice" and rows[0].target_name == "alice"
    assert GOOD_PASSWORD not in (rows[0].detail_json or "")


def test_auth_write_routes_are_threadpool_functions_not_coroutines() -> None:
    """R-A5：scrypt 在事件循环里会拖死 SSE——`login/register/password` 必须是同步 `def`。"""
    import inspect

    from app.routers import auth

    for handler in (auth.login, auth.register, auth.change_password):
        assert not inspect.iscoroutinefunction(handler), handler.__name__


def test_existing_session_rows_store_only_hashes(auth_client, solve_captcha, frozen, engine) -> None:
    """AC-155：`sessions.token_hash` 是 64 位十六进制，且不等于 Cookie 值。"""
    register(auth_client, solve_captcha)
    cookie = auth_client.cookies.get("aragon_sid")
    with sessionmaker(bind=engine)() as db:
        row = db.execute(select(UserSession)).scalar_one()
    assert cookie and row.token_hash != cookie
    assert len(row.token_hash) == 64 and int(row.token_hash, 16) >= 0
