'use client';

import { useInfiniteQuery } from '@tanstack/react-query';
import { MoreHorizontal, Plus, Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { useCan, useMe } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { dateTime, relativeTime } from '@/lib/format';
import type { AdminUser, AdminUserQuery, Role } from '@/lib/types';

import { RoleBadge } from '../auth/RoleBadge';
import { Button } from '../ui/Button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/DropdownMenu';
import { Input } from '../ui/Input';
import { Segmented } from '../ui/Tabs';
import { StatusDot } from '../ui/StatusDot';
import { AdminPageHeader, EmptyState, ErrorState, TableSkeleton, UserCell, userStatusKind, userStatusLabel } from './AdminPrimitives';
import { BulkQuotaDialog, BULK_MAX_TARGETS } from './BulkQuotaDialog';
import { CreateUserDialog } from './CreateUserDialog';
import { ExportLink } from './ExportLink';
import { adminKeys } from './keys';
import { UserActionDialogs, type UserActionRequest } from './UserActionDialogs';
import { UserDrawer } from './UserDrawer';
import { userActionsFor, type UserAction } from './userActions';

const t = copyAdmin.users;

type RoleFilter = 'all' | Role;
type StatusFilter = 'all' | 'active' | 'disabled';

/** 服务端对 `q` 的规则：小写字母 / 数字 / 下划线，1–20 位；其它字符一律空结果。 */
const SEARCH_PATTERN = /^[a-z0-9_]{1,20}$/;

/** 行菜单里能出现的动作及其顺序（权限裁剪在 `userActionsFor`）。 */
const MENU_ORDER: { action: UserAction; label: string; danger?: boolean }[] = [
  { action: 'unlock', label: t.actions.unlock },
  { action: 'enable', label: t.actions.enable },
  { action: 'disable', label: t.actions.disable, danger: true },
  { action: 'resetPassword', label: t.actions.resetPassword, danger: true },
  { action: 'revokeSessions', label: t.actions.revokeSessions },
  { action: 'promote', label: t.actions.promote },
  { action: 'demote', label: t.actions.demote, danger: true },
  { action: 'delete', label: t.actions.delete, danger: true },
];

function usageText(user: AdminUser): { main: string; bonus: string | null } {
  const bucket = user.quota?.report;
  if (!bucket) return { main: copyAdmin.common.emDash, bonus: null };
  const main =
    bucket.limit === null
      ? `${bucket.usedToday}`
      : fill(t.usageValue, { used: bucket.usedToday, limit: bucket.limit });
  return { main, bonus: bucket.bonus > 0 ? fill(t.usageBonus, { n: bucket.bonus }) : null };
}

function RowMenu({
  user,
  onView,
  onAction,
}: {
  user: AdminUser;
  onView: () => void;
  onAction: (action: UserAction) => void;
}) {
  const me = useMe();
  const set = userActionsFor(me, user);
  const entries = MENU_ORDER.filter((item) => set.actions.has(item.action));
  const label = fill(t.rowMenu, { name: user.username });
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={label}
          title={label}
          className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] data-[state=open]:bg-[var(--bg-active)]"
        >
          <MoreHorizontal className="size-4" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" aria-label={label}>
        <DropdownMenuItem onSelect={onView}>{t.actions.view}</DropdownMenuItem>
        {entries.length > 0 ? <DropdownMenuSeparator /> : null}
        {entries.map((item) => (
          <DropdownMenuItem
            key={item.action}
            tone={item.danger ? 'danger' : 'default'}
            onSelect={() => onAction(item.action)}
          >
            {item.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * 用户表（§24.3.15d）：搜索（用户名前缀）+ 角色 / 状态筛选 + 游标分页；点行开右侧抽屉。
 *
 * 行菜单与抽屉的危险区共用同一个动作执行处（`UserActionDialogs`），所以确认话术与
 * type-to-confirm 规则只有一份。动作的**可见性**由 `userActionsFor`（§24.3.14 矩阵）裁剪——
 * admin 看不到任命 / 降级；对 admin / root 目标只读。这只是体验层，服务端仍逐次裁决。
 */
export function UsersTable({ initialLocked = false }: { initialLocked?: boolean }) {
  const me = useMe();
  const canManage = useCan('admin.users.manage');
  const canExport = useCan('admin.users.read');
  const canBulk = useCan('admin.quota.manage');
  // ★Y2(c)：`/admin/users?locked=1`（概览“被锁定账号”卡的去向）还原成一个可清除的筛选 chip。
  const [locked, setLocked] = useState(initialLocked);
  // ★Y5(b)：批量选择（跨“加载更多”保留；筛选变化时清空）。
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [applied, setApplied] = useState('');
  const [role, setRole] = useState<RoleFilter>('all');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [request, setRequest] = useState<UserActionRequest | null>(null);
  const [creating, setCreating] = useState(false);

  // 输入停顿 300ms 再查询，避免每个按键一次请求。
  useEffect(() => {
    const handle = setTimeout(() => setApplied(search.trim().toLowerCase()), 300);
    return () => clearTimeout(handle);
  }, [search]);

  const searchInvalid = applied !== '' && !SEARCH_PATTERN.test(applied);
  const filters = useMemo<Omit<AdminUserQuery, 'before' | 'limit'>>(
    () => ({
      q: applied === '' ? undefined : applied,
      role: role === 'all' ? undefined : role,
      status: status === 'all' ? undefined : status,
      locked: locked || undefined,
    }),
    [applied, role, status, locked],
  );

  // 筛选变了，旧的选择就不再是“眼前这批人”：清空，避免对看不见的人批量操作。
  const filtersKey = JSON.stringify(filters);
  const lastFilters = useRef(filtersKey);
  useEffect(() => {
    if (lastFilters.current !== filtersKey) {
      lastFilters.current = filtersKey;
      setPicked(new Set());
    }
  }, [filtersKey]);

  const query = useInfiniteQuery({
    queryKey: adminKeys.users(filters),
    queryFn: ({ pageParam }) => adminApi.adminUsers({ ...filters, before: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    // 非法的前缀搜索不发请求：服务端也只会回空，省一次往返并给出明确提示。
    enabled: !searchInvalid,
  });

  const users = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const total = query.data?.pages[0]?.total;
  const selected = users.find((user) => user.id === selectedId) ?? null;
  const filtered = applied !== '' || role !== 'all' || status !== 'all' || locked;

  const clearLocked = () => {
    if (!locked) return;
    setLocked(false);
    // 地址栏同步去掉 `?locked=1`（Next 15 会把 replaceState 并入路由器状态）。
    if (typeof window !== 'undefined' && window.location.search.includes('locked')) {
      window.history.replaceState(null, '', window.location.pathname);
    }
  };

  const clearFilters = () => {
    setSearch('');
    setApplied('');
    setRole('all');
    setStatus('all');
    clearLocked();
  };

  const roleOptions = [
    { value: 'all' as const, label: t.roleAll },
    { value: 'user' as const, label: copy.auth.roles.user },
    { value: 'admin' as const, label: copy.auth.roles.admin },
    { value: 'root' as const, label: copy.auth.roles.root },
  ];
  const statusOptions = [
    { value: 'all' as const, label: t.statusAll },
    { value: 'active' as const, label: copyAdmin.common.status.active },
    { value: 'disabled' as const, label: copyAdmin.common.status.disabled },
  ];

  const selectable = (user: AdminUser) => userActionsFor(me, user).actions.has('adjustQuota');
  const pageSelectable = users.filter(selectable);
  const allOnPageSelected = pageSelectable.length > 0 && pageSelectable.every((user) => picked.has(user.id));
  const someOnPageSelected = pageSelectable.some((user) => picked.has(user.id));
  const atLimit = picked.size >= BULK_MAX_TARGETS;

  const toggleOne = (user: AdminUser) =>
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(user.id)) next.delete(user.id);
      else if (next.size < BULK_MAX_TARGETS) next.add(user.id);
      return next;
    });

  const toggleAllOnPage = () =>
    setPicked((current) => {
      const next = new Set(current);
      if (allOnPageSelected) pageSelectable.forEach((user) => next.delete(user.id));
      else for (const user of pageSelectable) if (next.size < BULK_MAX_TARGETS) next.add(user.id);
      return next;
    });

  const openAction = (action: UserAction, user: AdminUser) => setRequest({ action, user });
  const openView = (user: AdminUser) => setSelectedId(user.id);

  return (
    <div>
      <AdminPageHeader
        title={t.title}
        subtitle={t.subtitle}
        actions={
          <>
            {canExport ? (
              <ExportLink
                href={adminApi.adminUsersExportUrl({
                  q: filters.q,
                  role: filters.role,
                  status: filters.status,
                  locked: locked || undefined,
                })}
                label={t.export}
                hint={t.exportHint}
                started={t.exportStarted}
              />
            ) : null}
            {canManage ? (
              <Button variant="primary" leadingIcon={<Plus className="size-3.5" aria-hidden="true" />} onClick={() => setCreating(true)}>
                {t.create}
              </Button>
            ) : null}
          </>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="min-w-[200px] flex-1 max-w-[320px]">
          <label htmlFor="users-search" className="sr-only">
            {t.searchLabel}
          </label>
          <Input
            id="users-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onClear={() => setSearch('')}
            placeholder={t.searchPlaceholder}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            leadingIcon={<Search className="size-3.5" />}
            invalid={searchInvalid}
            aria-describedby={searchInvalid ? 'users-search-error' : undefined}
            inputSize="sm"
            className="font-mono"
          />
        </div>
        <Segmented options={roleOptions} value={role} onChange={setRole} ariaLabel={t.roleFilter} />
        <Segmented options={statusOptions} value={status} onChange={setStatus} ariaLabel={t.statusFilter} />
        {locked ? (
          <span
            data-testid="locked-chip"
            className="inline-flex h-7 items-center gap-1 rounded-full bg-[var(--warn-weak)] pl-2.5 pr-1 text-[11.5px] text-[var(--warn)]"
          >
            {t.lockedChip}
            <button
              type="button"
              onClick={clearLocked}
              aria-label={t.lockedChipClear}
              className="flex size-5 items-center justify-center rounded-full hover:bg-[var(--warn)]/15"
            >
              <X className="size-3" aria-hidden="true" />
            </button>
          </span>
        ) : null}
        {total !== undefined ? (
          <span className="num ml-auto text-[11.5px] text-[var(--text-tertiary)]">{fill(t.total, { n: total })}</span>
        ) : null}
      </div>
      <div id="users-search-error" aria-live="polite" className="mb-2 min-h-0 text-[11.5px] text-[var(--danger)]">
        {searchInvalid ? t.searchInvalid : null}
      </div>

      {canBulk && picked.size > 0 ? (
        <div
          role="toolbar"
          aria-label={t.select.toolbar}
          data-testid="bulk-toolbar"
          className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-md)] border border-[var(--accent)]/35 bg-[var(--bg-surface-2)] px-3 py-2 shadow-[var(--shadow-pop)]"
        >
          <span className="num text-[12.5px] font-medium text-[var(--text-primary)]" aria-live="polite">
            {fill(t.select.count, { n: picked.size })}
          </span>
          {atLimit ? (
            <span className="text-[11.5px] text-[var(--warn)]">{fill(t.select.limit, { max: BULK_MAX_TARGETS })}</span>
          ) : null}
          <span className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>
              {t.select.clear}
            </Button>
            <Button size="sm" variant="primary" onClick={() => setBulkOpen(true)}>
              {t.select.bulk}
            </Button>
          </span>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)]">
        {query.isError ? (
          <div className="p-4">
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          </div>
        ) : query.isPending && !searchInvalid ? (
          <TableSkeleton />
        ) : users.length === 0 ? (
          <EmptyState
            title={t.empty}
            hint={t.emptyHint}
            action={
              filtered ? (
                <Button size="sm" variant="secondary" onClick={clearFilters}>
                  {t.emptyFiltered}
                </Button>
              ) : null
            }
          />
        ) : (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-[12.5px]">
              <caption className="sr-only">{t.title}</caption>
              <thead>
                <tr className="border-b border-[var(--border-subtle)] text-[11px] text-[var(--text-tertiary)]">
                  {canBulk ? (
                    <th scope="col" className="w-9 py-2 pl-3 pr-0">
                      <input
                        type="checkbox"
                        aria-label={t.select.all}
                        checked={allOnPageSelected}
                        ref={(node) => {
                          if (node) node.indeterminate = !allOnPageSelected && someOnPageSelected;
                        }}
                        disabled={pageSelectable.length === 0}
                        onChange={toggleAllOnPage}
                        className="size-3.5 accent-[var(--accent)]"
                      />
                    </th>
                  ) : null}
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.columns.user}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.columns.role}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.columns.status}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t.columns.usage}
                  </th>
                  <th scope="col" className="hidden px-3 py-2 font-medium min-[900px]:table-cell">
                    {t.columns.lastLogin}
                  </th>
                  <th scope="col" className="hidden px-3 py-2 font-medium min-[900px]:table-cell">
                    {t.columns.createdAt}
                  </th>
                  <th scope="col" className="w-10 px-3 py-2">
                    <span className="sr-only">{t.columns.actions}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => {
                  const usage = usageText(user);
                  return (
                    <tr
                      key={user.id}
                      data-testid={`user-row-${user.username}`}
                      aria-selected={selectedId === user.id || undefined}
                      onClick={() => openView(user)}
                      className={cn(
                        'cursor-pointer border-b border-[var(--border-subtle)] transition-colors last:border-0 hover:bg-[var(--bg-hover)]',
                        selectedId === user.id && 'bg-[var(--bg-active)]',
                      )}
                    >
                      {canBulk ? (
                        <td className="w-9 py-2 pl-3 pr-0" onClick={(event) => event.stopPropagation()}>
                          <input
                            type="checkbox"
                            aria-label={fill(t.select.row, { name: user.username })}
                            checked={picked.has(user.id)}
                            disabled={!selectable(user) || (atLimit && !picked.has(user.id))}
                            onChange={() => toggleOne(user)}
                            className="size-3.5 accent-[var(--accent)]"
                          />
                        </td>
                      ) : null}
                      <td className="max-w-[220px] px-3 py-2">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            openView(user);
                          }}
                          className="block w-full min-w-0 text-left"
                        >
                          <UserCell username={user.username} displayName={user.displayName} />
                        </button>
                      </td>
                      <td className="px-3 py-2">
                        <RoleBadge role={user.role} />
                      </td>
                      <td className="px-3 py-2">
                        <StatusDot status={userStatusKind(user)} label={userStatusLabel(user)} />
                      </td>
                      <td className="num px-3 py-2 text-[var(--text-secondary)]">
                        {usage.main}
                        {usage.bonus ? <span className="ml-1.5 text-[11px] text-[var(--accent)]">{usage.bonus}</span> : null}
                      </td>
                      <td
                        className="hidden px-3 py-2 text-[var(--text-tertiary)] min-[900px]:table-cell"
                        title={user.lastLoginAt ? dateTime(user.lastLoginAt) : undefined}
                      >
                        {user.lastLoginAt ? relativeTime(user.lastLoginAt) : copyAdmin.common.never}
                      </td>
                      <td className="hidden px-3 py-2 text-[var(--text-tertiary)] min-[900px]:table-cell">
                        {dateTime(user.createdAt)}
                      </td>
                      <td className="px-3 py-2 text-right" onClick={(event) => event.stopPropagation()}>
                        <RowMenu user={user} onView={() => openView(user)} onAction={(action) => openAction(action, user)} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {query.hasNextPage ? (
        <div className="mt-3 text-center">
          <Button size="sm" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {copyAdmin.common.loadMore}
          </Button>
        </div>
      ) : null}

      <UserDrawer
        userId={selectedId}
        summary={selected}
        onClose={() => setSelectedId(null)}
        onAction={openAction}
      />
      <UserActionDialogs request={request} onClose={() => setRequest(null)} />
      <CreateUserDialog open={creating} onOpenChange={setCreating} />
      <BulkQuotaDialog
        open={bulkOpen}
        onOpenChange={setBulkOpen}
        userIds={Array.from(picked)}
        onDone={() => setPicked(new Set())}
      />
    </div>
  );
}
