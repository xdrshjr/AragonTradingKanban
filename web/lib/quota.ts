/**
 * 额度的**纯函数视图**（spec §24.3.8 / §24.3.15c）。
 *
 * 余额的算账在服务端（账本，§24.3.8）；这里只把 `QuotaBucket` 翻成界面要说的话：
 * 日额度还剩几次、赠送几次、什么时候重置、该不该转 `--warn`。没有任何"自己推断额度"的分支——
 * `remaining` 是权威字段，`dailyLeft` 只是 `limit - usedToday` 的展示拆分。
 */

import { copy, fill } from './copy';
import type { QuotaBucket } from './types';

export interface BucketView {
  /** `limit === null` 或 `remaining === null`。 */
  unlimited: boolean;
  limit: number;
  usedToday: number;
  /** 日额度还剩几次（不含赠送）。 */
  dailyLeft: number;
  bonus: number;
  /** 权威余额（日额度 + 赠送）。不限次时为 `Infinity`。 */
  remaining: number;
  exhausted: boolean;
  /** 剩 1 次或更少：芯片转 `--warn` 色。 */
  low: boolean;
}

export function bucketView(bucket: QuotaBucket | null | undefined): BucketView | null {
  if (!bucket) return null;
  const unlimited = bucket.limit === null || bucket.remaining === null;
  const limit = bucket.limit ?? 0;
  const bonus = Math.max(0, bucket.bonus ?? 0);
  const dailyLeft = unlimited ? 0 : Math.max(0, limit - bucket.usedToday);
  const remaining = unlimited ? Number.POSITIVE_INFINITY : Math.max(0, bucket.remaining ?? 0);
  return {
    unlimited,
    limit,
    usedToday: bucket.usedToday,
    dailyLeft,
    bonus,
    remaining,
    exhausted: !unlimited && remaining <= 0,
    low: !unlimited && remaining > 0 && remaining <= 1,
  };
}

function ymdIn(date: Date, timeZone: string): string {
  // `en-CA` 的 `YYYY-MM-DD` 格式便于字符串比较。
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
}

function hmIn(date: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

/**
 * 「明日 00:00 」/「今日 …」/「11-21 …」（时区 = 额度时区，不是浏览器时区）。
 * 末尾带一个空格：与 `重置` 连写时不贴字（中文里不需要，但保持文案模板一致）。
 */
export function formatReset(resetAt: string | null | undefined, timeZone = 'Asia/Shanghai', now: Date = new Date()): string {
  if (!resetAt) return '';
  const at = new Date(resetAt);
  if (Number.isNaN(at.getTime())) return '';
  let tz = timeZone;
  try {
    new Intl.DateTimeFormat('zh-CN', { timeZone: tz });
  } catch {
    tz = 'Asia/Shanghai';
  }
  const time = hmIn(at, tz).replace(/^24:/, '00:');
  const atDay = ymdIn(at, tz);
  const today = ymdIn(now, tz);
  const tomorrow = ymdIn(new Date(now.getTime() + 24 * 3600 * 1000), tz);
  // 重置时刻本身恰好是"次日 00:00"，在该时区里它的日期就是明天。
  if (atDay === tomorrow) return fill(copy.quota.resetTomorrow, { time });
  if (atDay === today) return fill(copy.quota.resetToday, { time });
  return fill(copy.quota.resetOn, { date: `${atDay.slice(5)} ${time}` });
}

/** 距离重置还有多久：「4 小时 12 分后」/「38 分钟后」。 */
export function resetCountdown(resetAt: string | null | undefined, now: Date = new Date()): string {
  if (!resetAt) return '';
  const diff = new Date(resetAt).getTime() - now.getTime();
  if (!Number.isFinite(diff) || diff <= 0) return fill(copy.quota.countdownMinutes, { minutes: 1 });
  const totalMinutes = Math.max(1, Math.ceil(diff / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0
    ? fill(copy.quota.countdownHours, { hours, minutes })
    : fill(copy.quota.countdownMinutes, { minutes: totalMinutes });
}

/** 芯片文案：「今日剩余 2/3」「今日剩余 0/3 +5」「不限次」。 */
export function chipText(view: BucketView): string {
  if (view.unlimited) return copy.quota.unlimited;
  const base = fill(copy.quota.chip, { left: view.dailyLeft, limit: view.limit });
  return view.bonus > 0 ? `${base}${fill(copy.quota.chipBonus, { bonus: view.bonus })}` : base;
}

/** Tooltip：「日额度 2/3 + 赠送 5 · 明日 00:00 重置」。 */
export function tooltipText(view: BucketView, resetAt: string | null | undefined, timeZone?: string, now?: Date): string {
  if (view.unlimited) return copy.quota.tooltipUnlimited;
  const reset = formatReset(resetAt, timeZone, now);
  return view.bonus > 0
    ? fill(copy.quota.tooltip, { left: view.dailyLeft, limit: view.limit, bonus: view.bonus, reset })
    : fill(copy.quota.tooltipNoBonus, { left: view.dailyLeft, limit: view.limit, reset });
}
