/**
 * ★v14（U1/U7）`web/lib/library.ts` 的三个纯函数：行链接、游标、分组。
 *
 * 缺了这组用例，坏掉的时候**屏幕上什么都不会变**：
 *
 * 1. **`libraryHref()` 掉了 `?report=`**（AC-143）：列表每一行照样能点、照样跳转到
 *    `/{symbol}`，只是打开的是 **latest**——用户点 v12 看到的是 v13，而他没有任何办法
 *    发现这件事。这是 U-0b 的原话，也是本文件第一段的全部理由。
 * 2. **游标被"归一化"**（★P1-3 / R-76）：`Z` → `+00:00`、或丢掉 `{id}` 那一段，
 *    服务端 `generated_at < ?` 的字符串比较就会**整段漏行且返回 200**。
 *    症状是"翻页得到的行数 < total"——没有报错，没有人报案。
 * 3. **4 个 tab 变 5 个**（★P1-6）：把 `latest` 混进 `SCOPE_TABS`，库页会多出一个
 *    "最近"tab，而它的内容（每个标的只留一份）与"我做过哪些报告、有哪些版本"的
 *    设计目的正好相反——补丁打上去的是**第二个默认视图**（§23.3.2 vs §23.3.8 的老争执）。
 * 4. **分组标签退化**：`groupByDay` 是本地自然日分组，`now` 是**参数**而不是 `Date.now()`，
 *    这样"今天"这个词才不是运气；调用方一旦改成用 UTC 天切，跨零点的那几行会悄悄挪到
 *    "昨天"。
 *
 * 这里只测纯函数：`?report=` 真的被写进 URL（AC-142 的 `router.replace`）与真实浏览器的
 * 翻页行为分别在 `web/tests/**` 的 RSC/组件用例与 `tests/e2e/**` 里。
 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_LIBRARY_SCOPE,
  LIBRARY_SCOPES,
  SCOPE_TABS,
  decodeCursor,
  encodeCursor,
  groupByDay,
  isScopeTab,
  libraryHref,
  scopeLabel,
} from '@/lib/library';
import { copy } from '@/lib/copy';
import type { LibraryResponse, ReportLibraryItem } from '@/lib/types';

/* ── 夹具 ────────────────────────────────────────────────────────────────────── */

/** AC-143 的路径形状：symbol 只允许 `A-Za-z0-9._-`，`report` 必须是 32 位小写 hex。 */
const HREF_RE = /^\/[A-Za-z0-9._-]+\?report=[0-9a-f]{32}$/;

const ID_A = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
const ID_B = '0123456789abcdef0123456789abcdef';
const ID_C = 'fedcba9876543210fedcba9876543210';

/** 真实标的符号（含 A 股 / 港股 / 美股三种点号形态）+ 一个真实形态的 32 位 hex id。 */
const HREF_CASES: { symbol: string; reportId: string; label: string }[] = [
  { symbol: '600519.SH', reportId: ID_A, label: 'A 股' },
  { symbol: '0700.HK', reportId: ID_B, label: '港股（前导零）' },
  { symbol: 'BRK.B', reportId: ID_C, label: '美股（多个点）' },
  { symbol: 'AAPL', reportId: ID_A, label: '美股（无点号）' },
  { symbol: '000001.SZ', reportId: ID_B, label: 'A 股（深市）' },
];

/**
 * 最小化的 `ReportLibraryItem` 工厂。
 *
 * 只填 `groupByDay` 真的会读的字段（`report.generatedAt`）与类型要求的必填项；
 * 其它字段刻意留成默认值，这样本文件不会因为 `ReportSummary` 加了一个可选维度而变红。
 */
function libraryItem(id: string, generatedAt: string, symbol = '600519.SH'): ReportLibraryItem {
  return {
    report: {
      id,
      instrumentId: `CN:${symbol}`,
      version: 1,
      status: 'ready',
      title: `${symbol} · 投研报告`,
      headline: '白酒动销回暖，维持"买入"',
      stance: 'buy',
      asOf: generatedAt,
      generatedAt,
      isLatest: true,
      pinned: false,
      archived: false,
      provisional: false,
      quality: { groundedRatio: 0.91, missingSections: [], prunedBlocks: 0, source: 'aragon' },
      costKnown: true,
      docBytes: 12_345,
    },
    instrument: { id: `CN:${symbol}`, symbol, name: '贵州茅台', market: 'CN', currency: 'CNY' },
    integrity: 'ok',
    deletedAt: null,
  };
}

/** 本地时间 → ISO（`groupByDay` 按**本地自然日**切，所以夹具必须用本地构造）。 */
function localIso(year: number, monthIndex: number, day: number, hour: number): string {
  return new Date(year, monthIndex, day, hour, 0, 0, 0).toISOString();
}

/* ── AC-143：每一行的 href 都带 ?report= ─────────────────────────────────────── */

describe('AC-143: libraryHref() 100% 带 ?report=（用 symbol 不用 instrumentId）', () => {
  it('全部形态的 href 都匹配 ^/[A-Za-z0-9._-]+\\?report=[0-9a-f]{32}$', () => {
    for (const { symbol, reportId, label } of HREF_CASES) {
      const href = libraryHref(symbol, reportId);
      expect(href, `${label}: ${href} 不符合 AC-143 的形状`).toMatch(HREF_RE);
      // 与上面的正则等价，但失败信息更能定位问题（正则只告诉你"不匹配"）。
      expect(href, `${label}: 没有 ?report= ⇒ 点 v12 会看到 v13`).toContain('?report=');
      expect(href.split('?').length, `${label}: query 段只能有一个 ?`).toBe(2);
      expect(href.endsWith(`?report=${reportId}`), `${label}: report 参数不是那一份报告的 id`).toBe(true);
    }
  });

  it('★P1-3 反例：href 里不能出现 instrumentId 形态（`CN:` 或 `CN%3A`）', () => {
    const item = libraryItem(ID_A, localIso(2026, 10, 16, 12));
    const bySymbol = libraryHref(item.instrument.symbol, item.report.id);

    expect(item.instrument.id).toBe('CN:600519.SH'); // 夹具前提：id 与 symbol 确实不同形
    expect(bySymbol).toBe(`/${item.instrument.symbol}?report=${item.report.id}`);
    expect(bySymbol).not.toContain(':');
    expect(bySymbol).not.toContain('%3A');
    // 用 id 拼出来的链接是不同的字符串 —— 这条断言防的是"两种拼法都行所以随手用 id"。
    expect(bySymbol).not.toBe(libraryHref(item.instrument.id, item.report.id));
  });

  it('symbol 含保留字符时被 URL 编码，且不会注入第二个 query 参数', () => {
    const href = libraryHref('600519 SH&S', ID_A);
    expect(href).not.toContain(' ');
    expect(href).not.toContain('&S');
    expect(href).toContain('%20');
    expect(href).toContain('%26');
    expect(href.split('?').length, 'symbol 里的 & 不能变成第二个参数').toBe(2);
    expect(href.endsWith(`?report=${ID_A}`)).toBe(true);
  });

  it('symbol 不需要编码时保持可读（点号与短横线不进 %XX）', () => {
    const href = libraryHref('BRK.B', ID_C);
    expect(href.startsWith('/BRK.B?')).toBe(true);
    expect(href).not.toContain('%2E');
  });
});

/* ── P1-3：游标原样透传 ──────────────────────────────────────────────────────── */

describe('★P1-3: encodeCursor() / decodeCursor() 往返不做时间归一化', () => {
  it('编码后的 `|` 是 %7C，且解码回来的是 {at, id} 原文', () => {
    const at = '2026-11-16T10:10:22Z';
    const raw = encodeCursor(at, ID_A);

    expect(raw).not.toContain('|');
    expect(raw).toContain('%7C');
    expect(decodeURIComponent(raw)).toBe(`${at}|${ID_A}`);

    const cursor = decodeCursor(raw);
    expect(cursor).toEqual({ at, id: ID_A });
    // 这三条是 P1-3 的正文：`Z` 不许变成 `+00:00`，秒不许被吃掉，id 不许丢。
    expect(cursor?.at).toBe(at);
    expect(cursor?.at.endsWith('Z')).toBe(true);
    expect(cursor?.at).not.toContain('+00:00');
    expect(raw).toContain('%3A'); // 冒号也编码了（服务端按解码后的原文比较，二者都不影响正确性）
  });

  it('游标当成真实 query 参数往返一次仍然逐字相同（%7C 的意义）', () => {
    const at = '2026-11-16T10:10:22Z';
    const raw = encodeCursor(at, ID_B);
    const url = new URL(`http://localhost/reports?scope=all&before=${raw}`);
    const readBack = url.searchParams.get('before');
    expect(readBack).toBe(`${at}|${ID_B}`);
    expect(decodeCursor(readBack)).toEqual({ at, id: ID_B });
  });

  it('全部 32 位小写 hex id 都能往返（tie-break 那一段不能丢）', () => {
    const at = '2026-11-16T10:10:22Z';
    for (const id of [ID_A, ID_B, ID_C]) {
      expect(decodeCursor(encodeCursor(at, id))).toEqual({ at, id });
    }
    // `id` 缺席的游标解不出来 —— 同一秒里两份报告，第二段比较才是正确性的一部分。
    expect(decodeCursor(encodeCursor(at, ''))).toBeNull();
  });
});

describe('★P1-3: decodeCursor() 解不开就返回 null（绝不猜一个时间）', () => {
  const id = ID_A;
  const nullCases: { label: string; raw: string | null | undefined }[] = [
    { label: 'null', raw: null },
    { label: 'undefined', raw: undefined },
    { label: "''（第一页）", raw: '' },
    { label: '裸时间戳，没有 |id', raw: '2026-11-16T10:10:22Z' },
    { label: '两段式但 hex 不足 32 位', raw: `2026-11-16T10:10:22Z|${id.slice(0, 8)}` },
    { label: 'Z 被归一化成 +00:00（R-76 的原始形态）', raw: `2026-11-16T10:10:22+00:00|${id}` },
    { label: '大写 hex（服务端只认小写）', raw: `2026-11-16T10:10:22Z|${id.toUpperCase()}` },
    { label: '毫秒精度（不是秒级）', raw: `2026-11-16T10:10:22.123Z|${id}` },
    { label: '多了一段', raw: `2026-11-16T10:10:22Z|${id}|extra` },
    { label: 'decodeURIComponent 会抛的畸形百分号编码', raw: '%E0%A4%A' },
  ];

  it('以上形态全部返回 null', () => {
    for (const { label, raw } of nullCases) {
      expect(decodeCursor(raw), `${label} 应当返回 null`).toBeNull();
    }
  });

  it('畸形编码真的会抛（这条用例的前提必须成立，否则上一条是空转）', () => {
    expect(() => decodeURIComponent('%E0%A4%A')).toThrow();
  });

  it('`+00:00` 形态在编码之后仍然是 null（不是"编码一下就合法了"）', () => {
    const raw = encodeCursor('2026-11-16T10:10:22+00:00', id);
    expect(decodeCursor(raw)).toBeNull();
  });
});

/* ── P1-6：4 个 tab，默认 all ────────────────────────────────────────────────── */

describe('★P1-6: 库页 4 个 tab、默认 all、latest 不是 tab', () => {
  it('SCOPE_TABS 恰好 4 个，且不含 latest', () => {
    expect(SCOPE_TABS.length).toBe(4);
    expect([...SCOPE_TABS]).toEqual(['all', 'starred', 'archived', 'trash']);
    expect(SCOPE_TABS as readonly string[]).not.toContain('latest');
    // 5 个 scope ≠ 4 个 tab：多出来的那一个只服务首页 / 命令面板 / 工作台首屏。
    expect(LIBRARY_SCOPES.length).toBe(5);
    expect(LIBRARY_SCOPES as readonly string[]).toContain('latest');
  });

  it('默认 scope 是 all（不是 latest 也不是 starred）', () => {
    expect(DEFAULT_LIBRARY_SCOPE).toBe('all');
    expect(isScopeTab(DEFAULT_LIBRARY_SCOPE)).toBe(true);
  });

  it('isScopeTab() 只认 4 个 tab 名', () => {
    for (const tab of SCOPE_TABS) expect(isScopeTab(tab)).toBe(true);
    expect(isScopeTab('latest')).toBe(false);
    expect(isScopeTab('nonsense')).toBe(false);
    expect(isScopeTab('')).toBe(false);
    expect(isScopeTab(null)).toBe(false);
    expect(isScopeTab(undefined)).toBe(false);
  });

  it('每个 scope 都有中文标签（服务端不下发 UI 文案）', () => {
    for (const scope of LIBRARY_SCOPES) {
      expect(scopeLabel(scope).length, `${scope} 缺少标签`).toBeGreaterThan(0);
    }
    expect(scopeLabel('all')).toBe(copy.library.scopes.all);
  });
});

/* ── groupByDay：本地自然日分组 ──────────────────────────────────────────────── */

describe('groupByDay(): 按本地自然日分组、保持输入顺序', () => {
  it('今天（2 行）/ 昨天（1 行）/ 更早（1 行）分成 3 组，标签互不相同', () => {
    // 本地时间构造：`groupByDay` 用 `new Date(y, m, d)` 切天，用 UTC 造夹具会跨零点。
    const nowMs = new Date(2026, 10, 16, 12, 0, 0, 0).getTime();
    const items = [
      libraryItem(ID_A, localIso(2026, 10, 16, 12)), // 今天
      libraryItem(ID_B, localIso(2026, 10, 16, 11)), // 今天（1 小时前）
      libraryItem(ID_C, localIso(2026, 10, 15, 12)), // 昨天
      libraryItem('b'.repeat(32), localIso(2026, 10, 8, 12)), // 8 天前
    ];

    const groups = groupByDay(items, nowMs);

    // 形状一次断言到位（`noUncheckedIndexedAccess` 下不靠下标取值）。
    const shape = groups.map((group) => ({
      label: group.label,
      ids: group.items.map((item) => item.report.id),
    }));
    expect(shape).toEqual([
      { label: copy.library.groups.today, ids: [ID_A, ID_B] },
      { label: copy.library.groups.yesterday, ids: [ID_C] },
      { label: '11 月 8 日', ids: ['b'.repeat(32)] },
    ]);

    const labels = groups.map((group) => group.label);
    expect(new Set(labels).size, '每组的标签必须不同，否则"今天/昨天"是装饰').toBe(labels.length);

    // 键 = 本地自然日 0 点的时间戳，且逐组不同。
    const keys = groups.map((group) => group.key);
    expect(keys).toEqual([
      String(new Date(2026, 10, 16).getTime()),
      String(new Date(2026, 10, 15).getTime()),
      String(new Date(2026, 10, 8).getTime()),
    ]);
  });

  it('输入顺序被保留（服务端已按 generated_at desc, id desc 排好，前端不再重排）', () => {
    const nowMs = new Date(2026, 10, 16, 12, 0, 0, 0).getTime();
    const items = [
      libraryItem(ID_A, localIso(2026, 10, 16, 12)),
      libraryItem(ID_B, localIso(2026, 10, 16, 11)),
      libraryItem(ID_C, localIso(2026, 10, 15, 12)),
      libraryItem('c'.repeat(32), localIso(2026, 10, 8, 12)),
    ];

    const flat = groupByDay(items, nowMs).flatMap((group) => group.items.map((item) => item.report.id));
    expect(flat).toEqual(items.map((item) => item.report.id));
    // 每个 item 只出现一次（既没丢也没重复）。
    expect(new Set(flat).size).toBe(items.length);
  });

  it('时间解析不了的行归到"时间未知"，不会静默消失', () => {
    const nowMs = new Date(2026, 10, 16, 12, 0, 0, 0).getTime();
    const groups = groupByDay([libraryItem(ID_A, localIso(2026, 10, 16, 12)), libraryItem(ID_B, 'not-a-date')], nowMs);

    expect(groups.map((group) => group.label)).toEqual([copy.library.groups.today, copy.library.groups.unknown]);
    expect(groups.flatMap((group) => group.items)).toHaveLength(2);
  });

  it('`now` 走本地自然日：同一个 generatedAt 在"当天"与"次日"分到不同组', () => {
    const generatedAt = localIso(2026, 10, 16, 12);
    const sameDay = groupByDay([libraryItem(ID_A, generatedAt)], new Date(2026, 10, 16, 23, 0, 0).getTime());
    const nextDay = groupByDay([libraryItem(ID_A, generatedAt)], new Date(2026, 10, 17, 1, 0, 0).getTime());

    expect(sameDay.map((group) => group.label)).toEqual([copy.library.groups.today]);
    expect(nextDay.map((group) => group.label)).toEqual([copy.library.groups.yesterday]);
  });
});

/* ── 夹具自检：`LibraryResponse` 的形状（防止工厂悄悄漂移） ───────────────────── */

describe('夹具自检', () => {
  it('工厂产出的行能装进 LibraryResponse，且 id 是 32 位小写 hex', () => {
    const response: LibraryResponse = {
      items: [libraryItem(ID_A, localIso(2026, 10, 16, 12))],
      nextCursor: encodeCursor('2026-11-16T10:10:22Z', ID_B),
      total: 1,
      counts: { all: 1 },
      active: [],
    };

    expect(response.items).toHaveLength(1);
    expect(response.items[0]?.report.id).toMatch(/^[0-9a-f]{32}$/);
    expect(decodeCursor(response.nextCursor)).toEqual({ at: '2026-11-16T10:10:22Z', id: ID_B });
    expect(libraryHref(response.items[0]?.instrument.symbol ?? '', ID_A)).toMatch(HREF_RE);
  });
});
