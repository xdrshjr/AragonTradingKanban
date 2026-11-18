'use client';

import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Loader2, RotateCw, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy, costSourceLabel, fill } from '@/lib/copy';
import { durationLabel } from '@/lib/format';
import { ACTION_LABELS, WAIT_RETRY_SECONDS, actionFor, primaryActionFor } from '@/lib/runErrors';
import { useUiStore } from '@/lib/store';
import type { RunStageId } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Button, IconButton } from '../ui/Button';
import type { GenerationState } from './useGenerationStream';

const STAGES: { id: RunStageId; pct: number }[] = [
  { id: 'resolve', pct: 5 },
  { id: 'snapshot', pct: 15 },
  { id: 'prepare', pct: 20 },
  { id: 'research', pct: 70 },
  { id: 'validate', pct: 85 },
  { id: 'persist', pct: 100 },
];

/**
 * 「这不是偶发失败」的阈值。与 `api/app/services/run_diagnostics.REPEAT_ALERT_THRESHOLD`
 * 同一个数、同一个含义：服务端算次数，前端只比较（措辞不同，判据唯一）。
 */
const REPEAT_ALERT_THRESHOLD = 3;

export interface GenerationOverlayProps {
  state: GenerationState;
  onCancel: () => void;
  onDismiss: () => void;
  onRetry: () => void;
  agentMode: 'real' | 'mock';
  onOpenReport?: (reportId: string) => void;
  /** ★v9（K6）：`checkKey` / `openSettings` 打开密钥抽屉（说明 + 自检按钮）。 */
  onOpenKeyDrawer?: () => void;
  /** ★v9（K6）：`openLog` 展开日志区。 */
  onOpenLog?: () => void;
}

/**
 * Non-modal progress card, fixed bottom-right (spec §5.5 "生成覆盖层为非模态侧边
 * 卡片"). It must never block reading the previous version — that is the whole
 * point of "生成中不阻塞阅读".
 */
export function GenerationOverlay({
  state,
  onCancel,
  onDismiss,
  onRetry,
  agentMode,
  onOpenReport,
  onOpenKeyDrawer,
  onOpenLog,
}: GenerationOverlayProps) {
  // Collapse lives in the store so the header capsule can expand the card
  // (spec §6.3: clicking the running badge opens the progress overlay).
  const collapsed = useUiStore((state) => state.overlayCollapsed);
  const setCollapsed = useUiStore((state) => state.setOverlayCollapsed);
  // ★v12（S6 / §22.3.7(a) / AC-123）：`wait` 的倒计时状态。它必须是 **state** 而不是
  // 一次性 `setTimeout` 的副作用——用户要看见"还有 N 秒"，否则"点了没反应"只是从
  // "没有动作"变成"看不出有没有动作"（S-9：界面上每个按钮都要真的做事）。
  const [waitSecondsLeft, setWaitSecondsLeft] = useState<number | null>(null);
  const waitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const waitTickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const visible = state.phase !== 'idle';

  // 卸载即清：卡片可以随时被关掉，留下一个会去重试的定时器是最糟的残留。
  // 这个 effect 必须在 early-return **之前**（hooks 顺序不能依赖 visible）。
  useEffect(
    () => () => {
      if (waitTimerRef.current) clearTimeout(waitTimerRef.current);
      if (waitTickRef.current) clearInterval(waitTickRef.current);
    },
    [],
  );

  if (!visible) return null;

  /**
   * ★v9（K6）：六个动作 → 三次回调。表在 `lib/runErrors.ts`，这里只做分发。
   *
   * ★v12 修订（V11-P1-6）：旧注释写着"`wait` / `none` 不出现按钮（`primaryActionFor()`
   * 返回 null），所以这里永远拿不到它们"——**前半句是错的**。`primaryActionFor` 只对
   * `primary:false` 与 `action === 'none'` 返回 null，而 `too_many_runs`（429）是
   * `{action:'wait', primary:true}` ⇒ `'wait'` **确实会**走进这里并落空。那正是 E-99
   * 那条"429 主按钮点了什么都不发生"的真实成因，也是本轮 S6 的「先红」判据。
   */
  const runAction = (action: string) => {
    if (action === 'retry') {
      onRetry();
      return;
    }
    if (action === 'wait') {
      // 不是"什么都不做"：8 秒后自动重试一次，并在按钮上显示倒计时。
      // 不做**静默**重试——那等于把一次可解释的拦截变成一次神秘的重复请求。
      if (waitSecondsLeft !== null) return;
      setWaitSecondsLeft(WAIT_RETRY_SECONDS);
      waitTickRef.current = setInterval(() => {
        setWaitSecondsLeft((left) => (left === null ? null : left <= 1 ? 0 : left - 1));
      }, 1000);
      waitTimerRef.current = setTimeout(() => {
        if (waitTickRef.current) clearInterval(waitTickRef.current);
        waitTickRef.current = null;
        waitTimerRef.current = null;
        setWaitSecondsLeft(null);
        onRetry();
      }, WAIT_RETRY_SECONDS * 1000);
      return;
    }
    if (action === 'checkKey' || action === 'openSettings') {
      onOpenKeyDrawer?.();
      return;
    }
    if (action === 'openLog') {
      onOpenLog?.();
    }
  };

  const running = state.phase === 'submitting' || state.phase === 'running';
  const done = state.phase === 'done';
  const failed = state.phase === 'error';
  const cancelled = state.phase === 'cancelled';
  const activeIndex = Math.max(
    0,
    STAGES.findIndex((stage) => stage.id === state.stage),
  );
  const elapsed = state.startedAt ? (state.finishedAt ?? Date.now()) - state.startedAt : 0;
  // ★v9（K6）：主按钮由码表决定（一张卡片上只出现一个）。
  const primaryAction = primaryActionFor(state.error?.code);
  /**
   * ★评审修订（I-103）：日志按钮的显隐判据是"它是不是**已经**作为主按钮出现"，
   * 而不是"这个码的动作是不是 openLog"。
   *
   * 旧写法 `action === 'openLog' ? null : <查看日志>` 有两个洞：
   * ① `schema_over_budget` / `prompt_over_budget`（动作 = openLog，但 `primary: false`）
   *    ⇒ 主按钮不出现、日志按钮被隐藏 ⇒ 卡片上一个动作都没有；
   * ② 反过来，`cancelled` / `instrument_not_found`（动作 = `none`）会多出一个毫无意义的
   *    「查看日志」—— 用户主动取消的那一次不需要动作。
   */
  const errorAction = actionFor(state.error?.code);
  const showLogAction = primaryAction !== 'openLog' && errorAction.action !== 'none';

  return (
    <section
      aria-label={copy.generation.title}
      aria-live="polite"
      data-print="hide"
      className={cn(
        'fixed bottom-4 right-4 z-40 w-[340px] overflow-hidden rounded-[var(--radius-lg)]',
        'border border-[var(--border-strong)] bg-[var(--bg-surface-2)] shadow-[var(--shadow-pop)]',
      )}
    >
      <header className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          {running ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-[var(--accent)]" aria-hidden="true" />
          ) : done ? (
            <CheckCircle2 className="size-3.5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
          ) : (
            <AlertCircle className="size-3.5 shrink-0 text-[var(--warn)]" aria-hidden="true" />
          )}
          <h2 className="truncate text-[12.5px] font-semibold text-[var(--text-primary)]">
            {running
              ? copy.generation.title
              : done
                ? copy.generation.doneTitle
                : cancelled
                  ? copy.generation.aborted
                  : copy.generation.failedTitle}
          </h2>
        </span>
        {/* ★v7（§20.3.4）：重试可见。一个转圈能解释"在等"，解释不了"为什么等"。 */}
        {state.retryCount > 0 ? (
          <Badge tone="warn" title={copy.generation.retrying}>
            <RotateCw className="mr-1 inline size-2.5" aria-hidden="true" />
            {state.retryCount}
          </Badge>
        ) : null}
        {running ? <Badge tone="accent">{(state.pct ?? 0).toString()}%</Badge> : null}
        <IconButton label={collapsed ? '展开' : '收起'} onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        </IconButton>
        {!running ? (
          <IconButton label={copy.generation.close} onClick={onDismiss}>
            <X className="size-3.5" />
          </IconButton>
        ) : null}
      </header>

      <div className="h-[3px] w-full bg-[var(--bg-surface-3)]" aria-hidden="true">
        <div
          className={cn(
            'h-full transition-[width] duration-500 ease-out',
            failed ? 'bg-[var(--warn)]' : 'bg-[var(--accent)]',
          )}
          style={{ width: `${Math.max(2, Math.min(100, state.pct))}%` }}
        />
      </div>

      {collapsed ? null : (
        <>
          <ol className="grid grid-cols-3 gap-px bg-[var(--border-subtle)]">
            {STAGES.map((stage, index) => {
              const isActive = running && index === activeIndex;
              const isDone = state.pct >= stage.pct || done;
              return (
                <li
                  key={stage.id}
                  className={cn(
                    'flex items-center gap-1 bg-[var(--bg-surface-1)] px-2 py-1.5',
                    isActive && 'bg-[var(--accent-weak)]',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      'size-1.5 shrink-0 rounded-full',
                      isDone ? 'bg-[var(--accent)]' : isActive ? 'animate-pulse bg-[var(--accent)]' : 'bg-[var(--border-strong)]',
                    )}
                  />
                  <span
                    className={cn(
                      'truncate text-[10.5px] leading-tight',
                      isActive ? 'font-medium text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]',
                    )}
                  >
                    {copy.generation.stages[stage.id]}
                  </span>
                </li>
              );
            })}
          </ol>

          <div className="max-h-[168px] overflow-y-auto px-3 py-2">
            {state.streamText ? (
              <p className="mb-1.5 line-clamp-3 border-l-2 border-[var(--accent)]/40 pl-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
                {state.streamText.slice(-240)}
              </p>
            ) : null}
            {state.logs.length === 0 ? (
              <p className="text-[11px] text-[var(--text-tertiary)]">{copy.generation.emptyLogs}</p>
            ) : (
              <ul className="space-y-0.5">
                {state.logs.slice(-14).map((log) => (
                  <li
                    key={log.id}
                    className={cn(
                      'mono text-[10.5px] leading-snug',
                      log.level === 'warn'
                        ? 'text-[var(--warn)]'
                        : log.level === 'error'
                          ? 'text-[var(--danger)]'
                          : 'text-[var(--text-tertiary)]',
                    )}
                  >
                    {log.message}
                  </li>
                ))}
              </ul>
            )}
            {state.tools.length > 0 ? (
              <p className="mt-1.5 text-[10px] text-[var(--text-tertiary)]">
                {copy.generation.toolCalls} · {state.tools.length} 次
              </p>
            ) : null}
          </div>

          <footer className="flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] px-3 py-2">
            <span className="truncate text-[10px] text-[var(--text-tertiary)]">
              {agentMode === 'mock' ? copy.generation.mockNotice : copy.generation.realNotice}
              {running && elapsed > 0 ? ` · ${durationLabel(elapsed)}` : ''}
            </span>
            {running ? (
              <Button size="sm" variant="ghost" onClick={onCancel}>
                {copy.generation.cancel}
              </Button>
            ) : failed ? (
              <Button size="sm" variant="secondary" onClick={onRetry}>
                {copy.generation.retry}
              </Button>
            ) : done && state.reportId && onOpenReport ? (
              <Button size="sm" variant="accent-ghost" onClick={() => onOpenReport(state.reportId!)}>
                查看报告
              </Button>
            ) : null}
          </footer>

          {/*
            ★v7（H4 / R-31）：成本三态**分措辞**。把估算写成"已确认成本"会让账本上的
            每个数都失去意义，所以这里永远先看 `costSource`，再看金额。
          */}
          {done || failed ? (
            <p
              data-testid="generation-cost"
              className="border-t border-[var(--border-subtle)] px-3 py-1.5 text-[10.5px] leading-snug text-[var(--text-secondary)]"
            >
              <span className="font-medium text-[var(--text-primary)]">
                {costSourceLabel(state.costSource)}
              </span>
              {state.costUsd !== null
                ? ` $${state.costUsd.toFixed(2)}`
                : state.costUsdEst !== null
                  ? ` $${state.costUsdEst.toFixed(2)}`
                  : ''}
              {/* 估算时并把"含估算"说清：一个数不够，两个数才是诚实的。 */}
              {state.costSource === 'estimated' && state.costUsdEst !== null
                ? ` · ${fill(copy.cost.includingEstimated, { usd: state.costUsdEst.toFixed(2) })}`
                : ''}
            </p>
          ) : null}

          {state.phase === 'error' && state.error ? (
            <div className="border-t border-[var(--border-subtle)] bg-[var(--warn-weak)] px-3 py-1.5">
              <p className="text-[10.5px] leading-snug text-[var(--warn)]">
                {/* ★v8 评审：`usage_error` 有三个来源（参数 / 配置 / schema 精化后的误判），
                    所以除了码本身还要一句人话标签——否则用户看到的是 `usage_error` 四个字。 */}
                {state.error.code === 'usage_error' ? `${copy.errors.usageRejected} · ` : ''}
                {state.error.code} · {state.error.message}
              </p>
              {/* ★v9（K6 / §21.3.7）：从"打印一个码"变成"给一个动作"。
                  动作表在 `lib/runErrors.ts`（与 `contracts/run_error_codes.json` 双向
                  约束），文案在 `copy.errors` —— 组件不拼句子（H-12 的同一条纪律）。 */}
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {primaryAction ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    data-testid="error-primary-action"
                    data-action={primaryAction}
                    data-waiting={primaryAction === 'wait' && waitSecondsLeft !== null ? 'true' : undefined}
                    // ★v12（AC-123）：倒计时期间禁用，避免连点排队出三次 429。
                    disabled={primaryAction === 'wait' && waitSecondsLeft !== null}
                    onClick={() => runAction(primaryAction)}
                  >
                    {primaryAction === 'wait' && waitSecondsLeft !== null
                      ? `${waitSecondsLeft}s`
                      : ACTION_LABELS[primaryAction]}
                  </Button>
                ) : null}
                {primaryAction === 'wait' && waitSecondsLeft !== null ? (
                  <span data-testid="error-wait-announce" aria-live="polite" className="text-[10.5px] text-[var(--text-tertiary)]">
                    {`${waitSecondsLeft} 秒后自动重试`}
                  </span>
                ) : null}
                {showLogAction ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid="error-open-log"
                    onClick={() => runAction('openLog')}
                  >
                    {ACTION_LABELS.openLog}
                  </Button>
                ) : null}
              </div>
              {/* ★v9（K2）：同一个 provider_* 失败已连续 ≥2 次 ⇒ 先自检一次。
                  次数由服务端算好（`repeatCount`），前端只比较。 */}
              {(state.error.code === 'provider_auth' || state.error.code === 'provider_unavailable') &&
              (state.error.repeatCount ?? 0) >= 2 ? (
                <p className="mt-1 text-[10.5px] leading-snug text-[var(--text-secondary)]">
                  {copy.engine.probeHint}
                </p>
              ) : null}
              {/* ★v8 评审（H3.5 的落地点）：同一根因第 N 次 → 换一句更强的措辞。
                  次数由服务端算好（`repeatCount`），前端只比较，不自己数。 */}
              {state.error.repeatCount !== undefined && state.error.repeatCount >= REPEAT_ALERT_THRESHOLD ? (
                <p data-testid="generation-repeat" className="mt-0.5 text-[10.5px] font-medium leading-snug text-[var(--warn)]">
                  {fill(copy.errors.sameCauseThrice, { count: state.error.repeatCount })}
                </p>
              ) : null}
              {/* ★v7（P-2 / H-12）：hint 由服务端下发——只有服务端知道 exit code 与引擎日志。 */}
              {state.error.hint ? (
                <p className="mt-0.5 text-[10.5px] leading-snug text-[var(--text-secondary)]">
                  <span className="font-medium">{copy.generation.hintLabel} · </span>
                  {state.error.hint}
                </p>
              ) : null}
            </div>
          ) : null}
          {state.phase === 'error' ? (
            <p className="border-t border-[var(--border-subtle)] px-3 py-1.5 text-[10.5px] leading-snug text-[var(--text-tertiary)]">
              {copy.generation.provisionalBody}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
