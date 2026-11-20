import type { AuditQuery, AdminUserQuery } from '@/lib/types';

/** 后台所有 TanStack Query 的 key 都从这里出，保证“改完哪些要作废”只有一处事实。 */
export const adminKeys = {
  all: ['admin'] as const,
  overview: ['admin', 'overview'] as const,
  users: (filters: Omit<AdminUserQuery, 'before' | 'limit'>) => ['admin', 'users', filters] as const,
  usersRoot: ['admin', 'users'] as const,
  user: (id: string) => ['admin', 'user', id] as const,
  invites: ['admin', 'invites'] as const,
  quotaRequests: (status: 'pending' | 'resolved' | 'all') => ['admin', 'quota-requests', status] as const,
  quotaRequestsRoot: ['admin', 'quota-requests'] as const,
  audit: (filters: Omit<AuditQuery, 'before' | 'limit'>) => ['admin', 'audit', filters] as const,
  settings: ['admin', 'settings'] as const,
  featured: ['admin', 'featured'] as const,
  budget: ['admin', 'budget'] as const,
};
