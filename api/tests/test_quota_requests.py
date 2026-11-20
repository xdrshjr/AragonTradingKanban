"""额度申请闭环与 `/me/*`（spec §24.3.8(f)、§24.5.2；AC-193）。

服务层（`quota_requests`）与 HTTP 层各一半：申请唯一 pending 由**数据库索引**兜底；通过 = 同事务写
`request_approved` 赠送行（并立刻体现在 `/me/quota`）；拒绝必须带理由且用户读得到；`deep` 与 `report`
额度互不影响；`cite` 追问不计额度。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.errors import AppError, Conflict, NotFound, QuotaRequestPending
from app.models import AuditLog, QuotaLedger, QuotaRequest, User
from app.security.principal import SYSTEM, user_principal
from app.services import quota_requests, quota_service

ME = "/api/v1/me"


def principal_of(record):  # noqa: ANN001, ANN201
    return user_principal(
        user_id=record.id, username=record.username, role=record.role, session_id=None, must_change_password=False  # type: ignore[arg-type]
    )


# ── 服务层 ──────────────────────────────────────────────────────────────────


def test_one_pending_request_per_user_and_the_index_is_the_real_guard(session, make_user) -> None:  # noqa: ANN001
    alice = make_user("alice")
    who = principal_of(alice)
    first = quota_requests.create(session, who, resource="report", amount=3, reason="想多做几份")
    session.commit()
    with pytest.raises(QuotaRequestPending) as caught:
        quota_requests.create(session, who, resource="deep_ask", amount=1, reason="again")
    assert caught.value.detail == {"requestId": first.id}
    assert quota_requests.to_dict(first)["status"] == "pending"

    # 绕过应用层检查直接写第二条 pending：部分唯一索引必须拒绝。
    from sqlalchemy.exc import IntegrityError

    session.add(
        QuotaRequest(
            id="x" * 32, user_id=alice.id, resource="report", amount=1, reason="r", status="pending", created_at="t"
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize(
    ("resource", "amount", "reason"),
    [("report", 0, "x"), ("report", 21, "x"), ("gpu", 1, "x"), ("report", 1, ""), ("report", 1, "字" * 201)],
)
def test_create_validates_its_input(session, make_user, resource, amount, reason) -> None:  # noqa: ANN001
    with pytest.raises(AppError) as caught:
        quota_requests.create(session, principal_of(make_user("bob")), resource=resource, amount=amount, reason=reason)
    assert caught.value.status_code == 422 and caught.value.code == "validation_error"


def test_approving_writes_the_bonus_row_in_the_same_transaction(session, make_user) -> None:  # noqa: ANN001
    alice, boss = make_user("alice"), make_user("boss", role="admin")
    request = quota_requests.create(session, principal_of(alice), resource="report", amount=4, reason="项目需要")
    session.commit()

    resolved = quota_requests.resolve(
        session, actor=principal_of(boss), request_id=request.id, decision="approve", grant=2
    )
    assert (resolved.status, resolved.granted, resolved.resolved_by) == ("approved", 2, boss.id)
    assert resolved.resolved_at and resolved.resolved_at.endswith("Z")
    session.rollback()  # 调用方还没提交：请求状态与账本行必须一起回滚
    assert session.get(QuotaRequest, request.id).status == "pending"
    assert session.execute(select(QuotaLedger).where(QuotaLedger.reason == "request_approved")).first() is None

    quota_requests.resolve(session, actor=principal_of(boss), request_id=request.id, decision="approve")
    session.commit()
    (row,) = session.execute(select(QuotaLedger).where(QuotaLedger.reason == "request_approved")).scalars()
    assert (row.delta, row.source, row.resource, row.user_id, row.actor_id) == (4, "bonus", "report", alice.id, boss.id)
    status = quota_service.status(session, session.get(User, alice.id))
    assert status["report"]["bonus"] == 4 and status["report"]["remaining"] == 7
    assert status["report"]["pendingRequest"] is None


def test_rejecting_needs_a_reason_and_a_resolved_request_cannot_be_resolved_again(session, make_user) -> None:  # noqa: ANN001
    alice, boss = make_user("alice"), make_user("boss", role="admin")
    request = quota_requests.create(session, principal_of(alice), resource="report", amount=1, reason="r")
    session.commit()
    with pytest.raises(AppError) as caught:
        quota_requests.resolve(session, actor=principal_of(boss), request_id=request.id, decision="reject")
    assert caught.value.status_code == 422
    rejected = quota_requests.resolve(
        session, actor=principal_of(boss), request_id=request.id, decision="reject", note="本周已超量"
    )
    session.commit()
    assert (rejected.status, rejected.granted, rejected.resolution_note) == ("rejected", 0, "本周已超量")
    with pytest.raises(Conflict):
        quota_requests.resolve(session, actor=principal_of(boss), request_id=request.id, decision="approve")
    with pytest.raises(NotFound):
        quota_requests.resolve(session, actor=principal_of(boss), request_id="nope", decision="approve")
    assert session.execute(select(QuotaLedger)).first() is None, "拒绝不写账本"
    # 处理完后可以再申请（pending 已清）。
    again = quota_requests.create(session, principal_of(alice), resource="deep_ask", amount=2, reason="换个资源")
    assert again.status == "pending"


def test_the_queue_is_oldest_first_for_pending_and_newest_first_otherwise(session, make_user) -> None:  # noqa: ANN001
    boss = principal_of(make_user("boss", role="admin"))
    for index in range(5):
        user = make_user(f"queue_{index}")
        row = quota_requests.create(session, principal_of(user), resource="report", amount=1, reason=f"r{index}")
        row.created_at = f"2026-11-20T00:00:0{index}Z"
        session.commit()  # 释放写事务：`make_user` 用的是另一个会话
    page, total, cursor = quota_requests.list_queue(session, limit=2)
    assert [row.reason for row in page] == ["r0", "r1"] and total == 5 and cursor
    rest, _, tail = quota_requests.list_queue(session, limit=10, before=cursor)
    assert [row.reason for row in rest] == ["r2", "r3", "r4"] and tail is None
    quota_requests.resolve(session, actor=boss, request_id=page[0].id, decision="reject", note="n")
    quota_requests.resolve(session, actor=boss, request_id=page[1].id, decision="reject", note="n")
    session.commit()
    done, done_total, _ = quota_requests.list_queue(session, status="rejected")
    assert [row.reason for row in done] == ["r1", "r0"] and done_total == 2
    with pytest.raises(AppError):
        quota_requests.list_queue(session, status="weird")


def test_a_non_user_principal_has_no_account_to_request_for(session) -> None:  # noqa: ANN001
    with pytest.raises(NotFound):
        quota_requests.create(session, SYSTEM, resource="report", amount=1, reason="r")


# ── HTTP：/me/* ─────────────────────────────────────────────────────────────


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def alice_client(auth_client, make_user, login_as):  # noqa: ANN001, ANN201
    record = make_user("alice")
    client = TestClient(auth_client.app)
    login_as(client, record)
    return client, record


def test_me_quota_is_the_quota_status_and_requires_a_session(alice_client, auth_client) -> None:  # noqa: ANN001
    client, _record = alice_client
    body = client.get(f"{ME}/quota").json()
    assert set(body) == {"report", "deepAsk", "tz"}
    assert body["report"]["limit"] == 3 and body["report"]["remaining"] == 3 and body["deepAsk"]["limit"] == 10
    guest = TestClient(auth_client.app).get(f"{ME}/quota")
    assert guest.status_code == 401 and guest.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/v1/auth/me").json()["quota"]["report"]["remaining"] == 3, "Me.quota 同源"


def test_the_ledger_endpoint_lists_my_rows_newest_first(alice_client, maker) -> None:  # noqa: ANN001
    client, record = alice_client
    with maker() as db:
        user = db.get(User, record.id)
        for _ in range(3):
            quota_service.grant(db, actor=SYSTEM, target_user=user, resource="report", amount=1, note="赠送")
        db.commit()
    page = client.get(f"{ME}/quota-ledger", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["nextCursor"]
    item = page["items"][0]
    assert (item["reason"], item["delta"], item["source"], item["resource"], item["note"]) == (
        "admin_grant",
        1,
        "bonus",
        "report",
        "赠送",
    )
    rest = client.get(f"{ME}/quota-ledger", params={"limit": 10, "before": page["nextCursor"]}).json()
    assert len(rest["items"]) == 1 and rest["nextCursor"] is None
    bad = client.get(f"{ME}/quota-ledger", params={"before": "abc"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_cursor"


def test_filing_a_request_over_http_then_reading_it_back(alice_client, maker) -> None:  # noqa: ANN001
    client, record = alice_client
    created = client.post(f"{ME}/quota-requests", json={"resource": "report", "amount": 5, "reason": "要写周报"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["status"], body["amount"], body["resource"], body["granted"]) == ("pending", 5, "report", None)

    duplicate = client.post(f"{ME}/quota-requests", json={"resource": "deep_ask", "amount": 1, "reason": "x"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "quota_request_pending"
    assert duplicate.json()["error"]["detail"] == {"requestId": body["id"]}

    quota = client.get(f"{ME}/quota").json()
    assert quota["report"]["pendingRequest"]["id"] == body["id"]
    assert quota["deepAsk"]["pendingRequest"] is None, "申请挂在被申请的资源上"

    with maker() as db:
        quota_requests.resolve(
            db, actor=SYSTEM, request_id=body["id"], decision="reject", note="额度紧张"
        )
        db.commit()
    mine = client.get(f"{ME}/quota-requests").json()["items"]
    assert [(row["status"], row["resolutionNote"]) for row in mine] == [("rejected", "额度紧张")]
    with maker() as db:
        actions = [row.action for row in db.execute(select(AuditLog)).scalars()]
    assert "quota.request.create" in actions


@pytest.mark.parametrize(
    "payload",
    [
        {"resource": "report", "amount": 0, "reason": "x"},
        {"resource": "report", "amount": 21, "reason": "x"},
        {"resource": "gpu", "amount": 1, "reason": "x"},
        {"resource": "report", "amount": 1, "reason": ""},
        {"resource": "report", "amount": 1, "reason": "字" * 201},
    ],
)
def test_request_validation_is_a_422(alice_client, payload) -> None:  # noqa: ANN001
    client, _ = alice_client
    response = client.post(f"{ME}/quota-requests", json=payload)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


def test_guests_have_no_quota_surface(auth_client) -> None:  # noqa: ANN001
    guest = TestClient(auth_client.app)
    filed = guest.post(f"{ME}/quota-requests", json={"resource": "report", "amount": 1, "reason": "r"})
    assert filed.status_code == 401
    assert guest.get(f"{ME}/quota-ledger").status_code == 401


def test_the_open_mode_service_principal_has_no_account_so_403_not_500(client) -> None:  # noqa: ANN001
    assert client.get(f"{ME}/quota").status_code == 403
