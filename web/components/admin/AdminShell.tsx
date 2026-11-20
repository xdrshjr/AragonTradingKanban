'use client';

import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Gauge, History, KeyRound, Settings2, Ticket, Users } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { adminApi } from '@/lib/api.admin';
import { useCan } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';

import { UserMenu } from '../auth/UserMenu';
import { BrandMark } from '../ui/BrandMark';
import { adminKeys } from './keys';

interface NavItem {
  href: string;
  label: string;
  icon: React.ReactNode;
  /** `/admin` 只在精确匹配时高亮，其它按前缀。 */
  exact?: boolean;
  /** 需要的权限；缺省不额外要求（进入后台本身已要求 `admin.overview.read`）。 */
  permission?: string;
  badge?: number;
}

const ICON = 'size-3.5 shrink-0';

function isActive(pathname: string | null, item: NavItem): boolean {
  if (!pathname) return false;
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

function currentLabel(pathname: string | null, items: NavItem[]): string {
  return items.find((item) => isActive(pathname, item))?.label ?? copyAdmin.nav.overview;
}

/**
 * 后台外壳（§24.3.15d）：顶部 52px 头（品牌 + 面包屑 + `UserMenu`）、左 200px 导航、右侧内容区 `max-w-[1120px]`。
 *
 * 窄屏（< 900px）导航收成顶部横向 Tabs 的外观——但它们是**真链接**（路由切换），所以用
 * `<nav>` + `aria-current="page"`，而不是 Radix `Tabs`（`role=tab` 不能承载路由跳转）。
 * 整个外壳只在 `/admin/**` 路由使用，不进工作台首屏包。
 *
 * 页面自己滚动：`body { overflow: hidden }` 是工作台的设定，这里让内容区成为唯一的滚动容器。
 */
export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const canRead = useCan('admin.quota.manage');

  // 待处理额度申请的红点数字：失败就安静（它只是个提示，不该让整个外壳报错）。
  const pending = useQuery({
    queryKey: adminKeys.quotaRequests('pending'),
    queryFn: () => adminApi.adminQuotaRequests('pending'),
    enabled: canRead,
    refetchInterval: 60_000,
    retry: false,
  });
  const pendingCount = pending.data?.total ?? 0;

  const items: NavItem[] = [
    { href: '/admin', label: copyAdmin.nav.overview, icon: <Gauge className={ICON} aria-hidden="true" />, exact: true },
    { href: '/admin/users', label: copyAdmin.nav.users, icon: <Users className={ICON} aria-hidden="true" /> },
    { href: '/admin/invites', label: copyAdmin.nav.invites, icon: <Ticket className={ICON} aria-hidden="true" /> },
    {
      href: '/admin/quota',
      label: copyAdmin.nav.quota,
      icon: <KeyRound className={ICON} aria-hidden="true" />,
      badge: pendingCount,
    },
    { href: '/admin/audit', label: copyAdmin.nav.audit, icon: <History className={ICON} aria-hidden="true" /> },
    { href: '/admin/settings', label: copyAdmin.nav.settings, icon: <Settings2 className={ICON} aria-hidden="true" /> },
  ];

  return (
    <div className="flex h-dvh flex-col bg-[var(--bg-canvas)] text-[var(--text-primary)]">
      <a
        href="#admin-main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-2 focus:z-50 focus:rounded focus:bg-[var(--bg-surface-3)] focus:px-3 focus:py-1.5 focus:text-[12px]"
      >
        {copyAdmin.nav.skip}
      </a>

      <header className="flex h-[var(--header-h)] shrink-0 items-center justify-between gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-4">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/" className="flex items-center gap-2" aria-label={`${copy.brand} ${copy.brandSuffix}`}>
            <BrandMark />
            <span className="hidden text-[13px] font-semibold tracking-tight sm:inline">{copy.brand}</span>
          </Link>
          <span aria-hidden="true" className="text-[var(--text-tertiary)]">
            /
          </span>
          <nav aria-label={copyAdmin.nav.breadcrumb} className="flex min-w-0 items-center gap-1.5 text-[12.5px]">
            <Link
              href="/admin"
              className="text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
            >
              {copyAdmin.title}
            </Link>
            {pathname !== '/admin' ? (
              <>
                <span aria-hidden="true" className="text-[var(--text-tertiary)]">
                  /
                </span>
                <span aria-current="page" className="truncate font-medium text-[var(--text-primary)]">
                  {currentLabel(pathname, items)}
                </span>
              </>
            ) : null}
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/"
            className="hidden items-center gap-1.5 rounded-[var(--radius-sm)] px-2 py-1 text-[12px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] sm:flex"
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" />
            {copyAdmin.backHome}
          </Link>
          <UserMenu />
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col min-[900px]:flex-row">
        <nav
          aria-label={copyAdmin.nav.label}
          className="shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] min-[900px]:w-[200px] min-[900px]:border-b-0 min-[900px]:border-r"
        >
          <ul className="flex gap-0.5 overflow-x-auto px-2 min-[900px]:flex-col min-[900px]:overflow-visible min-[900px]:p-3">
            {items.map((item) => {
              const active = isActive(pathname, item);
              return (
                <li key={item.href} className="shrink-0">
                  <Link
                    href={item.href}
                    data-admin-nav="true"
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'relative flex items-center gap-2 whitespace-nowrap border-b-2 border-transparent px-3 py-2.5 text-[12.5px] font-medium',
                      'text-[var(--text-tertiary)] transition-colors duration-[var(--dur-fast)]',
                      'hover:text-[var(--text-secondary)]',
                      'min-[900px]:rounded-[var(--radius-sm)] min-[900px]:border-b-0 min-[900px]:py-1.5 min-[900px]:hover:bg-[var(--bg-hover)]',
                    )}
                  >
                    {item.icon}
                    <span>{item.label}</span>
                    {item.badge ? (
                      <span
                        title={fill(copyAdmin.nav.pendingBadge, { n: item.badge })}
                        className="num ml-auto inline-flex min-w-[18px] items-center justify-center rounded-full bg-[var(--danger)] px-1.5 text-[10.5px] font-semibold leading-[18px] text-white"
                      >
                        <span aria-hidden="true">{item.badge > 99 ? '99+' : item.badge}</span>
                        <span className="sr-only">{fill(copyAdmin.nav.pendingBadge, { n: item.badge })}</span>
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <main id="admin-main" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto outline-none">
          <div className="mx-auto w-full max-w-[1120px] px-4 py-6 min-[900px]:px-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
