'use client';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';

/**
 * 单个数值的接地指示点（spec §19.3.2 / D2 / AC-49）。
 *
 * 为什么**永远渲染**一个 6px 的点，而不是只在未接地时出现：
 * 只在失败时出现会让"没有点"同时意味着"这个数字没问题"和"这件事还没检查过"，读者无法区分。
 * 恒定存在的点让每个数字都有同一处可读的状态，未接地时换色。
 *
 * 为什么 aria-label 是整句中文而不是"未接地"三个字：
 * 颜色**不能是唯一信号**（AC-36）。读屏用户拿到的必须是"28.4：未在快照中找到依据"这样
 * 自带数值与结论的句子，色觉障碍用户靠 `title` 拿到同一句。`role="img"` 让它成为一个
 * 有名字的图形，而不是一个被忽略的空 `<span>`。
 *
 * 纯展示组件：不读 store、不发请求、不做判定 —— `grounded` 由调用方（`metrics`/`table`
 * 的值单元格）算好传进来（O-7：不引入第二套渲染路径）。
 */

export interface GroundingDotProps {
  /** 这个点所标注的数字原文（如 `28.4`），会拼进可访问名。 */
  value: string;
  /** 是否已在快照中找到依据。`true` 时点变暗但仍存在。 */
  grounded: boolean;
  className?: string;
}

export function GroundingDot({ value, grounded, className }: GroundingDotProps) {
  const label = fill(grounded ? copy.trust.dotGrounded : copy.trust.dotMissing, { value });
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-grounded={grounded ? 'true' : 'false'}
      className={cn(
        // 6px（size-1.5）实体圆点，与数值基线对齐；不占布局高度，紧跟在值后面。
        'ml-1 inline-block size-1.5 shrink-0 rounded-full align-middle',
        grounded ? 'bg-[var(--text-tertiary)]/40' : 'bg-[var(--warn)]',
        className,
      )}
    />
  );
}
