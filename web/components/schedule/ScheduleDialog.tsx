'use client';

/**
 * 定时刷新编辑器（spec §18.3.3 / §18.5.5 / AC-31 / AC-36）。
 *
 * cron 的解释权在服务端（`services/cron.py`）；前端只做三件事：
 * 1. **即时校验**：镜像服务端的 5 字段子集规则，在字段下面写一行红字，永不阻断输入
 *    ——把明显写错的表达式先拦下来，而不是让用户拿一个 422 回来找哪一位越界了；
 * 2. **下次触发预览**：本地按 UTC 推算，展示时用 `toLocaleString` 转本地时区
 *    （服务端一律 UTC，不做夏令时推断，展示层负责本地化）；
 * 3. **只在服务端才知道的错误**：`409 duplicate_schedule` 在这里翻译成人话。
 *
 * 解析器是纯函数且导出（`nextFireAt`），这样 AC-31 的边界用例可以在 vitest 里直接跑。
 */

import { CalendarClock, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { copy, fill } from '@/lib/copy';
import { dateTime } from '@/lib/format';
import type { ScheduleCreate, ScheduleOut } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Button, Spinner } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';

const MIN_AGE_MIN = 1;
const MIN_AGE_MAX = 720;
const DEFAULT_MIN_AGE = 12;
/** 预览每分钟刷新一次，长时间开着弹窗也不会显示过期的「下次触发」。 */
const PREVIEW_TICK_MS = 30_000;

export interface ScheduleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  instrumentId: string;
  symbol: string;
  /** capabilities.features.schedule — false hides the whole entry */
  enabled: boolean;
}

/* ── cron 解析（纯函数，镜像 api 侧规则） ─────────────────────────────────── */

interface CronSpec {
  /** 排序后的分钟/小时，保证「最早的候选」先被返回。 */
  minutes: number[];
  hours: number[];
  daysOfMonth: Set<number>;
  months: Set<number>;
  daysOfWeek: Set<number>;
  dayOfMonthRestricted: boolean;
  dayOfWeekRestricted: boolean;
}

function digits(raw: string): number | null {
  return /^\d+$/.test(raw) ? Number(raw) : null;
}

/** 展开单个字段，支持 `*`、`n`、`a-b`、`* /n`、`a-b/n`、`a,b`（逗号项可混合）。 */
function expandField(raw: string, min: number, max: number): Set<number> | null {
  const values = new Set<number>();

  for (const part of raw.split(',')) {
    if (part.length === 0) return null;

    const segments = part.split('/');
    if (segments.length > 2) return null;
    const base = segments[0] ?? '';
    const stepRaw = segments[1];

    let step = 1;
    if (stepRaw !== undefined) {
      const parsedStep = digits(stepRaw);
      if (parsedStep === null || parsedStep < 1) return null;
      step = parsedStep;
    }

    let low: number;
    let high: number;

    if (base === '*') {
      low = min;
      high = max;
    } else if (base.includes('-')) {
      const bounds = base.split('-');
      if (bounds.length !== 2) return null;
      const lowValue = digits(bounds[0] ?? '');
      const highValue = digits(bounds[1] ?? '');
      if (lowValue === null || highValue === null) return null;
      low = lowValue;
      high = highValue;
    } else {
      const single = digits(base);
      if (single === null) return null;
      low = single;
      // `a/n` 按标准 cron 语义从 a 走到字段上界；不带步进时就是单点。
      high = stepRaw === undefined ? single : max;
    }

    if (low < min || high > max || low > high) return null;
    for (let value = low; value <= high; value += step) values.add(value);
  }

  return values.size > 0 ? values : null;
}

function parseCron(expr: string): CronSpec | null {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return null;

  const [minuteRaw, hourRaw, dayOfMonthRaw, monthRaw, dayOfWeekRaw] = fields;
  if (!minuteRaw || !hourRaw || !dayOfMonthRaw || !monthRaw || !dayOfWeekRaw) return null;

  const minutes = expandField(minuteRaw, 0, 59);
  const hours = expandField(hourRaw, 0, 23);
  const daysOfMonth = expandField(dayOfMonthRaw, 1, 31);
  const months = expandField(monthRaw, 1, 12);
  const daysOfWeek = expandField(dayOfWeekRaw, 0, 6);
  if (!minutes || !hours || !daysOfMonth || !months || !daysOfWeek) return null;

  return {
    minutes: [...minutes].sort((a, b) => a - b),
    hours: [...hours].sort((a, b) => a - b),
    daysOfMonth,
    months,
    daysOfWeek,
    dayOfMonthRestricted: dayOfMonthRaw !== '*',
    dayOfWeekRestricted: dayOfWeekRaw !== '*',
  };
}

/** 字段级语法 + 取值范围校验（不含「永不触发」这种语义问题）。 */
export function isValidCron(expr: string): boolean {
  return parseCron(expr) !== null;
}

function dayMatches(spec: CronSpec, dayOfMonth: number, dayOfWeek: number): boolean {
  const domOk = spec.daysOfMonth.has(dayOfMonth);
  const dowOk = spec.daysOfWeek.has(dayOfWeek);
  // POSIX 语义：DOM 与 DOW 同时受限取 OR（不是 Quartz 的 AND），与 §18.3.3 裁决一致。
  if (spec.dayOfMonthRestricted && spec.dayOfWeekRestricted) return domOk || dowOk;
  if (spec.dayOfMonthRestricted) return domOk;
  if (spec.dayOfWeekRestricted) return dowOk;
  return true;
}

/**
 * `nextFireAt('0 9 * * 1-5', new Date())` → 下一次触发的绝对时刻；表达式非法返回 null。
 *
 * 全程 UTC（`getUTC*` / `Date.UTC`），不做夏令时推断。两级推进：先按日判定，
 * 命中日再扫时分——最坏情况（`0 0 29 2 *`）只做几千次日期判定，注册/预览路径都够快。
 */
export function nextFireAt(expr: string, after: Date): Date | null {
  const spec = parseCron(expr);
  if (!spec) return null;
  if (Number.isNaN(after.getTime())) return null;

  // 严格晚于 after 的下一个整分钟，与服务端「next_fire(from)」的之后语义一致。
  const fromMs = Math.floor(after.getTime() / 60_000) * 60_000 + 60_000;
  const cursor = new Date(fromMs);
  cursor.setUTCHours(0, 0, 0, 0);

  // 8 年窗口足够覆盖 2/29 的闰年周期，再远就当「永不触发」处理。
  for (let day = 0; day < 366 * 8; day += 1) {
    const month = cursor.getUTCMonth() + 1;
    if (spec.months.has(month) && dayMatches(spec, cursor.getUTCDate(), cursor.getUTCDay())) {
      const year = cursor.getUTCFullYear();
      const dayOfMonth = cursor.getUTCDate();
      for (const hour of spec.hours) {
        for (const minute of spec.minutes) {
          const candidate = Date.UTC(year, month - 1, dayOfMonth, hour, minute, 0, 0);
          if (candidate >= fromMs) return new Date(candidate);
        }
      }
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }

  return null;
}

/* ── 组件 ────────────────────────────────────────────────────────────────── */

export function ScheduleDialog({ open, onOpenChange, instrumentId, symbol, enabled }: ScheduleDialogProps) {
  const [items, setItems] = useState<ScheduleOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [cron, setCron] = useState('');
  const [minAge, setMinAge] = useState(String(DEFAULT_MIN_AGE));
  const [active, setActive] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [tick, setTick] = useState(() => Date.now());

  const cronId = useId();
  const minAgeId = useId();
  const enabledId = useId();

  const trimmed = cron.trim();
  const cronInvalid = trimmed.length > 0 && !isValidCron(trimmed);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.schedules();
      setItems(response.items.filter((item) => item.instrumentId === instrumentId));
      setListError(null);
    } catch (cause: unknown) {
      setListError(cause instanceof ApiError ? cause.message : copy.schedule.loadFailed);
    } finally {
      setLoading(false);
    }
  }, [instrumentId]);

  useEffect(() => {
    if (!open || !enabled) return;
    void load();
  }, [open, enabled, load]);

  useEffect(() => {
    if (!open) return;
    setTick(Date.now());
    const timer = setInterval(() => setTick(Date.now()), PREVIEW_TICK_MS);
    return () => clearInterval(timer);
  }, [open]);

  const preview = useMemo(
    () => (trimmed.length > 0 && isValidCron(trimmed) ? nextFireAt(trimmed, new Date(tick)) : null),
    [trimmed, tick],
  );

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    // 字段下面已经有红字，这里只负责「不发请求」——不 disable 提交按钮，用户才知道为什么没保存。
    if (!isValidCron(trimmed)) return;

    const parsedAge = Number(minAge);
    const body: ScheduleCreate = {
      instrumentId,
      cron: trimmed,
      enabled: active,
      minAgeHours: Number.isFinite(parsedAge)
        ? Math.min(MIN_AGE_MAX, Math.max(MIN_AGE_MIN, Math.round(parsedAge)))
        : DEFAULT_MIN_AGE,
    };

    setSaving(true);
    try {
      await api.createSchedule(body);
      setCron('');
      setFormError(null);
      await load();
    } catch (cause: unknown) {
      if (cause instanceof ApiError && (cause.code === 'duplicate_schedule' || cause.status === 409)) {
        setFormError(copy.schedule.duplicate);
      } else {
        setFormError(cause instanceof ApiError ? cause.message : copy.schedule.loadFailed);
      }
    } finally {
      setSaving(false);
    }
  };

  const remove = async (scheduleId: string) => {
    setBusyId(scheduleId);
    setListError(null);
    try {
      await api.deleteSchedule(scheduleId);
      setConfirmId(null);
      await load();
    } catch (cause: unknown) {
      setListError(cause instanceof ApiError ? cause.message : copy.schedule.loadFailed);
    } finally {
      setBusyId(null);
    }
  };

  const toggle = async (item: ScheduleOut) => {
    setBusyId(item.id);
    setListError(null);
    try {
      await api.patchSchedule(item.id, { enabled: !item.enabled });
      await load();
    } catch (cause: unknown) {
      if (cause instanceof ApiError && (cause.code === 'duplicate_schedule' || cause.status === 409)) {
        setListError(copy.schedule.duplicate);
      } else {
        setListError(cause instanceof ApiError ? cause.message : copy.schedule.loadFailed);
      }
    } finally {
      setBusyId(null);
    }
  };

  // capabilities.features.schedule=false 时整个入口不存在（不渲染、也不发请求）。
  if (!enabled) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={copy.schedule.title}
        description={copy.schedule.cronHint}
        className="w-[min(600px,92vw)] overflow-hidden p-0"
      >
        <header className="flex items-center gap-1.5 border-b border-[var(--border-subtle)] px-3 py-2">
          <CalendarClock className="size-3.5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
          <h2 className="text-[12.5px] font-semibold text-[var(--text-primary)]">{copy.schedule.title}</h2>
          <span className="mono text-[10.5px] text-[var(--text-tertiary)]">{symbol}</span>
        </header>

        <div className="max-h-[64vh] overflow-y-auto">
          {/* 已有定时刷新：同一标的可能有一条（部分唯一索引），列出来让用户看到实际生效的是什么。 */}
          <section className="border-b border-[var(--border-subtle)] px-3 py-2.5" aria-label={copy.schedule.title}>
            {loading && items.length === 0 ? (
              <p className="flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]" aria-live="polite">
                <Spinner className="size-3" />
                <span className="sr-only">{copy.schedule.title}</span>
              </p>
            ) : null}

            {listError ? (
              <p role="alert" className="mb-1.5 text-[11px] text-[var(--danger)]">
                {listError}
              </p>
            ) : null}

            {!loading && items.length === 0 ? (
              <p className="text-[11.5px] text-[var(--text-tertiary)]">{copy.schedule.empty}</p>
            ) : null}

            {items.length > 0 ? (
              <ul className="space-y-1">
                {items.map((item) => (
                  <li
                    key={item.id}
                    className="rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2 py-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="mono min-w-0 truncate text-[12px] text-[var(--text-primary)]">{item.cron}</span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <Badge tone={item.enabled ? 'accent' : 'neutral'}>
                          {item.enabled ? copy.schedule.enabled : copy.schedule.disabled}
                        </Badge>
                        <input
                          type="checkbox"
                          checked={item.enabled}
                          disabled={busyId !== null}
                          aria-label={`${copy.schedule.enabled} · ${item.cron}`}
                          onChange={() => void toggle(item)}
                          className="size-3.5 accent-[var(--accent)]"
                        />
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`${copy.schedule.delete} · ${item.cron}`}
                          disabled={busyId !== null}
                          onClick={() => setConfirmId(item.id)}
                          leadingIcon={<Trash2 className="size-3" aria-hidden="true" />}
                        >
                          删除
                        </Button>
                      </span>
                    </div>

                    <p className="mt-0.5 text-[10.5px] text-[var(--text-tertiary)]">
                      {copy.schedule.nextRunAt} · {dateTime(item.nextRunAt)}
                      {` · ${copy.schedule.minAgeHours} ${item.minAgeHours}`}
                      {item.failureCount > 0 ? ` · ${fill(copy.schedule.failures, { count: item.failureCount })}` : ''}
                    </p>

                    {confirmId === item.id ? (
                      <div className="mt-1.5 rounded-[var(--radius-sm)] border border-[var(--danger)]/40 bg-[var(--danger-weak)] px-2 py-1.5">
                        <p className="text-[11px] text-[var(--danger)]">{copy.schedule.deleteConfirm}</p>
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <Button
                            size="sm"
                            variant="danger"
                            loading={busyId === item.id}
                            onClick={() => void remove(item.id)}
                          >
                            删除
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                            取消
                          </Button>
                        </div>
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          {/* 创建表单：`<form>` 让 Enter 直接提交（AC-36 键盘可达）。 */}
          <form onSubmit={submit} className="space-y-3 px-3 py-3" noValidate>
            <div>
              <label htmlFor={cronId} className="mb-1 block text-[11px] font-medium text-[var(--text-secondary)]">
                {copy.schedule.cron}
              </label>
              <Input
                id={cronId}
                value={cron}
                inputSize="sm"
                className="mono"
                placeholder={copy.schedule.cronPlaceholder}
                invalid={cronInvalid}
                aria-describedby={cronInvalid ? `${cronId}-error` : `${cronId}-hint`}
                onChange={(event) => setCron(event.target.value)}
              />
              {cronInvalid ? (
                <p id={`${cronId}-error`} role="alert" className="mt-1 text-[10.5px] text-[var(--danger)]">
                  {copy.schedule.cronInvalid}
                </p>
              ) : (
                <p id={`${cronId}-hint`} className="mt-1 text-[10.5px] text-[var(--text-tertiary)]">
                  {copy.schedule.cronHint}
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div className="w-[150px]">
                <label htmlFor={minAgeId} className="mb-1 block text-[11px] font-medium text-[var(--text-secondary)]">
                  {copy.schedule.minAgeHours}
                </label>
                <Input
                  id={minAgeId}
                  type="number"
                  inputSize="sm"
                  min={MIN_AGE_MIN}
                  max={MIN_AGE_MAX}
                  step={1}
                  value={minAge}
                  aria-describedby={`${minAgeId}-hint`}
                  onChange={(event) => setMinAge(event.target.value)}
                />
                <p id={`${minAgeId}-hint`} className="mt-1 text-[10.5px] text-[var(--text-tertiary)]">
                  {copy.schedule.minAgeHint}
                </p>
              </div>

              <div className="flex h-7 items-center gap-1.5">
                <input
                  id={enabledId}
                  type="checkbox"
                  checked={active}
                  onChange={(event) => setActive(event.target.checked)}
                  className="size-3.5 accent-[var(--accent)]"
                />
                <label htmlFor={enabledId} className="text-[11.5px] text-[var(--text-secondary)]">
                  {copy.schedule.enabled}
                </label>
              </div>
            </div>

            <div className="rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2 py-1.5">
              <p className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
                {copy.schedule.nextRunAt}
              </p>
              <p className="mono text-[11.5px] text-[var(--text-primary)]">
                {preview ? dateTime(preview.toISOString()) : '—'}
              </p>
              <p className="text-[10px] text-[var(--text-tertiary)]">{copy.schedule.timezoneNote}</p>
            </div>

            {formError ? (
              <p role="alert" className="text-[11px] text-[var(--danger)]">
                {formError}
              </p>
            ) : null}

            <div className="flex items-center justify-end gap-2">
              <Button type="submit" size="sm" variant="primary" loading={saving}>
                {saving ? copy.schedule.saving : copy.schedule.save}
              </Button>
            </div>
          </form>
        </div>
      </DialogContent>
    </Dialog>
  );
}
