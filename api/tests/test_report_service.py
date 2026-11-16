"""Versioning invariants: single latest pointer, soft delete, flags (AC-5, AC-20)."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select, text

from app.models import Bar, Quote, Report, Run, WatchlistItem
from app.services import report_service


def _make(session, instrument, sample, *, status="ready", provisional=False, **kwargs) -> Report:
    doc = copy.deepcopy(sample)
    if provisional:
        doc["provisional"] = True
    return report_service.create_version(
        session,
        instrument,
        doc,
        status=status,
        quality={"groundedRatio": 0.95, "missingSections": [], "prunedBlocks": 0, "source": "aragon"},
        is_provisional=provisional,
        **kwargs,
    )


def test_first_version_becomes_latest(session, instrument, sample_report) -> None:
    report = _make(session, instrument, sample_report)
    assert report.version == 1
    assert report.is_latest == 1
    assert report.status == "ready"


def test_second_version_supersedes_the_first(session, instrument, sample_report) -> None:
    """AC-5: regenerating produces v2 and v1 flips to `superseded`."""
    first = _make(session, instrument, sample_report)
    second = _make(session, instrument, sample_report)

    session.refresh(first)
    assert second.version == 2
    assert second.is_latest == 1
    assert first.is_latest == 0
    assert first.status == "superseded"


def test_only_one_latest_row_can_exist(session, instrument, sample_report) -> None:
    """The partial unique index `ux_reports_latest` is the backstop."""
    _make(session, instrument, sample_report)
    _make(session, instrument, sample_report)
    latest = (
        session.execute(select(Report).where(Report.instrument_id == instrument.id, Report.is_latest == 1))
        .scalars()
        .all()
    )
    assert len(latest) == 1


def test_provisional_versions_never_take_the_latest_pointer(session, instrument, sample_report) -> None:
    ready = _make(session, instrument, sample_report)
    provisional = _make(session, instrument, sample_report, status="provisional", provisional=True)

    session.refresh(ready)
    assert provisional.status == "provisional"
    assert provisional.is_latest == 0
    assert ready.is_latest == 1, "a failed attempt must not displace a good report"


def test_latest_of_falls_back_to_provisional_when_alone(session, instrument, sample_report) -> None:
    """★P1-10: a user whose only run failed still gets something to read."""
    provisional = _make(session, instrument, sample_report, status="provisional", provisional=True)
    latest = report_service.latest_of(session, instrument.id)
    assert latest is not None
    assert latest.id == provisional.id


def test_versions_are_listed_newest_first_and_pinned_float(session, instrument, sample_report) -> None:
    first = _make(session, instrument, sample_report)
    second = _make(session, instrument, sample_report)
    third = _make(session, instrument, sample_report)
    report_service.set_flags(session, first, pinned=True)

    rows, total = report_service.list_versions(session, instrument.id)
    assert total == 3
    assert rows[0].id == first.id, "pinned versions come first"
    assert {row.id for row in rows} == {first.id, second.id, third.id}


def test_set_latest_moves_the_pointer(session, instrument, sample_report) -> None:
    first = _make(session, instrument, sample_report)
    second = _make(session, instrument, sample_report)
    report_service.set_flags(session, first, set_latest=True)

    session.refresh(first)
    session.refresh(second)
    assert first.is_latest == 1
    assert second.is_latest == 0
    assert first.status == "ready"


def test_archiving_removes_the_latest_flag(session, instrument, sample_report) -> None:
    report = _make(session, instrument, sample_report)
    report_service.set_flags(session, report, archived=True)
    session.refresh(report)
    assert report.archived == 1
    assert report.is_latest == 0
    assert report.status == "superseded"


def test_archived_versions_are_hidden_by_default(session, instrument, sample_report) -> None:
    first = _make(session, instrument, sample_report)
    _make(session, instrument, sample_report)
    report_service.set_flags(session, first, archived=True)

    visible, total = report_service.list_versions(session, instrument.id)
    assert total == 1
    everything, all_total = report_service.list_versions(session, instrument.id, include_archived=True)
    assert all_total == 2


def test_soft_delete_keeps_the_row_and_moves_the_pointer(session, instrument, sample_report) -> None:
    """AC-20: DELETE is a soft delete — no FK error, audit trail intact."""
    first = _make(session, instrument, sample_report)
    second = _make(session, instrument, sample_report)
    report_service.soft_delete(session, second)

    session.expire_all()
    survivor = session.get(Report, second.id)
    assert survivor is not None, "the row must still exist"
    assert survivor.deleted_at is not None
    assert survivor.is_latest == 0

    first_after = session.get(Report, first.id)
    assert first_after is not None
    assert first_after.is_latest == 1, "the pointer should fall back to the surviving version"
    assert first_after.status == "ready"


def test_deleted_reports_are_invisible(session, instrument, sample_report) -> None:
    report = _make(session, instrument, sample_report)
    report_service.soft_delete(session, report)
    assert report_service.get(session, report.id) is None
    assert report_service.get(session, report.id, include_deleted=True) is not None
    rows, total = report_service.list_versions(session, instrument.id)
    assert total == 0 and rows == []


def test_soft_delete_skips_provisional_successors(session, instrument, sample_report) -> None:
    ready = _make(session, instrument, sample_report)
    provisional = _make(session, instrument, sample_report, status="provisional", provisional=True)
    report_service.soft_delete(session, ready)

    session.expire_all()
    assert session.get(Report, provisional.id).is_latest == 0


def test_soft_delete_keeps_the_run_link_intact(session, instrument, sample_report) -> None:
    """★P1-1: the row survives, so `runs.report_id` stays a valid audit link."""
    run = Run(
        id="run-1",
        instrument_id=instrument.id,
        status="succeeded",
        pct=100,
        started_at="2026-02-10T00:00:00Z",
        cost_known=0,
    )
    session.add(run)
    session.commit()

    report = _make(session, instrument, sample_report, run_id=run.id)
    session.refresh(run)
    assert run.report_id == report.id

    report_service.soft_delete(session, report)
    session.expire_all()
    assert session.get(Run, run.id).report_id == report.id
    assert session.get(Report, report.id).deleted_at is not None


def test_hard_delete_nulls_the_run_link_instead_of_failing(session, instrument, sample_report, monkeypatch) -> None:
    """The `SET NULL` on `runs.report_id` is what makes retention trimming safe."""
    run = Run(
        id="run-2",
        instrument_id=instrument.id,
        status="succeeded",
        pct=100,
        started_at="2026-02-10T00:00:00Z",
        cost_known=0,
    )
    session.add(run)
    session.commit()

    first = _make(session, instrument, sample_report, run_id=run.id)
    monkeypatch.setattr(report_service, "MAX_VERSIONS_PER_INSTRUMENT", 1)
    _make(session, instrument, sample_report)

    session.expire_all()
    assert session.get(Report, first.id) is None, "the old version should have been trimmed"
    assert session.get(Run, "run-2").report_id is None


def test_foreign_keys_are_actually_enforced(session, engine) -> None:
    """AC-20: `PRAGMA foreign_keys=ON` per connection, or CASCADE is decoration."""
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_deleting_an_instrument_cascades(session, engine, instrument) -> None:
    session.add(
        Bar(
            instrument_id=instrument.id,
            timeframe="1d",
            ts=1770681600,
            adjust="qfq",
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=100.0,
            source="synthetic",
        )
    )
    session.add(Quote(instrument_id=instrument.id, last=1.5, updated_at="2026-02-10T00:00:00Z"))
    session.add(WatchlistItem(instrument_id=instrument.id, sort_order=0, pinned=0, added_at="2026-02-10T00:00:00Z"))
    session.commit()

    session.delete(instrument)
    session.commit()

    assert session.execute(select(Bar).where(Bar.instrument_id == "CN:600519.SH")).first() is None
    assert session.get(Quote, "CN:600519.SH") is None
    assert session.get(WatchlistItem, "CN:600519.SH") is None


def test_orphan_instrument_reference_is_rejected(session) -> None:
    """A row pointing at a missing instrument must be a hard error, not a warning."""
    from sqlalchemy.exc import IntegrityError

    session.add(Quote(instrument_id="CN:DOES.NOT", last=1.0, updated_at="2026-02-10T00:00:00Z"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_version_trimming_keeps_the_newest(session, instrument, sample_report, monkeypatch) -> None:
    monkeypatch.setattr(report_service, "MAX_VERSIONS_PER_INSTRUMENT", 3)
    for _ in range(5):
        _make(session, instrument, sample_report)
    rows, total = report_service.list_versions(session, instrument.id, limit=100)
    assert total == 3
    assert max(row.version for row in rows) == 5


def test_pinned_versions_survive_trimming(session, instrument, sample_report, monkeypatch) -> None:
    monkeypatch.setattr(report_service, "MAX_VERSIONS_PER_INSTRUMENT", 2)
    pinned = _make(session, instrument, sample_report)
    report_service.set_flags(session, pinned, pinned=True)
    for _ in range(4):
        _make(session, instrument, sample_report)

    remaining = {row.id for row in session.execute(select(Report)).scalars()}
    assert pinned.id in remaining


def test_summarize_exposes_cost_known(session, instrument, sample_report) -> None:
    report = _make(session, instrument, sample_report, cost_usd=None, cost_known=False)
    summary = report_service.summarize(report)
    assert summary.costKnown is False
    assert summary.costUsd is None
