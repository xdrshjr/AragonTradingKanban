"""`GET /capabilities` under the three degraded CLI shapes (★P1-6).

The endpoint-level counterpart of `test_preflight.py`: the front end decides
whether to show the "示例模式" banner from exactly this payload, so its shape and
its `agentMode` verdict are part of the contract.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.agent.preflight import reset_cache
from app.config import get_settings

FAKE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_aragon.py"


@pytest.fixture()
def capability_client(client, monkeypatch, fake_aragon_bin):
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    monkeypatch.setattr(settings, "agent_mode", "auto", raising=False)
    monkeypatch.setattr(
        settings,
        "aragon_env_passthrough",
        "FAKE_ARAGON_INFO",
        raising=False,
    )

    def configure(info: str | None = None):
        if info:
            monkeypatch.setenv("FAKE_ARAGON_INFO", info)
        else:
            monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
        reset_cache()
        return client.get("/api/v1/capabilities").json()

    yield configure
    monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
    reset_cache()


def test_capable_cli_reports_real_mode(capability_client) -> None:
    body = capability_client()
    assert body["agentMode"] == "real"
    assert body["aragon"]["available"] is True
    assert body["aragon"]["hasSubmitResult"] is True
    assert body["aragon"]["canStructured"] is True
    assert body["aragon"]["degradedReason"] is None


def test_missing_submit_result_switches_to_mock_with_a_reason(capability_client) -> None:
    """The P0-1 shape: a CLI that cannot submit must never be trusted."""
    body = capability_client("no_submit")
    assert body["agentMode"] == "mock"
    assert body["aragon"]["hasSubmitResult"] is False
    assert body["aragon"]["degradedReason"] == "submit_result_missing"
    assert body["aragon"]["errors"], "the reason must be explainable, not just a boolean"


def test_missing_api_key_switches_to_mock(capability_client) -> None:
    body = capability_client("no_key")
    assert body["agentMode"] == "mock"
    assert body["aragon"]["degradedReason"] == "no_api_key"


def test_missing_cli_switches_to_mock(capability_client) -> None:
    body = capability_client("missing")
    assert body["agentMode"] == "mock"
    assert body["aragon"]["available"] is False
    assert body["aragon"]["degradedReason"] == "aragon_unavailable"


def test_capabilities_shape_is_stable(capability_client) -> None:
    body = capability_client()
    assert set(body) == {"aragon", "agentMode", "dataProviders", "features", "limits"}
    assert {"available", "version", "versionOk", "provider", "model", "ajv"} <= set(body["aragon"])
    # ★V4-P2-4: `ask` is no longer a 501 placeholder, so the flags are the v4 set —
    # and every one of them is a measurement, which is what the next test pins down.
    assert body["features"] == {
        "mock": True,
        "exportHtml": True,
        "exportMd": True,
        "pdf": False,
        "ask": True,
        "askDeep": True,
        "fullTextSearch": True,
        "pinyinSearch": False,
        # The suite runs with SCHEDULER_ENABLED=0 (the ticker is driven explicitly),
        # so this flag proves the payload follows the setting rather than a constant.
        "schedule": False,
        "offline": True,
        "realtimeQuote": False,
        "semanticSearch": False,
    }
    assert body["limits"]["askDailyLimit"] == 30


def test_feature_flags_follow_measurements(capability_client, monkeypatch) -> None:
    """The flags must be measured, not assumed: no FTS5 ⇒ `fullTextSearch=false`."""
    from app.services import search_index

    monkeypatch.setattr(search_index, "fts_ready", lambda *_args, **_kwargs: False)
    body = capability_client()
    assert body["features"]["fullTextSearch"] is False
    # `cite` still works — it never touches the index or the CLI.
    assert body["features"]["ask"] is True


def test_data_providers_are_listed_with_availability(capability_client) -> None:
    body = capability_client()
    providers = {entry["id"]: entry["available"] for entry in body["dataProviders"]}
    assert "synthetic" in providers
    assert providers["synthetic"] is True


def test_fake_cli_info_contract() -> None:
    """The fake must stay shaped like the real 0.6.68 payload it stands in for."""
    completed = subprocess.run(
        [sys.executable, str(FAKE_SCRIPT), "info", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    import json

    info = json.loads(completed.stdout)
    assert {"cli", "core", "provider", "model", "hasApiKey", "tools", "features", "result"} <= set(info)
    assert {"ajv", "schemaBytes", "schemaDepth", "schemaRefs", "validationNodes", "maxRepairTurns"} <= set(
        info["result"]
    )
