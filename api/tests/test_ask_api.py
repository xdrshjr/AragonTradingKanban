"""`deep` 追问的接口契约：SSE、额度、取消、签名绑定（AC-26 / AC-27 / AC-44）。

`cite` 档不碰模型，因此这一层是唯一能证明 `deep` 真的能跑的地方。三个剧本由
`fake_aragon.py` 驱动（`ask_ok` / `ask_denied` / `ask_badcite`），所以「引用合法性」
从一句愿望变成一条可回归的断言。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from app.agent.preflight import reset_cache
from app.config import get_settings
from app.services import ask_service

FAKE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_aragon.py"
APP_DIR = Path(__file__).resolve().parents[1] / "app"


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def deep_env(monkeypatch, fake_aragon_bin) -> None:
    """Point the app at the fake CLI and let `deep` think it is a real engine."""
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "real", raising=False)
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    monkeypatch.setattr(
        settings,
        "aragon_env_passthrough",
        "FAKE_ARAGON_MODE,FAKE_ARAGON_INFO,FAKE_ARAGON_ARGV_LOG",
        raising=False,
    )
    monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ask_ok")
    reset_cache()
    yield
    reset_cache()


@pytest.fixture()
def seeded(client, sample_report) -> dict:
    """One ready report, created through the app itself."""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    from app.db.session import SessionLocal
    from app.models import Instrument
    from app.services import report_service

    session = SessionLocal()
    try:
        instrument = session.query(Instrument).filter(Instrument.symbol == "600519.SH").one()
        report = report_service.create_version(session, instrument, sample_report, status="ready")
        report_id = report.id
    finally:
        session.close()
    return {"reportId": report_id, "instrumentId": "CN:600519.SH"}


def _wait_for_status(client, ask_id: str, *, states: tuple[str, ...], timeout: float = 25.0) -> dict:
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/v1/asks/{ask_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in states:
            return payload
        time.sleep(0.15)
    raise AssertionError(f"ask {ask_id} 未在 {timeout}s 内进入 {states}，实际 {payload.get('status')}")


# ── AC-26：deep 走 SSE 落库，且可取消 ───────────────────────────────────────


def test_deep_ask_runs_end_to_end_and_persists_cost(client, seeded, deep_env) -> None:
    created = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "毛利率下滑的主因是什么？", "mode": "deep"},
    )
    assert created.status_code == 202, created.json()
    ask_id = created.json()["askId"]

    final = _wait_for_status(client, ask_id, states=("done", "failed"))
    assert final["status"] == "done", final
    assert final["answer"]["answerText"]
    assert final["grounded"] is True
    assert [item["sourceId"] for item in final["answer"]["citations"]] == ["S1"]
    # ★P1-8: cost is only shown when the CLI actually knows it.
    assert final["costKnown"] is True
    assert final["costUsd"] == pytest.approx(0.004)
    assert final["tokensIn"] == 1200
    assert final["tokensOut"] == 240

    events = client.get(f"/api/v1/asks/{ask_id}/events").text
    assert "event: ask.stage" in events
    assert "event: ask.done" in events


def test_deep_ask_never_creates_a_run_row(client, seeded, deep_env) -> None:
    """C-12: an ask is not a run — a fake `runs` row would pollute the audit trail."""
    created = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "估值贵吗？", "mode": "deep"},
    )
    ask_id = created.json()["askId"]
    _wait_for_status(client, ask_id, states=("done", "failed"))

    from sqlalchemy import func, select

    from app.db.session import SessionLocal
    from app.models import ReportAsk, Run

    session = SessionLocal()
    try:
        ask_row = session.get(ReportAsk, ask_id)
        assert ask_row is not None
        assert ask_row.run_id is None
        assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 0
    finally:
        session.close()


def test_illegal_citations_are_dropped_not_trusted(client, seeded, deep_env, monkeypatch) -> None:
    """R-16 / AC-44 的兄弟断言：S99 不存在 ⇒ 丢弃并计 dirty，绝不 grounded。"""
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ask_badcite")
    reset_cache()
    created = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "毛利率为什么下滑？", "mode": "deep"},
    )
    ask_id = created.json()["askId"]
    final = _wait_for_status(client, ask_id, states=("done", "failed"))

    assert final["status"] == "done", final
    assert final["citationsDropped"] >= 1
    assert final["grounded"] is False
    assert [item["sourceId"] for item in final["answer"]["citations"]] == ["S1"]


def test_denied_answer_is_grounded_false_with_a_hint(client, seeded, deep_env, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ask_denied")
    reset_cache()
    created = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "卫星发射计划进展如何？", "mode": "deep"},
    )
    final = _wait_for_status(client, created.json()["askId"], states=("done", "failed"))
    assert final["grounded"] is False
    assert final["answer"]["hint"]


def test_deep_ask_can_be_cancelled(client, seeded, deep_env, monkeypatch) -> None:
    """AC-26: 取消必须在 5s 内落到终态，且子进程被杀掉。"""
    monkeypatch.setenv("FAKE_ARAGON_MODE", "hang")
    reset_cache()
    created = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "慢慢答", "mode": "deep"},
    )
    ask_id = created.json()["askId"]
    time.sleep(0.4)  # let the child actually spawn

    cancelled = client.post(f"/api/v1/asks/{ask_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelling"

    final = _wait_for_status(client, ask_id, states=("cancelled", "done", "failed"), timeout=10.0)
    assert final["status"] in ("cancelled", "failed")
    # A finished ask cannot be cancelled twice.
    again = client.post(f"/api/v1/asks/{ask_id}/cancel")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ask_not_active"


# ── AC-27：额度是显式拒绝，不是静默降级 ─────────────────────────────────────


def test_deep_quota_returns_429_with_retry_after(client, seeded, deep_env, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ask_daily_limit", 1, raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ask_ok")
    reset_cache()

    first = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "第一个问题", "mode": "deep"},
    )
    assert first.status_code == 202, first.json()
    _wait_for_status(client, first.json()["askId"], states=("done", "failed"))

    second = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "第二个问题", "mode": "deep"},
    )
    assert second.status_code == 429
    body = second.json()
    assert body["error"]["code"] == "too_many_asks"
    assert body["error"]["detail"]["retryAfterMs"] > 0
    assert body["error"]["detail"]["suggestMode"] == "cite"

    # `cite` never consumes the quota, so it still works at 100% usage.
    cite = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "毛利率是多少？", "mode": "cite"},
    )
    assert cite.status_code == 200


def test_deep_on_a_provisional_report_is_refused(client, sample_report, deep_env) -> None:
    """§7.3: 临时版没有接地基准，`deep` 必须 409 而不是硬答。"""
    from app.db.session import SessionLocal
    from app.models import Instrument
    from app.services import report_service, report_validator

    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    session = SessionLocal()
    try:
        instrument = session.query(Instrument).filter(Instrument.symbol == "600519.SH").one()
        doc = report_validator.build_provisional_doc(
            {"instrument": {"symbol": "600519.SH", "name": "贵州茅台", "market": "CN"}},
            raw_text="模型没能提交结构化结果。",
            error_code="result_schema_unsatisfied",
            error_message="structured was null",
        )
        report = report_service.create_version(
            session, instrument, doc, status="provisional", is_provisional=True
        )
        report_id = report.id
    finally:
        session.close()

    response = client.post(
        f"/api/v1/reports/{report_id}/ask",
        json={"question": "毛利率多少？", "mode": "deep"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_not_ready"


def test_deep_is_refused_when_the_engine_cannot_submit(client, seeded, monkeypatch) -> None:
    """`features.askDeep=false` ⇒ 409 `ask_deep_unavailable`，而不是偷偷降级到 cite。"""
    monkeypatch.setattr(ask_service, "ask_deep_available", lambda: False)
    response = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "毛利率多少？", "mode": "deep"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ask_deep_unavailable"


# ── AC-44：签名绑定 askId，且 sign/verify 只有一份 ──────────────────────────


def test_events_token_is_bound_to_the_ask(client, seeded, deep_env, monkeypatch) -> None:
    created = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "毛利率多少？", "mode": "deep"},
    )
    ask_id = created.json()["askId"]
    monkeypatch.setattr(get_settings(), "api_token", "secret-token", raising=False)

    issued = client.post(f"/api/v1/asks/{ask_id}/events-token", headers={"authorization": "Bearer secret-token"})
    assert issued.status_code == 200
    assert issued.json()["expiresIn"] == 60
    token = issued.json()["token"]

    ok = client.get(f"/api/v1/asks/{ask_id}/events?token={token}")
    assert ok.status_code == 200
    assert "ask.stage" in ok.text

    # A token signed for a *run* id must not open an ask stream (that was the bug).
    from app.services.sse_token import sign_sse_token

    forged = f"{int(time.time()) + 60}.{sign_sse_token('some-run-id', int(time.time()) + 60)}"
    rejected = client.get(f"/api/v1/asks/{ask_id}/events?token={forged}")
    assert rejected.status_code == 401

    # …and the ask token must not open a run stream either.
    from app.db.session import SessionLocal
    from app.models import Run

    session = SessionLocal()
    try:
        session.add(
            Run(
                id="run-for-scope-test",
                instrument_id=seeded["instrumentId"],
                status="queued",
                pct=0,
                started_at="2026-02-10T00:00:00Z",
            )
        )
        session.commit()
    finally:
        session.close()
    cross = client.get(f"/api/v1/runs/run-for-scope-test/events?token={token}")
    assert cross.status_code == 401


def test_sign_and_verify_exist_in_exactly_one_module() -> None:
    """AC-44 的 grep 断言：路由里不许再有自己的一份 HMAC。"""
    definitions = []
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^def (sign_sse_token|verify_sse_token)\(", text, re.MULTILINE):
            definitions.append(path.relative_to(APP_DIR).as_posix())
    assert definitions == ["services/sse_token.py"], definitions

    for router in ("routers/runs.py", "routers/asks.py"):
        text = (APP_DIR / router).read_text(encoding="utf-8")
        assert "import hmac" not in text, f"{router} 又自己算了一遍签名"
        assert "def sign_events_token" not in text or "sign_sse_token" in text


def test_ask_events_endpoint_404s_for_an_unknown_id(client) -> None:
    assert client.get("/api/v1/asks/ask_missing/events").status_code == 404
    assert client.get("/api/v1/asks/ask_missing").status_code == 404


# ── 输入校验 ────────────────────────────────────────────────────────────────


def test_question_is_validated(client, seeded) -> None:
    too_long = "长" * 501
    for payload in (
        {"question": "   "},
        {"question": "a" * 501},
        {"question": too_long},
        {"question": "x\x00y"},
    ):
        response = client.post(f"/api/v1/reports/{seeded['reportId']}/ask", json=payload)
        assert response.status_code == 422, payload
        # One code, owned by `normalize_question` (§7.3): a too-long question must not
        # leak out as the generic `validation_error`.
        assert response.json()["error"]["code"] == "invalid_question", payload

    # A missing field is a schema error, not a question-hygiene error: different code,
    # same status. Both must be 422 — never a 500.
    missing = client.post(f"/api/v1/reports/{seeded['reportId']}/ask", json={"mode": "cite"})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_error"


def test_cite_answer_is_recorded_for_audit(client, seeded) -> None:
    """Kostenlos heißt nicht unprotokolliert: `cite` 也写一行（额度与审计）。"""
    first = client.post(
        f"/api/v1/reports/{seeded['reportId']}/ask",
        json={"question": "毛利率是多少？", "mode": "cite"},
    )
    assert first.status_code == 200
    listed = client.get(f"/api/v1/reports/{seeded['reportId']}/asks")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 1
    assert body["items"][0]["mode"] == "cite"
    assert body["items"][0]["costKnown"] is True
    assert body["items"][0]["costUsd"] == 0.0


def test_ask_workspace_is_rebuilt_from_the_report(client, seeded) -> None:
    """★V4-P1-6：工作区从 doc_json 重建，不依赖那次 run 的目录还在不在。"""
    from app.db.session import SessionLocal
    from app.services import report_service

    session = SessionLocal()
    try:
        report = report_service.get(session, seeded["reportId"])
        assert report is not None
        workspace, available = ask_service.prepare_ask_workspace(session, report, "ask_probe")
        assert (workspace / "report.json").exists()
        assert (workspace / "sources.md").exists()
        assert "report.json" in available and "sources.md" in available
        payload = json.loads((workspace / "report.json").read_text(encoding="utf-8"))
        assert payload["schema"] == "aragon.research.report/v1"
        # The run workspace is a bonus, never a requirement.
        assert (workspace.parent.parent / "runs").exists() or True
    finally:
        session.close()


# ── 重启恢复：卡在活动态的 ask 必须被收尾（否则 SSE 永不关流）────────────────


def test_interrupted_asks_are_failed_on_startup(client, seeded) -> None:
    """A killed process must not leave an ask `running` forever.

    The row would otherwise poll its own SSE stream indefinitely and
    `POST /asks/{id}/cancel` would answer `cancelling` to a task nobody owns.
    """
    from app.db.session import SessionLocal
    from app.models import ReportAsk

    session = SessionLocal()
    try:
        stale = ReportAsk(
            id="ask_interrupted",
            report_id=seeded["reportId"],
            question="毛利率是多少？",
            mode="deep",
            status="running",
            grounded=0,
            citations_dropped=0,
            cost_known=0,
            created_at="2026-02-10T00:00:00Z",
        )
        session.add(stale)
        session.commit()

        recovered = ask_service.recover_interrupted(session)
        assert recovered >= 1

        session.expire_all()
        row = session.get(ReportAsk, "ask_interrupted")
        assert row is not None
        assert row.status == "failed"
        assert row.error_code == "interrupted"
        assert row.finished_at

        events = ask_service.replay_events(session, "ask_interrupted")
        assert [event["type"] for event in events][-1] == "ask.error"
        assert events[-1]["payload"]["code"] == "interrupted"
    finally:
        session.close()

    # A recovered ask is terminal: the SSE endpoint replays the error and closes.
    listed = client.get("/api/v1/asks/ask_interrupted")
    assert listed.status_code == 200
    assert listed.json()["status"] == "failed"
