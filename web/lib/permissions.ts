/**
 * 权限词表与角色矩阵的前端侧（spec §24.3.4 / E-12 / AC-175）。
 *
 * 单一真源是仓库根的 `contracts/permissions.json`；Python 侧 `security/permissions.py` 与这里
 * 各自展开，`web/tests/permissions.test.ts` 与 `api/tests/test_contract_permissions.py` 双向断言
 * "展开后逐角色集合相等"，所以任何一侧漂移都会红。
 *
 * ★这里的 `can()` 只服务**体验层**（隐藏按钮、挑选文案）。它不是授权依据——授权永远在服务端
 * （默认拒绝的策略表，§24.3.3）。`Me.permissions` 由服务端展开后下发，前端不自己推断角色。
 */

import contract from '../../contracts/permissions.json';

import type { Me, Role } from './types';

type RoleName = 'guest' | 'user' | 'admin' | 'root';

interface PermissionContract {
  version: number;
  roles: Record<RoleName, string[]>;
  service: { inherits: RoleName; minus: string[] };
}

const CONTRACT = contract as unknown as PermissionContract;

/** 展开 `@role` 继承；循环继承在契约里不存在，这里仍做防御。 */
function expand(role: RoleName, seen: Set<RoleName> = new Set()): Set<string> {
  const out = new Set<string>();
  if (seen.has(role)) return out;
  seen.add(role);
  for (const entry of CONTRACT.roles[role] ?? []) {
    if (entry.startsWith('@')) {
      for (const inherited of expand(entry.slice(1) as RoleName, seen)) out.add(inherited);
    } else {
      out.add(entry);
    }
  }
  return out;
}

const EXPANDED: Record<RoleName, ReadonlySet<string>> = {
  guest: expand('guest'),
  user: expand('user'),
  admin: expand('admin'),
  root: expand('root'),
};

/** `service` = 继承 `inherits` 后减去 `minus`（令牌泄露的最坏后果被词表层面收窄）。 */
const SERVICE: ReadonlySet<string> = (() => {
  const base = new Set(expand(CONTRACT.service.inherits));
  for (const permission of CONTRACT.service.minus) base.delete(permission);
  return base;
})();

export const PERMISSION_CONTRACT_VERSION = CONTRACT.version;

/** 契约里出现过的全部权限串（词表）。 */
export const ALL_PERMISSIONS: readonly string[] = Array.from(
  new Set([...EXPANDED.root, ...SERVICE]),
).sort();

export function permissionsForRole(role: RoleName | 'service'): readonly string[] {
  const set = role === 'service' ? SERVICE : EXPANDED[role];
  return Array.from(set).sort();
}

export const GUEST_PERMISSIONS: readonly string[] = permissionsForRole('guest');

export function isKnownPermission(permission: string): boolean {
  return ALL_PERMISSIONS.includes(permission);
}

/** 按角色判断；`undefined/null` 当作 guest。 */
export function roleHas(role: Role | 'guest' | 'service' | null | undefined, permission: string): boolean {
  const key = role ?? 'guest';
  if (key === 'service') return SERVICE.has(permission);
  return EXPANDED[key].has(permission);
}

/**
 * 当前主体是否拥有某权限。依据是服务端下发的 `me.permissions`（已展开）；
 * `me` 缺失（尚未取到）时退化为 guest 集合，而不是"全放行"。
 */
export function can(me: Pick<Me, 'permissions'> | null | undefined, permission: string): boolean {
  const list = me?.permissions ?? GUEST_PERMISSIONS;
  return list.includes(permission);
}
