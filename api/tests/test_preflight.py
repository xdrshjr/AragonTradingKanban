"""Preflight capability gates (★P0-1 / ★P1-6 / R-12).

Each test drives `aragon info --json` through the fake binary with a different
capability set, and asserts the *documented* verdict — because the failure mode
we are guarding against is a silent one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent import decide_mode
from app.agent.preflight import (
    AragonCapability,
    check_aragon,
    parse_version,
    reset_cache,
    resolve_binary,
    version_ok,
)
from app.config import get_settings

FAKE = Path(__file__).parent / "fixtures" / "fake_aragon.py"


@pytest.fixture()
def fake_aragon(monkeypatch, fake_aragon_bin):
    """Point ARAGON_BIN at the fake and clear the 60s capability cache."""
    settings = get_settings()

    def configure(mode: str = "ok", info: str | None = None, argv_log: Path | None = None):
        monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
        monkeypatch.setattr(settings, "force_preflight", True, raising=False)
        monkeypatch.setenv("FAKE_ARAGON_MODE", mode)
        if info:
            monkeypatch.setenv("FAKE_ARAGON_INFO", info)
        else:
            monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
        if argv_log:
            monkeypatch.setenv("FAKE_ARAGON_ARGV_LOG", str(argv_log))
        reset_cache()
        return settings

    yield configure
    reset_cache()


def test_fully_capable_cli_passes_every_gate(fake_aragon, monkeypatch) -> None:
    settings = fake_aragon()
    # The suite pins AGENT_MODE=mock; this test is about the `auto` decision.
    monkeypatch.setattr(settings, "agent_mode", "auto", raising=False)
    capability = check_aragon(force=True)
    assert capability.available is True
    assert capability.cli == "0.6.68"
    assert capability.versionOk is True
    assert capability.canStructured is True
    assert capability.hasSubmitResult is True
    assert capability.hasApiKey is True
    assert capability.fullyCapable is True
    assert capability.degradedReason is None
    assert decide_mode(settings, capability) == "real"


def test_missing_submit_result_forces_mock(fake_aragon) -> None:
    """★P0-1: the one capability whose absence silently kills generation."""
    settings = fake_aragon(info="no_submit")
    capability = check_aragon(force=True)
    assert capability.hasSubmitResult is False
    assert capability.fullyCapable is False
    assert capability.degradedReason == "submit_result_missing"
    assert any("submit_result" in error for error in capability.errors)
    assert decide_mode(settings, capability) == "mock", "auto must not attempt a doomed run"


def test_missing_api_key_forces_mock(fake_aragon, monkeypatch) -> None:
    """★v9（V10-P0-1）：CLI 说没钥匙 **且** 我们自己的 `.env` 也没有 ⇒ 才降级。

    必须显式清空 `aragon_api_key`：开发机的 `.env` 里恰好有 `ARAGON_API_KEY` 时，
    这条断言会误红，而它想证明的是"两处都没有"这一种情形（`conftest` 的 `setdefault`
    教训，I-12 同型）。
    """
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "aragon_api_key", SecretStr(""), raising=False)
    settings = fake_aragon(info="no_key")
    capability = check_aragon(force=True)
    assert capability.hasApiKey is False
    assert capability.keySource == "none"
    assert capability.degradedReason == "no_api_key"
    assert decide_mode(settings, capability) == "mock"


def test_version_out_of_range_is_reported(fake_aragon) -> None:
    settings = fake_aragon(info="old")
    capability = check_aragon(force=True)
    assert capability.versionOk is False
    assert capability.degradedReason == "version_out_of_range"
    assert decide_mode(settings, capability) == "mock"


def test_ajv_false_disables_structured_results(fake_aragon) -> None:
    setting = fake_aragon(info="no_ajv")
    capability = check_aragon(force=True)
    assert capability.ajv is False
    assert capability.canStructured is False
    assert capability.degradedReason == "structured_result_unsupported"
    assert decide_mode(setting, capability) == "mock"


def test_missing_structured_result_feature_disables_generation(fake_aragon) -> None:
    settings = fake_aragon(info="no_structured_feature")
    capability = check_aragon(force=True)
    assert capability.canStructured is False
    assert decide_mode(settings, capability) == "mock"


def test_unavailable_cli_degrades_to_mock(fake_aragon) -> None:
    settings = fake_aragon(info="missing")
    capability = check_aragon(force=True)
    assert capability.available is False
    assert capability.degradedReason == "aragon_unavailable"
    assert decide_mode(settings, capability) == "mock"


def test_forced_real_mode_still_reports_the_gap(fake_aragon, monkeypatch) -> None:
    settings = fake_aragon(info="no_submit")
    monkeypatch.setattr(settings, "agent_mode", "real", raising=False)
    capability = check_aragon(force=True)
    assert capability.fullyCapable is False
    assert capability.degradedReason == "submit_result_missing"

    from app.agent import build_runner
    from app.agent.runner_base import AgentError

    with pytest.raises(AgentError) as excinfo:
        build_runner(settings)
    assert excinfo.value.code in ("structured_result_unsupported", "submit_result_missing")


def test_forced_mock_mode_never_consults_the_cli(fake_aragon, monkeypatch) -> None:
    settings = fake_aragon(info="no_submit")
    monkeypatch.setattr(settings, "agent_mode", "mock", raising=False)
    assert decide_mode(settings, AragonCapability()) == "mock"

    from app.agent import build_runner
    from app.agent.mock_runner import MockRunner

    assert isinstance(build_runner(settings), MockRunner)


def test_capability_payload_shape(fake_aragon) -> None:
    fake_aragon()
    payload = check_aragon(force=True).as_payload()
    for key in (
        "available",
        "version",
        "versionOk",
        "provider",
        "model",
        "hasApiKey",
        "ajv",
        "canStructured",
        "hasSubmitResult",
        "extraAllowTools",
        "degradedReason",
        "errors",
    ):
        assert key in payload
    # The allow list is the intersection of the required tools and what the CLI offers.
    assert "submit_result" in payload["extraAllowTools"]


def test_allow_list_is_intersected_with_available_tools(fake_aragon, monkeypatch) -> None:
    settings = fake_aragon()
    monkeypatch.setattr(
        settings, "aragon_required_tools", "read_file,glob,grep,list_dir,submit_result,nonexistent_tool", raising=False
    )
    reset_cache()
    capability = check_aragon(force=True)
    assert "nonexistent_tool" not in capability.extraAllowTools
    assert "submit_result" in capability.extraAllowTools


def test_version_parsing_and_range() -> None:
    assert parse_version("0.6.68") == (0, 6, 68)
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.7") == (0, 7, 0)
    assert parse_version(None) is None

    assert version_ok("0.6.66") is True
    assert version_ok("0.6.68") is True
    assert version_ok("0.6.65") is False
    assert version_ok("0.7.0") is False
    assert version_ok("garbage") is False
    # A suffix must not smuggle an old CLI past the floor: naively concatenating
    # every digit in the part turned "0.6.65-hotfix1" into 0.6.651 (>= 0.6.66).
    assert parse_version("0.6.65-hotfix1") == (0, 6, 65)
    assert version_ok("0.6.65-hotfix1") is False


def test_resolve_binary_honours_the_override(monkeypatch, fake_aragon_bin) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    assert resolve_binary() == str(fake_aragon_bin)

    monkeypatch.setattr(settings, "aragon_bin", "C:/definitely/missing/aragon.exe", raising=False)
    assert resolve_binary() is None


def test_capability_is_cached_for_60_seconds(fake_aragon, monkeypatch) -> None:
    settings = fake_aragon()
    monkeypatch.setattr(settings, "force_preflight", False, raising=False)
    first = check_aragon(force=True)
    # Even with the env flipped, the cached verdict is reused.
    monkeypatch.setenv("FAKE_ARAGON_INFO", "no_submit")
    second = check_aragon()
    assert second is first


def test_force_flag_bypasses_the_cache(fake_aragon, monkeypatch) -> None:
    settings = fake_aragon()
    monkeypatch.setattr(settings, "force_preflight", False, raising=False)
    check_aragon(force=True)
    monkeypatch.setenv("FAKE_ARAGON_INFO", "no_submit")
    refreshed = check_aragon(force=True)
    assert refreshed.hasSubmitResult is False
