'use client';

import { useId, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

/** 检查器里统一的字段外壳：标签 + 控件 + 提示（紧凑密度，PyCharm 属性面板风格）。 */
export function Field({
  label,
  hint,
  counter,
  children,
  htmlFor,
}: {
  label: string;
  hint?: ReactNode;
  counter?: ReactNode;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[11.5px] font-medium text-[var(--text-secondary)]">
          {label}
        </label>
        {counter ? <span className="font-mono text-[10.5px] text-[var(--text-tertiary)]">{counter}</span> : null}
      </div>
      {children}
      {hint ? <div className="text-[11px] leading-snug text-[var(--text-tertiary)]">{hint}</div> : null}
    </div>
  );
}

export function Section({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-b border-[var(--border-subtle)] px-3 py-3 last:border-b-0">
      {title ? (
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{title}</h3>
      ) : null}
      {children}
    </section>
  );
}

export interface CheckOption {
  value: string;
  label: string;
  hint?: string;
  /** 额外状态点（如"章节覆盖"）：文字 + 点，不只靠颜色。 */
  status?: { ok: boolean; text: string };
}

export function CheckGroup({
  legend,
  options,
  value,
  disabled,
  onChange,
}: {
  legend: string;
  options: readonly CheckOption[];
  value: readonly string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
}) {
  const baseId = useId();
  const toggle = (option: string, checked: boolean): void => {
    const next = checked ? [...value, option] : value.filter((item) => item !== option);
    onChange(options.map((entry) => entry.value).filter((item) => next.includes(item)));
  };
  return (
    <fieldset className="flex flex-col gap-1" disabled={disabled}>
      <legend className="sr-only">{legend}</legend>
      {options.map((option, index) => {
        const id = `${baseId}-${index}`;
        return (
          <label
            key={option.value}
            htmlFor={id}
            className={cn(
              'flex cursor-pointer items-start gap-2 rounded-[var(--radius-sm)] px-1.5 py-1 text-[12px] hover:bg-[var(--bg-hover)]',
              disabled && 'cursor-not-allowed opacity-60',
            )}
          >
            <input
              id={id}
              type="checkbox"
              checked={value.includes(option.value)}
              onChange={(event) => toggle(option.value, event.target.checked)}
              className="mt-0.5 size-3.5 accent-[var(--accent)]"
            />
            <span className="min-w-0 flex-1">
              <span className="text-[var(--text-primary)]">{option.label}</span>
              {option.hint ? <span className="block text-[10.5px] text-[var(--text-tertiary)]">{option.hint}</span> : null}
            </span>
            {option.status ? (
              <span
                className="shrink-0 text-[10.5px]"
                style={{ color: option.status.ok ? 'var(--accent)' : 'var(--warn)' }}
              >
                {option.status.ok ? '●' : '○'} {option.status.text}
              </span>
            ) : null}
          </label>
        );
      })}
    </fieldset>
  );
}

export function SegmentedChoice<T extends string>({
  legend,
  options,
  value,
  disabled,
  onChange,
}: {
  legend: string;
  options: readonly { value: T; label: string }[];
  value: T;
  disabled?: boolean;
  onChange: (next: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={legend} className="inline-flex overflow-hidden rounded-[var(--radius-sm)] border border-[var(--border-strong)]">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            'h-7 px-3 text-[12px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]',
            option.value === value
              ? 'bg-[var(--bg-active)] font-medium text-[var(--text-primary)]'
              : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
