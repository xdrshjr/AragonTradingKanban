"""回收站：裁剪改软删、restore、`purge_trash`、FTS 成对（spec §23.3.5；AC-148/150/151）。

四件**性质**，每一件都对着一个具体的坏结局：

* **裁剪可逆**（AC-148）：`_trim_versions` 从 `session.delete` 改成软删 ⇒ 被裁的版本进
  `scope=trash`；置顶行既不算数也不会被裁；裁剪抛异常不影响"保存成功"这个结论。
* **能救回来**（AC-150）：`POST /reports/{id}/restore` 200；对没删的调 → 409
  `report_not_deleted`；恢复"唯一存活版本"且它未归档时 `is_latest=1`，并且不违反
  `ux_reports_latest`（部分唯一索引）。
* **只留一个硬删入口**（§23.3.5 第 3 条）：`purge_trash()` 必须过 `TRASH_RETENTION_DAYS`。
* **FTS 成对**（AC-151）：软删后搜不到、restore 后**同一次请求结束即可**再搜到。
"""

from __future__ import annotations

import copy

from sqlalchemy import func, select, text

from app.models import Instrument, Report, Run
from app.services import report_service, search_index

HEADLINE = "白酒动销回暖，维持买入"


def _doc(sample: dict, *, headline: str) -> dict:
    doc = copy.deepcopy(sample)
    doc["headline"] = headline[:160]
    return doc


def _fts_rows(session, report_id: str) -> int:
    return int(
        session.execute(
            text(f"SELECT count(*) FROM {search_index.REPORT_FTS} WHERE report_id = :rid"),
            {"rid": report_id},
        ).scalar_one()
    )


# ── 裁剪改软删（AC-148 前半） ──────────────────────────────────────────────


def test_trim_soft_deletes_and_enters_trash(session, instrument, sample_report, monkeypatch) -> None:
    first = report_service.create_version(
        session, instrument, _doc(sample_report, headline="会被裁的那一版"), status="ready"
    )
    monkeypatch.setattr(report_service, "MAX_VERSIONS_PER_INSTRUMENT", 1)
    report_service.create_version(
        session, instrument, _doc(sample_report, headline="留下来的那一版"), status="ready"
    )

    session.expire_all()
    row = session.get(Report, first.id)
    assert row is not None and row.deleted_at is not None
    assert _fts_rows(session, first.id) == 0, "软删必须同时清掉索引（AC-33/AC-151）"

    from app.services import report_library

    trashed = report_library.list_page(session, scope="trash", limit=50, with_counts=False)
    assert [item.report.id for item in trashed.items] == [first.id]
    assert trashed.items[0].deletedAt is not None


def test_pinned_versions_are_exempt_from_trimming(session, instrument, sample_report, monkeypatch) -> None:
    pinned = report_service.create_version(
        session, instrument, _doc(sample_report, headline="置顶的那一版"), status="ready"
    )
    report_service.set_flags(session, pinned, pinned=True)
    monkeypatch.setattr(report_service, "MAX_VERSIONS_PER_INSTRUMENT", 1)
    report_service.create_version(
        session, instrument, _doc(sample_report, headline="新的一版"), status="ready"
    )

    session.expire_all()
    fresh = session.get(Report, pinned.id)
    assert fresh.deleted_at is None, "置顶行既不算数也不会被裁"


def test_trim_failure_does_not_fail_the_persist(session, instrument, sample_report, monkeypatch) -> None:
    """不变量 3（E-128）：裁剪抛异常时报告已经落库，绝不能把这次生成渲染成失败。"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(report_service, "_trim_versions", boom)
    report = report_service.create_version(session, instrument, sample_report, status="ready")
    assert report.id, "裁剪失败不该影响返回值"
    assert session.get(Report, report.id) is not None


# ── restore（AC-150） ─────────────────────────────────────────────────────


def test_restore_brings_it_back(client, session, sample_report) -> None:
    from app.models import Instrument as InstrumentRow

    instrument = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()["instrument"]
    row = session.get(InstrumentRow, instrument["id"])
    report = report_service.create_version(session, row, sample_report, status="ready")
    report_id = report.id
    report_service.soft_delete(session, session.get(Report, report_id))

    before = client.get("/api/v1/reports", params={"scope": "trash"}).json()
    assert [item["report"]["id"] for item in before["items"]] == [report_id]

    response = client.post(f"/api/v1/reports/{report_id}/restore")
    assert response.status_code == 200
    assert response.json()["id"] == report_id

    after = client.get("/api/v1/reports", params={"scope": "all"}).json()
    assert report_id in [item["report"]["id"] for item in after["items"]]
    # 它是唯一存活版本且未归档 ⇒ 抢回 is_latest，并且不违反 ux_reports_latest。
    session.expire_all()
    assert session.get(Report, report_id).is_latest == 1
    assert len(
        session.execute(
            select(Report).where(Report.instrument_id == instrument["id"], Report.is_latest == 1)
        ).scalars().all()
    ) == 1


def test_restore_of_a_live_report_is_409(client, session, sample_report) -> None:
    from app.models import Instrument as InstrumentRow

    instrument = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()["instrument"]
    row = session.get(InstrumentRow, instrument["id"])
    report = report_service.create_version(session, row, sample_report, status="ready")

    response = client.post(f"/api/v1/reports/{report.id}/restore")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_not_deleted"
    assert response.json()["error"]["detail"]["reportId"] == report.id


def test_restore_of_an_unknown_id_is_404(client) -> None:
    response = client.post("/api/v1/reports/" + "a" * 32 + "/restore")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_not_found"


def test_an_archived_version_does_not_grab_the_latest_pointer(session, instrument, sample_report) -> None:
    """★P2-11：归档版恢复后仍然不能当门面（与 `set_flags(archived=True)` 的规则一致）。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")
    report_service.set_flags(session, report, archived=True)
    report_service.soft_delete(session, session.get(Report, report.id))
    report_service.restore(session, session.get(Report, report.id))

    session.expire_all()
    fresh = session.get(Report, report.id)
    assert fresh.deleted_at is None
    assert fresh.archived == 1
    assert fresh.is_latest == 0


# ── 唯一硬删入口（§23.3.5 第 3 条） ─────────────────────────────────────────


def test_purge_respects_the_retention_window(session, instrument, sample_report) -> None:
    fresh = report_service.create_version(session, instrument, sample_report, status="ready")
    report_service.soft_delete(session, fresh)

    assert report_service.purge_trash(session, retention_days=30) == {"reports": 0, "asks": 0}
    assert session.get(Report, fresh.id) is not None

    purged = report_service.purge_trash(session, retention_days=0)
    assert purged["reports"] == 1
    assert session.get(Report, fresh.id) is None


def test_purge_counts_the_asks_it_cascades(session, instrument, sample_report) -> None:
    """★P1-8：`report_asks.report_id` 是 CASCADE ⇒ 永久清理会连带删掉追问历史。"""
    from app.models import ReportAsk

    report = report_service.create_version(session, instrument, sample_report, status="ready")
    session.add(
        ReportAsk(
            id="ask_0000000000000000000000000000000001",
            report_id=report.id,
            question="这次毛利率为什么提升？",
            mode="cite",
            status="done",
            grounded=0,
            citations_dropped=0,
            created_at="2026-11-18T00:00:00Z",
        )
    )
    session.commit()
    report_service.soft_delete(session, report)

    purged = report_service.purge_trash(session, retention_days=0)
    assert purged == {"reports": 1, "asks": 1}, "计数必须写清楚（R-78：用户有知情权）"
    assert session.execute(select(func.count()).select_from(ReportAsk)).scalar_one() == 0


def test_purge_nulls_the_run_pointer(session, instrument, sample_report) -> None:
    session.add(
        Run(
            id="run-trash",
            instrument_id=instrument.id,
            status="succeeded",
            pct=100,
            started_at="2026-02-10T00:00:00Z",
            cost_known=0,
        )
    )
    session.commit()
    report = report_service.create_version(
        session, instrument, sample_report, status="ready", run_id="run-trash"
    )
    report_service.soft_delete(session, report)
    report_service.purge_trash(session, retention_days=0)

    session.expire_all()
    assert session.get(Run, "run-trash").report_id is None


# ── FTS 成对（AC-151） ────────────────────────────────────────────────────


def test_restore_reindexes_in_the_same_transaction(session, instrument, sample_report) -> None:
    report = report_service.create_version(
        session, instrument, _doc(sample_report, headline=HEADLINE), status="ready"
    )
    assert _fts_rows(session, report.id) > 0, "写入即可搜到（O-4）"

    report_service.soft_delete(session, report)
    assert _fts_rows(session, report.id) == 0

    report_service.restore(session, session.get(Report, report.id))
    assert _fts_rows(session, report.id) > 0, "恢复必须**同事务**重建索引，不是后台重建"


def test_trim_removes_index_rows_for_every_trimmed_version(
    session, instrument, sample_report, monkeypatch
) -> None:
    first = report_service.create_version(session, instrument, sample_report, status="ready")
    monkeypatch.setattr(report_service, "MAX_VERSIONS_PER_INSTRUMENT", 1)
    report_service.create_version(
        session, instrument, _doc(sample_report, headline="第二版"), status="ready"
    )
    assert _fts_rows(session, first.id) == 0


def test_the_only_hard_delete_entry_point_is_purge_trash() -> None:
    """`session.delete(...)` 不许再出现在裁剪路径里（那是"回收站进不去"的根因）。

    用 AST 数**调用**，而不是数文本：`_trim_versions` 的文档里写着"从 `session.delete`
    改成软删"——那是解释，不是行为。断言必须能分辨这两件事。
    """
    import ast
    import inspect

    def delete_calls(function) -> int:
        tree = ast.parse(inspect.getsource(function))
        return sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete"
        )

    assert delete_calls(report_service._trim_versions) == 0
    assert delete_calls(report_service.purge_trash) > 0


def test_instrument_rows_are_untouched_by_purge(session, instrument, sample_report) -> None:
    report = report_service.create_version(session, instrument, sample_report, status="ready")
    report_service.soft_delete(session, report)
    report_service.purge_trash(session, retention_days=0)
    assert session.get(Instrument, instrument.id) is not None
