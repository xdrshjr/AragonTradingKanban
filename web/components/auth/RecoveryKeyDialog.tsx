'use client';

import { Check, Copy, Download, KeyRound } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

import { copyAuthPages } from '@/lib/copy.authPages';
import { recoveryKeyFile } from '@/lib/recoveryKey';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';

export interface RecoveryKeyDialogProps {
  open: boolean;
  /** 已分组的明文恢复码。只存在于这个弹窗与用户自己的剪贴板 / 下载文件。 */
  recoveryKey: string;
  username: string;
  /** `rotated`：账号页轮换或找回成功后的"新码"（标题不同）。 */
  variant?: 'first' | 'rotated';
  /** 勾选"我已保存"后点击"继续"。 */
  onContinue: () => void;
  /** "跳过（不推荐）"；缺省则不提供。 */
  onSkip?: () => void;
}

/**
 * 恢复码展示弹窗（spec §25.3.3 前端）。**防误触丢码**：
 *
 * * `Esc` 与点击遮罩都**不**关闭（`preventDefault`），也没有右上角的关闭键；
 * * "继续"在勾选"我已保存"之前 `disabled`，并用 `aria-describedby` 指向原因；
 * * 提供复制与下载 `.txt`；"跳过（不推荐）"是明确的次级文字按钮，由调用方记下"未保存"以便账号页提醒。
 */
export function RecoveryKeyDialog({ open, recoveryKey, username, variant = 'first', onContinue, onSkip }: RecoveryKeyDialogProps) {
  const t = copyAuthPages.recoveryKey;
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState<'idle' | 'ok' | 'fail'>('idle');
  const [downloaded, setDownloaded] = useState(false);
  const reasonId = useId();
  const checkRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setSaved(false);
      setCopied('idle');
      setDownloaded(false);
    }
  }, [open, recoveryKey]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(recoveryKey);
      setCopied('ok');
    } catch {
      setCopied('fail');
    }
  };

  const download = () => {
    const file = recoveryKeyFile(username, recoveryKey, t.fileHeader, t.fileBody);
    const url = URL.createObjectURL(new Blob([file.text], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = file.name;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    setTimeout(() => URL.revokeObjectURL(url), 0);
    setDownloaded(true);
  };

  const title = variant === 'rotated' ? t.titleRotated : t.title;

  return (
    <Dialog open={open}>
      <DialogContent
        title={title}
        description={t.body}
        hideClose
        className="w-[min(94vw,460px)] max-h-[92dvh] overflow-y-auto p-6"
        data-testid="recovery-key-dialog"
        onEscapeKeyDown={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          checkRef.current?.focus();
        }}
      >
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent-weak)] text-[var(--accent)]">
            <KeyRound className="size-4" aria-hidden="true" />
          </span>
          <h2 aria-hidden="true" className="text-[16px] font-semibold tracking-tight text-[var(--text-primary)]">
            {title}
          </h2>
        </div>
        <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{t.body}</p>

        <div className="mt-4 rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] px-3 py-3.5">
          <div className="mb-1.5 text-[11px] text-[var(--text-tertiary)]">{t.label}</div>
          <code
            data-testid="recovery-key-value"
            aria-label={`${t.label} ${recoveryKey.replace(/-/g, ' ')}`}
            className="mono block select-all break-all text-center text-[15px] font-semibold tracking-[0.06em] text-[var(--text-primary)] min-[420px]:text-[18px] min-[420px]:tracking-[0.1em]"
          >
            {recoveryKey}
          </code>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void copy()}
            leadingIcon={copied === 'ok' ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
          >
            {copied === 'ok' ? t.copied : t.copy}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={download}
            leadingIcon={downloaded ? <Check className="size-3.5" aria-hidden="true" /> : <Download className="size-3.5" aria-hidden="true" />}
          >
            {downloaded ? t.downloaded : t.download}
          </Button>
          <span role="status" className="text-[11.5px] text-[var(--danger)]">
            {copied === 'fail' ? t.copyFailed : null}
          </span>
        </div>

        <label htmlFor={`${reasonId}-saved`} className="mt-4 flex items-start gap-2 text-[12.5px] text-[var(--text-primary)]">
          <input
            ref={checkRef}
            id={`${reasonId}-saved`}
            type="checkbox"
            checked={saved}
            onChange={(event) => setSaved(event.target.checked)}
            className="mt-0.5 size-3.5 shrink-0 accent-[var(--accent)]"
          />
          <span>{t.saved}</span>
        </label>

        <div className="mt-4 flex items-center justify-between gap-3">
          {onSkip ? (
            <div className="min-w-0">
              <button
                type="button"
                onClick={onSkip}
                className="text-[12px] text-[var(--text-tertiary)] underline underline-offset-2 hover:text-[var(--text-secondary)]"
              >
                {t.skip}
              </button>
            </div>
          ) : (
            <span />
          )}
          <Button
            variant="primary"
            disabled={!saved}
            aria-describedby={saved ? undefined : `${reasonId}-reason`}
            onClick={onContinue}
          >
            {t.continue}
          </Button>
        </div>
        <p id={`${reasonId}-reason`} className="mt-2 min-h-[1em] text-[11px] text-[var(--text-tertiary)]">
          {saved ? (onSkip ? t.skipHint : null) : t.needSaved}
        </p>
      </DialogContent>
    </Dialog>
  );
}
