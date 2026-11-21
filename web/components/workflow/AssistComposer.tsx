'use client';

import { ArrowUp, Square } from 'lucide-react';
import { useEffect, useRef } from 'react';

import { cn } from '@/lib/cn';
import { LIMITS } from '@/lib/workflow/contract';
import { assistCopy, fillAssist } from '@/lib/workflow/copy.assist';

import { Button } from '../ui/Button';

export interface AssistComposerProps {
  draft: string;
  disabled: boolean;
  /** 禁用原因（只读模板 / 引擎不可用…）；为空则仅因 busy 而禁用。 */
  disabledReason?: string | null;
  busy: boolean;
  focusCount: number;
  onDraftChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

/**
 * 右栏输入框：Enter 发送、Shift+Enter 换行（输入法组字期间不发送）、`n/2000` 计数。
 * 字数超限时不发送（服务端 422 兜底，这里只是少一次无谓的往返）。
 */
export function AssistComposer(props: AssistComposerProps) {
  const { draft, disabled, busy, focusCount } = props;
  const composing = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const max = LIMITS.ASSIST_MESSAGE_MAX;
  const count = Array.from(draft).length;
  const over = count > max;
  const canSend = !disabled && !busy && !over && draft.trim().length > 0;

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(120, Math.max(44, input.scrollHeight))}px`;
  }, [draft]);

  const submit = () => {
    if (canSend && !composing.current) props.onSubmit();
  };

  return (
    <form
      data-testid="assist-composer"
      className="shrink-0 border-t border-[var(--border-subtle)] p-2"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {focusCount > 0 ? (
        <p className="mb-1 text-[10.5px] text-[var(--text-tertiary)]">
          {fillAssist(assistCopy.composer.focusHint, { n: focusCount })}
        </p>
      ) : null}
      <textarea
        ref={inputRef}
        value={draft}
        disabled={disabled}
        rows={2}
        onChange={(event) => props.onDraftChange(event.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        onKeyDown={(event) => {
          if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={props.disabledReason ?? assistCopy.composer.placeholder}
        aria-label={assistCopy.composer.label}
        aria-invalid={over || undefined}
        className={cn(
          'w-full resize-none overflow-y-auto rounded-[var(--radius-sm)] border bg-[var(--bg-surface-1)]',
          'px-2 py-1.5 text-[12.5px] leading-5 text-[var(--text-primary)] outline-none',
          'placeholder:text-[var(--text-tertiary)] focus-visible:border-[var(--accent)]',
          'disabled:cursor-not-allowed disabled:opacity-60',
          over ? 'border-[var(--danger)]' : 'border-[var(--border-strong)]',
        )}
      />
      <div className="mt-1 flex items-center justify-between gap-2">
        <span
          className={cn('mono text-[10.5px]', over ? 'text-[var(--danger)]' : 'text-[var(--text-tertiary)]')}
          aria-live="off"
        >
          {fillAssist(assistCopy.composer.counter, { n: count, max })}
        </span>
        {busy ? (
          <Button size="sm" variant="ghost" onClick={props.onCancel} leadingIcon={<Square className="size-3" />}>
            {assistCopy.cancel}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="primary"
            type="submit"
            disabled={!canSend}
            leadingIcon={<ArrowUp className="size-3" />}
          >
            {assistCopy.composer.send}
          </Button>
        )}
      </div>
    </form>
  );
}
