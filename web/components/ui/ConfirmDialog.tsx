'use client';

import { useEffect, useId, useRef, useState } from 'react';

import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';

import { Button, type ButtonVariant } from './Button';
import { Dialog, DialogContent } from './Dialog';
import { Input } from './Input';

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** 后果说明：陈述事实，说清会发生什么。 */
  description: React.ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** 危险动作用 `danger`。 */
  tone?: Extract<ButtonVariant, 'danger' | 'primary' | 'secondary'>;
  /**
   * GitHub 式 type-to-confirm：非空时，必须输入与它**完全相同**的文字，确认键才可用
   * （删除用户、降级管理员、重置管理员密码，§24.3.14）。后端另有 `confirm` 校验，这只是体验层。
   */
  requireText?: string;
  requireTextLabel?: string;
  requireTextHint?: string;
  loading?: boolean;
  /** 返回 Promise 时，会在其结束前保持 loading；resolve 后由调用方决定是否关闭。 */
  onConfirm: () => void | Promise<void>;
}

/**
 * 二次确认对话框（后台与账号页共用的原语）。
 *
 * a11y：焦点先落在"取消"（默认安全），需要 type-to-confirm 时落在输入框；Esc 关闭并还原焦点
 * （Radix 默认）；确认键在 loading 时禁用，防止双击重复提交。
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = copyAdmin.common.cancel,
  tone = 'danger',
  requireText,
  requireTextLabel,
  requireTextHint,
  loading = false,
  onConfirm,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  // 同步守卫：`busy` 是异步 state，连点两下时第二次点击读到的仍是旧值。
  const busyRef = useRef(false);
  const inputId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setTyped('');
      setBusy(false);
    }
  }, [open]);

  const matches = !requireText || typed === requireText;
  const pending = loading || busy;

  const run = async () => {
    if (!matches || pending || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (pending ? undefined : onOpenChange(next))}>
      <DialogContent
        title={title}
        description={typeof description === 'string' ? description : undefined}
        hideClose
        className="w-[min(92vw,440px)] p-5"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          (requireText ? inputRef.current : cancelRef.current)?.focus();
        }}
      >
        <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{title}</h2>
        <div className="mt-2 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{description}</div>

        {requireText ? (
          <div className="mt-4">
            <label htmlFor={inputId} className="mb-1.5 block text-[12px] text-[var(--text-secondary)]">
              {requireTextLabel ?? fill(copyAdmin.common.typeToConfirm, { name: requireText })}
            </label>
            <Input
              id={inputId}
              ref={inputRef}
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  void run();
                }
              }}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder={requireText}
              className="font-mono"
            />
            {requireTextHint ? (
              <p className="mt-1 text-[11px] text-[var(--text-tertiary)]">{requireTextHint}</p>
            ) : null}
          </div>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <Button ref={cancelRef} variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
            {cancelLabel}
          </Button>
          <Button variant={tone} onClick={() => void run()} loading={pending} disabled={!matches}>
            {confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
