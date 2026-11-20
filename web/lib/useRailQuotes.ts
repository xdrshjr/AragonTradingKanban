import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { api } from '@/lib/api';
import { chartBarsQuery } from '@/lib/chartQuery';
import { getLatestChartChange } from '@/lib/chartDataset';
import type { RailItem } from '@/components/shell/SymbolRail';
import type { BarsResponse, ChartView, Quote } from '@/lib/types';

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

async function loadRailQuotes(ids: string[]) {
  const entries = await Promise.all(ids.map(async id => {
    try {
      return [id, await api.quote(id)] as const;
    } catch {
      // Missing one quote must not blank every other instrument.
      return [id, null] as const;
    }
  }));
  return Object.fromEntries(entries);
}

function selectedRow(
  item: RailItem,
  series: BarsResponse | undefined,
  quote: Quote | null | undefined,
  chart: ChartView,
): RailItem {
  const last = series?.bars.at(-1);
  if (last) {
    return {
      ...item,
      last: last.c,
      changePct: getLatestChartChange(series),
      source: series?.meta.source ?? null,
      adjust: chart.adjust,
    };
  }
  return {
    ...item,
    last: quote?.last ?? null,
    changePct: quote?.last == null ? null : quote.changePct ?? null,
    source: quote?.source ?? null,
    adjust: null,
  };
}

/** Share the chart's validated response only for the selected instrument. */
export function useRailQuotes(
  items: RailItem[],
  options: RailQuotesOptions = {},
): RailItem[] {
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
    queryFn: () => loadRailQuotes(ids),
  });

  const seriesQuery = useQuery(chartBarsQuery(selectedId ?? '', chart));

  return useMemo(() => {
    const data = quotes.data;
    return items.map((item) => {
      if (selectedId && item.instrument.id === selectedId) {
        return selectedRow(item, seriesQuery.data,
          data?.[item.instrument.id], chart);
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
  }, [chart, items, quotes.data, selectedId, seriesQuery.data]);
}
