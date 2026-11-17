/**
 * Fix: the left rail showed "—" for every price.
 *
 * §6.3 asks each rail row for 现价 + 涨跌, but rows were built only from
 * instrument metadata — no quote was ever fetched for them. Quotes are already
 * served by `GET /instruments/{id}/quote` and cached server-side, so the rail can
 * fetch its own small batch instead of being fed by the page.
 *
 * ★PCD-3 / §R-4: the *selected* row must not pair a `qfq` quote with an `hfq`
 * chart — measured +514% between the rail (1,439.78) and the header (8,840.24).
 * Solution C in the analysis (every rail row reads `/bars`) was rejected because it
 * costs 12 extra requests and moves the contradiction from "左栏 vs 图表" to
 * "左栏 vs 报告". So only the selected row switches source, and it asks for the
 * chart's **own** query (`['bars', id, timeframe, range, adjust]`) — TanStack Query
 * dedupes it against `PricePanel`, which means **zero** extra requests while the
 * chart is mounted. The other rows keep the 60 s `/quote` and carry a source badge.
 */

import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { api } from '@/lib/api';
import type { RailItem } from '@/components/shell/SymbolRail';
import type { Bar, BarsResponse, ChartView } from '@/lib/types';

/** Upper bound on parallel quote requests when the rail is long. */
const MAX_QUOTE_TARGETS = 12;

/** §6.1 #11: same clock as `PricePanel`, so the two panels cannot drift apart. */
const STALE_TIME = 60_000;

const DEFAULT_CHART: ChartView = { timeframe: '1d', range: '1y', adjust: 'qfq' };

export interface RailQuotesOptions {
  /** The instrument whose chart is open — its rail row follows the chart's series. */
  selectedId?: string | null;
  /** The chart's current window. Defaults to 日线 / 1年 / 前复权. */
  chart?: ChartView;
}

export function useRailQuotes(items: RailItem[], options: RailQuotesOptions = {}): RailItem[] {
  const { selectedId = null, chart = DEFAULT_CHART } = options;
  const ids = useMemo(
    () => items.slice(0, MAX_QUOTE_TARGETS).map((item) => item.instrument.id),
    [items],
  );

  const quotes = useQuery({
    queryKey: ['rail-quotes', ids.join(',')],
    enabled: ids.length > 0,
    staleTime: STALE_TIME,
    retry: 1,
    queryFn: async () => {
      const entries = await Promise.all(
        ids.map(async (id) => {
          try {
            const quote = await api.quote(id);
            return [id, quote] as const;
          } catch {
            // A single missing quote must not blank the whole rail.
            return [id, null] as const;
          }
        }),
      );
      return Object.fromEntries(entries);
    },
  });

  // The chart's window, asked for under the *same* key `PricePanel` uses.
  const seriesQuery = useQuery<BarsResponse>({
    queryKey: ['bars', selectedId, chart.timeframe, chart.range, chart.adjust],
    queryFn: () => api.bars(selectedId ?? '', chart.timeframe, chart.range, chart.adjust),
    enabled: Boolean(selectedId),
    staleTime: STALE_TIME,
    retry: 1,
  });

  return useMemo(() => {
    const data = quotes.data;
    const series = seriesQuery.data;
    const bars: Bar[] = series?.bars ?? [];
    const last = bars[bars.length - 1];
    const previous = bars[bars.length - 2];
    // Same pair rule as the header (BUG-2): price and change from one series, and no
    // invented change when there is no previous close to divide by.
    const seriesChange = last && previous ? (last.c / previous.c - 1) * 100 : null;

    return items.map((item) => {
      if (selectedId && item.instrument.id === selectedId) {
        if (last) {
          return {
            ...item,
            last: last.c,
            changePct: seriesChange,
            source: series?.meta.source ?? item.source ?? null,
            adjust: chart.adjust,
          };
        }
        // No bars to follow (chart empty / not loaded yet): keep the quote, and label
        // which 口径 it carries instead of silently pretending it matches the chart.
        // `null`, not `'qfq'`: a provider's realtime quote carries **no** adjustment
        // at all (akshare/yfinance only have the unadjusted tape), so stamping it as
        // 前复权 would be a claim the quote itself cannot back.
        const quote = data?.[item.instrument.id];
        return {
          ...item,
          last: quote?.last ?? item.last,
          changePct: quote?.changePct ?? item.changePct,
          source: quote?.source ?? item.source ?? null,
          adjust: null,
        };
      }
      const quote = data?.[item.instrument.id];
      if (!quote) return item;
      return {
        ...item,
        last: quote.last ?? item.last,
        changePct: quote.changePct ?? item.changePct,
        source: quote.source ?? item.source ?? null,
      };
    });
  }, [chart.adjust, items, quotes.data, selectedId, seriesQuery.data]);
}
