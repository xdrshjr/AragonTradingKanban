"""内容去重 = 「更新即复用」（spec §23.3.4a；AC-144 / AC-145 / AC-146）。

这里是本轮**唯一**会改变"写了什么"的地方，所以每条断言都对着一个具体的坏结局：

* **AC-144**：同一份 doc 连生两次 ⇒ `reports` 行数不变、`run.status=succeeded`、
  `run.report_id` 指向**既有**那一版、`run.done.deduped=true`、`version` 与既有版相同。
* **AC-145**：第二次仍然新增**一行** `cost_ledger`——「省了一次版本」不等于「省了一次调用」
  （E-127：`cost_service.record` 在 `api/` 里只有一处，任何"跳过 create_version"的去重都会
  静默吞掉这次的花费）。
* **AC-146**：`baseReportId` 属于别的标的 → 422；指向 provisional / 损坏版 → **不报错**、
  按无 base 处理；摘要段 **≤2048 B**、含 8 个章节标题、**指标标签段不含数字**。
"""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.agent.runner_base import AgentEvent, AgentEventType, AgentResult
from app.models import CostLedger, Instrument, Report, Run, RunEvent
from app.services import report_service
from app.services import run_manager as run_manager_module
from app.services import snapshot as snapshot_service
from app.services.market_data.base import SourceRef

# ── service 层：去重的三条性质 ────────────────────────────────────────────


def test_same_content_does_not_create_a_version(session, instrument, sample_report) -> None:
    first = report_service.create_version(
        session, instrument, copy.deepcopy(sample_report), status="ready", run_id=None
    )
    run_id = "run-dedupe-1"
    session.add(
        Run(
            id=run_id,
            instrument_id=instrument.id,
            status="running",
            pct=50,
            started_at="2026-02-10T00:00:00Z",
            cost_known=0,
        )
    )
    session.commit()

    outcome = report_service.create_or_reuse_version(
        session,
        instrument,
        copy.deepcopy(sample_report),
        status="ready",
        run_id=run_id,
        cost_usd=0.02,
        cost_known=True,
        cost_source="reported",
    )

    assert outcome.deduped is True
    assert outcome.recorded_cost is True
    assert outcome.report.id == first.id
    assert session.execute(select(func.count()).select_from(Report)).scalar_one() == 1
    session.expire_all()
    assert session.get(Run, run_id).report_id == first.id, "run 必须指向既有那一版"


def test_dedupe_still_records_cost(session, instrument, sample_report) -> None:
    """AC-145：账本必须 +1 行，`ref_id` 是**这次**的 runId（不是被复用的报告 id）。"""
    report_service.create_version(session, instrument, copy.deepcopy(sample_report), status="ready")
    before = session.execute(select(func.count()).select_from(CostLedger)).scalar_one()

    run_id = "run-dedupe-cost"
    session.add(
        Run(
            id=run_id,
            instrument_id=instrument.id,
            status="running",
            pct=50,
            started_at="2026-02-10T00:00:00Z",
            cost_known=0,
        )
    )
    session.commit()
    report_service.create_or_reuse_version(
        session,
        instrument,
        copy.deepcopy(sample_report),
        status="ready",
        run_id=run_id,
        cost_usd=0.035,
        cost_known=True,
        cost_source="reported",
    )

    rows = session.execute(select(CostLedger).where(CostLedger.ref_id == run_id)).scalars().all()
    assert len(rows) == 1, "去重路径也必须记账（硬约束 4）"
    assert rows[0].kind == "run"
    assert rows[0].usd == pytest.approx(0.035)
    assert rows[0].source == "reported"
    after = session.execute(select(func.count()).select_from(CostLedger)).scalar_one()
    assert after == before + 1


def test_different_content_creates_a_new_version(session, instrument, sample_report) -> None:
    first = report_service.create_version(
        session, instrument, copy.deepcopy(sample_report), status="ready"
    )
    changed = copy.deepcopy(sample_report)
    changed["headline"] = "改过的标题：动销回暖但估值偏高"
    outcome = report_service.create_or_reuse_version(session, instrument, changed, status="ready")

    assert outcome.deduped is False
    assert outcome.report.version == first.version + 1


def test_dedupe_false_forces_a_new_version(session, instrument, sample_report) -> None:
    """逃生阀：用户明确要求"再来一版"时必须真的多一版。"""
    first = report_service.create_version(
        session, instrument, copy.deepcopy(sample_report), status="ready"
    )
    outcome = report_service.create_or_reuse_version(
        session, instrument, copy.deepcopy(sample_report), status="ready", dedupe=False
    )
    assert outcome.deduped is False
    assert outcome.report.id != first.id


def test_a_provisional_version_never_becomes_the_baseline(session, instrument, sample_report) -> None:
    """★P1-10：基线是"最新**非临时**、未删除版本"，不是 `latest_of()`。

    否则去重命中会把这次成功生成的 `runs.report_id` 指向一份失败说明。
    """
    doc = copy.deepcopy(sample_report)
    doc["provisional"] = True
    provisional = report_service.create_version(
        session, instrument, doc, status="provisional", is_provisional=True
    )
    assert provisional.doc_sha1, "provisional 行照写指纹（列一致性）"
    assert report_service.content_baseline(session, instrument.id) is None

    outcome = report_service.create_or_reuse_version(
        session, instrument, copy.deepcopy(sample_report), status="ready"
    )
    assert outcome.deduped is False
    assert outcome.report.id != provisional.id


def test_a_null_fingerprint_is_not_a_match(session, instrument, sample_report) -> None:
    """硬约束 5：NULL 的语义是"不知道"，**不是**"不同"。两侧都非 NULL 才比较。"""
    first = report_service.create_version(
        session, instrument, copy.deepcopy(sample_report), status="ready"
    )
    first.doc_sha1 = None  # 模拟历史行（不回填）
    session.commit()

    outcome = report_service.create_or_reuse_version(
        session, instrument, copy.deepcopy(sample_report), status="ready"
    )
    assert outcome.deduped is False, "无法比较就不去重（多一版是无害的那一侧）"


# ── 管线层：`run.done.deduped` 真的发得出来（AC-144 的最后一格） ─────────────


class _StubRunner:
    name = "stub"
    source = "mock"

    def __init__(self, doc: dict) -> None:
        self.doc = doc

    async def stream(self, request, on_event=None):  # noqa: ANN001
        if on_event:
            on_event(AgentEvent(type=AgentEventType.TEXT, data={"text": "…"}))
        yield AgentEvent(type=AgentEventType.TEXT, data={"text": "…"})

    async def run(self, request, on_event=None) -> AgentResult:  # noqa: ANN001
        return AgentResult(doc=copy.deepcopy(self.doc), source="mock", exit_code=0)

    async def cancel(self) -> None:
        return None


@pytest.fixture()
def pipeline_env(session, instrument, sample_report, snapshot_payload, monkeypatch, tmp_path):
    """把 `_pipeline` 的两条外部依赖换掉：资料组装与引擎。"""
    monkeypatch.setattr(run_manager_module, "reset_manager", lambda: None, raising=False)
    bundle = snapshot_service.SnapshotBundle(
        snapshot=snapshot_payload,
        bars=[],
        fundamentals=[],
        sources=[SourceRef("S1", "行情快照", "synthetic", datetime(2026, 2, 10, tzinfo=UTC).date())],
        warnings=[],
    )

    async def fake_build_snapshot(*_args, **_kwargs):
        return bundle

    monkeypatch.setattr(snapshot_service, "build_snapshot", fake_build_snapshot)
    monkeypatch.setattr(
        run_manager_module,
        "build_runner",
        lambda *_args, **_kwargs: _StubRunner(sample_report),
    )

    def make_run(run_id: str) -> Run:
        workspace = tmp_path / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        row = Run(
            id=run_id,
            instrument_id=instrument.id,
            status="queued",
            pct=0,
            started_at="2026-02-10T00:00:00Z",
            cost_known=0,
            workspace_path=str(workspace),
        )
        session.add(row)
        session.commit()
        return row

    return make_run


def _drive(manager, session, instrument, run: Run, *, base_report_id=None, dedupe=True) -> None:
    asyncio.run(
        manager._pipeline(  # noqa: SLF001 - 断言的就是这段编排
            session,
            run,
            instrument,
            force=False,
            base_report_id=base_report_id,
            dedupe=dedupe,
        )
    )


def test_run_done_frame_carries_deduped_and_no_second_version(
    session, instrument, pipeline_env
) -> None:
    """AC-144：第二次生成 `deduped=true`、行数不变、run 指向既有版、cost 仍记一行。"""
    manager = run_manager_module.RunManager()

    first = pipeline_env("run-first")
    _drive(manager, session, instrument, first)
    session.expire_all()
    assert session.get(Run, "run-first").status == "succeeded"
    reports = session.execute(select(Report)).scalars().all()
    assert len(reports) == 1
    original = reports[0]

    second = pipeline_env("run-second")
    _drive(manager, session, instrument, second)
    session.expire_all()

    assert session.execute(select(func.count()).select_from(Report)).scalar_one() == 1
    fresh = session.get(Run, "run-second")
    assert fresh.status == "succeeded"
    assert fresh.report_id == original.id

    frames = [
        event.payload
        for event in session.execute(
            select(RunEvent).where(RunEvent.run_id == "run-second").order_by(RunEvent.seq)
        ).scalars()
        if event.type == "run.done"
    ]
    assert frames, "第二个 run 必须发出 run.done"
    assert frames[-1]["deduped"] is True
    assert frames[-1]["version"] == original.version

    ledger = session.execute(
        select(func.count()).select_from(CostLedger).where(CostLedger.ref_id == "run-second")
    ).scalar_one()
    assert ledger == 1, "第二次生成仍然要记一行账（AC-145）"


def test_first_run_reports_deduped_false(session, instrument, pipeline_env) -> None:
    manager = run_manager_module.RunManager()
    _drive(manager, session, instrument, pipeline_env("run-only"))
    frames = [
        event.payload
        for event in session.execute(
            select(RunEvent).where(RunEvent.run_id == "run-only").order_by(RunEvent.seq)
        ).scalars()
        if event.type == "run.done"
    ]
    assert frames[-1]["deduped"] is False


# ── API：`baseReportId` 的校验 ─────────────────────────────────────────────


def test_base_report_must_belong_to_the_instrument(client, session, sample_report) -> None:

    other = session.get(Instrument, "CN:600519.SH")
    if other is None:
        other = Instrument(
            id="CN:600519.SH",
            symbol="600519.SH",
            name="贵州茅台",
            market="CN",
            exchange="SSE",
            currency="CNY",
            sector="食品饮料",
            tz="Asia/Shanghai",
            created_at="2026-02-10T00:00:00Z",
            updated_at="2026-02-10T00:00:00Z",
        )
        session.add(other)
        session.commit()
    foreign = report_service.create_version(session, other, sample_report, status="ready")

    client.post("/api/v1/symbols/resolve", json={"query": "300750"})
    response = client.post(
        "/api/v1/instruments/CN:300750.SZ/reports",
        json={"baseReportId": foreign.id},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "base_report_mismatch"
    assert response.json()["error"]["detail"]["baseReportId"] == foreign.id


def test_the_202_body_says_whether_dedupe_was_requested(client) -> None:
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    body = client.post("/api/v1/instruments/CN:600519.SH/reports", json={}).json()
    assert body["dedupe"] is True
    # ★v15（§24.5.5）：202 体追加 `quota`（service/system 为 null；user 为 `QuotaStatus.report`）。
    assert set(body) == {"runId", "status", "instrumentId", "estimateUsd", "dedupe", "quota"}


# ── AC-146：摘要段的三条断言（上限 / 章节 / 无数字） ────────────────────────


def test_base_digest_is_bounded_and_has_the_eight_sections(sample_report) -> None:
    doc = copy.deepcopy(sample_report)
    doc["sections"] = [
        {"id": f"s{index}", "title": f"章节{index}", "blocks": []} for index in range(1, 9)
    ]
    text = run_manager_module.base_digest_text(doc, version=12)
    assert len(text.encode("utf-8")) <= run_manager_module.BASE_DIGEST_MAX_BYTES
    for index in range(1, 9):
        assert f"章节{index}" in text
    assert run_manager_module.BASE_DIGEST_INSTRUCTION in text


def test_base_digest_metric_segment_has_no_numbers(sample_report) -> None:
    """★P1-12：只对 **指标标签段** 断言"无数字"（`asOf` 行与版本号行显式豁免）。"""
    import re

    doc = copy.deepcopy(sample_report)
    doc["keyMetrics"] = [
        {"label": "市盈率", "value": "24.1×"},
        {"label": "营收增速", "value": "+15.2%"},
    ]
    text = run_manager_module.base_digest_text(doc, version=3)
    segment = next(line for line in text.splitlines() if "指标标签" in line)
    assert re.search(r"\d", segment) is None, segment
    # 值不能出现（R-69：把旧数写进提示词等于给模型一份可以照抄的答案）。
    assert "24.1" not in text and "15.2" not in text


def test_base_digest_never_exceeds_the_budget_for_a_huge_doc(sample_report) -> None:
    doc = copy.deepcopy(sample_report)
    doc["keyMetrics"] = [{"label": "指标" + "甲" * 30, "value": "1"} for _ in range(12)]
    doc["sections"] = [{"id": f"s{index}", "title": "长" * 60, "blocks": []} for index in range(8)]
    text = run_manager_module.base_digest_text(doc, version=99)
    assert len(text.encode("utf-8")) <= run_manager_module.BASE_DIGEST_MAX_BYTES


def test_provisional_base_is_treated_as_no_base(session, instrument, sample_report) -> None:
    """★P1-15：取不到结构 ⇒ 按无 base 处理（不 422、不崩），但 `baseVersion` 仍记原值。"""
    doc = copy.deepcopy(sample_report)
    doc["provisional"] = True
    provisional = report_service.create_version(
        session, instrument, doc, status="provisional", is_provisional=True
    )
    run = Run(
        id="run-base-prov",
        instrument_id=instrument.id,
        status="running",
        pct=10,
        started_at="2026-02-10T00:00:00Z",
        cost_known=0,
    )
    session.add(run)
    session.commit()

    manager = run_manager_module.RunManager()
    text, version = manager._base_digest(session, run, instrument, provisional.id)  # noqa: SLF001
    assert text == ""
    assert version == provisional.version
    logs = [
        event.payload["message"]
        for event in session.execute(
            select(RunEvent).where(RunEvent.run_id == "run-base-prov")
        ).scalars()
        if event.type == "run.log"
    ]
    assert any("base_digest 跳过" in message for message in logs), logs
