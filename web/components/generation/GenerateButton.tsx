'use client';

import { Sparkles } from 'lucide-react';

import { cn } from '@/lib/cn';
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
}

/**
 * The primary call to action (spec §6.3). While a run is in flight it is
 * `disabled` + `aria-busy` so a double click cannot queue a second run (§7.1).
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
}: GenerateButtonProps) {
  return (
    <Button
      variant={variant}
      size={size}
      loading={generating}
      disabled={disabled || generating}
      onClick={onGenerate}
      block={block}
      className={cn(className)}
      title={costHint ?? undefined}
      aria-busy={generating || undefined}
    >
      {!generating ? <Sparkles className="size-3.5" aria-hidden="true" /> : null}
      {children ?? '生成投研报告'}
    </Button>
  );
}
