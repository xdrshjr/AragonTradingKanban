import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowRight, FileText, LineChart, Layers } from 'lucide-react';

import { RecentReports } from '@/components/library/RecentReports';
import { LandingSearch } from '@/components/shell/LandingSearch';
import { api } from '@/lib/api';
import { copy } from '@/lib/copy';
import type { LibraryResponse, WatchlistEntry } from '@/lib/types';

export const metadata: Metadata = {
  title: 'Aragon Research Terminal',
};

export const dynamic = 'force-dynamic';

/**
 * 首页 = **两个区块 + 一个入口**（spec §23.3.7 / U1 + U6）。
 *
 * 它过去是 `1 × /watchlist + 1 × /instruments + 最多 12 × /reports/latest`（★E-126：
 * 最坏 14 次串行 RSC fetch），而且列的是**自选标的**——把茅台移出自选，它的报告就从
 * 首页彻底消失（U-0a / E-120）。现在：
 *
 * * **主区块**「最近生成的研报」—— 一次 `GET /reports?scope=latest&limit=6&includeActive=1&withCounts=1`；
 * * **次区块**「最近标的」—— 一次 `GET /watchlist`（它自带 instrument，**不再需要**
 *   `/instruments`，所以是 2 次 fetch 而不是 3 次）；降级为单列、最多 6 行、不再显示版本徽标
 *   —— 自选是"我关注的"，报告是"我做过的"，两者不能互相代表。
 *
 * 两个请求都**失败也不炸**：首页是读路径，读不到就是空态。
 */
async function loadHome(): Promise<{ reports: LibraryResponse; watchlist: WatchlistEntry[] }> {
  const [reports, watchlist] = await Promise.all([
    api
      .reportLibrary({ scope: 'latest', limit: 6, includeActive: true, withCounts: true })
      .catch<LibraryResponse>(() => ({ items: [], total: 0, counts: {}, active: [], nextCursor: null })),
    api
      .watchlist()
      .then((response) => response.items)
      .catch<WatchlistEntry[]>(() => []),
  ]);
  return { reports, watchlist: watchlist.slice(0, 6) };
}

const FEATURES = [
  {
    icon: <Layers className="size-4" />,
    title: '结构化报告，不是一段文本',
    body: '12 种内容块（图表 / 指标卡 / 同业 / 情景 / 时间轴），生成一次即成为可反复渲染的文档。',
  },
  {
    icon: <LineChart className="size-4" />,
    title: '与 K 线双向联动',
    body: '点报告里的日期，K 线定位到那一段；每条结论都带来源编号，点开即可核对。',
  },
  {
    icon: <FileText className="size-4" />,
    title: '生成一次，反复查看',
    body: '报告库按时间列出每一份报告；点进去看到的就是列表上那一版，刷新与分享都不变。',
  },
];

export default async function LandingPage() {
  const { reports, watchlist } = await loadHome();

  return (
    <main className="flex min-h-dvh flex-col items-center overflow-y-auto px-5 py-10">
      <div className="w-full max-w-[760px]">
        <header className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent)] text-[13px] font-bold text-[var(--accent-text)]">
              A
            </span>
            <span className="flex items-baseline gap-1.5">
              <span className="text-[14px] font-semibold tracking-tight">{copy.brand}</span>
              <span className="text-[11.5px] text-[var(--text-tertiary)]">{copy.brandSuffix}</span>
            </span>
          </div>
          <Link
            href="/reports"
            className="text-[11.5px] text-[var(--text-secondary)] underline-offset-2 hover:text-[var(--text-primary)] hover:underline"
          >
            {copy.library.open}
          </Link>
        </header>

        <section className="mt-16 text-center">
          <h1 className="text-[26px] font-semibold leading-tight tracking-tight text-[var(--text-primary)]">
            {copy.landing.title}
          </h1>
          <p className="mx-auto mt-2 max-w-[520px] text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {copy.tagline}。{copy.landing.subtitle}
          </p>

          <div className="mx-auto mt-6 max-w-[560px]">
            <LandingSearch />
            <p className="mt-2 text-[11px] text-[var(--text-tertiary)]">{copy.landing.hint}</p>
          </div>
        </section>

        <RecentReports initialData={reports} initialDataUpdatedAt={Date.now()} />

        {watchlist.length > 0 ? (
          <section className="mt-10" aria-labelledby="recent-symbols-heading">
            <div className="mb-2 flex items-baseline justify-between">
              <h2
                id="recent-symbols-heading"
                className="text-[11px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]"
              >
                {copy.landing.recent}
              </h2>
              <span className="text-[10px] text-[var(--text-tertiary)]">{copy.landing.watchlistIsNotReports}</span>
            </div>
            <ul className="space-y-1">
              {watchlist.map((entry) => (
                <li key={entry.instrument.id}>
                  <Link
                    href={`/${encodeURIComponent(entry.instrument.symbol)}`}
                    className="group flex items-center justify-between gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 transition-colors hover:bg-[var(--bg-surface-2)]"
                  >
                    <span className="flex min-w-0 items-baseline gap-1.5">
                      <span className="truncate text-[12px] text-[var(--text-primary)]">
                        {entry.instrument.name}
                      </span>
                      <span className="mono shrink-0 text-[10px] text-[var(--text-tertiary)]">
                        {entry.instrument.symbol}
                      </span>
                    </span>
                    <ArrowRight
                      aria-hidden="true"
                      className="size-3 shrink-0 text-[var(--text-tertiary)] transition-transform group-hover:translate-x-0.5"
                    />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <p className="mt-10 text-center text-[11.5px] text-[var(--text-tertiary)]">{copy.landing.empty}</p>
        )}

        <section className="mt-12 grid gap-2 sm:grid-cols-3">
          {FEATURES.map((feature) => (
            <article
              key={feature.title}
              className="rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] p-3"
            >
              <span className="mb-2 flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent-weak)] text-[var(--accent)]">
                {feature.icon}
              </span>
              <h3 className="text-[12.5px] font-semibold text-[var(--text-primary)]">{feature.title}</h3>
              <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--text-tertiary)]">{feature.body}</p>
            </article>
          ))}
        </section>

        <footer className="mt-12 border-t border-[var(--border-subtle)] pt-4 text-[10.5px] leading-relaxed text-[var(--text-tertiary)]">
          本产品由 AI 生成研究报告，仅供研究参考，不构成投资建议。
        </footer>
      </div>
    </main>
  );
}
