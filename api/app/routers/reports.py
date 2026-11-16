"""`/reports` — versioned reads, generation trigger, flags, soft delete, export (§7.1)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import InstrumentNotFound, ReportNotFound
from ..models import Instrument
from ..schemas.blocks import BlockRangeResponse
from ..schemas.report import (
    QUALITY_STATUS_EQUIVALENT,
    CreateReportRequest,
    DiffResult,
    ReportDetail,
    ReportListResponse,
    ReportPatch,
    ReportSummary,
)
from ..schemas.run import RunOut
from ..services import cost_service, report_service
from ..services.exporter import export_filename, render_html, render_markdown
from ..services.run_manager import RunManager, get_manager
from .runs import to_run_out

router = APIRouter(tags=["reports"])


def _instrument(db: Session, instrument_id: str) -> Instrument:
    row = db.get(Instrument, instrument_id)
    if row is None:
        row = db.execute(select(Instrument).where(Instrument.symbol == instrument_id.upper())).scalar_one_or_none()
    if row is None:
        raise InstrumentNotFound(f"标的 {instrument_id} 不存在")
    return row


@router.get("/instruments/{instrument_id}/reports", response_model=ReportListResponse)
def list_reports(
    instrument_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    includeArchived: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> ReportListResponse:
    instrument = _instrument(db, instrument_id)
    rows, total = report_service.list_versions(db, instrument.id, limit=limit, include_archived=includeArchived)
    return ReportListResponse(items=[report_service.summarize(row) for row in rows], total=total)


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(report_id: str, db: Session = Depends(get_db)) -> ReportDetail:
    report = report_service.get(db, report_id)
    if report is None:
        raise ReportNotFound()
    return ReportDetail(
        report=report_service.summarize(report),
        doc=report.doc,  # type: ignore[arg-type]
    )


@router.get("/instruments/{instrument_id}/reports/latest", response_model=ReportDetail | None)
def get_latest(instrument_id: str, db: Session = Depends(get_db)) -> ReportDetail | None:
    instrument = _instrument(db, instrument_id)
    report = report_service.latest_of(db, instrument.id)
    if report is None:
        return None
    return ReportDetail(report=report_service.summarize(report), doc=report.doc)  # type: ignore[arg-type]


@router.post("/instruments/{instrument_id}/reports", status_code=status.HTTP_202_ACCEPTED)
async def create_report(
    instrument_id: str,
    response: Response,
    payload: CreateReportRequest | None = None,
    override: int = Query(
        default=0,
        ge=0,
        le=1,
        description="★v6 G3：1 = 明确越过月度预算闸门（留痕到 runs.reason）",
    ),
    db: Session = Depends(get_db),
    manager: RunManager = Depends(get_manager),
) -> dict[str, object]:
    """Queue a generation and return immediately with the run id (§3.2 step 3).

    Must be `async`: `manager.schedule` needs a running event loop, and that loop
    must be the one that also serves the SSE stream.

    ★v6（V6-P0-1）：这是**唯一**的生成创建入口，所以预算闸门挂在这里。
    优先级写死：`409 run_conflict`（同标冲突）与 `429 too_many_runs`（并发满）
    **先于** `402 monthly_budget_exceeded`——402 说的是"你本月花超了"，
    而 409/429 说的是"现在不能开"；在一个本来也开不了的请求上说预算，
    用户会去改预算（无效动作），而不是等两分钟（AC-62）。
    """
    instrument = _instrument(db, instrument_id)
    body = payload or CreateReportRequest()
    run = manager.start(
        db,
        instrument,
        reason=body.reason,
        force=body.force,
        override_budget=bool(override),
    )
    manager.schedule(run.id, force=body.force)
    estimate_usd = cost_service.estimate(db, instrument.symbol, "run").get("estimatedUsd")
    if run.reason == "budget_override":
        # 响应的 202 体**不改名**，只追加 `estimateUsd`（★V6-P1-8：没有 streamUrl）。
        response.headers["X-Budget-Overridden"] = "1"
    return {
        "runId": run.id,
        "status": "queued",
        "instrumentId": instrument.id,
        "estimateUsd": estimate_usd,
    }


@router.patch("/reports/{report_id}", response_model=ReportSummary)
def patch_report(report_id: str, payload: ReportPatch, db: Session = Depends(get_db)) -> ReportSummary:
    report = report_service.get(db, report_id)
    if report is None:
        raise ReportNotFound()
    try:
        updated = report_service.set_flags(
            db,
            report,
            pinned=payload.pinned,
            archived=payload.archived,
            set_latest=payload.setLatest,
        )
    except ValueError as exc:
        raise ReportNotFound(str(exc)) from exc
    return report_service.summarize(updated)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: str, db: Session = Depends(get_db)) -> Response:
    """★P1-1: soft delete. The row survives, so the run audit trail stays intact."""
    report = report_service.get(db, report_id)
    if report is None:
        raise ReportNotFound()
    report_service.soft_delete(db, report)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/reports/{report_id}/export")
def export_report(
    report_id: str,
    format: str = Query(default="html"),
    db: Session = Depends(get_db),
) -> Response:
    report = report_service.get(db, report_id)
    if report is None:
        raise ReportNotFound()
    from ..errors import ExportUnsupported

    if format not in ("html", "md"):
        raise ExportUnsupported(detail={"format": format, "supported": ["html", "md"], "hint": "pdf 请用浏览器打印"})

    doc = report.doc
    instrument = db.get(Instrument, report.instrument_id)
    symbol = instrument.symbol if instrument else report.instrument_id
    title = f"{instrument.name if instrument else symbol} 投研报告 · v{report.version}"
    filename = export_filename(symbol, report.generated_at, report.version, format)
    if format == "html":
        body = render_html(doc, title=title, generated_at=report.generated_at)
        return HTMLResponse(
            content=body,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    body = render_markdown(doc, title=title, generated_at=report.generated_at)
    return PlainTextResponse(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/{report_id}/blocks", response_model=BlockRangeResponse)
def report_blocks(
    report_id: str,
    from_: str = Query(alias="from", description="YYYY-MM-DD，闭区间下界"),
    to: str = Query(description="YYYY-MM-DD，闭区间上界"),
    limit: int = Query(default=50),
    db: Session = Depends(get_db),
) -> BlockRangeResponse:
    """区间反查：这段时间被报告里的哪几段讲到（spec §19.3.3）。

    三个刻意的选择：

    * `limit` **夹到 200** 而不是 422 —— 越界是"要太多"，不是"参数错了"。
    * `provisional` 报告返回 `items: []` + `reason` —— 临时版没有结构化章节，
      明确说"没有"好过让人以为功能坏了。
    * 相对期间锚在 `doc.asOf` 上（`as_of` 由报告自己给），所以同一份报告的
      反查结果**永久可复现**（O-8）。`date.today()` 在这里一次都没出现。
    """
    from ..errors import InvalidRange, RangeTooWide, ReportNotReady
    from ..schemas.blocks import BlockHit, BlockRangeResponse, RangeWindow
    from ..services import block_dates

    report = report_service.get(db, report_id)
    if report is None:
        raise ReportNotFound()

    try:
        lo = date.fromisoformat(from_)
        hi = date.fromisoformat(to)
    except (TypeError, ValueError) as exc:
        raise InvalidRange(detail={"from": from_, "to": to}) from exc
    if lo > hi:
        raise InvalidRange(detail={"from": from_, "to": to, "hint": "from 不能晚于 to"})
    if block_dates.max_range_years(lo, hi):
        raise RangeTooWide(detail={"from": from_, "to": to, "maxYears": block_dates.MAX_RANGE_YEARS})
    if report.status in ("queued", "running"):
        # 只有"正在生成"才 409；`degraded` 仍是可读的报告，照常反查。
        raise ReportNotReady("报告仍在生成中，暂无可反查的内容")

    window = RangeWindow.model_validate({"from": lo.isoformat(), "to": hi.isoformat()})
    doc = report.doc
    if report.status == "provisional" or doc.get("provisional") is True:
        return BlockRangeResponse(
            reportId=report.id,
            range=window,
            items=[],
            total=0,
            truncated=False,
            asOf=str(doc.get("asOf") or report.as_of or ""),
            reason="provisional_no_dates",
        )

    cap = max(1, min(int(limit or 50), 200))
    items: list[BlockHit] = []
    total = 0
    for block_id, section_id, section_title, kind, days, match_kind, text in block_dates.iter_block_hits(doc, lo, hi):
        total += 1
        if len(items) >= cap:
            continue
        items.append(
            BlockHit(
                blockId=block_id,
                sectionId=section_id,
                sectionTitle=section_title,
                kind=kind,
                overlapDays=days,
                matchKind=match_kind,
                text=text,
            )
        )
    return BlockRangeResponse(
        reportId=report.id,
        range=window,
        items=items,
        total=total,
        truncated=total > len(items),
        asOf=str(doc.get("asOf") or report.as_of or ""),
    )


@router.get("/reports/{report_id}/diff/{other_id}", response_model=DiffResult)
def diff_reports(report_id: str, other_id: str, db: Session = Depends(get_db)) -> DiffResult:
    """Structural diff, `a` = old version, `b` = new version (§18.5.4).

    `tone` is computed server-side (`direction × polarity`) so no component has to
    re-guess that "PE 上升是坏事".
    """
    from ..errors import DiffSameReport
    from ..services.report_diff import diff_docs

    if report_id == other_id:
        raise DiffSameReport()
    left = report_service.get(db, report_id)
    right = report_service.get(db, other_id)
    if left is None or right is None:
        raise ReportNotFound()

    doc_a = left.doc
    doc_b = right.doc
    doc_a["_reportId"] = left.id
    doc_b["_reportId"] = right.id
    # ★v6 G4：模型名取两版报告的 `model` **列**（权威），不是 `quality.engine`。
    doc_a["_model"] = left.model or ""
    doc_b["_model"] = right.model or ""
    payload = diff_docs(doc_a, doc_b, quality_a=left.quality, quality_b=right.quality)
    # `superseded` only says "a newer version exists" — true for every adjacent pair,
    # so diffing it verbatim returned a meaningless change on every comparison. The
    # field is about **quality** (§18.5.4 illustrates `ready → degraded`), so an older
    # version is read as the ready report it was.
    status_changed = None
    before = QUALITY_STATUS_EQUIVALENT.get(left.status)
    after = QUALITY_STATUS_EQUIVALENT.get(right.status)
    if before and after and before != after:
        status_changed = {"from": before, "to": after}
    payload["statusChanged"] = status_changed
    return DiffResult.model_validate(payload)


@router.get("/instruments/{instrument_id}/runs", response_model=list[RunOut])
def list_instrument_runs(
    instrument_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[RunOut]:
    from sqlalchemy import select

    from ..models import Run

    instrument = _instrument(db, instrument_id)
    rows = db.execute(
        select(Run).where(Run.instrument_id == instrument.id).order_by(Run.started_at.desc()).limit(limit)
    ).scalars()
    return [to_run_out(row) for row in rows]
