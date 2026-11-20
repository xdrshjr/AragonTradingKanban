'use client';

import { ArrowDown, MessageSquarePlus, Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';

import { Button } from '../ui/Button';
import { AskAnswer } from './AskAnswer';
import type { AskTurnView } from './useAskConversation';
import { initialAskStreamState, type AskStreamState } from './useAskStream';

export interface AskConversationProps {
  turns: AskTurnView[];
  streamState: AskStreamState;
  historyStatus: 'idle' | 'loading' | 'ready' | 'error';
  historyEnabled: boolean;
  identityKey: string;
  readError?: string | null;
  onSuggestion: (question: string) => void;
  onReloadHistory: () => void | Promise<void>;
  onStartWithoutHistory: () => void;
  onRetry: (turn: AskTurnView) => void | Promise<void>;
  onRefreshTurn: (turn: AskTurnView) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onReconnect: () => void | Promise<void>;
  onAcknowledgeDuplicate: () => void;
  onJumpToBlock: (blockId: string, sectionId: string) => void;
  onJumpToSource?: (sourceId: string) => void;
}

function EmptyConversation({ onSuggestion }: Pick<
  AskConversationProps, 'onSuggestion'
>) {
  return <div className="flex h-full min-h-[120px] flex-col justify-center py-4">
    <Sparkles aria-hidden="true"
      className="mb-3 size-5 text-[var(--accent)]" />
    <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">
      {copy.ask.emptyTitle}
    </h3>
    <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--text-tertiary)]">
      {copy.ask.emptyBody}
    </p>
    <div className="assistant-suggestions mt-5 flex flex-col gap-2">
      {copy.ask.suggestions.map((question) => <button type="button"
        key={question} onClick={() => onSuggestion(question)}
        className={cn('flex items-center gap-2 rounded-[var(--radius-md)]',
          'border border-[var(--border-subtle)] px-3 py-2.5 text-left',
          'text-[12px] text-[var(--text-secondary)]',
          'hover:border-[var(--accent)]/40 hover:bg-[var(--bg-hover)]',
          'focus-visible:outline-none focus-visible:ring-2',
          'focus-visible:ring-[var(--accent-ring)]')}>
        <MessageSquarePlus aria-hidden="true" className="size-3.5 text-[var(--accent)]" />
        {question}
      </button>)}
    </div>
  </div>;
}

function TurnActions({ turn, current, ...props }: AskConversationProps & {
  turn: AskTurnView; current: boolean;
}) {
  if (current && turn.error?.code === 'cancel_unconfirmed') return (
    <Button size="sm" variant="ghost" className="mt-2"
      onClick={props.onRefresh}>{copy.ask.refreshStatus}</Button>
  );
  if (turn.phase === 'submission-unknown') return <div className="mt-2 flex flex-wrap gap-1">
    <Button size="sm" onClick={props.onReloadHistory}>{copy.ask.reloadHistory}</Button>
    <Button size="sm" variant="ghost" onClick={props.onAcknowledgeDuplicate}>
      {copy.ask.duplicateRisk}
    </Button>
  </div>;
  if (turn.phase === 'disconnected' || turn.phase === 'unverified') {
    return <div className="mt-2 flex gap-2">
      <Button size="sm" onClick={current ? props.onRefresh
        : () => props.onRefreshTurn(turn)}>{copy.ask.refreshStatus}</Button>
      {current && turn.phase === 'disconnected' ? <Button size="sm"
        variant="ghost" onClick={props.onReconnect}>{copy.ask.reconnect}</Button> : null}
    </div>;
  }
  if (turn.phase === 'error' && !props.streamState.quota
    && turn.error?.code !== 'invalid_ask_context') {
    return <Button size="sm" variant="ghost" className="mt-2"
      onClick={() => props.onRetry(turn)}>{copy.ask.retry}</Button>;
  }
  if (!current && (turn.phase === 'streaming' || turn.phase === 'cancelling')) {
    return <Button size="sm" variant="ghost" className="mt-2"
      onClick={() => props.onRefreshTurn(turn)}>{copy.ask.refreshStatus}</Button>;
  }
  return null;
}

function ConversationTurn(props: AskConversationProps & { turn: AskTurnView }) {
  const { turn, streamState } = props;
  const current = turn.askId ? turn.askId === streamState.askId
    : turn.question === streamState.question;
  const state: AskStreamState = {
    ...initialAskStreamState,
    ...(current ? streamState : {}),
    phase: turn.phase,
    mode: turn.mode,
    question: turn.question,
    askId: turn.askId,
    result: turn.result,
    streamText: turn.preview,
    error: turn.error,
  };
  return <article className="flex flex-col gap-3 border-b border-[var(--border-subtle)] py-4 last:border-0"
    data-ask-id={turn.askId ?? undefined}>
    <div className="ml-7 rounded-[var(--radius-md)] border border-[var(--accent)]/15 bg-[var(--accent-weak)] px-3 py-2.5">
      <p className="whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-[var(--text-primary)]">
        {turn.question}
      </p>
      <p className="mt-1 text-[10px] text-[var(--text-tertiary)]">
        {turn.mode === 'deep' ? copy.ask.modeDeep : copy.ask.modeCite}
      </p>
    </div>
    <div className="min-w-0">
      <AskAnswer state={state} onJumpToBlock={props.onJumpToBlock}
        onJumpToSource={props.onJumpToSource} />
      <TurnActions {...props} current={current} />
    </div>
  </article>;
}

export function AskConversation(props: AskConversationProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const nearBottom = useRef(true);
  const [hasNew, setHasNew] = useState(false);
  const lastPreview = props.turns.at(-1)?.preview;
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (!element) return;
      if (nearBottom.current) element.scrollTo({ top: element.scrollHeight });
      else setHasNew(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [props.turns, lastPreview]);
  return <div className="relative flex min-h-0 flex-1 flex-col">
    <div ref={scrollRef}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4"
      data-assistant-messages="" onScroll={() => {
        const element = scrollRef.current;
        if (!element) return;
        nearBottom.current = element.scrollHeight - element.clientHeight
          - element.scrollTop <= 64;
        if (nearBottom.current) setHasNew(false);
      }}>
      <HistoryNotice {...props} />
      {props.turns.length >= 100 ? <p
        className="pt-2 text-[10px] text-[var(--text-tertiary)]">
        {copy.ask.recentMemoryWindow}
      </p> : null}
      {props.readError ? <p role="alert"
        className="py-2 text-[11px] text-[var(--warn)]">
        {props.readError}
      </p> : null}
      {props.turns.map((turn) => <ConversationTurn
        key={turn.askId ?? turn.localId} {...props} turn={turn} />)}
      {props.turns.length === 0 && props.historyStatus !== 'loading'
        ? <EmptyConversation onSuggestion={props.onSuggestion} /> : null}
    </div>
    {hasNew ? <button type="button" onClick={() => {
      nearBottom.current = true;
      setHasNew(false);
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }} className="absolute bottom-2 left-1/2 flex -translate-x-1/2 items-center gap-1 whitespace-nowrap rounded-full border border-[var(--accent)]/30 bg-[var(--bg-surface-2)] px-3 py-1.5 text-[11px] text-[var(--accent)] shadow-[var(--shadow-pop)]">
      <ArrowDown className="size-3" aria-hidden="true" />{copy.ask.bottom}
    </button> : null}
  </div>;
}

function HistoryNotice(props: AskConversationProps) {
  if (props.historyStatus === 'loading') return <p className="py-3 text-[11px] text-[var(--text-tertiary)]">
    {copy.ask.loadingHistory}
  </p>;
  if (props.historyStatus === 'error') return <div role="alert"
    className="my-3 rounded-[var(--radius-md)] border border-[var(--warn)]/30 p-2.5 text-[11px] text-[var(--warn)]">
    <p>{copy.ask.historyError}</p>
    <div className="mt-2 flex flex-wrap gap-1">
      <Button size="sm" variant="ghost" onClick={props.onReloadHistory}>
        {copy.ask.reloadHistory}
      </Button>
      <Button size="sm" variant="ghost" onClick={props.onStartWithoutHistory}>
        {copy.ask.startWithoutHistory}
      </Button>
    </div>
  </div>;
  if (!props.historyEnabled) return null;
  return <p className="assistant-secondary pt-3 text-[10px] text-[var(--text-tertiary)]">
    {props.identityKey === 'open' ? copy.ask.localHistory : copy.ask.personalHistory}
    {' · '}{copy.ask.historyWindow}
  </p>;
}
