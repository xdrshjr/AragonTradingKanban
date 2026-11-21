"""Graph Engineering core (spec §3.5): compile a `WorkflowDoc` into the plan the agent reads.

`compile_workflow` is a **pure, deterministic** function — same document + same context gives a
byte-identical `workflow.md` (no clock, no randomness, no environment). It refuses graphs that
cannot run (`WorkflowCompileError`); the runtime answers that with a fallback to the built-in
workflow, never with a failed generation (§3.6.6).

Placeholder values come from third-party data (instrument name, sector), so they are sanitised
and interpolated in a **single pass**: a value containing a double-brace token stays literal, and
a backslash before the opening braces is the only escape. The escape uses a capture group, not a
lookbehind (the frontend mirror has to run on old Safari).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..schemas.workflow import Node, ValidationReport, WorkflowDoc
from . import workflow_contract as C
from .workflow_plan_text import (
    PlanEntry,
    PlanItem,
    PlanSpec,
    render_inline_index,
    render_plan,
)
from .workflow_validator import Analysis, Ceilings, analyze, validate

__all__ = [
    "CompileContext",
    "CompiledWorkflow",
    "PlanItem",
    "WorkflowCompileError",
    "compile_workflow",
    "interpolate",
    "sample_context",
    "validate_with_plan",
]

_PLACEHOLDER_RE = re.compile(r"(\\)?\{\{([^{}]*)\}\}")
_PREVIEW_NAME = "预览"


@dataclass(frozen=True)
class CompileContext:
    """System placeholder values for one run (one context per run)."""

    symbol: str
    name: str
    market: str
    currency: str
    sector: str
    as_of: str
    timeframe: str


def sample_context() -> CompileContext:
    raw = C.SAMPLE_CONTEXT
    return CompileContext(
        symbol=raw["symbol"],
        name=raw["name"],
        market=raw["market"],
        currency=raw["currency"],
        sector=raw["sector"],
        as_of=raw["asOf"],
        timeframe=raw["timeframe"],
    )


@dataclass(frozen=True)
class CompiledWorkflow:
    items: tuple[PlanItem, ...]
    levels: tuple[tuple[str, ...], ...]
    plan_md: str
    inline_index: str
    coverage: dict[str, tuple[str, ...]]
    sha1: str
    key_to_node: dict[str, str]
    template_name: str
    version: int | None


class WorkflowCompileError(Exception):
    """The graph cannot be turned into a runnable plan; `report` says why."""

    def __init__(self, report: ValidationReport) -> None:
        codes = ",".join(i.code for i in report.issues if i.severity == "error") or "not_publishable"
        super().__init__(f"workflow cannot be compiled: {codes}")
        self.report = report


# ── placeholders ─────────────────────────────────────────────────────────────


def _clean_value(value: str) -> str:
    stripped = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cf"))
    return stripped.replace("{{", "").replace("}}", "")[: C.PLACEHOLDER_VALUE_MAX].strip()


def _placeholder_values(context: CompileContext) -> dict[str, str]:
    raw = {
        "instrument.symbol": context.symbol,
        "instrument.name": context.name,
        "instrument.market": context.market,
        "instrument.currency": context.currency,
        "instrument.sector": context.sector,
        "asOf": context.as_of,
        "timeframe": context.timeframe,
    }
    return {name: _clean_value(value) for name, value in raw.items()}


def interpolate(text: str, values: dict[str, str]) -> str:
    """Single-pass substitution of double-brace placeholders; an escaped one yields the literal."""

    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            return match.group(0)[1:]
        name = match.group(2).strip()
        return values[name] if name in values else match.group(0)

    return _PLACEHOLDER_RE.sub(replace, text)


# ── items ────────────────────────────────────────────────────────────────────


def _assign_keys(analysis: Analysis) -> dict[str, str]:
    """`S1…`/`G1…` by (phase, document order); steps and gates are numbered separately."""
    compiled = [n for n in analysis.nodes.values() if n.kind in ("step", "gate")]
    compiled.sort(key=lambda n: (analysis.levels[n.id], analysis.order[n.id]))
    counters = {"step": 0, "gate": 0}
    keys: dict[str, str] = {}
    for node in compiled:
        counters[node.kind] += 1
        keys[node.id] = f"{'S' if node.kind == 'step' else 'G'}{counters[node.kind]}"
    return keys


def _text(node: Node, key: str) -> str:
    value = node.config.get(key)
    return value if isinstance(value, str) else ""


def _key_order(key: str) -> tuple[str, int]:
    return key[0], int(key[1:])


def _make_item(node: Node, key: str, keys: dict[str, str], analysis: Analysis) -> PlanItem:
    upstream = tuple(sorted((keys[p] for p in analysis.pred[node.id] if p in keys), key=_key_order))
    is_step = node.kind == "step"
    return PlanItem(
        key=key,
        node_id=node.id,
        kind=node.kind,  # type: ignore[arg-type]
        title=node.title.strip(),
        role=node.config.get("role") if is_step else None,
        level=analysis.levels[node.id],
        upstream=upstream,
        sections=tuple(node.config.get("sections") or ()) if is_step else (),
        files=tuple(node.config.get("files") or ()) if is_step else (),
    )


def _build_entries(analysis: Analysis, keys: dict[str, str], values: dict[str, str]) -> tuple[PlanEntry, ...]:
    ordered = sorted(keys, key=lambda nid: (analysis.levels[nid], analysis.order[nid]))
    entries: list[PlanEntry] = []
    for node_id in ordered:
        node = analysis.nodes[node_id]
        entries.append(
            PlanEntry(
                item=_make_item(node, keys[node_id], keys, analysis),
                goal=interpolate(_text(node, "prompt"), values),
                criteria=interpolate(_text(node, "successCriteria"), values),
                checklist=interpolate(_text(node, "checklist"), values),
            )
        )
    return tuple(entries)


def _levels_of(entries: tuple[PlanEntry, ...]) -> tuple[tuple[str, ...], ...]:
    phases: dict[int, list[str]] = {}
    for entry in entries:
        phases.setdefault(entry.item.level, []).append(entry.item.key)
    return tuple(tuple(phases[level]) for level in sorted(phases))


def _coverage_of(entries: tuple[PlanEntry, ...]) -> dict[str, tuple[str, ...]]:
    table: dict[str, list[str]] = {section: [] for section in C.SECTIONS}
    for entry in entries:
        for section in entry.item.sections:
            if section in table:
                table[section].append(entry.item.key)
    return {section: tuple(keys) for section, keys in table.items()}


# ── public ───────────────────────────────────────────────────────────────────


def compile_workflow(
    doc: WorkflowDoc,
    context: CompileContext,
    *,
    template_name: str,
    version: int | None,
    ceilings: Ceilings | None = None,
) -> CompiledWorkflow:
    """Compile `doc` for one run. Raises `WorkflowCompileError` for anything not publishable or a
    plan over `MAX_PLAN_BYTES` — never a best-effort plan."""
    report = validate(doc, ceilings=ceilings)
    if not report.publishable:
        raise WorkflowCompileError(report)
    analysis = analyze(doc)
    keys = _assign_keys(analysis)
    entries = _build_entries(analysis, keys, _placeholder_values(context))
    spec = PlanSpec(
        template_name=template_name,
        version=version,
        markers=doc.policy.progressMarkers,
        entries=entries,
        levels=_levels_of(entries),
        coverage=_coverage_of(entries),
    )
    plan_md, sha1 = render_plan(spec)
    plan_bytes = len(plan_md.encode("utf-8"))
    if plan_bytes > C.MAX_PLAN_BYTES:
        raise WorkflowCompileError(validate(doc, ceilings=ceilings, plan_bytes=plan_bytes))
    return CompiledWorkflow(
        items=tuple(e.item for e in entries),
        levels=spec.levels,
        plan_md=plan_md,
        inline_index=render_inline_index(spec),
        coverage=spec.coverage,
        sha1=sha1,
        key_to_node={e.item.key: e.item.node_id for e in entries},
        template_name=template_name,
        version=version,
    )


def validate_with_plan(doc: WorkflowDoc, *, ceilings: Ceilings | None = None) -> ValidationReport:
    """The report the service layer uses everywhere: structural validation plus the plan-size
    checks (`plan_too_large` / `plan_large`), measured by compiling with the sample context."""
    try:
        compiled = compile_workflow(doc, sample_context(), template_name=_PREVIEW_NAME, version=None, ceilings=ceilings)
    except WorkflowCompileError as error:
        return error.report
    return validate(doc, ceilings=ceilings, plan_bytes=len(compiled.plan_md.encode("utf-8")))
