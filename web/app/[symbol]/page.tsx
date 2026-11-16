import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { ReportMeta } from '@/components/shell/ReportMeta';

import { WorkspaceClient } from '@/components/shell/WorkspaceClient';
import { api, ApiError } from '@/lib/api';
import type { Capabilities, Instrument, ReportDetail, ReportListResponse, WatchlistEntry } from '@/lib/types';

export const dynamic = 'force-dynamic';

async function loadWorkspace(symbol: string) {
  const instrumentId = decodeURIComponent(symbol);

  let instrument: Instrument | null = null;
  try {
    const response = await api.instrument(instrumentId);
    instrument = response.items[0] ?? null;
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) return null;
    // A cold API must not turn into a 500 page: fall back to the client refetch.
    instrument = null;
  }
  if (!instrument) return null;

  const [reportsResult, capabilitiesResult, watchlistResult, latestResult] = await Promise.all([
    api.reports(instrument.id).catch(() => ({ items: [], total: 0 }) as ReportListResponse),
    api.capabilities().catch(
      () =>
        ({
          aragon: {
            available: false,
            versionOk: false,
            hasApiKey: false,
            ajv: false,
            canStructured: false,
            hasSubmitResult: false,
            extraAllowTools: [],
            errors: [],
            degradedReason: 'aragon_unavailable',
          },
          agentMode: 'mock',
          dataProviders: [],
          features: {},
          limits: {},
        }) as unknown as Capabilities,
    ),
    api.watchlist().catch(() => ({ items: [] }) as { items: WatchlistEntry[] }),
    api.latestReport(instrument.id).catch(() => null as ReportDetail | null),
  ]);

  return {
    instrument,
    reports: reportsResult,
    capabilities: capabilitiesResult,
    watchlist: watchlistResult.items,
    detail: latestResult,
  };
}

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }): Promise<Metadata> {
  const { symbol } = await params;
  try {
    const response = await api.instrument(decodeURIComponent(symbol));
    const instrument = response.items[0];
    if (instrument) return { title: `${instrument.name} 投研报告` };
  } catch {
    /* fall through to the generic title */
  }
  return { title: '投研报告' };
}

/**
 * Deep-link parameters (§18.4.5, AC-39/AC-45).
 *
 * `?report=` opens a specific version, `?section=` / `?block=` land the reader on a
 * search hit or an ask citation. They are read here (server side) so the first paint
 * already shows the right version — no client round trip, no flash of the latest.
 */
type WorkspaceSearchParams = {
  report?: string | string[];
  section?: string | string[];
  block?: string | string[];
};

function firstValue(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value[0] ?? null;
  return value ?? null;
}

export default async function WorkspacePage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<WorkspaceSearchParams>;
}) {
  const { symbol } = await params;
  const workspace = await loadWorkspace(symbol);
  if (!workspace) notFound();

  const query = await searchParams;
  const reportId = firstValue(query.report);
  const section = firstValue(query.section);
  const block = firstValue(query.block);

  // A deep link may point at an older version; fetch it rather than trusting "latest".
  let detail = workspace.detail;
  if (reportId && reportId !== detail?.report.id) {
    detail = await api.report(reportId).catch(() => detail);
  }

  return (
    <>
      {/* A5: the only offline-time evidence. Without it no badge is shown. */}
      <ReportMeta />
      <WorkspaceClient
        initialInstrument={workspace.instrument}
        initialReports={workspace.reports.items}
        initialDetail={detail}
        initialWatchlist={workspace.watchlist}
        capabilities={workspace.capabilities}
        initialReportId={reportId}
        initialSection={section}
        initialBlockId={block}
      />
    </>
  );
}
