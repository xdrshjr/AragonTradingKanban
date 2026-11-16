"""HTTP 层契约：AC-32 四类查询命中同一标的、AC-39 返回形状与「摘要即原文子串」、
以及 FTS 不可用时的降级不 500（§18.7.3 的「无 FTS5」失败用例）。

三条反例优先的断言（§18.7.3）：

* 摘要若退回 SQLite `snippet()` → 去标签后不再出现在块原文里（`in` 断言失败），
  并且会出现 `毛利 利率 率下` 这类 bigram 碎片（正则失败）；
* `blockId` 若不是从 `block_ids(doc)` 推出来的 → 反查失败；
* FTS 关闭时若没有兜底 → `/search` 500 而不是 200 + `degraded=true`。
"""

from __future__ import annotations

import re

import pytest

from app.config import get_settings
from app.services import block_text, report_service, search_index

#: bigram 碎片的形状：连续三段以上「两字 + 空格」的中文（`毛利 利率 率下 下滑`）。
#: 正常的中文句子与表格单元格不会出现这种连续空格切分。
BIGRAM_DEBRIS = re.compile(r"[\u4e00-\u9fff]{2}(?: [\u4e00-\u9fff]{2}){2,}")

TARGET_SYMBOL = "600519.SH"


def _prepare(session, instrument, sample_report):
    """落一版报告并把标的写进 `instrument_fts`。

    两件事都要做：报告行给 `reports[]`，标的行给 `instruments[]`；都缺一不可。
    """
    report = report_service.create_version(session, instrument, sample_report, status="ready")
    assert search_index.reindex_instrument(session, instrument) is True
    session.commit()
    return report


def _walk(doc: dict) -> dict[str, tuple[str, dict]]:
    """`{blockId: (sectionId, block)}` —— AC-39 用来反查 `blockId` 的唯一依据。"""
    return {block_id: (section_id, block) for block_id, section_id, block in block_text.block_ids(doc)}


def _reaches(body: dict, symbol: str) -> bool:
    """该响应是否抵达了这个标的：左栏命中，或报告命中里的 `symbol`。

    正文命中的标的只能从 `reports[].symbol` 看出来——`instruments[]` 由
    `symbol_resolver`（代码 / 名称 / 别名）加 `instrument_fts`（代码 / 拼音）供给，
    正文块不参与标的打分。
    """
    return symbol in {hit["symbol"] for hit in body["instruments"]} | {
        hit["symbol"] for hit in body["reports"]
    }


def _stripped(snippet: str) -> str:
    return snippet.replace("<mark>", "").replace("</mark>", "")


# ── AC-32：四类查询命中同一标的 ─────────────────────────────────────────


def test_search_reaches_the_same_instrument_from_a_chinese_body_fragment(
    client, session, instrument, sample_report
) -> None:
    """AC-32（中文二字）：`毛利` 命中正文块，并经 `reports[].symbol` 回到同一标的。"""
    _prepare(session, instrument, sample_report)

    response = client.get("/api/v1/search", params={"q": "毛利"})

    assert response.status_code == 200
    body = response.json()
    assert body["reports"], "中文二字片段必须命中报告块（bigram 索引）"
    assert _reaches(body, TARGET_SYMBOL)
    assert {hit["symbol"] for hit in body["reports"]} == {TARGET_SYMBOL}
    assert body["degraded"] is False


def test_search_finds_the_instrument_by_code(client, session, instrument, sample_report) -> None:
    """AC-32（代码）：`600519` 在左栏命中，且 `existing=true`（本地已有报告）。"""
    _prepare(session, instrument, sample_report)

    body = client.get("/api/v1/search", params={"q": "600519"}).json()

    hits = [hit for hit in body["instruments"] if hit["symbol"] == TARGET_SYMBOL]
    assert hits, body["instruments"]
    assert hits[0]["existing"] is True
    assert hits[0]["name"] == "贵州茅台"


def test_search_finds_the_instrument_by_name(client, session, instrument, sample_report) -> None:
    """AC-32（名称）：`茅台` 命中标的名称（走 symbol_resolver 的名称评分）。"""
    _prepare(session, instrument, sample_report)

    body = client.get("/api/v1/search", params={"q": "茅台"}).json()

    assert TARGET_SYMBOL in {hit["symbol"] for hit in body["instruments"]}


@pytest.mark.skipif(
    not search_index.pinyin_available(),
    reason="pypinyin 未安装：拼音维度自动关闭（AC-32 的降级路径），用下面的 capabilities 断言覆盖",
)
def test_search_finds_the_instrument_by_pinyin_initials(client, session, instrument, sample_report) -> None:
    """AC-32（拼音）：`gzmt` → `贵州茅台`（全拼 + 首字母写进 `instrument_fts.pinyin`）。"""
    assert search_index.pinyin_text("贵州茅台") == "guizhoumaotai gzmt"
    _prepare(session, instrument, sample_report)

    body = client.get("/api/v1/search", params={"q": "gzmt"}).json()

    assert TARGET_SYMBOL in {hit["symbol"] for hit in body["instruments"]}


def test_missing_pypinyin_turns_pinyin_search_off_without_an_error(client, monkeypatch) -> None:
    """AC-32 降级：拼音缺失时 `features.pinyinSearch=false`，其余三维照常、不报错。"""
    monkeypatch.setattr(search_index, "pinyin_available", lambda: False)

    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["features"]["pinyinSearch"] is False

    # 拼音查询不能变成 4xx/5xx：它只是一个关掉的维度。
    response = client.get("/api/v1/search", params={"q": "gzmt"})
    assert response.status_code == 200
    assert response.json()["degraded"] is False


# ── AC-39：reports[] 的形状与「摘要即原文子串」 ─────────────────────────


@pytest.mark.parametrize(
    ("query", "block_type"),
    [
        ("回撤", "paragraph"),  # 只出现在 thesis 的段落里
        ("2025Q2", "table"),  # 只出现在基本面表格的一行里
    ],
)
def test_report_hits_carry_block_ids_and_a_verbatim_snippet(
    client, session, instrument, sample_report, query: str, block_type: str
) -> None:
    """AC-39：每条命中都有可反查的 `sectionId`/`blockId`，摘要去标签后是块原文子串。"""
    _prepare(session, instrument, sample_report)

    body = client.get("/api/v1/search", params={"q": query}).json()

    assert body["reports"], f"`{query}` 应命中 {block_type} 块"
    walk = _walk(sample_report)
    titles = block_text.section_titles(sample_report)
    hit_types: set[str] = set()
    for hit in body["reports"]:
        assert hit["sectionId"], "v4 的核心修复：sectionId 必须带出来（v3 缺这一列）"
        assert hit["blockId"], "v4 的核心修复：blockId 必须带出来（供点击定位）"
        assert hit["blockId"] in walk, f"blockId 必须能被 block_ids(doc) 反查：{hit['blockId']}"

        section_id, block = walk[hit["blockId"]]
        assert hit["sectionId"] == section_id
        assert hit["sectionTitle"] == titles[section_id]
        hit_types.add(str(block.get("type")))

        stripped = _stripped(hit["snippet"])
        compact = " ".join(block_text.block_to_text(block).split())
        assert "<mark>" in hit["snippet"] and "</mark>" in hit["snippet"]
        assert len(stripped) >= 12, f"摘要过短：{stripped!r}"
        assert stripped in compact, f"摘要必须是块原文子串：{stripped!r} 不在 {compact!r} 里"
        assert not BIGRAM_DEBRIS.search(stripped), f"摘要含 bigram 拼接痕迹：{stripped!r}"

    assert block_type in hit_types


# ── 降级路径与空查询：永远 200 ──────────────────────────────────────────


def test_search_degrades_to_like_when_fts_is_disabled_by_settings(
    client, session, instrument, sample_report, monkeypatch
) -> None:
    """§18.7.3「无 FTS5」：`search_fts=False` → 200 + `degraded=true`，不 500。"""
    _prepare(session, instrument, sample_report)
    monkeypatch.setattr(get_settings(), "search_fts", False, raising=False)

    response = client.get("/api/v1/search", params={"q": "毛利"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["reports"], "LIKE 兜底也要能命中（走原文字符串匹配）"
    assert {hit["symbol"] for hit in body["reports"]} == {TARGET_SYMBOL}


def test_search_degrades_when_the_fts_tables_are_missing(
    client, session, instrument, sample_report, monkeypatch
) -> None:
    """§18.7.3「无 FTS5」的另一半：探测为 False（虚表没建出来）也必须 200。"""
    _prepare(session, instrument, sample_report)
    monkeypatch.setattr(search_index, "fts_ready", lambda target=None: False)

    response = client.get("/api/v1/search", params={"q": "毛利"})

    assert response.status_code == 200
    assert response.json()["degraded"] is True


def test_search_with_an_empty_query_returns_200(client, session, instrument, sample_report) -> None:
    """空查询是「浏览」而不是错误：200、无报告命中、不抛 422。"""
    _prepare(session, instrument, sample_report)

    assert client.get("/api/v1/search", params={"q": ""}).status_code == 200
    body = client.get("/api/v1/search").json()
    assert body["query"] == ""
    assert body["reports"] == []
    assert body["degraded"] is False


def test_search_rejects_an_over_long_query(client) -> None:
    """§18.5.3 的 `max_length=200`：超长查询是 4xx，不是 500。"""
    response = client.get("/api/v1/search", params={"q": "毛" * 201})
    assert response.status_code in (400, 422)
