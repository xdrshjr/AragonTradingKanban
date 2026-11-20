'use client';

import dynamic from 'next/dynamic';
import { Activity, CircleAlert } from 'lucide-react';

import { copy } from '@/lib/copy';
import { useUiStore } from '@/lib/store';
import type { AragonCapability } from '@/lib/types';

import { Badge } from '../ui/Badge';
import type { useGenerationStream } from '../generation/useGenerationStream';

// ★v9（K6）：端点徽标的**外壳**按需加载（首屏预算 AC-35，§11.2 的手段：next/dynamic）。
// 信息（`provider/model @ host` + 状态词）留在首屏，由下面的 fallback 用同一份 props 渲染，
// 所以第一帧与最终帧内容一致、没有布局跳动；只有三个盾牌图标晚一帧到。
const EndpointBadge = dynamic(() => import('./EndpointBadge').then((module) => module.EndpointBadge), {
  ssr: false,
  loading: () => <EndpointBadgeFallback />,
});

export interface RunStatusBadgeProps {
  generation: ReturnType<typeof useGenerationStream>;
  agentMode: 'real' | 'mock';
  degradedReason?: string | null;
  /** ★v9（K2）：端点徽标要读它（`provider/model @ host`）。 */
  aragon?: AragonCapability | null;
  /** ★v9（K6）：点徽标 = 打开密钥抽屉（自检的唯一入口在那里）。 */
  onOpenKeyDrawer?: () => void;
  /**
   * ★X6（§24.3.9(g)）：只有 `engine.probe`（管理员）才看得到端点徽标与密钥抽屉；
   * 其他人只看到“引擎已就绪 / 示例模式”一个状态点（`Capabilities` 对他们本就是脱敏的）。缺省 `true`。
   */
  canProbe?: boolean;
}

/** 静态那一帧：同一份信息、同样的高度，只是不带图标与状态机。 */
function EndpointBadgeFallback({ aragon }: { aragon?: AragonCapability | null }) {
  const engineLabel = [aragon?.provider, aragon?.model].filter(Boolean).join('/') || 'engine';
  const host = aragon?.baseUrlHost ?? null;
  return (
    <span
      data-testid="endpoint-badge-fallback"
      className="flex h-7 items-center gap-1.5 rounded-full border border-[var(--border-subtle)] px-2 text-[10.5px] text-[var(--text-tertiary)]"
    >
      <Activity className="size-3" aria-hidden="true" />
      <span className="max-w-[150px] truncate">
        {engineLabel}
        {host ? ` @ ${host}` : ''}
      </span>
      <span>{aragon?.verified ? copy.engine.verified : copy.engine.unverified}</span>
    </span>
  );
}

/**
 * Header capsule: a pulsing dot + the current stage while a run is live, and an
 * honest "示例模式" badge when the real engine is unavailable (§10.3, AC-15).
 *
 * ★v9（K6 / §21.3.7）新增**端点徽标**：`capabilities.aragon` 报的全是静态能力，
 * 而"这个端点此刻是通的吗"（Q-2）从来没有被测量过 —— 徽标就是那一项的可见性。
 */
export function RunStatusBadge({
  generation,
  agentMode,
  degradedReason,
  aragon,
  onOpenKeyDrawer,
  canProbe = true,
}: RunStatusBadgeProps) {
  const { state, isActive } = generation;
  const setOverlayCollapsed = useUiStore((store) => store.setOverlayCollapsed);

  if (isActive) {
    return (
      <button
        type="button"
        onClick={() => setOverlayCollapsed(false)}
        className="flex h-7 items-center gap-1.5 rounded-full border border-[var(--accent)]/35 bg-[var(--accent-weak)] px-2.5 text-[11.5px] text-[var(--accent)]"
        aria-live="polite"
      >
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--accent)] opacity-70" />
          <span className="relative inline-flex size-1.5 rounded-full bg-[var(--accent)]" />
        </span>
        <span className="max-w-[160px] truncate">{state.stageLabel ?? copy.generation.title}</span>
        <span className="num text-[10px] opacity-80">{state.pct}%</span>
      </button>
    );
  }

  if (agentMode === 'mock') {
    const reason = degradedReason ? copy.capabilities.reason[degradedReason] ?? degradedReason : null;
    return (
      <Badge tone="warn" icon={<CircleAlert className="size-3" />} title={reason ?? copy.capabilities.mockBody}>
        {copy.capabilities.degraded}
        {reason ? ` · ${reason}` : ''}
      </Badge>
    );
  }

  if (!canProbe) {
    return (
      <Badge tone="accent" icon={<span aria-hidden="true" className="size-1.5 rounded-full bg-[var(--accent)]" />}>
        {copy.auth.engineReady}
      </Badge>
    );
  }

  return <EndpointBadge aragon={aragon} onOpenKeyDrawer={onOpenKeyDrawer} />;
}
