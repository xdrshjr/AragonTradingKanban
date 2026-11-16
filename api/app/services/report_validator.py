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
import json
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

_schema_cache: dict[str, Any] = {}


def load_schema() -> dict[str, Any]:
    if "schema" not in _schema_cache:
        path: Path = get_settings().schemas_dir / "report_doc.schema.json"
        _schema_cache["schema"] = json.loads(path.read_text(encoding="utf-8"))
        _schema_cache["bytes"] = path.stat().st_size
    return _schema_cache["schema"]


def schema_bytes() -> int:
    load_schema()
    return int(_schema_cache.get("bytes", 0))


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
    return doc, repairs, pruned, missing


def validate_doc(
    doc: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    mock: bool = False,
    provisional: bool = False,
    source: str | None = None,
    engine: dict[str, Any] | None = None,
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
    """
    # Deep copy: the repair pass mutates nested sections/blocks/rating in place,
    # and the caller's document (a runner's module-level template, for instance)
    # must come out untouched.
    repaired, repairs, pruned, missing = normalize_doc(copy.deepcopy(doc), snapshot, provisional=provisional)
    errors: list[str] = []

    schema = load_schema()
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
    if provisional:
        # A provisional document is a failure transcript, not an analysis:
        # grounding and the five-section rule do not apply to it.
        status = "provisional" if not errors else "degraded"
    elif errors:
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
