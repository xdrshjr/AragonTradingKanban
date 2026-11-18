'use client';

import { Activity, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react';

import { copy } from '@/lib/copy';
import type { AragonCapability } from '@/lib/types';

/**
 * ★v12（S1 / AC-112）：新增第四态 `loop_blocked`。
 *
 * 三种旧态回答的是"端点通不通"，而第四态回答的是"这个进程能不能 spawn 子进程"——
 * 后者即使端点完全正常也做不成任何一次生成（E-88/E-89），所以它不能并进 `failed`
 * （那会让用户去查一个没坏的钥匙）。
 */
export type EndpointState = 'verified' | 'unverified' | 'failed' | 'loop_blocked';

/**
 * 三态判据（**唯一**一处）：绿 = 已验证、灰 = 未验证（可以点一次自检）、红 = 凭证失败。
 *
 * ★v9（K6 / §21.3.7）：这里不查 `/agent/probe` —— 那会让每次打开页面都多一次请求，
 * 而"这个端点此刻通不通"只有准备动手的人关心。自检的唯一入口是抽屉里的按钮。
 */
export function endpointState(aragon: AragonCapability | null | undefined): EndpointState {
  if (!aragon) return 'unverified';
  // ★v12（S1）：排在最前。连子进程都起不来时，"端点通不通"这个问题没有意义。
  if (aragon.loopOk === false || aragon.degradedReason === 'event_loop_unsupported') return 'loop_blocked';
  if (aragon.verified) return 'verified';
  if (aragon.degradedReason === 'no_api_key' || aragon.keyPresent === false) return 'failed';
  return 'unverified';
}

export interface EndpointBadgeProps {
  aragon?: AragonCapability | null;
  onOpenKeyDrawer?: () => void;
}

/**
 * 端点徽标：`{provider}/{model} @ {host}` + 三态点 + 点击打开密钥抽屉。
 *
 * 为什么它比"预检全绿"值钱：`capabilities.aragon` 报的全是**静态**能力（装没装、版本对不对、
 * 有没有 key），而"这个端点此刻是通的吗"从来没有被测量过（Q-2）。点一次自检 8~10 秒、
 * 约 $0.0016，于是"通不通"第一次变成可读的事实。
 *
 * 它被 `RunStatusBadge` 用 `next/dynamic` 懒加载（首屏预算 AC-35）；静态那一帧由
 * `RunStatusBadge` 自己用同一份 props 渲染，所以没有布局跳动。
 */
export function EndpointBadge({ aragon, onOpenKeyDrawer }: EndpointBadgeProps) {
  const state = endpointState(aragon);
  const engineLabel = [aragon?.provider, aragon?.model].filter(Boolean).join('/') || 'engine';
  const host = aragon?.baseUrlHost ?? null;
  const title =
    state === 'verified'
      ? `${copy.engine.verified}${aragon?.verifiedAt ? ` · ${aragon.verifiedAt}` : ''}`
      : state === 'loop_blocked'
        ? `${copy.capabilities.reason.event_loop_unsupported}${aragon?.loopReason ? ` · ${aragon.loopReason}` : ''}`
        : state === 'failed'
          ? copy.engine.failed
          : copy.engine.unverified;

  return (
    <button
      type="button"
      data-testid="endpoint-badge"
      data-state={state}
      aria-label={copy.engine.probeButton}
      title={title}
      onClick={() => onOpenKeyDrawer?.()}
      className="flex h-7 items-center gap-1.5 rounded-full border border-[var(--border-subtle)] px-2 text-[10.5px] text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
    >
      <Activity className="size-3" aria-hidden="true" />
      <span className="max-w-[150px] truncate">
        {engineLabel}
        {host ? ` @ ${host}` : ''}
      </span>
      <span className="flex items-center gap-0.5">
        {state === 'verified' ? (
          <ShieldCheck className="size-3 text-[var(--pos)]" aria-hidden="true" />
        ) : state === 'failed' || state === 'loop_blocked' ? (
          <ShieldAlert className="size-3 text-[var(--warn)]" aria-hidden="true" />
        ) : (
          <ShieldQuestion className="size-3" aria-hidden="true" />
        )}
        <span>
          {state === 'loop_blocked' ? copy.engine.loopBlocked : copy.engine[state as 'verified' | 'unverified' | 'failed']}
        </span>
      </span>
    </button>
  );
}
