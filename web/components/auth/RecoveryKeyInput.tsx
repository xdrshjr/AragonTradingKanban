'use client';

import { forwardRef } from 'react';

import { formatRecoveryKey } from '@/lib/recoveryKey';

import { Input, type InputProps } from '../ui/Input';

export interface RecoveryKeyInputProps extends Omit<InputProps, 'value' | 'onChange' | 'type'> {
  value: string;
  /** 回调拿到的永远是**已分组、已大写**的串（`K7QM4-XNP2R-…`）。 */
  onValueChange: (formatted: string) => void;
}

/**
 * 恢复码输入遮罩：自动大写、`O→0`/`I,L→1`、每 5 位插入连字符；粘贴整串（含空格/小写/带连字符）
 * 一次就被整理好。**只是展示层**——服务端 `normalize()` 才是规范化的真源（R-Y3）。
 */
export const RecoveryKeyInput = forwardRef<HTMLInputElement, RecoveryKeyInputProps>(function RecoveryKeyInput(
  { value, onValueChange, className, ...props },
  ref,
) {
  return (
    <Input
      ref={ref}
      {...props}
      value={value}
      onChange={(event) => onValueChange(formatRecoveryKey(event.target.value))}
      onPaste={(event) => {
        // 粘贴时直接整理，避免输入法/浏览器把空格、换行带进来。
        const text = event.clipboardData?.getData('text');
        if (!text) return;
        event.preventDefault();
        onValueChange(formatRecoveryKey(text));
      }}
      inputMode="text"
      autoComplete="off"
      autoCapitalize="characters"
      autoCorrect="off"
      spellCheck={false}
      maxLength={23}
      placeholder="XXXXX-XXXXX-XXXXX-XXXXX"
      className={`font-mono tracking-[0.08em] ${className ?? ''}`}
    />
  );
});
