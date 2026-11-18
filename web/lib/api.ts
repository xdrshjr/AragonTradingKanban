/**
 * Typed fetch client — one error shape, one timeout, SSR and CSR safe (§6.3).
 *
 * Server components call it with the internal API base; the browser calls the
 * public base. `<AbortSignal.timeout>` gives every request a hard ceiling so a
 * wedged API turns into a readable empty state instead of a hung page.
 */

import type {
  ApiErrorBody,
  AskListResponse,
  AskMode,
  AskOut,
  AskQueuedResponse,
  BarsResponse,
  BlockRangeResponse,
  Capabilities,
  CostEntriesResponse,
  CostSummary,
  CreateRunResponse,
  DiffResult,
  EstimateResponse,
  HealthResponse,
  Instrument,
  Quote,
  LibraryResponse,
  ReportDetail,
  ReportListResponse,
  ReportSummary,
  ResolveResponse,
  RunOut,
  ScheduleCreate,
  ScheduleListResponse,
  ScheduleOut,
  ProbeOutcome,
  ProbeStatus,
  SearchResponse,
  SymbolSearchHit,
  WatchlistEntry,
} from './types';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string;
  readonly detail: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody | null) {
    const code = body?.error.code ?? 'network_error';
    super(body?.error.message ?? `请求失败（HTTP ${status}）`);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.requestId = body?.error.requestId ?? '-';
    this.detail = body?.error.detail ?? {};
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  /** 429 payloads carry `detail.retryAfterMs` so the UI can say "多久之后". */
  get retryAfterMs(): number | null {
    const value = this.detail.retryAfterMs;
    return typeof value === 'number' ? value : null;
  }

  /** `detail.suggestMode` — e.g. "改用引用式检索", shown, never applied silently. */
  get suggestMode(): string | null {
    const value = this.detail.suggestMode;
    return typeof value === 'string' ? value : null;
  }
}

const PUBLIC_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';
const SERVER_BASE = process.env.INTERNAL_API_BASE ?? PUBLIC_BASE;
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? '';
const TIMEOUT_MS = 8000;

export function apiBase(): string {
  return typeof window === 'undefined' ? SERVER_BASE : PUBLIC_BASE;
}

/** SSE must never be proxied through Next's buffering layer unnecessarily. */
export function sseBase(): string {
  return apiBase();
}

function authHeaders(): Record<string, string> {
  return API_TOKEN ? { authorization: `Bearer ${API_TOKEN}` } : {};
}

/** `reportLibrary()` 的参数；`before` 是**已编码**的游标，不是原始 `{at}|{id}`。 */
export interface LibraryParams {
  scope?: import('./types').LibraryScope;
  q?: string;
  instrumentId?: string;
  limit?: number;
  before?: string | null;
  withCounts?: boolean;
  includeActive?: boolean;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  timeoutMs?: number;
  /** Next's cache hint; reports are immutable so they get `no-store`. */
  revalidate?: number;
  signal?: AbortSignal;
  raw?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = TIMEOUT_MS, revalidate, signal, raw } = options;
  const url = `${apiBase()}/api/v1${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const composedSignal = signal ?? controller.signal;

  try {
    const response = await fetch(url, {
      method,
      headers: {
        ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
        ...authHeaders(),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: composedSignal,
      ...(typeof revalidate === 'number' ? { next: { revalidate } } : {}),
      ...(typeof window === 'undefined' && typeof revalidate !== 'number' ? { cache: 'no-store' as const } : {}),
    });

    if (response.status === 204) {
      return undefined as T;
    }

    if (!response.ok) {
      let payload: ApiErrorBody | null = null;
      try {
        payload = (await response.json()) as ApiErrorBody;
      } catch {
        payload = null;
      }
      throw new ApiError(response.status, payload);
    }

    if (raw) {
      return (await response.text()) as unknown as T;
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, {
        error: { code: 'timeout', message: '请求超时，请稍后重试', requestId: '-', detail: {} },
      });
    }
    throw new ApiError(0, {
      error: {
        code: 'network_error',
        message: error instanceof Error ? error.message : '网络不可达',
        requestId: '-',
        detail: {},
      },
    });
  } finally {
    clearTimeout(timer);
  }
}

/* ── endpoints ───────────────────────────────────────────────────────────── */

export const api = {
  health: () => request<HealthResponse>('/health'),

  capabilities: (revalidate = 30) => request<Capabilities>('/capabilities', { revalidate }),

  searchSymbols: (query: string, limit = 8) =>
    request<{ hits: SymbolSearchHit[] }>(
      `/symbols/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),

  resolveSymbol: (query: string) =>
    request<ResolveResponse>('/symbols/resolve', { method: 'POST', body: { query } }),

  instruments: (ids: string[]) =>
    request<{ items: Instrument[] }>(`/instruments?ids=${encodeURIComponent(ids.join(','))}`),

  instrument: (id: string) => request<{ items: Instrument[] }>(`/instruments?ids=${encodeURIComponent(id)}`),

  bars: (id: string, timeframe = '1d', range = '1y', adjust = 'qfq') =>
    request<BarsResponse>(
      `/instruments/${encodeURIComponent(id)}/bars?timeframe=${timeframe}&range=${range}&adjust=${adjust}`,
    ),

  quote: (id: string) => request<Quote>(`/instruments/${encodeURIComponent(id)}/quote`),

  reports: (instrumentId: string, limit = 20, includeArchived = false) =>
    request<ReportListResponse>(
      `/instruments/${encodeURIComponent(instrumentId)}/reports?limit=${limit}&includeArchived=${includeArchived}`,
    ),

  latestReport: (instrumentId: string) =>
    request<ReportDetail | null>(`/instruments/${encodeURIComponent(instrumentId)}/reports/latest`),

  report: (reportId: string) => request<ReportDetail>(`/reports/${encodeURIComponent(reportId)}`),

  /**
   * ★v14（U1 / §23.5.1）：报告库列表 —— 首页、库页、命令面板的**唯一**读路径。
   *
   * 三个量别混：`counts` 是不含 `q`/`instrumentId` 的全库计数（tab 徽章恒定），
   * `total` 是当前过滤命中数（联动），`nextCursor` 只在有下一页时非 null。
   */
  reportLibrary: (params: LibraryParams = {}) => {
    const search = new URLSearchParams();
    search.set('scope', params.scope ?? 'latest');
    if (params.q) search.set('q', params.q);
    if (params.instrumentId) search.set('instrumentId', params.instrumentId);
    if (params.limit !== undefined) search.set('limit', String(params.limit));
    // 游标已经是 `encodeURIComponent` 过的：原样透传（服务端只做正则校验，不做时间解析）。
    if (params.before) search.set('before', params.before);
    if (params.withCounts === false) search.set('withCounts', '0');
    if (params.includeActive) search.set('includeActive', '1');
    return request<LibraryResponse>(`/reports?${search.toString()}`);
  },

  /** ★v14（U5 / §23.5.2）：把一份软删的版本救回来。未删除 ⇒ 409 `report_not_deleted`。 */
  restoreReport: (reportId: string) =>
    request<ReportSummary>(`/reports/${encodeURIComponent(reportId)}/restore`, { method: 'POST' }),

  createReport: (
    instrumentId: string,
    body: { reason?: string; force?: boolean; dedupe?: boolean; baseReportId?: string | null } = {},
    options: { override?: boolean } = {},
  ) =>
    request<CreateRunResponse>(
      `/instruments/${encodeURIComponent(instrumentId)}/reports${options.override ? '?override=1' : ''}`,
      {
        method: 'POST',
        body,
        timeoutMs: 15000,
      },
    ),

  /** ★v6 G3: `null` means "cannot estimate" — never a fake 0 (`/runs/estimate`). */
  estimateRun: (body: { symbol: string; kind?: 'run' | 'ask_deep' }) =>
    request<EstimateResponse>('/runs/estimate', { method: 'POST', body }),

  /* ── engine self-check (★v9 K2) ───────────────────────────────────────── */

  /** 跑一次最小真实生成（≈10 秒 / ≈$0.0016）。失败也是 200：结论也是结果。 */
  probeEngine: (force = false) =>
    request<ProbeOutcome>('/agent/probe', { method: 'POST', body: { force }, timeoutMs: 120_000 }),

  /** 纯读：面板打开一次不该花掉一次自检。 */
  probeStatus: (revalidate = 0) => request<ProbeStatus>('/agent/probe', { revalidate }),

  /* ── cost ledger (G3) ────────────────────────────────────────────────── */

  costSummary: (window: '7d' | '30d' | '90d' | 'mtd' | 'ytd' = '30d') =>
    request<CostSummary>(`/cost/summary?window=${window}`),

  costEntries: (limit = 50) => request<CostEntriesResponse>(`/cost/entries?limit=${limit}`),

  /** ★v6 G3: the budget lives in `settings`, so it takes effect immediately. */
  setBudget: (monthlyUsd: number) =>
    request<CostSummary>('/cost/budget', { method: 'POST', body: { monthlyUsd } }),

  /* ── range query (G2a) ───────────────────────────────────────────────── */

  /** "Which paragraphs talk about this stretch of the chart?" (§19.3.3) */
  reportBlocks: (reportId: string, from: string, to: string, limit = 50) =>
    request<BlockRangeResponse>(
      `/reports/${encodeURIComponent(reportId)}/blocks?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&limit=${limit}`,
    ),

  patchReport: (reportId: string, body: { pinned?: boolean; archived?: boolean; setLatest?: boolean }) =>
    request<ReportSummary>(`/reports/${encodeURIComponent(reportId)}`, { method: 'PATCH', body }),

  deleteReport: (reportId: string) =>
    request<void>(`/reports/${encodeURIComponent(reportId)}`, { method: 'DELETE' }),

  exportUrl: (reportId: string, format: 'html' | 'md') =>
    `${apiBase()}/api/v1/reports/${encodeURIComponent(reportId)}/export?format=${format}`,

  run: (runId: string) => request<RunOut>(`/runs/${encodeURIComponent(runId)}`),

  eventsToken: (runId: string) =>
    request<{ token: string; expiresIn: number }>(`/runs/${encodeURIComponent(runId)}/events-token`, {
      method: 'POST',
    }),

  cancelRun: (runId: string) =>
    request<{ status: string; runId: string }>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),

  /* ── ask (A1) ────────────────────────────────────────────────────────── */

  ask: (reportId: string, body: { question: string; mode?: AskMode }) =>
    request<AskOut | AskQueuedResponse>(`/reports/${encodeURIComponent(reportId)}/ask`, {
      method: 'POST',
      body: { mode: 'cite', ...body },
      // `cite` answers inline; `deep` returns 202 at once. This budget covers a
      // cold API, never a generation.
      timeoutMs: 20000,
    }),

  askById: (askId: string) => request<AskOut>(`/asks/${encodeURIComponent(askId)}`),

  reportAsks: (reportId: string, limit = 20) =>
    request<AskListResponse>(`/reports/${encodeURIComponent(reportId)}/asks?limit=${limit}`),

  askEventsToken: (askId: string) =>
    request<{ token: string; expiresIn: number }>(`/asks/${encodeURIComponent(askId)}/events-token`, {
      method: 'POST',
    }),

  cancelAsk: (askId: string) =>
    request<{ status: string; runId: string }>(`/asks/${encodeURIComponent(askId)}/cancel`, { method: 'POST' }),

  /* ── search / diff / schedules (A2, A3, A4) ──────────────────────────── */

  search: (query: string, limit = 20) =>
    request<SearchResponse>(`/search?q=${encodeURIComponent(query)}&limit=${limit}`),

  diff: (reportA: string, reportB: string) =>
    request<DiffResult>(`/reports/${encodeURIComponent(reportA)}/diff/${encodeURIComponent(reportB)}`),

  schedules: () => request<ScheduleListResponse>('/schedules'),

  createSchedule: (body: ScheduleCreate) => request<ScheduleOut>('/schedules', { method: 'POST', body }),

  patchSchedule: (scheduleId: string, body: { cron?: string; enabled?: boolean; minAgeHours?: number }) =>
    request<ScheduleOut>(`/schedules/${encodeURIComponent(scheduleId)}`, { method: 'PATCH', body }),

  deleteSchedule: (scheduleId: string) =>
    request<void>(`/schedules/${encodeURIComponent(scheduleId)}`, { method: 'DELETE' }),

  watchlist: () => request<{ items: WatchlistEntry[] }>('/watchlist'),

  addWatchlist: (instrumentId: string) =>
    request<{ items: WatchlistEntry[] }>('/watchlist', { method: 'POST', body: { instrumentId } }),

  removeWatchlist: (instrumentId: string) =>
    request<void>(`/watchlist/${encodeURIComponent(instrumentId)}`, { method: 'DELETE' }),

  reorderWatchlist: (ids: string[]) =>
    request<{ items: WatchlistEntry[] }>('/watchlist/reorder', { method: 'PATCH', body: { ids } }),
};

export { authHeaders, PUBLIC_BASE, SERVER_BASE };
