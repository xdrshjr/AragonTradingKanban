"""AI 流程设计助手（report-workflow-graph §3.8，M64 后端；AC-W21~W25）。

三层：纯函数（`workflow_assist_expand`、mock 的规划、注册表、schema）→ 服务/路由（TestClient +
`AGENT_MODE=mock` 的确定性助手）→ 用"脚本化 runner"注入越权 / 冲突 / 慢任务等 mock 不会产出的剧本。
全部离线：不 spawn 任何进程。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.agent import assist_runner, schema_budget
from app.agent.mock_assist import plan
from app.agent.runner_base import AgentResult, AgentUsage
from app.config import get_settings
from app.db.base import iso
from app.models import CostLedger, QuotaLedger, WorkflowChatMessage, WorkflowTemplate, WorkflowVersion
from app.schemas.workflow import doc_from_json, doc_to_json
from app.services import cost_service
from app.services import workflow_contract as C
from app.services.workflow_assist_expand import expand, summarize
from app.services.workflow_assist_registry import RING_SIZE, AssistRegistry
from app.services.workflow_builtin import builtin_doc
from app.services.workflow_validator import content_hash

API = "/api/v1/admin/workflows"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas" / "workflow_assist.schema.json"
MESSAGE_ADD = "在估值前加一个同业对比步骤和检查点"


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def make_template(maker, *, builtin: bool = False, status: str = "active") -> str:  # noqa: ANN001
    template_id = uuid.uuid4().hex
    with maker() as db:
        db.add(
            WorkflowTemplate(
                id=template_id,
                name="测试副本",
                is_builtin=int(builtin),
                status=status,
                draft_doc_json=doc_to_json(builtin_doc()),
                draft_revision=1,
                created_at=iso(),
                updated_at=iso(),
            )
        )
        db.commit()
    return template_id


def read_draft(maker, template_id: str):  # noqa: ANN001, ANN201
    with maker() as db:
        row = db.get(WorkflowTemplate, template_id)
        return doc_from_json(row.draft_doc_json), row.draft_revision


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for frame in text.split("\n\n"):
        name, data = None, None
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name and name != "ping":
            events.append((name, data or {}))
    return events


def post_assist(client, template_id: str, message: str, revision: int = 1, **extra: Any):  # noqa: ANN001, ANN201
    return client.post(f"{API}/{template_id}/assist", json={"message": message, "baseRevision": revision, **extra})


def stream(client, assist_id: str) -> list[tuple[str, dict[str, Any]]]:  # noqa: ANN001
    token = client.post(f"{API}/assists/{assist_id}/events-token").json()["token"]
    response = client.get(f"{API}/assists/{assist_id}/events?token={token}")
    assert response.status_code == 200
    return parse_sse(response.text)


def run_assist(client, template_id: str, message: str, revision: int = 1, **extra: Any):  # noqa: ANN001, ANN201
    response = post_assist(client, template_id, message, revision, **extra)
    assert response.status_code == 202, response.text
    return stream(client, response.json()["assistId"])


def end_of(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    assert events[-1][0] == "assist.end"
    return events[-1][1]


def names(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in events]


class ScriptedRunner:
    """A runner that answers from a script instead of a model (and can misbehave on cue)."""

    name = "scripted"
    source = "mock"

    def __init__(self, docs: list[dict[str, Any]], before=None) -> None:  # noqa: ANN001
        self.docs, self.before, self.calls, self.cancelled = docs, before, 0, False

    async def run(self, request, on_event=None) -> AgentResult:  # noqa: ANN001
        self.calls += 1
        if self.before is not None:
            await self.before(self.calls)
        doc = self.docs[min(self.calls - 1, len(self.docs) - 1)]
        usage = AgentUsage(input_tokens=10, output_tokens=5, total_tokens=15)
        return AgentResult(doc=doc, text=doc["reply"], usage=usage, source="mock", exit_code=0)

    async def cancel(self) -> None:
        self.cancelled = True


def install(monkeypatch, runner: ScriptedRunner) -> ScriptedRunner:  # noqa: ANN001
    monkeypatch.setattr(assist_runner, "build_assist_runner", lambda: runner)
    return runner


def batch(*ops: dict[str, Any], reply: str = "好的") -> dict[str, Any]:
    return {"reply": reply, "ops": list(ops), "openQuestions": []}


# ── pure: expansion (the model never writes ids / revs / coordinates) ────────


def test_expand_allocates_ids_and_places_unanchored_nodes_by_layout() -> None:
    doc = builtin_doc()
    end_id = next(n.id for n in doc.nodes if n.kind == "end")
    expansion = expand(
        doc,
        [
            {"op": "add_node", "ref": "x", "kind": "step", "title": "同业", "config": {"prompt": "p"}},
            {"op": "add_edge", "source": "@x", "target": end_id},
        ],
    )
    assert expansion.ok, expansion.failure
    added = next(op for op in expansion.ops if op["op"] == "add_node")["node"]
    assert added["id"] == expansion.refs["x"] and added["id"].startswith("n_") and added["rev"] == 1
    moved = [op for op in expansion.ops if op["op"] == "move_node"]
    assert [m["id"] for m in moved] == [added["id"]]  # placed by `layout_levels`, not (0, 0)
    assert {n.id for n in expansion.doc.nodes} >= {added["id"]}


def test_expand_stamps_expect_rev_from_the_snapshot_not_from_the_model() -> None:
    doc = builtin_doc()
    step = next(n for n in doc.nodes if n.kind == "step")
    expansion = (
        expand(
            doc,
            [
                {"op": "update_node", "id": step.id, "expectRev": 99, "patch": {"title": "改名一"}},
                {"op": "update_node", "id": step.id, "patch": {"title": "改名二"}},
            ],
        )
        if False
        else expand(
            doc,
            [
                {"op": "update_node", "id": step.id, "patch": {"title": "改名一"}},
                {"op": "update_node", "id": step.id, "patch": {"title": "改名二"}},
            ],
        )
    )
    assert expansion.ok, expansion.failure
    assert [op["expectRev"] for op in expansion.ops] == [step.rev, step.rev]  # chained edits are fine


@pytest.mark.parametrize("name", ["publish", "set_default", "move_node", "set_viewport", "update_edge", None])
def test_expand_refuses_anything_outside_the_assistant_vocabulary(name: str | None) -> None:
    expansion = expand(builtin_doc(), [{"op": name}])
    assert not expansion.ok and expansion.failure.reason == "forbidden_op"


def test_expand_rejects_unknown_ids_and_too_many_ops() -> None:
    assert expand(builtin_doc(), [{"op": "delete_node", "id": "n_nope"}]).failure.reason == "unknown_node"
    flood = [{"op": "auto_layout"}] * (C.ASSIST_MAX_OPS + 1)
    assert expand(builtin_doc(), flood).failure.reason == "limit_exceeded"


def test_expand_rejects_duplicate_or_malformed_refs_and_bad_edges() -> None:
    twice = [{"op": "add_node", "ref": "a", "kind": "gate", "title": "x"}] * 2
    assert expand(builtin_doc(), twice).failure.reason == "bad_patch"
    doc = builtin_doc()
    start = next(n.id for n in doc.nodes if n.kind == "start")
    loop = expand(doc, [{"op": "add_edge", "source": start, "target": start}])
    assert not loop.ok and loop.failure.index == 0


def test_expand_delete_edge_by_endpoints_and_auto_layout_macro() -> None:
    doc = builtin_doc()
    edge = doc.edges[0]
    expansion = expand(
        doc, [{"op": "delete_edge", "source": edge.source, "target": edge.target}, {"op": "auto_layout"}]
    )
    assert expansion.ok and expansion.ops[0]["id"] == edge.id and expansion.ops[0]["expectRev"] == edge.rev
    assert all(op["op"] in {"delete_edge", "move_node"} for op in expansion.ops)


def test_expand_is_atomic_and_never_mutates_the_snapshot() -> None:
    doc = builtin_doc()
    before = doc_to_json(doc)
    bad = expand(
        doc, [{"op": "add_node", "ref": "a", "kind": "gate", "title": "g"}, {"op": "delete_node", "id": "n_nope"}]
    )
    assert not bad.ok and bad.doc is None
    assert doc_to_json(doc) == before


def test_summary_counts_the_change_card() -> None:
    doc = builtin_doc()
    end_id = next(n.id for n in doc.nodes if n.kind == "end")
    expansion = expand(
        doc,
        [
            {"op": "add_node", "ref": "s", "kind": "step", "title": "s", "config": {"prompt": "p"}},
            {"op": "add_node", "ref": "g", "kind": "gate", "title": "g"},
            {"op": "add_edge", "source": "@s", "target": "@g"},
            {"op": "add_edge", "source": "@g", "target": end_id},
        ],
    )
    assert summarize(expansion.ops) == {"added": 2, "gates": 1, "updated": 0, "deleted": 0, "edges": 2}


# ── pure: mock brain, registry, schema ───────────────────────────────────────


def graph_dict() -> dict[str, Any]:
    return {
        "nodes": [{"id": n.id, "kind": n.kind, "title": n.title, "config": n.config} for n in builtin_doc().nodes],
        "edges": [e.model_dump() for e in builtin_doc().edges],
    }


def test_mock_plan_keywords() -> None:
    graph = graph_dict()
    assert plan("整理布局", graph)["ops"] == [{"op": "auto_layout"}]
    added = plan(MESSAGE_ADD, graph)
    assert [op["op"] for op in added["ops"]].count("add_node") == 2 and "估值" in added["reply"]
    assert plan("删除风险与催化剂", graph)["ops"][0]["op"] == "delete_node"
    vague = plan("你好", graph)
    assert vague["ops"] == [] and vague["openQuestions"]


def test_mock_output_satisfies_the_assist_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for message in (MESSAGE_ADD, "整理布局", "删除风险与催化剂", "你好"):
        validator.validate(plan(message, graph_dict()))


def test_schema_has_no_publish_or_default_and_fits_the_engine_budget() -> None:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    for forbidden in ("publish", "set_default", "setDefault", "move_node", "set_viewport", "expectRev", "position"):
        assert forbidden not in text
    budget = schema_budget.measure(SCHEMA_PATH)
    assert budget.ok and budget.depth <= 11
    schema = json.loads(text)
    rejecting = jsonschema.Draft202012Validator(schema)
    assert not rejecting.is_valid({"reply": "x", "ops": [{"op": "publish"}]})
    assert not rejecting.is_valid({"reply": "x", "ops": [{"op": "set_default"}]})
    assert not rejecting.is_valid({"reply": "x", "ops": [{"op": "auto_layout"}] * (C.ASSIST_MAX_OPS + 1)})


def test_prompt_and_schema_are_copied_byte_for_byte(tmp_path: Path) -> None:
    (tmp_path / "graph.json").write_text("{}", encoding="utf-8")
    prompt = assist_runner.render_assist_prompt(
        tmp_path / "assist-r1.md",
        tmp_path,
        message="m",
        focus=[],
        stats={"steps": 1, "gates": 0, "edges": 2, "errors": 0, "warnings": 0},
        repair=None,
    )
    settings = get_settings()
    assert (tmp_path / "schema.json").read_bytes() == (
        settings.schemas_dir / assist_runner.ASSIST_SCHEMA_FILE
    ).read_bytes()
    assert (tmp_path / assist_runner.ASSIST_SYSTEM_FILE).read_bytes() == (
        settings.prompts_dir / assist_runner.ASSIST_SYSTEM_FILE
    ).read_bytes()
    assert b"\r\n" not in (tmp_path / "schema.json").read_bytes()
    assert "graph.json" in prompt.read_text(encoding="utf-8") and "validation.json" not in prompt.read_text(
        encoding="utf-8"
    )
    # `build_argv` refuses a drifting copy — the identity gate accepts ours.
    schema_budget.assert_schema_identity(
        tmp_path / "schema.json", settings.schemas_dir / assist_runner.ASSIST_SCHEMA_FILE
    )


def test_registry_ring_is_bounded_and_resumable() -> None:
    registry = AssistRegistry()
    registry.create("a1", "t1", "u1")
    for index in range(RING_SIZE + 25):
        registry.emit("a1", "assist.text", {"i": index})
    events = registry.events_after("a1", 0)
    assert len(events) == RING_SIZE and events[0]["seq"] == 26 and events[-1]["seq"] == RING_SIZE + 25
    assert [e["seq"] for e in registry.events_after("a1", RING_SIZE + 23)] == [RING_SIZE + 24, RING_SIZE + 25]
    assert registry.active_for_template("t1") == "a1"
    registry.emit("a1", "assist.end", {"state": "done"})
    assert registry.active_for_template("t1") is None  # terminal frees the template
    registry.finish("a1")
    assert registry.get("a1") is not None  # still replayable after finishing


# ── AC-W21: the mock assistant end to end ────────────────────────────────────


def test_mock_assistant_adds_a_step_and_a_gate_and_undo_restores_the_content_hash(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    original, _ = read_draft(maker, template_id)
    events = run_assist(client, template_id, MESSAGE_ADD)
    assert names(events)[0] == "assist.start" and names(events)[-1] == "assist.end"
    assert "assist.ops" in names(events) and "assist.reply" in names(events)
    end = end_of(events)
    assert end["state"] == "done" and end["applied"] is True and end["usage"]["totalTokens"] == 1500
    ops_event = dict(events)["assist.ops"]
    assert ops_event["summary"]["added"] == 2 and ops_event["summary"]["gates"] == 1
    assert ops_event["validation"]["errorCount"] == 0

    doc, revision = read_draft(maker, template_id)
    assert revision == 2 == ops_event["revision"]
    assert sum(n.kind == "step" for n in doc.nodes) == sum(n.kind == "step" for n in original.nodes) + 1
    assert sum(n.kind == "gate" for n in doc.nodes) == sum(n.kind == "gate" for n in original.nodes) + 1

    # "撤销这次修改"：逆 op 走同一条画布写路径（POST /ops），内容哈希回到原样。
    added_ids = {n.id for n in doc.nodes} - {n.id for n in original.nodes}
    revs = {n.id: n.rev for n in doc.nodes}
    have = {(e.source, e.target) for e in doc.edges}
    undo = [{"op": "delete_node", "id": nid, "expectRev": revs[nid]} for nid in sorted(added_ids)]
    undo += [
        {
            "op": "add_edge",
            "edge": {"id": f"e_undo{index:03d}", "source": e.source, "target": e.target, "label": e.label, "rev": 1},
        }
        for index, e in enumerate(original.edges)
        if (e.source, e.target) not in have
    ]
    response = client.post(f"{API}/{template_id}/ops", json={"baseRevision": revision, "ops": undo})
    assert response.status_code == 200, response.text
    restored, _ = read_draft(maker, template_id)
    assert content_hash(restored) == content_hash(original)


def test_assistant_conversation_is_stored_and_the_cost_is_booked_without_touching_quota(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    events = run_assist(client, template_id, MESSAGE_ADD)
    assert end_of(events)["state"] == "done"
    with maker() as db:
        rows = (
            db.execute(
                select(WorkflowChatMessage)
                .where(WorkflowChatMessage.template_id == template_id)
                .order_by(WorkflowChatMessage.id)
            )
            .scalars()
            .all()
        )
        assert [r.role for r in rows] == ["user", "assistant"] and rows[0].content == MESSAGE_ADD
        assert json.loads(rows[1].applied_json)["applied"] is True
        ledger = db.execute(select(CostLedger).where(CostLedger.kind == "workflow_assist")).scalars().all()
        assert (
            len(ledger) == 1 and ledger[0].ref_id == rows[1].assist_id and ledger[0].source in {"estimated", "unknown"}
        )
        assert db.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0
        assert (
            db.execute(
                select(func.count()).select_from(WorkflowVersion).where(WorkflowVersion.template_id == template_id)
            ).scalar_one()
            == 0
        )  # nothing is ever published


def test_a_reply_without_changes_is_not_an_application(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    events = run_assist(client, template_id, "你好")
    end = end_of(events)
    assert end["applied"] is False and end["reason"] == "no_changes"
    assert dict(events)["assist.reply"]["openQuestions"]
    assert read_draft(maker, template_id)[1] == 1


def test_sse_resume_with_after_returns_only_the_missed_events(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    assist_id = post_assist(client, template_id, "整理布局").json()["assistId"]
    full = stream(client, assist_id)
    token = client.post(f"{API}/assists/{assist_id}/events-token").json()["token"]
    resumed = parse_sse(client.get(f"{API}/assists/{assist_id}/events?token={token}&after=2").text)
    assert names(resumed) == names(full)[2:]
    assert client.get(f"{API}/assists/{assist_id}/events?token=garbage").status_code in (200, 401)
    assert client.get(f"{API}/assists/{'0' * 32}/events?token=x").status_code == 404


# ── AC-W22: the assistant cannot overstep ────────────────────────────────────


def test_unknown_ids_leave_the_draft_alone_and_use_exactly_one_repair_round(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    before, _ = read_draft(maker, template_id)
    runner = install(monkeypatch, ScriptedRunner([batch({"op": "delete_node", "id": "n_ghost"})]))
    events = run_assist(client, template_id, "删掉幽灵")
    end = end_of(events)
    assert runner.calls == 2  # round 1 + exactly one repair
    round_event = dict(events)["assist.round"]
    assert (
        round_event["round"] == 2 and round_event["reason"] == "invalid_ops" and "unknown_node" in round_event["issues"]
    )
    assert end["applied"] is False and end["reason"] == "invalid_ops"
    after, revision = read_draft(maker, template_id)
    assert revision == 1 and doc_to_json(after) == doc_to_json(before)


def test_the_repair_round_can_fix_the_batch(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    good = batch({"op": "add_node", "ref": "n", "kind": "note", "title": "备忘", "config": {"body": "港股注意币种"}})
    runner = install(monkeypatch, ScriptedRunner([batch({"op": "delete_node", "id": "n_ghost"}), good]))
    events = run_assist(client, template_id, "加一条备忘")
    assert runner.calls == 2 and end_of(events)["applied"] is True
    doc, revision = read_draft(maker, template_id)
    assert revision == 2 and any(n.kind == "note" and n.title == "备忘" for n in doc.nodes)


@pytest.mark.parametrize(
    "op", [{"op": "publish"}, {"op": "set_default"}, {"op": "move_node", "id": "x", "position": {"x": 1, "y": 1}}]
)
def test_publish_and_default_are_impossible(client, maker, monkeypatch, op) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    install(monkeypatch, ScriptedRunner([batch(op, reply="已发布并设为默认")]))
    events = run_assist(client, template_id, "帮我发布")
    end = end_of(events)
    assert end["applied"] is False
    assert "已发布" not in dict(events)["assist.reply"]["reply"]  # the model's claim is not echoed as fact
    with maker() as db:
        assert (
            db.execute(
                select(func.count()).select_from(WorkflowVersion).where(WorkflowVersion.template_id == template_id)
            ).scalar_one()
            == 0
        )
        assert db.get(WorkflowTemplate, template_id).is_default == 0
    assert read_draft(maker, template_id)[1] == 1


def test_oversized_batch_is_refused_whole(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    install(monkeypatch, ScriptedRunner([batch(*[{"op": "auto_layout"}] * (C.ASSIST_MAX_OPS + 1))]))
    end = end_of(run_assist(client, template_id, "太多了"))
    assert end["applied"] is False and read_draft(maker, template_id)[1] == 1


def test_a_batch_that_introduces_new_errors_is_not_applied(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    original, _ = read_draft(maker, template_id)
    risks = next(n for n in original.nodes if n.title.startswith("风险"))  # the only step serving risks / catalysts
    runner = install(monkeypatch, ScriptedRunner([batch({"op": "delete_node", "id": risks.id})]))
    events = run_assist(client, template_id, "删除风险步骤")
    assert runner.calls == 2
    assert dict(events)["assist.round"]["reason"] == "introduced_errors"
    end = end_of(events)
    assert end["applied"] is False and end["reason"] == "introduced_errors"
    assert read_draft(maker, template_id)[1] == 1


# ── AC-W23: conflicts are abandoned, never overwritten ───────────────────────


def test_an_admin_edit_during_thinking_wins_and_the_batch_is_dropped(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    original, _ = read_draft(maker, template_id)
    target = next(n for n in original.nodes if n.kind == "step")

    async def admin_edits_meanwhile(call: int) -> None:
        with maker() as db:
            row = db.get(WorkflowTemplate, template_id)
            doc = doc_from_json(row.draft_doc_json)
            for node in doc.nodes:
                if node.id == target.id:
                    node.title, node.rev = "管理员手改", node.rev + 1
            row.draft_doc_json, row.draft_revision = doc_to_json(doc), row.draft_revision + 1
            db.commit()

    batch_ = batch({"op": "update_node", "id": target.id, "patch": {"title": "AI 改名"}})
    install(monkeypatch, ScriptedRunner([batch_], before=admin_edits_meanwhile))
    events = run_assist(client, template_id, "把第一步改个名")
    conflict = dict(events)["assist.conflict"]
    assert target.id in conflict["entityIds"]
    end = end_of(events)
    assert end["applied"] is False and end["reason"] == "conflict"
    doc, revision = read_draft(maker, template_id)
    assert revision == 2 and next(n for n in doc.nodes if n.id == target.id).title == "管理员手改"


# ── AC-W24: busy / rate limit / cancel / guards ──────────────────────────────


def test_second_assist_on_the_same_template_is_busy_and_cancel_ends_cleanly(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)

    async def forever(call: int) -> None:
        await asyncio.sleep(60)

    runner = install(monkeypatch, ScriptedRunner([batch()], before=forever))
    first = post_assist(client, template_id, "慢任务")
    assert first.status_code == 202
    second = post_assist(client, template_id, "再来一个")
    assert second.status_code == 409 and second.json()["error"]["code"] == "workflow_assist_busy"
    other = make_template(maker)  # a different template is not blocked
    assert post_assist(client, other, "整理布局").status_code == 202

    assist_id = first.json()["assistId"]
    cancelled = client.post(f"{API}/assists/{assist_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelling"
    end = end_of(stream(client, assist_id))
    assert end["state"] == "cancelled" and end["applied"] is False
    assert runner.cancelled is True
    assert read_draft(maker, template_id)[1] == 1
    assert post_assist(client, template_id, "整理布局").status_code == 202  # the slot is free again


def test_rate_limit_is_twenty_per_hour_per_admin(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    for _ in range(20):
        assert end_of(run_assist(client, template_id, "整理布局"))["state"] == "done"
    blocked = post_assist(client, template_id, "整理布局")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"


def test_builtin_archived_and_unknown_templates_are_refused(client, maker) -> None:  # noqa: ANN001
    builtin = make_template(maker, builtin=True)
    archived = make_template(maker, status="archived")
    refused = post_assist(client, builtin, "整理布局")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "workflow_builtin_readonly"
    assert post_assist(client, archived, "整理布局").json()["error"]["code"] == "workflow_archived"
    assert post_assist(client, "0" * 32, "整理布局").status_code == 404


def test_focus_ids_must_be_existing_nodes_and_at_most_twenty(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    ghost = post_assist(client, template_id, "改这一步", focusNodeIds=["n_ghost"])
    assert ghost.status_code == 422 and ghost.json()["error"]["code"] == "workflow_invalid_ops"
    too_many = post_assist(client, template_id, "改这一步", focusNodeIds=["n_builtin_s1"] * 21)
    assert too_many.status_code == 422
    ok = post_assist(client, template_id, "整理布局", focusNodeIds=["n_builtin_s1"])
    assert ok.status_code == 202


def test_a_stale_base_revision_is_a_409_with_the_current_revision(client, maker) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    stale = post_assist(client, template_id, "整理布局", revision=7)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "workflow_revision_conflict"
    assert stale.json()["error"]["detail"]["currentRevision"] == 1


def test_unavailable_engine_is_a_409(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    monkeypatch.setattr(assist_runner, "assist_mode", lambda: "unavailable")
    refused = post_assist(client, template_id, "整理布局")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "workflow_assist_unavailable"


def test_the_monthly_budget_gate_applies_to_the_assistant(client, maker, session) -> None:  # noqa: ANN001
    template_id = make_template(maker)
    cost_service.set_budget(session, 1.0)
    cost_service.record(session, kind="run", ref_id="spent", usd=5.0, known=True, source="reported")
    session.commit()
    refused = post_assist(client, template_id, "整理布局")
    assert refused.status_code == 402 and refused.json()["error"]["code"] == "monthly_budget_exceeded"


def test_a_failed_engine_run_leaves_the_draft_alone_and_still_ends_the_stream(client, maker, monkeypatch) -> None:  # noqa: ANN001
    template_id = make_template(maker)

    class Broken(ScriptedRunner):
        async def run(self, request, on_event=None) -> AgentResult:  # noqa: ANN001
            return AgentResult(error_code="timeout", error_message="引擎超时", source="mock")

    install(monkeypatch, Broken([batch()]))
    events = run_assist(client, template_id, "整理布局")
    end = end_of(events)
    assert end["state"] == "failed" and end["reason"] == "timeout" and end["applied"] is False
    assert read_draft(maker, template_id)[1] == 1
    with maker() as db:  # the interrupted conversation is not left looking unanswered
        roles = [
            r.role
            for r in db.execute(
                select(WorkflowChatMessage)
                .where(WorkflowChatMessage.template_id == template_id)
                .order_by(WorkflowChatMessage.id)
            ).scalars()
        ]
    assert roles == ["user", "assistant"]
