"""AC-28 —— `ask.schema.json` 的深度 / 字节预算与 argv 契约。

I-1 的教训：真实 CLI 的 `--result-schema` 深度上限是 12，且 `--ref` 不解引用，
超了只会**在接上真 CLI 之后**报 `result_schema_too_deep`——本地跑得通、上线才炸。
所以在接真 CLI 之前，只能用**与 CLI 完全相同的深度算法**把它拦在测试里。

本文件里的 `schema_depth()` 是从 `tests/test_report_schema.py` **原样复制**的
（不 import，避免测试之间互相依赖导致漏跑时静默失效）：标量算 1 层，逐层 +1。
同一份算法给出的实测值：ask schema = 7 层 / 913 B；report_doc schema = 11 层 / 18,392 B。

同时把 AC-42 的姊妹守卫放这里：`metric.polarity` 加进 `report_doc.schema.json` 之后，
AC-24（≤ 60,000 B / 深度 ≤ 12）与 AC-28（≤ 8）都必须仍然是绿的。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.agent.ask_runner import build_ask_argv
from app.agent.preflight import AragonCapability
from app.agent.runner_base import AgentRequest

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas"
ASK_SCHEMA_PATH = SCHEMA_DIR / "ask.schema.json"
REPORT_SCHEMA_PATH = SCHEMA_DIR / "report_doc.schema.json"

#: AC-28 的硬上限（I-1：CLI 深度上限 12，留 4 层余量）。
MAX_SCHEMA_BYTES = 16_384
MAX_SCHEMA_DEPTH = 8

#: §18.3.2 v4 修订（V4-P2-1）给的实测值：7 层，不是 v3 写的 6 层。
DESIGN_DEPTH = 7

#: AC-24 / AC-42 的姊妹上限。
MAX_REPORT_SCHEMA_BYTES = 60_000
MAX_REPORT_SCHEMA_DEPTH = 12

ASK_TOOLS = ["read_file", "glob", "grep", "list_dir"]


@pytest.mark.parametrize("ids", [None, [1], [""], [" "], ["x" * 65], ["a", "a"], [str(i) for i in range(7)]])
def test_context_ids_reject_invalid_structure(ids) -> None:
    from pydantic import ValidationError

    from app.schemas.ask import AskRequest

    with pytest.raises(ValidationError):
        AskRequest(question="问题", mode="deep", contextAskIds=ids)


def test_context_ids_are_optional_and_preserved() -> None:
    from app.schemas.ask import AskQueuedResponse, AskRequest

    assert AskRequest(question="问题").contextAskIds == []
    assert AskRequest(question="问题", mode="deep", contextAskIds=["a"]).contextAskIds == ["a"]
    assert AskQueuedResponse(id="a", askId="a", status="queued", streamUrl="/events").contextAskIds == []


def schema_depth(value, depth: int = 1) -> int:
    """Mirror of the CLI's `schemaDepth()` — raw document nesting, scalar = 1.

    Copied verbatim from `tests/test_report_schema.py`: AC-28 must be measured with
    the CLI's algorithm, not a different (e.g. `$ref`-aware) one.
    """
    if isinstance(value, list):
        deepest = depth
        for item in value:
            deepest = max(deepest, schema_depth(item, depth + 1))
        return deepest
    if not isinstance(value, dict):
        return depth
    deepest = depth
    for key in value:
        deepest = max(deepest, schema_depth(value[key], depth + 1))
    return deepest


def count_nodes(value) -> int:
    """Approximate the compiled validation-node count: one per schema keyword."""

    if isinstance(value, dict):
        return 1 + sum(count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_nodes(item) for item in value)
    return 1


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(ASK_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report_schema() -> dict:
    return json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def canonical_answer() -> dict:
    """A payload the schema is supposed to accept — `deep` 的真正终态形状。"""
    return {
        "answer": "毛利率下滑主因是直营占比提升带来的结构变化 [S3]。",
        "citations": [{"sourceId": "S3", "claim": "直营占比提升 7 个百分点"}],
        "confidence": "high",
        "denied": False,
    }


# ── AC-28：字节与深度 ────────────────────────────────────────────────────────


def test_ask_schema_fits_the_byte_budget() -> None:
    """AC-28：≤ 16,384 B（I-1 的另一半：schema 太大也会被 CLI 拒）。"""
    size = ASK_SCHEMA_PATH.stat().st_size
    assert size <= MAX_SCHEMA_BYTES, f"ask schema 实测 {size} B，上限 {MAX_SCHEMA_BYTES} B"
    assert size == len(ASK_SCHEMA_PATH.read_bytes()), "st_size 与真实字节数必须一致"


def test_ask_schema_depth_matches_the_design_value(schema: dict) -> None:
    """AC-28：深度 ≤ 8，且实测应等于设计值 7（余量只有 1 层，改 schema 就重跑）。"""
    depth = schema_depth(schema)
    assert depth <= MAX_SCHEMA_DEPTH, (
        f"ask schema 实测深度 {depth} 层，超过 AC-28 上限 {MAX_SCHEMA_DEPTH} 层"
        f"（CLI 上限 12，留 4 层余量）——真 CLI 会报 result_schema_too_deep"
    )
    assert depth == DESIGN_DEPTH, (
        f"ask schema 实测深度 {depth} 层，设计值 {DESIGN_DEPTH} 层（§18.3.2 V4-P2-1）。"
        "深度变了说明有人给 schema 加了一层嵌套：请重跑 AC-28 并同步设计文档"
    )


def test_ask_schema_depth_path_is_the_documented_one(schema: dict) -> None:
    """深度路径必须是文档写的那一条：root→properties→citations→items→properties→sourceId→标量。"""
    chain = schema["properties"]["citations"]["items"]["properties"]["sourceId"]
    # 逐层消掉文档给自己的那一层：7 / 5 / 4 / 3 / 2 / 1。
    assert schema_depth(schema) == 7  # root + properties
    assert schema_depth(schema["properties"]) == 6
    assert schema_depth(schema["properties"]["citations"]) == 5
    assert schema_depth(schema["properties"]["citations"]["items"]) == 4
    assert schema_depth(schema["properties"]["citations"]["items"]["properties"]) == 3
    assert schema_depth(chain) == 2
    assert schema_depth(chain["maxLength"]) == 1


def test_ask_schema_validation_node_count_stays_small(schema: dict) -> None:
    """节点数不是 AC 门槛，但它解释"深度余量只有 1 层"的代价：这里应保持很小。"""
    nodes = count_nodes(schema)
    assert nodes < 200, f"ask schema 有 {nodes} 个校验节点，明显已经开始堆砌"


# ── AC-28：Draft2020-12 合法性 + 正反例 ─────────────────────────────────────


def test_ask_schema_is_valid_draft_2020_12(schema: dict) -> None:
    """schema 自己得先合法，否则 `--result-schema` 在 CLI 侧就是坏的。"""
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"answer", "citations", "confidence", "denied"}


def test_canonical_answer_validates(schema: dict, canonical_answer: dict) -> None:
    jsonschema.Draft202012Validator(schema).validate(canonical_answer)


def test_answer_with_no_citation_is_accepted(schema: dict, canonical_answer: dict) -> None:
    """`denied=true` / 无引用是**合法**形状——由 `grounded` 判定，不由 schema 判定。"""
    payload = dict(canonical_answer, citations=[], confidence="low", denied=True)
    jsonschema.Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("missing", ["answer", "citations", "confidence", "denied"])
def test_missing_required_field_is_rejected(missing: str, schema: dict, canonical_answer: dict) -> None:
    payload = {key: value for key, value in canonical_answer.items() if key != missing}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_extra_field_is_rejected(schema: dict, canonical_answer: dict) -> None:
    """`additionalProperties: false` 是"不许模型自由发挥"的唯一机械保证。"""
    payload = dict(canonical_answer, reasoning="模型想多交一段解释")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_unknown_confidence_and_oversized_answer_are_rejected(schema: dict, canonical_answer: dict) -> None:
    validator = jsonschema.Draft202012Validator(schema)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(dict(canonical_answer, confidence="certain"))
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(dict(canonical_answer, answer="长" * 1_201))


# ── AC-28：argv 契约（`--allow-tool` 必须含 submit_result）──────────────────


@pytest.fixture()
def ask_request(tmp_path: Path) -> AgentRequest:
    workspace = tmp_path / "ask-workspace"
    workspace.mkdir()
    prompt = workspace / "ask-ask_test.md"
    prompt.write_text("# prompt", encoding="utf-8")
    system = workspace / "ask_system.md"
    system.write_text("# system", encoding="utf-8")
    schema_file = workspace / "ask.schema.json"
    schema_file.write_text(ASK_SCHEMA_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return AgentRequest(
        run_id="ask-ask_test",
        workspace=workspace,
        prompt_path=prompt,
        system_prompt_path=system,
        schema_path=schema_file,
        instrument_symbol="600519.SH",
        instrument_name="贵州茅台",
        max_turns=12,
        max_duration_ms=180_000,
    )


@pytest.fixture()
def capability() -> AragonCapability:
    """The real constructor fields (see `preflight.AragonCapability`)."""
    return AragonCapability(
        available=True,
        cli="0.6.68",
        versionOk=True,
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=list(ASK_TOOLS),
        tools=[*ASK_TOOLS, "submit_result"],
        resolvedBinary="/usr/bin/aragon",
    )


def test_ask_argv_allows_submit_result(ask_request: AgentRequest, capability: AragonCapability) -> None:
    """AC-28（第三半，★P0-1）：没这条通道，模型永远无法满足 `--result-schema`。"""
    argv = build_ask_argv(ask_request, capability, "/usr/bin/aragon")

    allow = argv[argv.index("--allow-tool") + 1].split(",")
    assert "submit_result" in allow, "ask argv 的 --allow-tool 缺 submit_result（P0-1 回归）"
    assert set(allow) == {*ASK_TOOLS, "submit_result"}
    assert argv[0] == "/usr/bin/aragon"


def test_ask_argv_points_at_the_ask_schema_and_prompt(
    ask_request: AgentRequest, capability: AragonCapability
) -> None:
    argv = build_ask_argv(ask_request, capability, "/usr/bin/aragon")

    assert argv[argv.index("--result-schema") + 1] == str(ask_request.schema_path)
    assert Path(argv[argv.index("--result-schema") + 1]).exists()
    assert argv[argv.index("--append-system-prompt-file") + 1] == str(ask_request.system_prompt_path)
    assert argv[argv.index("--prompt-file") + 1] == str(ask_request.prompt_path)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "strict"


def test_ask_argv_global_flags_precede_exec_and_budget_flags_follow(
    ask_request: AgentRequest, capability: AragonCapability
) -> None:
    """AC-19 的规则同样适用于 ask（§18.3.2）：放错作用域 CLI 只会回 `unknown option`。"""
    argv = build_ask_argv(ask_request, capability, "/usr/bin/aragon")
    exec_index = argv.index("exec")

    for flag in ("--cwd", "--tool-timeout", "--idle-timeout", "--max-tokens", "--no-color"):
        assert flag in argv, f"{flag} missing"
        assert argv.index(flag) < exec_index, f"{flag} 是全局 flag，必须在 exec 之前"

    for flag in ("--max-turns", "--max-duration"):
        assert argv.index(flag) > exec_index, f"{flag} 是 exec 的子命令 flag，必须在 exec 之后"

    assert argv[argv.index("--max-turns") + 1] == "12"
    assert argv[argv.index("--max-duration") + 1] == "180000"
    assert "--max-turns" not in argv[:exec_index]
    assert "--max-duration" not in argv[:exec_index]


def test_ask_argv_refuses_a_capability_without_submit_result(
    ask_request: AgentRequest, capability: AragonCapability
) -> None:
    """反例优先（§18.7.3）：缺 submit_result 必须在拼 argv 时就炸，而不是烧 90s。"""
    from app.agent.runner_base import AgentError

    capability.hasSubmitResult = False
    with pytest.raises(AgentError):
        build_ask_argv(ask_request, capability, "/usr/bin/aragon")


# ── AC-42 姊妹守卫：`metric.polarity` 之后 report_doc schema 仍不破线 ───────


def test_report_schema_still_fits_its_budgets_after_polarity(report_schema: dict) -> None:
    """AC-42：`polarity` 落下后 AC-24（≤ 60,000 B / 深度 ≤ 12）必须仍是绿的。"""
    size = REPORT_SCHEMA_PATH.stat().st_size
    depth = schema_depth(report_schema)

    assert size <= MAX_REPORT_SCHEMA_BYTES, f"report_doc schema 实测 {size} B，上限 {MAX_REPORT_SCHEMA_BYTES} B"
    assert depth <= MAX_REPORT_SCHEMA_DEPTH, (
        f"report_doc schema 实测深度 {depth} 层，上限 {MAX_REPORT_SCHEMA_DEPTH} 层；"
        "加一层嵌套就会撞 CLI 的 13 层硬拒"
    )


def test_report_schema_declares_metric_polarity(report_schema: dict) -> None:
    """V4-P1-1：语义色依赖 `polarity`，缺了它前端只能永远画灰色。"""
    polarity = report_schema["$defs"]["metric"]["properties"]["polarity"]
    allowed = set(polarity["enum"])

    assert {"bullish", "bearish", "neutral"} <= allowed, f"polarity 枚举不全: {allowed}"
    assert "polarity" not in report_schema["$defs"]["metric"].get("required", []), (
        "polarity 是可选字段；历史文档缺它时按 neutral 处理，不能变成校验失败"
    )


def test_polarity_does_not_grow_the_ask_depth_budget(schema: dict, report_schema: dict) -> None:
    """AC-42 与 AC-28 的交叉守卫：两个 schema 各自守自己的上限，互不顶破。"""
    assert schema_depth(schema) <= MAX_SCHEMA_DEPTH
    assert schema_depth(report_schema) <= MAX_REPORT_SCHEMA_DEPTH
    assert schema_depth(schema) < schema_depth(report_schema)
