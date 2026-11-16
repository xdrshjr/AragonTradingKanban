import Link from 'next/link';

import { copy } from '@/lib/copy';

export default function WorkspaceNotFound() {
  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="max-w-[420px] text-center">
        <p className="mono text-[11px] uppercase tracking-widest text-[var(--text-tertiary)]">404</p>
        <h1 className="mt-2 text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">
          {copy.errors.notFoundTitle}
        </h1>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{copy.errors.notFoundBody}</p>
        <Link
          href="/"
          className="mt-5 inline-flex h-8 items-center rounded-[var(--radius-sm)] bg-[var(--accent)] px-3 text-[12.5px] font-medium text-[var(--accent-text)] transition-[filter] hover:brightness-110"
        >
          {copy.errors.backToSearch}
        </Link>
      </div>
    </main>
  );
}
