"""AC-41 —— 块 id 单一真源 + 行内引用抽取（§18.5.1 / §18.7.3，被测 `app/services/block_text.py`）。

块 id 只有一条派生规则（V4-P1-2）：``blockId = f"{sectionId}#{index}"``，``index`` 是块在该
section 内的 0 基序号；它不存库、也不由模型输出。前端镜像实现是 `web/lib/blocks.ts::blockIdOf()`，
两侧靠**同一份 golden 文件**锁死——所以本文件同时是那份契约的生成器。

golden 文件（``tests/fixtures/block_ids.golden.json``）的格式，也是 AC-41 的比对契约：

    {"<sectionId>#<index>": "<sha1(block_to_text(block)) 的 hex>", ...}

序列化方式固定为 ``json.dumps(map, ensure_ascii=False, indent=2, sort_keys=True)`` + 末尾一个 ``"\\n"``。
这个文件是**导出物而不是输入**：本文件每次运行都整份重写它（见 `write_golden`）。
``web/tests/blocks.test.ts``（AC-41 的另一半，尚未创建）读同一份文件，比对自己算出来的
``blockIdOf()``/文本哈希；谁改了 `sample_report.json` 或 `block_to_text()`，这里就会先红。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from app.services import block_text

#: AC-41 契约文件名；前端 `web/tests/blocks.test.ts` 按同一路径读它。
GOLDEN_NAME = "block_ids.golden.json"

#: 分隔线投影成空串，于是它的哈希必然是空串的哈希（前端据此知道"不是正文"）。
EMPTY_SHA1 = hashlib.sha1(b"").hexdigest()


def block_text_map(doc: dict) -> dict[str, str]:
    """`{blockId: sha1(block_to_text(block))}` —— AC-41 映射本体（同一份题面给前后端）。"""
    return {
        block_id: hashlib.sha1(block_text.block_to_text(block).encode("utf-8")).hexdigest()
        for block_id, _section_id, block in block_text.block_ids(doc)
    }


def write_golden(doc: dict, path: Path) -> dict[str, str]:
    """把 golden 映射整份重写到 `path`，返回刚写出的映射。

    写而不是读：golden 是从 `sample_report` 派生出的**契约产物**，必须在每次运行时刷新；
    "稳定"由紧随其后的多次派生比对来保证（见 `test_golden_block_id_map_is_written_and_stable`）。
    """
    mapping = block_text_map(doc)
    path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return mapping


def block_at(doc: dict, section_id: str, index: int) -> dict:
    """按（sectionId, 0 基序号）取块——正是块 id 的坐标系。"""
    for section in doc["sections"]:
        if section["id"] == section_id:
            return section["blocks"][index]
    raise AssertionError(f"sample fixture 里没有 section {section_id}")


# ── 块 id 规则与文档顺序 ──────────────────────────────────────────────────────


def test_block_ids_follow_the_one_derivation_rule_in_document_order(sample_report: dict) -> None:
    """AC-41：`block_ids()` 按文档顺序返回 `(blockId, sectionId, block)`，id 只由 `sectionId#index` 决定。"""
    walk = block_text.block_ids(sample_report)

    expected: list[tuple[str, str]] = []
    for section in sample_report["sections"]:
        for index, _block in enumerate(section["blocks"]):
            expected.append((f"{section['id']}#{index}", section["id"]))
    assert [(block_id, section_id) for block_id, section_id, _ in walk] == expected

    # 序号在每个 section 内从 0 重新开始，且带给调用方的就是原块对象（不复制、不重排）。
    for section in sample_report["sections"]:
        ids = [block_id for block_id, section_id, _ in walk if section_id == section["id"]]
        assert ids == [block_text.block_id_for(section["id"], index) for index in range(len(section["blocks"]))]
        assert [block for _, section_id, block in walk if section_id == section["id"]] == section["blocks"]

    assert len(walk) == sum(len(section["blocks"]) for section in sample_report["sections"])
    assert len({block_id for block_id, _, _ in walk}) == len(walk), "块 id 在整份文档内必须唯一"
    assert walk[0][0] == block_text.block_id_for(sample_report["sections"][0]["id"], 0)


def test_block_ids_tolerates_empty_or_missing_sections() -> None:
    """投影层不做假设：没有 sections / blocks 是空列表，不是异常。"""
    assert block_text.block_ids({}) == []
    assert block_text.block_ids({"sections": None}) == []
    assert block_text.block_ids({"sections": [{"id": "thesis", "title": "核心逻辑"}]}) == []
    assert block_text.block_id_for("valuation", 7) == "valuation#7"
    assert block_text.block_id_for("", 0) == "#0"


# ── AC-41 golden 文件 ───────────────────────────────────────────────────────


def test_golden_block_id_map_is_written_and_stable(fixtures_dir: Path, sample_report: dict) -> None:
    """AC-41：golden 映射每次运行重写；同一份文档两次派生必须完全一致（契约文件的格式即此处）。"""
    path = fixtures_dir / GOLDEN_NAME
    first = write_golden(sample_report, path)
    assert path == fixtures_dir / "block_ids.golden.json"

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n"), "golden 文件必须以一个换行结尾（前端按行比对 diff）"
    assert raw == json.dumps(first, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert json.loads(raw) == first, "写出的字节必须能被原样读回同一个映射"

    # 重新派生 + 整份重写：结果逐字节不变（派生只依赖文档内容，不依赖对象身份/顺序）。
    second = write_golden(sample_report, path)
    assert second == first
    assert path.read_text(encoding="utf-8") == raw

    # 派生自另一份等值文档（深拷贝）也必须得到同一张映射。
    assert block_text_map(copy.deepcopy(sample_report)) == first

    expected_ids = [block_id for block_id, _, _ in block_text.block_ids(sample_report)]
    assert set(first) == set(expected_ids)
    assert len(first) == len(expected_ids)
    assert all(re.fullmatch(r"[0-9a-f]{40}", digest) for digest in first.values())
    assert all(block_id.count("#") == 1 and re.fullmatch(r".+#\d+", block_id) for block_id in first)

    # 分隔线投影为空 -> 哈希等于空串哈希；其它块都有可检索的正文。
    divider_ids = [
        block_id for block_id, _, block in block_text.block_ids(sample_report) if block["type"] == "divider"
    ]
    assert divider_ids, "sample fixture 应当含一条 divider 才能锁死这条边界"
    for block_id in divider_ids:
        assert first[block_id] == EMPTY_SHA1


# ── 行内引用抽取（★V4-P1-3） ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[S1]", ["S1"]),
        ("[S1,S2]", ["S1", "S2"]),
        ("见 [S3] 与 [S1]", ["S3", "S1"]),
        ("[S1, S2]", ["S1", "S2"]),  # 逗号前后的空白允许
        ("[S1,S1] 重复出现", ["S1"]),  # 去重且保序
        ("[S10] 不是 [S1]", ["S10", "S1"]),  # 两位数来源 id
    ],
)
def test_source_ids_in_text_reads_inline_markers(text: str, expected: list[str]) -> None:
    """AC-25/AC-40 的底座：行内 `[S1]` / `[S1,S2]` 必须被抽出来，且按出现顺序去重。"""
    assert block_text.source_ids_in_text(text) == expected


@pytest.mark.parametrize("text", ["没有引用", "[s1]", "[S]", "", None, "S1", "[1]", "[S1)", "(S1)"])
def test_source_ids_in_text_never_guesses(text) -> None:
    """反例优先：不模糊匹配、不大小写归一、不接受没有数字的 `[S]`——空列表是可信的事实。"""
    assert block_text.source_ids_in_text(text) == []


def test_source_ids_in_text_agrees_with_the_shared_regex() -> None:
    """`CITATION_RE` 是导出/检索/追问共用的唯一正则，抽取结果必须与它一致（C-9）。"""
    assert block_text.CITATION_RE.search("[S1,S2]").group(1) == "S1,S2"
    assert block_text.CITATION_RE.search("[s1]") is None
    text = "营收 +15.2% [S3]，估值分位 31% [S1,S4]。"
    assert block_text.source_ids_in_text(text) == ["S3", "S1", "S4"]


# ── 结构化 sourceRefs ∪ 行内引用（★V4-P1-3） ────────────────────────────────


def test_paragraph_citation_comes_from_the_inline_markers() -> None:
    """`paragraph` 块没有 `sourceRefs` 字段，正文里的 `[S1]` 是它唯一的接地依据。"""
    block = {"type": "paragraph", "text": "直营占比提升到 46%，毛利率因此受益 [S1]。"}
    assert "sourceRefs" not in block
    assert block_text.block_source_ids(block) == ("S1",)


def test_sample_paragraph_returns_its_inline_ids_in_order(sample_report: dict) -> None:
    """sample 正文先引用 S3 再引用 S1：顺序即阅读顺序，不去重到只剩一个。"""
    paragraph = block_at(sample_report, "thesis", 1)
    assert paragraph["type"] == "paragraph"
    assert block_text.block_source_ids(paragraph) == ("S3", "S1")


def test_bullets_merge_structured_refs_then_inline_refs_deduped() -> None:
    """结构化 `sourceRefs` 先、行内 `[S1]` 后，跨越两半去重且保序（禁止集合化后乱序）。"""
    block = {
        "type": "bullets",
        "items": [
            {"text": "直营占比提升贡献毛利率 [S2]", "sourceRefs": ["S3"]},
            {"text": "批价企稳，只有行内引用 [S1]"},
            {"text": "重复的结构化引用 [S3]", "sourceRefs": ["S2"]},
        ],
    }
    assert block_text.block_source_ids(block) == ("S3", "S2", "S1")


def test_metrics_block_merges_item_refs_with_inline_markers() -> None:
    """`metrics` 块的 `sourceRefs` 挂在每个 item 上，行内标记仍在投影文本里生效。"""
    block = {
        "type": "metrics",
        "items": [
            {"label": "毛利率", "value": "91.3%", "deltaPct": 0.4, "sourceRefs": ["S3"]},
            {"label": "PE(TTM)", "value": "24.1× [S2]"},
            {"label": "PB", "value": "7.2×", "sourceRefs": ["S3"]},
        ],
    }
    assert block_text.block_source_ids(block) == ("S3", "S2")


def test_sample_bullets_and_metrics_refs_are_grounded(sample_report: dict) -> None:
    """sample 里两类块的真实结果：bullets 三条各自的结构化引用，metrics 只来自 item。"""
    assert block_text.block_source_ids(block_at(sample_report, "thesis", 3)) == ("S2", "S4", "S1")
    assert block_text.block_source_ids(block_at(sample_report, "fundamentals", 0)) == ("S3",)


# ── 文本投影不丢内容（检索/导出/追问同源） ──────────────────────────────────


def test_every_sample_block_but_the_divider_projects_to_text(sample_report: dict) -> None:
    """AC-39 的前提：除分隔线外每一个块都必须投影出非空正文，否则检索与追问都会静默漏块。"""
    for block_id, _section_id, block in block_text.block_ids(sample_report):
        text = block_text.block_to_text(block)
        if block["type"] == "divider":
            assert text == "", f"{block_id} 是分隔线，应当投影为空串"
        else:
            assert text.strip(), f"{block_id}（{block['type']}）投影为空"


def test_block_to_text_degrades_instead_of_raising() -> None:
    """残缺块（缺字段/未知类型/非 dict）返回空串，绝不抛异常——导出与检索不能因一块崩掉。"""
    assert block_text.block_to_text({"type": "divider"}) == ""
    assert block_text.block_to_text({"type": "hologram", "text": "未来块"}) == ""
    assert block_text.block_to_text({"type": "paragraph"}) == ""
    assert block_text.block_to_text({}) == ""
    assert block_text.block_to_text(None) == ""  # type: ignore[arg-type]


def test_block_to_text_keeps_the_wording_of_prose_blocks() -> None:
    """散文类块必须原样返回（摘要接口承诺是它的子串，AC-39），不做截断或清洗。"""
    text = "2025 年三季度营收同比 +15.2%，净利率 49.3% [S3]。"
    assert block_text.block_to_text({"type": "paragraph", "text": text}) == text
    assert block_text.block_to_text({"type": "heading", "level": 2, "text": "一、需求见底"}) == "一、需求见底"
    callout = {"type": "callout", "tone": "insight", "title": "关键判断", "text": "结构升级接棒。"}
    assert block_text.block_to_text(callout) == "关键判断 结构升级接棒。"


# ── 合法引用集合（V4-P1-12） ────────────────────────────────────────────────


def test_legal_source_ids_is_exactly_the_document_sources(sample_report: dict) -> None:
    """`legal_source_ids` == 文档 `sources[].id` 全集，且**永不**包含 section id。"""
    legal = block_text.legal_source_ids(sample_report)
    assert legal == {source["id"] for source in sample_report["sources"]}
    assert not (legal & {section["id"] for section in sample_report["sections"]})

    cited = {
        source_id
        for _block_id, _section_id, block in block_text.block_ids(sample_report)
        for source_id in block_text.block_source_ids(block)
    }
    assert cited <= legal, f"正文引用了不存在的来源：{sorted(cited - legal)}"


def test_legal_source_ids_collapses_when_the_document_has_no_sources() -> None:
    """临时版报告（`sources=[]`）的合法引用集必须为空，哪怕正文里还留着 `[S1]`。"""
    doc = {
        "sections": [
            {"id": "thesis", "title": "核心逻辑", "blocks": [{"type": "paragraph", "text": "仍写着 [S1] 的正文"}]}
        ]
    }
    assert block_text.legal_source_ids(doc) == set()
    assert block_text.source_index(doc) == {}
    assert block_text.block_source_ids(doc["sections"][0]["blocks"][0]) == ("S1",)


# ── 检索单元 ────────────────────────────────────────────────────────────────


def block_at_block_id(doc: dict, block_id: str) -> dict:
    """按派生 id 取块——供下面的等值断言使用。"""
    for candidate_id, _section_id, block in block_text.block_ids(doc):
        if candidate_id == block_id:
            return block
    raise AssertionError(f"派生 id {block_id} 不在文档里")


def test_doc_units_skip_blocks_that_project_to_nothing(sample_report: dict) -> None:
    """检索单元与块 id 同源：跳过空投影块，并带出 section 标题与合法引用集。"""
    walk = block_text.block_ids(sample_report)
    expected = [block_id for block_id, _, block in walk if block_text.block_to_text(block).strip()]

    units = block_text.doc_units(sample_report)
    assert [unit["block_id"] for unit in units] == expected
    assert len(units) == len(walk) - 1, "sample 里只有一条 divider 会被跳过"

    titles = block_text.section_titles(sample_report)
    for unit in units:
        assert unit["section_title"] == titles[unit["section_id"]]
        assert unit["section_title"]
        assert unit["source_ids"] == block_text.block_source_ids(block_at_block_id(sample_report, unit["block_id"]))
