import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowRight, FileText, LineChart, Layers } from 'lucide-react';

import { LandingSearch } from '@/components/shell/LandingSearch';
import { api } from '@/lib/api';
import { copy } from '@/lib/copy';
import type { Instrument, ReportSummary, WatchlistEntry } from '@/lib/types';

export const metadata: Metadata = {
  title: 'Aragon Research Terminal',
};

export const dynamic = 'force-dynamic';

async function loadRail(): Promise<{ instruments: Instrument[]; reports: Record<string, ReportSummary> }> {
  try {
    const watchlist = await api.watchlist();
    const entries: WatchlistEntry[] = watchlist.items;
    const ids = entries.map((entry) => entry.instrument.id);
    if (ids.length === 0) return { instruments: [], reports: {} };

    const list = await api.instruments(ids);
    const reports: Record<string, ReportSummary> = {};
    await Promise.all(
      list.items.slice(0, 12).map(async (instrument) => {
        try {
          const latest = await api.latestReport(instrument.id);
          if (latest) reports[instrument.id] = latest.report;
        } catch {
          /* a broken report must not break the landing page */
        }
      }),
    );
    return { instruments: list.items, reports };
  } catch {
    return { instruments: [], reports: {} };
  }
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
    title: '版本化与导出',
    body: '每次生成都是一个不可变版本，可切换比较、置顶归档，导出 HTML / Markdown。',
  },
];

export default async function LandingPage() {
  const { instruments, reports } = await loadRail();

  return (
    <main className="flex min-h-dvh flex-col items-center overflow-y-auto px-5 py-10">
      <div className="w-full max-w-[760px]">
        <header className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent)] text-[13px] font-bold text-[var(--accent-text)]">
            A
          </span>
          <span className="flex items-baseline gap-1.5">
            <span className="text-[14px] font-semibold tracking-tight">{copy.brand}</span>
            <span className="text-[11.5px] text-[var(--text-tertiary)]">{copy.brandSuffix}</span>
          </span>
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

        {instruments.length > 0 ? (
          <section className="mt-12">
            <h2 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
              {copy.landing.recent}
            </h2>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {instruments.map((instrument) => {
                const report = reports[instrument.id];
                return (
                  <Link
                    key={instrument.id}
                    href={`/${encodeURIComponent(instrument.id)}`}
                    className="group flex items-center justify-between gap-2 rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3 py-2.5 transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-surface-2)]"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[12.5px] font-medium text-[var(--text-primary)]">
                        {instrument.name}
                      </span>
                      <span className="mono block text-[10.5px] text-[var(--text-tertiary)]">{instrument.symbol}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-1.5">
                      {report ? (
                        <span className="rounded-full border border-[var(--accent)]/35 bg-[var(--accent-weak)] px-1.5 py-px text-[10px] text-[var(--accent)]">
                          v{report.version}
                        </span>
                      ) : (
                        <span className="text-[10px] text-[var(--text-tertiary)]">未生成</span>
                      )}
                      <ArrowRight className="size-3 text-[var(--text-tertiary)] transition-transform group-hover:translate-x-0.5" />
                    </span>
                  </Link>
                );
              })}
            </div>
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
