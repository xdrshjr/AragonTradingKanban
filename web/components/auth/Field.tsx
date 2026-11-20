import { cn } from '@/lib/cn';

/**
 * 强调色文字按钮要用内联 `color`：全局样式里的 `button { color: inherit }` 是非分层规则，
 * Tailwind 的 `text-[var(--accent)]` 在分层里，压不过它（链接 `<a>` 不受影响）。
 */
export const ACCENT_TEXT = { color: 'var(--accent)' } as const;

export interface FieldProps {
  id: string;
  label: string;
  /** 常驻的说明（规则提示）；与 `error` 并存，错误在下。 */
  hint?: React.ReactNode;
  error?: string | null;
  /** 追加在 label 同一行右侧（如“忘记密码”这类次要动作）。 */
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

/** `hint` 与 `error` 的元素 id 约定：给输入框的 `aria-describedby` 用。 */
export function fieldDescribedBy(id: string, flags: { hint?: boolean; error?: boolean }): string | undefined {
  const ids = [flags.hint ? `${id}-hint` : null, flags.error ? `${id}-error` : null].filter(Boolean);
  return ids.length ? ids.join(' ') : undefined;
}

/**
 * 表单字段外壳：**每个输入都有可见 `<label>`**（§24.3.15e）。
 * 错误文案住在 `aria-live="polite"` 区域里，读屏器在出现时播报；输入框用 `aria-describedby` 关联。
 */
export function Field({ id, label, hint, error, aside, children, className }: FieldProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[12px] font-medium text-[var(--text-secondary)]">
          {label}
        </label>
        {aside}
      </div>
      {children}
      {hint ? (
        <div id={`${id}-hint`} className="text-[11px] leading-relaxed text-[var(--text-tertiary)]">
          {hint}
        </div>
      ) : null}
      <div id={`${id}-error`} aria-live="polite" className="min-h-0 text-[11.5px] leading-snug text-[var(--danger)]">
        {error ?? null}
      </div>
    </div>
  );
}
