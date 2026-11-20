'use client';

import { LogIn, Sparkles } from 'lucide-react';
import { useId } from 'react';

import { isGuest, useAuthUi, useMe } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { bucketView, formatReset } from '@/lib/quota';

import { QuotaChip } from '../auth/QuotaChip';
import { Button, type ButtonSize } from '../ui/Button';

export interface GenerateButtonProps {
  onGenerate: () => void;
  generating?: boolean;
  disabled?: boolean;
  size?: ButtonSize;
  variant?: 'primary' | 'secondary';
  children?: React.ReactNode;
  block?: boolean;
  className?: string;
  costHint?: string | null;
  /** 游客点击后弹登录框，登录成功回到该标的（`AuthIntent.symbol`）。 */
  symbol?: string;
  /** 在按钮旁显示额度芯片 / 游客提示（空态的大按钮用；头部已有全局芯片，小按钮不重复）。 */
  showQuota?: boolean;
}

/**
 * The primary call to action (spec §6.3). While a run is in flight it is
 * `disabled` + `aria-busy` so a double click cannot queue a second run (§7.1).
 *
 * ★X6（spec §24.3.15c）：**三态**由 `useMe()` 决定——文案与可点击性是产品，不是装饰：
 *
 * | 主体 | 按钮 | 点击 |
 * | --- | --- | --- |
 * | guest | `登录后生成投研报告`（主色保留，`LogIn` 图标） | 打开登录弹窗（不发任何 `POST /reports`）；成功后回到原位并聚焦本按钮，**不自动生成** |
 * | user，剩余 > 0 | 原文案 | 照旧；`showQuota` 时旁边有额度芯片 |
 * | user，剩余 = 0 | `aria-disabled`（**仍可聚焦**，描述说明原因） | 打开额度对话框，而不是静默无反应 |
 * | admin / root / 开放模式 | 原文案 | 照旧 |
 *
 * 用 `aria-disabled` 而不是 `disabled`：原生 disabled 会让按钮退出 Tab 顺序，键盘与读屏用户就
 * 再也找不到"为什么不能生成"的解释（焦点说明 + 点击出对话框）。
 */
export function GenerateButton({
  onGenerate,
  generating = false,
  disabled = false,
  size = 'md',
  variant = 'primary',
  children,
  block,
  className,
  costHint,
  symbol,
  showQuota = false,
}: GenerateButtonProps) {
  const me = useMe();
  const openAuth = useAuthUi((state) => state.openAuth);
  const openQuota = useAuthUi((state) => state.openQuota);
  const id = useId();
  const describedBy = `${id}-desc`;

  const guest = isGuest(me);
  const bucket = guest ? null : me.quota?.report;
  const view = bucketView(bucket);
  const exhausted = Boolean(view?.exhausted) && !guest;

  if (guest) {
    const label = size === 'sm' ? copy.auth.generateGuestShort : copy.auth.generateGuest;
    return (
      <span className={cn('inline-flex flex-col items-center gap-1.5', block && 'w-full')}>
        <Button
          id={id}
          variant={variant}
          size={size}
          disabled={disabled}
          block={block}
          className={className}
          title={copy.auth.generateGuestHint}
          aria-describedby={describedBy}
          data-generate-state="guest"
          leadingIcon={<LogIn className="size-3.5" aria-hidden="true" />}
          onClick={() => openAuth('login', { type: 'generate', symbol, focusId: id })}
        >
          {label}
        </Button>
        <span
          id={describedBy}
          className={cn(showQuota ? 'text-[11px] text-[var(--text-tertiary)]' : 'sr-only')}
        >
          {copy.auth.guestBrowse}
        </span>
      </span>
    );
  }

  if (exhausted && view && bucket) {
    const reason = fill(copy.quota.exhaustedHint, {
      used: view.usedToday,
      limit: view.limit,
      reset: formatReset(bucket.resetAt, me.quota?.tz),
    });
    return (
      <span className={cn('inline-flex items-center gap-2', block && 'w-full')}>
        <Button
          id={id}
          variant={variant}
          size={size}
          block={block}
          aria-disabled="true"
          aria-describedby={describedBy}
          title={reason}
          data-generate-state="exhausted"
          className={cn('cursor-not-allowed opacity-45', className)}
          leadingIcon={<Sparkles className="size-3.5" aria-hidden="true" />}
          onClick={() => openQuota(null)}
        >
          {copy.quota.exhausted}
        </Button>
        <span id={describedBy} className="sr-only">
          {reason}
        </span>
        {showQuota ? <QuotaChip /> : null}
      </span>
    );
  }

  const button = (
    <Button
      id={id}
      variant={variant}
      size={size}
      loading={generating}
      disabled={disabled || generating}
      onClick={onGenerate}
      block={block}
      className={cn(className)}
      title={costHint ?? undefined}
      aria-busy={generating || undefined}
      data-generate-state="ready"
    >
      {!generating ? <Sparkles className="size-3.5" aria-hidden="true" /> : null}
      {children ?? '生成投研报告'}
    </Button>
  );
  if (!showQuota || !view || view.unlimited) return button;
  return (
    <span className={cn('inline-flex items-center gap-2', block && 'w-full')}>
      {button}
      <QuotaChip />
    </span>
  );
}
