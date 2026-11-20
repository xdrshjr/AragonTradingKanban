'use client';

import { ArrowUp, Square } from 'lucide-react';
import { useEffect, useRef } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import type { AskMode } from '@/lib/types';

import { Button } from '../ui/Button';

export interface AskComposerProps {
  draft: string;
  mode: AskMode;
  maxChars: number;
  canSubmit: boolean;
  isActive: boolean;
  isCancelling: boolean;
  canCancel: boolean;
  online: boolean;
  short: boolean;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  onDraftChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onCancel: () => void | Promise<void>;
}

/** Match Python normalize_question before applying the product limit. */
export function normalizedQuestionLength(raw: string): number {
  return Array.from(raw.normalize('NFKC')
    .replace(/[\r\n\t]/g, ' ').trim()).length;
}

export function AskComposer(props: AskComposerProps) {
  const composing = useRef(false);
  const submitting = useRef(false);
  const count = normalizedQuestionLength(props.draft);
  const limit = Number.isInteger(props.maxChars) && props.maxChars > 0
    ? Math.min(4000, props.maxChars) : 500;
  const overLimit = count > limit || Array.from(props.draft).length > 4000;
  const canSend = props.canSubmit && props.online && !overLimit && count > 0;
  const submit = async () => {
    if (!canSend || composing.current || submitting.current) return;
    submitting.current = true;
    try { await props.onSubmit(); }
    finally { submitting.current = false; }
  };
  useEffect(() => {
    const input = props.inputRef.current;
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(
      props.short ? 52 : 104, Math.max(38, input.scrollHeight),
    )}px`;
  }, [props.draft, props.short, props.inputRef]);
  return (
    <form className="assistant-composer shrink-0 border-t border-[var(--border-subtle)] p-3"
      onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <textarea
        ref={props.inputRef}
        value={props.draft}
        onChange={(event) => props.onDraftChange(event.target.value)}
        onCompositionStart={() => { composing.current = true; }}
        onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={(event) => {
          if (composing.current || event.nativeEvent.isComposing
            || event.nativeEvent.keyCode === 229) return;
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            void submit();
          }
        }}
        rows={props.short ? 1 : 2}
        placeholder={copy.ask.placeholder}
        aria-label={copy.ask.questionLabel}
        aria-invalid={overLimit || undefined}
        className={cn(
          'w-full resize-none overflow-y-auto rounded-[var(--radius-md)]',
          'border border-[var(--border-strong)] bg-[var(--bg-surface-1)]',
          'px-3 py-2 text-[13px] leading-[22px] text-[var(--text-primary)]',
          'outline-none placeholder:text-[var(--text-tertiary)]',
          'focus:border-[var(--accent)]/60 focus:ring-2',
          'focus:ring-[var(--accent-ring)]/40',
        )}
      />
      <div className="mt-2 flex items-center gap-2">
        <span className={cn('num text-[10.5px]', overLimit
          ? 'text-[var(--danger)]' : 'text-[var(--text-tertiary)]')}>
          {fill(copy.ask.counting, { count, limit })}
        </span>
        <span className="assistant-secondary text-[10px] text-[var(--text-tertiary)]">
          {copy.ask.shortcut}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {props.isActive && props.canCancel ? (
            <Button size="sm" variant="ghost" onClick={props.onCancel}
              disabled={props.isCancelling} leadingIcon={<Square className="size-3" />}>
              {props.isCancelling ? copy.ask.cancelling : copy.ask.cancel}
            </Button>
          ) : null}
          <Button type="submit" variant="primary" size="sm"
            disabled={!canSend} leadingIcon={<ArrowUp className="size-3.5" />}>
            {props.mode === 'deep' ? copy.ask.deepSubmit : copy.ask.submit}
          </Button>
        </div>
      </div>
      {overLimit ? <p role="alert" className="mt-1 text-[11px] text-[var(--danger)]">
        {copy.ask.overLimit}
      </p> : null}
      {!props.online ? <p role="status" className="mt-1 text-[11px] text-[var(--warn)]">
        {copy.ask.offline}
      </p> : null}
    </form>
  );
}

interface AskModePickerProps {
  mode: AskMode;
  deepAvailable: boolean;
  disabled: boolean;
  dailyLimit: number;
  onChange: (mode: AskMode) => void;
}

export function AskModePicker(props: AskModePickerProps) {
  return <div role="radiogroup" aria-label={copy.ask.modeGroup}
    className="inline-flex rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] p-0.5">
    {(['cite', 'deep'] as const).map((mode) => <button
      key={mode}
      type="button"
      role="radio"
      aria-checked={props.mode === mode}
      disabled={props.disabled || (mode === 'deep' && !props.deepAvailable)}
      title={mode === 'cite' ? copy.ask.citeContext
        : props.deepAvailable
          ? fill(copy.ask.deepHint, { limit: props.dailyLimit })
          : copy.ask.deepUnavailable}
      onClick={() => props.onChange(mode)}
      className={cn('h-7 rounded-[5px] px-2.5 text-[11px] font-medium',
        'disabled:cursor-not-allowed disabled:opacity-45',
        'focus-visible:outline-none focus-visible:ring-2',
        'focus-visible:ring-[var(--accent-ring)]',
        props.mode === mode ? 'bg-[var(--bg-surface-3)] text-[var(--text-primary)]'
          : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]')}>
      {mode === 'cite' ? copy.ask.modeCite : copy.ask.modeDeep}
    </button>)}
  </div>;
}
