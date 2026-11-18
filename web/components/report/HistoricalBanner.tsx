'use client';

/**
 * 历史版本横幅（spec §23.3.3 / U2；AC-141 / AC-142）。
 *
 * 为什么是**常驻横幅**而不是 modal / toast：它必须一直在，否则读者会忘了自己在看历史。
 * 40px 高、三个动作，每个都对应一条真实路径：
 *
 * * **回到最新** → `selectVersion(latestReportId)`，于是 URL 的 `report` 参数被删掉
 *   （URL 只记录"与默认不同"的部分，与 `tf/win/adj` 的处理完全一致）。
 * * **基于此版本更新** → `requestGeneration({ baseReportId })`（U3：结构不变、结论按新材料改）。
 * * **版本比较** → 复用既有 `openCompare()`（它已经把"上一版 vs 当前版"算好了）。
 *
 * ★P1-7：`latestReportId` **必须**由调用方给出权威值（`initialReports` 里 `isLatest` →
 * 否则版本最大且非 provisional → 否则版本最大）。组件自己**不猜**——猜错的后果是把最新版
 * 渲染成历史版（或反之），而这两种错误在界面上长得一模一样。
 */

import { History } from 'lucide-react';

import { copy } from '@/lib/copy';
import { relativeTime } from '@/lib/format';

export interface HistoricalBannerProps {
  version: number;
  generatedAt: string;
  latestVersion: number;
  latestGeneratedAt?: string | null;
  onBackToLatest: () => void;
  onBaseThisVersion: () => void;
  onCompare?: () => void;
  /** 生成中禁用两个"会写数据"的动作（不静默失败）。 */
  busy?: boolean;
}

function text(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (acc, [key, value]) => acc.replace(`{${key}}`, String(value)),
    template,
  );
}

export function HistoricalBanner({
  version,
  generatedAt,
  latestVersion,
  latestGeneratedAt,
  onBackToLatest,
  onBaseThisVersion,
  onCompare,
  busy = false,
}: HistoricalBannerProps) {
  return (
    <div
      role="status"
      className="flex min-h-[40px] flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--warn)]/30 bg-[var(--warn-weak)] px-3 py-1.5 text-[11.5px] text-[var(--warn)]"
    >
      <History className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0">
        {text(copy.history.banner, { version, when: relativeTime(generatedAt) })}
      </span>
      <span className="text-[10.5px] text-[var(--text-tertiary)]">
        {text(copy.history.latestIs, {
          version: latestVersion,
          when: relativeTime(latestGeneratedAt ?? generatedAt),
        })}
      </span>

      <span className="ml-auto flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={onBackToLatest}
          className="h-6 rounded-[var(--radius-sm)] border border-[var(--warn)]/40 px-2 text-[11px] hover:bg-[var(--warn)]/15"
        >
          {text(copy.history.backToLatest, { version: latestVersion })}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onBaseThisVersion}
          className="h-6 rounded-[var(--radius-sm)] border border-[var(--warn)]/40 px-2 text-[11px] hover:bg-[var(--warn)]/15 disabled:opacity-45"
        >
          {copy.history.baseThisVersion}
        </button>
        {onCompare ? (
          <button
            type="button"
            onClick={onCompare}
            className="h-6 rounded-[var(--radius-sm)] border border-[var(--warn)]/40 px-2 text-[11px] hover:bg-[var(--warn)]/15"
          >
            {copy.history.compare}
          </button>
        ) : null}
      </span>
    </div>
  );
}
