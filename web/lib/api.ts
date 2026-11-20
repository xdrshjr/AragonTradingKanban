/**
 * Typed fetch client — one error shape, one timeout, SSR and CSR safe (§6.3).
 *
 * Server components call it with the internal API base; the browser calls the
 * public base. `<AbortSignal.timeout>` gives every request a hard ceiling so a
 * wedged API turns into a readable empty state instead of a hung page.
 *
 * ★X6（spec §24.3.2 / §24.4.2）：客户端是**工厂** `createApi(ctx)`——
 *
 * * 浏览器：`credentials: 'include'`，`aragon_sid` 这个 HttpOnly Cookie 由浏览器自己带；
 *   `apiBase()` 做**回环别名对齐**（`localhost` ↔ `127.0.0.1`），否则 Cookie 落在另一个主机上，
 *   表现为"登录成功但立刻掉线"（R-A1）。
 * * 服务端（RSC）：fetch 不会自动带浏览器 Cookie，所以 `serverApi()` 把 `aragon_sid` 显式转发进
 *   `ctx.cookie`；服务端实例**一律** `cache: 'no-store'` 并**忽略** `revalidate`——`Capabilities`
 *   随主体而异（脱敏），Next Data Cache 若按 URL 复用会把管理员的全量响应回放给游客（RV-6）。
 * * 这个文件里**没有**任何会被内联进公开 JS 的服务凭据（E-15 / RV-1 / AC-206）：整站不再有
 *   "浏览器自带 Bearer"，身份只来自 Cookie 会话。
 */

import type {
  ApiErrorBody,
  AskListResponse,
  AskRequest,
  AskOut,
  AskQueuedResponse,
  BarsResponse,
  BlockRangeResponse,
  AuthConfig,
  CaptchaChallenge,
  Capabilities,
  LoginBody,
  Me,
  QuotaLedgerResponse,
  QuotaRequestOut,
  QuotaResource,
  QuotaStatus,
  RecoverBody,
  RecoveryKeyResult,
  RegisterBody,
  RegisterResult,
  DeleteAccountBody,
  SessionOut,
  WatchlistResponse,
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
} from './types';
import { emitAuthEvent } from './authEvents';

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
const TIMEOUT_MS = 8000;

/** 回环主机的三种写法。它们在 Cookie 眼里是**三个不同的主机**。 */
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

function normalizeHost(host: string): string {
  const lower = host.trim().toLowerCase();
  return lower === '::1' ? '[::1]' : lower;
}

/**
 * ★回环别名对齐（§24.3.2(d) / R-A1）：页面在 `http://localhost:3000`、API 在
 * `http://127.0.0.1:8000` 时，浏览器把 `aragon_sid` 记在 `127.0.0.1` 名下，页面所在主机
 * `localhost` 的请求带不上它。两者**都属于**回环集合且不相等时，把 API 的主机名换成页面的
 * （端口、协议、路径不变）。生产走 nginx 同源（公网域名），这个分支不会触发。
 */
export function alignLoopbackHost(base: string, locationHostname: string | null | undefined): string {
  if (!locationHostname) return base;
  const page = normalizeHost(locationHostname);
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    return base;
  }
  const target = normalizeHost(url.hostname);
  if (!LOOPBACK_HOSTS.has(page) || !LOOPBACK_HOSTS.has(target) || page === target) return base;
  const port = url.port ? `:${url.port}` : '';
  const path = url.pathname === '/' ? '' : url.pathname;
  return `${url.protocol}//${page}${port}${path}`;
}

export function apiBase(): string {
  if (typeof window === 'undefined') return SERVER_BASE;
  return alignLoopbackHost(PUBLIC_BASE, window.location?.hostname);
}

/** SSE must never be proxied through Next's buffering layer unnecessarily. */
export function sseBase(): string {
  return apiBase();
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

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  timeoutMs?: number;
  /**
   * Next's cache hint. ★X6（RV-6）：只在**浏览器**侧保留语义（浏览器 `fetch` 本就忽略 `next`）；
   * 服务端实例一律 `no-store`，传了也忽略。
   */
  revalidate?: number;
  signal?: AbortSignal;
  raw?: boolean;
}

/** 构造 `createApi` 的上下文：服务端只需要转发 Cookie（浏览器侧留空）。 */
export interface ApiContext {
  /** 形如 `aragon_sid=<token>`；仅服务端 fetch 使用（浏览器的 Cookie 由浏览器自带）。 */
  cookie?: string;
}

/** `POST /instruments/{id}/reports` 的请求体里允许的字段。 */
export interface CreateReportBody {
  reason?: string;
  force?: boolean;
  dedupe?: boolean;
  baseReportId?: string | null;
}

export function queryString(values: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

/**
 * 浏览器侧：把"会话失效 / 必须改密"广播出去，让 `Providers` 统一处理（§24.3.15a）。
 *
 * * `401 unauthorized` 且 `detail.reason !== 'no_session'`：**会话失效**（过期 / 被吊销 / 被停用），
 *   不是"本来就没登录"——后者是调用方自己的分支（比如游客点了生成，弹登录框）。
 * * `403 password_change_required`：全局重定向到强制改密页。
 */
function announceAuthFailure(error: ApiError): void {
  if (error.status === 401 && error.code === 'unauthorized') {
    const reason = typeof error.detail.reason === 'string' ? error.detail.reason : null;
    if (reason !== 'no_session') emitAuthEvent('unauthorized', { reason });
    return;
  }
  if (error.status === 403 && error.code === 'password_change_required') {
    emitAuthEvent('password-change-required', {});
  }
}

/**
 * ★Y2(d) / F-23：请求骨架（超时 / Cookie / 错误归一）独立导出，`lib/api.admin.ts` 复用它，
 * 于是 20 余个 `admin*` 方法不再进入首屏 chunk。
 */
export function makeRequest(ctx: ApiContext = {}) {
  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { method = 'GET', body, timeoutMs = TIMEOUT_MS, revalidate, signal, raw } = options;
    const isServer = typeof window === 'undefined';
    const url = `${apiBase()}/api/v1${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const composedSignal = signal ?? controller.signal;

    try {
      const response = await fetch(url, {
        method,
        headers: {
          ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
          ...(isServer && ctx.cookie ? { cookie: ctx.cookie } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: composedSignal,
        // 浏览器：带上 HttpOnly 会话 Cookie（跨源时也要，见 CORS `allow_credentials`）。
        ...(isServer ? {} : { credentials: 'include' as const }),
        // 服务端：一律 no-store，且**不**传 `next.revalidate`（RV-6：脱敏响应不得进 Data Cache）。
        ...(isServer
          ? { cache: 'no-store' as const }
          : typeof revalidate === 'number'
            ? { next: { revalidate } }
            : {}),
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
        const error = new ApiError(response.status, payload);
        if (!isServer) announceAuthFailure(error);
        throw error;
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
  return request;
}

export function createApi(ctx: ApiContext = {}) {
  const request = makeRequest(ctx);

  return {
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

    createReport: (instrumentId: string, body: CreateReportBody = {}, options: { override?: boolean } = {}) =>
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

    ask: (reportId: string, body: AskRequest) =>
      request<AskOut | AskQueuedResponse>(`/reports/${encodeURIComponent(reportId)}/ask`, {
        method: 'POST',
        body: { mode: 'cite', ...body },
        // `cite` answers inline; `deep` returns 202 at once. This budget covers a
        // cold API, never a generation.
        timeoutMs: 20000,
      }),

    askById: (askId: string) => request<AskOut>(`/asks/${encodeURIComponent(askId)}`),

    reportAsks: (
      reportId: string,
      limit = 20,
      options: { scope?: 'mine'; signal?: AbortSignal } = {},
    ) => request<AskListResponse>(
      `/reports/${encodeURIComponent(reportId)}/asks${queryString({
        limit, scope: options.scope,
      })}`,
      { signal: options.signal },
    ),

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

    watchlist: () => request<WatchlistResponse>('/watchlist'),

    addWatchlist: (instrumentId: string) =>
      request<WatchlistResponse>('/watchlist', { method: 'POST', body: { instrumentId } }),

    removeWatchlist: (instrumentId: string) =>
      request<void>(`/watchlist/${encodeURIComponent(instrumentId)}`, { method: 'DELETE' }),

    reorderWatchlist: (ids: string[]) =>
      request<WatchlistResponse>('/watchlist/reorder', { method: 'PATCH', body: { ids } }),

    /* ── ★X6 认证 / 账号（§24.5.1）──────────────────────────────────────── */

    /** 渲染注册页规则提示的唯一来源（规则不在前端写死第二份）。 */
    authConfig: () => request<AuthConfig>('/auth/config'),

    /** 永不 401：guest 返回 `authenticated:false`。 */
    me: (timeoutMs?: number) => request<Me>('/auth/me', { timeoutMs }),

    captcha: (purpose: 'register' | 'login' | 'recover') => request<CaptchaChallenge>(`/auth/captcha?purpose=${purpose}`),

    login: (body: LoginBody) => request<Me>('/auth/login', { method: 'POST', body }),

    register: (body: RegisterBody) => request<RegisterResult>('/auth/register', { method: 'POST', body }),

    /** ★Y3：恢复码 + 验证码 + 新密码。成功体是**新**恢复码（只出现一次）；不自动登录。 */
    recover: (body: RecoverBody) => request<RecoveryKeyResult>('/auth/recover', { method: 'POST', body }),

    /** ★Y3：登录态签发/轮换恢复码（需当前密码）。 */
    rotateRecoveryKey: (currentPassword: string) =>
      request<RecoveryKeyResult>('/auth/recovery-key', { method: 'POST', body: { currentPassword } }),

    /** ★Y4：自助注销（密码 + 键入用户名）。成功 204，服务端清 Cookie。 */
    deleteAccount: (body: DeleteAccountBody) => request<void>('/auth/account', { method: 'DELETE', body }),

    /** 幂等：无会话也返回 204。 */
    logout: () => request<void>('/auth/logout', { method: 'POST' }),

    changePassword: (body: { currentPassword: string; newPassword: string }) =>
      request<void>('/auth/password', { method: 'POST', body }),

    sessions: () => request<{ items: SessionOut[] }>('/auth/sessions'),

    revokeSession: (sessionId: string) =>
      request<void>(`/auth/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),

    revokeOtherSessions: () => request<{ revoked: number }>('/auth/sessions/revoke-others', { method: 'POST' }),

    updateProfile: (body: { displayName: string | null }) => request<Me>('/auth/profile', { method: 'PATCH', body }),

    /* ── ★X4 额度（§24.5.2）──────────────────────────────────────────────── */

    myQuota: () => request<QuotaStatus>('/me/quota'),

    quotaLedger: (params: { limit?: number; before?: string | null } = {}) =>
      request<QuotaLedgerResponse>(
        `/me/quota-ledger${queryString({ limit: params.limit ?? 50, before: params.before })}`,
      ),

    createQuotaRequest: (body: { resource: QuotaResource; amount: number; reason: string }) =>
      request<QuotaRequestOut>('/me/quota-requests', { method: 'POST', body }),

    myQuotaRequests: () => request<{ items: QuotaRequestOut[] }>('/me/quota-requests'),
  };
}

export type Api = ReturnType<typeof createApi>;

/** 浏览器 / 无身份实例。服务端页面一律用 `await serverApi()`（会转发 Cookie）。 */
export const api: Api = createApi({});

export { PUBLIC_BASE, SERVER_BASE };
