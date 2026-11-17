"""K4 重试权收回：只重试两种码、attempt 目录、`run.retry.layer`、usage 累加（AC-100 / AC-101）。

背景（E-69 / E-70）：`--retry-max` 管不住 CLI 的**第二层**重试（`maxRetries=10`、每次退避
60 s），一次"端点不可达"花掉 2 分 03 秒并撞上时长预算；关掉之后（`--retry-max 0`）同样
的问题 **2.0 秒**返回且可归因。所以重试权收回 runner，判据只有一张白名单：

* 重试：`provider_unavailable` / `result_missing`
* 不重试：`provider_auth`（改密钥才有用）、`cancelled`（用户意图）、
  `budget_exceeded`（要改配置）、`result_schema_unsatisfied`（多半同样结局且成本翻倍）

代价是每次 attempt 都花 token，所以两边都要有机器断言——重试该发生的发生、不该发生的
一次都不许多 spawn。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent import aragon_runner
from app.agent.failure import RETRYABLE_BY_RUNNER
from app.agent.preflight import AragonCapability
from app.agent.runner_base import AgentRequest
from app.config import get_settings

SCHEMAS = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas"


def capability(resolved: str) -> AragonCapability:
    return AragonCapability(
        available=True,
        cli="0.6.68",
        versionOk=True,
        provider="openai",
        model="deepseek-flash",
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=["read_file", "glob", "grep", "list_dir", "submit_result"],
        result={"maxRepairTurns": 2, "schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 5000},
        resolvedBinary=resolved,
    )


@pytest.fixture()
def run_env(tmp_path: Path, monkeypatch, fake_aragon_bin):
    """把 `AGENT_ATTEMPTS` 打回 2（conftest 默认 1）并指向假引擎。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_env_passthrough", "FAKE_ARAGON_MODE", raising=False)
    monkeypatch.setattr(settings, "agent_attempts", 2, raising=False)

    workspace = tmp_path / "run"
    workspace.mkdir()
    (workspace / "prompt.md").write_text("# prompt", encoding="utf-8")
    (workspace / "system.md").write_text("# system", encoding="utf-8")
    (workspace / "schema.json").write_bytes((SCHEMAS / "report_doc.schema.json").read_bytes())

    def configure(mode: str, *, max_duration_ms: int = 900_000):
        monkeypatch.setenv("FAKE_ARAGON_MODE", mode)
        request = AgentRequest(
            run_id="run-k4",
            workspace=workspace,
            prompt_path=workspace / "prompt.md",
            system_prompt_path=workspace / "system.md",
            schema_path=workspace / "schema.json",
            schema_source_path=SCHEMAS / "report_doc.schema.json",
            instrument_symbol="600519.SH",
            instrument_name="贵州茅台",
            max_duration_ms=max_duration_ms,
        )
        runner = aragon_runner.AragonRunner(capability(str(fake_aragon_bin)))
        return request, runner

    return configure


def _drive(request: AgentRequest, runner: aragon_runner.AragonRunner) -> tuple[object, list]:
    async def go():
        events = [event async for event in runner.stream(request)]
        return events

    events = asyncio.run(go())
    result = next(
        event.payload["__result"]
        for event in events
        if event.type == aragon_runner.AgentEventType.log and "__result" in event.payload
    )
    return result, events


def test_provider_unavailable_is_retried_once(run_env) -> None:
    """AC-100：`provider_unavailable` ⇒ 共 spawn 2 次，`run.retry{layer:"runner"}` 一条，
    两个 `attempt-<n>/` 目录都在。"""
    request, runner = run_env("retry_storm")
    result, events = _drive(request, runner)

    retries = [
        event
        for event in events
        if event.type == aragon_runner.AgentEventType.retry and event.payload.get("layer") == "runner"
    ]
    assert len(retries) == 1, f"应当只多试一次，实际 {len(retries)}"
    payload = retries[0].payload
    assert (payload["attempt"], payload["of"], payload["code"]) == (2, 2, "provider_unavailable")
    assert payload["backoffMs"] >= 1_500

    assert result.error_code == "provider_unavailable"
    assert result.attempts_made == 2
    # 两次尝试的产物都留档（AC-100 的后半句）。
    assert (request.workspace / "attempt-1" / "argv.json").is_file()
    assert (request.workspace / "attempt-2" / "argv.json").is_file()
    # 第二层重试的目击者：两次的 ndjson 各自留档。
    assert (request.workspace / "attempt-1" / "aragon.ndjson").is_file()
    assert (request.workspace / "attempt-2" / "aragon.ndjson").is_file()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # provider_auth 不在白名单里：改密钥才有用，重试只会再失败一次。
        ("probe_auth", "provider_auth"),
        # schema 不满足不在白名单里：多半同样结局，且成本翻倍。
        ("unsatisfied", "result_schema_unsatisfied"),
        # 撞预算不在白名单里：要改配置。
        ("budget", "budget_exceeded"),
        ("usage", "usage_error"),
    ],
)
def test_these_codes_are_never_retried(run_env, mode: str, expected: str) -> None:
    """AC-101：spawn 计数 = 1（没有 `attempt-2/` 目录，`attempts_made == 1`）。"""
    request, runner = run_env(mode)
    result, events = _drive(request, runner)

    assert result.error_code == expected
    assert result.attempts_made == 1
    assert not (request.workspace / "attempt-2").exists()
    assert not [event for event in events if event.payload.get("layer") == "runner"]


def test_success_never_retries(run_env) -> None:
    request, runner = run_env("ok")
    result, _events = _drive(request, runner)
    assert result.ok is True
    assert result.attempts_made == 1
    assert not (request.workspace / "attempt-1").exists(), "单次成功不该多出一层目录"


def test_the_retry_whitelist_is_exactly_two_codes() -> None:
    """白名单是**数据**，不是散落的分支。多一个码 = 多一类被翻倍烧钱的失败。"""
    assert frozenset({"provider_unavailable", "result_missing"}) == RETRYABLE_BY_RUNNER


def test_budget_guard_skips_the_retry_when_time_is_short(run_env) -> None:
    """预算守卫：`elapsed + expected >= max_duration` ⇒ **不重试**，把第一次的错误交出去。

    比"重试到超时"诚实：用户至少知道第一次为什么失败。
    """
    request, runner = run_env("retry_storm", max_duration_ms=100)
    result, events = _drive(request, runner)

    assert result.attempts_made == 1
    assert not [event for event in events if event.payload.get("layer") == "runner"]


def test_the_probe_never_retries() -> None:
    """探针 `max_attempts=1`：一次自检不值得再花一次 token，也不该拖成两倍时间。"""
    from app.agent import probe

    probe_source = Path(probe.__file__).read_text(encoding="utf-8")
    assert "max_attempts=1" in probe_source
