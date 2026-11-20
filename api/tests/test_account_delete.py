"""自助注销与 `account_lifecycle` 收敛（spec §25.3.4；AC-225 – AC-228、§25.8 #4、#9）。

授权在调用方、副作用在 `account_lifecycle.delete_account`：自助路径不能走管理矩阵
（它对"删自己"一律 `422 self_action_forbidden`，AC-197），root 的拒绝用既有码 `root_protected`。
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import AuditLog, QuotaLedger, QuotaRequest, User, UserSession, UserWatchlistItem
from app.services import account_lifecycle, admin_user_service, auth_service, user_service

AUTH = "/api/v1/auth"
ADMIN = "/api/v1/admin"


@pytest.fixture(autouse=True)
def _frozen(frozen_now):  # noqa: ANN001, ANN202
    return frozen_now


def error_of(response) -> dict:  # noqa: ANN001
    return response.json()["error"]


def delete_me(client, password: str, confirm: str):  # noqa: ANN001, ANN201
    return client.request("DELETE", f"{AUTH}/account", json={"password": password, "confirm": confirm})


def fresh(auth_client):  # noqa: ANN001, ANN201
    return auth_client.__class__(auth_client.app)


# ── AC-225 ──────────────────────────────────────────────────────────────────


def test_password_and_confirm_must_both_be_right(auth_client, make_user, login_as) -> None:  # noqa: ANN001
    person = make_user("leaver")
    login_as(auth_client, person)
    wrong_password = delete_me(auth_client, "Wrong-Pass-1x", "leaver")
    assert wrong_password.status_code == 401 and error_of(wrong_password)["code"] == "invalid_credentials"
    wrong_confirm = delete_me(auth_client, person.password, "Leaver")  # case-sensitive
    assert wrong_confirm.status_code == 422
    assert error_of(wrong_confirm)["code"] == "validation_error" and error_of(wrong_confirm)["detail"] == {
        "field": "confirm"
    }
    assert delete_me(auth_client, person.password, "").status_code == 422
    assert auth_client.get(f"{AUTH}/me").json()["authenticated"] is True  # nothing happened yet

    cookie = auth_client.cookies.get("aragon_sid")
    done = delete_me(auth_client, person.password, "leaver")
    assert done.status_code == 204
    # The very same cookie, replayed, is dead.
    replay = fresh(auth_client)
    replay.cookies.set("aragon_sid", cookie)
    assert replay.get(f"{AUTH}/sessions").status_code == 401
    assert auth_client.get(f"{AUTH}/me").json()["authenticated"] is False


# ── AC-226 ──────────────────────────────────────────────────────────────────


def test_every_side_effect_happens(auth_client, make_user, login_as, instrument, engine, session) -> None:  # noqa: ANN001
    person = make_user("leaver")
    login_as(auth_client, person)
    other_device = fresh(auth_client)
    login_as(other_device, person)
    now = "2026-11-20T00:00:00Z"
    with sessionmaker(bind=engine)() as db:
        db.add(UserWatchlistItem(user_id=person.id, instrument_id=instrument.id, added_at=now))
        db.add(
            QuotaRequest(
                id="q" * 32, user_id=person.id, resource="report", amount=3, reason="想多做几份", created_at=now
            )
        )
        db.add(
            QuotaLedger(
                user_id=person.id,
                resource="report",
                delta=2,
                reason="signup_bonus",
                source="bonus",
                day_key="2026-11-20",
                created_at=now,
            )
        )
        db.commit()

    assert delete_me(auth_client, person.password, "leaver").status_code == 204

    with sessionmaker(bind=engine)() as db:
        user = db.get(User, person.id)
        assert user.status == "deleted" and user.username_lc == f"deleted:{person.id}"
        assert user.password_hash == "!" and user.recovery_hash is None
        assert all(s.revoked_at for s in db.execute(select(UserSession)).scalars() if s.user_id == person.id)
        assert db.execute(select(UserWatchlistItem).where(UserWatchlistItem.user_id == person.id)).first() is None
        request = db.get(QuotaRequest, "q" * 32)
        assert (request.status, request.resolution_note) == ("rejected", "账号已注销")
        assert db.execute(select(QuotaLedger).where(QuotaLedger.user_id == person.id)).first() is not None  # kept
        actions = [row.action for row in db.execute(select(AuditLog)).scalars()]
    assert "user.self_delete" in actions and "user.delete" not in actions
    assert other_device.get(f"{AUTH}/sessions").status_code == 401

    # The name is free again, and the new owner is a different person.
    again = make_user("leaver")
    assert again.id != person.id


def test_the_audit_row_keeps_the_name_the_person_had(auth_client, make_user, login_as, engine) -> None:  # noqa: ANN001
    person = make_user("remembered")
    login_as(auth_client, person)
    assert delete_me(auth_client, person.password, "remembered").status_code == 204
    with sessionmaker(bind=engine)() as db:
        row = db.execute(select(AuditLog).where(AuditLog.action == "user.self_delete")).scalar_one()
    assert (row.actor_name, row.target_name, row.target_id) == ("remembered", "remembered", person.id)


# ── AC-227 · 谁能 / 谁不能 ───────────────────────────────────────────────────


def test_root_cannot_delete_itself_and_the_code_is_the_existing_one(auth_client, engine) -> None:  # noqa: ANN001
    from sqlalchemy import update

    with sessionmaker(bind=engine)() as db:
        db.execute(update(User).where(User.role == "root").values(must_change_password=0))
        db.commit()
    assert auth_client.post(f"{AUTH}/login", json={"username": "jdqqjr", "password": "13519126390"}).status_code == 200
    response = delete_me(auth_client, "13519126390", "jdqqjr")
    assert response.status_code == 403
    assert error_of(response)["code"] == "root_protected" and error_of(response)["detail"] == {"action": "self_delete"}


def test_the_service_principal_and_guests_are_refused(auth_client, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "api_token", "service-token", raising=False)
    service = fresh(auth_client)
    service.headers["Authorization"] = "Bearer service-token"
    response = delete_me(service, "x", "x")
    assert response.status_code == 403 and error_of(response)["code"] == "forbidden"
    assert error_of(response)["detail"] == {"permission": "account.self"}
    assert delete_me(fresh(auth_client), "x", "x").status_code == 401


def test_a_plain_user_and_an_admin_can_both_delete_themselves(auth_client, make_user, login_as) -> None:  # noqa: ANN001
    """The matrix's "no self-delete" rule belongs to the *admin API* only (AC-197 unchanged)."""
    for name, role in (("self_user", "user"), ("self_admin", "admin")):
        person = make_user(name, role=role)
        client = fresh(auth_client)
        login_as(client, person)
        assert delete_me(client, person.password, name).status_code == 204, role

    admin = make_user("still_admin", role="admin")
    admin_client = fresh(auth_client)
    login_as(admin_client, admin)
    via_admin_api = admin_client.delete(f"{ADMIN}/users/{admin.id}", params={"confirm": "still_admin"})
    assert via_admin_api.status_code == 422 and error_of(via_admin_api)["code"] == "self_action_forbidden"


# ── AC-228 · 收敛 ────────────────────────────────────────────────────────────


def test_both_paths_use_the_one_implementation_and_authorise_differently() -> None:
    admin_source = inspect.getsource(admin_user_service.delete_user)
    self_source = inspect.getsource(auth_service.delete_own_account)
    assert "anonymize(" not in admin_source and "anonymize(" not in self_source
    assert 'account_lifecycle.delete_account(db, actor, target, via="admin"' in admin_source
    assert 'account_lifecycle.delete_account(db, principal, user, via="self")' in self_source
    assert "assert_can_manage_user" in admin_source and "assert_can_manage_user" not in self_source
    assert "assert_can_self_delete" in self_source
    assert "anonymize(" in inspect.getsource(account_lifecycle.delete_account)


def _snapshot(engine, user_id: str) -> dict:  # noqa: ANN001
    """Everything the deletion touches, with the per-user ids / names factored out."""
    with sessionmaker(bind=engine)() as db:
        user = db.get(User, user_id)
        sessions = sorted(
            (s.revoked_at is not None, bool(s.revoked_reason)) for s in db.query(UserSession).filter_by(user_id=user_id)
        )
        requests = sorted((r.status, r.resolution_note) for r in db.query(QuotaRequest).filter_by(user_id=user_id))
        return {
            "user": (
                user.status,
                user.username_lc == f"deleted:{user.id}",
                user.password_hash,
                user.display_name,
                user.recovery_hash,
                user.must_change_password,
                user.failed_logins,
            ),
            "sessions": sessions,
            "watchlist": db.query(UserWatchlistItem).filter_by(user_id=user_id).count(),
            "requests": requests,
            "ledger": db.query(QuotaLedger).filter_by(user_id=user_id).count(),
        }


def test_self_and_admin_deletion_leave_the_same_final_state(  # noqa: ANN001
    auth_client, make_user, login_as, instrument, engine
) -> None:
    now = "2026-11-20T00:00:00Z"
    people = [make_user("twin_a"), make_user("twin_b")]
    for index, person in enumerate(people):
        with sessionmaker(bind=engine)() as db:
            db.add(UserWatchlistItem(user_id=person.id, instrument_id=instrument.id, added_at=now))
            db.add(
                QuotaRequest(
                    id=f"{index}" * 32, user_id=person.id, resource="report", amount=2, reason="r", created_at=now
                )
            )
            db.add(
                QuotaLedger(
                    user_id=person.id,
                    resource="report",
                    delta=1,
                    reason="signup_bonus",
                    source="bonus",
                    day_key="2026-11-20",
                    created_at=now,
                )
            )
            db.commit()

    self_client = fresh(auth_client)
    login_as(self_client, people[0])
    assert delete_me(self_client, people[0].password, "twin_a").status_code == 204

    login_as(fresh(auth_client), people[1])  # one live session each, so the two snapshots are comparable
    admin = make_user("twin_admin", role="admin")
    admin_client = fresh(auth_client)
    login_as(admin_client, admin)
    assert admin_client.delete(f"{ADMIN}/users/{people[1].id}", params={"confirm": "twin_b"}).status_code == 204

    assert _snapshot(engine, people[0].id) == _snapshot(engine, people[1].id)
    # …and they differ only where they must: which audit action, and the session reason.
    with sessionmaker(bind=engine)() as db:
        actions = {row.action for row in db.query(AuditLog)}
    assert {"user.self_delete", "user.delete"} <= actions


def test_a_deleted_account_cannot_be_deleted_again(auth_client, make_user, login_as, engine) -> None:  # noqa: ANN001
    person = make_user("once_only")
    login_as(auth_client, person)
    assert delete_me(auth_client, person.password, "once_only").status_code == 204
    with sessionmaker(bind=engine)() as db:
        user = db.get(User, person.id)
        assert user_service.get_by_username(db, "once_only") is None
        assert user.status == "deleted"
    assert delete_me(auth_client, person.password, "once_only").status_code == 401
