'use client';

import { Eye, EyeOff } from 'lucide-react';
import { forwardRef, useState } from 'react';

import { copyAuthPages } from '@/lib/copy.authPages';

import { Input, type InputProps } from '../ui/Input';

export interface PasswordFieldProps extends Omit<InputProps, 'type' | 'trailingSlot'> {
  /** `current-password`（登录/改密的旧密码）或 `new-password`（注册/新密码）——密码管理器据此工作。 */
  autoComplete: 'current-password' | 'new-password';
}

/**
 * 密码输入：显示切换 + CapsLock 提示。
 *
 * 显示切换按钮 `aria-pressed` 且有可读名称；CapsLock 提示是 `role="status"`，
 * 只在**聚焦且开启**时出现（失焦即隐藏，避免残留的错误印象）。
 */
export const PasswordField = forwardRef<HTMLInputElement, PasswordFieldProps>(function PasswordField(
  { autoComplete, onKeyDown, onKeyUp, onBlur, ...props },
  ref,
) {
  const [visible, setVisible] = useState(false);
  const [caps, setCaps] = useState(false);
  const text = copyAuthPages.password;

  const detect = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (typeof event.getModifierState === 'function') setCaps(event.getModifierState('CapsLock'));
  };

  return (
    <div>
      <Input
        ref={ref}
        {...props}
        type={visible ? 'text' : 'password'}
        autoComplete={autoComplete}
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        onKeyDown={(event) => {
          detect(event);
          onKeyDown?.(event);
        }}
        onKeyUp={(event) => {
          detect(event);
          onKeyUp?.(event);
        }}
        onBlur={(event) => {
          setCaps(false);
          onBlur?.(event);
        }}
        trailingSlot={
          <button
            type="button"
            onClick={() => setVisible((value) => !value)}
            aria-pressed={visible}
            aria-label={visible ? text.hide : text.show}
            title={visible ? text.hide : text.show}
            className="shrink-0 rounded p-0.5 text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
          >
            {visible ? <EyeOff className="size-3.5" aria-hidden="true" /> : <Eye className="size-3.5" aria-hidden="true" />}
          </button>
        }
      />
      <div role="status" className="min-h-0 text-[11px] text-[var(--warn)]">
        {caps ? text.capsLock : null}
      </div>
    </div>
  );
});
