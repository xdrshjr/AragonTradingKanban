'use client';

/**
 * ★v14（U1 / §23.6.3）：`useInfiniteQuery` + 游标拼接 + `getNextPageParam` 的**唯一**实现。
 *
 * 三个消费者（库页、首页、命令面板）共用它，所以"下一页从哪来"只有一处：
 * `nextCursor` 是服务端算好的 `"{generated_at}|{id}"`（已 `encodeURIComponent`），
 * 前端只负责原样回传。**不**在前端拼游标、**不**做时间解析（★P1-3）。
 *
 * `initialData` 必须配 `initialDataUpdatedAt`（★P2-10）：否则 TanStack 把它当成 `0`
 * （= 无限陈旧）⇒ 挂载瞬间立刻再请求一次，AC-137 的"只发一次 `/reports`"当场就不成立。
 */

import { useInfiniteQuery, type UseInfiniteQueryResult } from '@tanstack/react-query';

import { api } from '@/lib/api';
import type { LibraryResponse, LibraryScope } from '@/lib/types';

export const LIBRARY_QUERY_KEY = 'library';

export interface UseReportLibraryOptions {
  scope: LibraryScope;
  q?: string;
  instrumentId?: string;
  limit?: number;
  withCounts?: boolean;
  includeActive?: boolean;
  /** RSC 侧那一页（首屏零往返）。`initialDataUpdatedAt` 与它一起给。 */
  initialData?: LibraryResponse;
  initialDataUpdatedAt?: number;
  enabled?: boolean;
}

export interface LibraryPage {
  items: LibraryResponse['items'];
  nextCursor: string | null;
  total: number;
  counts: LibraryResponse['counts'];
  active: LibraryResponse['active'];
}

/**
 * 查询键的第二段把**所有**影响结果的参数写进去：换 scope 必须是一次新查询，
 * 而不是"复用上一页的 items 再筛一遍"。
 */
export function libraryQueryKey(options: Pick<UseReportLibraryOptions, 'scope' | 'q' | 'instrumentId' | 'withCounts' | 'includeActive'>) {
  return [
    'reports',
    LIBRARY_QUERY_KEY,
    options.scope,
    options.q ?? '',
    options.instrumentId ?? '',
    options.withCounts === false ? 0 : 1,
    options.includeActive ? 1 : 0,
  ] as const;
}

export function useReportLibrary(
  options: UseReportLibraryOptions,
): UseInfiniteQueryResult<{ pages: LibraryPage[]; pageParams: (string | null)[] }, Error> {
  const {
    scope,
    q = '',
    instrumentId,
    limit = 20,
    withCounts = true,
    includeActive = false,
    initialData,
    initialDataUpdatedAt,
    enabled = true,
  } = options;

  return useInfiniteQuery({
    queryKey: libraryQueryKey({ scope, q, instrumentId, withCounts, includeActive }),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const response = await api.reportLibrary({
        scope,
        q,
        instrumentId,
        limit,
        before: pageParam,
        withCounts,
        includeActive,
      });
      return {
        items: response.items,
        nextCursor: response.nextCursor ?? null,
        total: response.total,
        counts: response.counts,
        active: response.active ?? [],
      } satisfies LibraryPage;
    },
    // `nextCursor` 为 null 就是到底了（服务端只在有下一页时给值）。
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    // ★P2-10：`initialData` 与 `initialDataUpdatedAt` 必须成对出现，`staleTime` 必须 > 0。
    ...(initialData
      ? {
          initialData: {
            pages: [
              {
                items: initialData.items,
                nextCursor: initialData.nextCursor ?? null,
                total: initialData.total,
                counts: initialData.counts,
                active: initialData.active ?? [],
              } satisfies LibraryPage,
            ],
            pageParams: [null],
          },
          initialDataUpdatedAt: initialDataUpdatedAt ?? Date.now(),
        }
      : {}),
    staleTime: 30_000,
    enabled,
  });
}

/** 把分页结果摊平（渲染要的是一维列表，分组再在 `lib/library.ts::groupByDay` 里做）。 */
export function flattenPages(data: { pages: LibraryPage[] } | undefined): LibraryPage['items'] {
  if (!data) return [];
  return data.pages.flatMap((page) => page.items);
}
