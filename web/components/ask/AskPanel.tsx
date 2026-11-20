'use client';

import { RotateCcw, Sparkles, X } from 'lucide-react';
import { useCallback, useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';

import { copy, fill } from '@/lib/copy';
import { useReturnFocus } from '@/lib/focus';

import { Button, IconButton } from '../ui/Button';
import { AskComposer, AskModePicker } from './AskComposer';
import { AskConversation } from './AskConversation';
import { useAskConversation } from './useAskConversation';
import { assistantViewportStyle, useVisualViewport } from './useVisualViewport';

export interface AskPanelProps {
  reportId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  deepAvailable: boolean;
  dailyLimit: number;
  onJumpToBlock: (blockId: string, sectionId: string) => void;
  onJumpToSource?: (sourceId: string) => void;
  triggerRef?: React.RefObject<HTMLElement | null>;
  identityKey?: string;
  conversationAvailable?: boolean;
  questionMaxChars?: number;
  reportLabel?: string;
  panelId?: string;
  returnFocus?: boolean;
  onUnreadChange?: (hasUnread: boolean) => void;
  onActivityChange?: (isActive: boolean) => void;
}

/** A non-modal assistant; closing only changes visibility, never the task. */
export function AskPanel(props: AskPanelProps) {
  const {
    reportId, open, deepAvailable, dailyLimit,
    identityKey = 'open', conversationAvailable = false,
    questionMaxChars = 500, panelId = 'aragon-ai-assistant',
    onOpenChange, onUnreadChange, onActivityChange,
  } = props;
  const conversation = useAskConversation({
    reportId, identityKey, open, deepAvailable, conversationAvailable,
    dailyLimit, questionMaxChars,
  });
  const viewport = useVisualViewport(open);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const fallbackTrigger = useRef<HTMLElement | null>(null);
  const close = useCallback(() => onOpenChange(false), [onOpenChange]);
  useReturnFocus(props.triggerRef ?? fallbackTrigger, open, close, {
    handleEscape: false, returnFocus: props.returnFocus ?? true,
  });
  useEffect(() => {
    if (open) inputRef.current?.focus({ preventScroll: true });
  }, [open]);
  useEffect(() => {
    onUnreadChange?.(conversation.hasUnread);
  }, [conversation.hasUnread, onUnreadChange]);
  useEffect(() => {
    onActivityChange?.(conversation.isActive);
  }, [conversation.isActive, onActivityChange]);
  if (!open || typeof document === 'undefined') return null;
  const compact = viewport
    ? viewport.width < 640 || viewport.height < 640 : false;
  const short = viewport ? viewport.height < 480 : false;
  return createPortal(<section id={panelId} role="region"
    aria-label={copy.ask.title} data-print="hide" className="assistant-panel"
    data-compact={compact} data-short={short}
    style={assistantViewportStyle(viewport)}>
    <PanelHeader reportLabel={props.reportLabel} onClose={close} />
    <PanelContext conversation={conversation} deepAvailable={deepAvailable}
      dailyLimit={dailyLimit} conversationAvailable={conversationAvailable} />
    <AskConversation turns={conversation.turns}
      streamState={conversation.stream.state}
      historyStatus={conversation.historyStatus}
      historyEnabled={conversationAvailable} identityKey={identityKey}
      readError={conversation.readError}
      onSuggestion={(question) => {
        conversation.setDraft(question);
        inputRef.current?.focus();
      }}
      onReloadHistory={conversation.reloadHistory}
      onStartWithoutHistory={conversation.startWithoutHistory}
      onRetry={conversation.retry}
      onRefreshTurn={conversation.refreshTurn}
      onRefresh={conversation.stream.refresh}
      onReconnect={conversation.stream.reconnect}
      onAcknowledgeDuplicate={conversation.stream.acknowledgeDuplicateRisk}
      onJumpToBlock={props.onJumpToBlock}
      onJumpToSource={props.onJumpToSource} />
    <QuotaNotice conversation={conversation} dailyLimit={dailyLimit} />
    <AskComposer draft={conversation.draft} mode={conversation.mode}
      maxChars={questionMaxChars} canSubmit={conversation.canSubmit}
      isActive={conversation.isActive}
      isCancelling={conversation.stream.state.phase === 'cancelling'
        && conversation.stream.state.error?.code !== 'cancel_unconfirmed'}
      canCancel={Boolean(conversation.stream.state.askId)}
      online={conversation.online} short={short} inputRef={inputRef}
      onDraftChange={conversation.setDraft}
      onSubmit={() => conversation.submit()}
      onCancel={conversation.stream.cancel} />
    <AssistantAnnouncement phase={conversation.stream.state.phase} />
  </section>, document.body);
}

function PanelHeader({ reportLabel, onClose }: {
  reportLabel?: string; onClose: () => void;
}) {
  return <header className="shrink-0 border-b border-[var(--border-subtle)] px-4 py-2">
    <div className="flex items-center gap-2.5">
      <span className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent-weak)]">
        <Sparkles className="size-4 text-[var(--accent)]" aria-hidden="true" />
      </span>
      <h2 className="min-w-0 flex-1 text-[13px] font-semibold text-[var(--text-primary)]">
        {copy.ask.title}
      </h2>
      <IconButton label={copy.ask.close} onClick={onClose} className="!size-11">
        <X className="size-4" aria-hidden="true" />
      </IconButton>
    </div>
    {reportLabel ? <p title={reportLabel}
      className="assistant-version truncate pb-1 text-[11px] text-[var(--text-tertiary)]">
      {reportLabel}
    </p> : null}
  </header>;
}

interface PanelContextProps {
  conversation: ReturnType<typeof useAskConversation>;
  deepAvailable: boolean;
  dailyLimit: number;
  conversationAvailable: boolean;
}

function PanelContext(props: PanelContextProps) {
  const { conversation } = props;
  return <div className="shrink-0 border-b border-[var(--border-subtle)] px-3 py-2">
    <div className="flex flex-wrap items-center justify-between gap-1.5">
      <AskModePicker mode={conversation.mode}
        deepAvailable={props.deepAvailable} disabled={conversation.isActive}
        dailyLimit={props.dailyLimit} onChange={conversation.setMode} />
      <Button size="sm" variant="ghost" disabled={conversation.isActive}
        onClick={conversation.newTopic} title={copy.ask.newTopicHint}
        leadingIcon={<RotateCcw className="size-3" aria-hidden="true" />}>
        {copy.ask.newTopic}
      </Button>
    </div>
    <p className="assistant-secondary mt-1.5 text-[10.5px] leading-relaxed text-[var(--text-tertiary)]">
      {conversation.mode === 'deep'
        ? fill(copy.ask.deepContext, { count: Math.min(6, conversation.contextCandidateIds.length) })
        : copy.ask.citeContext}
    </p>
    {!props.conversationAvailable ? <p
      className="assistant-secondary mt-1 text-[10.5px] text-[var(--text-tertiary)]">
      {copy.ask.singleQuestion}
    </p> : null}
    {!props.deepAvailable ? <p
      className="assistant-secondary mt-1 text-[10.5px] text-[var(--warn)]">
      {copy.ask.deepUnavailable}
    </p> : null}
    {props.deepAvailable && conversation.deepQuotaKnown ? <p
      className="assistant-secondary mt-1 text-[10px] text-[var(--text-tertiary)]">
      {conversation.deepUnlimited ? copy.quota.unlimited
        : fill(copy.ask.deepRemaining, { remaining: conversation.stream.deepRemaining })}
    </p> : null}
  </div>;
}

function QuotaNotice({ conversation, dailyLimit }: {
  conversation: ReturnType<typeof useAskConversation>; dailyLimit: number;
}) {
  const titleId = useId();
  const bodyId = useId();
  const quota = conversation.stream.state.quota;
  if (!quota) return null;
  return <div role="alertdialog" aria-labelledby={titleId}
    aria-describedby={bodyId}
    className="mx-3 mb-2 shrink-0 rounded-[var(--radius-md)] border border-[var(--warn)]/40 bg-[var(--warn-weak)] p-3">
    <p className="text-[10px] text-[var(--warn)]">{copy.ask.citeFallbackTitle}</p>
    <p id={titleId} className="mt-1 text-[12px] font-semibold text-[var(--warn)]">
      {conversation.stream.state.error?.code === 'quota_exceeded'
        ? conversation.stream.state.error.message
        : fill(copy.ask.quotaTitle, {
          limit: quota.limit > 0 ? quota.limit : dailyLimit,
        })}
    </p>
    <p id={bodyId} className="mt-1 text-[11px] text-[var(--text-secondary)]">
      {copy.ask.quotaBody}
    </p>
    <div className="mt-2 flex gap-2">
      <Button variant="accent-ghost" size="sm"
        onClick={() => void conversation.stream.confirmFallback()}>{copy.ask.quotaAccept}</Button>
      <Button variant="ghost" size="sm"
        onClick={conversation.stream.declineFallback}>{copy.ask.quotaDecline}</Button>
    </div>
  </div>;
}

function AssistantAnnouncement({ phase }: { phase: string }) {
  const label = phase === 'submitting' ? copy.ask.started
    : phase === 'done' ? copy.ask.completed
      : phase === 'error' ? copy.ask.failed
        : phase === 'cancelled' ? copy.ask.cancelled : '';
  return <span role="status" aria-live="polite"
    aria-label={copy.a11y.askLiveRegion} className="sr-only">{label}</span>;
}

export default AskPanel;
