/**
 * Hand-mirrored mirror of `api/app/schemas/*` (spec §4).
 *
 * No code generation on purpose: the toolchain risk is not worth it, and a
 * contract test (`tests/contract.test.ts`) fails if the two drift apart on the
 * fields that actually cross the wire.
 */

export type Market = 'CN' | 'HK' | 'US';
export type Stance = 'buy' | 'hold' | 'sell' | 'watch';
export type Severity = 'low' | 'medium' | 'high';
export type Tone = 'info' | 'warn' | 'insight';
export type MetricTone = 'pos' | 'neg' | 'neutral';
export type ChartKind = 'line' | 'bar' | 'area' | 'scatter' | 'waterfall';
export type ReportStatus = 'ready' | 'degraded' | 'provisional' | 'superseded';
export type RunStatus =
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timeout'
  | 'budget_exceeded';

export interface Instrument {
  id: string;
  symbol: string;
  name: string;
  market: Market;
  exchange?: string | null;
  currency: string;
  sector?: string | null;
  tz: string;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface SymbolSearchHit {
  symbol: string;
  name: string;
  market: Market;
  currency: string;
  score: number;
  existing: boolean;
}

export interface ResolveResponse {
  instrument: Instrument;
  created: boolean;
  needsConfirm: boolean;
  candidates: SymbolSearchHit[];
}

export interface Quote {
  instrumentId: string;
  last?: number | null;
  changePct?: number | null;
  prevClose?: number | null;
  dayHigh?: number | null;
  dayLow?: number | null;
  volume?: number | null;
  updatedAt: string;
  source?: string | null;
  /** ★§6.1 #17：这次回答来自缓存而不是实时源（原本塞在 `source` 字符串里）。 */
  cached?: boolean;
}

export interface Bar {
  ts: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  amount?: number | null;
}

export type IndicatorSeries = (number | null)[];

export interface Indicators {
  ma5: IndicatorSeries;
  ma10: IndicatorSeries;
  ma20: IndicatorSeries;
  ma60: IndicatorSeries;
  bollUpper: IndicatorSeries;
  bollLower: IndicatorSeries;
  macd: IndicatorSeries;
  macdSignal: IndicatorSeries;
  rsi14: IndicatorSeries;
  ema12?: IndicatorSeries;
  ema26?: IndicatorSeries;
  bollMiddle?: IndicatorSeries;
  macdHistogram?: IndicatorSeries;
  kdjK?: IndicatorSeries;
  kdjD?: IndicatorSeries;
  kdjJ?: IndicatorSeries;
  atr14?: IndicatorSeries;
  volumeMa5?: IndicatorSeries;
  volumeMa10?: IndicatorSeries;
}

export type BarsProfile = 'standard' | 'chart';

/** Mirror of the backend chart/v1 contract; dates are trading dates. */
export interface BarStats {
  ts: number;
  periodStart: string;
  periodEnd: string;
  leadingPartial: boolean;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  amplitudePct: number | null;
}

export interface ChartDetails {
  version: 'chart/v1';
  stats: BarStats[];
  calculationBarsCount: number;
  warmupBarsCount: number;
  historyLimited: boolean;
  volumeUnit: 'share' | 'lot' | 'unit' | 'unknown';
  volumeUnitSize: number | null;
  amountCurrency: string;
  effectiveAdjust: 'none' | 'qfq' | 'hfq' | 'auto' | 'synthetic';
  returnBasis: 'adjacent_close';
  indicatorConvention: 'aragon-chart-v1';
}

export interface BarsMeta {
  source: string;
  barsCount: number;
  fromTs?: number | null;
  toTs?: number | null;
  adjust: string;
  timeframe: string;
  cached: boolean;
  /** 只表示"最新一根落后于应有会话"；原因走 `reasonCode`（★§R-7）。 */
  stale: boolean;
  /**
   * `ok / uncovered / provider_fallback / mixed_source / legacy_generation / divergent_source`。
   * 一个回答只带一个码，调用方不必匹配中文文案就能分辨"缓存没覆盖 / 序列曾经混源 /
   * 报价在别处"。
   */
  reasonCode?: string;
  warnings: string[];
}

/* ── chart view (the window the chart is showing) ─────────────────────────── */

export type ChartTimeframe = '1d' | '1w' | '1m';
export type ChartRange = '1m' | '3m' | '6m' | '1y' | '3y';
export type ChartAdjust = 'qfq' | 'none' | 'hfq';

/**
 * ★PCD-3：图表当前的口径。它由 `WorkspaceClient` 拥有（因为要写进 URL，还要让左栏
 * 的**选中行**跟着它走），所以 `PricePanel` 既能受控也能自己管（`price-panel.test.tsx`
 * 就是不带这套 props 直接渲染的）。
 */
export interface ChartView {
  timeframe: ChartTimeframe;
  range: ChartRange;
  adjust: ChartAdjust;
}

export interface BarsResponse {
  instrument: Instrument;
  bars: Bar[];
  indicators: Partial<Indicators>;
  meta: BarsMeta;
  chart?: ChartDetails | null;
}

/* ── report document (aragon.research.report/v1) ─────────────────────────── */

export interface SourceRef {
  id: string;
  title: string;
  publisher: string;
  publishedAt?: string | null;
  url?: string | null;
  retrievedAt?: string | null;
}

export interface Metric {
  label: string;
  value: string;
  deltaPct?: number | null;
  tone?: MetricTone | null;
  /**
   * ★V4-P1-1: which direction is *good* for this metric. Optional — an unlabelled
   * metric is grey on purpose, never guessed green (AC-42).
   */
  polarity?: 'bullish' | 'bearish' | 'neutral' | null;
  sourceRefs?: string[];
}

export interface Rating {
  stance: Stance;
  conviction: number;
  horizon: string;
  targetPrice?: number | null;
  upsidePct?: number | null;
}

export interface BulletItem {
  text: string;
  sourceRefs?: string[];
}

export interface TableColumn {
  key: string;
  label: string;
  align?: 'left' | 'right' | 'center' | null;
}

export interface ChartSeries {
  name: string;
  data: (number | null)[];
}

export interface ChartPayload {
  kind: ChartKind;
  title: string;
  xAxis: { type: 'category' | 'time' | 'value'; labels?: string[] | null };
  series: ChartSeries[];
  unit?: string | null;
  note?: string | null;
}

export interface TimelineEvent {
  date: string;
  title: string;
  detail?: string | null;
  sourceRefs?: string[];
}

export interface Peer {
  name: string;
  symbol: string;
  pe?: number | null;
  pb?: number | null;
  growthPct?: number | null;
  note?: string | null;
}

export interface Scenario {
  name: 'bear' | 'base' | 'bull';
  targetPrice: number;
  probability: number;
  assumptions: string;
}

export interface ParagraphBlock {
  type: 'paragraph';
  text: string;
}

export interface HeadingBlock {
  type: 'heading';
  level: 2 | 3 | 4;
  text: string;
}

export interface BulletsBlock {
  type: 'bullets';
  items: BulletItem[];
}

export interface CalloutBlock {
  type: 'callout';
  tone: Tone;
  title: string;
  text: string;
}

export interface MetricsBlock {
  type: 'metrics';
  items: Metric[];
}

export interface TableBlock {
  type: 'table';
  columns: TableColumn[];
  rows: Record<string, string | number | null>[];
  caption?: string | null;
}

export interface ChartBlock {
  type: 'chart';
  chart: ChartPayload;
}

export interface QuoteBlock {
  type: 'quote';
  text: string;
  attribution: string;
  sourceRefs: string[];
}

export interface TimelineBlock {
  type: 'timeline';
  events: TimelineEvent[];
}

export interface ComparablesBlock {
  type: 'comparables';
  peers: Peer[];
}

export interface ScenarioBlock {
  type: 'scenario';
  scenarios: Scenario[];
}

export interface DividerBlock {
  type: 'divider';
}

export type Block =
  | ParagraphBlock
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
  | DividerBlock;

export type BlockType = Block['type'];

export const BLOCK_TYPES: readonly BlockType[] = [
  'paragraph',
  'heading',
  'bullets',
  'callout',
  'metrics',
  'table',
  'chart',
  'quote',
  'timeline',
  'comparables',
  'scenario',
  'divider',
] as const;

export interface Section {
  id: string;
  title: string;
  blocks: Block[];
}

export interface Risk {
  title: string;
  severity: Severity;
  trigger: string;
  mitigant?: string | null;
  sourceRefs?: string[];
}

export interface ReportDoc {
  schema: 'aragon.research.report/v1';
  provisional?: boolean;
  instrument: { symbol: string; name: string; market: Market; currency: string };
  asOf: string;
  headline: string;
  rating: Rating;
  summary: string;
  keyMetrics: Metric[];
  sections: Section[];
  risks: Risk[];
  sources: SourceRef[];
  disclaimer: string;
}

export interface ReportQuality {
  groundedRatio: number;
  missingSections: string[];
  prunedBlocks: number;
  /** Prose numerals that could not be traced into the snapshot (§16 verdict 4). */
  ungroundedCount?: number;
  source: 'aragon' | 'mock' | 'aragon-text';
  /**
   * ★v6 G1: the locatable half of `groundedRatio`. Absent on reports generated
   * before v5 — and an absent key means "don't render the badge", never "0 issues".
   */
  grounding?: GroundingQuality | null;
  /** ★v6 G4: the engine that produced this version. No `model`/`provider` here. */
  engine?: EngineInfo | null;
  /** ★v7 H4: 三态成本。老报告没有这个键 → 界面按「早期版本」渲染，不回填。 */
  cost?: QualityCost | null;
  /** ★v7 H5: 当次提示词的版本与指纹。**不进**引擎指纹（裁决 H-5）。 */
  prompt?: QualityPrompt | null;
  /**
   * ★v9（K5）：抢救记录。`undefined` = 没走抢救（老报告也没有这个键，**不回填**）。
   * 三个计数器互不重叠：未知块归 `prunedBlocks`，这里只数 salvage 自己丢的东西。
   */
  salvage?: QualitySalvage | null;
  /**
   * ★v12（S3 / §22.6.3）：**格式收敛**记录。`undefined` = 没收敛，或这是老报告
   * （v1–v12 的 `quality` 按纪律**不回填**）。读法写死为 `(q.clamp ?? {count: 0})`。
   */
  clamp?: QualityClamp | null;
  /**
   * ★v16（研究流程图 §3.6.5）：按哪个流程研究、遵循度如何。`undefined` = 旧报告 / 流程关闭
   * （**不回填**，界面不显示这一行）。镜像 `schemas/report.py::WorkflowQuality`。
   */
  workflow?: QualityWorkflow | null;
}

export type WorkflowAdherence = 'full' | 'partial' | 'none' | 'unreported';

/** `quality.workflow` —— 与后端 `WorkflowQuality` 同形（生产者 / 响应模型 / 本类型三处同时落地）。 */
export interface QualityWorkflow {
  templateId?: string | null;
  templateName: string;
  version?: number | null;
  versionId?: string | null;
  hash?: string | null;
  /** `published | builtin-code` */
  source: 'published' | 'builtin-code';
  fallbackReason?: string | null;
  steps?: { total: number; started: number; done: number; skipped: number } | null;
  gates?: { total: number; done: number } | null;
  adherence: WorkflowAdherence;
  markersSeen?: number;
  unknownMarkers?: number;
}

/** `quality.clamp` —— 与 `report_validator.clamp_doc()` 的落地计数同形（★v12 S3）。 */
export interface QualityClamp {
  count: number;
  /** 被截短的字段路径，如 `["summary", "heading|段落"]`。 */
  fields: string[];
}

/** `quality.salvage` —— 与 `report_validator.SalvageReport.as_payload()` 同形。 */
export interface QualitySalvage {
  used: boolean;
  droppedKeys: number;
  droppedSections: number;
  droppedBlocks: number;
  /** ★v12（S3 / V11-P2-2）：被裁掉的未知块数。过去 `as_payload()` 漏发它 ⇒ 写进了一个前端读不到的地方。 */
  prunedBlocks?: number;
  /** ★v12（S3）：抢救过程中顺便收敛了几处超长字段（与 `dropped*` 不重叠：那些是丢了，这个是被截短）。 */
  clamped?: number;
  /** ★v12（S4）：从模型正文还原的方式（`json` / `json_repaired`）。缺省 ⇒ 没走 L1.5。 */
  how?: string | null;
  reasons: string[];
}

/** `quality.cost` —— 与 `agent/cost_model.py::CostResolution.as_payload()` 同形。 */
export interface QualityCost {
  source: CostSource;
  reportedUsd?: number | null;
  estimatedUsd?: number | null;
  usd?: number | null;
  priceVersion?: string | null;
}

/** `quality.prompt` —— `{version, sha1, bytes}`。 */
export interface QualityPrompt {
  version?: number | null;
  sha1: string;
  bytes: number;
}

/** One un-traceable number, with the coordinates needed to jump to it. */
export interface GroundingSample {
  /** `blockId` from the single derivation rule (`lib/blocks.ts`). */
  blockId: string;
  path: string;
  raw: string;
  text: string;
}

/** Mirrors `api/app/services/numeric_claims.py::GroundingReport.as_payload()`. */
export interface GroundingQuality {
  factClaims: number;
  factMissing: number;
  /** Every un-traceable number; `samples` only carries the first 20 blocks. */
  totalMissing: number;
  /**
   * ★A4: distinct values behind `factMissing` (which counts **references**).
   * Absent on payloads written before `claims-v3` — fall back to `factMissing`.
   */
  distinctMissing?: number;
  /** Distinct values in the denominator (references vs values, again). */
  distinctClaims?: number;
  /**
   * ★R3-1: `samples` is deduped by `blockId`, so `samples.length` counts **blocks**,
   * not references. These two are the exact denominators the panel needs: how many
   * blocks actually carry an ungrounded number, and how many distinct values the
   * emitted samples cover. Absent on older payloads → the panel states nothing about
   * coverage rather than guessing (see `TrustBadge`).
   */
  sampledBlocks?: number;
  sampledValues?: number;
  projectionClaims: number;
  derivedClaims: number;
  identifierClaims: number;
  /** `'insufficient_sample'` wins over a pretty 1.0 (★V6-P2-8). */
  skipped: string | null;
  samples: GroundingSample[];
  /**
   * ★A6: the rules version that produced these numbers (`claims-v3`).
   * Absent = an earlier rules generation: the counts are **not comparable**
   * with a current one, so the UI must not show a percentage (`TrustState.legacy`).
   */
  rules?: string | null;
  /** ★A6: input fingerprints — they make the verdict mechanically re-computable. */
  docHash?: string | null;
  snapshotHash?: string | null;
}

/** Mirrors `QualityEngineOut` — four fields, on purpose (★V6-P1-3). */
export interface EngineInfo {
  fingerprint: string;
  cliVersion?: string | null;
  allowTools: string[];
  schemaBytes: number;
  /** ★v7：**当次交给 CLI 的那份** schema 的 sha1（`workspace/schema.json`）。 */
  schemaSha1?: string | null;
}

/** ★v7（AC-70）：当前 schema 的实测预算与深度余量。`headroom` 当前 = 1。 */
export interface EngineSchemaBudget {
  bytes: number;
  depth: number;
  nodes: number;
  maxBytes: number;
  maxDepth: number;
  maxNodes: number;
  headroom: number;
  ok: boolean;
}

/** ★v7：这次 spawn 的显式预算（镜像 `EngineLimitsOut`）。 */
export interface EngineLimits {
  retryMax: number;
  repairTurns: number;
  maxOutputTokens: number;
  valueBytes: number;
  schemaBytes: number;
}

/** ★v7（H3.5）：最近一次失败。只读、不据此降级。 */
export interface EngineLastFailure {
  code: string;
  hint: string;
  at?: string | null;
  /** 同一指纹在同一标的最近 N 条 run 里出现的次数（`>= 3` 才换更强措辞）。 */
  count: number;
  /** ★v8：服务端判定好的"该换更强措辞了吗"——阈值不在前端各写一份。 */
  alert?: boolean;
}

/** `GET /capabilities.engine` — a new object, not a field on `aragon`. */
export interface EngineCapability {
  fingerprint?: string | null;
  drift: boolean;
  schemaBytes: number;
  allowTools: string[];
  /** ★v7：`lean` / `full`。出问题时能追到"是不是体位导致的"（R-32）。 */
  posture?: string | null;
  schemaSha1?: string | null;
  schema?: EngineSchemaBudget | null;
  limits?: EngineLimits | null;
  priceVersion?: string | null;
  lastFailure?: EngineLastFailure | null;
}

/** ★v6 G4: "these two versions came from different engines". Never changes a delta. */
export interface Comparability {
  /** `null` means "at least one side has no fingerprint" — we do not guess. */
  same: boolean | null;
  changed: string[];
  deltaUnaffected: boolean;
  note: string;
}

export type BudgetState = 'ok' | 'near' | 'exceeded' | 'unlimited';

export interface CostBudget {
  monthlyUsd: number;
  /** `null` when the budget is unlimited (`monthlyUsd === 0`). */
  remainingUsd: number | null;
  resetAt: string | null;
  state: BudgetState;
}

export interface CostModelRow {
  model: string;
  usd: number;
  runs: number;
}

/** Mirrors `api/app/schemas/cost.py::CostSummary`. */
export interface CostSummary {
  window: string;
  from: string;
  to: string;
  /** Known spend only — unknown runs are counted separately, never as $0.00. */
  spentUsd: number;
  /** ★v7：按 token × 单价估算的部分，单独一列，绝不并进 `spentUsd`。 */
  estimatedUsd: number;
  /** ★v7：两数相加的合计，界面上的「含估算」。 */
  spentIncludingEstimatedUsd: number;
  knownRuns: number;
  /** ★v7：三态各计一次（`reported + estimated + unknown == 行数`）。 */
  reportedRuns: number;
  estimatedRuns: number;
  unknownRuns: number;
  byKind: Record<string, number>;
  byModel: CostModelRow[];
  budget: CostBudget;
  /** ★v7：估算用的价格表版本。 */
  priceVersion?: string | null;
  /** Ledger rows lost to a savepoint (report still landed) — R-26. */
  ledgerGap: number;
}

/** 成本三态。`estimated` 与 `reported` 永不同义（H-6）。 */
export type CostSource = 'reported' | 'estimated' | 'unknown';

export interface CostEntry {
  id: number;
  kind: string;
  refId: string;
  symbol?: string | null;
  reportId?: string | null;
  model?: string | null;
  usd?: number | null;
  known: boolean;
  /** ★v7：估算值与来源三态（`usd` / `usdEst` 互斥，AC-86）。 */
  usdEst?: number | null;
  source: CostSource;
  createdAt: string;
}

export interface CostEntriesResponse {
  items: CostEntry[];
  total: number;
}

export interface EstimateResponse {
  estimatedUsd: number | null;
  reason?: 'no_history' | 'no_price' | null;
  basis?: {
    samples: number;
    medianTokens: number;
    model: string;
    priceVersion?: string | null;
  } | null;
}

/** Mirrors `api/app/schemas/blocks.py::BlockHit`. */
export interface BlockHit {
  blockId: string;
  sectionId: string;
  sectionTitle: string;
  kind: string;
  overlapDays: number;
  matchKind: string;
  text: string;
}

/** Mirrors `api/app/schemas/blocks.py::BlockRangeResponse`. */
export interface BlockRangeResponse {
  reportId: string;
  range: { from: string; to: string };
  items: BlockHit[];
  total: number;
  truncated: boolean;
  asOf: string;
  reason?: string | null;
}

export interface ReportSummary {
  id: string;
  instrumentId: string;
  version: number;
  status: ReportStatus;
  title: string;
  headline: string;
  stance?: Stance | null;
  asOf: string;
  generatedAt: string;
  isLatest: boolean;
  pinned: boolean;
  archived: boolean;
  provisional: boolean;
  groundedRatio?: number | null;
  quality: ReportQuality;
  provider?: string | null;
  model?: string | null;
  costUsd?: number | null;
  costKnown: boolean;
  tokensIn?: number | null;
  tokensOut?: number | null;
  durationMs?: number | null;
  docBytes: number;
  /**
   * ★X6（§24.3.9(c)）：三个**按请求者视角**由服务端算好的追加键。
   * 全部可选：`AUTH_MODE=open` 或旧后端不下发——此时界面按"未知 ⇒ 不收窄"处理
   * （`!== false`），绝不在前端自行推断谁是作者。
   */
  author?: string | null;
  canManage?: boolean;
  canCurate?: boolean;
}

export interface ReportDetail {
  report: ReportSummary;
  doc: ReportDoc;
  /**
   * ★v14（U4 / §23.5.4）：`'ok' | 'corrupt'`。默认 `'ok'` ⇒ 老后端缺这个键时按"正常"处理
   * （向后兼容），而损坏的 doc 仍然可读（12 种块渲染器一行不用改）。
   */
  integrity?: 'ok' | 'corrupt';
}

export interface ReportListResponse {
  items: ReportSummary[];
  total: number;
}

/* ── 报告库（★v14 U1 / §23.4.3）───────────────────────────────────────────── */

/** 五个 scope。`latest` 服务首页/命令面板；库页 4 个 tab（默认 `all`）。 */
export type LibraryScope = 'latest' | 'all' | 'starred' | 'archived' | 'trash';

export interface LibraryInstrument {
  id: string;
  symbol: string;
  name: string;
  market: Market;
  currency: string;
}

export interface ReportLibraryItem {
  report: ReportSummary;
  instrument: LibraryInstrument;
  /** `null` = 早期版本（`doc_sha1` 不回填）。**不是**"内容不同"。 */
  docSha1?: string | null;
  integrity: 'ok' | 'corrupt';
  /** 非删除行恒 `null`；`scope=trash` 里非空（回收站要显示"删除于 X / 还剩 N 天"）。 */
  deletedAt?: string | null;
}

export interface ActiveRunItem {
  runId: string;
  instrumentId: string;
  symbol: string;
  name: string;
  stage?: string | null;
  pct: number;
  startedAt: string;
}

export interface LibraryResponse {
  items: ReportLibraryItem[];
  nextCursor?: string | null;
  total: number;
  /** 键 = 5 个 scope 名（**不含** `q` / `instrumentId` 的过滤）；服务端不下发中文标签。 */
  counts: Partial<Record<LibraryScope, number>>;
  active: ActiveRunItem[];
}

/* ── runs / capabilities ─────────────────────────────────────────────────── */

export type RunStageId = 'resolve' | 'snapshot' | 'prepare' | 'research' | 'validate' | 'persist';

export interface RunUsage {
  inputTokens?: number | null;
  outputTokens?: number | null;
  totalTokens?: number | null;
  turns?: number | null;
  toolCalls?: number | null;
}

export interface RunOut {
  id: string;
  instrumentId: string;
  status: RunStatus;
  stage?: string | null;
  pct: number;
  startedAt: string;
  finishedAt?: string | null;
  exitCode?: number | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  reportId?: string | null;
  costUsd?: number | null;
  costKnown: boolean;
  usage: RunUsage;
  /** ★v7（§20.5.1）：8 个新字段。`hint` 由服务端按码下发，前端不拼文案（H-12）。 */
  retryCount: number;
  structuredAttempts?: number | null;
  agentProvider?: string | null;
  agentModel?: string | null;
  costSource?: CostSource | null;
  costUsdEst?: number | null;
  failureFingerprint?: string | null;
  hint?: string | null;
  /** ★v16：冻结绑定的流程名与版本，如「标准五段式研究 v3」；对所有用户公开。`null` = 未使用流程。 */
  workflowLabel?: string | null;
  /** ★v16：遵循度；仅拥有者/管理员可见，其余为 `null`。 */
  workflowAdherence?: WorkflowAdherence | null;
}

/**
 * ★v16（研究流程图 §5.3）：`run.workflow` 事件载荷。**不含**管理员的 prompt / checklist 正文。
 * 前端遇到未知 `kind` 一律忽略（向前兼容）。
 */
export interface WorkflowPlanItem {
  key: string;
  kind: 'step' | 'gate';
  title: string;
  role?: string | null;
  level: number;
  sections?: string[];
  upstream: string[];
}

export interface WorkflowPlanEvent {
  kind: 'plan';
  templateName: string;
  version: number | null;
  hash?: string | null;
  fallback: boolean;
  total: number;
  items: WorkflowPlanItem[];
}

export type WorkflowStepState = 'start' | 'done' | 'skip';

export interface WorkflowStepEvent {
  kind: 'step';
  key: string;
  state: WorkflowStepState;
  note?: string | null;
  doneCount: number;
  total: number;
}

export type RunWorkflowEvent = WorkflowPlanEvent | WorkflowStepEvent;

export interface CreateRunResponse {
  runId: string;
  status: string;
  instrumentId: string;
  /** ★v6: may be `null` — "无法预估" is an answer, never a fake 0. */
  estimateUsd?: number | null;
  /**
   * ★v14（§23.5.3）：这次请求**请求了**去重，不是结论。
   * 最终结论只有一个来源：`run.done.deduped`。
   */
  dedupe?: boolean;
  /** ★X4（§24.5.5）：202 体追加，让芯片不必再请求一次 `/auth/me`。 */
  quota?: { remaining: number | null; resetAt?: string | null } | null;
}

export interface AragonCapability {
  available: boolean;
  version?: string | null;
  core?: string | null;
  versionOk: boolean;
  provider?: string | null;
  model?: string | null;
  hasApiKey: boolean;
  ajv: boolean;
  canStructured: boolean;
  hasSubmitResult: boolean;
  extraAllowTools: string[];
  resolvedBinary?: string | null;
  degradedReason?: string | null;
  /** ★v7（V8-P1-2）：`exec` 的 `system` 事件里的 `model.baseUrl`；没有就为空。 */
  baseUrl?: string | null;
  /** ★v7（H8）：`aragon doctor` 的摘要。**只 warn 不降级**——`ok` 之外的值不是故障。 */
  doctor?: string | null;
  /** ★v7：目标模型是否在 `aragon models` 的名单里。`null` = 没测（自检关闭）。 */
  modelKnown?: boolean | null;
  /* ── ★v9（K1/K2）：五个新字段。V10-P1-1：它们也必须在 `schemas/capabilities.py` 里
     声明，否则 pydantic 会静默丢弃（这是同一个坑的第三次）。─────────────── */
  /** 只 host[:port]，**永不**回显 path/query。 */
  baseUrlHost?: string | null;
  /** `keySource != "none"` —— 合并事实（`.env` 的 key 也算）。 */
  keyPresent?: boolean;
  /** `env | cli_profile | none`（唯一取值域，不设配置项）。 */
  keySource?: 'env' | 'cli_profile' | 'none';
  /** 探针的唯一真源：这个端点**此刻**是通的吗。 */
  verified?: boolean;
  verifiedAt?: string | null;
  /**
   * ★v12（S1 / AC-112）：本进程能不能 spawn 子进程。
   *
   * 它与 `runtime.loopOk` 是**同一个** `preflight.spawn_capability()` 的两个消费点，
   * 所以 `loopOk === false` ⟺ `degradedReason === 'event_loop_unsupported'`。
   * `fullyCapable` **不在 wire 上**（那只是 `degradedReason === null` 的另一种写法，
   * 多发一个就是第二个真源）。
   */
  loopOk?: boolean;
  loopReason?: string | null;
  errors: string[];
}

/** ★v9（K2）：`GET/POST /agent/probe` 的形状（§21.5.1）。 */
export interface ProbeOutcome {
  ok: boolean;
  code: string | null;
  message?: string | null;
  ms: number;
  turns?: number | null;
  tokensIn?: number | null;
  tokensOut?: number | null;
  structureAttempts?: number | null;
  engine: { provider?: string | null; model?: string | null; baseUrlHost?: string | null };
  cli?: string | null;
  probedAt?: string | null;
}

export interface ProbeStatus {
  last: ProbeOutcome | null;
  verified: boolean;
  verifiedAt: string | null;
  staleReason: 'never' | 'expired' | 'identity_changed' | 'failed' | null;
  probeCostHintUsd?: number | null;
  probeTtlSec: number;
}

export interface Capabilities {
  aragon: AragonCapability;
  agentMode: 'real' | 'mock';
  dataProviders: { id: string; available: boolean }[];
  features: Record<string, boolean> & {
    /** Older APIs omit this; only true enables mine history and context. */
    askConversation?: boolean;
  };
  limits: Record<string, unknown>;
  /** ★v6 G4: a new top-level object (model/provider stay in `aragon`). */
  engine?: EngineCapability | null;
  /**
   * ★v12（S1 / S7 / §22.5.1）：**进程级事实**——"这个进程现在能做什么"。
   *
   * 为什么与 `aragon` 分开：两者可以矛盾（引擎完全正常，但这个 `--reload` 起的进程
   * spawn 不了子进程）。混在一起会让"降级原因"读起来自相矛盾。
   */
  runtime?: RuntimeCapability | null;
  /** ★X6（§24.3.9(g)）：非 `engine.probe` 主体拿到的是脱敏的同形状响应。 */
  redacted?: boolean;
}

/** `capabilities.runtime` —— 与 `api/app/schemas/capabilities.py::RuntimeCapabilityOut` 同形。 */
export interface RuntimeCapability {
  /** 实际运行中的事件循环类名（诊断用，如 `_WindowsSelectorEventLoop`）。 */
  eventLoop?: string | null;
  /** 本进程能不能 spawn 子进程。`false` ⇒ 任何生成都做不成（唯一判据是 `preflight.spawn_capability()`）。 */
  loopOk: boolean;
  /** 不能时的机器前缀：`event_loop_selector:…` / `event_loop_policy:…`。 */
  loopReason?: string | null;
  /** `ARAGON_MAX_DURATION_MS/1000 + agent_hard_deadline_grace_sec`。 */
  hardDeadlineSec: number;
  /** 由 `agent_attempts` 派生（`>1` ⇒ `retry_only`，`=1` ⇒ `off`），**不是配置项**。 */
  sessionSave: 'retry_only' | 'off' | string;
  /** CLI 会话目录体积（MB，只读测量）。超阈值只提示，永不自动删。 */
  sessionDirMb?: number | null;
}

export interface WatchlistEntry {
  instrument: Instrument;
  sortOrder: number;
  pinned: boolean;
  addedAt: string;
}

/** ★X6（§24.5.5）：`scope` 说明这份列表是"我的"还是"平台精选"（guest 看到的）。 */
export interface WatchlistResponse {
  items: WatchlistEntry[];
  scope?: 'personal' | 'featured';
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    requestId: string;
    detail: Record<string, unknown>;
  };
}

/* ── ask / search / diff / schedule (mirrors api/app/schemas/*) ──────────── */

export type AskMode = 'cite' | 'deep';
export interface AskRequest {
  question: string;
  mode?: AskMode;
  contextAskIds?: string[];
}
export type AskStatus = 'queued' | 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled';
export type MetricToneDirection = 'up' | 'down' | 'neutral';

export interface Citation {
  sourceId: string;
  title?: string | null;
  publisher?: string | null;
}

export interface AskHit {
  /** `f"{sectionId}#{index}"` — the single derivation rule (see `lib/blocks.ts`). */
  blockId: string;
  sectionId: string;
  sectionTitle: string;
  blockType: string;
  score: number;
  excerpt: string;
  citations: string[];
}

export interface AskAnswer {
  answerText?: string | null;
  hits: AskHit[];
  citations: Citation[];
  hint?: string | null;
}

export interface AskOut {
  id: string;
  reportId: string;
  question: string;
  /** Absent on older APIs; new APIs return the effective context ids. */
  contextAskIds?: string[];
  mode: AskMode;
  status: AskStatus;
  grounded: boolean;
  confidence?: number | null;
  citationsDropped: number;
  answer: AskAnswer;
  costUsd?: number | null;
  costKnown: boolean;
  tokensIn?: number | null;
  tokensOut?: number | null;
  latencyMs?: number | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  createdAt: string;
  finishedAt?: string | null;
}

export interface AskQueuedResponse {
  id: string;
  askId: string;
  status: AskStatus;
  streamUrl: string;
  contextAskIds?: string[];
}

export interface AskListResponse {
  items: AskOut[];
  total: number;
}

export interface InstrumentHit {
  symbol: string;
  name: string;
  market: Market;
  currency: string;
  score: number;
  existing: boolean;
}

export interface ReportHit {
  reportId: string;
  symbol: string;
  title: string;
  sectionId: string;
  sectionTitle: string;
  blockId: string;
  snippet: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  instruments: InstrumentHit[];
  reports: ReportHit[];
  tookMs: number;
  degraded: boolean;
}

export interface MetricDelta {
  label: string;
  before?: number | null;
  after?: number | null;
  deltaPct?: number | null;
  direction: 'up' | 'down' | 'flat';
  polarity: 'bullish' | 'bearish' | 'neutral';
  /** Server-decided colour — the client never re-derives "PE 上升是坏事". */
  tone: MetricToneDirection;
}

export interface BlockChange {
  sectionId: string;
  blockType: string;
  change: 'added' | 'removed' | 'modified';
  blockIdA?: string | null;
  blockIdB?: string | null;
}

export interface RatingChange {
  from: { stance?: Stance | null; target?: string | null };
  to: { stance?: Stance | null; target?: string | null };
}

export interface DiffResult {
  a: string;
  b: string;
  statusChanged?: { from?: string | null; to?: string | null } | null;
  ratingChanged?: RatingChange | null;
  metricsDelta: MetricDelta[];
  sectionsAdded: string[];
  sectionsRemoved: string[];
  blocksChanged: BlockChange[];
  summary: string;
  /** ★v6 G4: appended key. The server never renames `a`/`b`/`summary`. */
  comparability?: Comparability | null;
}

export interface ScheduleOut {
  id: string;
  instrumentId: string;
  symbol?: string | null;
  cron: string;
  enabled: boolean;
  minAgeHours: number;
  nextRunAt: string;
  lastRunId?: string | null;
  failureCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ScheduleListResponse {
  items: ScheduleOut[];
  total: number;
}

export interface ScheduleCreate {
  instrumentId: string;
  cron: string;
  enabled?: boolean;
  minAgeHours?: number;
}

export interface HealthResponse {
  status: string;
  version: string;
  db: string;
  uptimeSec: number;
}

/* ── 用户与权限（★X6 / spec §24.5）──────────────────────────────────────────── */

/** 数据库角色。`guest` 不是角色，是"没有有效会话"。 */
export type Role = 'user' | 'admin' | 'root';

export interface AuthUser {
  id: string;
  username: string;
  displayName: string | null;
  role: Role;
  status: 'active';
  createdAt: string;
  lastLoginAt: string | null;
  mustChangePassword: boolean;
  passwordIsDefault: boolean;
  /** ★Y3：账号是否已有恢复码（只追加，§25.5.1）。缺省按"未设置"。 */
  hasRecoveryKey?: boolean;
}

export type QuotaResource = 'report' | 'deep_ask';

export interface QuotaBucket {
  /** `null` = 不限。 */
  limit: number | null;
  usedToday: number;
  bonus: number;
  /** `null` = 不限。 */
  remaining: number | null;
  resetAt: string;
  pendingRequest: { id: string; createdAt: string } | null;
}

export interface QuotaStatus {
  report: QuotaBucket;
  deepAsk: QuotaBucket;
  tz: string;
}

export interface Me {
  authMode: 'enforced' | 'open';
  authenticated: boolean;
  user: AuthUser | null;
  /** 服务端展开后的权限串——**体验层**用，不是授权依据。 */
  permissions: string[];
  quota: QuotaStatus | null;
}

export type RegistrationMode = 'open' | 'invite' | 'closed';

export interface AuthConfig {
  authMode: 'enforced' | 'open';
  registrationMode: RegistrationMode;
  registrationOpen: boolean;
  passwordPolicy: { min: number; max: number; minClasses: number };
  usernamePolicy: { min: number; max: number; pattern: string };
  captchaKind: 'captcha';
  forceRootPasswordChange: boolean;
  /** ★Y3：站点是否开放自助找回（恢复码）。缺省 true（旧后端无此键时不隐藏入口失败，由服务端 403 兜底）。 */
  recoveryEnabled?: boolean;
}

/** `POST /auth/register` 的 201 体：`Me` + 一次性恢复码（★Y3；关闭自助找回时为 null）。 */
export interface RegisterResult extends Me {
  recoveryKey?: string | null;
}

/** `POST /auth/recover`、`POST /auth/recovery-key` 的成功体：**新**恢复码，只出现这一次。 */
export interface RecoveryKeyResult {
  recoveryKey: string;
  setAt: string;
}

export interface RecoverBody {
  username: string;
  recoveryKey: string;
  newPassword: string;
  captchaId: string;
  captchaAnswer: string;
}

export interface DeleteAccountBody {
  password: string;
  confirm: string;
}

export interface CaptchaChallenge {
  challengeId: string;
  /** `data:image/png;base64,…` */
  image: string;
  width: number;
  height: number;
  expiresIn: number;
}

export interface LoginBody {
  username: string;
  password: string;
  remember?: boolean;
  captchaId?: string;
  captchaAnswer?: string;
}

export interface RegisterBody {
  username: string;
  password: string;
  captchaId?: string;
  captchaAnswer?: string;
  inviteCode?: string;
  displayName?: string;
  /** 蜜罐：人类看不见，必须保持空串。 */
  website?: string;
  /** 验证码**签发**的时刻（ms）——服务端据此判"过快"。 */
  issuedAt?: number;
}

export interface SessionOut {
  id: string;
  current: boolean;
  createdAt: string;
  lastSeenAt: string | null;
  ip: string | null;
  userAgent: string | null;
}

export interface QuotaLedgerItem {
  id: string;
  at: string;
  resource: QuotaResource;
  delta: number;
  reason: string;
  source: 'daily' | 'bonus' | string;
  refId: string | null;
  note: string | null;
}

export interface QuotaLedgerResponse {
  items: QuotaLedgerItem[];
  nextCursor?: string | null;
}

export type QuotaRequestStatus = 'pending' | 'approved' | 'rejected';

export interface QuotaRequestOut {
  id: string;
  resource: QuotaResource;
  amount: number;
  reason: string;
  status: QuotaRequestStatus;
  note?: string | null;
  createdAt: string;
  resolvedAt?: string | null;
}

/* ── 管理后台（M52 使用；形状按 §24.5.3，前端只读不推断）───────────────────── */

export interface AdminQuotaLite {
  report: Pick<QuotaBucket, 'limit' | 'usedToday' | 'bonus' | 'remaining'>;
}

export interface AdminUser {
  id: string;
  username: string;
  displayName: string | null;
  role: Role;
  status: 'active' | 'disabled' | 'deleted';
  createdAt: string;
  lastLoginAt: string | null;
  quota?: AdminQuotaLite;
  locked: boolean;
  invitedBy?: string | null;
}

export interface AdminUserDetail extends AdminUser {
  quotaStatus?: QuotaStatus;
  failedLogins?: number;
  lockedUntil?: string | null;
  disabledReason?: string | null;
  sessions?: SessionOut[];
  /** 形状按后端 `AdminRunRow`（M50）：时间是 `startedAt`，`symbol` 在标的被清理后可能为空。 */
  recentRuns?: {
    id: string;
    instrumentId?: string;
    symbol: string | null;
    status: string;
    startedAt: string;
    finishedAt?: string | null;
    costUsd?: number | null;
  }[];
}

export interface AdminUserListResponse {
  items: AdminUser[];
  total: number;
  nextCursor?: string | null;
}

export interface AdminUserQuery {
  q?: string;
  role?: Role;
  status?: 'active' | 'disabled';
  /** ★Y2(c)：仅返回处于锁定期的 active 用户。 */
  locked?: boolean;
  limit?: number;
  before?: string | null;
}

export interface AdminCreateUserBody {
  username: string;
  role?: 'user' | 'admin';
  password?: string;
  displayName?: string;
  bonusCredits?: number;
}

export interface AdminPatchUserBody {
  displayName?: string | null;
  role?: Role;
  status?: 'active' | 'disabled';
  disabledReason?: string;
}

export interface AdminQuotaBody {
  resource?: QuotaResource;
  grant?: number;
  dailyLimit?: number | null;
  note?: string;
}

export interface InviteOut {
  id: string;
  code: string;
  maxUses: number;
  usedCount: number;
  expiresAt: string | null;
  bonusCredits: number;
  note?: string | null;
  disabledAt?: string | null;
  createdAt: string;
  createdBy?: string | null;
  /** 服务端推出的状态（撤销 > 过期 > 用尽 > 可用）；缺省时前端按字段推。 */
  status?: 'active' | 'exhausted' | 'expired' | 'revoked';
}

export interface InviteCreateBody {
  maxUses: number;
  expiresInDays: number | null;
  bonusCredits: number;
  note?: string;
}

export interface AdminQuotaRequest extends QuotaRequestOut {
  /** 申请人；账号被清理的极端情形下为空。 */
  user: { id: string; username: string; displayName?: string | null } | null;
  userRuns7d: number;
  granted?: number | null;
  resolvedBy?: string | null;
  /** 处理说明（拒绝理由 / 备注）。后端字段名是 `resolutionNote`。 */
  resolutionNote?: string | null;
}

export interface AuditRow {
  id: string | number;
  at: string;
  actorName: string | null;
  actorRole: string | null;
  action: string;
  targetName: string | null;
  /** 已按 /24（IPv6 /48）截断；后端字段名是 `ip`。 */
  ip?: string | null;
  /** 旧写法（规格早期叫 `ipPrefix`），保留为可选以免读旧夹具时出错。 */
  ipPrefix?: string | null;
  targetType?: string | null;
  requestId?: string | null;
  detail?: Record<string, unknown> | null;
}

export interface AuditQuery {
  action?: string;
  actor?: string;
  target?: string;
  from?: string;
  to?: string;
  limit?: number;
  before?: string | null;
}

export interface AdminBulkQuotaBody {
  userIds: string[];
  /** −50..50，非 0（与后端 `GRANT_MIN/MAX` 同值）。 */
  grant: number;
  note?: string;
}

export interface AdminBulkQuotaResult {
  applied: number;
  skipped: number;
  batchId: string;
}

/** 概览"安全事件（24 h）"（§25.3.5c）。失败类是**尝试次数**（Σ detail.count），不是行数。 */
export interface AdminSecurity24h {
  loginFailures: number;
  loginFailureBursts: number;
  loginFailuresCapped: boolean;
  lockouts: number;
  recoveryFailures: number;
  recoveries: number;
  selfDeletes: number;
  exports: number;
}

export interface AdminSettings {
  registrationMode: RegistrationMode;
  /** ★Y3：严格布尔（后端对 1/"true"/null 回 422 invalid_setting）。 */
  authRecoveryEnabled?: boolean;
  defaultDailyReport: number;
  defaultDailyDeepAsk: number;
  signupBonus: number;
  siteDailyCap: number;
}

export interface AdminOverview {
  users: { total: number; byRole: Record<string, number>; active7d: number; disabled: number; locked: number };
  signups: { day: string; count: number }[];
  runs: { today: number; succeeded: number; failed: number; successRate: number | null };
  runsDaily: { day: string; count: number }[];
  quota: { consumedToday: number; refundedToday: number };
  topUsers7d: { userId: string; username: string | null; runs: number }[];
  pendingQuotaRequests: number;
  recentAudit: AuditRow[];
  security24h?: AdminSecurity24h;
}
