"""REST contract: status codes, shapes, error envelope, soft delete, SSE auth.

Covers AC-1, AC-5, AC-13, AC-20 (soft delete) and AC-22 (SSE token).
"""

from __future__ import annotations

import pytest

from app.services import report_service

# ── health & capabilities ────────────────────────────────────────────────


def test_health_reports_db_ok(client) -> None:
    """AC-1."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["version"]


def test_capabilities_shape(client) -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["agentMode"] in ("real", "mock")
    assert "available" in body["aragon"]
    assert isinstance(body["dataProviders"], list)
    assert body["features"]["pdf"] is False
    assert body["features"]["ask"] is False
    assert body["limits"]["maxConcurrentRuns"] == 2


def test_root_endpoint(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"]


# ── symbols & instruments ────────────────────────────────────────────────


def test_symbol_resolve_creates_and_returns_instrument(client) -> None:
    response = client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    assert response.status_code == 200
    body = response.json()
    assert body["instrument"]["symbol"] == "600519.SH"
    assert body["created"] is True


def test_symbol_resolve_is_idempotent(client) -> None:
    first = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()
    second = client.post("/api/v1/symbols/resolve", json={"query": "600519.SH"}).json()
    assert first["instrument"]["id"] == second["instrument"]["id"]
    assert second["created"] is False


def test_symbol_resolve_rejects_empty_query(client) -> None:
    response = client.post("/api/v1/symbols/resolve", json={"query": ""})
    assert response.status_code == 422


def test_symbol_resolve_unknown_name_is_404(client) -> None:
    response = client.post("/api/v1/symbols/resolve", json={"query": "zzzqqq不存在"})
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "instrument_not_found"
    assert "requestId" in body["error"]


def test_symbol_search_requires_a_query(client) -> None:
    assert client.get("/api/v1/symbols/search?q=").json()["hits"] == []


def test_symbol_search_returns_hits(client) -> None:
    body = client.get("/api/v1/symbols/search?q=茅台").json()
    assert any(hit["symbol"] == "600519.SH" for hit in body["hits"])


def test_instruments_batch_lookup(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    body = client.get("/api/v1/instruments?ids=CN:600519.SH").json()
    assert body["items"][0]["symbol"] == "600519.SH"


def test_instruments_missing_id_is_404(client) -> None:
    response = client.get("/api/v1/instruments?ids=CN:NOPE.SH")
    assert response.status_code == 404


def test_bars_endpoint_returns_bars_and_indicators(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    response = client.get("/api/v1/instruments/CN:600519.SH/bars?timeframe=1d&range=3m&adjust=qfq")
    assert response.status_code == 200
    body = response.json()
    assert body["bars"]
    assert body["meta"]["barsCount"] == len(body["bars"])
    assert "ma5" in body["indicators"]
    first = body["bars"][0]
    assert {"ts", "o", "h", "l", "c", "v"} <= set(first)


def test_bars_rejects_an_invalid_timeframe(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    assert client.get("/api/v1/instruments/CN:600519.SH/bars?timeframe=5m").status_code == 422


def test_quote_endpoint(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    response = client.get("/api/v1/instruments/CN:600519.SH/quote")
    assert response.status_code == 200
    body = response.json()
    assert body["instrumentId"] == "CN:600519.SH"
    assert body["last"] is not None


# ── reports ──────────────────────────────────────────────────────────────


@pytest.fixture()
def seeded_report(client, engine, sample_report):
    """One instrument with two versions, straight through the service layer."""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        instrument = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()["instrument"]
        from app.models import Instrument

        row = session.get(Instrument, instrument["id"])
        first = report_service.create_version(
            session, row, sample_report, status="ready", quality={"groundedRatio": 0.95}
        )
        second = report_service.create_version(
            session, row, sample_report, status="ready", quality={"groundedRatio": 0.97}
        )
        return {"instrument": instrument, "first": first.id, "second": second.id}
    finally:
        session.close()


def test_reports_list_for_instrument(client, seeded_report) -> None:
    body = client.get(f"/api/v1/instruments/{seeded_report['instrument']['id']}/reports").json()
    assert body["total"] == 2
    assert body["items"][0]["isLatest"] is True
    assert {item["version"] for item in body["items"]} == {1, 2}


def test_report_detail_returns_the_document(client, seeded_report, sample_report) -> None:
    body = client.get(f"/api/v1/reports/{seeded_report['second']}").json()
    assert body["report"]["version"] == 2
    assert body["doc"]["schema"] == "aragon.report/v1".replace("aragon.report", "aragon.research.report")
    assert body["doc"]["sections"]


def test_report_not_found(client) -> None:
    response = client.get("/api/v1/reports/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_not_found"


def test_latest_report_endpoint(client, seeded_report) -> None:
    body = client.get(f"/api/v1/instruments/{seeded_report['instrument']['id']}/reports/latest").json()
    assert body["report"]["id"] == seeded_report["second"]


def test_patch_report_pins_and_archives(client, seeded_report) -> None:
    pinned = client.patch(f"/api/v1/reports/{seeded_report['first']}", json={"pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True

    archived = client.patch(f"/api/v1/reports/{seeded_report['first']}", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert archived.json()["isLatest"] is False


def test_patch_set_latest_moves_the_pointer(client, seeded_report) -> None:
    response = client.patch(f"/api/v1/reports/{seeded_report['first']}", json={"setLatest": True})
    assert response.status_code == 200
    assert response.json()["isLatest"] is True
    latest = client.get(f"/api/v1/instruments/{seeded_report['instrument']['id']}/reports/latest").json()
    assert latest["report"]["id"] == seeded_report["first"]


def test_delete_is_soft_and_returns_204(client, seeded_report, engine) -> None:
    """AC-20: 204, the row survives with `deleted_at`, latest moves to v1."""
    response = client.delete(f"/api/v1/reports/{seeded_report['second']}")
    assert response.status_code == 204
    assert response.content == b""

    listed = client.get(f"/api/v1/instruments/{seeded_report['instrument']['id']}/reports").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == seeded_report["first"]
    assert listed["items"][0]["isLatest"] is True

    from app.db.session import SessionLocal
    from app.models import Report

    session = SessionLocal()
    try:
        row = session.get(Report, seeded_report["second"])
        assert row is not None and row.deleted_at is not None
    finally:
        session.close()


def test_delete_missing_report_is_404(client) -> None:
    assert client.delete("/api/v1/reports/missing").status_code == 404


def test_export_html_is_an_attachment(client, seeded_report) -> None:
    response = client.get(f"/api/v1/reports/{seeded_report['second']}/export?format=html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert ".html" in response.headers["content-disposition"]
    assert "<script" not in response.text.lower()


def test_export_markdown_is_an_attachment(client, seeded_report) -> None:
    response = client.get(f"/api/v1/reports/{seeded_report['second']}/export?format=md")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert response.text.lstrip().startswith("#")


def test_export_pdf_is_501_with_guidance(client, seeded_report) -> None:
    response = client.get(f"/api/v1/reports/{seeded_report['second']}/export?format=pdf")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "export_unsupported"


def test_ask_endpoint_is_reserved_with_501(client, seeded_report) -> None:
    response = client.post(f"/api/v1/reports/{seeded_report['second']}/ask")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "ask_not_implemented"


# ── runs ─────────────────────────────────────────────────────────────────


def test_create_report_returns_202_with_a_run_id(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    response = client.post("/api/v1/instruments/CN:600519.SH/reports", json={})
    assert response.status_code == 202
    body = response.json()
    assert body["runId"]
    assert body["status"] == "queued"


def test_run_endpoint_shape(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_id = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]
    body = client.get(f"/api/v1/runs/{run_id}").json()
    assert body["id"] == run_id
    assert body["status"] in ("queued", "running", "cancelling", "succeeded", "failed")
    assert "costKnown" in body
    assert "usage" in body


def test_run_not_found(client) -> None:
    assert client.get("/api/v1/runs/nope").status_code == 404


def test_cancel_unknown_run_is_404(client) -> None:
    assert client.post("/api/v1/runs/nope/cancel").status_code == 404


def test_duplicate_run_conflicts_409(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    first = client.post("/api/v1/instruments/CN:600519.SH/reports", json={})
    assert first.status_code == 202
    second = client.post("/api/v1/instruments/CN:600519.SH/reports", json={})
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "run_conflict"
    assert body["error"]["detail"]["activeRunId"] == first.json()["runId"]


# ── SSE (AC-22) ──────────────────────────────────────────────────────────


def test_sse_without_token_is_rejected_when_api_token_is_set(client, monkeypatch) -> None:
    """AC-22: `EventSource` cannot send headers, so the query token is enforced."""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_id = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "api_token", "secret-token", raising=False)

    response = client.get(f"/api/v1/runs/{run_id}/events")
    assert response.status_code == 401


def test_sse_token_is_accepted(client, monkeypatch) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_id = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "api_token", "secret-token", raising=False)
    # Minting a token is itself authenticated; only the SSE endpoint accepts the
    # short-lived `?token=` instead of a header (★P1-3).
    issued = client.post(f"/api/v1/runs/{run_id}/events-token", headers={"authorization": "Bearer secret-token"})
    assert issued.status_code == 200
    token = issued.json()["token"]
    assert token
    assert issued.json()["expiresIn"] == 60

    with client.stream("GET", f"/api/v1/runs/{run_id}/events?token={token}") as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers["content-type"]
        assert stream.headers["x-accel-buffering"] == "no"
        assert "no-transform" in stream.headers["cache-control"]
        first = next(stream.iter_lines())
        assert first.startswith("id:") or first.startswith("event:") or first.strip() == ""


def test_events_token_is_bound_to_the_run(client, monkeypatch) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_a = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]

    from app.config import get_settings
    from app.routers.runs import sign_events_token

    monkeypatch.setattr(get_settings(), "api_token", "secret-token", raising=False)
    forged = f"{9999999999}.{sign_events_token('a-different-run', 9999999999)}"
    response = client.get(f"/api/v1/runs/{run_a}/events?token={forged}")
    assert response.status_code == 401


def test_sse_token_endpoint_404s_for_unknown_run(client) -> None:
    assert client.post("/api/v1/runs/nope/events-token").status_code == 404


def test_sse_replays_stage_events(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_id = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]

    with client.stream("GET", f"/api/v1/runs/{run_id}/events?after=0") as stream:
        lines = []
        for line in stream.iter_lines():
            lines.append(line)
            if line.startswith("event: run.done") or line.startswith("event: run.error"):
                break
    assert any(line.startswith("event: run.stage") for line in lines)


# ── watchlist ────────────────────────────────────────────────────────────


def test_watchlist_round_trip(client) -> None:
    instrument = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()["instrument"]
    created = client.post("/api/v1/watchlist", json={"instrumentId": instrument["id"]})
    assert created.status_code == 201
    assert len(created.json()["items"]) == 1

    listed = client.get("/api/v1/watchlist").json()
    assert listed["items"][0]["instrument"]["id"] == instrument["id"]

    assert client.delete(f"/api/v1/watchlist/{instrument['id']}").status_code == 204
    assert client.get("/api/v1/watchlist").json()["items"] == []


def test_watchlist_reorder(client) -> None:
    a = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()["instrument"]
    b = client.post("/api/v1/symbols/resolve", json={"query": "AAPL"}).json()["instrument"]
    client.post("/api/v1/watchlist", json={"instrumentId": a["id"]})
    client.post("/api/v1/watchlist", json={"instrumentId": b["id"]})

    response = client.patch("/api/v1/watchlist/reorder", json={"ids": [b["id"], a["id"]]})
    assert response.status_code == 200
    assert [item["instrument"]["id"] for item in response.json()["items"]] == [b["id"], a["id"]]


def test_watchlist_rejects_unknown_instrument(client) -> None:
    assert client.post("/api/v1/watchlist", json={"instrumentId": "CN:NOPE"}).status_code == 404


# ── error envelope ───────────────────────────────────────────────────────


def test_error_envelope_shape(client) -> None:
    body = client.get("/api/v1/reports/missing").json()
    assert set(body) == {"error"}
    assert {"code", "message", "requestId", "detail"} <= set(body["error"])


def test_request_id_header_is_echoed(client) -> None:
    response = client.get("/api/v1/health", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


def test_generated_request_id_when_absent(client) -> None:
    response = client.get("/api/v1/health")
    assert len(response.headers["x-request-id"]) >= 8


def test_unknown_route_uses_the_envelope(client) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert "error" in response.json()


def test_wrong_method_is_not_reported_as_a_server_error(client) -> None:
    # A 405 used to come back as `code: "internal"`, which reads like a crash.
    response = client.put("/api/v1/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_validation_error_uses_the_envelope(client) -> None:
    response = client.post("/api/v1/symbols/resolve", json={"query": ""})
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "invalid_query"
    assert body["error"]["detail"]["errors"]
