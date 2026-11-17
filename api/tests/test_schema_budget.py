"""Schema / 资料预算闸门（spec §20.3.3 / M19；AC-70 / AC-71 / AC-72 / AC-88）。

反例优先（§20.7.3）：先写"深度 13 必须被拦、且一次都不许 spawn"，再写"当前 schema
必须通过"。只写后者的测试等于没测——它对任何实现都是绿的。

`depth` 的口径在这里被钉死（V8-P2-11）：根记为 1，**dict / list 与它们的标量叶子各算
一层**。实测 E-52：合成 schema 容器层数 12 → CLI 报 `nests 13 levels deep; the limit is
12` 并 exit 2（3.8 s，没花一次模型调用）；容器 11 → 放行。naive 的"只数容器"DFS 对真源
得 10，比 CLI 少一层，闸门就会放行一个 CLI 必拒的 schema。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.agent import aragon_runner, schema_budget
from app.agent.aragon_runner import build_argv
from app.agent.preflight import AragonCapability
from app.agent.runner_base import AgentError, AgentRequest
from app.services import report_validator

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas"
REAL_SCHEMA = SCHEMAS_DIR / "report_doc.schema.json"

LIMITS = {"schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 5000}
ALL_TOOLS = ["read_file", "glob", "grep", "list_dir", "submit_result"]


def capability(**overrides) -> AragonCapability:
    base = AragonCapability(
        available=True,
        cli="0.6.68",
        versionOk=True,
        provider="openai",
        model="deepseek-flash",
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=list(ALL_TOOLS),
        tools=list(ALL_TOOLS),
        result={**LIMITS, "valueBytes": 262144, "maxRepairTurns": 2},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def nested(depth: int) -> object:
    """构造一份 JSON，使其在本模块口径下**恰好** `depth` 层（标量叶子算一层）。

    用单元素列表做链：`["leaf"]` = 2 层，`[["leaf"]]` = 3 层。这样断言的是口径本身，
    而不是某个具体的 schema 形状——口径与 CLI 的 `nests` 一一对应（V8-P2-11 / E-52）。
    """
    value: object = "leaf"
    for _ in range(max(0, depth - 1)):
        value = [value]
    return value


def _point_schemas_dir(monkeypatch, path: Path) -> None:
    """把 `settings.schemas_dir`（只读 property）指向临时目录。

    `Settings` 是 pydantic 模型，`schemas_dir` 是派生属性而不是字段，所以只能在
    **类**上换成 property——这也正好证明生产代码确实走的是 `get_settings().schemas_dir`。
    """
    from app.config import Settings

    monkeypatch.setattr(Settings, "schemas_dir", property(lambda self: path))


@pytest.fixture()
def request_factory(tmp_path: Path):
    workspace = tmp_path / "run"
    workspace.mkdir()

    def factory(schema_source: Path | None = None, *, source_path: Path | None = None) -> AgentRequest:
        """`schema_source` 决定工作区那份的内容；`source_path` 决定"真源"是谁。

        默认两者都是真源（= 没有漂移）。要构造漂移，传一个不同的 `source_path`
        （或先造好请求再改工作区字节）。
        """
        schema = workspace / "schema.json"
        schema.write_bytes((schema_source or REAL_SCHEMA).read_bytes())
        prompt = workspace / "prompt.md"
        prompt.write_text("# prompt", encoding="utf-8")
        system = workspace / "system.md"
        system.write_text("# system", encoding="utf-8")
        return AgentRequest(
            run_id="budget",
            workspace=workspace,
            prompt_path=prompt,
            system_prompt_path=system,
            schema_path=schema,
            schema_source_path=source_path or schema,
            instrument_symbol="600519.SH",
            instrument_name="贵州茅台",
        )

    return factory


# ── AC-70：当前 schema 的实测值（数字来自 E-46，不是抄来的） ─────────────────


def test_current_schema_budget() -> None:
    """AC-70：`bytes=18392, depth=11, nodes=658, ok=True, headroom=1`。"""
    budget = schema_budget.measure(REAL_SCHEMA, LIMITS)
    assert budget.bytes == 18392
    assert budget.depth == 11
    assert budget.nodes == 658
    assert budget.ok is True
    assert budget.headroom == 1, "深度余量只有一层——这是本轮最该被看见的数字"
    assert budget.reason() is None
    payload = budget.as_payload()
    assert payload["headroom"] == 1 and payload["maxDepth"] == 12 and payload["ok"] is True


def test_depth_counts_scalar_leaves_like_the_cli() -> None:
    """V8-P2-11：`{"type": "string"}` 这个值本身算一层，与 CLI 的 `nests` 口径一致。"""
    assert schema_budget.measure(_write(_tmp_file({"type": "string"}))).depth == 2
    # 深度 12 = 上限：**等于放行**（CLI 的判据是 `nests > 12` 才拒绝，E-52）
    at_limit = schema_budget.measure(_write(_tmp_file(nested(12))), LIMITS)
    assert at_limit.depth == 12 and at_limit.ok is True and at_limit.headroom == 0
    # 深度 13：CLI 必拒，闸门也必须拒
    over = schema_budget.measure(_write(_tmp_file(nested(13))), LIMITS)
    assert over.depth == 13 and over.ok is False
    assert "深度" in (over.reason() or "")


def test_bytes_and_nodes_boundaries(tmp_path: Path) -> None:
    """三档边界：`depth` / `bytes` / `nodes` 各自独立触发，且都写在 `reason()` 里。"""
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"type": "object", "properties": {"a": {"type": "string"}}}), encoding="utf-8")
    tiny = schema_budget.measure(path, {"schemaBytes": 8, "schemaDepth": 12, "validationNodes": 5000})
    assert tiny.ok is False and "体积" in (tiny.reason() or "")

    few = schema_budget.measure(REAL_SCHEMA, {"schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 10})
    assert few.ok is False and "校验节点" in (few.reason() or "")


def test_limits_come_from_the_capability_and_never_become_unlimited() -> None:
    """缺项回退**兜底上限**，绝不回退成"不限"（那样闸门在最需要时失效）。"""
    assert schema_budget.limits_from_capability(capability()) == LIMITS
    assert schema_budget.limits_from_capability(capability(result={})) == schema_budget.FALLBACK_LIMITS
    partial = schema_budget.limits_from_capability(capability(result={"schemaDepth": 8}))
    assert partial["schemaDepth"] == 8
    assert partial["schemaBytes"] == schema_budget.FALLBACK_LIMITS["schemaBytes"]


# ── AC-71：深度超限在 spawn 之前失败，且一次都不 spawn ──────────────────────


def test_over_deep_schema_never_spawns(request_factory, monkeypatch) -> None:
    """AC-71：深度 13 → `schema_over_budget`，且 `create_subprocess_exec` **零调用**。"""
    spawns: list[object] = []

    async def spy(*args, **kwargs):  # noqa: ANN002, ANN003
        spawns.append(args)
        raise AssertionError("闸门拦下的请求不许 spawn")

    monkeypatch.setattr(aragon_runner.asyncio, "create_subprocess_exec", spy)
    deep = _write(_tmp_file(nested(13)))
    request = request_factory(deep)

    runner = aragon_runner.AragonRunner(capability(resolvedBinary="aragon"))

    async def go() -> None:
        with pytest.raises(AgentError) as info:
            await runner.run(request)
        assert info.value.code == "schema_over_budget"
        assert "深度 13" in info.value.message

    asyncio.run(go())
    assert spawns == []
    # ★V8-P2-5：度量**在**闸门失败之前就落盘了——那一次最需要审计材料。
    written = json.loads((request.workspace / "schema_budget.json").read_text(encoding="utf-8"))
    assert written["depth"] == 13 and written["ok"] is False


def test_build_argv_refuses_an_over_budget_schema(request_factory) -> None:
    request = request_factory(_write(_tmp_file(nested(13))))
    with pytest.raises(AgentError) as info:
        build_argv(request, capability(), "aragon")
    assert info.value.code == "schema_over_budget"


# ── AC-72：漂移在 spawn 之前 ────────────────────────────────────────────────


def test_schema_drift_blocks_before_spawn(request_factory) -> None:
    """AC-72：workspace schema 与真源不同一个字节 → `schema_drift`，且在 spawn 之前。"""
    request = request_factory(source_path=REAL_SCHEMA)
    original = request.schema_path.read_bytes()
    # 改一个字节（不是改文本）：闸门比的是 sha1，任何一处差异都算漂移。
    request.schema_path.write_bytes(original[:-1] + (b" " if original[-1:] != b" " else b"\n"))
    assert request.schema_path.read_bytes() != original

    with pytest.raises(AgentError) as info:
        build_argv(request, capability(), "aragon")
    assert info.value.code == "schema_drift"
    # 错误消息里必须写出两份文件的路径与 sha1（R-37：这条故意是硬失败）
    assert "sha1" in info.value.message and "report_doc.schema.json" in info.value.message


def test_a_byte_identical_copy_passes_the_gate(request_factory) -> None:
    """正控：同一个真源逐字节复制过来时必须通过——否则闸门会把每一次生成都拦下。"""
    request = request_factory(source_path=REAL_SCHEMA)
    argv = build_argv(request, capability(), "aragon")
    assert "--result-schema" in argv


def test_assert_schema_identity_returns_the_source_sha1() -> None:
    sha = schema_budget.assert_schema_identity(REAL_SCHEMA, REAL_SCHEMA)
    assert sha == report_validator.schema_sha1()
    assert len(sha) == 12


def test_snapshot_copy_is_asserted_at_write_time(tmp_path: Path, monkeypatch) -> None:
    """`services/snapshot.write_schema_copy()` 是**第一个**调用点（快速失败）。

    它也是"唯一实现、两个调用点"（H-17）里被 `test_boundaries` 允许的那个方向。
    """
    from app.services import snapshot

    _point_schemas_dir(monkeypatch, SCHEMAS_DIR)
    target = tmp_path / "schema.json"
    sha = snapshot.write_schema_copy(target)
    assert sha == report_validator.schema_sha1()
    assert target.read_bytes() == REAL_SCHEMA.read_bytes()


# ── AC-88：校验用**当次交付**的那份 schema（构造性证明） ─────────────────────


def test_validation_uses_the_shipped_schema(sample_report, snapshot_payload, tmp_path: Path, monkeypatch) -> None:
    """AC-88：把"真源"换成一个必然拒绝的 schema，校验仍按 workspace 那份**通过**。

    这是构造性证明，不是时间窗口赌博：`validate_doc(schema_path=…)` 读的就是当次交给
    CLI 的字节，所以"生成那两分钟里有人改了 schema"不再能丢掉一份已付费的报告。
    """
    shipped = tmp_path / "schema.json"
    shipped.write_bytes(REAL_SCHEMA.read_bytes())

    broken_dir = tmp_path / "schemas"
    broken_dir.mkdir()
    (broken_dir / "report_doc.schema.json").write_text(
        json.dumps({"type": "object", "required": ["this_field_does_not_exist"]}), encoding="utf-8"
    )
    _point_schemas_dir(monkeypatch, broken_dir)
    # 让进程级缓存失效，确保"默认回退"真的会读到那份坏 schema。
    report_validator._schema_cache.clear()

    good = report_validator.validate_doc(sample_report, snapshot_payload, schema_path=shipped)
    assert good.errors == [], "当次交付的那份 schema 是好的，校验就必须通过"

    bad = report_validator.validate_doc(sample_report, snapshot_payload)
    assert bad.errors, "没有传 schema_path 时回退到 schemas_dir 的缓存——那份已经坏了"

    report_validator._schema_cache.clear()


# ── 资料预算（prompt + system + schema） ────────────────────────────────────


def test_prompt_budget_blocks_an_inlined_csv(request_factory) -> None:
    """把 47.6 KB 的 bars.csv 内联进 prompt 是很容易顺手做的一件事，也是本病最贵的动作。"""
    request = request_factory()
    request.prompt_path.write_text("x" * 40_000, encoding="utf-8")
    with pytest.raises(AgentError) as info:
        build_argv(request, capability(), "aragon")
    assert info.value.code == "prompt_over_budget"

    total = schema_budget.assert_prompt_budget(request_factory(), 32768)
    assert 18_000 < total < 32_768


def _tmp_file(payload: dict) -> Path:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        return Path(handle.name)


def _write(path: Path) -> Path:
    return path
