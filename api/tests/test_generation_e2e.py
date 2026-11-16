"""End-to-end generation through the fake CLI: success, failure, cancel, timeout.

This is where the six stages, the NDJSON parser and the provisional fallback meet
the real orchestration loop. `FAKE_ARAGON_MODE` scripts the CLI, so every path —
including the ugly ones — is reproducible without a model.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.agent.aragon_runner import AragonRunner
from app.agent.preflight import check_aragon, reset_cache
from app.agent.runner_base import AgentError, AgentRequest
from app.config import get_settings

FAKE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_aragon.py"


@pytest.fixture()
def aragon_env(monkeypatch, fake_aragon_bin, tmp_path: Path):
    """Point the agent at the fake CLI and give it a scratch workspace."""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    # The child env whitelist is deliberate (spec §11.3); the fake CLI is given
    # its script variables through the sanctioned passthrough door.
    monkeypatch.setattr(
        settings,
        "aragon_env_passthrough",
        "FAKE_ARAGON_MODE,FAKE_ARAGON_INFO,FAKE_ARAGON_ARGV_LOG",
        raising=False,
    )
    monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
    reset_cache()
    argv_log = tmp_path / "argv.json"

    def configure(mode: str) -> tuple[AragonRunner, AgentRequest]:
        monkeypatch.setenv("FAKE_ARAGON_MODE", mode)
        monkeypatch.setenv("FAKE_ARAGON_ARGV_LOG", str(argv_log))
        reset_cache()
        capability = check_aragon(force=True)
        workspace = tmp_path / f"run-{mode}"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "prompt.md").write_text("# prompt", encoding="utf-8")
        (workspace / "system.md").write_text("# system", encoding="utf-8")
        (workspace / "schema.json").write_text("{}", encoding="utf-8")
        request = AgentRequest(
            run_id=f"test-{mode}",
            workspace=workspace,
            prompt_path=workspace / "prompt.md",
            system_prompt_path=workspace / "system.md",
            schema_path=workspace / "schema.json",
            instrument_symbol="600519.SH",
            instrument_name="贵州茅台",
        )
        return AragonRunner(capability), request

    yield configure
    reset_cache()


# ── success ──────────────────────────────────────────────────────────────


def test_ok_mode_produces_a_structured_document(aragon_env) -> None:
    runner, request = aragon_env("ok")
    result = __import__("asyncio").run(runner.run(request))
    assert result.exit_code == 0
    assert result.error_code is None
    assert result.doc is not None
    assert result.doc["schema"] == "aragon.research.report/v1"
    assert result.usage.total_tokens == 89650
    assert result.usage.tool_calls >= 1


def test_ok_mode_reports_cost_as_unknown(aragon_env) -> None:
    """★P1-8: `cost.known=false` must never be rendered as $0.00."""
    runner, request = aragon_env("ok")
    result = __import__("asyncio").run(runner.run(request))
    assert result.cost_known is False
    assert result.cost_usd is None


def test_ok_mode_archives_the_raw_ndjson(aragon_env) -> None:
    """The audit trail C-2 asks for: every line on disk, replayable."""
    runner, request = aragon_env("ok")
    __import__("asyncio").run(runner.run(request))
    ndjson = request.workspace / "aragon.ndjson"
    assert ndjson.exists()
    lines = [line for line in ndjson.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines
    assert all(json.loads(line) for line in lines), "every archived line must be valid JSON"


def test_argv_is_persisted_next_to_the_run(aragon_env) -> None:
    runner, request = aragon_env("ok")
    __import__("asyncio").run(runner.run(request))
    recorded = json.loads((request.workspace / "argv.json").read_text(encoding="utf-8"))
    assert "exec" in recorded
    assert "submit_result" in recorded[recorded.index("--allow-tool") + 1]


def test_result_payload_is_saved(aragon_env) -> None:
    runner, request = aragon_env("ok")
    __import__("asyncio").run(runner.run(request))
    saved = json.loads((request.workspace / "result.json").read_text(encoding="utf-8"))
    assert saved["structured"]["value"]["schema"] == "aragon.research.report/v1"


# ── failure modes ────────────────────────────────────────────────────────


def test_unsatisfied_mode_reports_schema_failure_with_text(aragon_env) -> None:
    """AC-9: `structured=null` → result_schema_unsatisfied, and the text survives."""
    runner, request = aragon_env("unsatisfied")
    result = __import__("asyncio").run(runner.run(request))
    assert result.doc is None
    assert result.error_code == "result_schema_unsatisfied"
    assert result.text, "the model's last words must be kept for the provisional report"


def test_budget_mode_is_classified_from_the_exit_code(aragon_env) -> None:
    """AC-10: exit 3 → budget_exceeded, not a generic crash."""
    runner, request = aragon_env("budget")
    result = __import__("asyncio").run(runner.run(request))
    assert result.doc is None
    assert result.exit_code == 3
    assert result.error_code == "budget_exceeded"


def test_usage_error_is_classified(aragon_env) -> None:
    runner, request = aragon_env("usage")
    result = __import__("asyncio").run(runner.run(request))
    assert result.exit_code == 2
    assert result.error_code == "usage_error"
    assert "unknown option" in (result.error_message or "")


def test_provider_error_is_classified(aragon_env) -> None:
    runner, request = aragon_env("error")
    result = __import__("asyncio").run(runner.run(request))
    assert result.exit_code == 1
    assert result.error_code == "agent_error"


def test_garbage_lines_do_not_break_the_stream(aragon_env) -> None:
    """§5.4 point 7: a non-JSON line is logged and skipped, never fatal."""
    runner, request = aragon_env("garbage")
    result = __import__("asyncio").run(runner.run(request))
    assert result.doc is not None, "unknown/garbage lines must not abort a healthy run"


# ── large lines & cancellation ───────────────────────────────────────────


def test_huge_tool_output_is_not_truncated_by_the_buffer(aragon_env) -> None:
    """A 200KB single line must be read whole (no small line limit)."""
    runner, request = aragon_env("huge")
    result = __import__("asyncio").run(runner.run(request))
    assert result.doc is not None
    ndjson = (request.workspace / "aragon.ndjson").read_text(encoding="utf-8")
    assert len(ndjson) > 200_000


def test_hang_is_cancelled_and_the_process_tree_dies(aragon_env) -> None:
    """AC-11: cancel closes the stream instead of hanging forever."""
    import asyncio

    runner, request = aragon_env("hang")

    async def scenario() -> None:
        async def consume() -> None:
            async for _ in runner.stream(request):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.6)
        await runner.cancel()
        await asyncio.wait_for(task, timeout=15)

    asyncio.run(scenario())


def test_cancel_before_start_is_a_no_op(aragon_env) -> None:
    import asyncio

    runner, _request = aragon_env("hang")
    asyncio.run(runner.cancel())  # nothing spawned yet; must not raise


# ── orchestration through the API ────────────────────────────────────────


def test_full_run_through_the_api_persists_a_report(client, monkeypatch, aragon_env) -> None:
    """The whole six-stage pipeline, driven by the fake CLI."""
    # The suite pins AGENT_MODE=mock; these tests are about the real pipeline,
    # so let `auto` pick the (fully capable) fake CLI.
    monkeypatch.setattr(get_settings(), "agent_mode", "auto", raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ok")
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    response = client.post("/api/v1/instruments/CN:600519.SH/reports", json={})
    assert response.status_code == 202
    run_id = response.json()["runId"]

    deadline = time.time() + 90
    status = None
    while time.time() < deadline:
        body = client.get(f"/api/v1/runs/{run_id}").json()
        status = body["status"]
        if status in ("succeeded", "failed", "cancelled", "timeout", "budget_exceeded"):
            break
        time.sleep(0.25)

    assert status == "succeeded", body.get("errorMessage")

    reports = client.get("/api/v1/instruments/CN:600519.SH/reports").json()
    assert reports["total"] >= 1
    latest = reports["items"][0]
    assert latest["isLatest"] is True
    assert latest["quality"]["source"] in ("aragon", "mock", "aragon-text")

    stages = client.get(f"/api/v1/runs/{run_id}").json()
    assert stages["pct"] == 100


def test_failed_run_leaves_a_provisional_report_through_the_api(client, monkeypatch, aragon_env) -> None:
    """AC-21 end to end: the user always has something to read."""
    # The suite pins AGENT_MODE=mock; these tests are about the real pipeline,
    # so let `auto` pick the (fully capable) fake CLI.
    monkeypatch.setattr(get_settings(), "agent_mode", "auto", raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "unsatisfied")
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_id = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]

    deadline = time.time() + 90
    status = None
    while time.time() < deadline:
        body = client.get(f"/api/v1/runs/{run_id}").json()
        status = body["status"]
        if status in ("succeeded", "failed", "cancelled", "timeout", "budget_exceeded"):
            break
        time.sleep(0.25)

    assert status == "failed"
    reports = client.get("/api/v1/instruments/CN:600519.SH/reports").json()
    assert reports["total"] >= 1
    provisional = [item for item in reports["items"] if item["provisional"]]
    assert provisional, "a failed run must still leave a readable version"

    detail = client.get(f"/api/v1/reports/{provisional[0]['id']}").json()
    assert detail["doc"]["provisional"] is True
    assert detail["doc"]["sections"][0]["id"] == "raw"


def test_cancel_through_the_api_terminates_the_run(client, monkeypatch, aragon_env) -> None:
    """AC-11 end to end."""
    # The suite pins AGENT_MODE=mock; these tests are about the real pipeline,
    # so let `auto` pick the (fully capable) fake CLI.
    monkeypatch.setattr(get_settings(), "agent_mode", "auto", raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "hang")
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    run_id = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()["runId"]

    time.sleep(1.0)
    cancel = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] in ("cancelling", "cancelled")

    deadline = time.time() + 30
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/v1/runs/{run_id}").json()["status"]
        if status in ("cancelled", "failed", "succeeded"):
            break
        time.sleep(0.25)
    assert status == "cancelled"


def test_runner_without_submit_result_never_spawns(aragon_env) -> None:
    """A capability gap must fail before the process starts, not after 90s."""
    import asyncio

    runner, request = aragon_env("ok")
    runner.capability.hasSubmitResult = False

    async def consume() -> None:
        async for _ in runner.stream(request):
            pass

    with pytest.raises(AgentError) as excinfo:
        asyncio.run(consume())
    assert excinfo.value.code == "structured_result_unsupported"
