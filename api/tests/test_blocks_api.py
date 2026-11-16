"""`GET /reports/{id}/blocks` —— 区间反查的 REST 契约（spec §19.3.3 / AC-54）。

覆盖四个错误码（`invalid_range` × 2 / `range_too_wide` / `report_not_found`）、
`limit` 的**夹取**语义（越界不是 422）、`provisional` 的显式 `reason`、`asOf`
回显与 `truncated` 的"只有真截断才为真"。

报告一律走 `report_service.create_version` 落库——与 `test_reports_api.py` 的
`seeded_report` 同一路径，免得测试自己拼一行 Report 而与版本指针语义脱节。
`sample_report` 里 2025 年的日期足够多（表行 `2024Q4`/`2025Q1`、时间线的
`2025-04-29`、`2025 年三季度` 等），所以区间用自然年即可，不必造文档。
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.models import Report
from app.services import report_service

#: 闭区间的一年——`sample_report` 的正文/表格/时间线都落在这段里。
RANGE_2025 = {"from": "2025-01-01", "to": "2025-12-31"}


@pytest.fixture()
def ready_report(session, instrument, sample_report) -> Report:
    """一份 `ready` 报告：文档里的 2025 年日期可命中多个块。"""
    return report_service.create_version(
        session,
        instrument,
        sample_report,
        status="ready",
        quality={"groundedRatio": 0.95},
    )


def _blocks(client, report_id: str, **params: object):
    return client.get(f"/api/v1/reports/{report_id}/blocks", params=params)


# ── 正常路径 ────────────────────────────────────────────────────────────────


def test_blocks_returns_items_for_a_range_the_document_talks_about(client, ready_report) -> None:
    """200 + 有内容的 items，且每一项都能被 UI 直接渲染。"""
    response = _blocks(client, ready_report.id, **RANGE_2025)
    assert response.status_code == 200
    body = response.json()

    assert body["reportId"] == ready_report.id
    assert body["range"] == {"from": "2025-01-01", "to": "2025-12-31"}
    assert body["items"], "sample_report 有多个块谈 2025 年，空列表说明反查没生效"

    item = body["items"][0]
    assert {"blockId", "sectionId", "sectionTitle", "kind", "overlapDays", "matchKind", "text"} <= set(item)
    # blockId = f"{sectionId}#{index}"（block_text 的唯一推导规则，V4-P1-2）
    assert item["blockId"].startswith(f"{item['sectionId']}#")
    assert item["overlapDays"] > 0, "闭区间重叠天数必须为正，否则它就不该出现在结果里"
    assert body["total"] >= len(body["items"])
    assert body["truncated"] is False, "默认 limit 足够装下整份报告的命中"
    assert body["reason"] is None, "有结果就不需要解释为什么是空的"


def test_every_hit_is_unique_and_actually_overlaps(client, ready_report) -> None:
    """块不重复、重叠天数为正：两条都是 UI 的直接前提（去重靠 `blockId`）。"""
    body = _blocks(client, ready_report.id, **RANGE_2025).json()
    block_ids = [item["blockId"] for item in body["items"]]
    assert len(block_ids) == len(set(block_ids)), "同一个块不能出现两次"
    assert all(item["overlapDays"] > 0 for item in body["items"])
    assert all(item["matchKind"] for item in body["items"]), "matchKind 决定 UI 的角标文案"


def test_as_of_is_echoed_from_the_document(client, ready_report, sample_report) -> None:
    """`asOf` 取自 `doc.asOf`：相对期间的锚点，也是 AC-53"永久可复现"的凭据。"""
    body = _blocks(client, ready_report.id, **RANGE_2025).json()
    assert body["asOf"] == sample_report["asOf"]


def test_limit_is_clamped_not_rejected(client, ready_report) -> None:
    """AC-54：`limit=500` 夹到 200，返回 200 而不是 422——"要太多"不是"参数错了"。"""
    response = _blocks(client, ready_report.id, limit=500, **RANGE_2025)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 200
    assert body["total"] >= len(body["items"])


def test_truncated_is_true_exactly_when_total_exceeds_shown(client, ready_report) -> None:
    """`truncated` 只在"确实少给了"时为真，且 `total` 仍是完整命中数。"""
    full = _blocks(client, ready_report.id, **RANGE_2025).json()
    assert full["total"] > 1, "这条断言要求文档至少有 2 个命中块"

    clipped = _blocks(client, ready_report.id, limit=1, **RANGE_2025).json()
    assert len(clipped["items"]) == 1
    assert clipped["total"] == full["total"], "total 是完整命中数，不随 limit 变化"
    assert clipped["truncated"] is True


def test_exactly_five_calendar_years_is_allowed(client, ready_report) -> None:
    """上限是 5 个日历年：正好 5 年放行，多一天才拒（边界写在 `max_range_years` 里）。"""
    response = _blocks(client, ready_report.id, **{"from": "2020-01-01", "to": "2025-01-01"})
    assert response.status_code == 200


# ── 四个错误码 ──────────────────────────────────────────────────────────────


def test_from_later_than_to_is_invalid_range(client, ready_report) -> None:
    response = _blocks(client, ready_report.id, **{"from": "2025-12-31", "to": "2025-01-01"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_range"
    assert error["detail"]["from"] == "2025-12-31"
    assert error["detail"]["to"] == "2025-01-01"


@pytest.mark.parametrize("bad", ["2025-1-1", "not-a-date", "2025-13-01", ""])
def test_unparseable_date_is_invalid_range(client, ready_report, bad) -> None:
    """格式错误与"先后颠倒"共用 `invalid_range`：前端只需一种恢复动作（改日期）。"""
    response = _blocks(client, ready_report.id, **{"from": bad, "to": "2025-12-31"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_range"


def test_span_over_five_years_is_range_too_wide(client, ready_report) -> None:
    """挡住"拿 1900-01-01 做全量扫描"这类用法，且把上限告诉调用方。"""
    response = _blocks(client, ready_report.id, **{"from": "2015-01-01", "to": "2025-12-31"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "range_too_wide"
    assert error["detail"]["maxYears"] == 5


def test_missing_report_is_404(client) -> None:
    response = _blocks(client, "no-such-report", **RANGE_2025)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_not_found"


def test_report_still_generating_is_409(client, session, ready_report) -> None:
    """第五个分支（不是四码之一，但同属"为什么不给我结果"）：生成中 → 409。"""
    session.execute(update(Report).where(Report.id == ready_report.id).values(status="running"))
    session.commit()

    response = _blocks(client, ready_report.id, **RANGE_2025)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_not_ready"


# ── provisional ─────────────────────────────────────────────────────────────


def test_provisional_report_says_why_it_is_empty(client, session, instrument, sample_report) -> None:
    """临时版没有结构化章节：200 + `items: []` + `reason`，而不是假装"这段没人谈"。"""
    provisional = report_service.create_version(
        session,
        instrument,
        {**sample_report, "provisional": True},
        status="provisional",
        is_provisional=True,
    )
    assert provisional.status == "provisional"

    response = _blocks(client, provisional.id, **RANGE_2025)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["truncated"] is False
    assert body["reason"] == "provisional_no_dates"
    assert body["asOf"] == sample_report["asOf"]
