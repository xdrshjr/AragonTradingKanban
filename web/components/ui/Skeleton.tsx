import { cn } from '@/lib/cn';

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'rounded-[var(--radius-sm)] bg-gradient-to-r from-[var(--bg-surface-2)] via-[var(--bg-surface-3)] to-[var(--bg-surface-2)]',
        'animate-pulse',
        className,
      )}
    />
  );
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton
          key={index}
          className={cn('h-3', index === lines - 1 ? 'w-2/3' : index % 3 === 1 ? 'w-[92%]' : 'w-full')}
        />
      ))}
    </div>
  );
}

export function SkeletonBlock({ className }: { className?: string }) {
  return <Skeleton className={cn('h-24 w-full', className)} />;
}
