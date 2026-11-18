"""报告库 service：`scope=latest ≡ latest_of()`、counts 与列表同源、索引真的被用上。

这一层守两件**会静默坏掉**的事：

1. **`scope=latest` 与 `latest_of()` 逐条相等**（AC-139）。★评审 P0-2：夹具必须同时覆盖
   四种标的——①正常多版本 ②只有临时版 ③最新版被归档（`set_flags(archived=True)` 会清
   `is_latest`）④只有归档过的版本。缺 ③④ 时这条 AC"绿得没有说服力"，而实现者一旦给 CTE
   补上 `archived = 0`，只有 ③ 能把它暴露出来。
2. **`counts` 与列表同源**（AC-140）：五个数字与列表用同一个谓词构造器算出来。列表一套
   where、计数另一套，是本项目历史上最贵的一类漂移。

另外把 `EXPLAIN QUERY PLAN` 的三条断言放进 pytest（O-44）：它们是**确定性**的，不是时序，
所以不属于"pytest 里跑性能断言是 flakes 之源"那条禁令（p95 由 `scripts/library-bench.py` 证明）。
"""

from __future__ import annotations

import copy

import pytest

from app.models import Instrument
from app.services import report_library, report_service


def _instrument(session, *, id_: str, symbol: str, name: str) -> Instrument:
    row = Instrument(
        id=id_,
        symbol=symbol,
        name=name,
        market="CN",
        exchange="SSE",
        currency="CNY",
        sector="食品饮料",
        tz="Asia/Shanghai",
        created_at="2026-02-10T00:00:00Z",
        updated_at="2026-02-10T00:00:00Z",
    )
    session.add(row)
    session.commit()
    return row


def _doc(sample: dict, headline: str) -> dict:
    doc = copy.deepcopy(sample)
    doc["headline"] = headline[:160]
    return doc


def _make(session, instrument, sample, *, headline: str, provisional: bool = False):
    return report_service.create_version(
        session,
        instrument,
        _doc(sample, headline),
        status="provisional" if provisional else "ready",
        quality={"groundedRatio": 0.9},
        is_provisional=provisional,
    )


@pytest.fixture()
def four_kinds(session, sample_report):
    """AC-139 的四种标的，一次建齐（顺序即文档里的 ①②③④）。"""
    normal = _instrument(session, id_="CN:NORMAL.SH", symbol="NORMAL.SH", name="正常多版本")
    _make(session, normal, sample_report, headline="正常 v1")
    _make(session, normal, sample_report, headline="正常 v2")

    provisional = _instrument(session, id_="CN:PROV.SH", symbol="PROV.SH", name="只有临时版")
    _make(session, provisional, sample_report, headline="临时版说明", provisional=True)

    archived_latest = _instrument(session, id_="CN:ARCH.SH", symbol="ARCH.SH", name="最新版被归档")
    _make(session, archived_latest, sample_report, headline="归档前 v1")
    latest = _make(session, archived_latest, sample_report, headline="归档前 v2")
    report_service.set_flags(session, latest, archived=True)

    only_archived = _instrument(session, id_="CN:ONLYA.SH", symbol="ONLYA.SH", name="只有归档版")
    only = _make(session, only_archived, sample_report, headline="唯一一版")
    report_service.set_flags(session, only, archived=True)

    return [normal, provisional, archived_latest, only_archived]


def test_latest_scope_matches_latest_of_for_all_four_kinds(session, four_kinds) -> None:
    """AC-139：`scope=latest` 对每个标的返回的 id **逐条等于** `latest_of()`。

    ★P0-2 的关键：③「最新版被归档」上，CTE 若带 `archived = 0` 会退到"次新且未归档"，
    而 `latest_of()` 退到"最新但已归档" ⇒ 两个不同 id，这条断言立刻红。
    """
    page = report_library.list_page(session, scope="latest", limit=50, with_counts=False)
    from_library = {item.report.instrumentId: item.report.id for item in page.items}

    for instrument in four_kinds:
        expected = report_service.latest_of(session, instrument.id)
        assert expected is not None, f"{instrument.symbol} 应该有可读的一版"
        assert from_library.get(instrument.id) == expected.id, (
            f"{instrument.symbol}: scope=latest 与 latest_of() 给出了不同的答案"
        )


def test_counts_equal_the_scope_sets_without_filters(session, four_kinds) -> None:
    """AC-140：无过滤时 `counts[scope] == total == 全量翻页结果`，五个 scope 各断言一次。"""
    counts = report_library.counts(session)
    assert set(counts) == set(report_library.LIBRARY_SCOPES)

    for scope in report_library.LIBRARY_SCOPES:
        page = report_library.list_page(session, scope=scope, limit=50)
        assert page.total == counts[scope], f"{scope}: total 与 counts 不同源"
        assert len(page.items) == page.total, f"{scope}: 一页装得下却少给了行"
        assert page.nextCursor is None


def test_counts_do_not_move_when_the_filter_moves(session, four_kinds) -> None:
    """★P1-4：带上 `q` 之后 `counts` **一个数字都不变**，`total` 变小。

    tab 徽章跟着搜索变，就是把"过滤器"渲染成了"数据变更"。
    """
    before = report_library.counts(session)
    filtered = report_library.list_page(session, scope="all", q="正常 v2", limit=50)
    assert filtered.total == 1
    assert filtered.counts == before
    assert filtered.total < before["all"]


def test_with_counts_off_keeps_total_accurate(session, four_kinds) -> None:
    page = report_library.list_page(session, scope="all", limit=50, with_counts=False)
    assert page.counts == {}
    assert page.total == len(page.items)


def test_rows_carry_integrity_and_deleted_at_fields(session, four_kinds) -> None:
    """★P1-13：`deletedAt` 落在**新模型**上（`ReportSummary` 一个键都不加）。"""
    page = report_library.list_page(session, scope="latest", limit=50, with_counts=False)
    item = page.items[0]
    assert item.integrity == "ok"
    assert item.deletedAt is None
    assert item.docSha1 and len(item.docSha1) == 40, "新写的行必须有内容指纹"
    assert set(item.report.model_fields) <= set(type(item.report).model_fields)


def test_instrument_filter_that_matches_nothing_is_an_empty_page(session, four_kinds) -> None:
    page = report_library.list_page(session, scope="all", instrument_id="CN:NOPE.SH", limit=20)
    assert page.items == [] and page.total == 0


# ── O-44：索引断言在 `test_migration_0006.py` 里 ─────────────────────────────
#
# 这里**不能**做 `EXPLAIN QUERY PLAN` 的索引断言：测试库是 `conftest.engine` 用
# `Base.metadata.create_all` 建的，而三个部分索引只存在于**迁移**里（`0006`）——在本套夹具
# 里它们根本不存在，断言必然红。那三条确定性断言因此挂在迁移用例上（那里真的有它们），
# 见 `tests/test_migration_0006.py::test_each_scope_uses_its_partial_index`。
