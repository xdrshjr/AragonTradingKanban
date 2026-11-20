'use client';

import { AlertTriangle, ArrowUpRight, Loader2 } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import type { AskHit, AskOut, Citation } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { CitationLegend } from './CitationLegend';
import type { AskStreamState } from './useAskStream';

function StatusLine({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-1.5 text-[12px] text-[var(--text-secondary)]">
      <Loader2 className="size-3.5 shrink-0 animate-spin text-[var(--accent)]" aria-hidden="true" />
      <span>{label}</span>
    </p>
  );
}

function paragraphs(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

/** 置信度是 0–1 的数字（§18.5.1 示例 0.82），文案映射集中在 copy。 */
function confidenceBadge(value: number | null | undefined): { label: string; tone: 'accent' | 'neutral' | 'warn' } | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  if (value >= 0.75) return { label: copy.ask.confidence.high, tone: 'accent' };
  if (value >= 0.5) return { label: copy.ask.confidence.medium, tone: 'neutral' };
  return { label: copy.ask.confidence.low, tone: 'warn' };
}

interface AnswerBodyProps {
  state: AskStreamState;
  onJumpToBlock: (blockId: string, sectionId: string) => void;
  onJumpToSource?: (sourceId: string) => void;
}

export function AskAnswer({ state, onJumpToBlock, onJumpToSource }: AnswerBodyProps) {
  if (state.phase === 'idle') {
    return <p className="text-[12px] leading-relaxed text-[var(--text-tertiary)]">{copy.ask.empty}</p>;
  }

  if (state.phase === 'submitting') {
    return <StatusLine label={state.mode === 'deep' ? copy.ask.thinking : copy.ask.searching} />;
  }

  if (state.phase === 'streaming' || state.phase === 'cancelling'
    || state.phase === 'verifying') {
    return (
      <div className="flex flex-col gap-2">
        <StatusLine label={state.phase === 'verifying' ? copy.ask.verifying
          : state.reconnectAttempt > 0 ? copy.ask.reconnecting
            : state.stage?.label || copy.ask.thinking} />
        {state.error?.message ? <p role="alert"
          className="text-[11px] text-[var(--warn)]">
          {state.error.message}
        </p> : null}
        {state.streamText ? (
          <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
            {state.streamText}
          </p>
        ) : null}
      </div>
    );
  }

  if (state.phase === 'disconnected' || state.phase === 'unverified'
    || state.phase === 'submission-unknown') {
    return <div className="flex flex-col gap-2">
      <p className="text-[12px] text-[var(--warn)]">
        {state.phase === 'disconnected' ? copy.ask.disconnected
          : state.phase === 'unverified' ? copy.ask.unverified
            : copy.ask.submissionUnknown}
      </p>
      {state.error?.message ? <p
        className="text-[11px] text-[var(--text-secondary)]">
        {state.error.message}
      </p> : null}
      {state.streamText ? <p
        className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
        {state.streamText}
      </p> : null}
    </div>;
  }

  if (state.phase === 'cancelled') {
    return <p className="text-[12px] text-[var(--text-tertiary)]">{copy.ask.cancelled}</p>;
  }

  if (state.phase === 'error') {
    return (
      <p
        role="alert"
        className={cn(
          'flex items-start gap-1 rounded-[var(--radius-sm)] border border-[var(--danger)]/40',
          'bg-[var(--danger-weak)] px-2 py-1.5 text-[12px] leading-relaxed text-[var(--danger)]',
        )}
      >
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span>
          {copy.ask.failed}
          {state.error?.code === 'invalid_ask_context'
            ? `：${copy.ask.invalidContext}`
            : state.error?.message ? `：${state.error.message}` : null}
        </span>
      </p>
    );
  }

  if (!state.result) {
    return <p className="text-[12px] leading-relaxed text-[var(--text-tertiary)]">{copy.ask.empty}</p>;
  }

  return <AnswerResult result={state.result} onJumpToBlock={onJumpToBlock} onJumpToSource={onJumpToSource} />;
}

function AnswerResult({
  result,
  onJumpToBlock,
  onJumpToSource,
}: {
  result: AskOut;
  onJumpToBlock: (blockId: string, sectionId: string) => void;
  onJumpToSource?: (sourceId: string) => void;
}) {
  const hits = result.answer.hits ?? [];
  const citations: Citation[] = result.answer.citations ?? [];
  const confidence = confidenceBadge(result.confidence);
  const hint = result.answer.hint?.trim() ?? '';

  return (
    <div className="flex flex-col gap-2.5">
      {result.mode === 'deep' ? <p
        className="text-[10.5px] text-[var(--text-tertiary)]">
        {fill(copy.ask.deepContext, { count: result.contextAskIds?.length ?? 0 })}
      </p> : null}
      {/* 引用合法性是硬信息：丢了就说丢了，不悄悄少显示几条（§18.3.2）。 */}
      {result.citationsDropped > 0 ? (
        <p
          className={cn(
            'flex items-start gap-1 rounded-[var(--radius-sm)] border border-[var(--warn)]/40',
            'bg-[var(--warn-weak)] px-2 py-1 text-[11px] leading-snug text-[var(--warn)]',
          )}
        >
          <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden="true" />
          <span>{fill(copy.ask.droppedNotice, { count: result.citationsDropped })}</span>
        </p>
      ) : null}

      {/* 未接地时必须给出原因（临时版报告就是 hint），否则就是一个空盒子。 */}
      {!result.grounded ? (
        <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
          {hint || copy.ask.notGrounded}
        </p>
      ) : null}

      {result.mode === 'deep' && result.answer.answerText ? (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <h3 className="text-[11.5px] font-semibold text-[var(--text-secondary)]">{copy.ask.answerTitle}</h3>
            {confidence ? <Badge tone={confidence.tone}>{confidence.label}</Badge> : null}
          </div>
          {paragraphs(result.answer.answerText).map((text, index) => (
            <p key={index} className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--text-primary)]">
              {text}
            </p>
          ))}
        </div>
      ) : null}

      {hits.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-[11.5px] font-semibold text-[var(--text-secondary)]">
            {fill(copy.ask.hitsTitle, { count: hits.length })}
          </h3>
          <ul className="flex flex-col gap-1">
            {hits.map((hit) => (
              <li key={hit.blockId}>
                <HitButton hit={hit} onJump={onJumpToBlock} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <CitationLegend citations={citations} onJumpToSource={onJumpToSource} />

      {result.latencyMs !== null && result.latencyMs !== undefined ? (
        <p className="assistant-secondary num text-[10.5px] text-[var(--text-tertiary)]">
          {fill(copy.ask.latency, { ms: result.latencyMs })}
          {result.mode === 'deep' ? ` · ${result.costKnown && result.costUsd != null ? `$${result.costUsd.toFixed(2)}` : copy.ask.costUnknown}` : null}
        </p>
      ) : null}
    </div>
  );
}

function HitButton({ hit, onJump }: { hit: AskHit; onJump: (blockId: string, sectionId: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onJump(hit.blockId, hit.sectionId)}
      className={cn(
        'group flex w-full flex-col gap-1 rounded-[var(--radius-sm)] border border-[var(--border-subtle)]',
        'bg-[var(--bg-surface-1)] px-2.5 py-1.5 text-left',
        'transition-colors duration-[var(--dur-fast)]',
        'hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]',
      )}
    >
      <span className="flex w-full items-center gap-1.5 text-[10.5px] text-[var(--text-tertiary)]">
        <span className="truncate text-[var(--text-secondary)]">{hit.sectionTitle}</span>
        <span className="font-mono uppercase">{hit.blockType}</span>
        <ArrowUpRight
          className="ml-auto size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
          aria-hidden="true"
        />
      </span>
      <span className="line-clamp-3 text-[12px] leading-relaxed text-[var(--text-primary)]">{hit.excerpt}</span>
      {hit.citations.length > 0 ? (
        <span className="font-mono text-[10px] text-[var(--accent)]">{hit.citations.join(' ')}</span>
      ) : null}
    </button>
  );
}
