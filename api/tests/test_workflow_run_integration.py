"""研究流程接入生成管线（report-workflow-graph §3.6；AC-W11~W17）。

引擎用桩（不 spawn、不联网），断言的是**编排**：冻结、回退、遵循度、标记剔除、进度单调。
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.runner_base import AgentEvent, AgentEventType, AgentResult
from app.db.base import iso
from app.models import Report, Run, RunEvent, Setting, WorkflowTemplate, WorkflowVersion
from app.schemas.workflow import doc_to_json
from app.services import run_manager as run_manager_module
from app.services import settings_service, workflow_runtime
from app.services import snapshot as snapshot_service
from app.services.market_data.base import SourceRef
from app.services.workflow_builtin import builtin_doc
from app.services.workflow_compiler import compile_workflow, sample_context
from app.services.workflow_validator import content_hash


class MarkerRunner:
    """Emits `chunks` as text deltas, then returns the sample document (or an error)."""

    name = "stub"
    source = "mock"

    def __init__(self, doc: dict, chunks: list[str], *, result_text: str = "", error: str | None = None) -> None:
        self.doc, self.chunks, self.result_text, self.error = doc, chunks, result_text, error

    async def stream(self, request, on_event=None):  # noqa: ANN001
        if False:  # pragma: no cover
            yield None

    async def run(self, request, on_event=None) -> AgentResult:  # noqa: ANN001
        for chunk in self.chunks:
            if on_event:
                on_event(AgentEvent(AgentEventType.text, {"delta": chunk}))
        if self.error:
            return AgentResult(
                doc=None, error_code=self.error, error_message="stub", source="mock", text=self.result_text
            )
        return AgentResult(doc=copy.deepcopy(self.doc), source="mock", exit_code=0, text=self.result_text)

    async def cancel(self) -> None:
        return None


class Env:
    def __init__(self, session, instrument, sample_report, state, tmp_path) -> None:  # noqa: ANN001
        self.session, self.instrument, self.sample = session, instrument, sample_report
        self.state, self.tmp = state, tmp_path

    def runner(self, chunks: list[str], **kwargs) -> None:  # noqa: ANN003
        self.state["runner"] = MarkerRunner(self.sample, chunks, **kwargs)

    def make_run(self, run_id: str) -> Run:
        workspace = self.tmp / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        resolved = workflow_runtime.resolve_for_run(self.session)
        row = Run(
            id=run_id,
            instrument_id=self.instrument.id,
            status="queued",
            pct=0,
            started_at="2026-02-10T00:00:00Z",
            cost_known=0,
            workspace_path=str(workspace),
            workflow_version_id=resolved.version_id if resolved else None,
            workflow_label=resolved.label if resolved else None,
        )
        self.session.add(row)
        self.session.commit()
        return row

    def keys(self, run: Run) -> list[str]:
        prepared = workflow_runtime.prepare(self.session, run, {"instrument": {}})
        return [item.key for item in prepared.compiled.items]

    def drive(self, run: Run) -> Run:
        manager = run_manager_module.RunManager()
        asyncio.run(manager._pipeline(self.session, run, self.instrument, force=False))  # noqa: SLF001
        self.session.expire_all()
        return self.session.get(Run, run.id)

    def events(self, run_id: str, kind: str) -> list[dict]:
        rows = self.session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)).scalars()
        return [json.loads(r.payload_json) for r in rows if r.type == kind]

    def quality(self) -> dict:
        report = self.session.execute(select(Report)).scalars().first()
        return json.loads(report.quality_json)


@pytest.fixture()
def env(session, instrument, sample_report, snapshot_payload, monkeypatch, tmp_path) -> Env:
    bundle = snapshot_service.SnapshotBundle(
        snapshot=snapshot_payload,
        bars=[],
        fundamentals=[],
        sources=[SourceRef("S1", "行情快照", "synthetic", datetime(2026, 2, 10, tzinfo=UTC).date())],
        warnings=[],
    )

    async def fake_build_snapshot(*_a, **_k):  # noqa: ANN002, ANN003
        return bundle

    monkeypatch.setattr(snapshot_service, "build_snapshot", fake_build_snapshot)
    state: dict = {"runner": None}
    monkeypatch.setattr(run_manager_module, "build_runner", lambda *_a, **_k: state["runner"])
    for row in session.execute(select(WorkflowTemplate)).scalars().all():
        session.delete(row)
    session.commit()
    return Env(session, instrument, sample_report, state, tmp_path)


def _markers(keys: list[str]) -> list[str]:
    return [f"[[wf:{key}:{state}]]\n" for key in keys for state in ("start", "done")]


def test_enabled_without_any_default_row_uses_the_builtin_code_workflow(env, session) -> None:
    """RW-2：无默认行 ≠ 关闭；走 builtin-code，且生成成功、遵循度 full。"""
    run = env.make_run("r1")
    assert run.workflow_label == workflow_runtime.BUILTIN_CODE_LABEL and run.workflow_version_id is None
    env.runner(_markers(env.keys(run)))
    run = env.drive(run)
    assert run.status == "succeeded" and run.workflow_adherence == "full"
    workspace = Path(run.workspace_path)
    assert (workspace / "workflow.md").exists() and (workspace / "workflow.json").exists()
    prompt = (workspace / "prompt.md").read_text(encoding="utf-8")
    assert "workflow.md" in prompt
    quality = env.quality()["workflow"]
    assert quality["source"] == "builtin-code" and quality["adherence"] == "full"
    assert quality["steps"]["done"] == quality["steps"]["total"]


def test_disabled_flag_keeps_the_legacy_prompt(env, session) -> None:
    """AC-W12：只有 `workflow.enabled=0` 走旧提示词：无 workflow.md、无事件、列为空。"""
    settings_service.set_str(session, settings_service.KEY_WORKFLOW_ENABLED, "0")
    session.commit()
    run = env.make_run("r2")
    assert run.workflow_label is None
    env.runner([])
    run = env.drive(run)
    workspace = Path(run.workspace_path)
    assert not (workspace / "workflow.md").exists() and not (workspace / "workflow.json").exists()
    prompt = (workspace / "prompt.md").read_text(encoding="utf-8")
    assert "研究流程" not in prompt and "{%" not in prompt and "{{" not in prompt
    assert env.events("r2", "run.workflow") == []
    assert run.workflow_adherence is None and run.workflow_version_id is None
    assert "workflow" not in env.quality()


@pytest.mark.parametrize(("share", "expected"), [(1.0, "full"), (0.4, "partial"), (0.0, "none")])
def test_adherence_is_observed_and_never_fails_the_run(env, session, share, expected) -> None:
    run = env.make_run("r3")
    keys = env.keys(run)
    env.runner(_markers(keys[: int(len(keys) * share)]))
    run = env.drive(run)
    assert run.status == "succeeded" and run.workflow_adherence == expected


def test_markers_split_across_deltas_are_parsed_and_never_shown(env) -> None:
    run = env.make_run("r4")
    env.runner(["正文一[[wf:S1:", "start]]\n中间", "[[wf:S1:do", "ne]]\n正文二"])
    env.drive(run)
    texts = "".join(p.get("delta", "") for p in env.events("r4", "agent.text"))
    assert "[[wf" not in texts and "正文一" in texts and "正文二" in texts
    steps = [e for e in env.events("r4", "run.workflow") if e["kind"] == "step"]
    assert [(e["key"], e["state"]) for e in steps] == [("S1", "start"), ("S1", "done")]


def test_pure_marker_delta_writes_no_empty_agent_text(env) -> None:
    run = env.make_run("r5")
    env.runner(["[[wf:S1:start]]"])
    env.drive(run)
    assert all(p.get("delta") for p in env.events("r5", "agent.text"))


def test_result_text_is_stripped_before_the_provisional_report(env, session) -> None:
    """RW-3 / RW-12：失败 run 的临时报告不含标记，且仍写遵循度。"""
    run = env.make_run("r6")
    env.runner(["[[wf:S1:done]]"], result_text="[[wf:S1:done]]\n只有一句失败说明", error="agent_error")
    run = env.drive(run)
    assert run.status == "failed"
    assert run.workflow_adherence in ("partial", "none")
    report = session.execute(select(Report)).scalars().first()
    assert report is not None and "[[wf:" not in report.doc_json


def test_progress_is_monotonic_and_capped_before_submission(env) -> None:
    run = env.make_run("r7")
    env.runner(_markers(env.keys(run)))
    env.drive(run)
    pcts = [p["pct"] for p in env.events("r7", "run.progress") if "pct" in p]
    assert pcts and pcts == sorted(pcts) and max(pcts) <= 69


def _publish_default(session, *, doc_json: str | None = None, name: str = "自定义流程") -> None:
    doc = builtin_doc()
    now = iso()
    session.add(
        WorkflowTemplate(
            id="wf_t1", name=name, description="", is_builtin=0, is_default=1, status="active",
            draft_doc_json=doc_to_json(doc), draft_revision=1, current_version_id="wv_1",
            created_at=now, updated_at=now,
        )  # fmt: skip
    )
    session.flush()
    session.add(
        WorkflowVersion(
            id="wv_1", template_id="wf_t1", version=3,
            doc_json=doc_json or doc_to_json(doc, with_viewport=False),
            doc_sha1=content_hash(doc), change_note="", step_count=6, plan_bytes=1, published_at=now,
        )  # fmt: skip
    )
    session.commit()


def test_run_is_frozen_to_the_version_it_started_with(env, session) -> None:
    """AC-W11：start 之后切默认/改名，该 run 仍用原版本。"""
    _publish_default(session)
    run = env.make_run("r8")
    assert run.workflow_version_id == "wv_1" and run.workflow_label == "自定义流程 v3"
    template = session.get(WorkflowTemplate, "wf_t1")
    template.is_default = 0
    session.commit()
    env.runner([])
    env.drive(run)
    assert env.events("r8", "run.workflow")[0]["templateName"] == "自定义流程"
    quality = env.quality()["workflow"]
    assert quality["source"] == "published" and quality["version"] == 3


@pytest.mark.parametrize("bad", ["{not json", json.dumps({"schemaVersion": 1, "nodes": [], "edges": []})])
def test_broken_version_falls_back_without_failing(env, session, bad) -> None:
    """AC-W14：文档损坏 / 编译失败 → builtin-code 回退，生成照常成功并留痕。"""
    _publish_default(session, doc_json=bad)
    run = env.make_run("r9")
    env.runner([])
    run = env.drive(run)
    assert run.status == "succeeded"
    quality = env.quality()["workflow"]
    assert quality["source"] == "builtin-code" and quality["fallbackReason"] in ("doc_corrupt", "compile_failed")
    assert any(entry.get("code") == "workflow_fallback" for entry in env.events("r9", "run.log"))


def test_resolve_never_raises_and_respects_the_switch(session) -> None:
    assert workflow_runtime.resolve_for_run(session).label == workflow_runtime.BUILTIN_CODE_LABEL
    session.add(Setting(key=settings_service.KEY_WORKFLOW_ENABLED, value="0"))
    session.commit()
    assert workflow_runtime.resolve_for_run(session) is None


def test_the_builtin_workflow_always_compiles() -> None:
    compiled = compile_workflow(builtin_doc(), sample_context(), template_name="x", version=None)
    assert compiled.items and len(compiled.inline_index.encode()) <= 2048
