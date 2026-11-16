"""Single projection source: block → plain text, block ids, inline citations (§18.4.2).

Four callers depend on this module and must never re-implement any of it (C-9):

* ``exporter`` — the inline ``[S1]`` regex (moved here so 导出/检索/追问 share one）；
* ``search_index`` / ``search_service`` — the index body and the snippet source text;
* ``ask_service`` — retrieval units plus the *legal* citation set;
* ``report_diff`` — the per-block hash used to decide "changed".

Block ids are **derived**, never stored and never produced by the model:
``blockId = f"{sectionId}#{index}"`` where ``index`` is the 0-based position of the
block inside its section (V4-P1-2). ``web/lib/blocks.ts`` mirrors the rule and
``tests/test_block_text.py`` writes the golden map both sides are checked against
(AC-41).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: The one and only inline-citation regex (★V4-P1-3). It used to live in
#: ``exporter``; it moved here so that "export can link it" and "ask understands
#: it" can never drift apart. Matches ``[S1]`` and ``[S1,S2]`` — nothing fuzzy.
CITATION_RE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")


# ── text projection ──────────────────────────────────────────────────────────


def _num(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def block_to_text(block: dict[str, Any]) -> str:
    """Project one content block to readable plain text.

    Must stay **lossless for wording**: the snippet endpoint claims its result is
    a substring of this output (AC-39), so formatting choices here are part of
    that contract.
    """
    if not isinstance(block, dict):  # pragma: no cover - defensive
        return ""
    block_type = block.get("type")

    if block_type in ("paragraph", "heading"):
        return str(block.get("text") or "")

    if block_type == "callout":
        return " ".join(part for part in (str(block.get("title") or ""), str(block.get("text") or "")) if part)

    if block_type == "bullets":
        return "\n".join(str(item.get("text") or "") for item in block.get("items") or [])

    if block_type == "metrics":
        parts: list[str] = []
        for item in block.get("items") or []:
            chunk = f"{item.get('label') or ''} {item.get('value') or ''}".strip()
            if item.get("deltaPct") is not None:
                chunk = f"{chunk}（{_num(item['deltaPct'])}%）"
            parts.append(chunk)
        return "；".join(part for part in parts if part)

    if block_type == "table":
        columns = [str(col.get("label") or col.get("key") or "") for col in block.get("columns") or []]
        lines: list[str] = []
        if block.get("caption"):
            lines.append(str(block["caption"]))
        if columns:
            lines.append(" ".join(columns))
        for row in block.get("rows") or []:
            if isinstance(row, dict):
                lines.append(" ".join("" if value is None else str(value) for value in row.values()))
        return "\n".join(line for line in lines if line)

    if block_type == "chart":
        chart = block.get("chart") or {}
        bits = [str(chart.get("title") or "")]
        bits += [str(series.get("name") or "") for series in chart.get("series") or []]
        if chart.get("note"):
            bits.append(str(chart["note"]))
        return " ".join(bit for bit in bits if bit)

    if block_type == "quote":
        text = str(block.get("text") or "")
        attribution = str(block.get("attribution") or "")
        return f"{text} —— {attribution}" if attribution else text

    if block_type == "timeline":
        lines = []
        for event in block.get("events") or []:
            bits = [str(event.get("date") or ""), str(event.get("title") or "")]
            if event.get("detail"):
                bits.append(str(event["detail"]))
            lines.append(" ".join(bit for bit in bits if bit))
        return "\n".join(lines)

    if block_type == "comparables":
        parts = []
        for peer in block.get("peers") or []:
            bits = [str(peer.get("name") or ""), str(peer.get("symbol") or "")]
            if peer.get("pe") is not None:
                bits.append(f"PE {_num(peer['pe'])}")
            if peer.get("pb") is not None:
                bits.append(f"PB {_num(peer['pb'])}")
            if peer.get("growthPct") is not None:
                bits.append(f"增速 {_num(peer['growthPct'])}%")
            if peer.get("note"):
                bits.append(str(peer["note"]))
            parts.append(" ".join(bit for bit in bits if bit))
        return "；".join(part for part in parts if part)

    if block_type == "scenario":
        parts = []
        for scenario in block.get("scenarios") or []:
            bits = [str(scenario.get("name") or "")]
            if scenario.get("targetPrice") is not None:
                bits.append(f"目标价 {_num(scenario['targetPrice'])}")
            if scenario.get("probability") is not None:
                bits.append(f"概率 {_num(scenario['probability'])}")
            if scenario.get("assumptions"):
                bits.append(str(scenario["assumptions"]))
            parts.append(" ".join(bit for bit in bits if bit))
        return "；".join(part for part in parts if part)

    return ""


# ── citations ────────────────────────────────────────────────────────────────


def source_ids_in_text(text: str) -> list[str]:
    """Extract ``[S1]`` / ``[S1,S2]`` markers, ordered and de-duplicated.

    Deliberately strict: no fuzzy matching, no guessing. An empty list means the
    text carries no citation, which is a fact the caller must be able to trust.
    """
    found: list[str] = []
    for match in CITATION_RE.finditer(text or ""):
        for part in match.group(1).split(","):
            source_id = part.strip()
            if source_id and source_id not in found:
                found.append(source_id)
    return found


def _declared_source_refs(block: dict[str, Any]) -> list[str]:
    """Block-level and nested ``sourceRefs`` — the structured half of citations."""
    refs: list[str] = []

    def add(values: Any) -> None:
        for value in values or []:
            if isinstance(value, str) and value and value not in refs:
                refs.append(value)

    add(block.get("sourceRefs"))
    for key in ("items", "events", "peers", "scenarios"):
        for child in block.get(key) or []:
            if isinstance(child, dict):
                add(child.get("sourceRefs"))
    return refs


def block_source_ids(block: dict[str, Any]) -> tuple[str, ...]:
    """★V4-P1-3: ``sourceRefs`` ∪ inline ``[S1]`` markers, in that order.

    ``paragraph`` — the main prose block — has no ``sourceRefs`` field at all, so
    the inline half is what makes "提问正文里写着的答案" grounded at all.
    """
    merged = list(_declared_source_refs(block))
    for source_id in source_ids_in_text(block_to_text(block)):
        if source_id not in merged:
            merged.append(source_id)
    return tuple(merged)


# ── block ids / document walk ────────────────────────────────────────────────


def block_id_for(section_id: str, index: int) -> str:
    """The one derivation rule (V4-P1-2). Mirrored by ``web/lib/blocks.ts``."""
    return f"{section_id}#{index}"


def block_ids(doc: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """``[(blockId, sectionId, block)]`` in document order."""
    walk: list[tuple[str, str, dict[str, Any]]] = []
    for section in (doc or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("id") or "")
        for index, block in enumerate(section.get("blocks") or []):
            if isinstance(block, dict):
                walk.append((block_id_for(section_id, index), section_id, block))
    return walk


def section_titles(doc: dict[str, Any]) -> dict[str, str]:
    return {
        str(section.get("id") or ""): str(section.get("title") or "")
        for section in (doc or {}).get("sections") or []
        if isinstance(section, dict)
    }


def source_index(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{sourceId: source}`` — the only legal citation set (V4-P1-12)."""
    return {
        str(source.get("id")): source
        for source in (doc or {}).get("sources") or []
        if isinstance(source, dict) and source.get("id")
    }


def legal_source_ids(doc: dict[str, Any]) -> set[str]:
    """A citation may only name ``doc.sources[].id``; sections never count."""
    return set(source_index(doc))


def doc_units(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Retrieval units: one per block, with its text and legal source ids."""
    units: list[dict[str, Any]] = []
    titles = section_titles(doc)
    for block_id, section_id, block in block_ids(doc):
        text = block_to_text(block)
        if not text.strip():
            continue
        units.append(
            {
                "block_id": block_id,
                "section_id": section_id,
                "section_title": titles.get(section_id, ""),
                "block_type": str(block.get("type") or ""),
                "text": text,
                "source_ids": block_source_ids(block),
            }
        )
    return units


def iter_texts(doc: dict[str, Any]) -> Iterable[str]:
    for _, _, block in block_ids(doc):
        text = block_to_text(block)
        if text:
            yield text


__all__ = [
    "CITATION_RE",
    "block_id_for",
    "block_ids",
    "block_source_ids",
    "block_to_text",
    "doc_units",
    "iter_texts",
    "legal_source_ids",
    "section_titles",
    "source_ids_in_text",
    "source_index",
]
