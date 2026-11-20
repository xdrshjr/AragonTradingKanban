import { Crown, ShieldCheck } from 'lucide-react';

import { copy } from '@/lib/copy';
import type { Role } from '@/lib/types';

import { Badge } from '../ui/Badge';

export interface RoleBadgeProps {
  role: Role | 'guest' | string | null | undefined;
  className?: string;
}

/**
 * 角色徽标（§24.3.15e）：root＝accent 实心 + 皇冠；admin＝info；user＝中性；guest 不显示。
 * 图标 + 文字并用，所以不靠颜色单独区分。
 */
export function RoleBadge({ role, className }: RoleBadgeProps) {
  if (!role || role === 'guest') return null;
  const label = (copy.auth.roles as Record<string, string>)[role] ?? role;
  if (role === 'root') {
    return (
      <span
        className={`inline-flex items-center gap-1 rounded-full bg-[var(--accent)] px-1.5 py-px text-[10.5px] font-semibold leading-[16px] text-[var(--accent-text)] ${className ?? ''}`}
      >
        <Crown className="size-3" aria-hidden="true" />
        {label}
      </span>
    );
  }
  if (role === 'admin') {
    return (
      <Badge tone="info" className={className} icon={<ShieldCheck className="size-3" aria-hidden="true" />}>
        {label}
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" className={className}>
      {label}
    </Badge>
  );
}
