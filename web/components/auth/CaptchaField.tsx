'use client';

import { RefreshCw } from 'lucide-react';
import { forwardRef } from 'react';

import { copyAuthPages } from '@/lib/copy.authPages';
import type { CaptchaChallenge } from '@/lib/types';

import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Field, fieldDescribedBy } from './Field';

export interface CaptchaFieldProps {
  id: string;
  challenge: CaptchaChallenge | null;
  loading: boolean;
  /** 取图失败的错误码；非空时显示"重试"。 */
  failure: string | null;
  value: string;
  onChange: (value: string) => void;
  onRefresh: () => void;
  error?: string | null;
  /** 额外的一行提示（如“多次登录失败，请输入验证码”）。 */
  hint?: React.ReactNode;
}

/**
 * 图形验证码字段：图 + 输入 + “换一张”。
 *
 * * 图固定 `160×56`（服务端出 `@2x`），`width/height` 写死避免布局跳动；
 * * `alt` 说清是 5 位字母数字、不区分大小写（§24.3.6 无障碍）；
 * * 图本身只读（`alt` 才是它的可访问名称）；换图统一走旁边的“换一张”按钮，换图时 `aria-busy`；
 * * 输入框：`autocomplete=off`、不自动大写（服务端不区分大小写，别让手机键盘把输入变成别的样子）。
 */
export const CaptchaField = forwardRef<HTMLInputElement, CaptchaFieldProps>(function CaptchaField(
  { id, challenge, loading, failure, value, onChange, onRefresh, error, hint },
  ref,
) {
  const text = copyAuthPages.captcha;
  return (
    <Field
      id={id}
      label={text.label}
      hint={hint ?? text.hint}
      error={error}
    >
      <div className="flex items-stretch gap-2">
        <Input
          ref={ref}
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={text.placeholder}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          maxLength={8}
          invalid={Boolean(error)}
          aria-describedby={fieldDescribedBy(id, { hint: true, error: Boolean(error) })}
          className="min-w-0 flex-1 font-mono tracking-[0.18em]"
        />
        <div
          className="relative flex h-9 w-[88px] shrink-0 items-center justify-center overflow-hidden rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[#17181c] sm:w-[120px]"
          aria-busy={loading || undefined}
        >
          {challenge ? (
            // 验证码图是 data URI：无需 next/image（也不该走它的优化管线）。
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={challenge.image}
              alt={text.alt}
              width={challenge.width}
              height={challenge.height}
              className="size-full object-cover"
              draggable={false}
            />
          ) : failure ? (
            <button
              type="button"
              onClick={onRefresh}
              className="px-2 text-[11px] text-[var(--text-secondary)] underline-offset-2 hover:underline"
            >
              {text.loadFailed} · {text.retry}
            </button>
          ) : (
            <span className="text-[11px] text-[var(--text-tertiary)]">{text.refreshing}</span>
          )}
        </div>
        <Button
          variant="ghost"
          size="md"
          onClick={onRefresh}
          disabled={loading}
          aria-label={text.refresh}
          title={text.refresh}
          className="!px-2"
        >
          <RefreshCw className={loading ? 'size-3.5 animate-spin' : 'size-3.5'} aria-hidden="true" />
          <span className="sr-only">{text.refresh}</span>
        </Button>
      </div>
    </Field>
  );
});
