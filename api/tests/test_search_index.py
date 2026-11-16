"""索引层契约：中文 bigram 切分、AC-33 同事务可见、AC-39 块级索引与「没有 snippet()」。

每条断言都对准「实现退回去就会变红」的那个动作：

* 切分退回 `unicode61`（中文整段一个 token）→ `tokenize_zh_bigram` 直接失败；
* 索引退回「整篇一行」或改成后台重建 → AC-33 的同 session 立即可见失败；
* 索引粒度不是块 → 「块数 == 索引行数」与 `section_id`/`block_id` 非空断言失败；
* 摘要退回 SQLite 的 `snippet()` → AC-39 的源码守卫失败（§18.3.4 v4 修订）。
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text

from app.services import block_text, report_service, search_index

#: 源码守卫的扫描根：`api/app`（测试以 cwd=api 运行，但仍用 __file__ 兜底）。
APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: 真正要禁掉的是 **SQL 函数** `snippet(`。服务层自己那个 `_snippet(` 是 v4 指定的
#: 唯一生产者（§18.3.4），必须放行；文档串里用反引号写明「snippet() 不再使用」的那两处
#: 也要放行——它们正是这条负向断言的说明书。
_SQL_SNIPPET_CALL = re.compile(r"(?<![A-Za-z0-9_])snippet\(")
_DOC_MENTION = re.compile(r"`snippet\(\)`")


# ── 切分：写入与查询共用同一份规则 ───────────────────────────────────────


def test_tokenize_splits_a_chinese_run_into_overlapping_bigrams() -> None:
    """AC-33 / §18.3.4：`毛利率下滑` → `毛利/利率/率下/下滑`，顺序固定。"""
    assert search_index.tokenize_zh_bigram("毛利率下滑") == ["毛利", "利率", "率下", "下滑"]


def test_tokenize_keeps_a_one_character_run_as_one_token() -> None:
    """单字不切（否则查询侧与索引侧会对不上）。"""
    assert search_index.tokenize_zh_bigram("率") == ["率"]
    assert search_index.tokenize_zh_bigram("毛利率 A") == ["毛利", "利率", "a"]


def test_tokenize_keeps_latin_and_digit_runs_whole() -> None:
    """`62.1%` 必须是一个整 token —— 拆成数字碎片就再也查不回来了。"""
    assert search_index.tokenize_zh_bigram("62.1%") == ["62.1%"]
    assert search_index.tokenize_zh_bigram("PE(TTM)") == ["pe", "ttm"]


def test_tokenize_returns_empty_for_an_empty_string() -> None:
    assert search_index.tokenize_zh_bigram("") == []
    assert search_index.tokenize_zh_bigram("   ") == []


def test_query_terms_drops_stopwords() -> None:
    """§18.3.4：停用词只在查询侧剔除，`什么` 不参与匹配。"""
    terms = search_index.query_terms("毛利率为什么下滑？")
    assert "什么" not in terms
    assert "毛利" in terms
    assert "下滑" in terms
    assert not (set(terms) & search_index.STOPWORDS)


def test_query_terms_never_returns_empty_for_a_non_empty_question() -> None:
    """整句都是停用词时宁可给一个弱查询，也不能给空表达式（空 MATCH 会炸）。"""
    assert search_index.query_terms("为什么") != []
    assert search_index.query_terms("的") != []
    assert search_index.query_terms("") == []


def test_match_expression_quotes_every_term() -> None:
    """表头里的代码断言：`62.1%` 未加引号会被当成 FTS5 语法，直接 500。"""
    expression = search_index.match_expression(["62.1%", "毛利"])
    assert expression == '"62.1%" OR "毛利"'
    assert search_index.match_expression([]) == ""


# ── 虚表存在性：fixture 造得出索引，AC-33 才不是恒假 ─────────────────────


def test_fts_tables_exist_after_the_fixture_built_the_schema(session) -> None:
    """★V4-P1-7：`create_all` 建不出虚表，所以 conftest 之后调了 `ensure_fts_schema`。"""
    assert search_index.fts_ready(session) is True
    assert search_index.detect_fts(session) is True


def test_ensure_fts_schema_is_idempotent(session) -> None:
    """迁移 `0002` 与 conftest 共用同一份 DDL：重复调用不得抛错。"""
    assert search_index.ensure_fts_schema(session) is True
    assert search_index.ensure_fts_schema(session) is True
    assert search_index.fts_ready(session) is True


# ── AC-33：索引写入与报告落库同事务 ─────────────────────────────────────


def test_created_report_is_searchable_in_the_same_session(session, instrument, sample_report) -> None:
    """AC-33：`create_version` 返回后**立刻**能搜到，不需要任何后台重建。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")

    rows = search_index.search_report_blocks(session, "毛利")

    assert rows, "生成完成即刻应可搜到「毛利」（同事务写入，O-4）"
    assert report.id in {row["report_id"] for row in rows}
    assert all(row["block_id"] and row["section_id"] for row in rows)


def test_soft_deleted_report_disappears_from_the_index_immediately(
    session, instrument, sample_report
) -> None:
    """AC-33：软删后立刻搜不到（索引行在同一事务里被删）。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")
    assert report.id in {row["report_id"] for row in search_index.search_report_blocks(session, "毛利")}

    report_service.soft_delete(session, report)

    remaining = search_index.search_report_blocks(session, "毛利")
    assert report.id not in {row["report_id"] for row in remaining}
    assert remaining == []


# ── AC-39：索引粒度是块，一块一行，且带 section/block id ─────────────────


def test_index_granularity_is_one_row_per_non_empty_block(session, instrument, sample_report) -> None:
    """AC-39：行数 == 非空文本块数；每行都带可反查的 section_id / block_id。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")

    expected = [
        (block_id, section_id)
        for block_id, section_id, block in block_text.block_ids(sample_report)
        if block_text.block_to_text(block).strip()
    ]
    assert expected, "夹具报告必须有可索引的块"

    written = search_index.reindex_report(session, report)

    assert written == len(expected)
    stored = session.execute(
        text("SELECT section_id, block_id FROM report_fts WHERE report_id=:rid"), {"rid": report.id}
    ).fetchall()
    assert len(stored) == len(expected), "一块一行，既不能整篇一行也不能重复"
    assert {(row[1], row[0]) for row in stored} == {(block_id, section_id) for block_id, section_id in expected}

    sections = session.execute(
        text("SELECT DISTINCT section_id FROM report_fts WHERE report_id=:rid"), {"rid": report.id}
    ).fetchall()
    assert sections and all(row[0] for row in sections), "sectionId 必须能深链（v3 缺这一列）"

    blocks = session.execute(
        text("SELECT DISTINCT block_id FROM report_fts WHERE report_id=:rid"), {"rid": report.id}
    ).fetchall()
    assert all(row[0] for row in blocks)
    assert len(blocks) == len(expected)


def test_hits_carry_the_section_and_block_they_came_from(session, instrument, sample_report) -> None:
    """AC-39：命中必须定位到块（`回撤` 只出现在 thesis 段落里）。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")

    rows = search_index.search_report_blocks(session, "回撤")

    assert rows
    assert {(row["section_id"], row["block_id"]) for row in rows} == {("thesis", "thesis#1")}
    assert {row["report_id"] for row in rows} == {report.id}


def test_reindex_report_payload_backfills_exactly_like_the_migration(
    session, instrument, sample_report
) -> None:
    """AC-33：迁移 `0002` 的回填走同一个投影（不能另写一份 SQL 拼装）。"""
    report = report_service.create_version(session, instrument, sample_report, status="ready")
    expected_rows = search_index.reindex_report(session, report)

    search_index.delete_report_index(session, report.id)
    assert search_index.search_report_blocks(session, "毛利") == []

    written = search_index.reindex_report_payload(session, report.id, report.doc_json, report.title)

    assert written == expected_rows
    assert report.id in {row["report_id"] for row in search_index.search_report_blocks(session, "毛利")}


# ── 标的索引 ─────────────────────────────────────────────────────────────


def test_reindex_instrument_makes_it_findable_by_symbol_fragment(session, instrument) -> None:
    """§18.3.4：`instrument_fts` 里存 symbol / name / pinyin，代码片段要能查回来。

    FTS 只按 token 匹配，所以 `600519` / `600519.SH` 命中而 `6005` 不命中——
    更短的前缀由 `symbol_resolver.search_local` 的 `startswith` 评分负责，
    两条路径在 `search_service` 里合并（见 test_search.py）。
    """
    assert search_index.reindex_instrument(session, instrument) is True

    assert search_index.search_instruments(session, "600519") == [instrument.id]
    assert search_index.search_instruments(session, "600519.SH") == [instrument.id]
    assert search_index.search_instruments(session, "000858") == []
    # 更短的前缀不是 FTS 的活（token 完整性），交给 symbol_resolver。
    assert search_index.search_instruments(session, "6005") == []


def test_reindex_instrument_makes_it_findable_by_name_bigram(session, instrument) -> None:
    """The name dimension only works when both sides segment the same way.

    `query_terms()` asks for Chinese **bigrams**; writing the name verbatim left it as
    one `unicode61` token, so `茅台` could never match `贵州茅台` — the whole name
    dimension was silently dead while the symbol dimension kept the test green.
    """
    assert search_index.reindex_instrument(session, instrument) is True

    assert search_index.search_instruments(session, "茅台") == [instrument.id]
    assert search_index.search_instruments(session, "贵州茅台") == [instrument.id]
    assert search_index.search_instruments(session, "五粮液") == []


def test_pinyin_text_matches_the_documented_projection() -> None:
    """§18.3.4：`贵州茅台` → `guizhoumaotai gzmt`；缺 pypinyin 时写空而不是抛错。"""
    if search_index.pinyin_available():
        assert search_index.pinyin_text("贵州茅台") == "guizhoumaotai gzmt"
    else:
        assert search_index.pinyin_text("贵州茅台") == ""
    assert search_index.pinyin_text("") == ""


# ── AC-39 负向断言：SQL 的 snippet() 不许回来 ────────────────────────────


def test_sql_snippet_function_is_absent_from_the_app_source() -> None:
    """AC-39：`api/app` 里不得出现 SQL 函数 `snippet(`。

    bigram 索引上的 `snippet()` 只能返回碎片（v4 实测 E-2），摘要改由
    `search_service._snippet` 从块原文切片。这里只放行两类已知的良性出现：
    服务层自己的 `_snippet(`（前瞻避免误报）与反引号写成的文档串 `` `snippet()` ``。
    """
    offenders: list[str] = []
    scanned = 0
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned += 1
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not _SQL_SNIPPET_CALL.search(line):
                continue
            if _DOC_MENTION.search(line):
                continue
            offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{number}: {line.strip()}")

    # 防止「扫了个空目录」这种假绿：守卫必须真的走完了 api/app。
    assert scanned > 20, f"扫描只覆盖了 {scanned} 个文件，守卫形同虚设"
    assert offenders == [], "SQL 的 snippet() 不得出现在 api/app 里：\n" + "\n".join(offenders)
