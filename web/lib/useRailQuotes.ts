/**
 * Fix: the left rail showed "—" for every price.
 *
 * §6.3 asks each rail row for 现价 + 涨跌, but rows were built only from
 * instrument metadata — no quote was ever fetched for them. Quotes are already
 * served by `GET /instruments/{id}/quote` and cached server-side, so the rail can
 * fetch its own small batch instead of being fed by the page.
 */

import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { api } from '@/lib/api';
import type { RailItem } from '@/components/shell/SymbolRail';

/** Upper bound on parallel quote requests when the rail is long. */
const MAX_QUOTE_TARGETS = 12;

export function useRailQuotes(items: RailItem[]): RailItem[] {
  const ids = useMemo(
    () => items.slice(0, MAX_QUOTE_TARGETS).map((item) => item.instrument.id),
    [items],
  );

  const quotes = useQuery({
    queryKey: ['rail-quotes', ids.join(',')],
    enabled: ids.length > 0,
    staleTime: 30_000,
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

  return useMemo(() => {
    const data = quotes.data;
    if (!data) return items;
    return items.map((item) => {
      const quote = data[item.instrument.id];
      if (!quote) return item;
      return { ...item, last: quote.last ?? item.last, changePct: quote.changePct ?? item.changePct };
    });
  }, [items, quotes.data]);
}
