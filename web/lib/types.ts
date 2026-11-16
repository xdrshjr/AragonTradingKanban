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
}

export interface BarsMeta {
  source: string;
  barsCount: number;
  fromTs?: number | null;
  toTs?: number | null;
  adjust: string;
  timeframe: string;
  cached: boolean;
  stale: boolean;
  warnings: string[];
}

export interface BarsResponse {
  instrument: Instrument;
  bars: Bar[];
  indicators: Partial<Indicators>;
  meta: BarsMeta;
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
  projectionClaims: number;
  derivedClaims: number;
  identifierClaims: number;
  /** `'insufficient_sample'` wins over a pretty 1.0 (★V6-P2-8). */
  skipped: string | null;
  samples: GroundingSample[];
}

/** Mirrors `QualityEngineOut` — four fields, on purpose (★V6-P1-3). */
export interface EngineInfo {
  fingerprint: string;
  cliVersion?: string | null;
  allowTools: string[];
  schemaBytes: number;
}

/** `GET /capabilities.engine` — a new object, not a field on `aragon`. */
export interface EngineCapability {
  fingerprint?: string | null;
  drift: boolean;
  schemaBytes: number;
  allowTools: string[];
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
  knownRuns: number;
  unknownRuns: number;
  byKind: Record<string, number>;
  byModel: CostModelRow[];
  budget: CostBudget;
  /** Ledger rows lost to a savepoint (report still landed) — R-26. */
  ledgerGap: number;
}

export interface CostEntry {
  id: number;
  kind: string;
  refId: string;
  symbol?: string | null;
  reportId?: string | null;
  model?: string | null;
  usd?: number | null;
  known: boolean;
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
}

export interface ReportDetail {
  report: ReportSummary;
  doc: ReportDoc;
}

export interface ReportListResponse {
  items: ReportSummary[];
  total: number;
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
}

export interface CreateRunResponse {
  runId: string;
  status: string;
  instrumentId: string;
  /** ★v6: may be `null` — "无法预估" is an answer, never a fake 0. */
  estimateUsd?: number | null;
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
  errors: string[];
}

export interface Capabilities {
  aragon: AragonCapability;
  agentMode: 'real' | 'mock';
  dataProviders: { id: string; available: boolean }[];
  features: Record<string, boolean>;
  limits: Record<string, unknown>;
  /** ★v6 G4: a new top-level object (model/provider stay in `aragon`). */
  engine?: EngineCapability | null;
}

export interface WatchlistEntry {
  instrument: Instrument;
  sortOrder: number;
  pinned: boolean;
  addedAt: string;
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
