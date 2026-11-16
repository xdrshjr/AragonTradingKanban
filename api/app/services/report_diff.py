"""Structural version diff — no text LCS (§18.3.5).

Character-level Chinese diff is unreadable (one character edits a whole paragraph)
and expensive, so version comparison is **structural**: sections by id, blocks by
`(type, index-within-type)`, metrics by label, tables by shape, charts by shape,
everything else by a hash of `block_to_text`.

`tone` is computed here and nowhere else (C-9): `direction × polarity`. "PE 上升是
坏事" is domain knowledge the front end must not have to re-guess — and `polarity`
defaults to `neutral`, so an unlabelled metric is grey rather than wrongly green
(★V4-P1-1 / AC-42).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from . import block_text

Tone = str  # "up" | "down" | "neutral"
Direction = str  # "up" | "down" | "flat"

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

POLARITY_TONE: dict[tuple[str, str], Tone] = {
    # A rise in a bullish metric is good news; a rise in a bearish one is bad.
    ("up", "bullish"): "up",
    ("up", "bearish"): "down",
    ("down", "bullish"): "down",
    ("down", "bearish"): "up",
}


def parse_number(value: Any) -> float | None:
    """`"62.1%"` / `"24.1×"` / `"-3.4pct"` → float; unparseable → None.

    None is honest: the caller renders "—" instead of inventing a delta.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = _NUMBER_RE.search(value.replace(",", ""))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:  # pragma: no cover - regex already guarantees a float
        return None


def tone_for(direction: Direction, polarity: str | None) -> Tone:
    """The one place semantic colour is decided."""
    if direction == "flat":
        return "neutral"
    return POLARITY_TONE.get((direction, (polarity or "neutral")), "neutral")


def _block_hash(block: dict[str, Any]) -> str:
    return hashlib.sha1(block_text.block_to_text(block).encode("utf-8")).hexdigest()


def _first_column(block: dict[str, Any]) -> set[str]:
    columns = block.get("columns") or []
    if not columns:
        return set()
    key = columns[0].get("key")
    values: set[str] = set()
    for row in block.get("rows") or []:
        if isinstance(row, dict) and key in row:
            values.add(str(row[key]))
    return values


def _metrics_map(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("label")): item
        for item in block.get("items") or []
        if isinstance(item, dict) and item.get("label")
    }


def _pair_blocks(section_a: dict[str, Any], section_b: dict[str, Any]) -> list[tuple[dict | None, dict | None]]:
    """Pair blocks inside one section by `(type, index within that type)`."""
    pairs: list[tuple[dict | None, dict | None]] = []
    grouped_a = _group(section_a)
    grouped_b = _group(section_b)
    for block_type in list(dict.fromkeys([*grouped_a.keys(), *grouped_b.keys()])):
        left = grouped_a.get(block_type, [])
        right = grouped_b.get(block_type, [])
        for index in range(max(len(left), len(right))):
            pairs.append(
                (
                    left[index] if index < len(left) else None,
                    right[index] if index < len(right) else None,
                )
            )
    return pairs


def _group(section: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for block in section.get("blocks") or []:
        if isinstance(block, dict):
            grouped.setdefault(str(block.get("type") or ""), []).append(block)
    return grouped


def compare_engine_quality(
    quality_a: dict[str, Any] | None,
    quality_b: dict[str, Any] | None,
    *,
    model_a: str | None = None,
    model_b: str | None = None,
) -> dict[str, Any]:
    """纯函数：两版 `quality.engine` → `comparability`（★v6 G4 / AC-58）。

    `same` 三态：同指纹 → `true`；指纹不同 → `false`；**任一方缺指纹 → `None`**
    （老报告没有引擎记录，回填会伪造数据，所以"不知道"就明说不确定）。

    `changed` 的取值是**指纹的组成项**（`cliVersion / provider / model /
    allowTools / schemaBytes`）。模型名与 provider 取两版报告的 `model` / `provider`
    列——不读 `quality.engine` 里的副本，因为它根本不存在（V6-P1-3）。

    `deltaUnaffected` 永远是 `true`：这个函数不碰任何 delta（O-10）。它是承诺，
    证明它的责任在 `test_report_diff.py`，不在这个字段上。
    """
    engine_a = (quality_a or {}).get("engine") or None
    engine_b = (quality_b or {}).get("engine") or None
    if not engine_a or not engine_b:
        return {
            "same": None,
            "changed": [],
            "deltaUnaffected": True,
            "note": "其中一版没有引擎指纹（早期报告）",
        }

    changed: list[str] = []
    if (engine_a.get("cliVersion") or "") != (engine_b.get("cliVersion") or ""):
        changed.append("cliVersion")
    if (model_a or "") != (model_b or ""):
        changed.append("model")
    left_tools = list(engine_a.get("allowTools") or [])
    right_tools = list(engine_b.get("allowTools") or [])
    if left_tools != right_tools:
        changed.append("allowTools")
    if int(engine_a.get("schemaBytes") or 0) != int(engine_b.get("schemaBytes") or 0):
        changed.append("schemaBytes")

    same = str(engine_a.get("fingerprint") or "") == str(engine_b.get("fingerprint") or "")
    if same and not changed:
        return {"same": True, "changed": [], "deltaUnaffected": True, "note": ""}

    hint = ""
    if model_a and model_b and model_a != model_b:
        hint = f"（{model_a} → {model_b}）"
    note = "两版由不同引擎生成" + hint + "，差异可能来自模型更换而非观点变化"
    return {
        "same": same,
        "changed": changed,
        "deltaUnaffected": True,
        "note": note,
    }


def diff_docs(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    quality_a: dict[str, Any] | None = None,
    quality_b: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure function: two report documents in, one `DiffResult` out.

    ★v6：`comparability` 是**追加键**，既有 `a` / `b` / `summary`（字符串）/
    `metricsDelta` / `blocksChanged` / `sectionsAdded` / `sectionsRemoved` 全部不动。
    改名或换成对象会让 `DiffResult` 校验失败、`/diff` 直接 500（V6-P1-2）。
    """
    sections_a = {str(s.get("id")): s for s in a.get("sections") or [] if isinstance(s, dict)}
    sections_b = {str(s.get("id")): s for s in b.get("sections") or [] if isinstance(s, dict)}
    added = [sections_b[key].get("title") or key for key in sections_b if key not in sections_a]
    removed = [sections_a[key].get("title") or key for key in sections_a if key not in sections_b]

    metrics_delta: list[dict[str, Any]] = []
    blocks_changed: list[dict[str, Any]] = []

    for section_id in sections_a.keys() & sections_b.keys():
        for block_a, block_b in _pair_blocks(sections_a[section_id], sections_b[section_id]):
            if block_a is None or block_b is None:
                blocks_changed.append(
                    {
                        "sectionId": section_id,
                        "blockType": str((block_a or block_b or {}).get("type") or ""),
                        "change": "added" if block_a is None else "removed",
                        "blockIdA": None if block_a is None else _block_id(sections_a[section_id], block_a),
                        "blockIdB": None if block_b is None else _block_id(sections_b[section_id], block_b),
                    }
                )
                continue
            block_type = str(block_a.get("type") or "")
            if block_type == "metrics":
                metrics_delta.extend(_diff_metrics(block_a, block_b))
                if _metrics_map(block_a).keys() != _metrics_map(block_b).keys():
                    blocks_changed.append(
                        {
                            "sectionId": section_id,
                            "blockType": block_type,
                            "change": "modified",
                            "blockIdA": _block_id(sections_a[section_id], block_a),
                            "blockIdB": _block_id(sections_b[section_id], block_b),
                        }
                    )
                continue
            if _block_hash(block_a) != _block_hash(block_b):
                blocks_changed.append(
                    {
                        "sectionId": section_id,
                        "blockType": block_type,
                        "change": "modified",
                        "blockIdA": _block_id(sections_a[section_id], block_a),
                        "blockIdB": _block_id(sections_b[section_id], block_b),
                    }
                )

    rating_a = a.get("rating") or {}
    rating_b = b.get("rating") or {}
    rating_changed = None
    if rating_a != rating_b:
        rating_changed = {
            "from": {"stance": rating_a.get("stance"), "target": _target(rating_a)},
            "to": {"stance": rating_b.get("stance"), "target": _target(rating_b)},
        }

    result = {
        "a": str(a.get("_reportId") or ""),
        "b": str(b.get("_reportId") or ""),
        "statusChanged": None,
        "ratingChanged": rating_changed,
        "metricsDelta": metrics_delta,
        "sectionsAdded": added,
        "sectionsRemoved": removed,
        "blocksChanged": blocks_changed,
        "summary": _summary(rating_changed, metrics_delta, added, removed),
        "comparability": compare_engine_quality(
            quality_a,
            quality_b,
            model_a=str(a.get("_model") or "") or None,
            model_b=str(b.get("_model") or "") or None,
        ),
    }
    return result


def _target(rating: dict[str, Any]) -> str | None:
    value = rating.get("targetPrice")
    return None if value is None else f"{value:g}"


def _block_id(section: dict[str, Any], block: dict[str, Any]) -> str:
    section_id = str(section.get("id") or "")
    for index, candidate in enumerate(section.get("blocks") or []):
        if candidate is block:
            return block_text.block_id_for(section_id, index)
    return block_text.block_id_for(section_id, 0)


def _diff_metrics(block_a: dict[str, Any], block_b: dict[str, Any]) -> list[dict[str, Any]]:
    left = _metrics_map(block_a)
    right = _metrics_map(block_b)
    deltas: list[dict[str, Any]] = []
    for label in list(dict.fromkeys([*left.keys(), *right.keys()])):
        before_item = left.get(label) or {}
        after_item = right.get(label) or {}
        before = parse_number(before_item.get("value"))
        after = parse_number(after_item.get("value"))
        if before is None or after is None:
            direction: Direction = "flat"
            delta_pct = None
        else:
            difference = after - before
            delta_pct = round(difference / abs(before) * 100, 2) if before else None
            if abs(difference) < 1e-9:
                direction = "flat"
            else:
                direction = "up" if difference > 0 else "down"
        polarity = after_item.get("polarity") or before_item.get("polarity") or "neutral"
        deltas.append(
            {
                "label": label,
                "before": before,
                "after": after,
                "deltaPct": delta_pct,
                "direction": direction,
                "polarity": polarity,
                "tone": tone_for(direction, polarity),
            }
        )
    return deltas


def _summary(
    rating_changed: dict[str, Any] | None,
    metrics_delta: list[dict[str, Any]],
    added: list[str],
    removed: list[str],
) -> str:
    parts: list[str] = []
    if rating_changed:
        parts.append(
            f"评级由{rating_changed['from']['stance'] or '—'}调整为{rating_changed['to']['stance'] or '—'}"
        )
    for delta in metrics_delta[:3]:
        if delta.get("deltaPct") is None:
            continue
        parts.append(f"{delta['label']} {delta['deltaPct']:+.1f}%")
    if added:
        parts.append(f"新增「{'、'.join(added[:3])}」章节")
    if removed:
        parts.append(f"移除「{'、'.join(removed[:3])}」章节")
    return "，".join(parts) if parts else "两个版本没有结构性差异"


__all__ = ["compare_engine_quality", "diff_docs", "parse_number", "tone_for"]
