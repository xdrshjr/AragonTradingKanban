/**
 * 报告库的纯函数（spec §23.6.3）：scope 常量、游标编解码、按日分组、行链接。
 *
 * 三条纪律，每一条都是"不制造第二真源"的具体化：
 *
 * 1. **这里没有 `relativeTime`**。那份唯一实现在 `web/lib/format.ts:relativeTime()`，
 *    首页、库页与工作台（`ReportPanel` / `VersionSwitcher`）都 import 它——★P1-9：
 *    第二份实现会让同一份报告在两个页面显示不同的话（一档是 `<7d`、一档是 `<30d`）。
 * 2. **`encodeCursor` / `decodeCursor` 是纯函数**，因为它们要能被单测（AC-143 的同型守卫）。
 *    服务端只做正则校验、**不做任何时间解析**（★P1-3）：`Z` → `+00:00` 的一次归一化就会
 *    让 `generated_at < ?` 整段漏行，所以游标里的时间是**原样文本**。
 * 3. **`libraryHref()` 100% 带 `?report=`**（AC-143）。不带它，点 v12 进去看到的是 v13
 *    —— 那是 U-0b 的原话。
 */

import type { LibraryScope, ReportLibraryItem } from './types';
import { copy } from './copy';

/** 五个 scope 的名字（与 `api/app/services/report_library.py::LIBRARY_SCOPES` 同序）。 */
export const LIBRARY_SCOPES = ['latest', 'all', 'starred', 'archived', 'trash'] as const;

/**
 * 库页的 4 个 tab（**5 个 scope ≠ 5 个 tab**，★P1-6）。
 *
 * `latest` 不是 tab：它回答"这个标的默认打开哪一份"，服务首页、命令面板与工作台首屏；
 * 库页要回答"我做过哪些报告、有哪些版本"，答案必须包含 superseded 版本 ⇒ 默认 `all`。
 */
export const SCOPE_TABS = ['all', 'starred', 'archived', 'trash'] as const;

export type ScopeTab = (typeof SCOPE_TABS)[number];

export const DEFAULT_LIBRARY_SCOPE: ScopeTab = 'all';

export function isScopeTab(value: string | null | undefined): value is ScopeTab {
  return typeof value === 'string' && (SCOPE_TABS as readonly string[]).includes(value);
}

export function scopeLabel(scope: LibraryScope): string {
  return copy.library.scopes[scope];
}

/* ── 游标 ────────────────────────────────────────────────────────────────── */

/** 与服务端**同一个**正则：三段式、秒级、`Z` 结尾、32 位小写 hex。 */
const CURSOR_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\|([0-9a-f]{32})$/;

export interface Cursor {
  at: string;
  id: string;
}

/**
 * `"{generated_at}|{id}"`，并**编码**（`|` 必须变成 `%7C`）。
 *
 * 不做 URL 编码的后果不是 400，而是某些代理把 `|` 处理成别的东西之后
 * `generated_at < ?` 的比较对象变了——那正是 R-76 说的"翻页静默丢行"。
 */
export function encodeCursor(at: string, id: string): string {
  return encodeURIComponent(`${at}|${id}`);
}

/** 解不开就返回 `null`（调用方据此回到第一页），**绝不**猜一个时间。 */
export function decodeCursor(raw: string | null | undefined): Cursor | null {
  if (!raw) return null;
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  const match = CURSOR_RE.exec(decoded);
  const at = match?.[1];
  const id = match?.[2];
  if (!at || !id) return null;
  return { at, id };
}

/* ── 行链接 ─────────────────────────────────────────────────────────────── */

/**
 * `/{symbol}?report={id}` —— **用 symbol 不用 instrumentId**。
 *
 * `app/[symbol]` 两种都认（`routers/reports.py` 的 `_instrument()` 先按 id 再按 symbol），
 * 但 symbol 是可读、可分享的那一个。
 */
export function libraryHref(symbol: string, reportId: string): string {
  return `/${encodeURIComponent(symbol)}?report=${encodeURIComponent(reportId)}`;
}

/* ── 按日分组 ───────────────────────────────────────────────────────────── */

export interface DayGroup {
  key: string;
  label: string;
  items: ReportLibraryItem[];
}

const DAY = 86_400_000;

function startOfDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

function dayLabel(then: number, now: number): string {
  const diffDays = Math.round((startOfDay(new Date(now)) - startOfDay(new Date(then))) / DAY);
  if (diffDays <= 0) return copy.library.groups.today;
  if (diffDays === 1) return copy.library.groups.yesterday;
  if (diffDays < 7) return copy.library.groups.thisWeek;
  const date = new Date(then);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return sameYear
    ? `${date.getMonth() + 1} 月 ${date.getDate()} 日`
    : `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/**
 * 按**本地自然日**分组，保持输入顺序（服务端已按 `generated_at desc, id desc` 排好）。
 *
 * `now` 是参数而不是 `Date.now()`：分组要能被单测固定住，"今天"这个词才不是运气。
 */
export function groupByDay(items: ReportLibraryItem[], now: number = Date.now()): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const item of items) {
    const then = new Date(item.report.generatedAt).getTime();
    const key = Number.isNaN(then) ? 'unknown' : String(startOfDay(new Date(then)));
    const existing = groups[groups.length - 1];
    if (existing && existing.key === key) {
      existing.items.push(item);
      continue;
    }
    groups.push({
      key,
      label: Number.isNaN(then) ? copy.library.groups.unknown : dayLabel(then, now),
      items: [item],
    });
  }
  return groups;
}

/* ── 回收站 ─────────────────────────────────────────────────────────────── */

/** "还剩 N 天" —— 唯一实现在这里（服务端只给 `deletedAt`，不算天数）。 */
export function daysLeftInTrash(deletedAt: string | null | undefined, retentionDays: number, now = Date.now()): number {
  if (!deletedAt) return retentionDays;
  const then = new Date(deletedAt).getTime();
  if (Number.isNaN(then)) return retentionDays;
  const elapsed = Math.floor((now - then) / DAY);
  return Math.max(0, retentionDays - elapsed);
}
