/**
 * 管理后台客户端（★Y2(d) / F-23）——只被 `components/admin/*` 与 `app/admin/*` import。
 *
 * 从 `lib/api.ts` 拆出：20 余个 `admin*` 方法与它们的类型不再进入**首屏** chunk。请求骨架
 * （超时 / Cookie / 401·403 广播 / 错误归一）复用 `makeRequest`，凭据面与 `api.ts` 完全一致
 * （AC-206 的扫描同时覆盖这个文件）：这里没有任何服务凭据，身份只来自 Cookie 会话。
 */

import { apiBase, makeRequest, queryString, type ApiContext } from './api';
import type {
  AdminBulkQuotaBody,
  AdminBulkQuotaResult,
  AdminCreateUserBody,
  AdminOverview,
  AdminPatchUserBody,
  AdminQuotaBody,
  AdminQuotaRequest,
  AdminSettings,
  AdminUser,
  AdminUserDetail,
  AdminUserListResponse,
  AdminUserQuery,
  AuditQuery,
  AuditRow,
  InviteCreateBody,
  InviteOut,
  QuotaRequestOut,
  QuotaStatus,
  WatchlistEntry,
} from './types';

export function createAdminApi(ctx: ApiContext = {}) {
  const request = makeRequest(ctx);

  return {
        adminOverview: () => request<AdminOverview>('/admin/overview'),

    adminUsers: (params: AdminUserQuery = {}) =>
      request<AdminUserListResponse>(
        `/admin/users${queryString({
          q: params.q,
          role: params.role,
          status: params.status,
          locked: params.locked ? 1 : undefined,
          limit: params.limit ?? 50,
          before: params.before,
        })}`,
      ),

    adminCreateUser: (body: AdminCreateUserBody) =>
      request<{ user: AdminUser; temporaryPassword?: string }>('/admin/users', { method: 'POST', body }),

    adminUser: (userId: string) => request<AdminUserDetail>(`/admin/users/${encodeURIComponent(userId)}`),

    adminPatchUser: (userId: string, body: AdminPatchUserBody) =>
      request<AdminUser>(`/admin/users/${encodeURIComponent(userId)}`, { method: 'PATCH', body }),

    /** 临时密码**只在本响应出现一次**。 */
    adminResetPassword: (userId: string) =>
      request<{ temporaryPassword: string }>(`/admin/users/${encodeURIComponent(userId)}/reset-password`, {
        method: 'POST',
      }),

    adminRevokeSessions: (userId: string) =>
      request<{ revoked: number }>(`/admin/users/${encodeURIComponent(userId)}/revoke-sessions`, {
        method: 'POST',
      }),

    /** ★M52（F-16）：逐条吊销一个会话；§24.5.3 原只有“全部吊销”，后端已在 B-19 补此端点。 */
    adminRevokeSession: (userId: string, sessionId: string) =>
      request<void>(
        `/admin/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}`,
        { method: 'DELETE' },
      ),

    adminUnlockUser: (userId: string) =>
      request<void>(`/admin/users/${encodeURIComponent(userId)}/unlock`, { method: 'POST' }),

    adminAdjustQuota: (userId: string, body: AdminQuotaBody) =>
      request<QuotaStatus>(`/admin/users/${encodeURIComponent(userId)}/quota`, { method: 'POST', body }),

    /** `confirm` 必须等于目标用户名（后端也校验，不只靠前端的 type-to-confirm）。 */
    adminDeleteUser: (userId: string, confirm: string) =>
      request<void>(`/admin/users/${encodeURIComponent(userId)}${queryString({ confirm })}`, {
        method: 'DELETE',
      }),

    /** ★Y5(b)：批量赠送/收回额度（两遍制，全成全败；−50..50）。 */
    adminBulkQuota: (body: AdminBulkQuotaBody) =>
      request<AdminBulkQuotaResult>('/admin/users/bulk-quota', { method: 'POST', body }),

    /**
     * ★Y5(a)：CSV 导出的**直链**（浏览器 `<a href download>` 带 Cookie 直取，不经 fetch / 不进内存）。
     * 过滤参数与对应列表接口完全相同；`limit/before` 不适用。
     */
    adminUsersExportUrl: (params: AdminUserQuery = {}) =>
      `${apiBase()}/api/v1/admin/users/export.csv${queryString({
        q: params.q,
        role: params.role,
        status: params.status,
        locked: params.locked ? 1 : undefined,
      })}`,

    adminAuditExportUrl: (params: AuditQuery = {}) =>
      `${apiBase()}/api/v1/admin/audit/export.csv${queryString({
        action: params.action,
        actor: params.actor,
        target: params.target,
        from: params.from,
        to: params.to,
      })}`,

    adminInvites: () => request<{ items: InviteOut[] }>('/admin/invites'),

    adminCreateInvite: (body: InviteCreateBody) => request<InviteOut>('/admin/invites', { method: 'POST', body }),

    adminRevokeInvite: (inviteId: string) =>
      request<void>(`/admin/invites/${encodeURIComponent(inviteId)}`, { method: 'DELETE' }),

    /**
     * 后端的 `status` 只有 `pending | approved | rejected`（M50）；“已处理 / 全部”是前端的并集：
     * 并行取各状态再按处理时间倒序合并（每个状态至多 50 条，足够一屏）。
     */
    adminQuotaRequests: async (
      status: 'pending' | 'approved' | 'rejected' | 'resolved' | 'all' = 'pending',
    ): Promise<{ items: AdminQuotaRequest[]; total: number }> => {
      const fetchOne = (one: 'pending' | 'approved' | 'rejected') =>
        request<{ items: AdminQuotaRequest[]; total: number }>(`/admin/quota-requests?status=${one}`);
      if (status !== 'resolved' && status !== 'all') return fetchOne(status);
      const parts = await Promise.all(
        (status === 'all' ? (['pending', 'approved', 'rejected'] as const) : (['approved', 'rejected'] as const)).map(
          fetchOne,
        ),
      );
      const items = parts
        .flatMap((part) => part.items)
        .sort((a, b) => (b.resolvedAt ?? b.createdAt).localeCompare(a.resolvedAt ?? a.createdAt));
      return { items, total: parts.reduce((sum, part) => sum + part.total, 0) };
    },

    adminResolveQuotaRequest: (
      requestId: string,
      body: { decision: 'approve' | 'reject'; grant?: number; note?: string },
    ) =>
      request<QuotaRequestOut>(`/admin/quota-requests/${encodeURIComponent(requestId)}/resolve`, {
        method: 'POST',
        body,
      }),

    adminAudit: (params: AuditQuery = {}) =>
      request<{ items: AuditRow[]; nextCursor?: string | null }>(
        `/admin/audit${queryString({
          action: params.action,
          actor: params.actor,
          target: params.target,
          from: params.from,
          to: params.to,
          limit: params.limit ?? 50,
          before: params.before,
        })}`,
      ),

    adminSettings: () => request<AdminSettings>('/admin/settings'),

    adminPatchSettings: (body: Partial<AdminSettings>) =>
      request<AdminSettings>('/admin/settings', { method: 'PATCH', body }),

    adminFeatured: () => request<{ items: WatchlistEntry[] }>('/admin/featured-watchlist'),

    adminAddFeatured: (instrumentId: string) =>
      request<{ items: WatchlistEntry[] }>('/admin/featured-watchlist', {
        method: 'POST',
        body: { instrumentId },
      }),

    adminRemoveFeatured: (instrumentId: string) =>
      request<void>(`/admin/featured-watchlist/${encodeURIComponent(instrumentId)}`, { method: 'DELETE' }),
  };
}

export type AdminApi = ReturnType<typeof createAdminApi>;

export const adminApi: AdminApi = createAdminApi({});
