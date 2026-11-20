import { Check, Circle } from 'lucide-react';

import { cn } from '@/lib/cn';
import { fill } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';
import type { PasswordEvaluation, PasswordPolicy } from '@/lib/passwordPolicy';

export interface PasswordStrengthProps {
  evaluation: PasswordEvaluation;
  policy: PasswordPolicy;
  id?: string;
}

const SEGMENT_TONE = [
  'bg-[var(--danger)]',
  'bg-[var(--danger)]',
  'bg-[var(--warn)]',
  'bg-[var(--accent)]',
  'bg-[var(--accent)]',
];

/**
 * 强度条 + 规则清单。
 *
 * 强度**不靠颜色单独表达**：条旁有文字等级；清单里每条规则是「勾 / 圆点 + 文字」，
 * 并用 `sr-only` 补一句"已满足 / 未满足"。只有文字等级放进 `aria-live`，避免每敲一个字符都播报整张清单。
 */
export function PasswordStrength({ evaluation, policy, id }: PasswordStrengthProps) {
  const text = copyAuthPages.password;
  // 还没输入时所有规则都是“待满足”：不能让“不得包含用户名”这类对空串天然成立的规则亮绿勾。
  const pristine = evaluation.score === -1;
  const level = evaluation.score >= 0 ? (text.levels[evaluation.score as 0 | 1 | 2 | 3 | 4] ?? null) : null;

  return (
    <div id={id} className="space-y-2" data-testid="password-strength">
      <div className="flex items-center gap-2">
        <div className="flex flex-1 gap-1" aria-hidden="true">
          {[0, 1, 2, 3, 4].map((index) => (
            <span
              key={index}
              className={cn(
                'h-1 flex-1 rounded-full bg-[var(--bg-surface-3)] transition-colors',
                evaluation.score >= index && SEGMENT_TONE[Math.max(0, evaluation.score)],
              )}
            />
          ))}
        </div>
        <span aria-live="polite" className="w-[4.5em] text-right text-[11px] text-[var(--text-tertiary)]">
          {level ? `${text.strength}：${level}` : ''}
        </span>
      </div>
      <ul className="space-y-1" aria-label={text.rulesTitle}>
        {evaluation.rules.map((rule) => (
          <li
            key={rule.id}
            data-rule={rule.id}
            data-ok={rule.ok && !pristine}
            className={cn(
              'flex items-center gap-1.5 text-[11px]',
              rule.ok && !pristine ? 'text-[var(--accent)]' : 'text-[var(--text-tertiary)]',
            )}
          >
            {rule.ok && !pristine ? (
              <Check className="size-3 shrink-0" aria-hidden="true" />
            ) : (
              <Circle className="size-3 shrink-0" aria-hidden="true" />
            )}
            <span>{fill(text.rules[rule.id] ?? text.unknownRule, { min: policy.min, max: policy.max })}</span>
            <span className="sr-only">{rule.ok && !pristine ? '，已满足' : '，未满足'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
