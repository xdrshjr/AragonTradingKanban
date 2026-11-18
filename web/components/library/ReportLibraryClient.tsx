'use client';

/**
 * `/reports` 的客户端列表（spec §23.3.8 / U1 + U7）。
 *
 * 五个行为，每条都对着一个"看起来能用但其实是坑"的实现：
 *
 * 1. **scope / q 走 URL**（`?scope=&q=`）：可收藏、可分享；`router.replace` 不新增历史条目。
 * 2. **键盘注册在页面级**（不是 `useHotkeys` 的全局表）：离开库页之后 `P` 不该还是"星标"。
 *    命中 `isEditableTarget()` 时**全部让路**——`Delete` 尤其不能吃掉输入框里的退格。
 * 3. **`aria-live` 播报"已加载 N 份"**：列表刷新对读屏用户不是"什么都没发生"。
 * 4. **三个状态的文案各不相同**：没有报告 / 搜不到 / 回收站空——一句"暂无数据"是敷衍。
 * 5. **减少动画偏好**：`motion-reduce:` 关掉 pulse / 平移动画（既有 `theme.ts` 的偏好）。
 */

import { useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { triggerDownload, exportFilenameHint } from '@/lib/download';
import { isEditableTarget } from '@/lib/keyboard';
import { DEFAULT_LIBRARY_SCOPE, groupByDay, isScopeTab, libraryHref, type ScopeTab } from '@/lib/library';
import type { LibraryResponse, ReportLibraryItem } from '@/lib/types';

import { Button } from '../ui/Button';
import { Skeleton } from '../ui/Skeleton';
import { notify } from '../ui/Toast';
import { ActiveRunsStrip } from './ActiveRunsStrip';
import { LibraryFilters } from './LibraryFilters';
import { ReportLibraryRow } from './ReportLibraryRow';
import { flattenPages, libraryQueryKey, useReportLibrary } from './useReportLibrary';

export interface ReportLibraryClientProps {
  initialData: LibraryResponse;
  initialDataUpdatedAt: number;
  initialScope: ScopeTab;
  initialQuery?: string;
  /** `features.reportLibrary` — false 时不该有这一页（导航入口也不渲染）。 */
  enabled: boolean;
  trashRetentionDays?: number;
}

export function ReportLibraryClient({
  initialData,
  initialDataUpdatedAt,
  initialScope,
  initialQuery = '',
  enabled,
  trashRetentionDays = 30,
}: ReportLibraryClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const scopeParam = searchParams.get('scope');
  const scope: ScopeTab = isScopeTab(scopeParam) ? scopeParam : initialScope ?? DEFAULT_LIBRARY_SCOPE;
  const q = searchParams.get('q') ?? initialQuery;

  const [focusIndex, setFocusIndex] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const library = useReportLibrary({
    scope,
    q,
    limit: 20,
    withCounts: true,
    initialData: scope === initialScope && q === initialQuery ? initialData : undefined,
    initialDataUpdatedAt,
    enabled,
  });

  const items = useMemo(() => flattenPages(library.data), [library.data]);
  const groups = useMemo(() => groupByDay(items), [items]);
  const counts = library.data?.pages[0]?.counts ?? initialData.counts;
  const total = library.data?.pages[0]?.total ?? initialData.total;
  const active = library.data?.pages[0]?.active ?? initialData.active ?? [];

  /** 只改自己那两个参数，其余原样保留（与 `WorkspaceClient.syncUrl` 同一条纪律）。 */
  const writeUrl = useCallback(
    (next: { scope?: ScopeTab; q?: string }) => {
      const params = new URLSearchParams(window.location.search);
      const nextScope = next.scope ?? scope;
      const nextQ = next.q ?? q;
      if (nextScope === DEFAULT_LIBRARY_SCOPE) params.delete('scope');
      else params.set('scope', nextScope);
      if (!nextQ) params.delete('q');
      else params.set('q', nextQ);
      const query = params.toString();
      router.replace(query ? `/reports?${query}` : '/reports', { scroll: false });
    },
    [q, router, scope],
  );

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] });
  }, [queryClient]);

  const toggleStar = useCallback(
    async (item: ReportLibraryItem) => {
      try {
        await api.patchReport(item.report.id, { pinned: !item.report.pinned });
        invalidate();
      } catch {
        notify.error(copy.errors.generic);
      }
    },
    [invalidate],
  );

  const archive = useCallback(
    async (item: ReportLibraryItem) => {
      try {
        await api.patchReport(item.report.id, { archived: true });
        notify.success(copy.library.actions.archive);
        invalidate();
      } catch {
        notify.error(copy.errors.generic);
      }
    },
    [invalidate],
  );

  const restore = useCallback(
    async (item: ReportLibraryItem) => {
      try {
        await api.restoreReport(item.report.id);
        notify.success(copy.library.actions.restore);
        invalidate();
      } catch (error) {
        if (error instanceof ApiError && error.code === 'report_not_deleted') {
          notify.info(copy.errors.reportNotDeleted);
          invalidate();
          return;
        }
        notify.error(copy.errors.generic);
      }
    },
    [invalidate],
  );

  const exportHtml = useCallback((item: ReportLibraryItem) => {
    triggerDownload(
      api.exportUrl(item.report.id, 'html'),
      exportFilenameHint(item.report.title, item.report.version, 'html'),
    );
    notify.success(copy.report.exportHtml);
  }, []);

  const open = useCallback((item: ReportLibraryItem) => {
    window.location.href = libraryHref(item.instrument.symbol, item.report.id);
  }, []);

  // 页面级键盘（★P2-5）：键位与既有 `matchHotkey` 不冲突，且输入框聚焦时全部让路。
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === '/' && !isEditableTarget(event.target)) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (event.key === 'Escape' && isEditableTarget(event.target)) {
        // `Escape` 在输入框里 = 清空搜索（不是"关闭浮层"）。
        (event.target as HTMLInputElement).blur();
        writeUrl({ q: '' });
        return;
      }
      if (isEditableTarget(event.target)) return;
      if (items.length === 0) return;

      const current = items[Math.min(focusIndex, items.length - 1)];
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          setFocusIndex((index) => Math.min(index + 1, items.length - 1));
          break;
        case 'ArrowUp':
          event.preventDefault();
          setFocusIndex((index) => Math.max(index - 1, 0));
          break;
        case 'Enter':
          if (current) {
            event.preventDefault();
            open(current);
          }
          break;
        case 'p':
        case 'P':
          if (current) {
            event.preventDefault();
            void toggleStar(current);
          }
          break;
        case 'e':
        case 'E':
          if (current) {
            event.preventDefault();
            exportHtml(current);
          }
          break;
        case 'Delete':
          // **归档**，不是删除：删除必须走二次确认（既有 `report.deleteVersion` 的弹窗）。
          if (current && scope !== 'trash') {
            event.preventDefault();
            void archive(current);
          }
          break;
        default:
          break;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [archive, exportHtml, focusIndex, items, open, scope, toggleStar, writeUrl]);

  // 键盘移动焦点时把它滚进视野（`listbox` 的焦点是虚拟的，所以手动 scrollIntoView）。
  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>(`[data-index="${focusIndex}"]`);
    node?.scrollIntoView({ block: 'nearest' });
  }, [focusIndex]);

  if (!enabled) {
    return (
      <p className="rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] p-4 text-[12px] text-[var(--text-secondary)]">
        {copy.library.needsNewerBackend}
      </p>
    );
  }

  const error = library.error instanceof ApiError ? library.error : null;
  const emptyCopy =
    scope === 'trash'
      ? { title: copy.library.emptyTrash, body: copy.library.emptyTrashBody }
      : q
        ? { title: copy.library.emptySearch, body: copy.library.emptySearchBody }
        : { title: copy.library.empty, body: copy.library.emptyBody };

  return (
    <div className="flex min-h-0 flex-col">
      <ActiveRunsStrip active={active} />

      <LibraryFilters
        scope={scope}
        counts={counts}
        q={q}
        total={total}
        searchRef={searchRef}
        onScopeChange={(next) => {
          setFocusIndex(0);
          writeUrl({ scope: next });
        }}
        onQueryChange={(next) => {
          setFocusIndex(0);
          writeUrl({ q: next });
        }}
      />

      <p aria-live="polite" className="sr-only">
        {copy.library.loaded.replace('{count}', String(items.length))}
      </p>

      {library.isPending ? (
        <div className="mt-2 space-y-1">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-[52px] w-full motion-reduce:animate-none" />
          ))}
        </div>
      ) : error ? (
        <div className="mt-6 rounded-[var(--radius-md)] border border-[var(--danger)]/40 bg-[var(--danger-weak)] p-4">
          <p className="text-[12.5px] font-medium text-[var(--danger)]">{error.message}</p>
          <p className="mt-1 text-[10.5px] text-[var(--text-tertiary)]">
            {copy.errors.requestId}：{error.requestId}
          </p>
          <Button size="sm" className="mt-2" onClick={() => void library.refetch()}>
            {copy.library.retry}
          </Button>
        </div>
      ) : items.length === 0 ? (
        <div className="mt-10 text-center">
          <p className="text-[13px] text-[var(--text-secondary)]">{emptyCopy.title}</p>
          <p className="mt-1 text-[11.5px] text-[var(--text-tertiary)]">{emptyCopy.body}</p>
        </div>
      ) : (
        <div
          ref={listRef}
          role="listbox"
          aria-label={copy.library.title}
          className="mt-1 min-h-0 flex-1 overflow-y-auto"
        >
          {groups.map((group) => (
            <section key={group.key} aria-label={group.label}>
              <h2 className="sticky top-0 z-[1] bg-[var(--bg-base)]/95 px-1 py-1 text-[10.5px] font-medium uppercase tracking-wider text-[var(--text-tertiary)] backdrop-blur">
                {group.label}
              </h2>
              {group.items.map((item) => {
                const index = items.indexOf(item);
                return (
                  <div key={item.report.id} data-index={index}>
                    <ReportLibraryRow
                      item={item}
                      selected={index === focusIndex}
                      trash={scope === 'trash'}
                      trashRetentionDays={trashRetentionDays}
                      onOpen={open}
                      onToggleStar={toggleStar}
                      onRestore={restore}
                    />
                  </div>
                );
              })}
            </section>
          ))}

          <div className={cn('flex items-center justify-center gap-3 py-3')}>
            {library.hasNextPage ? (
              <Button
                size="sm"
                loading={library.isFetchingNextPage}
                onClick={() => void library.fetchNextPage()}
              >
                {copy.library.loadMore}
              </Button>
            ) : (
              <span className="text-[10.5px] text-[var(--text-tertiary)]">{copy.library.allLoaded}</span>
            )}
            <span className="text-[10.5px] text-[var(--text-tertiary)]">
              {copy.library.count.replace('{total}', String(total))}
            </span>
          </div>
        </div>
      )}

      {scope === 'trash' ? (
        <p className="mt-2 rounded-[var(--radius-sm)] border border-[var(--warn)]/30 bg-[var(--warn-weak)] px-2 py-1.5 text-[10.5px] leading-relaxed text-[var(--warn)]">
          {copy.library.trashNotice}。{copy.library.trashNoticePurge}。
        </p>
      ) : (
        <p className="mt-2 text-[10.5px] text-[var(--text-tertiary)]">{copy.library.keys}</p>
      )}
    </div>
  );
}

/**
 * 供 RSC 壳与命令面板共用的查询键（避免第三处硬编码 `['reports','library',…]`）。
 */
export const REPORT_LIBRARY_KEY = libraryQueryKey({ scope: 'all' });
