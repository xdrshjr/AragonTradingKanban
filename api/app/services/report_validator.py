"""Report document validation and repair (spec §6.2, §8.3, §11.3).

Three jobs, in this order:

1. **Structure** — validate against the same JSON Schema the CLI used, then
   repair what can be repaired (missing sections, missing metrics, unknown
   blocks, out-of-range numbers). Repair is reported, not hidden.
2. **Grounding** — every number in the document must appear in the snapshot.
   The ratio decides `degraded` vs `ready` (threshold 0.9, §16 verdict 4: mark,
   never discard — the user must always have something to read).
3. **Quality** — assemble the `quality` record the API stores and the UI shows.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from ..config import get_settings
from ..logging import get_logger
from ..schemas.report import BLOCK_TYPES, SECTION_IDS, ReportDoc
from .numeric_claims import (
    GroundingReport,
    canonical_number,
    grounding_report,
    numbers_in_text,
    snapshot_number_corpus,
)

logger = get_logger("report_validator")

GROUNDED_THRESHOLD = 0.9

#: ★v6（V6-P0-3）：`NUMBER_RE` / `IDENTIFIER_RE` / `DATE_LIKE_RE` **只**定义在
#: `services/numeric_claims.py`，`CITATION_RE` **只**定义在 `services/block_text.py`；
#: 本模块过去各写一份（I-33），现在连名字都不再绑定——没有第二份可漂移的真源。
#: `_numbers_in_text` 保留为薄别名，因为它是既有测试的公开契约
#: （`tests/test_report_validator.py` 直接调用）。
_numbers_in_text = numbers_in_text
_canonical_number = canonical_number
_snapshot_number_corpus = snapshot_number_corpus

SECTION_TITLES = {
    "thesis": "核心逻辑",
    "fundamentals": "基本面",
    "valuation": "估值",
    "risks": "风险",
    "catalysts": "催化剂与跟踪项",
}

MAX_PROVISIONAL_TEXT = 4000

#: ★v12（S3 / §22.3.4 / C-37）：格式收敛记录的**前缀**。`repairs` 里带这个前缀的条目
#: 就是一次 clamp，`ValidationOutcome.quality.clamp` 与 `run.log{code:"clamp"}` 都从
#: 这里派生——**一份记录，两处消费**，不写第二个真源。
CLAMP_PREFIX = "收敛"

#: ★v12（S3 / §22.3.4 / C-37）：`block.text` 的上限**按块型分派**。
#: schema 里 `sections[].blocks[]` 是 `/$defs/block` 的 `anyOf`（12 个块型），
#: `text` 的上限因块型而异（实测 E-105：paragraph 4000 / heading **120** /
#: callout 1200 / quote 800）。所以「一刀切 4000」会让一个 400 字的 heading 仍然
#: 整份判死（正是 E-93 那一类）。
#:
#: 这张表是**文档与断言的对象**，不是运行时真源——`clamp_doc()` 直接遍历 schema 自己
#: 取上界（见 `_clamp_node`），所以 schema 改了它也不会漂移；
#: `tests/test_clamp.py::test_block_text_limits_match_the_schema` 反向盯住这张表。
BLOCK_TEXT_LIMITS: dict[str, int] = {
    "paragraph": 4000,
    "heading": 120,
    "callout": 1200,
    "quote": 800,
}

#: ★v12（S3）：句末标点，按「优先级从高到低」切句。截断**只能**落在这些位置之后，
#: 否则「-1,509.76」会变成「-1.」，亲手制造一个未接地 claim（R-56）。
_SENTENCE_END = ("。", "！", "？", "!", "?", "\n")
_CLAUSE_END = ("；", ";", "，", ",")

_schema_cache: dict[str, Any] = {}


def load_schema() -> dict[str, Any]:
    """`schemas_dir/report_doc.schema.json`（带进程级缓存）。

    ★v7（V8-P0-1 / 裁决 H-18）：这份缓存**不再被当成唯一真源**——真正会丢掉付费
    报告的时间窗是"生成过程中的那两分钟"，期间任何一次 schema 改动都会让"CLI 收下的
    那份"与"Python 校验的那份"不同。所以 `validate_doc(..., schema_path=…)` 优先用
    **当次交给 CLI 的那份**（构造性正确），这个函数只作为向后兼容的默认值。
    """
    if "schema" not in _schema_cache:
        path: Path = get_settings().schemas_dir / "report_doc.schema.json"
        _schema_cache["schema"] = json.loads(path.read_text(encoding="utf-8"))
        _schema_cache["bytes"] = path.stat().st_size
    return _schema_cache["schema"]


def schema_bytes() -> int:
    load_schema()
    return int(_schema_cache.get("bytes", 0))


def schema_sha1() -> str | None:
    """真源 schema 的 12 位 sha1（`quality.engine.schemaSha1` 的默认值）。

    ★v8 评审：文件不在时返回 `None` 而不是抛——`/capabilities` 是诊断端点，
    一个坏掉的 schema 恰恰是最需要它**能打开**的时刻。
    """
    path: Path = get_settings().schemas_dir / "report_doc.schema.json"
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except OSError:  # pragma: no cover - 只有真源被删才会走到
        return None


#: `<!-- version: 8 -->` —— 提示词版本标记（AC-81 读它，也在页面上展示）。
#:
#: ★v14（P0-3）：版本标记的唯一宿主是**两个 system 提示词**（`research_system.md` /
#: `ask_system.md`，两者必须一致）。`research_task.md.j2` **没有也不需要**标记——
#: 不要给它加一个（`test_prompt_discipline` 只认那两个 system 文件）。
#: 7 → 8 的语义是“提示词契约升了一版（新增 base_digest 段与它的硬指令）”，不是“正文改了”。
PROMPT_VERSION_RE = re.compile(r"<!--\s*version:\s*(\d+)\s*-->")


def prompt_quality(
    path: Path,
    *,
    base_report_id: str | None = None,
    base_version: int | None = None,
) -> dict[str, Any] | None:
    """`quality.prompt = {version, sha1, bytes, baseReportId, baseVersion}`；文件不存在 → `None`。

    ★v14（★P1-11）：两个新键**恒在**（值可为 `None`），且**生产者就是这里**。只给响应
    模型加字段等于加两个永远为 `None` 的空键——那正是 `cost` / `prompt` 丢了整整两个版本
    的那个坑（V10-P2-7 / R-77）。四个键“都有生产者”由
    `tests/test_prompt_quality_keys.py` 跑一次真实调用来守。

    ★v7（裁决 H-5）：提示词指纹**不进**引擎指纹。指纹是可比性的输入，多一个输入项
    会让**所有历史报告**变成"引擎不同"，于是版本比较永远带一条琥珀横幅——那恰好违反
    G4 的初衷（只提示真正的差异）。`quality.prompt` 只做展示与追溯。
    """
    try:
        raw = path.read_bytes()
    except OSError:  # pragma: no cover - 工作区被删才会走到
        return None
    match = PROMPT_VERSION_RE.search(raw.decode("utf-8", errors="replace"))
    return {
        "version": int(match.group(1)) if match else None,
        "sha1": hashlib.sha1(raw).hexdigest()[:12],
        "bytes": len(raw),
        # ★v14：两个键恒在，值可为 None —— “这一版是基于哪一版更新的”的唯一来源。
        "baseReportId": base_report_id,
        "baseVersion": base_version,
    }


@dataclass(slots=True)
class ValidationOutcome:
    doc: dict[str, Any]
    grounded_ratio: float = 1.0
    missing_sections: list[str] = field(default_factory=list)
    pruned_blocks: int = 0
    ungrounded_places: int = 0
    repairs: list[str] = field(default_factory=list)
    status: str = "ready"
    errors: list[str] = field(default_factory=list)
    #: Who produced the document. A field, not a constant, because the caller is
    #: the only thing that knows whether this came from the model or the mock.
    source: str = "aragon"
    #: ★v6 G1: the structured screening report behind `groundedRatio`. Optional in
    #: the type only because old reports have no such record (no backfill, §19.6.3).
    grounding: GroundingReport | None = None
    #: ★v6 G4: `quality.engine` — `{fingerprint, cliVersion, allowTools, schemaBytes}`.
    #: Model/provider are deliberately **not** here (authority: `reports.model|provider`).
    engine: dict[str, Any] | None = None
    #: ★v7 H4: `quality.cost` — `{source, reportedUsd, estimatedUsd, priceVersion}`。
    #: 老报告没有这个键 → 界面按缺失渲染（"早期版本"），**不回填**（§19.6.3）。
    cost: dict[str, Any] | None = None
    #: ★v7 H5: `quality.prompt` — `{version, sha1, bytes}`。**不进**引擎指纹（裁决 H-5）。
    prompt: dict[str, Any] | None = None
    #: ★v9 H5 / K5: `quality.salvage` —— 抢救记录。`None` = 没走抢救（老报告也是 `None`，
    #: 读路径按"可缺失"处理，**不回填**，沿用 §19.6.3 口径）。
    salvage: SalvageReport | None = None

    @property
    def quality(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "groundedRatio": round(self.grounded_ratio, 4),
            "missingSections": self.missing_sections,
            "prunedBlocks": self.pruned_blocks,
            "ungroundedCount": self.ungrounded_places,
            "source": self.source,
        }
        # `grounding` only when we actually computed it: an absent key is what tells
        # the UI "don't render the badge", which is the降级永不上抛 rule (§19.0).
        if self.grounding is not None:
            payload["grounding"] = self.grounding.as_payload()
        if self.engine is not None:
            payload["engine"] = self.engine
        if self.cost is not None:
            payload["cost"] = self.cost
        if self.prompt is not None:
            payload["prompt"] = self.prompt
        if self.salvage is not None:
            payload["salvage"] = self.salvage.as_payload()
        # ★v12（S3 / C-48）：`clamp` 与 `prunedBlocks` 同类——**派生**，不新增构造参数。
        # 只在实际收敛过时出现：老报告没有这个键（§22.6.3 的"不回填"），前端按
        # `(q.clamp ?? {count: 0})` 读，于是"没有这个键"不会渲染成"收敛了 0 处"的矛盾。
        clamp = clamp_payload(self.repairs)
        if clamp["count"]:
            payload["clamp"] = clamp
        return payload

    @property
    def ok(self) -> bool:
        return not self.errors


def _iter_block_text(block: dict[str, Any]) -> Iterable[str]:
    """★v6：**只剩散文**。

    分类搬到 `numeric_claims` 之后，这个函数不再决定"哪些数字要核"——它只回答
    "这个块的散文在哪"，`metrics` / `comparables` / `scenario` 的分支已移除（它们的
    数字由路径判据接管，见 §19.3.2 的分类表）。保留它是因为 `quote` / `timeline`
    这类块没有结构化数字字段，散文就是全部。
    """
    kind = block.get("type")
    if kind in ("paragraph", "heading"):
        yield block.get("text") or ""
    elif kind == "bullets":
        for item in block.get("items") or []:
            yield item.get("text") or ""
    elif kind == "callout":
        yield block.get("title") or ""
        yield block.get("text") or ""
    elif kind == "quote":
        yield block.get("text") or ""
    elif kind == "timeline":
        for event in block.get("events") or []:
            yield event.get("title") or ""
            yield event.get("detail") or ""


def compute_grounding(doc: dict[str, Any], snapshot: dict[str, Any]) -> tuple[float, int]:
    """Fraction of prose numerals that can be traced back into the snapshot.

    薄封装：真正的判定只有一个实现（`numeric_claims.grounding_report`），返回值签名
    保持不变以便既有调用者与测试不回归。第二个返回值是 `fact_missing`，与
    `quality.ungroundedCount` 必须是同一个数（AC-61）。
    """
    report = grounding_report(doc, snapshot)
    return report.ratio, report.fact_missing


def prune_unknown_blocks(doc: dict[str, Any]) -> int:
    """Drop blocks whose `type` is not one of the twelve known kinds."""
    pruned = 0
    for section in doc.get("sections") or []:
        blocks = section.get("blocks") or []
        kept = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") in BLOCK_TYPES:
                kept.append(block)
            else:
                pruned += 1
        section["blocks"] = kept
    return pruned


def ensure_sections(doc: dict[str, Any]) -> list[str]:
    """Guarantee the five canonical sections exist, in order (§5.4 prompt rule 3)."""
    sections = doc.get("sections")
    if not isinstance(sections, list):
        sections = []
    by_id = {section.get("id"): section for section in sections if isinstance(section, dict)}
    ordered: list[dict[str, Any]] = []
    missing: list[str] = []
    for section_id in SECTION_IDS:
        existing = by_id.get(section_id)
        if existing is None:
            missing.append(section_id)
            ordered.append(
                {
                    "id": section_id,
                    "title": SECTION_TITLES[section_id],
                    "blocks": [],
                }
            )
        else:
            existing.setdefault("title", SECTION_TITLES[section_id])
            existing.setdefault("blocks", [])
            ordered.append(existing)
    doc["sections"] = ordered
    return missing


def fill_metrics_from_snapshot(doc: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """If the model returned no key metrics, derive them from the snapshot."""
    metrics = doc.get("keyMetrics")
    if isinstance(metrics, list) and metrics:
        return False
    quote = snapshot.get("quote") or {}
    performance = snapshot.get("performance") or {}
    last = quote.get("last")
    # `.get("last", 0)` would still hand back None when the key exists but is
    # null, and `f"{None:,.2f}"` raises — a repair must never fail the run.
    last_text = f"{last:,.2f}" if isinstance(last, (int, float)) else "n/a"
    ret_1y = performance.get("ret1y")
    ret_text = f"{ret_1y:+.1f}%" if isinstance(ret_1y, (int, float)) else "n/a"
    derived = [
        {"label": "最新价", "value": last_text, "tone": "neutral", "sourceRefs": ["S1"]},
        {
            "label": "近 1 年收益",
            "value": ret_text,
            "tone": "neutral",
            "sourceRefs": ["S1"],
        },
    ]
    doc["keyMetrics"] = derived
    return True


def _clamp_text(text: str, limit: int, *, min_length: int = 0) -> str:
    """把过长文本按**句边界**收到 `limit` 以内，末尾补一个省略号。

    ★v12 修订（V11-P2-4 的连带）：省略号**占一个字符**，所以先退到 `limit - 1` 再拼
    `…`，否则「600 → 601」就是我们亲手制造的一次字数越界。三条硬约束（AC-118 断言）：

    1. 结果长度 **≤ limit**；
    2. 若真的切了，结果以 `…` 结尾；
    3. **不切在数字中间**（R-56）：`-1,509.76` 切成 `-1.` 会凭空造出一个未接地 claim，
       所以只在标点之后落刀；找不到合适的标点就退到「最后一个非数字边界」，再不行才硬切。
    """
    if limit <= 0 or len(text) <= limit:
        return text
    budget = max(min_length, limit - 1)  # 留一个字符给 `…`
    head = text[:budget]
    cut = -1
    for marker in _SENTENCE_END + _CLAUSE_END:
        search_from = len(head)
        while True:
            index = head.rfind(marker, 0, search_from)
            if index < 0:
                break
            # ★v12：ASCII 逗号/句点在**数字之间**是千分位与小数点，不是句子边界。
            # `1,509.76` 的逗号若被当成句末，截出来的就是 `1,` —— 一个凭空产生的、
            # 必然未接地的数字（R-56 的原话："绝不切在数字中间"）。
            if marker in (",", ".") and index > 0 and index + 1 < len(text):
                before, after = text[index - 1], text[index + 1]
                if before.isdigit() and (after.isdigit() or after in ".," ):
                    search_from = index
                    continue
            if index + 1 > cut:
                cut = index + 1
            break
    if cut <= 0:
        # 没有可用的标点：退到「最后一个不是数字/小数点的位置」，避免切断一个数字。
        cut = budget
        while cut > max(min_length, 1) and (text[cut - 1].isdigit() or text[cut - 1] in ".,%"):
            cut -= 1
        if cut < min_length or cut <= 0:  # pragma: no cover - 极端夹具
            cut = budget
    trimmed = text[:cut].rstrip()
    if not trimmed:  # pragma: no cover - 全是标点的夹具
        trimmed = text[:budget]
    return f"{trimmed}…"


def _resolve_schema(node: Any, defs: dict[str, Any]) -> dict[str, Any]:
    """跟随单层 `$ref`（本项目 schema 只有 `#/$defs/<name>` 这一种形态）。"""
    seen = 0
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and seen < 8:
        ref = node["$ref"]
        if not ref.startswith("#/$defs/"):
            return node
        target = defs.get(ref.split("/")[-1])
        if not isinstance(target, dict):
            return node
        # `$ref` 与兄弟键（若同时存在）合并：`{"$ref": ..., "maxItems": 8}` 的写法要都生效。
        merged = dict(target)
        merged.update({k: v for k, v in node.items() if k != "$ref"})
        node = merged
        seen += 1
    return node if isinstance(node, dict) else {}


def _pick_variant(node: dict[str, Any], value: Any, defs: dict[str, Any]) -> dict[str, Any] | None:
    """在 `anyOf` 里挑**这一个值**所属的变体。

    ★v12（C-37 的机制）：`sections[].blocks[]` 是 12 个块型的 `anyOf`，按 `type` 字段
    分派。分派不出去（例如块没有 `type`）就返回 `None`，**不要**随便挑第一个——那会拿
    paragraph 的 4000 去量一个 heading，正是设计草案被评审否掉的那一版。
    """
    variants = node.get("anyOf")
    if not isinstance(variants, list):
        return None
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str):
            for variant in variants:
                resolved = _resolve_schema(variant, defs)
                spec = (resolved.get("properties") or {}).get("type") or {}
                allowed = spec.get("enum")
                if spec.get("const") == kind or (isinstance(allowed, list) and kind in allowed):
                    return resolved
    return None


def _clamp_node(
    value: Any,
    node: Any,
    defs: dict[str, Any],
    path: str,
    repairs: list[str],
) -> Any:
    """把**一个**值按它自己的 schema 节点收敛。返回收敛后的值（可能与入参同一对象）。

    做四件事，全部只处理 schema **明说**的上界（S-4：格式问题 clamp，事实问题不动）：

    * `maxLength` 且是 `str` → `_clamp_text()`（按句边界）；
    * `maxItems` 且是 `list` → 截掉多余的条目；
    * `maxProperties` 且是 `dict` → 丢掉**非必填**的多余键（表行的 12 键上限）；
    * 否则递归（`properties` 逐个字段、`items` 逐个元素、`anyOf` 按 `type` 分派）。
    """
    node = _resolve_schema(node, defs)
    if not node:
        return value

    variant = _pick_variant(node, value, defs)
    if variant is not None:
        return _clamp_node(value, variant, defs, path, repairs)

    max_length = node.get("maxLength")
    if isinstance(value, str) and isinstance(max_length, int):
        min_length = node.get("minLength")
        clamped = _clamp_text(value, max_length, min_length=min_length if isinstance(min_length, int) else 0)
        if clamped != value:
            repairs.append(f"{CLAMP_PREFIX} {path} {len(value)}→{len(clamped)}")
            return clamped
        return value

    max_items = node.get("maxItems")
    if isinstance(value, list) and isinstance(max_items, int) and len(value) > max_items:
        repairs.append(f"{CLAMP_PREFIX} {path} {len(value)}→{max_items}")
        drop = len(value) - max_items
        item_node = node.get("items") or {}
        kept = [
            _clamp_node(item, item_node, defs, f"{path}[{index}]", repairs)
            for index, item in enumerate(value[:max_items])
        ]
        for offset in range(max_items, max_items + drop):
            repairs.append(f"{CLAMP_PREFIX} {path}[{offset}] 条目超出上限已丢弃")
        return kept

    if isinstance(value, list):
        item_node = node.get("items") or {}
        return [
            _clamp_node(item, item_node, defs, f"{path}[{index}]", repairs)
            for index, item in enumerate(value)
        ]

    if isinstance(value, dict):
        max_properties = node.get("maxProperties")
        properties = node.get("properties") or {}
        required = set(node.get("required") or [])
        working = value
        if isinstance(max_properties, int) and len(working) > max_properties:
            keep: dict[str, Any] = {}
            for key, item in working.items():
                if len(keep) < max_properties or key in required:
                    keep[key] = item
                else:
                    repairs.append(f"{CLAMP_PREFIX} {path}.{key} 键数超出上限已丢弃")
            working = keep
        out: dict[str, Any] = {}
        for key, item in working.items():
            spec = properties.get(key)
            if spec is None:
                out[key] = item
                continue
            # ★v12：根层不要前导点（`summary` 而不是 `.summary`）——`quality.clamp.fields`
            # 是给界面读的字段名，前导点会让"哪几个字段被截了"多出一个不存在的容器。
            child = f"{path}.{key}" if path else key
            out[key] = _clamp_node(item, spec, defs, child, repairs)
        return out

    return value


def clamp_doc(doc: dict[str, Any], repairs: list[str]) -> list[str]:
    """★v12（S3 / §22.3.4 / C-34）：把越界的**格式**收敛到 schema 说明的上界。

    返回被收敛的字段路径列表（空 ⇒ 一个字都没动）。`repairs` 里追加的每条都以
    `CLAMP_PREFIX` 开头，`ValidationOutcome.quality.clamp` 从它派生
    （`_clamp_payload()`），所以"收敛了几处、哪几处"只有一份记录。

    三条纪律（写在代码里，因为它们是验收点）：

    1. **只收敛「长了」和「多了」，不猜内容**（S-4）。真事实问题（编造数字）由接地口径
       负责，两者不重叠——clamp 不许借机改数；
    2. **必须留痕**：每处收敛一行 `收敛 <path> <before>→<after>`，读者在「降级」抽屉里
       能看到"摘要超长，已截断 1 处"，而不是永远不知道为什么黄；
    3. **status 必须降级**：调用方据 `repairs` 里的前缀把 `status` 打到 `degraded`
       （`validate_doc` 的 `clamped` 分支）。

    为什么挂在 `normalize_doc()` 里（C-34）：它是**唯一**的修补入口，而 `salvage_doc()`
    内部也调它——于是「问题只在长度上」的文档两条路（validate / salvage）都能救回来，
    正是 E-93 的 B 分支那个洞。
    """
    schema = load_schema()
    defs = schema.get("$defs") or {}
    clamped = _clamp_node(doc, schema, defs, "", repairs)
    if isinstance(clamped, dict):
        doc.clear()
        doc.update(clamped)
    return [repair for repair in repairs if repair.startswith(CLAMP_PREFIX)]


def clamp_payload(repairs: Iterable[str]) -> dict[str, Any]:
    """从 `repairs` 派生 `quality.clamp = {count, fields}`（**唯一**派生点）。

    `fields` 取每条的第二个词（`收敛 summary 601→600` → `summary`），并按「插入顺序去重」：
    同一份文档里两个段落都超长时，读者要看的是"哪几个字段被截了"而不是重复两行。
    """
    fields: list[str] = []
    for repair in repairs:
        if not repair.startswith(CLAMP_PREFIX):
            continue
        parts = repair.split()
        name = parts[1] if len(parts) > 1 else "unknown"
        if name not in fields:
            fields.append(name)
    return {"count": len(fields), "fields": fields}


def normalize_doc(
    doc: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    provisional: bool = False,
) -> tuple[dict[str, Any], list[str], int, list[str]]:
    """Repair pass. Returns the (possibly mutated) doc plus what was changed.

    ``provisional`` is set explicitly rather than defaulted: whether a document is
    a draft is a fact about the run, so a model that writes `"provisional": true`
    into a perfectly good report must not be able to mislabel it.
    """
    repairs: list[str] = []
    instrument = snapshot.get("instrument") or {}
    doc.setdefault("schema", "aragon.research.report/v1")
    doc.setdefault(
        "instrument",
        {
            "symbol": instrument.get("symbol", ""),
            "name": instrument.get("name", ""),
            "market": instrument.get("market", "CN"),
            "currency": instrument.get("currency", "CNY"),
        },
    )
    # `asOf` is a snapshot fact. The schema forbids `format` on purpose (§8.3), so
    # a model writing "2026-11-16T07:31:04Z (Asia/Shanghai)" would otherwise reach
    # the UI and render as an unparsable date.
    snapshot_as_of = snapshot.get("asOf")
    incoming_as_of = doc.get("asOf")
    if snapshot_as_of:
        doc["asOf"] = snapshot_as_of
        if incoming_as_of is not None and incoming_as_of != snapshot_as_of:
            repairs.append("asOf 已按快照校正（模型给出的时点不是 ISO-8601）")
    elif not isinstance(incoming_as_of, str) or not incoming_as_of:
        doc["asOf"] = ""

    if doc.get("provisional") != provisional:
        doc["provisional"] = provisional
        repairs.append("provisional 标记已由流水线重置（模型不得自定为草稿）")
    doc.setdefault("disclaimer", "本报告由 AI 生成，仅供研究参考，不构成投资建议。")
    if not isinstance(doc.get("sources"), list):
        doc["sources"] = []
    if not doc["sources"]:
        doc["sources"] = snapshot.get("sources") or []
        if doc["sources"]:
            repairs.append("sources 缺失，已从快照补齐")

    missing = ensure_sections(doc)
    if missing:
        repairs.append(f"缺失章节已补空壳：{', '.join(missing)}")
    pruned = prune_unknown_blocks(doc)
    if pruned:
        repairs.append(f"裁剪未知块 {pruned} 个")

    if fill_metrics_from_snapshot(doc, snapshot):
        repairs.append("keyMetrics 缺失，已从快照补齐")

    rating = doc.get("rating")
    if not isinstance(rating, dict):
        doc["rating"] = {"stance": "watch", "conviction": 1, "horizon": "n/a"}
        repairs.append("rating 缺失，已置为 watch")
    else:
        if rating.get("stance") not in ("buy", "hold", "sell", "watch"):
            rating["stance"] = "watch"
            repairs.append("rating.stance 非法，已置为 watch")
        conviction = rating.get("conviction")
        if not isinstance(conviction, int) or not 1 <= conviction <= 5:
            rating["conviction"] = 3
            repairs.append("rating.conviction 越界，已置为 3")
        rating.setdefault("horizon", "6-12m")

    if not isinstance(doc.get("risks"), list):
        doc["risks"] = []
    if not isinstance(doc.get("keyMetrics"), list):
        doc["keyMetrics"] = []
    # ★v12（S3 / C-34）：**最后**一个 pass —— 把越界的格式收敛到 schema 说明的上界。
    # 放在最后是必须的：前面 11 个 pass 会补字段、补章节、补指标，任何一个都可能让文档
    # 从"短"变"长"；先 clamp 再补等于白做。这也保证 `salvage_doc()` 走同一条路
    # （它内部就调本函数），于是「只有长度不合规」的文档两条路都能救回来。
    clamp_doc(doc, repairs)
    return doc, repairs, pruned, missing


def validate_doc(
    doc: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    mock: bool = False,
    provisional: bool = False,
    source: str | None = None,
    engine: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    prompt: dict[str, Any] | None = None,
    schema_path: Path | None = None,
    salvage: SalvageReport | None = None,
    pruned_blocks: int | None = None,
) -> ValidationOutcome:
    """Full validation pipeline for a model-produced document.

    ``mock=True`` means the document was assembled from the snapshot by rules, so
    the grounding ratio is informational only — there is no model prose to
    fabricate anything (spec §16 verdict 6 keeps mock identifiable instead).

    ``provisional=True`` marks the document as a failure transcript; the pipeline
    passes it explicitly so a model can never label its own report a draft.

    ``source`` defaults to ``mock``/``aragon`` from ``mock`` and is what the
    ``quality.source`` record carries through to the UI.

    ``engine`` (★v6 G4) is the engine fingerprint assembled by the caller. It is a
    parameter rather than something computed here because `agent/` may not import
    `services/` (`test_boundaries` rule 4) — the dependency arrow is
    `run_manager → agent.fingerprint → report_validator`, never the reverse.

    ``cost`` / ``prompt`` (★v7 H4/H5) 是同样形状的入参：只有调用方知道这次 run 的三态
    成本与当次提示词的指纹。

    ``schema_path`` (★v8 V8-P0-1 / AC-88) 是**当次交给 CLI 的那份** schema
    （`workspace/schema.json`）。默认仍回退 `load_schema()` 以便旧调用者不回归，但
    生产路径永远传它——两份文件、两次读取、零个断言正是 E-55 证明过会漂移的东西。
    """
    # Deep copy: the repair pass mutates nested sections/blocks/rating in place,
    # and the caller's document (a runner's module-level template, for instance)
    # must come out untouched.
    repaired, repairs, pruned, missing = normalize_doc(copy.deepcopy(doc), snapshot, provisional=provisional)
    # ★实施发现（I-91）：抢救路径已经裁过一次未知块（`salvage_doc` 内部的
    # `normalize_doc`），那个数字由调用方转交进来，否则 `quality.prunedBlocks` 会归零。
    if pruned_blocks:
        pruned += max(0, int(pruned_blocks))
    errors: list[str] = []

    # 构造性正确：直接用当次交付的那份字节，不复用进程级缓存的"真源"。
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path is not None else load_schema()
    try:
        jsonschema.validate(repaired, schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        errors.append(f"{location}: {exc.message[:300]}")
    except jsonschema.SchemaError as exc:  # pragma: no cover - schema bug
        errors.append(f"schema invalid: {exc.message[:300]}")

    # A second pass through the Pydantic mirror catches what jsonschema cannot
    # express (discriminated unions, ge/le bounds) and gives a decent message.
    if not errors:
        try:
            ReportDoc.model_validate(repaired)
        except Exception as exc:  # pydantic ValidationError
            errors.append(str(exc)[:500])

    grounding = grounding_report(repaired, snapshot)
    ratio = grounding.ratio
    # AC-61: `ungroundedCount` and `grounding.factMissing` are the same number by
    # construction, not by coincidence — one field is read off the other.
    ungrounded = grounding.fact_missing
    # ★v12（S3 / §22.3.4 纪律 3）：收敛过就必须降级。读者有权知道这份报告被截过——
    # 悄悄截断会让"我们的上限"伪装成"模型的原话"，与 v5 §19 立下的"只标不改数"同源。
    clamp = clamp_payload(repairs)
    if provisional:
        # A provisional document is a failure transcript, not an analysis:
        # grounding and the five-section rule do not apply to it.
        status = "provisional" if not errors else "degraded"
    elif errors:
        status = "degraded"
    elif clamp["count"]:
        # ★v12（S3 / AC-117）：比 salvage 更高优先级的降级理由——它就是"这份被截过"。
        status = "degraded"
    elif salvage is not None and salvage.used:
        # ★v9（K5 / 裁决 K-11）：抢救过的报告**永远是 degraded**，不是 ready 也不是
        # provisional。悄悄抢救 = 让不完整的内容冒充完整（同 v5"只标不改数"的理由）。
        status = "degraded"
    elif mock:
        status = "ready"
    elif ratio < GROUNDED_THRESHOLD or missing:
        status = "degraded"
    else:
        status = "ready"

    outcome = ValidationOutcome(
        doc=repaired,
        grounded_ratio=ratio,
        missing_sections=missing,
        pruned_blocks=pruned,
        ungrounded_places=ungrounded,
        repairs=repairs,
        status=status,
        errors=errors,
        source=source or ("mock" if mock else "aragon"),
        grounding=grounding,
        engine=engine,
        cost=cost,
        prompt=prompt,
        salvage=salvage,
    )
    logger.info(
        "validate grounded=%.3f missing=%s pruned=%s status=%s errors=%s source=%s fact=%s/%s skipped=%s",
        ratio,
        missing,
        pruned,
        status,
        len(errors),
        outcome.source,
        grounding.fact_claims - grounding.fact_missing,
        grounding.fact_claims,
        grounding.skipped,
    )
    return outcome


@dataclass(slots=True)
class SalvageReport:
    """★v9（K5 / §21.3.6）：抢救"接近合规"的模型产出时，到底丢了什么。

    三个计数器**互不重叠**（分工写死在 §21.3.6 的 v10 修订里）：

    * `droppedKeys`     —— 多余的**顶层**键（报告文档的 12 个键是白名单）；
    * `droppedSections` —— 缺 `id`/`title` 的节；
    * `droppedBlocks`   —— **合法 `type` 但字段不全**的块。

    未知 `type` **不在这里数**：它已经被 `prune_unknown_blocks()` 在更早一步裁掉并计入
    `quality.prunedBlocks`（`report_validator.py` 的 `normalize_doc()` 在校验之前就调用它，
    实测 E-79：`prunedBlocks == 1`）。两个计数器重叠会让"丢了 3 处"变成"丢了 4 处"。
    """

    used: bool = False
    droppedKeys: int = 0
    droppedSections: int = 0
    droppedBlocks: int = 0
    #: ★实施发现（I-91）：未知 `type` 的块由**本函数**内部的 `normalize_doc()` 裁掉，
    #: 所以"裁了几个"这个数只有这里知道。它不是 salvage 的第四个计数器，而是**转交**
    #: 给 `quality.prunedBlocks` 的那个既有数的来源（两个计数器仍然不重叠）。
    prunedBlocks: int = 0
    #: ★v12（S3 / §22.3.4 纪律 1）：`normalize_doc()` 里的 clamp 救回来几处**格式**越界。
    #: 与上面三个计数器不重叠：那三个数"丢掉了什么"，这个数"截短了什么"——后者是有救的，
    #: 前者是没有的，混在一起会让"丢了 3 处"变得不可解释。
    clamped: int = 0
    #: ★v12（S4 / §22.6.3）：从模型正文里还原出来的方式（`json` / `json_repaired`）。
    #: `None` ⇒ 没走 L1.5。它是 `quality.salvage.how` 的唯一来源（"从正文还原"这句话的
    #: 事实依据，不是前端猜的）。
    how: str | None = None
    reasons: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "droppedKeys": self.droppedKeys,
            "droppedSections": self.droppedSections,
            "droppedBlocks": self.droppedBlocks,
            # ★v12 修订（V11-P2-2）：`as_payload()` 过去只发 5 个键，**漏了** `prunedBlocks`
            # ——写进一个前端读不到的地方等于没写。本轮把 `prunedBlocks` / `clamped` /
            # `how` 一起补上，`schemas/report.py::SalvageQuality` 同步声明（C-48 的同型守卫）。
            "prunedBlocks": self.prunedBlocks,
            "clamped": self.clamped,
            **({"how": self.how} if self.how else {}),
            # 最多 5 条、去重、每条 ≤80 字符（前端要能展开，但不要把日志贴上去）。
            "reasons": self.reasons[:5],
        }

    @property
    def total(self) -> int:
        """前端显示"3 处"的**唯一**来源：服务端算好，不在前端拼第二个真源。"""
        return self.droppedKeys + self.droppedSections + self.droppedBlocks


#: 每一类块的**必填项**（`report_doc.schema.json` 的 `block.anyOf` 里各自的 `required`，
#: 去掉 `type` 之后的值）。只在 salvage 里用：缺一项就是"这一块救不回来"，丢掉并计数。
#:
#: ★评审修订（I-101）：这张表原来对 `heading` / `callout` / `quote` 只写了 `text`，而 schema
#: 还要求 `level`、`tone`+`title`、`attribution`+`sourceRefs`。后果不是"少救一块"，而是
#: **整份报告救不回来**：那三块被当成"合法块"留下，第 7 步 `jsonschema.validate` 直接失败
#: ⇒ 返回 `None` ⇒ 用户拿到一张"这不是一份可用的投研报告"的临时卡片，而这正是 K5 要消灭的
#: 那种结局（Q-4「丢得少」）。表与 schema 的一致性由
#: `tests/test_salvage.py::test_block_required_matches_the_schema` 机器守着。
BLOCK_REQUIRED: dict[str, tuple[str, ...]] = {
    "paragraph": ("text",),
    "heading": ("level", "text"),
    "bullets": ("items",),
    "callout": ("tone", "title", "text"),
    "metrics": ("items",),
    "table": ("columns", "rows"),
    "chart": ("chart",),
    "quote": ("attribution", "sourceRefs", "text"),
    "timeline": ("events",),
    "comparables": ("peers",),
    "scenario": ("scenarios",),
    "divider": (),
}


def salvage_doc(raw: Any, snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, SalvageReport]:
    """★v9（K5）：把"接近合规"的模型产出救成一份**过 schema 校验**的文档。

    七步，每步只做一件事，任何一步失败就返回 `(None, report)`：

    1. `raw` 必须是 dict；按 schema 的 `properties` 白名单（12 个键）裁掉多余键；
    2. `sections` 逐节要求 `id`/`title`/`blocks`；缺 `id` 的节丢弃；
    3. `blocks` 逐块按**各自必填项**判定：`paragraph` 缺 `text` 丢弃、`table` 缺 `rows`
       丢弃……**未知 `type` 不在这里处理**（它已被 `prune_unknown_blocks()` 更早裁掉）；
    4. `rating.stance` 不在枚举内 → `watch`；`conviction` clamp 到 1..5；
    5. `keyMetrics` 逐条要求 `label` 且 `value` 可转字符串；
    6. 交给 `normalize_doc()`（既有：补 asOf / 补 sources / 补空章节 / 裁未知块）走同一条路；
    7. `jsonschema.validate` —— **不过就是 `None`**（绝不允许"半份不合法文档"被存下来）。

    为什么必须可见（裁决 K-11）：一份"少了两块"的报告被当成完整报告，会让用户拿它做
    判断——这正是 v5 §19 立下"只标不改数"的同一个理由。所以 salvage 必须降 status 到
    `degraded`，并且每个被丢掉的块都要有一个理由。
    """
    report = SalvageReport(used=False)
    if not isinstance(raw, dict):
        report.reasons.append("unsalvageable")
        return None, report

    schema = load_schema()
    allowed = set((schema.get("properties") or {}).keys())

    # ── 1. 顶层键白名单 ────────────────────────────────────────────────────
    candidate: dict[str, Any] = {}
    for key, value in raw.items():
        if key in allowed:
            candidate[key] = value
        else:
            report.droppedKeys += 1
            report.reasons.append(f"未知顶层键 {key}"[:80])
    if not candidate:
        report.reasons.append("unsalvageable")
        return None, report

    # ── 2. sections 逐节 ───────────────────────────────────────────────────
    sections = candidate.get("sections")
    kept_sections: list[dict[str, Any]] = []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                report.droppedSections += 1
                report.reasons.append("章节不是对象")
                continue
            if not section.get("id"):
                report.droppedSections += 1
                report.reasons.append("章节缺 id")
                continue
            kept_sections.append(section)
    else:
        kept_sections = []
    candidate["sections"] = kept_sections

    # ── 3. blocks 逐块（按各自必填项；未知 type 交给 prune_unknown_blocks）──
    for section in kept_sections:
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            section["blocks"] = []
            continue
        kept_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                report.droppedBlocks += 1
                report.reasons.append("块不是对象")
                continue
            kind = str(block.get("type") or "")
            required = BLOCK_REQUIRED.get(kind)
            if required is None:
                # 未知 type：原样留着，让 `normalize_doc()` 的 `prune_unknown_blocks()`
                # 裁掉并计入 `quality.prunedBlocks`（**不在这里数**，V10-P1-3）。
                kept_blocks.append(block)
                continue
            missing = [field_name for field_name in required if block.get(field_name) in (None, "", [])]
            if missing:
                report.droppedBlocks += 1
                report.reasons.append(f"{kind} 缺 {'/'.join(missing)}"[:80])
                continue
            kept_blocks.append(block)
        section["blocks"] = kept_blocks

    # ── 4. rating ──────────────────────────────────────────────────────────
    rating = candidate.get("rating")
    if isinstance(rating, dict):
        if rating.get("stance") not in ("buy", "hold", "sell", "watch"):
            rating["stance"] = "watch"
            report.reasons.append("rating.stance 非法 → watch"[:80])
        conviction = rating.get("conviction")
        try:
            value = int(conviction)
        except (TypeError, ValueError):
            value = 3
        rating["conviction"] = max(1, min(5, value))
        rating.setdefault("horizon", "6-12m")

    # ── 5. keyMetrics ──────────────────────────────────────────────────────
    metrics = candidate.get("keyMetrics")
    if isinstance(metrics, list):
        kept_metrics: list[dict[str, Any]] = []
        for item in metrics:
            if not isinstance(item, dict) or not item.get("label"):
                report.reasons.append("指标缺 label")
                continue
            value = item.get("value")
            if value is None or isinstance(value, (dict, list)):
                report.reasons.append("指标 value 不可读")
                continue
            item["value"] = str(value)
            kept_metrics.append(item)
        candidate["keyMetrics"] = kept_metrics

    # ── 6 + 7. 走既有的修补路径，然后**必须**过 schema ─────────────────────
    # ★实施发现（I-91）：`normalize_doc()` 会调用 `prune_unknown_blocks()`，而那个数字
    # 过去在这里就丢了 —— 于是 O-20 / AC-102 要的 `quality.prunedBlocks == 1` 永远拿不到
    # （第二次 `validate_doc` 时未知块已经不在文档里了）。先把"待裁的未知块"数出来。
    report.prunedBlocks = sum(
        1
        for section in kept_sections
        for block in section.get("blocks") or []
        if isinstance(block, dict) and str(block.get("type") or "") not in BLOCK_REQUIRED
    )
    repaired, _repairs, _pruned, _missing = normalize_doc(
        copy.deepcopy(candidate), snapshot, provisional=False
    )
    # ★v12（S3 / §22.3.4）：`normalize_doc()` 里的 clamp 救回来几处，只有这里知道。
    # 不数出来，"抢救后仍不合 schema"与"抢救后截了 3 处"在界面上长得一模一样。
    report.clamped = len(clamp_payload(_repairs)["fields"])
    try:
        jsonschema.validate(repaired, schema)
        ReportDoc.model_validate(repaired)
    except Exception as exc:  # jsonschema.ValidationError / pydantic ValidationError
        logger.info("salvage 仍不合 schema：%s", str(exc)[:200])
        report.reasons.append(f"救不回来：{str(exc)[:60]}")
        report.used = False
        return None, report

    report.used = (
        report.droppedKeys + report.droppedSections + report.droppedBlocks > 0
    )
    # 理由去重并截断（`as_payload()` 只放前 5 条）。
    report.reasons = list(dict.fromkeys(reason for reason in report.reasons if reason))
    logger.info(
        "salvage ok keys=%s sections=%s blocks=%s",
        report.droppedKeys,
        report.droppedSections,
        report.droppedBlocks,
    )
    return repaired, report


def _drop_partial_string(candidate: str) -> str:
    """去掉尾部那半截未闭合的字符串（截断最常发生在这里）。

    返回"最后一个未闭合引号之前"的内容；没有半截字符串则原样返回。
    """
    in_string = False
    escaped = False
    last_open = -1
    for position, char in enumerate(candidate):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            last_open = position
    if not in_string or last_open < 0:
        return candidate
    return candidate[:last_open].rstrip().rstrip(",")


def _pad_closers(candidate: str, *, max_pad: int) -> str | None:
    """按未闭合括号栈补齐尾部的 `}` / `]`；需要的字符数超过 `max_pad` 就返回 `None`。"""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]" and stack:
            stack.pop()
    if not stack or len(stack) > max_pad:
        return None
    return candidate + "".join(reversed(stack))


def _close_unbalanced(candidate: str, *, max_pad: int = 40, max_attempts: int = 8) -> dict[str, Any] | None:
    """★v12（S4 / §22.3.5 L1.5(b)）：给被截断的 JSON 做**有界**尾补，补不成就放弃。

    规则写死四条，因为"补结构"与"猜内容"之间只有一线：

    1. **只补尾部**的 `}` / `]`，顺序由未闭合括号栈决定；
    2. 补的字符总数 **≤ max_pad**（默认 40）：一次被 `--max-tokens` 截断的输出，缺口就是
       几个闭合符；补 40 个还不行说明它是被拦腰切断的，那就该放弃；
    3. 截断落在**字符串中间**时先丢掉那半截字符串，然后**再退到上一个边界**
       （`,` / `{` / `[`）重试 —— 否则剩下的 `"disclaimer": ` 是个悬空键，永远补不回来
       （实测 `payload[:-2]` 就是这样；★v12 的落地修正见 I-115）；
    4. 补完**必须** `json.loads` 成功且是 dict，否则返回 `None`。

    为什么值得做（S-6）：这些字节**已经付过钱**了。扔掉它们等于把同一段输出再买一次。
    ★v12 修订（V11-P2-11）：本机 24 份 `result.json` 里 `text` 含 `{` 的有 **0** 份，
    所以这是**防御性**能力，验收靠构造夹具（AC-134），不声称能救回真实生成的 33%。

    `max_attempts` 有界是必须的：每次退边界都会变短，无界重试等于在长文里做二次指数搜索。
    """
    if not candidate or not candidate.lstrip().startswith(("{", "[")):
        return None
    trimmed = _drop_partial_string(candidate)
    for _attempt in range(max_attempts):
        padded = _pad_closers(trimmed, max_pad=max_pad)
        if padded is not None:
            try:
                parsed = json.loads(padded)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        boundary = max(trimmed.rfind(","), trimmed.rfind("{"), trimmed.rfind("["))
        if boundary <= 0:
            return None
        trimmed = trimmed[:boundary].rstrip()
        if trimmed.endswith(","):
            trimmed = trimmed[:-1]
    return None


def rescue_from_text(
    text: str,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, SalvageReport | None]:
    """★v12（S4 / §22.3.5 L1.5）：从模型**正文**里把结构化报告捡回来。

    返回 `(doc, how, report)`，`how ∈ {"json", "json_repaired"}`，`None` 表示救不回来
    （调用方继续走原来的 `result_schema_unsatisfied` 路径，不合成假报告）。

    三条守卫（R-58：绝不能从散文里抠出**别的** JSON，例如提示词回显）：

    1. 候选必须含 `sections`（报告文档的骨架），否则不认；
    2. 必须过 `salvage_doc()` 的 schema 校验（它内部还会再 `jsonschema.validate` 一次）；
    3. `how` 必须落盘（`quality.salvage.how`），且抢救过的报告一定是 `degraded`。

    顺序：先试"第一个 `{` 到最后一个 `}`"的整段（最可能是完整 JSON），再试有界尾补。
    `json.loads` 成功一次就不再尝试更激进的补法——它更可信。
    """
    if not isinstance(text, str) or "{" not in text:
        return None, None, None
    start = text.find("{")
    end = text.rfind("}")
    candidates: list[tuple[str, str]] = []
    if start >= 0 and end > start:
        # ①整段（含最后一个 `}`）：完整 JSON 的正常形态。
        candidates.append((text[start : end + 1], "json"))
    if start >= 0:
        # ②从第一个 `{` 到结尾：被截断的形态，交给有界尾补。
        candidates.append((text[start:], "json_repaired"))
    for candidate, how in candidates:
        parsed: Any = None
        if how == "json":
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
        else:
            parsed = _close_unbalanced(candidate)
            if parsed is None:
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    parsed = None
                if parsed is not None:
                    how = "json"
        if not isinstance(parsed, dict) or "sections" not in parsed:
            continue
        doc, report = salvage_doc(parsed, snapshot)
        if doc is None:
            continue
        # 即使 salvage 一个字都没丢，这也是"从正文还原"——它必须可见，不能被当成正常产出。
        report.how = how
        report.used = True
        report.reasons = list(dict.fromkeys([*report.reasons, f"从正文还原（{how}）"]))[:5]
        return doc, how, report
    return None, None, None


def build_provisional_doc(
    snapshot: dict[str, Any],
    *,
    raw_text: str,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    """★P1-10: a failed generation still ships something readable.

    The result must validate against the same schema, otherwise it could not be
    stored at all — so it is built from the smallest legal shape (§8.3).
    """
    instrument = snapshot.get("instrument") or {}
    text = (raw_text or "").strip() or "（本次生成没有返回任何可读文本。）"
    body = text[:MAX_PROVISIONAL_TEXT]
    return {
        "schema": "aragon.research.report/v1",
        "provisional": True,
        "instrument": {
            "symbol": instrument.get("symbol", ""),
            "name": instrument.get("name", ""),
            "market": instrument.get("market", "CN"),
            "currency": instrument.get("currency", "CNY"),
        },
        "asOf": snapshot.get("asOf") or "",
        "headline": f"本次生成未完成（{error_code}）",
        "rating": {"stance": "watch", "conviction": 1, "horizon": "n/a"},
        "summary": f"生成引擎返回的内容不满足结构化契约（{error_code}）。以下为原始产出摘要，建议重新生成。",
        "keyMetrics": [],
        "sections": [
            {
                "id": "raw",
                "title": "原始产出摘要",
                "blocks": [
                    {
                        "type": "callout",
                        "tone": "warn",
                        "title": "这不是一份可用的投研报告",
                        "text": (error_message or "结构校验失败，内容未经接地校验。")[:1200],
                    },
                    {"type": "paragraph", "text": body},
                ],
            }
        ],
        "risks": [],
        "sources": [],
        "disclaimer": "本报告由 AI 生成，仅供研究参考，不构成投资建议。",
    }
