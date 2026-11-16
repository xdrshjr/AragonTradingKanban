import { Skeleton } from '@/components/ui/Skeleton';

/** Three-column skeleton with the exact proportions, so hydration does not shift (§6.3). */
export default function WorkspaceLoading() {
  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <div className="flex h-[var(--header-h)] shrink-0 items-center gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3">
        <Skeleton className="size-6 rounded-[var(--radius-sm)]" />
        <Skeleton className="h-3 w-24" />
        <div className="ml-auto flex gap-2">
          <Skeleton className="h-6 w-16 rounded-full" />
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        <aside style={{ width: 'var(--rail-w)' }} className="shrink-0 border-r border-[var(--border-subtle)] p-2">
          <Skeleton className="mb-2 h-7 w-full" />
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="mb-1 h-9 w-full" />
          ))}
        </aside>
        <section style={{ width: 'var(--kline-w)' }} className="shrink-0 border-r border-[var(--border-subtle)] p-3">
          <Skeleton className="mb-2 h-4 w-32" />
          <Skeleton className="h-[calc(100%-2rem)] w-full" />
        </section>
        <section className="min-w-0 flex-1 p-4">
          <Skeleton className="mb-2 h-5 w-3/4" />
          <Skeleton className="mb-4 h-3 w-1/2" />
          <div className="space-y-2">
            {Array.from({ length: 10 }).map((_, index) => (
              <Skeleton key={index} className={index % 4 === 3 ? 'h-3 w-2/3' : 'h-3 w-full'} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
