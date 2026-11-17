'use client';

import { CircleDollarSign, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { copy, fill } from '@/lib/copy';
import { dateTime } from '@/lib/format';
import type { BudgetState, CostSummary } from '@/lib/types';

import { Badge, type BadgeTone } from '../ui/Badge';
import { Button, Spinner } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { Segmented, type SegmentedOption } from '../ui/Tabs';
import { notify } from '../ui/Toast';

/**
 * 成本面板 + 预算闸门（spec §19.3.5 / §19.5.1 / AC-55 / AC-56 / AC-63）。
 *
 * 这个面板的全部设计都围绕一条规矩：**`NULL ≠ 0`**。
 *  - `spentUsd` 只累加已知成本，未知次数在 `unknownRuns` 里单独成句（「另有 4 次成本未知」）；
 *    把未知折算成 `$0.00` 会让"本月花了多少"说谎，而这正是这份数据存在的理由。
 *  - `budget.state === 'unlimited'`（`monthlyUsd = 0`，默认）时**不渲染剩余额度行**：
 *    剩余 `$—` 会被读成"额度已经用光"，比不显示更糟。
 *  - `ledgerGap > 0` 是"账本少了几行"（savepoint 兜住的写入失败），报告不受影响，所以
 *    只提示、不报警、不允许重试——它已经过去了。
 *
 * 预算改完**即时生效**：`POST /cost/budget` 写的是 `settings`，服务端 `can_start()` 每次读
 * `get_settings()`（不缓存），所以这里保存后重取一次汇总，让"剩余"跟闸门用同一份事实。
 *
 * 注意：`copy.cost` 里没有"重试"这个键，重试按钮复用既有的 `copy.chart.retry`
 * ——不自造中文、也不去改 `copy.ts`（那是另一个真源）。
 */

const WINDOW_KEYS = ['7d', '30d', '90d', 'mtd', 'ytd'] as const;
type CostWindow = (typeof WINDOW_KEYS)[number];

const WINDOW_OPTIONS: readonly SegmentedOption<CostWindow>[] = WINDOW_KEYS.map((value) => ({
  value,
  label: copy.cost.windows[value],
}));

/** 预算四态只影响颜色，文案唯一真源仍在 `copy.cost.state`。 */
const STATE_TONE: Record<BudgetState, BadgeTone> = {
  ok: 'accent',
  near: 'warn',
  exceeded: 'danger',
  unlimited: 'neutral',
};

/** 金额一律两位小数；`null` 不是 0，显示成 `—`。 */
function usd(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—';
}

/** `byKind` 的键由后端给（`run` / `ask_deep`），未登记的品类原样显示而不是吞掉。 */
function kindLabel(kind: string): string {
  const labels: Record<string, string> = copy.cost.kinds;
  return labels[kind] ?? kind;
}

export interface CostPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CostPanel({ open, onOpenChange }: CostPanelProps) {
  const [costWindow, setCostWindow] = useState<CostWindow>('30d');
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [budgetInput, setBudgetInput] = useState('');
  const [saving, setSaving] = useState(false);
  const inputId = useId();
  /** 快速切窗口时，先发后到的旧汇总不许覆盖新的。 */
  const generationRef = useRef(0);

  const load = useCallback(async (target: CostWindow) => {
    const generation = (generationRef.current += 1);
    setLoading(true);
    setError(null);
    try {
      const next = await api.costSummary(target);
      if (generationRef.current !== generation) return;
      setSummary(next);
      // 输入框跟随服务端事实（含"不限额 = 0"），不保留上一次的草稿值。
      setBudgetInput(String(next.budget.monthlyUsd));
    } catch {
      if (generationRef.current !== generation) return;
      // 详情（如 4xx 的 message）对用户没有可操作性，这里只给可重试的一句话。
      setError(copy.cost.loadFailed);
    } finally {
      if (generationRef.current === generation) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void load(costWindow);
  }, [open, costWindow, load]);

  const submitBudget = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = Number(budgetInput);
    // 非数字/负数不发请求：`monthlyUsd` 是金额，猜一个值比拒绝更糟。
    if (!Number.isFinite(parsed) || parsed < 0) return;

    setSaving(true);
    try {
      await api.setBudget(parsed);
      notify.success(copy.cost.saved);
      await load(costWindow);
    } catch {
      setError(copy.cost.loadFailed);
    } finally {
      setSaving(false);
    }
  };

  const empty = summary !== null && summary.knownRuns + summary.unknownRuns === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={copy.cost.title}
        aria-describedby={undefined}
        className="w-[min(560px,92vw)] overflow-hidden p-0"
      >
        <header className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
          <CircleDollarSign className="size-3.5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
          <h2 className="text-[12.5px] font-semibold text-[var(--text-primary)]">{copy.cost.title}</h2>
          <span className="flex-1" />
          <Segmented
            options={WINDOW_OPTIONS}
            value={costWindow}
            onChange={setCostWindow}
            ariaLabel={copy.cost.title}
          />
        </header>

        <div className="max-h-[64vh] space-y-2.5 overflow-y-auto px-3 py-2.5">
          {loading && !summary ? (
            <p className="flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
              <Spinner className="size-3" />
              <span className="sr-only">{copy.cost.title}</span>
            </p>
          ) : null}

          {error ? (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--danger)]/40 bg-[var(--danger-weak)] px-2 py-1.5"
            >
              <p className="min-w-0 flex-1 text-[11.5px] text-[var(--danger)]">{error}</p>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void load(costWindow)}
                leadingIcon={<RefreshCw className="size-3" aria-hidden="true" />}
              >
                {copy.chart.retry}
              </Button>
            </div>
          ) : null}

          {summary ? (
            <>
              <div className="space-y-0.5">
                {/*
                  ★v7（H4 / R-31）：两个数并排，永不合并。
                  `spentUsd` 只含引擎回执；`spentIncludingEstimatedUsd` 才是"这个月大概花了多少"。
                  把估算折进第一个数会让"已确认"这三个字变成谎言。
                */}
                <p className="text-[13px] font-semibold text-[var(--text-primary)]">
                  <span className="mr-1 text-[10.5px] font-normal text-[var(--text-tertiary)]">
                    {copy.cost.confirmed}
                  </span>
                  {fill(copy.cost.spent, { usd: usd(summary.spentUsd) })}
                </p>
                {summary.estimatedRuns > 0 ? (
                  <p className="text-[11.5px] text-[var(--text-secondary)]">
                    <span className="mr-1 text-[10.5px] text-[var(--text-tertiary)]">{copy.cost.estimatedLabel}</span>
                    {`$${usd(summary.estimatedUsd)}`}
                    {` · ${fill(copy.cost.estimatedRuns, { count: summary.estimatedRuns })}`}
                    {` · ${fill(copy.cost.includingEstimated, { usd: usd(summary.spentIncludingEstimatedUsd) })}`}
                  </p>
                ) : null}
                {/* 未知成本单独一行：绝不折算成 $0.00，也绝不并进上一行。 */}
                {summary.unknownRuns > 0 ? (
                  <p className="text-[11.5px] text-[var(--warn)]">
                    {fill(copy.cost.unknownRuns, { count: summary.unknownRuns })}
                  </p>
                ) : null}
                {summary.ledgerGap > 0 ? (
                  <p className="text-[11px] text-[var(--warn)]">
                    {fill(copy.cost.gap, { count: summary.ledgerGap })}
                  </p>
                ) : null}
                {summary.priceVersion ? (
                  <p className="text-[10.5px] text-[var(--text-tertiary)]">
                    {`${copy.cost.estimatedLabel} · ${summary.priceVersion}`}
                  </p>
                ) : null}
              </div>

              {empty ? (
                <p className="text-[11.5px] text-[var(--text-tertiary)]">{copy.cost.empty}</p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  <section
                    aria-label={copy.cost.byKind}
                    className="rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2 py-1.5"
                  >
                    <h3 className="mb-1 text-[10.5px] font-semibold text-[var(--text-tertiary)]">
                      {copy.cost.byKind}
                    </h3>
                    <ul className="space-y-0.5">
                      {Object.entries(summary.byKind).map(([kind, value]) => (
                        <li key={kind} className="flex items-center justify-between gap-2 text-[11.5px]">
                          <span className="truncate text-[var(--text-secondary)]">{kindLabel(kind)}</span>
                          <span className="mono shrink-0 text-[var(--text-primary)]">{usd(value)}</span>
                        </li>
                      ))}
                    </ul>
                  </section>

                  <section
                    aria-label={copy.cost.byModel}
                    className="rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2 py-1.5"
                  >
                    <h3 className="mb-1 text-[10.5px] font-semibold text-[var(--text-tertiary)]">
                      {copy.cost.byModel}
                    </h3>
                    <ul className="space-y-0.5">
                      {summary.byModel.map((row) => (
                        <li key={row.model} className="flex items-center justify-between gap-2 text-[11.5px]">
                          <span className="mono truncate text-[var(--text-secondary)]">{row.model}</span>
                          <span className="mono shrink-0 text-[var(--text-primary)]">
                            {usd(row.usd)}
                            <span className="ml-1 text-[var(--text-tertiary)]">{`×${row.runs}`}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                </div>
              )}

              <section
                aria-label={copy.cost.budget}
                className="rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2 py-1.5"
              >
                <div className="flex items-center gap-1.5">
                  <h3 className="text-[10.5px] font-semibold text-[var(--text-tertiary)]">
                    {copy.cost.budget}
                  </h3>
                  <Badge tone={STATE_TONE[summary.budget.state]}>
                    {copy.cost.state[summary.budget.state]}
                  </Badge>
                </div>

                {summary.budget.state === 'unlimited' ? (
                  <p className="mt-0.5 text-[11.5px] text-[var(--text-tertiary)]">
                    {copy.cost.budgetUnlimited}
                  </p>
                ) : (
                  <p className="mt-0.5 text-[11.5px] text-[var(--text-secondary)]">
                    {fill(copy.cost.budgetRemaining, { usd: usd(summary.budget.remainingUsd) })}
                    {` · ${fill(copy.cost.budgetResetAt, { time: dateTime(summary.budget.resetAt) })}`}
                  </p>
                )}

                <form onSubmit={submitBudget} className="mt-1.5 flex items-center gap-1.5" noValidate>
                  <label htmlFor={inputId} className="sr-only">
                    {copy.cost.budget}
                  </label>
                  <Input
                    id={inputId}
                    value={budgetInput}
                    inputSize="sm"
                    className="mono w-28"
                    inputMode="decimal"
                    placeholder="0"
                    onChange={(event) => setBudgetInput(event.target.value)}
                  />
                  <Button size="sm" variant="secondary" type="submit" loading={saving}>
                    {copy.cost.save}
                  </Button>
                </form>
              </section>
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ── 预算闸门 ────────────────────────────────────────────────────────────── */

export interface BudgetGateDialogProps {
  open: boolean;
  spentUsd: number;
  budgetUsd: number;
  /** `POST /runs/estimate` 给不出时是 `null`——那时说"无法预估"，不显示 `$0.00`。 */
  estimateUsd: number | null;
  /**
   * ★v7（H4）：402 detail 里的 `note` —— 它会说清"其中 $x 是按 token × 单价估算，
   * 不是引擎回执"。闸门用含估算的基数判定（H-7），所以必须把这个事实摆在批准按钮旁边。
   */
  note?: string | null;
  onOverride: () => void;
  onCancel: () => void;
}

/**
 * 402 `monthly_budget_exceeded` 的显式确认（AC-56 / R-25）。
 *
 * 闸门的语义是"可覆盖 + 留痕"，不是静默阻断：主按钮是「仍然生成」，覆盖后服务端会把
 * `run.reason` 记成 `budget_override` 并照常写账本。所以这里必须把三个数字都摆出来
 * （已花/上限/本次预估），让用户知道自己在批准什么。按 Esc 或关闭 = 取消，不做任何事。
 */
export function BudgetGateDialog({
  open,
  spentUsd,
  budgetUsd,
  estimateUsd,
  note,
  onOverride,
  onCancel,
}: BudgetGateDialogProps) {
  const title = fill(copy.cost.gateTitle, { spent: usd(spentUsd), budget: usd(budgetUsd) });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent title={title} description={copy.cost.gateBody} className="w-[min(420px,92vw)] p-0">
        <div className="space-y-2.5 px-3.5 py-3">
          <h2 className="pr-6 text-[12.5px] font-semibold text-[var(--text-primary)]">{title}</h2>
          <p className="text-[11.5px] text-[var(--text-secondary)]">
            {estimateUsd === null
              ? copy.cost.estimateUnknown
              : fill(copy.cost.gateBody, { estimate: usd(estimateUsd) })}
          </p>
          {/* ★v7（H-7 / R-35）：闸门的基数含估算，所以"哪部分是估算"必须写在按钮旁边。 */}
          {note ? (
            <p className="rounded-[var(--radius-sm)] bg-[var(--bg-surface-3)] px-2 py-1 text-[10.5px] leading-snug text-[var(--text-tertiary)]">
              {note}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-1.5">
            <Button size="sm" variant="ghost" onClick={onCancel}>
              {copy.cost.gateCancel}
            </Button>
            <Button size="sm" variant="primary" onClick={onOverride}>
              {copy.cost.gateOverride}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
