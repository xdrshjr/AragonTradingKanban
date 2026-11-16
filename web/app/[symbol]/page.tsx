import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

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

export default async function WorkspacePage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  const workspace = await loadWorkspace(symbol);
  if (!workspace) notFound();

  return (
    <WorkspaceClient
      initialInstrument={workspace.instrument}
      initialReports={workspace.reports.items}
      initialDetail={workspace.detail}
      initialWatchlist={workspace.watchlist}
      capabilities={workspace.capabilities}
    />
  );
}
