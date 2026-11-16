"""`cite` 档追问：命中 / 未命中 / 引用合法性 / 正文行内引用（AC-25、AC-40）。

`cite` 是纯抽取式的（§18.3.2：零 token、毫秒级、没有生成步骤就没有幻觉路径），
所以绝大多数用例不需要数据库；只有落库那一例要 `session` + `instrument`。

两条被测的"必然失败面"来自 v4 评审：

* AC-25 / R-16 —— 引用 id 必须 ∈ `doc.sources`；编造的 id 只能被丢弃并计数，
  **绝不能**被返回（"看起来有依据"比"没有依据"更糟）；
* AC-40 / V4-P1-3 —— `paragraph` 没有 `sourceRefs`，正文的引用是行内 `[S1]`；
  只取块级 `sourceRefs` 的实现在"问正文"时会永远返回 `grounded=false`。
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.errors import InvalidQuestion
from app.models.ask import ReportAsk
from app.services import ask_service, block_text, report_service

#: 文档里确实写着答案的问题（fundamentals 的 metrics 块 + 正文段落都谈到毛利率）。
QUESTION_ANSWERED = "毛利率下滑的主因是什么？"

#: 文档里没有任何依据的问题。刻意不含「公司」二字：`thesis#5`（业绩说明会引文）
#: 以「公司将继续推进…」开头，出现这种通用词会命中一个无关块（见末尾说明）。
QUESTION_UNANSWERED = "卫星发射计划进展如何？"

#: AC-40 的正文问题——答案只存在于一个只有行内 `[S1]` 的 paragraph 里。
QUESTION_IN_PARAGRAPH = "渠道库存回落到了几个月？"


def _doc(sample: dict) -> dict:
    return copy.deepcopy(sample)


def _paragraph_only_doc(sample: dict) -> dict:
    """把整份报告压成一个「只有正文、没有 sourceRefs」的最小文档（AC-40 前件）。

    只留 `thesis` 一节的一个 paragraph，正文里带行内 `[S1]`；`sources` 只留 S1，
    这样"引用是否合法"不会被别的来源顺手救活。
    """
    doc = _doc(sample)
    doc["sections"] = [
        {
            "id": "thesis",
            "title": "核心逻辑",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "渠道库存已回落至 1.2 个月的合理水位，直营渠道占比提升至 46% [S1]。",
                }
            ],
        }
    ]
    doc["sources"] = [source for source in doc["sources"] if source["id"] == "S1"]
    return doc


def _provisional_doc(sample: dict) -> dict:
    """`provisional` 报告：`sources=[]`，块里仍留着 `[S1]` 这种孤儿标记。"""
    doc = _doc(sample)
    doc["provisional"] = True
    doc["sources"] = []
    return doc


def _legal_ids(doc: dict) -> set[str]:
    return {source["id"] for source in doc.get("sources") or []}


# ── AC-25：正例 ──────────────────────────────────────────────────────────────


def test_cite_grounds_a_question_the_document_actually_answers(sample_report: dict) -> None:
    """AC-25 正例：文档内确有答案 → ≥1 条引用、`grounded=true`、每条引用都合法。"""
    result = ask_service.answer_cite(sample_report, QUESTION_ANSWERED)

    assert result.answer.hits, "文档里明明写着毛利率，`cite` 却没命中任何块"
    assert result.grounded is True
    assert result.answer.citations, "命中块带 sourceRefs，citations 不该为空"

    legal = _legal_ids(sample_report)
    cited = [citation.sourceId for citation in result.answer.citations]
    assert set(cited) <= legal, f"引用了不存在的来源: {set(cited) - legal}"
    assert result.citations_dropped == 0, "全部引用都合法时不该有丢弃计数"

    # 引用形状（§18.5.1）：title/publisher 来自 doc.sources 本身。
    titles = {source["id"]: source["title"] for source in sample_report["sources"]}
    for citation in result.answer.citations:
        assert citation.title == titles[citation.sourceId]

    # `blockId = f"{sectionId}#{index}"`（V4-P1-2）：命中块必须能被反查。
    known_blocks = {block_id for block_id, _, _ in block_text.block_ids(sample_report)}
    for hit in result.answer.hits:
        assert hit.blockId in known_blocks
        assert set(hit.citations) <= legal
        assert hit.excerpt, "命中块必须带回原文片段，否则用户无从核对"


# ── AC-25：反例 ──────────────────────────────────────────────────────────────


def test_cite_never_invents_evidence_when_the_document_is_silent(sample_report: dict) -> None:
    """AC-25 反例：文档内无答案 → `hits=[]`、`grounded=false`、`citations=[]` + hint。"""
    result = ask_service.answer_cite(sample_report, QUESTION_UNANSWERED)

    assert result.answer.hits == [], "卫星发射计划不在报告里，不该有命中块"
    assert result.answer.citations == [], "没有命中块就不许有引用"
    assert result.grounded is False
    assert result.citations_dropped == 0
    assert result.answer.hint, "未命中时必须给出 hint，否则前端只能说『报告未涉及』而没有出路"
    assert result.confidence == 0.0


# ── AC-40：正文（paragraph）里的答案 ─────────────────────────────────────────


def test_cite_reads_inline_markers_from_a_paragraph_block(sample_report: dict) -> None:
    """AC-40（V4-P1-3）：答案写在只有行内 `[S1]` 的 paragraph 里，仍须有依据。"""
    doc = _paragraph_only_doc(sample_report)
    paragraph = doc["sections"][0]["blocks"][0]

    # 前件：这一块确实没有 sourceRefs —— 否则这条用例测不到 V4-P1-3 的修法。
    assert "sourceRefs" not in paragraph
    assert block_text.block_source_ids(paragraph) == ("S1",)

    result = ask_service.answer_cite(doc, QUESTION_IN_PARAGRAPH)

    assert result.answer.hits, "问的正是这一段写着的答案，应该命中"
    assert result.grounded is True, "行内 [S1] 没被算成引用 → 回退成 v3 的假『报告未涉及』"
    assert [citation.sourceId for citation in result.answer.citations] == ["S1"]
    assert result.answer.hits[0].blockId == "thesis#0"
    assert result.answer.hits[0].citations == ["S1"]
    assert result.citations_dropped == 0
    assert result.answer.hint is None


def test_cite_explains_the_provisional_report_instead_of_returning_nothing(sample_report: dict) -> None:
    """AC-40 第二个例子：`sources=[]` 的临时版 → `hits=[]`/`grounded=false` **且** hint 非空。"""
    doc = _provisional_doc(sample_report)
    assert doc["provisional"] is True
    assert doc["sources"] == []

    result = ask_service.answer_cite(doc, QUESTION_ANSWERED)

    assert result.answer.hits == []
    assert result.answer.citations == []
    assert result.grounded is False
    assert result.citations_dropped == 0, "没有合法来源时不该把整份文档的引用都算成『丢弃』"
    assert result.answer.hint, "临时版必须显式说明原因，否则界面上看起来就是坏了"
    assert "来源清单" in result.answer.hint


# ── R-16：引用合法性只认 doc.sources ─────────────────────────────────────────


def test_validate_citations_drops_fabricated_ids_and_counts_them(sample_report: dict) -> None:
    """§18.6.2 / R-16：编造的 id 被丢弃并计数，合法的留下。"""
    kept, dropped = ask_service.validate_citations(sample_report, ["S1", "S99"])

    assert kept == ["S1"]
    assert dropped == 1


def test_validate_citations_rejects_section_ids_and_dedupes(sample_report: dict) -> None:
    """V4-P1-12：合法集合只有 `doc.sources[].id`，章节 id 永不算 citation。"""
    sections = [section["id"] for section in sample_report["sections"]]
    assert sections, "fixture 应该有章节，否则这条用例没有意义"

    kept, dropped = ask_service.validate_citations(sample_report, [sections[0]])
    assert kept == []
    assert dropped == 1

    # 重复的合法 id 只保留一份，且不算丢弃（去重不是错误）。
    assert ask_service.validate_citations(sample_report, ["S2", "S2"]) == (["S2"], 0)


# ── 问题卫生（§18.5.1：1–500 字符、NFKC 后非空、禁控制字符）────────────────


def test_normalize_question_nfkc_normalizes_and_trims() -> None:
    """NFKC + 去首尾空白：全角空格与全角括号都要归一，否则检索词对不上。"""
    assert ask_service.normalize_question("  毛利率（TTM）  ") == "毛利率(TTM)"
    assert ask_service.normalize_question("毛利率\u3000下滑") == "毛利率 下滑"
    assert ask_service.normalize_question("毛利率\t下滑") == "毛利率 下滑"
    assert ask_service.normalize_question("毛利率下滑\n") == "毛利率下滑"
    assert ask_service.normalize_question("毛" * 500) == "毛" * 500


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "空串"),
        ("   ", "只有空白"),
        ("\u3000\u3000", "只有全角空白"),
        ("毛" * 501, "超过 500 字符上限"),
        ("毛利率\x00下滑", "含控制字符 \\x00"),
        ("毛利率\x07下滑", "含控制字符 \\x07"),
        ("毛利率\u200b下滑", "含格式字符 U+200B"),
    ],
)
def test_normalize_question_rejects_bad_input(raw: str, reason: str) -> None:
    """§7.3：坏问题返回 422 `invalid_question`，**绝不是** 500。"""
    with pytest.raises(InvalidQuestion) as excinfo:
        ask_service.normalize_question(raw)
    assert excinfo.value.code == "invalid_question", reason
    assert excinfo.value.status_code == 422


# ── 检索的确定性（§18.3.2：bigram 保证同一问题永远同一结果）───────────────


def test_search_units_is_deterministic(sample_report: dict) -> None:
    """同一问题两次检索必须给出完全相同的 `(blockId, score)` 序列。"""
    first = ask_service.search_units(sample_report, QUESTION_ANSWERED)
    second = ask_service.search_units(sample_report, QUESTION_ANSWERED)

    ranking = [(hit.block_id, hit.score) for hit in first]
    assert ranking == [(hit.block_id, hit.score) for hit in second]
    assert ranking, "确定性断言需要一个非空结果"
    assert ranking == sorted(ranking, key=lambda pair: pair[1], reverse=True), "分数必须降序"
    # 同一问题两次比对的是不同对象，形状一致但没有共享可变状态。
    assert all(0.0 < hit.score <= 1.0 for hit in first), "分数按 top1 归一化到 (0,1]"


def test_build_units_skips_empty_blocks(sample_report: dict) -> None:
    """`divider` 这类没有文字的块不进检索（否则只会稀释 BM25 的平均长度）。"""
    units = ask_service.build_units(sample_report)
    assert units, "fixture 应该有可检索块"
    assert all(unit.text.strip() for unit in units)
    assert all(unit.tokens for unit in units)
    assert "divider" not in {unit.block_type for unit in units}


# ── 落库（需要 DB）：cite 不写 run、不吃饭额度 ─────────────────────────────


def test_ask_cite_persists_one_row_and_never_spends_the_deep_quota(
    session, instrument, sample_report: dict
) -> None:
    """AC-25 的落库面：一行 `report_asks`、`cost_known=1`/`cost=0`、deep 额度不动。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")

    ask = ask_service.ask_cite(session, report, QUESTION_ANSWERED)

    rows = session.execute(select(ReportAsk).where(ReportAsk.report_id == report.id)).scalars().all()
    assert len(rows) == 1, "一次 cite 只允许落一行"
    row = rows[0]
    assert row.mode == "cite"
    assert row.status == "done"
    assert bool(row.cost_known) is True
    assert row.cost_usd == 0.0
    assert row.tokens_in == 0 and row.tokens_out == 0
    # V4-P1-5：ask 不建 `runs` 行。
    assert row.run_id is None

    assert ask.id == row.id
    assert ask_service.read_ask_state(session, ask.id) == "done"

    # §18.7.3 额度：第 1 次 cite 永远不受限，也不会把 deep 的 24h 额度吃掉。
    assert ask_service.daily_count(session, report.id, mode="deep") == 0
    assert ask_service.daily_count(session, report.id, mode="cite") == 1

    # 出参形状（§18.5.1）：引用与命中块随行落库，读回后仍 grounded。
    payload = ask_service.to_ask_out(ask)
    assert payload.mode == "cite"
    assert payload.status == "done"
    assert payload.grounded is True
    assert payload.costKnown is True
    assert payload.answer.hits, "落库后 hits 必须还能读回来（前端靠它滚动定位）"
    assert [citation.sourceId for citation in payload.answer.citations] == [
        source.sourceId for source in ask_service.answer_cite(report.doc, QUESTION_ANSWERED).answer.citations
    ]
