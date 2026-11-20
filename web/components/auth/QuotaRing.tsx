import { cn } from '@/lib/cn';
import { bucketView } from '@/lib/quota';
import type { QuotaBucket } from '@/lib/types';

export interface QuotaRingProps {
  bucket: QuotaBucket;
  size?: number;
  className?: string;
  /** 读屏器用的完整描述；缺省按数字自动生成。 */
  label?: string;
}

/**
 * 额度环：弧长 = 日额度**剩余**比例（满环 = 一次没用），中心是 `剩余/总数`。
 * 用 `role="img"` + `aria-label` 把同一信息给读屏器；颜色（accent / warn / danger）只是冗余。
 */
export function QuotaRing({ bucket, size = 64, className, label }: QuotaRingProps) {
  const view = bucketView(bucket);
  if (!view) return null;
  const stroke = 4;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const fraction = view.unlimited ? 1 : view.limit > 0 ? Math.max(0, Math.min(1, view.dailyLeft / view.limit)) : 0;
  const tone = view.exhausted ? 'var(--danger)' : view.low ? 'var(--warn)' : 'var(--accent)';
  const center = view.unlimited ? '∞' : `${view.dailyLeft}/${view.limit}`;
  const description =
    label ?? (view.unlimited ? '不限次' : `日额度剩余 ${view.dailyLeft}，共 ${view.limit}${view.bonus > 0 ? `，另有赠送 ${view.bonus}` : ''}`);

  return (
    <div
      role="img"
      aria-label={description}
      className={cn('relative inline-flex shrink-0 items-center justify-center', className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--bg-surface-3)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={tone}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${circumference * fraction} ${circumference}`}
          style={{ transition: 'stroke-dasharray var(--dur-panel) var(--ease-panel)' }}
        />
      </svg>
      <span aria-hidden="true" className="num absolute text-[12px] font-semibold text-[var(--text-primary)]">
        {center}
      </span>
    </div>
  );
}
