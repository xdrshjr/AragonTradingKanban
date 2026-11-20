import type { Metadata } from 'next';
import Link from 'next/link';

import { ReportLibraryClient } from '@/components/library/ReportLibraryClient';
import { UserMenu } from '@/components/auth/UserMenu';
import { copy } from '@/lib/copy';
import { serverApi } from '@/lib/serverApi';
import { DEFAULT_LIBRARY_SCOPE, isScopeTab, type ScopeTab } from '@/lib/library';
import type { Capabilities, LibraryResponse } from '@/lib/types';

/**
 * `/reports` 的 RSC 壳（spec §23.3.8 / U1）。
 *
 * 只做三件事：metadata、**一次** `api.reportLibrary()` 拿首屏、把结果交给客户端列表。
 * 默认 `scope=all`（★P1-6）：库页要回答"我做过哪些报告、有哪些版本"，答案必须包含
 * superseded 版本；`latest` 回答的是另一个问题（每个标的默认打开哪一份），它服务首页。
 *
 * `features.reportLibrary=false` 时**不渲染入口**（导航里也不出现），但直接访问本页仍然
 * 给出可读的空态而不是死链——"不留死链"不等于"打不开时给 404"。
 */

export const metadata: Metadata = {
  title: `${copy.library.title} · ${copy.brand} ${copy.brandSuffix}`,
  description: copy.library.subtitle,
};

export const dynamic = 'force-dynamic';

interface PageProps {
  searchParams: Promise<{ scope?: string; q?: string }>;
}

export default async function ReportsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const api = await serverApi();
  const initialScope: ScopeTab = isScopeTab(params.scope) ? params.scope : DEFAULT_LIBRARY_SCOPE;
  const initialQuery = params.q ?? '';

  // 两个请求并发，其中任何一个失败都不该把整页变成错误页：
  // 报告库是**读**路径，读不到就是空态（"起不来的服务比能看不能用的服务更糟"）。
  const [library, capabilities] = await Promise.all([
    api
      .reportLibrary({ scope: initialScope, q: initialQuery, limit: 20, withCounts: true })
      .catch<LibraryResponse>(() => ({ items: [], total: 0, counts: {}, active: [], nextCursor: null })),
    api.capabilities().catch<Capabilities | null>(() => null),
  ]);

  const enabled = capabilities?.features?.reportLibrary ?? true;

  return (
    <main className="mx-auto flex h-dvh w-full max-w-[960px] flex-col overflow-hidden px-4 py-5">
      <header className="mb-3 flex items-baseline justify-between gap-3">
        <div>
          <h1 className="text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
            {copy.library.title}
          </h1>
          <p className="mt-0.5 text-[11px] text-[var(--text-tertiary)]">{copy.library.subtitle}</p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="text-[11.5px] text-[var(--text-secondary)] underline-offset-2 hover:text-[var(--text-primary)] hover:underline"
          >
            {copy.library.backHome}
          </Link>
          <UserMenu />
        </div>
      </header>

      <ReportLibraryClient
        initialData={library}
        // ★P2-10：`initialDataUpdatedAt` 取**服务端这次 fetch 的时间**，否则 TanStack
        // 把它当成 0（无限陈旧）⇒ 挂载瞬间立刻再请求一次。
        initialDataUpdatedAt={Date.now()}
        initialScope={initialScope}
        initialQuery={initialQuery}
        enabled={enabled}
      />
    </main>
  );
}
