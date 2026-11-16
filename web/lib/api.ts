/**
 * Typed fetch client — one error shape, one timeout, SSR and CSR safe (§6.3).
 *
 * Server components call it with the internal API base; the browser calls the
 * public base. `<AbortSignal.timeout>` gives every request a hard ceiling so a
 * wedged API turns into a readable empty state instead of a hung page.
 */

import type {
  ApiErrorBody,
  BarsResponse,
  Capabilities,
  CreateRunResponse,
  HealthResponse,
  Instrument,
  Quote,
  ReportDetail,
  ReportListResponse,
  ReportSummary,
  ResolveResponse,
  RunOut,
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

  createReport: (instrumentId: string, body: { reason?: string; force?: boolean } = {}) =>
    request<CreateRunResponse>(`/instruments/${encodeURIComponent(instrumentId)}/reports`, {
      method: 'POST',
      body,
      timeoutMs: 15000,
    }),

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

  watchlist: () => request<{ items: WatchlistEntry[] }>('/watchlist'),

  addWatchlist: (instrumentId: string) =>
    request<{ items: WatchlistEntry[] }>('/watchlist', { method: 'POST', body: { instrumentId } }),

  removeWatchlist: (instrumentId: string) =>
    request<void>(`/watchlist/${encodeURIComponent(instrumentId)}`, { method: 'DELETE' }),

  reorderWatchlist: (ids: string[]) =>
    request<{ items: WatchlistEntry[] }>('/watchlist/reorder', { method: 'PATCH', body: { ids } }),
};

export { authHeaders, PUBLIC_BASE, SERVER_BASE };
