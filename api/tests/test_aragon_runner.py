"""AC-18 / AC-19 — the argv contract with `aragon exec` (spec §5.4, C-1).

This is the test that would have caught P0-1. It asserts the *shape* of the
command line, because a fake runner always cooperates and the real CLI does not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.agent.aragon_runner import DENY_TOOLS, build_argv, sanitize_session_id
from app.agent.preflight import AragonCapability
from app.agent.runner_base import AgentError, AgentRequest

FAKE = Path(__file__).parent / "fixtures" / "fake_aragon.py"

ALL_TOOLS = ["read_file", "glob", "grep", "list_dir", "submit_result", "bash", "write_file", "skill", "task"]


def capability(**overrides) -> AragonCapability:
    base = AragonCapability(
        available=True,
        cli="0.6.68",
        core="0.2.80",
        versionOk=True,
        provider="openai",
        model="deepseek-flash",
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=["read_file", "glob", "grep", "list_dir", "submit_result"],
        tools=list(ALL_TOOLS),
        resolvedBinary=str(FAKE),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture()
def request_factory(tmp_path: Path):
    workspace = tmp_path / "run"
    workspace.mkdir()
    schema = workspace / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    prompt = workspace / "prompt.md"
    prompt.write_text("# prompt", encoding="utf-8")
    system = workspace / "system.md"
    system.write_text("# system", encoding="utf-8")

    def factory() -> AgentRequest:
        return AgentRequest(
            run_id="abc123",
            workspace=workspace,
            prompt_path=prompt,
            system_prompt_path=system,
            schema_path=schema,
            instrument_symbol="600519.SH",
            instrument_name="贵州茅台",
            max_turns=40,
            max_duration_ms=900_000,
        )

    return factory


def test_allow_list_contains_submit_result(request_factory) -> None:
    """AC-18: without `submit_result` in the allow list the schema can never be met."""
    argv = build_argv(request_factory(), capability(), str(FAKE))
    allow = argv[argv.index("--allow-tool") + 1].split(",")
    assert "submit_result" in allow, "P0-1 regression: submit_result missing from --allow-tool"
    assert set(allow) == {"read_file", "glob", "grep", "list_dir", "submit_result"}


def test_deny_list_covers_writers_and_shell(request_factory) -> None:
    argv = build_argv(request_factory(), capability(), str(FAKE))
    deny = argv[argv.index("--deny-tool") + 1].split(",")
    for forbidden in ("bash", "write_file", "edit_file"):
        assert forbidden in deny
        assert forbidden in DENY_TOOLS
    # Nothing may be both allowed and denied — deny wins silently in the CLI,
    # which would be a confusing way to lose a tool.
    allow = set(argv[argv.index("--allow-tool") + 1].split(","))
    assert allow.isdisjoint(set(deny))


def test_system_prompt_is_injected(request_factory) -> None:
    """AC-19 (first half, ★P1-11): the grounding rules must actually be passed in."""
    request = request_factory()
    argv = build_argv(request, capability(), str(FAKE))
    index = argv.index("--append-system-prompt-file")
    assert argv[index + 1] == str(request.system_prompt_path)
    assert Path(argv[index + 1]).exists()


def test_global_flags_precede_exec_and_budget_flags_follow(request_factory) -> None:
    """AC-19 (second half, ★P2-04): wrong scope is `error: unknown option`."""
    argv = build_argv(request_factory(), capability(), str(FAKE))
    exec_index = argv.index("exec")

    global_flags = {"--cwd", "--tool-timeout", "--idle-timeout", "--max-tokens", "--no-color"}
    sub_flags = {
        "--output-format",
        "--partial-messages",
        "--no-save-session",
        "--permission-mode",
        "--allow-tool",
        "--deny-tool",
        "--append-system-prompt-file",
        "--result-schema",
        "--result-repair-turns",
        "--on-result-unsatisfied",
        "--max-turns",
        "--max-duration",
        "--prompt-file",
    }

    for flag in global_flags:
        assert flag in argv, f"{flag} missing"
        assert argv.index(flag) < exec_index, f"{flag} must come before `exec`"

    for flag in sub_flags:
        assert flag in argv, f"{flag} missing"
        assert argv.index(flag) > exec_index, f"{flag} must come after `exec`"

    assert "--max-turns" not in argv[:exec_index]
    assert "--max-duration" not in argv[:exec_index]


def test_provider_and_model_are_omitted_when_unset(request_factory, monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_provider", "", raising=False)
    monkeypatch.setattr(settings, "aragon_model", "", raising=False)
    argv = build_argv(request_factory(), capability(), str(FAKE))
    assert "--provider" not in argv
    assert "--model" not in argv


def test_provider_and_model_are_emitted_before_exec_when_set(request_factory, monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_provider", "anthropic", raising=False)
    monkeypatch.setattr(settings, "aragon_model", "claude-sonnet-4", raising=False)
    argv = build_argv(request_factory(), capability(), str(FAKE))
    exec_index = argv.index("exec")
    assert argv.index("--provider") < exec_index
    assert argv[argv.index("--provider") + 1] == "anthropic"
    assert argv.index("--model") < exec_index
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4"


def test_missing_submit_result_raises_instead_of_silently_failing(request_factory) -> None:
    """AC-18 (second half): a capability gap must be loud, not a 2-minute mystery."""
    weak = capability(hasSubmitResult=False)
    with pytest.raises(AgentError) as excinfo:
        build_argv(request_factory(), weak, str(FAKE))
    assert excinfo.value.code == "structured_result_unsupported"


def test_session_id_is_sanitised() -> None:
    """§5.4 point 4: `--session-id` accepts `[A-Za-z0-9][A-Za-z0-9._-]*` only."""
    assert sanitize_session_id("CN:600519.SH") == "CN-600519.SH"
    assert sanitize_session_id("rsrch-600519-SH-20260210T093012") == "rsrch-600519-SH-20260210T093012"
    long_value = sanitize_session_id("x" * 200)
    assert len(long_value) <= 48
    assert sanitize_session_id(":leading-colon").startswith("rsrch-")


def test_prompt_file_and_result_schema_are_absolute(request_factory) -> None:
    request = request_factory()
    argv = build_argv(request, capability(), str(FAKE))
    assert argv[argv.index("--prompt-file") + 1] == str(request.prompt_path)
    assert argv[argv.index("--result-schema") + 1] == str(request.schema_path)
    assert argv[argv.index("--cwd") + 1] == str(request.workspace)
    assert argv[argv.index("--result-repair-turns") + 1] == "2"
    assert argv[argv.index("--on-result-unsatisfied") + 1] == "error"


def test_runner_raises_when_capability_is_missing(tmp_path) -> None:
    """`structured_result_unsupported` must not degrade into a generic failure."""
    import asyncio

    from app.agent.aragon_runner import AragonRunner

    runner = AragonRunner(capability(hasSubmitResult=False))
    request = AgentRequest(
        run_id="x",
        workspace=tmp_path,
        prompt_path=tmp_path / "prompt.md",
        system_prompt_path=tmp_path / "system.md",
        schema_path=tmp_path / "schema.json",
        instrument_symbol="600519.SH",
        instrument_name="贵州茅台",
    )

    async def consume() -> None:
        async for _ in runner.stream(request):
            pass

    with pytest.raises(AgentError) as excinfo:
        asyncio.run(consume())
    assert excinfo.value.code == "structured_result_unsupported"


def test_fake_aragon_info_payload_is_valid_json() -> None:
    import subprocess

    completed = subprocess.run(
        [sys.executable, str(FAKE), "info", "--json"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "submit_result" in payload["tools"]
