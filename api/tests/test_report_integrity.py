"""不变量 2：损坏的 doc **不是 500**（spec §23.3.6 / AC-147，★P0-1）。

五条读路径必须**全部**走 `report_service.load_doc_safe()`：

1. `GET /reports/{id}`（详情）
2. `GET /instruments/{id}/reports/latest`（工作台首屏）
3. `GET /reports/{id}/export?format=html`（**导出**）
4. `GET /reports/{id}/blocks`（区间反查）
5. `GET /reports/{a}/diff/{b}`（版本比较）

v1 只改了前两条，而后三条恰恰是"损坏横幅"让用户去点的东西（"请先导出/截图"）——
导出用裸 `report.doc` 就会 500，那是**把容错做成了陷阱**。

另外两条：壳必须能通过 `ReportDoc` 校验（★P0-1 的第一版自己过不了：`summary` 只有 1 个字符
而它有 `min_length=10`），以及"列表用 SQL 的 `json_valid()`、详情用 Python 解析"两种实现
的判定语义必须一致（★P1-2）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.schemas.report import ReportDoc
from app.services import report_service


@pytest.fixture()
def corrupt_report(client, session, sample_report):
    """一版好报告 + 一版坏报告（同一标的，坏的是最新）。"""
    from app.models import Instrument

    instrument = client.post("/api/v1/symbols/resolve", json={"query": "600519"}).json()["instrument"]
    row = session.get(Instrument, instrument["id"])
    good = report_service.create_version(session, row, sample_report, status="ready")
    bad = report_service.create_version(session, row, sample_report, status="ready")
    # 直接把 doc_json 打坏（模拟磁盘位翻转 / 手工 SQL / 半写事务）。
    session.execute(text("UPDATE reports SET doc_json = '{' WHERE id = :id"), {"id": bad.id})
    session.commit()
    return {"instrumentId": instrument["id"], "good": good.id, "bad": bad.id}


def test_corrupt_doc_returns_200_with_a_valid_shell(client, corrupt_report) -> None:
    response = client.get(f"/api/v1/reports/{corrupt_report['bad']}")
    assert response.status_code == 200, "一行坏数据不该让整份报告变成 500 页面"
    body = response.json()
    assert body["integrity"] == "corrupt"
    # ★P0-1 的核心：壳**自己**必须过 ReportDoc，否则 500 只是从 json.loads 挪到了 pydantic。
    ReportDoc.model_validate(body["doc"])
    assert len(body["doc"]["summary"]) >= 10
    assert len(body["doc"]["asOf"]) >= 10
    callout = body["doc"]["sections"][0]["blocks"][0]
    assert callout["type"] == "callout" and {"tone", "title", "text"} <= set(callout)
    # 列表与版本链不受影响：摘要仍然读得出来。
    assert body["report"]["id"] == corrupt_report["bad"]


def test_a_good_doc_still_reports_ok(client, corrupt_report) -> None:
    body = client.get(f"/api/v1/reports/{corrupt_report['good']}").json()
    assert body["integrity"] == "ok"
    assert body["doc"]["headline"]


def test_latest_endpoint_still_answers(client, corrupt_report) -> None:
    """latest 指针不变（`latest_of()` 只按行取、不解析 doc），详情页给壳。"""
    response = client.get(f"/api/v1/instruments/{corrupt_report['instrumentId']}/reports/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["id"] == corrupt_report["bad"]
    assert body["integrity"] == "corrupt"


@pytest.mark.parametrize("fmt", ["html", "md"])
def test_export_of_a_corrupt_report_still_200(client, corrupt_report, fmt: str) -> None:
    """★P0-1：损坏横幅让用户"先导出留档"，那条路必须能走通。"""
    response = client.get(f"/api/v1/reports/{corrupt_report['bad']}/export", params={"format": fmt})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    text_body = response.text
    assert "正文无法解析" in text_body, "导出的应该是一份说明自己坏了的文档，而不是空白"
    assert "原文已损坏" in text_body


def test_range_query_of_a_corrupt_report_is_not_500(client, corrupt_report) -> None:
    response = client.get(
        f"/api/v1/reports/{corrupt_report['bad']}/blocks",
        params={"from": "2026-01-01", "to": "2026-12-31"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["reason"] == "corrupt_doc", "要说清'读不出来'，而不是'没日期'"


def test_diff_of_a_corrupt_report_is_not_500(client, corrupt_report) -> None:
    response = client.get(f"/api/v1/reports/{corrupt_report['good']}/diff/{corrupt_report['bad']}")
    assert response.status_code == 200
    body = response.json()
    assert body["a"] == corrupt_report["good"]
    assert body["b"] == corrupt_report["bad"]


def test_the_list_marks_the_row_corrupt_without_parsing_the_doc(client, corrupt_report) -> None:
    """★P1-2：列表的 `integrity` 由 SQL `json_valid()` 算 —— 与详情语义相同、成本不同。"""
    body = client.get("/api/v1/reports", params={"scope": "all", "limit": 50}).json()
    by_id = {item["report"]["id"]: item for item in body["items"]}
    assert by_id[corrupt_report["bad"]]["integrity"] == "corrupt"
    assert by_id[corrupt_report["good"]]["integrity"] == "ok"
    assert body["total"] == 2, "损坏的行仍然在列表里（列表页不能凭空少一行）"


def test_searching_the_library_with_a_corrupt_row_is_not_500(client, corrupt_report) -> None:
    """★v14 评审（R-1）：`q` 过滤会求值 `doc_json -> $.headline`，而 SQLite 的
    `json_extract()` 在**语法坏掉**的行上抛 `OperationalError: malformed JSON`（实测 3.41.2），
    不是返回 NULL ⇒ 库里只要有一条坏数据，"搜索报告"就整页 500，`total` 一起炸。

    这与 AC-147 是**同一条**不变量（坏一行不得让别人看不了），差别只是多了一个 `q`：
    不带 `q` 时那条表达式根本不会被求值，所以上一个用例是绿的。

    关键词挑的是**只在 headline 里**出现的词（`需求见底`），否则 `OR` 会先命中
    title/symbol/name 而短路，`json_extract()` 不会被求值——那样这条用例就又"绿得没说服力"了。
    """
    response = client.get("/api/v1/reports", params={"scope": "all", "q": "需求见底"})
    assert response.status_code == 200, "一条坏数据不该让整页搜索变成 500"
    body = response.json()
    ids = [item["report"]["id"] for item in body["items"]]
    # 好行仍然搜得到（headline 命中了），坏行只是搜不到——它既没消失、也没把别人带走。
    assert ids == [corrupt_report["good"]]
    assert body["total"] == 1
    # 列表仍要标出那一条坏数据（不带过滤时）。
    unfiltered = client.get("/api/v1/reports", params={"scope": "all", "limit": 50}).json()
    by_id = {item["report"]["id"]: item for item in unfiltered["items"]}
    assert by_id[corrupt_report["bad"]]["integrity"] == "corrupt"


def test_load_doc_safe_never_raises_on_junk() -> None:
    """`load_doc_safe()` 的契约是"永不抛"——连 `None` 与不是对象的 JSON 也走壳。"""

    class Stub:
        id = "a" * 32
        version = 3
        as_of = "2026-11-17"
        generated_at = "2026-11-17T00:00:00Z"
        instrument_id = "CN:600519.SH"
        doc_json = "null"

    doc, integrity = report_service.load_doc_safe(Stub())  # type: ignore[arg-type]
    assert integrity == "corrupt"
    ReportDoc.model_validate(doc)

    Stub.doc_json = "[]"
    doc, integrity = report_service.load_doc_safe(Stub())  # type: ignore[arg-type]
    assert integrity == "corrupt"


def test_internal_marker_keys_are_stripped() -> None:
    """版本比较会往 doc 里塞 `_reportId` / `_model`；它们一旦回传到详情端点就会 500。

    `ReportDoc` 是 `extra="forbid"`，所以剥掉内部键是一条**必须**的纪律，而不是整洁癖。
    """

    class Stub:
        id = "b" * 32
        version = 1
        as_of = "2026-11-17"
        generated_at = "2026-11-17T00:00:00Z"
        instrument_id = "CN:600519.SH"
        doc_json = '{"schema": "aragon.research.report/v1", "_reportId": "x", "_model": "y"}'

    doc, integrity = report_service.load_doc_safe(Stub())  # type: ignore[arg-type]
    assert integrity == "ok"
    assert doc == {"schema": "aragon.research.report/v1"}
