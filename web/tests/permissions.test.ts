import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  ALL_PERMISSIONS,
  GUEST_PERMISSIONS,
  can,
  isKnownPermission,
  permissionsForRole,
  roleHas,
} from '@/lib/permissions';

/**
 * AC-175 / E-12：权限矩阵的单一真源是仓库根 `contracts/permissions.json`。
 * 这里**独立**读 JSON、独立展开，再与 `lib/permissions.ts` 的结果逐角色比对——
 * 任何一侧（TS 展开逻辑、JSON 内容）漂移都会红。Python 侧有同构的 `test_contract_permissions.py`。
 */

interface Contract {
  roles: Record<'guest' | 'user' | 'admin' | 'root', string[]>;
  service: { inherits: 'admin'; minus: string[] };
}

const CONTRACT = JSON.parse(readFileSync(join(process.cwd(), '..', 'contracts', 'permissions.json'), 'utf8')) as Contract;

function expand(role: keyof Contract['roles']): Set<string> {
  const out = new Set<string>();
  for (const entry of CONTRACT.roles[role]) {
    if (entry.startsWith('@')) expand(entry.slice(1) as keyof Contract['roles']).forEach((p) => out.add(p));
    else out.add(entry);
  }
  return out;
}

describe('permissions 契约（AC-175）', () => {
  it.each(['guest', 'user', 'admin', 'root'] as const)('%s：TS 展开与独立展开逐项相等', (role) => {
    expect([...permissionsForRole(role)].sort()).toEqual([...expand(role)].sort());
  });

  it('service = admin 减去 minus（RV-1/RV-17：令牌泄露的最坏后果被词表收窄）', () => {
    const expected = [...expand('admin')].filter((p) => !CONTRACT.service.minus.includes(p)).sort();
    expect([...permissionsForRole('service')]).toEqual(expected);
    // 能改“人、钱、门”的权限一个都不能留
    for (const forbidden of ['admin.users.manage', 'admin.settings.manage', 'admin.invites.manage', 'admin.quota.manage', 'cost.manage']) {
      expect(roleHas('service', forbidden)).toBe(false);
    }
    // 但保留运维要用的
    for (const kept of ['report.generate', 'report.manage_any', 'cost.read', 'engine.probe', 'admin.audit.read']) {
      expect(roleHas('service', kept)).toBe(true);
    }
  });

  it('root ⊋ admin ⊋ user ⊋ guest（严格包含）', () => {
    const sets = (['guest', 'user', 'admin', 'root'] as const).map((role) => new Set(permissionsForRole(role)));
    for (let index = 1; index < sets.length; index += 1) {
      const lower = sets[index - 1]!;
      const upper = sets[index]!;
      for (const permission of lower) expect(upper.has(permission)).toBe(true);
      expect(upper.size).toBeGreaterThan(lower.size);
    }
  });

  it('关键裁决：cost.manage 只给 root；admin.admins.manage 只给 root', () => {
    expect(roleHas('root', 'cost.manage')).toBe(true);
    expect(roleHas('admin', 'cost.manage')).toBe(false);
    expect(roleHas('root', 'admin.admins.manage')).toBe(true);
    expect(roleHas('admin', 'admin.admins.manage')).toBe(false);
  });

  it('游客的权限集 = guest：能读、不能生成', () => {
    expect(GUEST_PERMISSIONS).toContain('report.read');
    expect(GUEST_PERMISSIONS).toContain('ask.cite');
    expect(GUEST_PERMISSIONS).not.toContain('report.generate');
    expect(GUEST_PERMISSIONS).not.toContain('watchlist.own');
  });
});

describe('can()', () => {
  it('依据服务端下发的 me.permissions，而不是自己推断角色', () => {
    expect(can({ permissions: ['report.generate'] }, 'report.generate')).toBe(true);
    expect(can({ permissions: ['report.generate'] }, 'cost.read')).toBe(false);
  });

  it('me 缺失时退化为 guest 集合，而不是“全放行”', () => {
    expect(can(null, 'report.read')).toBe(true);
    expect(can(undefined, 'report.generate')).toBe(false);
  });
});

describe('前端源码里用到的权限串都在词表里（防拼写漂移）', () => {
  function walk(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
      if (name === 'node_modules' || name.startsWith('.')) continue;
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full, out);
      else if (/\.(ts|tsx)$/.test(name)) out.push(full);
    }
    return out;
  }

  it('useCan(...) / can(me, ...) 的字面量全部是已知权限', () => {
    const root = process.cwd();
    const used = new Set<string>();
    for (const dir of ['lib', 'components', 'app']) {
      for (const file of walk(join(root, dir))) {
        const source = readFileSync(file, 'utf8');
        for (const match of source.matchAll(/useCan\(\s*'([a-z_.]+)'\s*\)/g)) used.add(match[1]!);
        for (const match of source.matchAll(/\bcan\(\s*[A-Za-z_.]+\s*,\s*'([a-z_.]+)'\s*\)/g)) used.add(match[1]!);
      }
    }
    expect(used.size).toBeGreaterThan(3);
    for (const permission of used) {
      expect(isKnownPermission(permission), `未知权限串：${permission}`).toBe(true);
    }
    expect(ALL_PERMISSIONS.length).toBeGreaterThan(15);
  });
});
