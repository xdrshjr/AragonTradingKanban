'use client';

import Link from 'next/link';
import { useEffect } from 'react';

import { copy } from '@/lib/copy';

/** Global error boundary: shows the request id so a failure is diagnosable (§7.3). */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error('[aragon] unhandled error', error);
  }, [error]);

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="max-w-[460px] text-center">
        <h1 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">{copy.errors.generic}</h1>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
          {error.message || '页面渲染时发生了未预期的错误。'}
        </p>
        {error.digest ? (
          <p className="mono mt-2 text-[10.5px] text-[var(--text-tertiary)]">
            {copy.errors.requestId}: {error.digest}
          </p>
        ) : null}
        <div className="mt-5 flex items-center justify-center gap-2">
          <button
            type="button"
            onClick={reset}
            className="inline-flex h-8 items-center rounded-[var(--radius-sm)] bg-[var(--accent)] px-3 text-[12.5px] font-medium text-[var(--accent-text)] transition-[filter] hover:brightness-110"
          >
            {copy.errors.retry}
          </button>
          <Link
            href="/"
            className="inline-flex h-8 items-center rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-3 text-[12.5px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
          >
            {copy.errors.backToSearch}
          </Link>
        </div>
      </div>
    </main>
  );
}
