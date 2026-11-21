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
    #: ★V4-P1-1: which way is *good* for this metric. Optional and defaulted to
    #: `neutral` so historical documents stay valid — and so an unlabelled metric
    #: renders grey instead of being guessed green.
    polarity: Literal["bullish", "bearish", "neutral"] | None = None
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


class GroundingSample(BaseModel):
    """一个未接地的数字，带可跳转的坐标（AC-48：`blockId` 必须能用于 `?block=`）。"""

    blockId: str
    path: str
    raw: str
    text: str = ""


class GroundingQuality(BaseModel):
    """★v6 G1：`groundedRatio` 背后的可定位报告（`numeric_claims.GroundingReport`）。

    `skipped == "insufficient_sample"` **优先于** `ratio >= 0.9`（★V6-P2-8）：
    一份只有 3 个数字的报告不该被展示成"已核验 3/3"，那是虚假的安心。
    """

    factClaims: int = 0
    factMissing: int = 0
    #: 全量未接地数；`samples` 只放前 20 个块（R-22）。
    totalMissing: int = 0
    #: ★A4：按**值**去重的计数（`factMissing` 是引用次数）。面板要能说
    #: 「43 个不同数值，出现 141 次」，否则读者把"141 处"读成 141 个错误。
    distinctMissing: int = 0
    distinctClaims: int = 0
    #: ★R3-1：`samples` 按 `blockId` 去重 → 块级/值级两个分母，面板的两句话各用一个。
    sampledBlocks: int = 0
    sampledValues: int = 0
    projectionClaims: int = 0
    derivedClaims: int = 0
    identifierClaims: int = 0
    skipped: str | None = None
    samples: list[GroundingSample] = Field(default_factory=list)
    #: ★A6：口径版本与输入指纹。跨口径（或缺少 `rules`）时 UI 不给可比百分比。
    rules: str | None = None
    docHash: str | None = None
    snapshotHash: str | None = None


class EngineInfo(BaseModel):
    """★v6 G4：`quality.engine` —— **只有四项**。

    `model` / `provider` 的权威是 `reports.model|provider` 列与
    `capabilities.aragon.*`；写第三份副本必然漂移（V6-P1-3 / O-13）。
    """

    fingerprint: str
    cliVersion: str | None = None
    allowTools: list[str] = Field(default_factory=list)
    schemaBytes: int = 0


class SalvageQuality(BaseModel):
    """★v9（K5）：`quality.salvage` —— 抢救记录（进了 `quality` 信封，**不进**报告文档）。

    L6 的硬边界：`report_doc.schema.json` 顶层 `additionalProperties: false`，12 个键
    一个字都不许加；所以"这份报告是抢救来的"这个事实只能落在我们自己的 quality 里。
    三个计数器互不重叠（未知块归 `prunedBlocks`，见 `SalvageReport` 的文档）。
    """

    used: bool = False
    droppedKeys: int = 0
    droppedSections: int = 0
    droppedBlocks: int = 0
    #: ★v12（S3 / V11-P2-2）：`SalvageReport.as_payload()` 过去漏发 `prunedBlocks`，
    #: 于是"裁掉未知块"这件事写进了一个前端读不到的地方。三处同步补：报告键、响应模型、
    #: `web/lib/types.ts`。
    prunedBlocks: int = 0
    #: ★v12（S3）：格式收敛了几处（与 dropped* 不重叠：那些是丢了，这个是被截短）。
    clamped: int = 0
    #: ★v12（S4）：从正文还原的方式（`json` / `json_repaired`）。`None` ⇒ 没走 L1.5。
    how: str | None = None
    reasons: list[str] = Field(default_factory=list)


class CostQuality(BaseModel):
    """★v7 H4：`quality.cost` 的三态（`reported | estimated | unknown`）。"""

    source: str = "unknown"
    reportedUsd: float | None = None
    estimatedUsd: float | None = None
    usd: float | None = None
    priceVersion: str | None = None


class PromptQuality(BaseModel):
    """★v7 H5：`quality.prompt` —— 提示词指纹（只做展示与追溯，**不进**引擎指纹）。

    ★v14（§23.3.4b / ★P1-11）：追加 `baseReportId` / `baseVersion`，语义是"这一版是基于
    哪一版更新的"。两个键**恒在、值可为 `None`**（独立生成 ⇒ 两个都是 `None`）。

    只加响应字段**不算做完了**：生产者是 `report_validator.prompt_quality()`，它必须
    同时给这两个键赋值，否则就是第三个"声明了但没有生产者"的空字段（R-77）。
    `tests/test_prompt_quality_keys.py` 直接跑一次真实函数来盯这件事。
    """

    version: int | None = None
    sha1: str | None = None
    bytes: int = 0
    #: ★v14：用户点「基于此版本更新」时选的那一版。取值是**用户的选择**，即使那一版
    #: 取不出结构（provisional / corrupt）也照记原值——事后要能看出"用户确实选了它"。
    baseReportId: str | None = None
    #: ★v14：上面那一版的 `version` 号（省一次前端查询；缺失 ⇒ 显示"独立生成"）。
    baseVersion: int | None = None


class ClampQuality(BaseModel):
    """★v12（S3 / C-48）：`quality.clamp` —— 格式收敛记录（纯格式问题，不是事实问题）。

    三个纪律：

    1. **只标不改数**（S-4）：clamp 只处理 schema **明说**的上界（"模型写得比我们允许的
       更长"），真事实问题（编造数字）由 S5 的接地口径负责，两者不重叠。
    2. `count > 0` **必须**降 `status = degraded`：读者有权知道这份报告是被截过的。
    3. 老报告没有这个键（v1–v12 的 `quality` 不回填，§22.6.3），所以类型是可缺失的。
    """

    count: int = 0
    fields: list[str] = Field(default_factory=list)


class WorkflowStepCounts(BaseModel):
    total: int = 0
    started: int = 0
    done: int = 0
    skipped: int = 0


class WorkflowGateCounts(BaseModel):
    total: int = 0
    done: int = 0


class WorkflowQuality(BaseModel):
    """report-workflow-graph §3.6.5: `quality.workflow` — which research flow produced this report
    and how closely the agent reported following it (an observation, never a gate)."""

    templateId: str | None = None
    templateName: str
    version: int | None = None
    versionId: str | None = None
    hash: str
    source: Literal["published", "builtin-code"] = "published"
    fallbackReason: str | None = None
    steps: WorkflowStepCounts = Field(default_factory=WorkflowStepCounts)
    gates: WorkflowGateCounts = Field(default_factory=WorkflowGateCounts)
    adherence: Literal["full", "partial", "none", "unreported"] = "none"
    markersSeen: int = 0
    unknownMarkers: int = 0


class ReportQuality(BaseModel):
    groundedRatio: float = 1.0
    missingSections: list[str] = Field(default_factory=list)
    prunedBlocks: int = 0
    #: How many prose numerals could not be traced into the snapshot (§16 verdict
    #: 4 requires the UI to say "N 处数字未能在快照中找到来源").
    ungroundedCount: int = 0
    source: Literal["aragon", "mock", "aragon-text"] = "aragon"
    #: ★v6: both optional —老报告没有它们，读路径必须按"可缺失"处理（不回填）。
    grounding: GroundingQuality | None = None
    engine: EngineInfo | None = None
    #: ★v9（V10-P2-7，顺带发现的存量缺陷）：`cost` / `prompt` 自 v7 起就一直写在
    #: `ValidationOutcome.quality` 里，却**从未**在响应模型上声明 —— pydantic 静默丢弃，
    #: 于是 H4/H5 的字段一次都没到过前端（"第二道门"的第三次）。本轮补齐三个字段，
    #: 并加一条反射守卫用例（`test_every_quality_key_has_a_field`）：下次再加键时它先红。
    cost: CostQuality | None = None
    prompt: PromptQuality | None = None
    salvage: SalvageQuality | None = None
    #: ★v12（S3 / AC-117 / C-48）：格式收敛记录。`None` = 没有收敛（或老报告）。
    #: 反射守卫 `test_every_quality_key_has_a_response_field` 会盯住这个键。
    clamp: ClampQuality | None = None
    #: report-workflow-graph: absent on reports made before the feature / with `workflow.enabled=0`.
    workflow: WorkflowQuality | None = None


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
    # ★v15（§24.3.9(c)）：按**请求者视角**由服务端算好，前端不自行推断。
    #: 作者用户名；已注销 → "已注销用户"；历史/系统数据 → `None`。
    author: str | None = None
    #: 请求者能否改/删/恢复这份报告（拥有者或 `report.manage_any`）。
    canManage: bool = False
    #: 请求者能否置顶 / 设为最新（只有 `report.manage_any`，RV-10）。
    canCurate: bool = False


class ReportDetail(BaseModel):
    report: ReportSummary
    doc: ReportDoc
    #: ★v14（U4 / §23.5.4）：`"ok" | "corrupt"`。默认 `ok` ⇒ 老后端缺这个键时前端按
    #: "正常"处理（向后兼容），而损坏壳仍然可读（12 种块渲染器一行不用改）。
    #: 单数真源是 `report_service.load_doc_safe()`：列表用 SQL `json_valid()` 算，
    #: 详情用 Python 解析算，**语义相同、成本不同**（★P1-2）。
    integrity: Literal["ok", "corrupt"] = "ok"


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
    #: ★v14（§23.3.4a）：内容指纹与最新非临时版相同时**不新增版本**。默认开——这正是
    #: "更新即复用"的那一半；关掉是逃生阀（用户明确要求"再来一版"）。
    #: 响应体只追加 `"dedupe": true`，它说的是"这次请求**请求了**去重"，不是结论；
    #: 最终结论只有一个来源：`run.done.deduped`。
    dedupe: bool = True
    #: ★v14（§23.3.4b）：把上一版的**结构摘要**注入任务提示词（≤2048 B，只给章节标题与
    #: 指标标签，不给数值）。校验失败 → 422 `base_report_mismatch`。
    baseReportId: str | None = None


# ── version diff (§18.5.4) ──────────────────────────────────────────────────


class MetricDelta(BaseModel):
    label: str
    before: float | None = None
    after: float | None = None
    deltaPct: float | None = None
    direction: Literal["up", "down", "flat"] = "flat"
    polarity: Literal["bullish", "bearish", "neutral"] = "neutral"
    #: Server-decided semantic colour: `direction × polarity` (never guessed client-side).
    tone: Literal["up", "down", "neutral"] = "neutral"


class BlockChange(BaseModel):
    sectionId: str
    blockType: str
    change: Literal["added", "removed", "modified"]
    blockIdA: str | None = None
    blockIdB: str | None = None


class RatingSnapshot(BaseModel):
    stance: Stance | None = None
    target: str | None = None


class RatingChange(BaseModel):
    from_: RatingSnapshot = Field(alias="from")
    to: RatingSnapshot

    model_config = ConfigDict(populate_by_name=True)


class StatusChange(BaseModel):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class Comparability(BaseModel):
    """★v6 G4：两版的引擎指纹是否可比。

    `same` 三态：`true`（同指纹）/ `false`（不同）/ `null`（至少一方缺指纹，**不猜**）。
    `deltaUnaffected` 是给 UI 与测试看的显式承诺：横幅只是提示，不改变任何 delta
    （O-10 / AC-58 —— 这条必须靠测试证明，不能靠字段自证）。
    """

    same: bool | None = None
    changed: list[str] = Field(default_factory=list)
    deltaUnaffected: bool = True
    note: str = ""


class DiffResult(BaseModel):
    a: str
    b: str
    statusChanged: StatusChange | None = None
    ratingChanged: RatingChange | None = None
    metricsDelta: list[MetricDelta] = Field(default_factory=list)
    sectionsAdded: list[str] = Field(default_factory=list)
    sectionsRemoved: list[str] = Field(default_factory=list)
    blocksChanged: list[BlockChange] = Field(default_factory=list)
    summary: str = ""
    #: ★v6：**追加键**。`a` / `b` / `summary: str` 一个都不改名（改名会让 /diff 直接 500）。
    comparability: Comparability | None = None


#: Only these three statuses describe **report quality**; `superseded` only says
#: "a newer version exists", which is true for every adjacent pair and therefore
#: says nothing. Diffing it would make `statusChanged` always non-null (§18.5.4).
QUALITY_STATUSES = ("ready", "degraded", "provisional")

#: What a status *means* for a version comparison: an incumbent that has been
#: superseded was a normal (ready) report before the newer version arrived.
QUALITY_STATUS_EQUIVALENT = {
    "ready": "ready",
    "superseded": "ready",
    "degraded": "degraded",
    "provisional": "provisional",
}

SECTION_IDS = ("thesis", "fundamentals", "valuation", "risks", "catalysts")
