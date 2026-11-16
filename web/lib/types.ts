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

export interface HealthResponse {
  status: string;
  version: string;
  db: string;
  uptimeSec: number;
}
