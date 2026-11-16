'use client';

import { AlertTriangle, ArrowUpRight, Loader2, X } from 'lucide-react';
import { useCallback, useEffect, useId, useRef } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { useReturnFocus } from '@/lib/focus';
import type { AskHit, AskMode, AskOut, Citation } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Button, IconButton } from '../ui/Button';
import { CitationLegend } from './CitationLegend';
import { useAskStream, type AskStreamState } from './useAskStream';

/**
 * 报告内追问（spec §18.3.2 / §18.5.1 / AC-26 / AC-27 / AC-36）。
 *
 * 形态是**非模态抽屉**（`role="region"`，不是 Dialog）：读者要能一边看答案一边滚动
 * 报告，所以不做焦点陷阱——Tab 可以自由进出，Esc 关闭并把焦点还给触发元素。
 * 由集成方用 `next/dynamic({ssr:false})` 挂载（AC-35：不进首屏 chunk），
 * 因此本文件不允许有 import 期副作用，也不允许在模块顶层发请求。
 */
export interface AskPanelProps {
  reportId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** capabilities.features.askDeep — false in mock mode */
  deepAvailable: boolean;
  /** limits.askDailyLimit */
  dailyLimit: number;
  /** scroll the report to a block and highlight it */
  onJumpToBlock: (blockId: string, sectionId: string) => void;
  /** open the source drawer for a sourceId */
  onJumpToSource?: (sourceId: string) => void;
  /** element to return focus to on close */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

export function AskPanel({
  reportId,
  open,
  onOpenChange,
  deepAvailable,
  dailyLimit,
  onJumpToBlock,
  onJumpToSource,
  triggerRef,
}: AskPanelProps) {
  const { state, setQuestion, setMode, submit, confirmFallback, declineFallback, cancel, isActive, deepRemaining } =
    useAskStream({ reportId, dailyLimit, deepAvailable });

  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const quotaTitleId = useId();
  const quotaBodyId = useId();

  const mode: AskMode = deepAvailable ? state.mode : 'cite';
  const canSubmit = state.question.trim().length > 0 && !isActive;
  const streaming = state.phase === 'submitting' || state.phase === 'streaming' || state.phase === 'cancelling';

  const close = useCallback(() => {
    onOpenChange(false);
  }, [onOpenChange]);

  // AC-36 / I-24：焦点归还只允许一个实现，就是 `lib/focus.ts`。它同时负责
  // Esc 关闭与"从开到关"的焦点归还，并且忽略已被上层浮层处理过的键事件，
  // 所以不会再和来源抽屉一起被同一次 Esc 关掉。
  // 一个不会被赋值的占位 ref：调用方没给触发元素时，焦点归还安静地什么都不做，
  // 而不是把焦点丢给 `body`。
  const fallbackTriggerRef = useRef<HTMLElement | null>(null);
  useReturnFocus(triggerRef ?? fallbackTriggerRef, open, close);

  // 打开即把焦点交给输入框（不是陷阱，只是让键盘用户少按几次 Tab）。
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <section
      role="region"
      aria-label={copy.ask.title}
      data-print="hide"
      className={cn(
        'fixed bottom-0 left-1/2 z-30 flex w-[min(96vw,760px)] -translate-x-1/2 flex-col',
        'max-h-[74vh] rounded-t-[var(--radius-lg)] border border-b-0 border-[var(--border-strong)]',
        'bg-[var(--bg-surface-2)] shadow-[var(--shadow-pop)]',
      )}
    >
      <header className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
        <h2 className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-[var(--text-primary)]">
          {copy.ask.title}
        </h2>
        <div
          role="radiogroup"
          aria-label={copy.ask.modeGroup}
          className={cn(
            'inline-flex items-center gap-px rounded-[var(--radius-sm)] border border-[var(--border-strong)]',
            'bg-[var(--bg-surface-1)] p-px',
          )}
        >
          <ModeButton active={mode === 'cite'} label={copy.ask.modeCite} onClick={() => setMode('cite')} />
          <ModeButton
            active={mode === 'deep'}
            label={copy.ask.modeDeep}
            title={deepAvailable ? fill(copy.ask.deepHint, { limit: dailyLimit }) : copy.ask.deepUnavailable}
            disabled={!deepAvailable}
            onClick={() => setMode('deep')}
          />
        </div>
        <IconButton label={copy.ask.close} onClick={close}>
          <X className="size-3.5" aria-hidden="true" />
        </IconButton>
      </header>

      <form
        className="flex flex-col gap-1.5 border-b border-[var(--border-subtle)] px-3 py-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <textarea
          ref={inputRef}
          value={state.question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            // Enter 换行，⌘/Ctrl+Enter 提交（和生成按钮的肌肉记忆保持一致）。
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void submit();
            }
          }}
          rows={2}
          maxLength={500}
          placeholder={copy.ask.placeholder}
          aria-label={copy.ask.questionLabel}
          className={cn(
            'w-full resize-none rounded-[var(--radius-sm)] border border-[var(--border-strong)]',
            'bg-[var(--bg-surface-1)] px-2.5 py-2 text-[13px] leading-relaxed text-[var(--text-primary)]',
            'outline-none placeholder:text-[var(--text-tertiary)]',
            'transition-colors duration-[var(--dur-fast)]',
            'focus:border-[var(--accent)]/60 focus:ring-2 focus:ring-[var(--accent-ring)]/40',
          )}
        />

        <div className="flex items-center gap-2">
          <p className="min-w-0 flex-1 text-[11px] leading-snug text-[var(--text-tertiary)]">
            {mode === 'deep' ? fill(copy.ask.deepHint, { limit: dailyLimit }) : copy.ask.hint}
          </p>
          {isActive && state.askId ? (
            <Button variant="ghost" size="sm" onClick={() => void cancel()}>
              {state.phase === 'cancelling' ? copy.ask.cancelling : copy.ask.cancel}
            </Button>
          ) : null}
          {state.phase === 'error' ? (
            <Button variant="ghost" size="sm" onClick={() => void submit()}>
              {copy.ask.retry}
            </Button>
          ) : null}
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={!canSubmit}
            loading={isActive}
          >
            {mode === 'deep' ? copy.ask.deepSubmit : copy.ask.submit}
          </Button>
        </div>

        {deepAvailable ? (
          <p className="text-[11px] text-[var(--text-tertiary)]">
            {fill(copy.ask.deepRemaining, { remaining: deepRemaining })}
          </p>
        ) : (
          <p className="flex items-start gap-1 text-[11px] leading-snug text-[var(--warn)]">
            <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden="true" />
            {copy.ask.deepUnavailable}
          </p>
        )}
      </form>

      {/* aria-live="polite"：答案到达时播报一次，不打断读者（spec §18.3.6）。 */}
      <div
        aria-live="polite"
        aria-busy={streaming || undefined}
        aria-label={copy.a11y.askLiveRegion}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-3"
      >
        {state.quota ? (
          <div
            role="alertdialog"
            aria-labelledby={quotaTitleId}
            aria-describedby={quotaBodyId}
            className={cn(
              'rounded-[var(--radius-md)] border border-[var(--warn)]/40 bg-[var(--warn-weak)] px-3 py-2.5',
            )}
          >
            <p className="text-[10.5px] uppercase tracking-wide text-[var(--warn)]">{copy.ask.citeFallbackTitle}</p>
            <p id={quotaTitleId} className="mt-0.5 text-[12.5px] font-semibold text-[var(--warn)]">
              {fill(copy.ask.quotaTitle, { limit: state.quota.limit > 0 ? state.quota.limit : dailyLimit })}
            </p>
            <p id={quotaBodyId} className="mt-1 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
              {copy.ask.quotaBody}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <Button variant="accent-ghost" size="sm" onClick={() => void confirmFallback()}>
                {copy.ask.quotaAccept}
              </Button>
              <Button variant="ghost" size="sm" onClick={declineFallback}>
                {copy.ask.quotaDecline}
              </Button>
            </div>
          </div>
        ) : null}

        <div className={cn(state.quota && 'mt-2.5')}>
          <AnswerBody state={state} onJumpToBlock={onJumpToBlock} onJumpToSource={onJumpToSource} />
        </div>
      </div>
    </section>
  );
}

/* ── 内部件 ──────────────────────────────────────────────────────────────── */

function ModeButton({
  active,
  label,
  title,
  disabled,
  onClick,
}: {
  active: boolean;
  label: string;
  title?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      title={title ?? label}
      onClick={onClick}
      className={cn(
        'rounded-[5px] px-2 text-[11.5px] font-medium transition-colors duration-[var(--dur-fast)]',
        'h-6 disabled:cursor-not-allowed disabled:opacity-45',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]',
        active
          ? 'bg-[var(--bg-surface-3)] text-[var(--text-primary)]'
          : 'text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-secondary)]',
      )}
    >
      {label}
    </button>
  );
}

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

function AnswerBody({ state, onJumpToBlock, onJumpToSource }: AnswerBodyProps) {
  if (state.phase === 'idle') {
    return <p className="text-[12px] leading-relaxed text-[var(--text-tertiary)]">{copy.ask.empty}</p>;
  }

  if (state.phase === 'submitting') {
    return <StatusLine label={state.mode === 'deep' ? copy.ask.thinking : copy.ask.searching} />;
  }

  if (state.phase === 'streaming' || state.phase === 'cancelling') {
    return (
      <div className="flex flex-col gap-2">
        <StatusLine label={state.stage?.label || copy.ask.thinking} />
        {state.streamText ? (
          <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
            {state.streamText}
          </p>
        ) : null}
      </div>
    );
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
          {state.error?.message ? `：${state.error.message}` : null}
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
        <p className="num text-[10.5px] text-[var(--text-tertiary)]">
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

export default AskPanel;
