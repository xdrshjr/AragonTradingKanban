'use client';

import { CircleAlert, Crosshair, RotateCcw, Undo2 } from 'lucide-react';

import { cn } from '@/lib/cn';
import type { AppliedChange, AssistUiMessage } from '@/lib/workflow/assistState';
import { assistCopy, fillAssist } from '@/lib/workflow/copy.assist';

import { Button } from '../ui/Button';

export interface AssistMessageProps {
  message: AssistUiMessage;
  /** 画布聚焦并短暂高亮这些节点。 */
  onLocate: (ids: string[]) => void;
  onUndo: (message: AssistUiMessage) => void;
  /** 重发一条文本（追问 chips / 重试 / 基于最新画布重试）。 */
  onResend: (text: string) => void;
  disabled?: boolean;
}

/** 变更卡片的一行摘要：`新增 2 步 · 1 检查点 · 3 条连线`（计数来自已展开的基本 op，服务端摘要兜底）。 */
export function summarizeChange(change: AppliedChange): string {
  const gates = change.ops.filter((op) => op.op === 'add_node' && op.node.kind === 'gate').length;
  const steps = Math.max(0, change.summary.added - gates);
  const parts: string[] = [];
  if (steps > 0) parts.push(fillAssist(assistCopy.card.added, { n: steps }));
  if (gates > 0) parts.push(fillAssist(assistCopy.card.addedGate, { n: gates }));
  if (change.summary.updated > 0) parts.push(fillAssist(assistCopy.card.updated, { n: change.summary.updated }));
  if (change.summary.deleted > 0) parts.push(fillAssist(assistCopy.card.deleted, { n: change.summary.deleted }));
  if (change.summary.edges > 0) parts.push(fillAssist(assistCopy.card.edges, { n: change.summary.edges }));
  return parts.length > 0 ? parts.join(' · ') : assistCopy.card.nothing;
}

function ChangeCard({ message, change, onLocate, onUndo, disabled }: AssistMessageProps & { change: AppliedChange }) {
  if (!change.applied) {
    return (
      <p data-testid="assist-not-applied" className="mt-1 text-[11px] text-[var(--text-tertiary)]">
        {assistCopy.card.notApplied}
        {change.reason ? ` · ${assistCopy.reasons[change.reason] ?? assistCopy.reasons.default}` : ''}
      </p>
    );
  }
  const validation = change.validation;
  return (
    <div
      data-testid="assist-change-card"
      className="mt-1.5 rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-2 py-1.5"
    >
      <p className="text-[11.5px] font-medium text-[var(--text-primary)]">{summarizeChange(change)}</p>
      {validation ? (
        <p className="mono mt-0.5 text-[10.5px] text-[var(--text-tertiary)]">
          {fillAssist(assistCopy.card.validation, { errors: validation.errorCount, warnings: validation.warningCount })}
        </p>
      ) : null}
      <div className="mt-1.5 flex items-center gap-1.5">
        {change.affectedIds.length > 0 ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onLocate(change.affectedIds)}
            leadingIcon={<Crosshair className="size-3" />}
          >
            {assistCopy.card.locate}
          </Button>
        ) : null}
        {change.undone ? (
          <span className="text-[10.5px] text-[var(--text-tertiary)]">{assistCopy.card.undone}</span>
        ) : change.undoHandle ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => onUndo(message)}
            leadingIcon={<Undo2 className="size-3" />}
          >
            {assistCopy.card.undo}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function SpecialCard({ message, onResend, disabled }: AssistMessageProps) {
  if (message.kind === 'interrupted') {
    return (
      <div data-testid="assist-interrupted" className="flex items-center gap-2 text-[11.5px] text-[var(--text-tertiary)]">
        <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="flex-1">{assistCopy.interrupted.text}</span>
        {message.retryText ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => onResend(message.retryText ?? '')}
            leadingIcon={<RotateCcw className="size-3" />}
          >
            {assistCopy.interrupted.retry}
          </Button>
        ) : null}
      </div>
    );
  }
  if (message.kind === 'conflict') {
    return (
      <div
        data-testid="assist-conflict"
        role="alert"
        className="rounded-[var(--radius-sm)] border border-[var(--warn)]/40 bg-[var(--warn-weak)] px-2 py-1.5 text-[11.5px] text-[var(--warn)]"
      >
        <p className="flex items-start gap-1.5">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>{assistCopy.conflict.title}</span>
        </p>
        {message.retryText ? (
          <Button
            size="sm"
            variant="secondary"
            className="mt-1.5"
            disabled={disabled}
            onClick={() => onResend(message.retryText ?? '')}
            leadingIcon={<RotateCcw className="size-3" />}
          >
            {assistCopy.conflict.retry}
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <p data-testid="assist-error" className="text-[11.5px] text-[var(--text-secondary)]">
      {assistCopy.reasons[message.content] ?? assistCopy.reasons.default}
    </p>
  );
}

/** 一条对话消息：用户气泡 / 助手回复（含变更卡片、追问 chips）/ 冲突卡 / 中断提示。 */
export function AssistMessage(props: AssistMessageProps) {
  const { message, onResend, disabled } = props;
  const isUser = message.role === 'user';
  return (
    <li
      data-testid={`assist-msg-${message.role}`}
      className={cn('flex flex-col gap-0.5 px-2.5 py-1.5', isUser ? 'items-end' : 'items-start')}
    >
      <span className="text-[10px] text-[var(--text-tertiary)]">
        {isUser ? assistCopy.user : assistCopy.assistant}
      </span>
      <div
        className={cn(
          'max-w-full rounded-[var(--radius-sm)] px-2 py-1.5 text-[12.5px] leading-[1.55]',
          isUser
            ? 'whitespace-pre-wrap break-words bg-[var(--accent-weak)] text-[var(--text-primary)]'
            : 'w-full bg-[var(--bg-surface-1)] text-[var(--text-primary)]',
        )}
      >
        {message.kind === 'text' ? (
          <>
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
            {message.applied ? <ChangeCard {...props} change={message.applied} /> : null}
            {message.openQuestions.length > 0 ? (
              <div data-testid="assist-followups" className="mt-1.5">
                <p className="mb-1 text-[10.5px] text-[var(--text-tertiary)]">{assistCopy.followUps}</p>
                <div className="flex flex-wrap gap-1">
                  {message.openQuestions.map((question) => (
                    <button
                      key={question}
                      type="button"
                      disabled={disabled}
                      onClick={() => onResend(question)}
                      className="rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-left text-[11px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] disabled:opacity-50"
                    >
                      {question}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <SpecialCard {...props} />
        )}
      </div>
    </li>
  );
}
