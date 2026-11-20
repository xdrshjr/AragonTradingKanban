'use client';

import { ChevronDown, LogOut, ShieldCheck, UserRound } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import { isGuest, useAuthActions, useAuthUi, useCan, useMe } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import type { AuthUser } from '@/lib/types';

import { notify } from '../ui/Toast';
import { ACCENT_TEXT } from './Field';
import { RoleBadge } from './RoleBadge';

function displayOf(user: AuthUser): string {
  return user.displayName?.trim() || user.username;
}

const ITEM_CLASS =
  'flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-left text-[12.5px] text-[var(--text-secondary)] outline-none transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] focus-visible:bg-[var(--bg-hover)] focus-visible:text-[var(--text-primary)]';

function SignedInMenu({ user }: { user: AuthUser }) {
  const router = useRouter();
  const canAdmin = useCan('admin.overview.read');
  const { signOut } = useAuthActions(() => router.refresh());
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const name = displayOf(user);

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    // 打开时把焦点交给第一项（键盘用户不必再按一次 ↓）。
    panelRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  const onPanelKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close(true);
      return;
    }
    if (event.key === 'Tab') {
      setOpen(false);
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Home' && event.key !== 'End') return;
    const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    if (items.length === 0) return;
    event.preventDefault();
    const index = items.findIndex((item) => item === document.activeElement);
    let next = index;
    if (event.key === 'ArrowDown') next = (index + 1) % items.length;
    else if (event.key === 'ArrowUp') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else next = items.length - 1;
    items[next]?.focus();
  };

  return (
    <div ref={rootRef} className="relative" data-print="hide">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${copy.auth.menu.label}：${name}`}
        data-testid="user-menu-trigger"
        className={cn(
          'flex h-7 items-center gap-1.5 rounded-full border border-[var(--border-strong)] bg-[var(--bg-surface-2)] py-0.5 pl-0.5 pr-2 text-[12px]',
          'text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-surface-3)] hover:text-[var(--text-primary)]',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'flex size-6 items-center justify-center rounded-full text-[11px] font-semibold uppercase',
            user.role === 'root'
              ? 'bg-[var(--accent)] text-[var(--accent-text)]'
              : 'bg-[var(--bg-surface-3)] text-[var(--text-primary)]',
          )}
        >
          {name.slice(0, 1)}
        </span>
        <span className="hidden max-w-[110px] truncate sm:inline">{name}</span>
        <ChevronDown className="size-3" aria-hidden="true" />
      </button>

      {open ? (
        <div
          ref={panelRef}
          role="menu"
          aria-label={copy.auth.menu.label}
          onKeyDown={onPanelKeyDown}
          className="absolute right-0 top-9 z-30 w-[232px] rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]"
        >
          <div className="px-2 pb-2 pt-1.5">
            <div className="text-[10.5px] text-[var(--text-tertiary)]">{copy.auth.menu.signedInAs}</div>
            <div className="mt-0.5 flex items-center gap-1.5">
              <span className="min-w-0 truncate text-[12.5px] font-medium text-[var(--text-primary)]">{name}</span>
              <RoleBadge role={user.role} />
            </div>
            {user.displayName ? (
              <div className="mono mt-0.5 truncate text-[10.5px] text-[var(--text-tertiary)]">@{user.username}</div>
            ) : null}
          </div>
          <div className="my-1 h-px bg-[var(--border-subtle)]" role="separator" />
          <Link href="/account" role="menuitem" className={ITEM_CLASS} onClick={() => close(false)}>
            <UserRound className="size-3.5" aria-hidden="true" />
            {copy.auth.menu.account}
          </Link>
          {canAdmin ? (
            <Link href="/admin" role="menuitem" className={ITEM_CLASS} onClick={() => close(false)}>
              <ShieldCheck className="size-3.5" aria-hidden="true" />
              {copy.auth.menu.admin}
            </Link>
          ) : null}
          <div className="my-1 h-px bg-[var(--border-subtle)]" role="separator" />
          <button
            type="button"
            role="menuitem"
            className={ITEM_CLASS}
            onClick={() => {
              close(false);
              void signOut().then(() => notify.info(copy.auth.signedOut));
            }}
          >
            <LogOut className="size-3.5" aria-hidden="true" />
            {copy.auth.signOut}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 头部右上的用户入口（首屏静态代码，所以刻意手写、不引 Radix DropdownMenu）。
 *
 * * 游客：`登录` + `注册` 两个按钮——点开的是**懒加载**的 `AuthDialog`（弹窗，不跳走）；
 * * 已登录：头像 + 名称 + 菜单（账号与额度 / 管理后台〔有 `admin.overview.read` 才出现〕/ 退出登录）；
 * * `AUTH_MODE=open`（无主体）：不渲染——那是“服务账号”，没有“登录/退出”可言。
 */
export function UserMenu({ className }: { className?: string }) {
  const me = useMe();
  const openAuth = useAuthUi((state) => state.openAuth);

  if (isGuest(me)) {
    return (
      <div className={cn('flex items-center gap-1', className)} data-print="hide">
        <button
          type="button"
          onClick={() => openAuth('login', { type: 'generic' })}
          className="h-7 rounded-[var(--radius-sm)] px-2.5 text-[12px] font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
        >
          {copy.auth.signIn}
        </button>
        <button
          type="button"
          onClick={() => openAuth('register', { type: 'generic' })}
          style={ACCENT_TEXT}
          className="hidden h-7 rounded-[var(--radius-sm)] border border-[var(--accent)]/30 bg-[var(--accent-weak)] px-2.5 text-[12px] font-medium text-[var(--accent)] transition-colors hover:bg-[var(--accent)]/20 sm:block"
        >
          {copy.auth.signUp}
        </button>
      </div>
    );
  }
  if (!me.user) return null;
  return (
    <div className={className}>
      <SignedInMenu user={me.user} />
    </div>
  );
}
