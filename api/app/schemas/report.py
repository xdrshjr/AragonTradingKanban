"""Pydantic mirror of the `aragon.research.report/v1` document (spec §8.3).

The JSON Schema in ``app/agent/schemas/report_doc.schema.json`` is what the CLI
validates against and what ``report_validator`` enforces; these models are the
typed view used by the API layer and by the exporters. ``tests/test_report_schema.py``
keeps the two in step (AC-24).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_ID = "aragon.research.report/v1"

Stance = Literal["buy", "hold", "sell", "watch"]
Severity = Literal["low", "medium", "high"]
Tone = Literal["info", "warn", "insight"]
CalloutTone = Literal["info", "warn", "insight"]
ChartKind = Literal["line", "bar", "area", "scatter", "waterfall"]
ScenarioName = Literal["bear", "base", "bull"]


class StrictModel(BaseModel):
    """`additionalProperties: false` for the whole document, like the schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReportInstrument(StrictModel):
    symbol: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=64)
    market: Literal["CN", "HK", "US"]
    currency: str = Field(min_length=1, max_length=8)


class Rating(StrictModel):
    stance: Stance
    conviction: int = Field(ge=1, le=5)
    horizon: str = Field(min_length=1, max_length=24)
    targetPrice: float | None = None
    upsidePct: float | None = None


class Metric(StrictModel):
    label: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=32)
    deltaPct: float | None = None
    tone: Literal["pos", "neg", "neutral"] | None = None
    sourceRefs: list[str] = Field(default_factory=list, max_length=8)


# ── blocks ────────────────────────────────────────────────────────────────


class ParagraphBlock(StrictModel):
    type: Literal["paragraph"]
    text: str = Field(min_length=1, max_length=4000)


class HeadingBlock(StrictModel):
    type: Literal["heading"]
    level: Literal[2, 3, 4]
    text: str = Field(min_length=1, max_length=120)


class BulletItem(StrictModel):
    text: str = Field(min_length=1, max_length=600)
    sourceRefs: list[str] = Field(default_factory=list, max_length=8)


class BulletsBlock(StrictModel):
    type: Literal["bullets"]
    items: list[BulletItem] = Field(min_length=1, max_length=20)


class CalloutBlock(StrictModel):
    type: Literal["callout"]
    tone: CalloutTone
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)


class MetricsBlock(StrictModel):
    type: Literal["metrics"]
    items: list[Metric] = Field(min_length=1, max_length=12)


class TableColumn(StrictModel):
    key: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=48)
    align: Literal["left", "right", "center"] | None = None


class TableBlock(StrictModel):
    type: Literal["table"]
    columns: list[TableColumn] = Field(min_length=1, max_length=10)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=80)
    caption: str | None = Field(default=None, max_length=200)


class ChartAxis(StrictModel):
    type: Literal["category", "time", "value"] = "category"
    labels: list[str] = Field(default_factory=list, max_length=200)


class ChartSeries(StrictModel):
    name: str = Field(min_length=1, max_length=48)
    data: list[float | None] = Field(min_length=1, max_length=200)


class ChartPayload(StrictModel):
    kind: ChartKind
    title: str = Field(min_length=1, max_length=120)
    xAxis: ChartAxis
    series: list[ChartSeries] = Field(min_length=1, max_length=6)
    unit: str | None = Field(default=None, max_length=16)
    note: str | None = Field(default=None, max_length=300)


class ChartBlock(StrictModel):
    type: Literal["chart"]
    chart: ChartPayload


class QuoteBlock(StrictModel):
    type: Literal["quote"]
    text: str = Field(min_length=1, max_length=800)
    attribution: str = Field(min_length=1, max_length=120)
    sourceRefs: list[str] = Field(min_length=1, max_length=8)


class TimelineEvent(StrictModel):
    date: str = Field(min_length=8, max_length=24)
    title: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=600)
    sourceRefs: list[str] = Field(default_factory=list, max_length=8)


class TimelineBlock(StrictModel):
    type: Literal["timeline"]
    events: list[TimelineEvent] = Field(min_length=1, max_length=20)


class Peer(StrictModel):
    name: str = Field(min_length=1, max_length=48)
    symbol: str = Field(min_length=1, max_length=24)
    pe: float | None = None
    pb: float | None = None
    growthPct: float | None = None
    note: str | None = Field(default=None, max_length=200)


class ComparablesBlock(StrictModel):
    type: Literal["comparables"]
    peers: list[Peer] = Field(min_length=1, max_length=10)


class Scenario(StrictModel):
    name: ScenarioName
    targetPrice: float
    probability: float = Field(ge=0, le=1)
    assumptions: str = Field(min_length=1, max_length=400)


class ScenarioBlock(StrictModel):
    type: Literal["scenario"]
    scenarios: list[Scenario] = Field(min_length=1, max_length=3)


class DividerBlock(StrictModel):
    type: Literal["divider"]


Block = Annotated[
    ParagraphBlock
    | HeadingBlock
    | BulletsBlock
    | CalloutBlock
    | MetricsBlock
    | TableBlock
    | ChartBlock
    | QuoteBlock
    | TimelineBlock
    | ComparablesBlock
    | ScenarioBlock
    | DividerBlock,
    Field(discriminator="type"),
]

BLOCK_TYPES: tuple[str, ...] = (
    "paragraph",
    "heading",
    "bullets",
    "callout",
    "metrics",
    "table",
    "chart",
    "quote",
    "timeline",
    "comparables",
    "scenario",
    "divider",
)


class Section(StrictModel):
    id: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=64)
    blocks: list[Block] = Field(default_factory=list, max_length=40)


class Risk(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    severity: Severity
    trigger: str = Field(min_length=1, max_length=200)
    mitigant: str | None = Field(default=None, max_length=200)
    sourceRefs: list[str] = Field(default_factory=list, max_length=8)


class Source(StrictModel):
    id: str = Field(min_length=1, max_length=8)
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=80)
    publishedAt: str | None = Field(default=None, max_length=24)
    url: str | None = Field(default=None, max_length=500)
    retrievedAt: str | None = Field(default=None, max_length=24)


class ReportDoc(StrictModel):
    schema_: Literal["aragon.research.report/v1"] = Field(default=SCHEMA_ID, alias="schema")
    provisional: bool = False
    instrument: ReportInstrument
    asOf: str = Field(min_length=10, max_length=40)
    headline: str = Field(min_length=4, max_length=160)
    rating: Rating
    summary: str = Field(min_length=10, max_length=600)
    keyMetrics: list[Metric] = Field(default_factory=list, max_length=12)
    sections: list[Section] = Field(min_length=1, max_length=8)
    risks: list[Risk] = Field(default_factory=list, max_length=10)
    sources: list[Source] = Field(default_factory=list, max_length=40)
    disclaimer: str = Field(min_length=4, max_length=300)


# ── API-facing wrappers ───────────────────────────────────────────────────


class ReportQuality(BaseModel):
    groundedRatio: float = 1.0
    missingSections: list[str] = Field(default_factory=list)
    prunedBlocks: int = 0
    #: How many prose numerals could not be traced into the snapshot (§16 verdict
    #: 4 requires the UI to say "N 处数字未能在快照中找到来源").
    ungroundedCount: int = 0
    source: Literal["aragon", "mock", "aragon-text"] = "aragon"


class ReportSummary(BaseModel):
    """Row shape for the version switcher and the rail (never the full doc)."""

    id: str
    instrumentId: str
    version: int
    status: Literal["ready", "degraded", "provisional", "superseded"]
    title: str
    headline: str = ""
    stance: Stance | None = None
    asOf: str
    generatedAt: str
    isLatest: bool
    pinned: bool
    archived: bool
    provisional: bool = False
    groundedRatio: float | None = None
    quality: ReportQuality = Field(default_factory=ReportQuality)
    provider: str | None = None
    model: str | None = None
    costUsd: float | None = None
    costKnown: bool = False
    tokensIn: int | None = None
    tokensOut: int | None = None
    durationMs: int | None = None
    docBytes: int = 0


class ReportDetail(BaseModel):
    report: ReportSummary
    doc: ReportDoc


class ReportListResponse(BaseModel):
    items: list[ReportSummary]
    total: int


class ReportPatch(BaseModel):
    pinned: bool | None = None
    archived: bool | None = None
    setLatest: bool | None = None


class CreateReportRequest(BaseModel):
    reason: Literal["manual", "refresh"] = "manual"
    force: bool = False


SECTION_IDS = ("thesis", "fundamentals", "valuation", "risks", "catalysts")
