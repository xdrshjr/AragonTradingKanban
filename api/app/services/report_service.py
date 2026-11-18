"""Report versioning — the "one latest per instrument" invariant (spec §6.2, §7.1).

Every write goes through `create_version`, which in a single transaction clears
`is_latest`, computes `max(version)+1`, inserts, supersedes the old row, and
points the run at the new report. The unique partial index `ux_reports_latest`
is the backstop if any of that is ever bypassed.

Deletion is **soft** (★P1-1): `deleted_at` is stamped, the row disappears from
listings, and if it was the latest the pointer moves to the newest surviving
version. Hard deletes happen only in the retention job.

★v14（§23.3.4~§23.3.6）在这里落了四件事，每一件都是**不变量**而不是功能：

1. **内容去重**：`create_or_reuse_version()` 在"内容指纹与最新非临时版相同"时**不新增版本**，
   但成本照记（E-127：`cost_service.record` 在 `api/` 里只有这一处，任何"跳过写入"的
   去重都会静默吞掉这次生成的花费）。
2. **可读性不依赖 doc 是否可解析**：`load_doc_safe()` 是**唯一**的读入口，损坏的
   `doc_json` 返回一份能通过 `ReportDoc` 校验的最小壳 + `integrity="corrupt"`，
   而不是把 500 从 `json.loads` 挪到 pydantic。
3. **裁剪可逆**：`_trim_versions()` 从 `session.delete` 改成批量软删，于是"被系统清掉"
   也进回收站；硬删只剩 `purge_trash()` 一个入口，且必须过 `TRASH_RETENTION_DAYS`。
4. **裁剪失败不影响"保存成功"**：`_trim_versions()` 包在 `try/except` 里（E-128：
   报告已 commit、run 指针已写、成本已记，再让它抛异常就等于把一次成功渲染成失败）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..logging import get_logger
from ..models import Instrument, Report, Run
from ..models.ask import ReportAsk
from ..models.run import ACTIVE_STATUSES
from ..schemas.report import ReportQuality, ReportSummary
from . import cost_service, search_index

logger = get_logger("report_service")

#: ★v14（§23.3.5）：20 → 50。裁剪现在是**软删**，"被裁"等于"进回收站"，所以上限可以从
#: "够用的历史深度"放宽到"回收站还看得懂的数量"。置顶行仍然**既不算数也不会被裁**。
MAX_VERSIONS_PER_INSTRUMENT = 50


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return uuid.uuid4().hex


def summarize(report: Report) -> ReportSummary:
    doc: dict[str, Any] = {}
    try:
        doc = json.loads(report.doc_json)
    except (json.JSONDecodeError, TypeError):  # pragma: no cover - defensive
        doc = {}
    quality_payload = report.quality or {}
    return ReportSummary(
        id=report.id,
        instrumentId=report.instrument_id,
        version=report.version,
        status=report.status,  # type: ignore[arg-type]
        title=report.title,
        headline=str(doc.get("headline") or "")[:200],
        stance=report.stance or (doc.get("rating") or {}).get("stance"),  # type: ignore[arg-type]
        asOf=report.as_of,
        generatedAt=report.generated_at,
        isLatest=bool(report.is_latest),
        pinned=bool(report.pinned),
        archived=bool(report.archived),
        provisional=bool(doc.get("provisional")) or report.status == "provisional",
        groundedRatio=report.grounded_ratio,
        quality=ReportQuality(
            groundedRatio=quality_payload.get("groundedRatio", report.grounded_ratio or 1.0) or 1.0,
            missingSections=quality_payload.get("missingSections") or [],
            prunedBlocks=quality_payload.get("prunedBlocks") or 0,
            ungroundedCount=quality_payload.get("ungroundedCount") or 0,
            source=quality_payload.get("source") or "aragon",
            # ★v6: an absent key stays absent (old reports render without a badge,
            # and `comparability.same` becomes null rather than a guess).
            grounding=quality_payload.get("grounding"),
            engine=quality_payload.get("engine"),
        ),
        provider=report.provider,
        model=report.model,
        costUsd=report.cost_usd,
        costKnown=bool(report.cost_known),
        tokensIn=report.tokens_in,
        tokensOut=report.tokens_out,
        durationMs=report.duration_ms,
        docBytes=report.doc_bytes,
    )


def _canonical_json(doc: dict[str, Any]) -> str:
    """稳定序列化：键排序 + 紧凑分隔符。指纹只认这一种形态。"""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha1(doc: dict[str, Any]) -> str:
    """`sha1(canonical_json(doc))` —— 内容指纹的**唯一**算法（§23.3.4a）。

    为什么是 sha1(内容) 而不是"版本号 + 时间"：doc 里已经有 `asOf` / `rating` /
    `sections` / `sources`，同样的数据再生成一次，模型只要有一处用词不同 sha 就不同
    ⇒ 我们**只**去掉"完全一模一样"的重复。这是**故意保守**的：误判"重复"会让用户以为
    没更新（危险），误判"不重复"只是多一个版本（无害）。

    它**不**写进 doc（`report_doc.schema.json` 顶层 `additionalProperties: false`，
    加一个键就是全灭），只落 `reports.doc_sha1` 这一列。
    """
    return hashlib.sha1(_canonical_json(doc).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PersistOutcome:
    """`_persist()` 的返回值：版本 + "这次到底新增了吗" + "成本记账了吗"。

    `recorded_cost` 恒为 `True`，字段存在的意义是让测试能断言"成本一定记了"
    （E-127 / AC-145）——一个布尔比一句注释更能防住下一次"跳过 create_version"。
    """

    report: Report
    deduped: bool
    recorded_cost: bool


def content_baseline(session: Session, instrument_id: str) -> Report | None:
    """去重基线：最新**非临时**、未删除版本（★P1-10）。

    **不是** `latest_of()`：后者在"标的只有临时版"时返回 provisional 行，用它当基线
    等于把"这次成功生成"的 `runs.report_id` 指向一份失败说明。provisional 行照写
    `doc_sha1`（列一致性、也能自证"这行为什么没参与比较"），但**永不参与比较**。
    """
    return (
        session.execute(
            select(Report)
            .where(
                Report.instrument_id == instrument_id,
                Report.deleted_at.is_(None),
                Report.status != "provisional",
            )
            .order_by(Report.version.desc())
        )
        .scalars()
        .first()
    )


def create_version(
    session: Session,
    instrument: Instrument,
    doc: dict[str, Any],
    *,
    status: str,
    quality: dict[str, Any] | None = None,
    run_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    cost_known: bool = False,
    cost_source: str | None = None,
    cost_usd_est: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
    is_provisional: bool = False,
) -> Report:
    """Insert the next version and make it latest (unless provisional).

    Provisional versions never compete for `is_latest` (§8.1) — they appear in
    the version switcher as 临时版 and only render by default when they are the
    only thing the instrument has.

    ★v14：签名与返回值**一个字都没改**（`api/` 里 28 处调用点、14 个文件在用它），
    去重是**另一个入口** `create_or_reuse_version()`；这条路径 `dedupe=False`，
    所以既有调用者的语义完全不变。
    """
    return _persist(
        session,
        instrument,
        doc,
        status=status,
        quality=quality,
        run_id=run_id,
        provider=provider,
        model=model,
        cost_usd=cost_usd,
        cost_known=cost_known,
        cost_source=cost_source,
        cost_usd_est=cost_usd_est,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        is_provisional=is_provisional,
        dedupe=False,
    ).report


def create_or_reuse_version(
    session: Session,
    instrument: Instrument,
    doc: dict[str, Any],
    *,
    status: str,
    quality: dict[str, Any] | None = None,
    run_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    cost_known: bool = False,
    cost_source: str | None = None,
    cost_usd_est: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
    is_provisional: bool = False,
    dedupe: bool = True,
) -> PersistOutcome:
    """★v14（U3 / §23.3.4a）：更新 = 生成新版本，除非内容与最新非临时版**完全相同**。

    `dedupe=False` 与 `create_version()` 行为等价（逃生阀：请求层可以显式关掉去重）。
    """
    return _persist(
        session,
        instrument,
        doc,
        status=status,
        quality=quality,
        run_id=run_id,
        provider=provider,
        model=model,
        cost_usd=cost_usd,
        cost_known=cost_known,
        cost_source=cost_source,
        cost_usd_est=cost_usd_est,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        is_provisional=is_provisional,
        dedupe=dedupe,
    )


def _persist(
    session: Session,
    instrument: Instrument,
    doc: dict[str, Any],
    *,
    status: str,
    quality: dict[str, Any] | None = None,
    run_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    cost_known: bool = False,
    cost_source: str | None = None,
    cost_usd_est: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
    is_provisional: bool = False,
    dedupe: bool = False,
) -> PersistOutcome:
    """The one write path. Order is load-bearing (see the numbered list below).

    1. 先算 `payload` / `doc_sha1`（两个序列化，分开存：一个给人看、一个给比较）；
    2. `dedupe` 且基线行存在且两侧指纹都非 NULL 且相等 ⇒ **不写 reports 行、不动
       `is_latest`、不重建 FTS、不裁剪**，但**仍然**记成本、**仍然**把 run 指向既有版、
       **仍然** commit；
    3. 否则走既有的插入路径（清 `is_latest` → `max(version)+1` → insert → FTS savepoint →
       cost record → run 指向 → commit），并把 `doc_sha1` 一起写入；
    4. 裁剪包在 `try/except` 里（不变量 3）：它失败不该让一次已经落库的生成变成 500。
    """
    payload = json.dumps(doc, ensure_ascii=False)
    doc_sha1 = content_sha1(doc)
    quality_payload = quality or {}
    generated_at = _iso()
    as_of = str(doc.get("asOf") or generated_at)
    rating = doc.get("rating") or {}
    title_parts = [
        str((doc.get("instrument") or {}).get("name") or instrument.name),
        "投研报告",
    ]
    title = " · ".join(part for part in title_parts if part)[:250]
    # ★v8 评审（R-42 的第二个入口）：`cost_source` 的默认值 `"unknown"` 本身就是一个坑
    # —— 调用方传了 `cost_usd` 却忘了 `source`，`record()` 会把 `usd` 丢弃（三态互斥），
    # 于是那一行变成"未知成本"而**没有任何报错**。所以这里在**两条分支共用一个**推导，
    # 把"漏传"变成"按已知信息推对"。
    resolved_source = cost_source or (
        "reported"
        if cost_known and cost_usd is not None
        else ("estimated" if cost_usd_est is not None else "unknown")
    )

    # ── 2. 去重（不变量 1）────────────────────────────────────────────────
    if dedupe and not is_provisional:
        baseline = content_baseline(session, instrument.id)
        if baseline is not None and baseline.doc_sha1 and baseline.doc_sha1 == doc_sha1:
            # ★硬约束 4 / E-127：**成本照记**。"省了一次版本"不等于"省了一次调用"——
            # 这次 run 真的花了钱，账本若丢这一行，`/cost/summary` 就会比 `runs` 少。
            cost_service.record(
                session,
                kind="run",
                ref_id=run_id or baseline.id,
                symbol=instrument.symbol,
                report_id=baseline.id,
                model=model,
                usd=cost_usd,
                known=bool(cost_known),
                usd_est=cost_usd_est,
                source=resolved_source,
            )
            if run_id:
                session.execute(update(Run).where(Run.id == run_id).values(report_id=baseline.id))
            session.commit()
            session.expire_all()
            logger.info(
                "report deduped instrument=%s reuse=%s v%s sha1=%s",
                instrument.id,
                baseline.id,
                baseline.version,
                doc_sha1[:12],
            )
            return PersistOutcome(report=baseline, deduped=True, recorded_cost=True)

    current_max = session.execute(
        select(func.max(Report.version)).where(Report.instrument_id == instrument.id)
    ).scalar_one()
    next_version = int(current_max or 0) + 1

    make_latest = not is_provisional
    if make_latest:
        session.execute(
            update(Report)
            .where(Report.instrument_id == instrument.id, Report.is_latest == 1)
            .values(is_latest=0, status="superseded")
        )

    report = Report(
        id=_new_id(),
        instrument_id=instrument.id,
        version=next_version,
        status="provisional" if is_provisional else ("degraded" if status == "degraded" else "ready"),
        title=title,
        stance=rating.get("stance"),
        as_of=as_of,
        generated_at=generated_at,
        doc_json=payload,
        doc_bytes=len(payload.encode("utf-8")),
        # provisional 行**照写**指纹：列一致性，也让"这行为什么没参与比较"能用一条
        # SQL 自证（它 `status='provisional'`）。
        doc_sha1=doc_sha1,
        grounded_ratio=quality_payload.get("groundedRatio"),
        quality_json=json.dumps(quality_payload, ensure_ascii=False) if quality_payload else None,
        provider=provider,
        model=model,
        run_id=run_id,
        cost_usd=cost_usd,
        cost_known=cost_known,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        is_latest=make_latest,
        pinned=False,
        archived=False,
    )
    session.add(report)
    session.flush()

    # ★V4-P0-1 / O-4: the index rows are written in this very transaction, so
    # "生成完即可搜到" is a transaction property instead of a background race.
    # The projection itself lives in `search_index` (block-level, section+block ids).
    # A savepoint is what makes the promise below真实: catching the exception without
    # one leaves the connection in an aborted-transaction state, so the *report*
    # commit would then raise `PendingRollbackError` — "search never blocks a
    # generation" would be a comment, not a property.
    try:
        with session.begin_nested():
            search_index.reindex_report_payload(session, report.id, payload, title)
    except Exception:  # pragma: no cover - search must never block a generation
        logger.exception("FTS 索引写入失败 report=%s（报告本身仍会落库）", report.id)

    # ★v6 G3 / O-4: the ledger row is projected in this very transaction, and the
    # write is guarded by its own savepoint (`cost_service.record`), so "账本失败
    # 不得回滚报告" is a property of the code rather than a comment. Not a second
    # `begin_nested()` around this call — the template above is already the shape.
    # ★v8 评审（R-42 的第二个入口）：`cost_source` 的默认值 `"unknown"` 本身就是同一个
    # 坑——调用方传了 `cost_usd` 却忘了 `source`，`record()` 会把 `usd` 丢弃（三态互斥），
    # 于是那一行变成"未知成本"而**没有任何报错**。所以默认值改成 `None`，在函数开头一次性
    # 推导出 `resolved_source`：三态的真源仍然只有 `COST_SOURCES`，这里只是不让"漏传"
    # 变成"错标"。去重分支与插入分支**共用**同一个值。
    cost_service.record(
        session,
        kind="run",
        ref_id=run_id or report.id,
        symbol=instrument.symbol,
        report_id=report.id,
        model=model,
        usd=cost_usd,
        known=bool(cost_known),
        usd_est=cost_usd_est,
        source=resolved_source,
    )

    if run_id:
        session.execute(update(Run).where(Run.id == run_id).values(report_id=report.id))
    session.commit()
    # The supersede above went through a Core UPDATE, which the identity map does
    # not see (`expire_on_commit=False`); without this, a list loaded earlier in
    # the request would still report two `is_latest=1` rows.
    session.expire_all()

    logger.info(
        "report created instrument=%s version=%s status=%s latest=%s bytes=%s sha1=%s",
        instrument.id,
        next_version,
        report.status,
        make_latest,
        report.doc_bytes,
        doc_sha1[:12],
    )
    # ★v14（不变量 3 / E-128）：裁剪**绝不能**影响"保存成功"这个结论。走到这里报告已经
    # commit、run 指针已写、成本已记；再让它抛异常就等于把一次成功的生成渲染成失败
    # （用户会再点一次，然后撞上 `run_conflict` 或预算）。
    try:
        trimmed = _trim_versions(session, instrument.id)
        if trimmed:
            logger.debug("trimmed=%s instrument=%s", trimmed, instrument.id)
    except Exception:  # pragma: no cover - 只有磁盘/索引层出问题才会走到
        logger.exception("裁剪失败（报告已保存，不影响本次结果） instrument=%s", instrument.id)
        session.rollback()
    return PersistOutcome(report=report, deduped=False, recorded_cost=True)


def _trim_versions(session: Session, instrument_id: str, keep: int | None = None) -> int:
    """R9: keep the newest N unpinned versions; **soft**-delete the rest.

    ★v14（U5 / §23.3.5）：从 `session.delete`（硬删）改成批量软删。三条细节写死：

    * **豁免口径照旧**：只有 `pinned = 0` 的行参与计数与裁剪；置顶行既不算数也不会被裁。
    * **被裁的行不复位 latest 指针**：它们本来就是 `is_latest = 0` 的老版本，批量路径
      **不调** `soft_delete()` 里那段"找后继"的逻辑（那是给"删的正好是 latest"准备的）。
    * **不进 `runs.report_id` 的 SET NULL**：软删之后报告仍然存在，所以 run 的审计指针
      **继续指向它**；只有 `purge_trash()` 真的把它硬删之后才变 NULL。

    `keep` 在调用时解析，所以上限可以配置（也可以被测试 monkeypatch）而不用重新 import。
    """
    limit = MAX_VERSIONS_PER_INSTRUMENT if keep is None else keep
    rows = (
        session.execute(
            select(Report)
            .where(
                Report.instrument_id == instrument_id,
                Report.pinned == 0,
                Report.deleted_at.is_(None),
            )
            .order_by(Report.version.desc())
        )
        .scalars()
        .all()
    )
    doomed = rows[limit:]
    if not doomed:
        return 0
    stamp = _iso()
    for row in doomed:
        row.deleted_at = stamp
        # 幂等：正常情况下它已经是 0；万一不是，也不该让一份已软删的版本继续当门面。
        row.is_latest = False
        # 与 `soft_delete()` 同一个事务、同一个语义："搜不到已删报告"（AC-33/AC-151）。
        search_index.delete_report_index(session, row.id)
    session.commit()
    session.expire_all()
    # ★评审 P2-7：回收站从此混着"我删的"和"系统自动清掉的"。本轮不新增列去区分
    # （硬约束 1），所以这条日志就是事后唯一能分辨二者的地方。
    logger.info("trimmed %s versions into trash instrument=%s", len(doomed), instrument_id)
    return len(doomed)


def get(session: Session, report_id: str, *, include_deleted: bool = False) -> Report | None:
    report = session.get(Report, report_id)
    if report is None:
        return None
    if report.deleted_at and not include_deleted:
        return None
    return report


def latest_of(session: Session, instrument_id: str) -> Report | None:
    """The version the UI opens by default.

    Falls back to the newest provisional when nothing else exists — a user with
    only a failed attempt still gets something to read (★P1-10).
    """
    report = (
        session.execute(
            select(Report)
            .where(
                Report.instrument_id == instrument_id,
                Report.is_latest == 1,
                Report.deleted_at.is_(None),
            )
            .order_by(Report.version.desc())
        )
        .scalars()
        .first()
    )
    if report is not None:
        return report
    return (
        session.execute(
            select(Report)
            .where(Report.instrument_id == instrument_id, Report.deleted_at.is_(None))
            .order_by(Report.version.desc())
        )
        .scalars()
        .first()
    )


def list_versions(
    session: Session,
    instrument_id: str,
    *,
    limit: int = 20,
    include_archived: bool = False,
) -> tuple[list[Report], int]:
    conditions = [Report.instrument_id == instrument_id, Report.deleted_at.is_(None)]
    if not include_archived:
        conditions.append(Report.archived == 0)
    total = session.execute(select(func.count()).select_from(Report).where(*conditions)).scalar_one()
    rows = (
        session.execute(
            select(Report)
            .where(*conditions)
            .order_by(Report.pinned.desc(), Report.version.desc())
            .limit(max(1, min(limit, 100)))
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def set_flags(
    session: Session,
    report: Report,
    *,
    pinned: bool | None = None,
    archived: bool | None = None,
    set_latest: bool | None = None,
) -> Report:
    if pinned is not None:
        report.pinned = pinned
    if archived is not None:
        report.archived = archived
        if archived:
            # An archived version can never be the front page (§7.1 PATCH).
            report.is_latest = False
            report.status = "superseded"
    if set_latest:
        if report.deleted_at:
            raise ValueError("cannot promote a deleted report")
        # Demote the incumbent first: `ux_reports_latest` allows exactly one.
        session.execute(
            update(Report)
            .where(
                Report.instrument_id == report.instrument_id,
                Report.id != report.id,
                Report.is_latest == 1,
            )
            .values(is_latest=0, status="superseded")
        )
        report.is_latest = True
        if report.status == "superseded":
            report.status = "ready"
    session.commit()
    # Same reason as `create_version`: the demotion is a Core UPDATE.
    session.expire_all()
    return report


def soft_delete(session: Session, report: Report) -> None:
    """★P1-1: stamp `deleted_at`, keep the audit trail, move the latest pointer."""
    report.deleted_at = _iso()
    was_latest = bool(report.is_latest)
    report.is_latest = False
    # Same transaction as the flag change: a soft-deleted report must stop being
    # findable *immediately* (AC-33).
    search_index.delete_report_index(session, report.id)
    session.flush()

    if was_latest:
        successor = (
            session.execute(
                select(Report)
                .where(
                    Report.instrument_id == report.instrument_id,
                    Report.id != report.id,
                    Report.deleted_at.is_(None),
                    Report.archived == 0,
                    Report.status != "provisional",
                )
                .order_by(Report.version.desc())
            )
            .scalars()
            .first()
        )
        if successor is not None:
            successor.is_latest = True
            if successor.status == "superseded":
                successor.status = "ready"
    session.commit()
    logger.info("report soft-deleted id=%s was_latest=%s", report.id, was_latest)


def restore(session: Session, report: Report) -> Report:
    """★v14（U5 / §23.5.2）：把一份软删的版本救回来。

    四件事，同一事务，顺序不能变：

    1. `deleted_at = NULL`；`archived` **保持原值**（不替用户取消归档）。
    2. **latest 指针**：只有"该标的当前**没有** `is_latest=1`、且这一份是存活行里版本最大、
       且**未归档**（`archived = 0`）、且非临时版"时才抢指针。`ux_reports_latest` 只允许
       一个，这是数据库层的硬约束（R-70）——所以先查有没有，再抢。
       ★评审 P2-11：归档版**不抢** `is_latest`，否则与 `set_flags(archived=True)` 的既有
       规则（"归档的版本永远当不了门面"）自相矛盾。
    3. **FTS 成对**：`reindex_report_payload()` 在**同一个事务**里、用 `begin_nested()` 包住，
       与 `create_version()` 完全同形。索引失败只记 error，报告仍然恢复成功（AC-151）。
    4. `commit`。

    `restore` **不会**把"被 CASCADE 删掉的追问"变回来——那些行在 `purge_trash()` 时就已经
    没了。30 天窗口内 restore 是完全无损的（含追问），只有硬清理之后才不可逆。
    """
    report.deleted_at = None
    session.flush()

    if not report.archived and report.status != "provisional":
        incumbent = session.execute(
            select(Report.id).where(
                Report.instrument_id == report.instrument_id,
                Report.is_latest == 1,
                Report.deleted_at.is_(None),
            )
        ).first()
        if incumbent is None:
            newest = (
                session.execute(
                    select(Report)
                    .where(
                        Report.instrument_id == report.instrument_id,
                        Report.deleted_at.is_(None),
                        Report.archived == 0,
                        Report.status != "provisional",
                    )
                    .order_by(Report.version.desc())
                )
                .scalars()
                .first()
            )
            if newest is not None and newest.id == report.id:
                # Core UPDATE（与 `create_version()` 同形）：`ux_reports_latest` 只允许一个。
                session.execute(
                    update(Report)
                    .where(
                        Report.instrument_id == report.instrument_id,
                        Report.id != report.id,
                        Report.is_latest == 1,
                    )
                    .values(is_latest=0, status="superseded")
                )
                report.is_latest = True
                if report.status == "superseded":
                    report.status = "ready"
                session.flush()

    # 与 `create_version()` 完全同形的 savepoint：索引失败只记 error，恢复仍然成功。
    try:
        with session.begin_nested():
            search_index.reindex_report_payload(session, report.id, report.doc_json, report.title)
    except Exception:  # pragma: no cover - 搜索永不阻断恢复
        logger.exception("FTS 索引重建失败 report=%s（报告仍然恢复成功）", report.id)

    session.commit()
    session.expire_all()
    logger.info("report restored id=%s latest=%s", report.id, bool(report.is_latest))
    return report


def purge_trash(session: Session, *, retention_days: int | None = None) -> dict[str, int]:
    """★v14（U5 / §23.3.5）：**唯一**的硬删入口，且必须过 `TRASH_RETENTION_DAYS`。

    完整副作用清单（★评审 P1-8：v1 只写了半条，漏掉的那条会删掉用户数据）：

    * `reports` 行 —— 硬删（本入口的唯一目的）。
    * `report_asks` —— 数据库 CASCADE 会一并删除（`ondelete="CASCADE"`）。这是 schema 里
      既有的外键，本轮不改表 ⇒ 只能把它写进回收站文案（"永久清理会连同该版本的追问记录
      一起删除"），并在这里计数。
    * `reports_fts` —— 同事务 `delete_report_index()`（幂等），保持"搜不到已删报告"。
    * `runs.report_id` —— 置 `NULL`（Core UPDATE，与旧 `_trim_versions` 一致；FK 上也有
      `SET NULL` 兜底）：审计链必须变成"指向已清理的报告"，不是孤儿。
    * `cost_ledger.report_id` —— **不动**（它是**无外键**的 `String(36)`）。账本不允许因为
      清理报告而丢行/改行（成本纪律）；留下的 id 是可接受的旁证。

    `TRASH_RETENTION_DAYS=0` ⇒ 只保留到下一次 purge（测试里就用它验证硬删路径）。
    """
    days = get_settings().trash_retention_days if retention_days is None else retention_days
    # `<=` 而不是 `<`：`deleted_at` 是秒级 ISO 文本，而 `cutoff` 也是秒级——用 `<` 会让
    # "同一秒内删掉的行"永远差一秒，于是 `TRASH_RETENTION_DAYS=0` 的语义（"只保留到下一次
    # purge"）在测试里恒为 0 行。两种写法对 30 天窗口的差别是一个秒边界，对 0 天是"能跑/不能跑"。
    cutoff = _iso(datetime.now(UTC) - timedelta(days=max(0, int(days))))
    doomed = (
        session.execute(
            select(Report).where(Report.deleted_at.is_not(None), Report.deleted_at <= cutoff)
        )
        .scalars()
        .all()
    )
    if not doomed:
        return {"reports": 0, "asks": 0}

    purged_reports = 0
    purged_asks = 0
    for row in doomed:
        purged_asks += int(
            session.execute(
                select(func.count()).select_from(ReportAsk).where(ReportAsk.report_id == row.id)
            ).scalar_one()
        )
        session.execute(update(Run).where(Run.report_id == row.id).values(report_id=None))
        search_index.delete_report_index(session, row.id)
        session.delete(row)
        purged_reports += 1
    session.commit()
    session.expire_all()
    # ★评审 P1-8 / R-78：这条日志是"追问记录去哪了"的唯一事后证据。
    logger.info("purged %s reports / %s asks", purged_reports, purged_asks)
    return {"reports": purged_reports, "asks": purged_asks}


# ── 读路径：损坏的 doc 不是 500（不变量 2 / ★P0-1）──────────────────────────

#: `load_doc_safe()` 的两个取值。前端据此把 `TrustBadge` 切到 `warn`。
INTEGRITY_OK = "ok"
INTEGRITY_CORRUPT = "corrupt"


def strip_underscored(doc: dict[str, Any]) -> dict[str, Any]:
    """去掉内部标记键（`_reportId` / `_model`）。

    `ReportDetail.doc` 的类型是 `ReportDoc`，而 `ReportDoc` 是 `extra="forbid"`：
    版本比较往 doc 里塞的 `_reportId` 一旦被回传给详情端点，序列化就会 500。所以
    `load_doc_safe()` 对**所有**读路径统一剥掉下划线开头的内部键——一条纪律，而不是
    让每个调用点自己记得。
    """
    return {key: value for key, value in doc.items() if not key.startswith("_")}


def corrupt_shell(report: Report, instrument: Instrument | None = None) -> dict[str, Any]:
    """损坏 doc 的**最小可渲染壳**（★P0-1 的第二版：v1 的壳自身过不了 `ReportDoc`）。

    逐字段对着 `ReportDoc` / `Section` / `CalloutBlock` 的约束填：

    * `summary` 有 `min_length=10`（v1 给的是 `"…"`，1 个字符 ⇒ 壳会在**序列化**时抛
      `ResponseValidationError`，只是把 500 从 `json.loads` 挪到 pydantic）；
    * `asOf` 也有 `min_length=10`；
    * `instrument` 四个字段都必填（报告行本身不存这些，只能从 `instruments` 表取）；
    * `callout` 的四个键 `{type, tone, title, text}` 一个都不能少。

    满足这些约束之后，**12 种块渲染器一行不用改**：前端拿到的是一份"说明自己坏了的
    报告"，而不是白屏。
    """
    symbol = (instrument.symbol if instrument else report.instrument_id) or "UNKNOWN"
    name = (instrument.name if instrument else symbol) or "UNKNOWN"
    as_of = str(report.as_of or report.generated_at or "1970-01-01")[:40]
    if len(as_of) < 10:  # ReportDoc.asOf 有 min_length=10
        as_of = f"{as_of:<10}"
    return {
        "schema": "aragon.research.report/v1",
        "provisional": True,
        "instrument": {
            "symbol": symbol[:24],
            "name": name[:64],
            "market": (instrument.market if instrument else "CN"),  # type: ignore[dict-item]
            "currency": (instrument.currency if instrument else "CNY"),
        },
        "asOf": as_of,
        "headline": f"报告内容损坏（v{report.version} · {report.id[:8]}）"[:160],
        "rating": {"stance": "watch", "conviction": 1, "horizon": "—"},
        "summary": (
            "这份报告的正文无法解析，已按最小可渲染内容展示；"
            "数据本身仍在库里，可先导出留档后再决定是否清理。"
        ),
        "keyMetrics": [],
        "sections": [
            {
                "id": "integrity",
                "title": "内容损坏",
                "blocks": [
                    {
                        "type": "callout",
                        "tone": "warn",
                        "title": "正文无法解析",
                        "text": (
                            f"报告 {report.id}（v{report.version}）的 doc_json 不是合法 JSON 或不是对象。"
                            "列表、版本链与成本记录不受影响；建议先导出留档，再决定是否删除。"
                        ),
                    }
                ],
            }
        ],
        "risks": [],
        "sources": [],
        "disclaimer": "本页为容错视图，不含投资建议。",
    }


def load_doc_safe(
    report: Report,
    instrument: Instrument | None = None,
) -> tuple[dict[str, Any], str]:
    """→ `(doc, integrity)`，`integrity ∈ {"ok", "corrupt"}`。**永不抛。**

    **五条读路径都必须走它**（详情 / latest / 导出 / 区间反查 / 版本比较）：
    v1 只改了前两条，而后三条恰恰是"损坏横幅"要用户去做的事（"请先导出/截图"）——
    导出用裸 `report.doc` 就会 500，那是把容错做成了陷阱。

    两件事**不**在这里做：

    * **不**再跑一次 schema 校验（校验发生在写入时；读时再校验一次等于给每次翻页加
      10 ms，还会把"老版本的旧文档"误判成损坏）；
    * **不**修数据（§19"只标不改数"）：壳只用于展示，`doc_json` 一个字节都不动。
    """
    try:
        parsed = json.loads(report.doc_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("doc_json 无法解析，按损坏处理 report=%s", report.id)
        return corrupt_shell(report, instrument), INTEGRITY_CORRUPT
    if not isinstance(parsed, dict):
        logger.warning("doc_json 不是对象，按损坏处理 report=%s", report.id)
        return corrupt_shell(report, instrument), INTEGRITY_CORRUPT
    return strip_underscored(parsed), INTEGRITY_OK


def count_active_runs(session: Session) -> int:
    # ★评审 P2-8：活跃三态读 `models.run.ACTIVE_STATUSES`（唯一真源），不写字面量元组。
    return int(
        session.execute(
            select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE_STATUSES))
        ).scalar_one()
    )


def active_run_for(session: Session, instrument_id: str) -> Run | None:
    return (
        session.execute(
            select(Run)
            .where(
                Run.instrument_id == instrument_id,
                Run.status.in_(("queued", "running", "cancelling")),
            )
            .order_by(Run.started_at.desc())
        )
        .scalars()
        .first()
    )


def next_version_number(session: Session, instrument_id: str) -> int:
    current_max = session.execute(
        select(func.max(Report.version)).where(Report.instrument_id == instrument_id)
    ).scalar_one()
    return int(current_max or 0) + 1
