'use client';

import { useQuery } from '@tanstack/react-query';
import { Diamond, MoreHorizontal, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { assistCopy } from '@/lib/workflow/copy.assist';
import { useWorkflowStore } from '@/lib/workflow/store';
import { useAssistStream } from '@/lib/workflow/useAssistStream';

import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/DropdownMenu';
import { AssistComposer } from './AssistComposer';
import { AssistMessage } from './AssistMessage';

type ChipId = keyof typeof assistCopy.chips;
const CHIP_IDS: ChipId[] = ['layout', 'criteria', 'coverage', 'restructure'];

/**
 * 右 30% 的 AI 流程设计助手（spec §3.10.7）。无 props，状态读 `useWorkflowStore`。
 *
 * 助手**只写草稿**：它没有发布 / 设默认的入口（后端也没有对应 op）。
 */
export default function AssistPanel() {
  const { state, send, cancel, clear, undo, locate } = useAssistStream();
  const readOnly = useWorkflowStore((store) => store.readOnly);
  const loaded = useWorkflowStore((store) => store.loaded);
  const focusIds = useWorkflowStore((store) => store.selection.nodes);
  const [draft, setDraft] = useState('');
  const listRef = useRef<HTMLUListElement | null>(null);
  const capabilities = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => api.capabilities(),
    staleTime: 60_000,
  });
  const engine = capabilities.data?.agentMode === 'mock' ? 'mock' : 'aragon';
  const disabledReason = readOnly ? assistCopy.readOnly : state.unavailable ? assistCopy.unavailable.body : null;
  const locked = readOnly || state.unavailable || !loaded;

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [state.messages.length, state.thinking]);

  const submit = (text: string) => {
    if (locked || state.busy) return;
    setDraft('');
    void send(text, focusIds);
  };

  return (
    <aside
      data-testid="assist-panel"
      aria-label={assistCopy.title}
      className="flex h-full min-h-0 flex-col bg-[var(--bg-surface-1)]"
    >
      <header className="flex h-7 shrink-0 items-center gap-1.5 border-b border-[var(--border-subtle)] px-2">
        <Diamond className="size-3 text-[var(--accent)]" aria-hidden="true" />
        <h2 className="min-w-0 flex-1 truncate text-[12px] font-semibold text-[var(--text-primary)]">
          {assistCopy.title}
        </h2>
        <Badge tone={engine === 'mock' ? 'info' : 'accent'} title={assistCopy.engine[engine]}>
          {assistCopy.engine[engine]}
        </Badge>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={assistCopy.menu}
              title={assistCopy.menu}
              className="grid size-5 place-items-center rounded-[var(--radius-sm)] text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={state.busy || state.messages.length === 0} onSelect={() => void clear()}>
              <Trash2 className="size-3.5" aria-hidden="true" />
              {assistCopy.clear}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      {state.unavailable ? (
        <div
          role="alert"
          data-testid="assist-unavailable"
          className="shrink-0 border-b border-[var(--border-subtle)] bg-[var(--warn-weak)] px-2.5 py-1.5 text-[11.5px] text-[var(--warn)]"
        >
          <p className="font-medium">{assistCopy.unavailable.title}</p>
          <p>{assistCopy.unavailable.body}</p>
          <Link href="/capabilities" className="underline decoration-dotted">
            {assistCopy.unavailable.link}
          </Link>
        </div>
      ) : null}

      <ul
        ref={listRef}
        aria-label={assistCopy.title}
        aria-live="polite"
        className="min-h-0 flex-1 overflow-y-auto py-1"
      >
        {state.messages.length === 0 && !state.busy ? (
          <li className="px-3 py-4 text-[12px] leading-relaxed text-[var(--text-tertiary)]">
            <p>{assistCopy.empty}</p>
            <p className="mt-1 text-[11px]">{assistCopy.emptyHint}</p>
          </li>
        ) : null}
        {state.messages.map((message) => (
          <AssistMessage
            key={message.id}
            message={message}
            disabled={locked || state.busy}
            onLocate={locate}
            onUndo={undo}
            onResend={submit}
          />
        ))}
        {state.busy ? (
          <li data-testid="assist-thinking" className="px-3 py-1.5 text-[11.5px] text-[var(--text-tertiary)]">
            <span className={cn('motion-safe:animate-pulse')}>
              {state.round >= 2 ? assistCopy.round2 : assistCopy.thinking}
            </span>
            {state.thinking ? <p className="mt-0.5 line-clamp-4 whitespace-pre-wrap">{state.thinking}</p> : null}
          </li>
        ) : null}
        {state.error ? (
          <li role="alert" data-testid="assist-error-line" className="px-3 py-1.5 text-[11.5px] text-[var(--danger)]">
            {state.error}
          </li>
        ) : null}
      </ul>

      <div className="flex shrink-0 flex-wrap gap-1 border-t border-[var(--border-subtle)] px-2 py-1.5">
        {CHIP_IDS.map((id) => (
          <Button
            key={id}
            size="sm"
            variant="ghost"
            disabled={locked || state.busy}
            className="!h-6 !px-1.5 !text-[11px]"
            onClick={() => submit(assistCopy.chipPrompts[id])}
          >
            {assistCopy.chips[id]}
          </Button>
        ))}
      </div>

      <AssistComposer
        draft={draft}
        disabled={locked}
        disabledReason={disabledReason}
        busy={state.busy}
        focusCount={focusIds.length}
        onDraftChange={setDraft}
        onSubmit={() => submit(draft)}
        onCancel={() => void cancel()}
      />
      <p className="sr-only" aria-live="polite">
        {state.busy ? assistCopy.busyBadge : ''}
      </p>
    </aside>
  );
}
