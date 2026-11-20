import type { AdminUser, Me } from '@/lib/types';

import { can } from '@/lib/permissions';

/**
 * 管理动作的**体验层**裁剪（§24.3.14 矩阵）。
 *
 * 只决定“要不要显示这个按钮”。授权永远在服务端（`assert_can_manage_user`），
 * 所以这里宁可保守：拿不准就不显示，服务端的 `403 / 422` 仍是最终裁决。
 */
export type UserAction =
  | 'disable'
  | 'enable'
  | 'resetPassword'
  | 'revokeSessions'
  | 'unlock'
  | 'adjustQuota'
  | 'delete'
  | 'promote'
  | 'demote';

export interface UserActionSet {
  /** 可见的动作集合（不含“查看详情”，那个人人都有）。 */
  actions: ReadonlySet<UserAction>;
  /** 完全只读：任何写动作都没有。 */
  readOnly: boolean;
  /** 目标就是当前登录者本人。 */
  self: boolean;
  /** 只读的原因，用于抽屉里给一句话说明。 */
  reason: 'none' | 'self' | 'protected' | 'deleted';
}

export function userActionsFor(me: Pick<Me, 'permissions' | 'user'>, target: AdminUser): UserActionSet {
  const canManage = can(me, 'admin.users.manage');
  const canQuota = can(me, 'admin.quota.manage');
  const canAppoint = can(me, 'admin.admins.manage');
  const self = Boolean(me.user && me.user.id === target.id);
  const out = new Set<UserAction>();

  const none = (reason: UserActionSet['reason']): UserActionSet => ({
    actions: out,
    readOnly: out.size === 0,
    self,
    reason: out.size === 0 ? reason : 'none',
  });

  if (target.status === 'deleted') return none('deleted');
  // root 永远只读（root_protected）；admin 只有 root 能动。
  if (target.role === 'root') return none('protected');
  if (target.role === 'admin' && !canAppoint) return none('protected');

  if (canQuota) out.add('adjustQuota');
  if (canManage) {
    if (target.locked) out.add('unlock');
    if (!self) {
      out.add(target.status === 'disabled' ? 'enable' : 'disable');
      out.add('resetPassword');
      out.add('revokeSessions');
      out.add('delete');
      if (canAppoint) out.add(target.role === 'admin' ? 'demote' : 'promote');
    }
  }
  return { actions: out, readOnly: out.size === 0, self, reason: self ? 'self' : 'none' };
}

/** 需要输入用户名才能确认的动作（§24.3.14）：删除、降级 admin、重置 admin 密码。 */
export function needsTypedConfirm(action: UserAction, target: Pick<AdminUser, 'role'>): boolean {
  if (action === 'delete' || action === 'demote') return true;
  if (action === 'resetPassword') return target.role === 'admin';
  return false;
}
