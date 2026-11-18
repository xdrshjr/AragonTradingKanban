"""`GET /api/v1/reports` 的 HTTP 契约（spec §23.5.1；AC-137~AC-140、AC-143）。

四条断言各自守一件会静默坏掉的事：

1. **翻页不重不漏**（AC-138）：45 行、`limit=20` 连续翻页必须得到 45 个互不重复的 id，且
   第三页 `nextCursor=null`。★P1-3：`generated_at` 是**秒级**文本 ⇒ 同一秒生成的一批报告
   必须靠 `id` 的 tie-break 才不丢行——这份夹具**全部落在同一秒**（`_iso()` 就是秒级），
   所以它同时是"tie-break 是正确性的一部分"的机器化版本。
2. **非法游标是 422**（`invalid_cursor`），不是静默忽略：静默忽略会让"翻页翻不动"变成幽灵 bug。
   把游标里的 `Z` 换成 `+00:00` 也必须 422——那正是"任何一次时间归一化都会整段漏行"的证据。
3. **`counts` 恒定、`total` 联动**（AC-140）。
4. **`/reports/abc` 不能被列表吞掉**（R-72）：它仍是 404 `report_not_found`。
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Instrument, Report
from app.services import report_service


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


def _shift_generated_at(session, *, seconds: int) -> None:
    """把库里所有报告的 `generated_at` 往前挪 `seconds` 秒（保持相对顺序与秒级 `Z` 形态）。

    只给"新插入的行必须严格更新"这类用例用：`generated_at` 是**秒级** `Text`，
    同一秒内的并列只能靠 `id` 排序，不挪走的话"谁更新"是没有答案的问题（★D-1）。
    """
    for row in session.execute(select(Report)).scalars().all():
        moment = datetime.strptime(row.generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        row.generated_at = (moment - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.commit()


@pytest.fixture()
def many_reports(session, sample_report):
    """3 个标的 × 15 份 = 45 份（同一秒内生成，逼出 id tie-break）。"""
    created = []
    for id_, symbol, name in [
        ("CN:600519.SH", "600519.SH", "贵州茅台"),
        ("CN:300750.SZ", "300750.SZ", "宁德时代"),
        ("US:AAPL", "AAPL", "Apple"),
    ]:
        instrument = _instrument(session, id_=id_, symbol=symbol, name=name)
        for version in range(1, 16):
            doc = copy.deepcopy(sample_report)
            doc["headline"] = f"{name} 第 {version} 版：白酒动销回暖"[:160]
            created.append(
                report_service.create_version(
                    session,
                    instrument,
                    doc,
                    status="ready",
                    quality={"groundedRatio": 0.91},
                )
            )
    return created


def test_list_reports_across_instruments(client, many_reports) -> None:
    """一页 20 行、形状齐全、`counts` 五个键都在、每行带 `docSha1` 与 `integrity`。"""
    response = client.get("/api/v1/reports", params={"scope": "all", "limit": 20})
    assert response.status_code == 200
    body = response.json()

    assert len(body["items"]) == 20
    assert body["total"] == 45
    assert set(body["counts"]) == {"latest", "all", "starred", "archived", "trash"}
    assert body["counts"]["all"] == 45
    assert body["counts"]["latest"] == 3, "每个标的只有一份 latest"
    assert body["nextCursor"] is not None

    row = body["items"][0]
    assert set(row) == {"report", "instrument", "docSha1", "integrity", "deletedAt"}
    assert row["integrity"] == "ok"
    assert row["deletedAt"] is None
    assert len(row["docSha1"]) == 40
    # 嵌套的 `ReportSummary` 一列都不许少（23 个键，一个不落地发）。
    summary_keys = {
        "id",
        "instrumentId",
        "version",
        "status",
        "title",
        "headline",
        "stance",
        "asOf",
        "generatedAt",
        "isLatest",
        "pinned",
        "archived",
        "provisional",
        "groundedRatio",
        "quality",
        "provider",
        "model",
        "costUsd",
        "costKnown",
        "tokensIn",
        "tokensOut",
        "durationMs",
        "docBytes",
    }
    assert set(row["report"]) == summary_keys
    # 标的是一次 join 拿齐的，前端不再逐行查 `/instruments/{id}`。
    assert set(row["instrument"]) == {"id", "symbol", "name", "market", "currency"}


def test_five_scopes_return_what_they_promise(client, many_reports) -> None:
    for scope in ("latest", "all", "starred", "archived", "trash"):
        response = client.get("/api/v1/reports", params={"scope": scope, "limit": 50})
        assert response.status_code == 200, scope
        body = response.json()
        if scope == "latest":
            assert body["total"] == 3
        elif scope == "all":
            assert body["total"] == 45
        else:
            assert body["total"] == 0, scope
            assert body["items"] == []


def test_invalid_scope_is_422(client) -> None:
    response = client.get("/api/v1/reports", params={"scope": "nope"})
    assert response.status_code == 422


def test_cursor_pagination_has_no_duplicates_and_no_gaps(client, many_reports) -> None:
    """AC-138：45 行、`limit=20` ⇒ 3 页、45 个互不重复的 id、第三页 `nextCursor=null`。"""
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, object] = {"scope": "all", "limit": 20}
        if cursor:
            params["before"] = cursor
        body = client.get("/api/v1/reports", params=params).json()
        pages += 1
        seen.extend(item["report"]["id"] for item in body["items"])
        cursor = body["nextCursor"]
        if cursor is None:
            break
        assert pages < 10, "游标没有推进"

    assert pages == 3
    assert len(seen) == 45
    assert len(set(seen)) == 45, "翻页出现了重复行（id tie-break 丢了）"


def test_a_report_inserted_between_pages_does_not_shift_the_window(
    client, many_reports, session, sample_report
) -> None:
    """keyset 的好处：翻页过程中新增一行不会让"已翻过的页"重复出现（OFFSET 会）。

    ★实施节点发现（见 spec §23.12 D-1）：这条用例原先的前提不成立。`generated_at` 是
    **秒级**文本，夹具 45 行全部落在同一秒（这正是上面第 1 条要的 tie-break 场景），
    所以"插入的那一份"与它们在 `generated_at` 上并列、只能靠 `id` 排序 —— 它的 id 落在
    游标之下时出现在第二页是**正确的键集行为**，与"窗口不该漂移"是两件事。
    修法不是放松断言，而是先把夹具整体挪到过去一小时：于是新生成的那份**严格更新**，
    "落在窗口之外"才是一个能被机器反驳的命题。
    """
    _shift_generated_at(session, seconds=3600)

    first = client.get("/api/v1/reports", params={"scope": "all", "limit": 20}).json()
    first_ids = [item["report"]["id"] for item in first["items"]]

    instrument = session.get(Instrument, "US:AAPL")
    newest = report_service.create_version(
        session, instrument, copy.deepcopy(sample_report), status="ready"
    )

    second = client.get(
        "/api/v1/reports", params={"scope": "all", "limit": 20, "before": first["nextCursor"]}
    ).json()
    second_ids = [item["report"]["id"] for item in second["items"]]
    assert not set(first_ids) & set(second_ids), "已翻过的页不该重复出现"
    assert newest.id not in first_ids + second_ids


def test_invalid_cursor_is_422_not_silently_ignored(client, many_reports) -> None:
    for bad in ("nonsense", "2026-11-16T10:10:22Z", "2026-11-16T10:10:22Z|deadbeef", "2026-11-16|" + "a" * 32):
        response = client.get("/api/v1/reports", params={"scope": "all", "before": bad})
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "invalid_cursor"
        assert response.json()["error"]["detail"]["before"] == bad


def test_a_normalized_cursor_is_rejected(client, many_reports) -> None:
    """R-76：`Z` → `+00:00` 必须 **422**，而不是"返回一个看起来正常的结果"。"""
    page = client.get("/api/v1/reports", params={"scope": "all", "limit": 20}).json()
    at, _, report_id = page["nextCursor"].partition("|")
    normalized = f"{at.replace('Z', '+00:00')}|{report_id}"
    response = client.get("/api/v1/reports", params={"scope": "all", "before": normalized})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


def test_valid_cursor_is_accepted_verbatim(client, many_reports) -> None:
    page = client.get("/api/v1/reports", params={"scope": "all", "limit": 5}).json()
    response = client.get("/api/v1/reports", params={"scope": "all", "limit": 5, "before": page["nextCursor"]})
    assert response.status_code == 200
    assert response.json()["items"]


def test_with_counts_off_keeps_total(client, many_reports) -> None:
    body = client.get("/api/v1/reports", params={"scope": "all", "withCounts": 0, "limit": 5}).json()
    assert body["counts"] == {}
    assert body["total"] == 45, "关掉计数不等于关掉 total"


def test_q_filters_total_but_not_counts(client, many_reports) -> None:
    """★P1-4：tab 徽章跟着搜索变，就是把过滤器当成了数据变更。"""
    base = client.get("/api/v1/reports", params={"scope": "all", "limit": 5}).json()
    filtered = client.get("/api/v1/reports", params={"scope": "all", "limit": 5, "q": "宁德时代"}).json()
    assert filtered["total"] == 15
    assert filtered["counts"] == base["counts"]
    assert {item["instrument"]["symbol"] for item in filtered["items"]} == {"300750.SZ"}


def test_q_matches_the_headline_field(client, many_reports) -> None:
    body = client.get("/api/v1/reports", params={"scope": "all", "q": "第 7 版"}).json()
    assert body["total"] == 3, "三份 v7 的 headline 里都有「第 7 版」"


def test_q_escapes_like_wildcards(client, many_reports) -> None:
    """`%` 是字面量，不是通配符：转义漏了会变成"匹配一切"。"""
    body = client.get("/api/v1/reports", params={"scope": "all", "q": "%"}).json()
    assert body["total"] == 0


def test_q_longer_than_80_is_422(client, many_reports) -> None:
    response = client.get("/api/v1/reports", params={"scope": "all", "q": "x" * 81})
    assert response.status_code == 422


def test_unknown_instrument_filter_is_an_empty_200(client, many_reports) -> None:
    """过滤条件不匹配不是"资源不存在"。"""
    response = client.get("/api/v1/reports", params={"scope": "all", "instrumentId": "CN:NOPE.SH"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_include_active_returns_running_runs(client, many_reports, session) -> None:
    """AC-152（后端那一半）：`includeActive=1` 时 `active` 字段齐全。"""
    from app.models import Run

    session.add(
        Run(
            id="run-active-1",
            instrument_id="CN:600519.SH",
            status="running",
            stage="research",
            pct=40,
            started_at="2026-11-18T06:40:00Z",
            cost_known=0,
        )
    )
    session.commit()

    body = client.get(
        "/api/v1/reports", params={"scope": "latest", "includeActive": 1, "limit": 6}
    ).json()
    assert len(body["active"]) == 1
    item = body["active"][0]
    assert set(item) == {"runId", "instrumentId", "symbol", "name", "stage", "pct", "startedAt"}
    assert item["stage"] == "research"
    assert item["pct"] == 40
    assert item["symbol"] == "600519.SH"

    plain = client.get("/api/v1/reports", params={"scope": "latest"}).json()
    assert plain["active"] == [], "默认不附带 active"


def test_a_non_hex_report_id_is_still_404(client, many_reports) -> None:
    """R-72：`/reports/abc` 不能被列表吞掉返回 200 列表。"""
    response = client.get("/api/v1/reports/abc")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_not_found"


def test_unknown_report_id_is_404_not_a_crash(client) -> None:
    response = client.get("/api/v1/reports/" + "f" * 32)
    assert response.status_code == 404


def test_limit_ceiling_is_50(client, many_reports) -> None:
    assert client.get("/api/v1/reports", params={"scope": "all", "limit": 51}).status_code == 422
    assert client.get("/api/v1/reports", params={"scope": "all", "limit": 0}).status_code == 422
