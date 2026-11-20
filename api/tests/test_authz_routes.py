"""既有能力的多用户改造（spec §24.3.9；AC-174、AC-178 – AC-181、AC-189/190、AC-192 的路由半边）。

`test_authz_objects.py` 钉的是纯函数判定表；这里钉的是**路由真的调用了它们**：谁能管谁的报告、
谁能看谁的追问、`RunOut` / `Capabilities` 对谁脱敏、自选怎么分人、定时刷新只给管理员，以及额度预检
排在 409 前面。世界是一个 `enforced` 应用，三个各有 Cookie 罐的客户端：alice、bob（普通用户）与 boss（admin）。
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import iso
from app.models import (
    AuditLog,
    Instrument,
    QuotaLedger,
    Report,
    ReportAsk,
    Run,
    Schedule,
    User,
    UserWatchlistItem,
    WatchlistItem,
)
from app.services import ask_service, quota_service, settings_service, watchlist_service

API = "/api/v1"
#: 一份能通过 `ReportDoc` 校验的真文档（详情路由要把它解析成响应模型）。
SAMPLE_DOC = (Path(__file__).parent / "fixtures" / "sample_report.json").read_text(encoding="utf-8")


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def world(auth_client, make_user, login_as, instrument, maker):  # noqa: ANN001, ANN201
    class World:
        pass

    w = World()
    w.app = auth_client.app
    w.instrument = instrument
    w.maker = maker
    w.records = {
        "alice": make_user("alice"),
        "bob": make_user("bob"),
        "boss": make_user("boss", role="admin"),
    }
    w.clients = {}
    for name, record in w.records.items():
        # alice 用 `auth_client` 本体：它是带持久 portal 的上下文客户端，后台生成任务在请求结束后
        # 仍然活着；普通 `TestClient(app)` 每个请求一个事件循环，请求一结束任务就被掐成 `interrupted`。
        client = auth_client if name == "alice" else TestClient(auth_client.app)
        login_as(client, record)
        w.clients[name] = client
    w.guest = TestClient(auth_client.app)
    return w


def code_of(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def add_report(maker, instrument, *, created_by: str | None, version: int = 1, deleted: bool = False) -> str:  # noqa: ANN001
    report_id = uuid.uuid4().hex
    with maker() as db:
        db.add(
            Report(
                id=report_id,
                instrument_id=instrument.id,
                version=version,
                status="ready",
                title="t",
                as_of="2026-02-10T00:00:00Z",
                generated_at=f"2026-02-10T00:00:{version:02d}Z",
                doc_json=SAMPLE_DOC,
                doc_bytes=len(SAMPLE_DOC),
                is_latest=False,
                pinned=False,
                archived=False,
                created_by=created_by,
                deleted_at="2026-02-11T00:00:00Z" if deleted else None,
            )
        )
        db.commit()
    return report_id


def add_run(maker, instrument, *, created_by: str | None, status: str = "succeeded", **extra) -> str:  # noqa: ANN001, ANN003
    run_id = uuid.uuid4().hex
    with maker() as db:
        db.add(
            Run(
                id=run_id,
                instrument_id=instrument.id,
                status=status,
                pct=0,
                started_at=iso(datetime.now(UTC)),
                cost_known=1,
                created_by=created_by,
                **extra,
            )
        )
        db.commit()
    return run_id


# ── 生成入口：401 / 预检优先 / 202 体（AC-189、AC-190、AC-192） ─────────────


def test_guest_cannot_generate_and_gets_a_401_not_a_run(world) -> None:  # noqa: ANN001
    response = world.guest.post(f"{API}/instruments/{world.instrument.id}/reports", json={})
    assert response.status_code == 401 and code_of(response) == "unauthorized"
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(Run)).scalar_one() == 0


def test_a_user_generates_and_the_202_body_carries_the_quota(world) -> None:  # noqa: ANN001
    response = world.clients["alice"].post(f"{API}/instruments/{world.instrument.id}/reports", json={})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["quota"]["limit"] == 3 and body["quota"]["remaining"] == 2 and body["quota"]["usedToday"] == 1
    with world.maker() as db:
        run = db.get(Run, body["runId"])
        assert run.created_by == world.records["alice"].id
        (row,) = db.execute(select(QuotaLedger).where(QuotaLedger.reason == "run")).scalars()
        assert row.ref_id == run.id


def test_an_admin_generates_without_quota_and_the_body_says_null(world) -> None:  # noqa: ANN001
    response = world.clients["boss"].post(f"{API}/instruments/{world.instrument.id}/reports", json={})
    assert response.status_code == 202 and response.json()["quota"]["remaining"] is None
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0


def test_quota_is_checked_before_the_instrument_conflict(world) -> None:  # noqa: ANN001
    """AC-189：额度为 0 的用户对"同标已有运行中"得 429 而不是 409；额度充足时仍是 409。"""
    add_run(world.maker, world.instrument, created_by=world.records["boss"].id, status="running")
    url = f"{API}/instruments/{world.instrument.id}/reports"
    assert world.clients["alice"].post(url, json={}).status_code == 409

    with world.maker() as db:
        db.get(User, world.records["alice"].id).quota_daily_limit = 0
        db.commit()
    refused = world.clients["alice"].post(url, json={})
    assert refused.status_code == 429 and code_of(refused) == "quota_exceeded"
    assert refused.json()["error"]["detail"]["scope"] == "user"


def test_a_conflict_charges_nothing_and_creates_no_second_run(world) -> None:  # noqa: ANN001
    add_run(world.maker, world.instrument, created_by=None, status="running")
    refused = world.clients["alice"].post(f"{API}/instruments/{world.instrument.id}/reports", json={})
    assert refused.status_code == 409 and code_of(refused) == "run_conflict"
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(Run)).scalar_one() == 1


def test_the_site_wide_cap_is_a_429_the_user_cannot_request_their_way_out_of(world) -> None:  # noqa: ANN001
    other = Instrument(
        id="CN:000001.SZ",
        symbol="000001.SZ",
        name="平安银行",
        market="CN",
        currency="CNY",
        tz="Asia/Shanghai",
        created_at="2026-02-10T00:00:00Z",
        updated_at="2026-02-10T00:00:00Z",
    )
    with world.maker() as db:
        db.add(other)
        settings_service.set_value(db, settings_service.KEY_SITE_DAILY_CAP, 1)
        db.commit()
        # 站点当日已有 1 个名额被 alice 用掉（直接落账：不依赖后台任务的快慢）。
        from app.security.principal import user_principal

        who = user_principal(
            user_id=world.records["alice"].id,
            username="alice",
            role="user",
            session_id=None,
            must_change_password=False,
        )
        with quota_service.reservation(db, who, "report", uuid.uuid4().hex):
            db.add(
                Run(
                    id=uuid.uuid4().hex, instrument_id=world.instrument.id, status="succeeded", pct=100,
                    started_at=iso(datetime.now(UTC)), cost_known=0, created_by=who.user_id,
                )
            )
            db.commit()
    refused = world.clients["bob"].post(f"{API}/instruments/{other.id}/reports", json={})
    assert refused.status_code == 429 and code_of(refused) == "quota_exceeded"
    detail = refused.json()["error"]["detail"]
    assert detail["scope"] == "site" and detail["canRequest"] is False
    # 站点名额满不是个人的事：管理员不受限也不占名额。
    assert world.clients["boss"].post(f"{API}/instruments/{other.id}/reports", json={}).status_code == 202


# ── 报告管理：拥有者 / 管理员 / 全站旗标（AC-178、RV-10） ───────────────────


def test_report_summary_carries_author_and_the_viewers_powers(world) -> None:  # noqa: ANN001
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id, version=1)
    legacy = add_report(world.maker, world.instrument, created_by=None, version=2)

    as_alice = world.clients["alice"].get(f"{API}/reports/{mine}").json()["report"]
    assert (as_alice["author"], as_alice["canManage"], as_alice["canCurate"]) == ("alice", True, False)
    as_bob = world.clients["bob"].get(f"{API}/reports/{mine}").json()["report"]
    assert (as_bob["author"], as_bob["canManage"], as_bob["canCurate"]) == ("alice", False, False)
    as_boss = world.clients["boss"].get(f"{API}/reports/{mine}").json()["report"]
    assert (as_boss["canManage"], as_boss["canCurate"]) == (True, True)
    as_guest = world.guest.get(f"{API}/reports/{mine}").json()["report"]
    assert (as_guest["author"], as_guest["canManage"], as_guest["canCurate"]) == ("alice", False, False)

    old = world.clients["alice"].get(f"{API}/reports/{legacy}").json()["report"]
    assert old["author"] is None and old["canManage"] is False, "历史数据不归谁来问就归谁"


def test_an_anonymised_author_reads_as_a_deregistered_user(world) -> None:  # noqa: ANN001
    from app.services import user_service

    mine = add_report(world.maker, world.instrument, created_by=world.records["bob"].id)
    with world.maker() as db:
        user_service.anonymize(db, db.get(User, world.records["bob"].id))
        db.commit()
    assert world.guest.get(f"{API}/reports/{mine}").json()["report"]["author"] == "已注销用户"


def test_owner_may_archive_delete_and_restore_others_may_not(world) -> None:  # noqa: ANN001
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id)
    alice, bob = world.clients["alice"], world.clients["bob"]
    url = f"{API}/reports/{mine}"

    assert bob.patch(url, json={"archived": True}).status_code == 403
    assert bob.delete(url).status_code == 403
    assert world.guest.patch(url, json={"archived": True}).status_code == 401

    assert alice.patch(url, json={"archived": True}).status_code == 200
    assert alice.delete(url).status_code == 204
    assert bob.post(f"{url}/restore").status_code == 404, "回收站内容不外泄：别人的软删报告对非拥有者就是不存在"
    assert world.guest.post(f"{url}/restore").status_code == 401
    restored = alice.post(f"{url}/restore")
    assert restored.status_code == 200 and restored.json()["canManage"] is True
    assert alice.post(f"{url}/restore").status_code == 409, "没删的恢复仍是 409 report_not_deleted"


def test_pinned_and_set_latest_need_manage_any_even_on_your_own_report(world) -> None:  # noqa: ANN001
    """RV-10：整个请求原子失败——同请求里合法的 `archived` 也不生效。"""
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id)
    url = f"{API}/reports/{mine}"
    for body in ({"pinned": True}, {"setLatest": True}, {"pinned": False}, {"archived": True, "pinned": True}):
        refused = world.clients["alice"].patch(url, json=body)
        assert refused.status_code == 403, body
        assert refused.json()["error"]["detail"] == {"permission": "report.manage_any"}
    with world.maker() as db:
        row = db.get(Report, mine)
        assert (row.archived, row.pinned, row.is_latest) == (False, False, False)
    assert world.clients["boss"].patch(url, json={"pinned": True}).status_code == 200


def test_history_rows_without_an_owner_are_admin_only(world) -> None:  # noqa: ANN001
    legacy = add_report(world.maker, world.instrument, created_by=None)
    assert world.clients["alice"].patch(f"{API}/reports/{legacy}", json={"archived": True}).status_code == 403
    assert world.clients["alice"].delete(f"{API}/reports/{legacy}").status_code == 403
    assert world.clients["boss"].patch(f"{API}/reports/{legacy}", json={"archived": True}).status_code == 200


def test_an_admin_touching_someone_elses_report_is_audited(world) -> None:  # noqa: ANN001
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id)
    own = add_report(world.maker, world.instrument, created_by=world.records["boss"].id, version=2)
    assert world.clients["boss"].delete(f"{API}/reports/{own}").status_code == 204
    assert world.clients["boss"].delete(f"{API}/reports/{mine}").status_code == 204
    with world.maker() as db:
        rows = list(db.execute(select(AuditLog).where(AuditLog.action == "report.manage_other")).scalars())
    assert len(rows) == 1, "管理自己的报告不写 report.manage_other"
    assert rows[0].target_id == mine and rows[0].actor_name == "boss"


def test_the_trash_scope_is_mine_for_users_and_everything_for_admins(world) -> None:  # noqa: ANN001
    a = add_report(world.maker, world.instrument, created_by=world.records["alice"].id, version=1, deleted=True)
    b = add_report(world.maker, world.instrument, created_by=world.records["bob"].id, version=2, deleted=True)
    c = add_report(world.maker, world.instrument, created_by=None, version=3, deleted=True)

    def trash_ids(client) -> set[str]:  # noqa: ANN001
        body = client.get(f"{API}/reports", params={"scope": "trash"}).json()
        return {item["report"]["id"] for item in body["items"]}

    assert trash_ids(world.clients["alice"]) == {a}
    assert trash_ids(world.clients["bob"]) == {b}
    assert trash_ids(world.clients["boss"]) == {a, b, c}
    counts = world.clients["alice"].get(f"{API}/reports").json()["counts"]
    assert counts["trash"] == 1, "tab 徽章与列表同口径"
    assert world.guest.get(f"{API}/reports", params={"scope": "trash"}).status_code == 401
    assert world.guest.get(f"{API}/reports", params={"scope": "starred"}).status_code == 200


# ── run：取消与脱敏（AC-181） ───────────────────────────────────────────────


def test_run_out_is_redacted_for_everyone_but_the_owner_and_admins(world) -> None:  # noqa: ANN001
    run_id = add_run(
        world.maker,
        world.instrument,
        created_by=world.records["alice"].id,
        cost_usd=0.42,
        cost_usd_est=0.4,
        cost_source="reported",
        agent_provider="deepseek",
        agent_model="m1",
        failure_fingerprint="abcdef012345",
        exit_code=2,
    )
    hidden = ("costUsd", "costUsdEst", "costSource", "agentProvider", "agentModel", "failureFingerprint", "exitCode")
    for who in ("alice", "boss"):
        full = world.clients[who].get(f"{API}/runs/{run_id}").json()
        assert full["costUsd"] == 0.42 and full["agentModel"] == "m1" and full["exitCode"] == 2, who
    for viewer in (world.clients["bob"], world.guest):
        redacted = viewer.get(f"{API}/runs/{run_id}").json()
        assert all(redacted[key] is None for key in hidden)
        assert redacted["status"] == "succeeded" and redacted["id"] == run_id, "状态仍公开：进度本来就可见"
    listed = world.guest.get(f"{API}/instruments/{world.instrument.id}/runs").json()
    assert all(item[key] is None for item in listed for key in hidden)
    mine = world.clients["alice"].get(f"{API}/instruments/{world.instrument.id}/runs").json()
    assert mine[0]["costUsd"] == 0.42


def test_cancel_run_is_owner_or_admin_and_the_other_case_is_audited(world) -> None:  # noqa: ANN001
    run_id = add_run(world.maker, world.instrument, created_by=world.records["alice"].id, status="running")
    assert world.clients["bob"].post(f"{API}/runs/{run_id}/cancel").status_code == 403
    assert world.guest.post(f"{API}/runs/{run_id}/cancel").status_code == 401
    assert world.clients["boss"].post(f"{API}/runs/{run_id}/cancel").status_code == 200
    with world.maker() as db:
        assert db.get(Run, run_id).status == "cancelling"
        rows = list(db.execute(select(AuditLog).where(AuditLog.action == "run.cancel_other")).scalars())
    assert [row.target_id for row in rows] == [run_id]


def test_owner_cancelling_their_own_run_is_not_an_audit_event(world, maker) -> None:  # noqa: ANN001
    run_id = add_run(maker, world.instrument, created_by=world.records["alice"].id, status="running")
    assert world.clients["alice"].post(f"{API}/runs/{run_id}/cancel").status_code == 200
    with maker() as db:
        assert db.execute(select(AuditLog).where(AuditLog.action == "run.cancel_other")).first() is None


def test_a_historical_run_without_an_owner_is_admin_only(world) -> None:  # noqa: ANN001
    run_id = add_run(world.maker, world.instrument, created_by=None, status="running")
    assert world.clients["alice"].post(f"{API}/runs/{run_id}/cancel").status_code == 403


def test_estimate_requires_the_generate_permission(world) -> None:  # noqa: ANN001
    payload = {"symbol": world.instrument.symbol, "kind": "run"}
    assert world.guest.post(f"{API}/runs/estimate", json=payload).status_code == 401
    assert world.clients["alice"].post(f"{API}/runs/estimate", json=payload).status_code == 200


# ── Capabilities：同形状脱敏（AC-180） ──────────────────────────────────────


def test_capabilities_is_redacted_in_place_for_non_engine_probers(world) -> None:  # noqa: ANN001
    full = world.clients["boss"].get(f"{API}/capabilities").json()
    assert full["redacted"] is False and full["engine"] is not None and full["runtime"] is not None
    for viewer in (world.guest, world.clients["alice"]):
        thin = viewer.get(f"{API}/capabilities").json()
        assert set(thin) == set(full), "键集合与完整响应相同（同形状）"
        assert set(thin["aragon"]) == set(full["aragon"])
        assert set(thin["features"]) == set(full["features"])
        assert thin["redacted"] is True
        assert thin["engine"] is None and thin["runtime"] is None
        assert thin["limits"]["monthlyBudgetUsd"] == 0 and set(thin["limits"]) == set(full["limits"])
        aragon = thin["aragon"]
        assert aragon["keyPresent"] is False and aragon["baseUrlHost"] is None and aragon["verified"] is False
        assert aragon["provider"] is None and aragon["model"] is None
        assert aragon["extraAllowTools"] == [] and aragon["errors"] == []
        assert {"available", "versionOk", "canStructured", "degradedReason"} <= set(aragon)
        assert aragon["available"] == full["aragon"]["available"]


# ── 追问（AC-179） ──────────────────────────────────────────────────────────


def add_ask(maker, report_id: str, *, user_id: str | None, status: str = "done", mode: str = "cite") -> str:  # noqa: ANN001
    ask_id = ask_service.new_ask_id()
    with maker() as db:
        db.add(
            ReportAsk(
                id=ask_id,
                report_id=report_id,
                question="为什么",
                mode=mode,
                status=status,
                created_at=iso(datetime.now(UTC)),
                user_id=user_id,
            )
        )
        db.commit()
    return ask_id


def test_asks_belong_to_their_asker_and_strangers_get_404(world) -> None:  # noqa: ANN001
    report_id = add_report(world.maker, world.instrument, created_by=None)
    ask = add_ask(world.maker, report_id, user_id=world.records["alice"].id, status="running", mode="deep")
    legacy = add_ask(world.maker, report_id, user_id=None)

    for suffix, method in (("", "get"), ("/events-token", "post"), ("/cancel", "post")):
        mine = getattr(world.clients["alice"], method)(f"{API}/asks/{ask}{suffix}")
        assert mine.status_code == 200, suffix
        stranger = getattr(world.clients["bob"], method)(f"{API}/asks/{ask}{suffix}")
        assert stranger.status_code == 404 and code_of(stranger) == "not_found", suffix
        assert getattr(world.clients["boss"], method)(f"{API}/asks/{ask}{suffix}").status_code in (200, 409)
    assert world.clients["alice"].get(f"{API}/asks/{legacy}").status_code == 404, "历史追问仅管理员可见"
    assert world.clients["boss"].get(f"{API}/asks/{legacy}").status_code == 200
    assert world.guest.get(f"{API}/asks/{ask}").status_code == 404


def test_the_events_stream_trusts_the_token_not_a_cookie(world) -> None:  # noqa: ANN001
    """RV-5：EventSource 不带 Authorization，开发期跨源也不带 Cookie——拥有者拿着令牌无 Cookie 订阅。"""
    report_id = add_report(world.maker, world.instrument, created_by=None)
    ask = add_ask(world.maker, report_id, user_id=world.records["alice"].id, status="done", mode="deep")
    token = world.clients["alice"].post(f"{API}/asks/{ask}/events-token").json()["token"]
    cookieless = TestClient(world.app)
    bad = cookieless.get(f"{API}/asks/{ask}/events", params={"token": "garbage"})
    assert bad.status_code == 401
    other = add_ask(world.maker, report_id, user_id=world.records["alice"].id, status="done", mode="deep")
    wrong = cookieless.get(f"{API}/asks/{other}/events", params={"token": token})
    assert wrong.status_code == 401, "令牌绑定 ask_id"
    assert world.clients["bob"].post(f"{API}/asks/{ask}/events-token").status_code == 404, "他拿不到令牌"


def test_the_ask_list_is_only_mine_and_empty_for_guests(world) -> None:  # noqa: ANN001
    report_id = add_report(world.maker, world.instrument, created_by=None)
    mine = add_ask(world.maker, report_id, user_id=world.records["alice"].id)
    theirs = add_ask(world.maker, report_id, user_id=world.records["bob"].id)
    legacy = add_ask(world.maker, report_id, user_id=None)

    def ids(client) -> set[str]:  # noqa: ANN001
        return {item["id"] for item in client.get(f"{API}/reports/{report_id}/asks").json()["items"]}

    assert ids(world.clients["alice"]) == {mine}
    assert ids(world.clients["bob"]) == {theirs}
    assert ids(world.clients["boss"]) == {mine, theirs, legacy}
    response = world.guest.get(f"{API}/reports/{report_id}/asks")
    assert response.status_code == 200 and response.json() == {"items": [], "total": 0}


def test_cite_is_free_for_guests_and_never_touches_the_ledger(world) -> None:  # noqa: ANN001
    report_id = add_report(world.maker, world.instrument, created_by=None)
    asked = world.guest.post(f"{API}/reports/{report_id}/ask", json={"question": "估值如何", "mode": "cite"})
    assert asked.status_code == 200, asked.text
    mine = world.clients["alice"].post(f"{API}/reports/{report_id}/ask", json={"question": "估值如何", "mode": "cite"})
    assert mine.status_code == 200
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0
        owners = set(db.execute(select(ReportAsk.user_id)).scalars())
    assert owners == {None, world.records["alice"].id}


def test_deep_ask_needs_a_login_and_spends_the_deep_pool(world, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(ask_service, "ask_deep_available", lambda: True)
    monkeypatch.setattr(ask_service, "schedule_deep", lambda ask_id: None)
    report_id = add_report(world.maker, world.instrument, created_by=None)
    body = {"question": "再深入一点", "mode": "deep"}

    refused = world.guest.post(f"{API}/reports/{report_id}/ask", json=body)
    assert refused.status_code == 401 and code_of(refused) == "unauthorized"

    queued = world.clients["alice"].post(f"{API}/reports/{report_id}/ask", json=body)
    assert queued.status_code == 202, queued.text
    with world.maker() as db:
        ask = db.get(ReportAsk, queued.json()["askId"])
        assert ask.user_id == world.records["alice"].id
        (row,) = db.execute(select(QuotaLedger)).scalars()
        assert (row.resource, row.reason, row.ref_id) == ("deep_ask", "ask", ask.id)
    quota = world.clients["alice"].get(f"{API}/me/quota").json()
    assert quota["deepAsk"]["remaining"] == 9 and quota["report"]["remaining"] == 3


def test_deep_ask_over_the_deep_quota_is_429_and_leaves_no_ask_row(world, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(ask_service, "ask_deep_available", lambda: True)
    monkeypatch.setattr(ask_service, "schedule_deep", lambda ask_id: None)
    with world.maker() as db:
        db.get(User, world.records["alice"].id).quota_deep_daily_limit = 0
        db.commit()
    report_id = add_report(world.maker, world.instrument, created_by=None)
    refused = world.clients["alice"].post(f"{API}/reports/{report_id}/ask", json={"question": "再深入", "mode": "deep"})
    assert refused.status_code == 429 and code_of(refused) == "quota_exceeded"
    assert refused.json()["error"]["detail"]["resource"] == "deep_ask"
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(ReportAsk)).scalar_one() == 0


def test_the_per_report_daily_limit_counts_per_user(world, monkeypatch) -> None:  # noqa: ANN001
    from app.config import get_settings

    monkeypatch.setattr(ask_service, "ask_deep_available", lambda: True)
    monkeypatch.setattr(ask_service, "schedule_deep", lambda ask_id: None)
    monkeypatch.setattr(get_settings(), "ask_daily_limit", 1, raising=False)
    report_id = add_report(world.maker, world.instrument, created_by=None)
    body = {"question": "再深入一点", "mode": "deep"}
    assert world.clients["alice"].post(f"{API}/reports/{report_id}/ask", json=body).status_code == 202
    again = world.clients["alice"].post(f"{API}/reports/{report_id}/ask", json=body)
    assert again.status_code == 429 and code_of(again) == "too_many_asks"
    assert world.clients["bob"].post(f"{API}/reports/{report_id}/ask", json=body).status_code == 202, (
        "一个用户耗尽不了全站对某份报告的追问名额"
    )


# ── 自选：个人 / 精选 / 幂等播种（RV-15） ───────────────────────────────────


@pytest.fixture()
def featured(world):  # noqa: ANN001, ANN201
    other = Instrument(
        id="CN:000001.SZ",
        symbol="000001.SZ",
        name="平安银行",
        market="CN",
        currency="CNY",
        tz="Asia/Shanghai",
        created_at="2026-02-10T00:00:00Z",
        updated_at="2026-02-10T00:00:00Z",
    )
    with world.maker() as db:
        db.add(other)
        db.flush()
        db.add(
            WatchlistItem(instrument_id=world.instrument.id, sort_order=0, pinned=0, added_at="2026-02-10T00:00:00Z")
        )
        db.add(WatchlistItem(instrument_id=other.id, sort_order=1, pinned=0, added_at="2026-02-10T00:00:01Z"))
        db.commit()
    return [world.instrument.id, other.id]


def test_guests_see_the_featured_list_read_only(world, featured) -> None:  # noqa: ANN001
    response = world.guest.get(f"{API}/watchlist")
    assert response.status_code == 200 and response.json()["scope"] == "featured"
    assert [item["instrument"]["id"] for item in response.json()["items"]] == featured
    assert response.headers["cache-control"] == "private, no-store"
    for method, url, body in (
        ("post", f"{API}/watchlist", {"instrumentId": featured[0]}),
        ("delete", f"{API}/watchlist/{featured[0]}", None),
        ("patch", f"{API}/watchlist/reorder", {"ids": featured}),
    ):
        refused = getattr(world.guest, method)(url, **({"json": body} if body else {}))
        assert refused.status_code == 401, method


def test_a_new_user_is_seeded_once_from_the_featured_list(world, featured) -> None:  # noqa: ANN001
    alice = world.clients["alice"]
    first = alice.get(f"{API}/watchlist").json()
    assert first["scope"] == "personal"
    assert [item["instrument"]["id"] for item in first["items"]] == featured
    assert alice.delete(f"{API}/watchlist/{featured[0]}").status_code == 204
    second = alice.get(f"{API}/watchlist").json()
    assert [item["instrument"]["id"] for item in second["items"]] == [featured[1]], "旗标已置：不会重新播种"

    bob = world.clients["bob"].get(f"{API}/watchlist").json()
    assert [item["instrument"]["id"] for item in bob["items"]] == featured, "每个用户各自一份"
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(WatchlistItem)).scalar_one() == 2, "精选本身不动"


def test_personal_writes_never_touch_the_featured_table(world, featured) -> None:  # noqa: ANN001
    alice = world.clients["alice"]
    alice.get(f"{API}/watchlist")
    assert alice.delete(f"{API}/watchlist/{featured[1]}").status_code == 204
    assert alice.delete(f"{API}/watchlist/{featured[1]}").status_code == 404
    added = alice.post(f"{API}/watchlist", json={"instrumentId": featured[1]})
    assert added.status_code == 201 and added.json()["scope"] == "personal"
    reordered = alice.patch(f"{API}/watchlist/reorder", json={"ids": [featured[1], featured[0]]})
    assert [item["instrument"]["id"] for item in reordered.json()["items"]] == [featured[1], featured[0]]
    assert alice.patch(f"{API}/watchlist/reorder", json={"ids": ["CN:nope"]}).status_code == 404
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(WatchlistItem)).scalar_one() == 2
        assert db.execute(select(func.count()).select_from(UserWatchlistItem)).scalar_one() == 2
    assert [i["instrument"]["id"] for i in world.guest.get(f"{API}/watchlist").json()["items"]] == featured


def test_two_simultaneous_first_reads_seed_exactly_once(world, featured, engine) -> None:  # noqa: ANN001
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    user_id = world.records["alice"].id
    barrier = threading.Barrier(8)
    results: list[bool] = []
    guard = threading.Lock()

    def first_read() -> None:
        db = factory()
        try:
            barrier.wait()
            seeded = watchlist_service.ensure_seeded(db, user_id)
            with guard:
                results.append(seeded)
        finally:
            db.close()

    threads = [threading.Thread(target=first_read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert results.count(True) == 1, "只有 INSERT 旗标成功的那一个请求才播种"
    with world.maker() as db:
        assert db.execute(select(func.count()).select_from(UserWatchlistItem)).scalar_one() == 2


def test_open_mode_keeps_editing_the_legacy_table(client, instrument) -> None:  # noqa: ANN001
    """`AUTH_MODE=open`（单人本地模式）的 service 主体没有 user_id：行为与改造前逐字相同。"""
    added = client.post(f"{API}/watchlist", json={"instrumentId": instrument.id})
    assert added.status_code == 201 and added.json()["scope"] == "featured"
    assert [i["instrument"]["id"] for i in client.get(f"{API}/watchlist").json()["items"]] == [instrument.id]
    assert client.delete(f"{API}/watchlist/{instrument.id}").status_code == 204


# ── 定时刷新：仅管理员，system 不扣额度（AC-192） ───────────────────────────


def test_schedules_are_admin_only(world) -> None:  # noqa: ANN001
    payload = {"instrumentId": world.instrument.id, "cron": "0 9 * * 1-5"}
    assert world.guest.post(f"{API}/schedules", json=payload).status_code == 401
    assert world.clients["alice"].post(f"{API}/schedules", json=payload).status_code == 403
    assert world.clients["alice"].get(f"{API}/schedules").status_code == 403
    created = world.clients["boss"].post(f"{API}/schedules", json=payload)
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]
    patched = world.clients["boss"].patch(f"{API}/schedules/{schedule_id}", json={"enabled": False})
    assert patched.status_code == 200
    assert world.clients["boss"].delete(f"{API}/schedules/{schedule_id}").status_code == 204
    with world.maker() as db:
        actions = [row.action for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()]
    schedule_actions = [action for action in actions if action.startswith("schedule.")]
    assert schedule_actions == ["schedule.create", "schedule.update", "schedule.delete"]


def test_a_schedule_remembers_its_owner_and_the_scheduled_run_costs_no_quota(world, engine, monkeypatch) -> None:  # noqa: ANN001
    from app.db import session as session_module
    from app.services import run_manager as run_manager_module
    from app.services.scheduler_ticker import SchedulerTicker

    boss = world.records["boss"]
    created = world.clients["boss"].post(
        f"{API}/schedules", json={"instrumentId": world.instrument.id, "cron": "*/15 * * * *"}
    )
    schedule_id = created.json()["id"]
    with world.maker() as db:
        row = db.get(Schedule, schedule_id)
        assert row.owner_id == boss.id
        row.next_run_at = iso(datetime.now(UTC) - timedelta(minutes=30))
        db.commit()

    monkeypatch.setattr(session_module, "SessionLocal", world.maker)

    async def scenario() -> int:
        started = await SchedulerTicker(tick_seconds=3600).tick_once()
        manager = run_manager_module.get_manager()
        for _ in range(200):
            if not manager._tasks:  # noqa: SLF001
                break
            await asyncio.sleep(0.01)
        for task in list(manager._tasks.values()):  # noqa: SLF001
            task.cancel()
        if manager._tasks:  # noqa: SLF001
            await asyncio.gather(*manager._tasks.values(), return_exceptions=True)
        return started

    assert asyncio.run(scenario()) == 1
    run_manager_module.reset_manager()
    with world.maker() as db:
        run = db.execute(select(Run).where(Run.reason == "scheduled")).scalar_one()
        assert run.created_by == boss.id, "定时 run 记创建者"
        assert db.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0, "system 不扣额度"


# ── 成本 / 探针：权限（AC-174） ─────────────────────────────────────────────


def test_cost_and_probe_are_admin_surfaces(world) -> None:  # noqa: ANN001
    for path in ("/cost/summary", "/cost/entries", "/agent/probe"):
        assert world.guest.get(f"{API}{path}").status_code == 401, path
        assert world.clients["alice"].get(f"{API}{path}").status_code == 403, path
        assert world.clients["boss"].get(f"{API}{path}").status_code == 200, path
    assert world.clients["boss"].post(f"{API}/cost/budget", json={"monthlyUsd": 10}).status_code == 403, "预算只给 root"
    assert world.clients["alice"].post(f"{API}/agent/probe", json={}).status_code == 403


def test_the_symbol_resolver_stays_public(world) -> None:  # noqa: ANN001
    response = world.guest.post(f"{API}/symbols/resolve", json={"query": "600519"})
    assert response.status_code != 401 and response.status_code != 403


def test_the_generated_report_is_authored_by_the_runs_owner_end_to_end(world) -> None:  # noqa: ANN001
    """完整链路：user 点生成 → mock 引擎跑完 → 报告 `author` 是他、`canManage` 对他为真。"""
    import time

    alice = world.clients["alice"]
    run_id = alice.post(f"{API}/instruments/{world.instrument.id}/reports", json={}).json()["runId"]
    deadline = time.time() + 90
    body: dict = {}
    while time.time() < deadline:
        body = alice.get(f"{API}/runs/{run_id}").json()
        if body["status"] in ("succeeded", "failed", "cancelled", "timeout", "budget_exceeded"):
            break
        time.sleep(0.25)
    assert body["status"] == "succeeded", body
    report_id = body["reportId"]
    summary = alice.get(f"{API}/reports/{report_id}").json()["report"]
    assert summary["author"] == "alice" and summary["canManage"] is True and summary["canCurate"] is False
    assert world.clients["bob"].get(f"{API}/reports/{report_id}").json()["report"]["canManage"] is False
    with world.maker() as db:
        rows = list(db.execute(select(QuotaLedger)).scalars())
    assert [row.reason for row in rows] == ["run"], "成功的 run 不退款"
    assert alice.get(f"{API}/me/quota").json()["report"]["remaining"] == 2



def _set_latest(maker, report_id: str) -> None:  # noqa: ANN001
    with maker() as db:
        db.get(Report, report_id).is_latest = True
        db.commit()


def _flags(maker, report_id: str) -> tuple[bool, bool, bool, str]:  # noqa: ANN001
    with maker() as db:
        row = db.get(Report, report_id)
        return bool(row.is_latest), bool(row.archived), bool(row.pinned), row.status


def test_q9_owner_archiving_the_latest_hands_the_pointer_to_the_previous_version(world) -> None:  # noqa: ANN001
    """AC-213（D-11）：用户归档自己的最新版 → 后继者成为 `is_latest`，该标的不会没有首页。"""
    older = add_report(world.maker, world.instrument, created_by=world.records["alice"].id, version=1)
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id, version=2)
    _set_latest(world.maker, mine)
    response = world.clients["alice"].patch(f"{API}/reports/{mine}", json={"archived": True})
    assert response.status_code == 200, response.text
    assert _flags(world.maker, mine)[:2] == (False, True)
    assert _flags(world.maker, older)[0] is True
    assert _flags(world.maker, older)[3] == "ready"
    with world.maker() as db:
        latest = db.execute(
            select(Report.id).where(Report.instrument_id == world.instrument.id, Report.is_latest == 1)
        ).scalars().all()
    assert latest == [older]


def test_q9_archiving_the_only_version_is_refused_and_changes_nothing(world) -> None:  # noqa: ANN001
    """AC-213：唯一可用版本 → `403 forbidden` + `detail.reason == 'only_version'`，库里无任何变化。"""
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id)
    _set_latest(world.maker, mine)
    before = _flags(world.maker, mine)
    refused = world.clients["alice"].patch(f"{API}/reports/{mine}", json={"archived": True})
    assert refused.status_code == 403
    assert code_of(refused) == "forbidden"
    assert refused.json()["error"]["detail"]["reason"] == "only_version"
    assert _flags(world.maker, mine) == before
    # 管理员（manage_any）保持既有行为：可以让标的"没有首页"。
    assert world.clients["boss"].patch(f"{API}/reports/{mine}", json={"archived": True}).status_code == 200
    assert _flags(world.maker, mine)[:2] == (False, True)


def _four_versions(world, owner: str) -> dict[str, str]:  # noqa: ANN001
    """AC-244 的夹具：v4 ready 最新、v3 ready、v2 archived、v1 deleted，外加 v5 provisional。"""
    ids = {
        "v1": add_report(world.maker, world.instrument, created_by=owner, version=1, deleted=True),
        "v2": add_report(world.maker, world.instrument, created_by=owner, version=2),
        "v3": add_report(world.maker, world.instrument, created_by=owner, version=3),
        "v4": add_report(world.maker, world.instrument, created_by=owner, version=4),
        "v5": add_report(world.maker, world.instrument, created_by=owner, version=5),
    }
    with world.maker() as db:
        db.get(Report, ids["v2"]).archived = True
        db.get(Report, ids["v5"]).status = "provisional"
        db.get(Report, ids["v4"]).is_latest = True
        db.commit()
    return ids


def test_archive_and_delete_pick_the_same_successor(world, engine) -> None:  # noqa: ANN001
    """AC-244（RV-Y10）：同一份数据，"归档最新版"与"删除最新版"选出同一个后继者（v3），无 IntegrityError。"""
    owner = world.records["alice"].id
    ids = _four_versions(world, owner)
    assert world.clients["alice"].patch(f"{API}/reports/{ids['v4']}", json={"archived": True}).status_code == 200
    archived_pick = {name: _flags(world.maker, rid)[0] for name, rid in ids.items()}
    assert archived_pick == {"v1": False, "v2": False, "v3": True, "v4": False, "v5": False}

    # 用同一标的重新造一份夹具再删除（清空上一份，避免版本号撞车）。
    with world.maker() as db:
        db.execute(Report.__table__.delete())
        db.commit()
    ids = _four_versions(world, owner)
    assert world.clients["alice"].delete(f"{API}/reports/{ids['v4']}").status_code == 204
    deleted_pick = {name: _flags(world.maker, rid)[0] for name, rid in ids.items()}
    assert deleted_pick == archived_pick


def test_a_patch_mixing_archive_and_pin_fails_atomically_for_an_owner(world) -> None:  # noqa: ANN001
    """AC-244：非 manage_any 的 `{archived, pinned}` → 403，`archived/pinned/is_latest` 三者都未变。"""
    older = add_report(world.maker, world.instrument, created_by=world.records["alice"].id, version=1)
    mine = add_report(world.maker, world.instrument, created_by=world.records["alice"].id, version=2)
    _set_latest(world.maker, mine)
    before = (_flags(world.maker, mine), _flags(world.maker, older))
    refused = world.clients["alice"].patch(f"{API}/reports/{mine}", json={"archived": True, "pinned": True})
    assert refused.status_code == 403
    assert (_flags(world.maker, mine), _flags(world.maker, older)) == before
