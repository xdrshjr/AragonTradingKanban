import { api } from './api';
import { validateChartResponse } from './chartDataset';
import type { ChartView } from './types';

/** The rail and stable chart owner share this validated cache entry. */
export function chartBarsQuery(instrumentId: string, view: ChartView) {
  const { timeframe, range, adjust } = view;
  return {
    queryKey: [
      'bars', instrumentId, timeframe, range, adjust, 'chart-v1',
    ] as const,
    queryFn: async () => validateChartResponse(
      await api.bars(instrumentId, timeframe, range, adjust, {
        profile: 'chart',
      }),
      { instrumentId, ...view },
    ),
    enabled: Boolean(instrumentId),
    staleTime: 60_000,
    retry: 1,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  };
}
