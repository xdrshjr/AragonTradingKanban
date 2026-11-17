"""确定性 spawn 契约（spec §20.3.2 / M19；AC-67 / AC-68 / AC-69）。

这三条守的是同一件事：**同一个生成任务，今天和明天应该用同一个命令跑完**。

实测（E-43）默认体位比 lean 体位多 **2551 input tokens**（3352 → 5903，+76%），
并且 stderr 每次都被打上一条与生成无关的 `[skills] warn: SKILL.md changed since
install…`；`--max-tokens auto` 让"截断"变得不可预测；`--retry-max` 沿用 CLI 默认的
10 次会把一个网络问题记成"模型太慢"（E-45：6 次退避已吃 44 s）。

写法上有两处刻意的选择：

* **断言索引关系，不是集合包含**（AC-67 的原话）：`--no-skills` 放在 `exec` 之后是
  `error: unknown option`（exit 2），"出现在 argv 里"完全看不出这个问题。
* **反例优先**（§20.7.3）：先写"full 体位一个 flag 都不许出现"，再写 lean 体位必须全都在。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.aragon_runner import LEAN_FLAGS, build_argv, clamp_repair_turns
from app.agent.preflight import AragonCapability
from app.agent.runner_base import AgentRequest
from app.config import get_settings

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas"
FAKE = "aragon"
ALL_TOOLS = ["read_file", "glob", "grep", "list_dir", "submit_result"]


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
        extraAllowTools=list(ALL_TOOLS),
        tools=list(ALL_TOOLS),
        resolvedBinary=FAKE,
        result={
            "schemaBytes": 65536,
            "schemaDepth": 12,
            "validationNodes": 5000,
            "valueBytes": 262144,
            "maxRepairTurns": 2,
        },
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture()
def request_factory(tmp_path: Path):
    workspace = tmp_path / "run"
    workspace.mkdir()
    schema = workspace / "schema.json"
    schema.write_bytes((SCHEMAS_DIR / "report_doc.schema.json").read_bytes())
    prompt = workspace / "prompt.md"
    prompt.write_text("# prompt", encoding="utf-8")
    system = workspace / "system.md"
    system.write_text("# system", encoding="utf-8")

    def factory() -> AgentRequest:
        return AgentRequest(
            run_id="posture",
            workspace=workspace,
            prompt_path=prompt,
            system_prompt_path=system,
            schema_path=schema,
            schema_source_path=SCHEMAS_DIR / "report_doc.schema.json",
            instrument_symbol="600519.SH",
            instrument_name="贵州茅台",
        )

    return factory


# ── AC-67：lean 体位下六个 flag 全在 `exec` 之前 ─────────────────────────────


def test_lean_flags_precede_exec(request_factory, monkeypatch) -> None:
    """AC-67：断言**索引关系**——放在 `exec` 之后就是 exit 2（P2-04 的同型问题）。"""
    monkeypatch.setattr(get_settings(), "aragon_agent_posture", "lean", raising=False)
    argv = build_argv(request_factory(), capability(), FAKE)
    exec_index = argv.index("exec")

    for flag in ("--no-update", "--no-skills", "--no-gene", "--no-memory", "--no-todo", "--no-team"):
        assert flag in argv, f"{flag} 缺位：默认体位会让本机 skills/gene/记忆混进每一次请求"
        assert argv.index(flag) < exec_index, f"{flag} 必须落在全局段（`exec` 之前）"

    assert set(LEAN_FLAGS) == {"--no-update", "--no-skills", "--no-gene", "--no-memory", "--no-todo", "--no-team"}
    assert len([flag for flag in argv if flag in LEAN_FLAGS]) == len(LEAN_FLAGS), "六个 flag 各出现一次"


def test_full_posture_keeps_default_flags(request_factory, monkeypatch) -> None:
    """AC-68：回退路径可用——`full` 时六个 flag 一个都不出现（R-32 的一行回退）。"""
    monkeypatch.setattr(get_settings(), "aragon_agent_posture", "full", raising=False)
    argv = build_argv(request_factory(), capability(), FAKE)
    assert [flag for flag in argv if flag in LEAN_FLAGS] == []
    assert "--no-color" in argv, "体位只控制六个 --no-*，`--no-color` 与它无关"


# ── AC-69：三个预算 flag 显式且被夹取 ───────────────────────────────────────


def test_budget_flags_are_explicit(request_factory, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_max_output_tokens", 40_000, raising=False)
    monkeypatch.setattr(settings, "aragon_retry_max", 6, raising=False)
    argv = build_argv(request_factory(), capability(), FAKE)

    assert argv[argv.index("--max-tokens") + 1] == "40000", "`auto` 的含义是'用模型上限'，截断因此不可预测"
    assert argv[argv.index("--retry-max") + 1] == "6"
    # `--retry-max` 是**全局** flag（实测放在 `exec` 之前被接受，E-51/E-54）。
    assert argv.index("--retry-max") < argv.index("exec")


def test_retry_max_is_clamped_to_the_cli_range(request_factory, monkeypatch) -> None:
    """CLI 的 `--retry-max` 是 0-20；配错不许变成一个 exit 2。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_retry_max", 99, raising=False)
    assert build_argv(request_factory(), capability(), FAKE)[
        build_argv(request_factory(), capability(), FAKE).index("--retry-max") + 1
    ] == "20"
    monkeypatch.setattr(settings, "aragon_retry_max", -3, raising=False)
    argv = build_argv(request_factory(), capability(), FAKE)
    assert argv[argv.index("--retry-max") + 1] == "0"


def test_repair_turns_clamped_by_cli(request_factory, monkeypatch) -> None:
    """AC-69：`min(config, info.result.maxRepairTurns)`——CLI 的上限是硬上限。"""
    monkeypatch.setattr(get_settings(), "aragon_repair_turns", 5, raising=False)
    strict_cli = capability(result={"schemaDepth": 12, "maxRepairTurns": 2})
    argv = build_argv(request_factory(), strict_cli, FAKE)
    assert argv[argv.index("--result-repair-turns") + 1] == "2"

    tighter = capability(result={"schemaDepth": 12, "maxRepairTurns": 1})
    assert clamp_repair_turns(5, tighter) == 1
    # `info.result` 缺这一项时用配置值（**不回退成"不限"**，也不回退成 0）。
    assert clamp_repair_turns(2, capability(result={})) == 2


def test_prompt_file_and_schema_still_follow_the_exec_subcommand(request_factory) -> None:
    """三道闸门插在全局段与 `exec` 之间，不能把既有 flag 挤到错的那一段。"""
    argv = build_argv(request_factory(), capability(), FAKE)
    exec_index = argv.index("exec")
    for flag in ("--result-schema", "--result-repair-turns", "--prompt-file", "--allow-tool", "--deny-tool"):
        assert argv.index(flag) > exec_index, f"{flag} 必须仍在 `exec` 之后"

def test_idle_timeout_is_at_least_tool_timeout_plus_30s(request_factory, monkeypatch) -> None:
    """§20.5.3 的"显式保证"：一次慢工具不许被当成"空闲"掐掉。

    CLI 自己也会抬这个值，但那是它的事；配置写错时我们**显式**抬到安全值并留一条 warn，
    而不是让一次生成以一条无法归因的 `cancelled` 结束（那正是最难查的一类失败）。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_tool_timeout_ms", 170_000, raising=False)
    monkeypatch.setattr(settings, "aragon_idle_timeout_ms", 175_000, raising=False)
    argv = build_argv(request_factory(), capability(), FAKE)
    idle = int(argv[argv.index("--idle-timeout") + 1])
    assert idle >= 170_000 + 30_000

    # 已经够宽时不改动用户的配置（这条断言防"顺手把 180000 改成 999999"）。
    monkeypatch.setattr(settings, "aragon_idle_timeout_ms", 180_000, raising=False)
    monkeypatch.setattr(settings, "aragon_tool_timeout_ms", 60_000, raising=False)
    argv = build_argv(request_factory(), capability(), FAKE)
    assert int(argv[argv.index("--idle-timeout") + 1]) == 180_000

