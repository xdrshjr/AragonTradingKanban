/**
 * ★v14（U1/U6/U7，★评审 P1-9）`relativeTime` 只有一份实现：`web/lib/format.ts:80-93`。
 *
 * 缺了这组用例，坏掉的时候**两个页面各说各话，而且都是"看起来正常"的**：
 * v1 的计划是在新的 `web/lib/library.ts` 里再写一份 `relativeTime`，档位还不同
 * （新文 `<7d`、既有 `<30d`）。于是同一份报告在首页/库页显示"11 月 8 日"、
 * 在工作台（`ReportPanel` / `VersionSwitcher`）显示"8 天前"——没有人会怀疑这是 bug，
 * 所有人都会以为那是两处不同的设计。这正是本文自己的 O-39（第二真源）与 P1-9。
 *
 * 两条断言线，缺一条都不算守住：
 *
 * 1. **机械的**：逐文件读源码 —— 凡是渲染相对时间的组件，`from '@/lib/format'` 必须在，
 *    且**不许**自己声明 `function relativeTime` / `const relativeTime`；
 *    `lib/library.ts` 里更不许有这个名字的定义（P1-9 的正文）。
 * 2. **行为的**：档位就是 `刚刚 / N 分钟前 / N 小时前 / N 天前（<30 天）/ 本地日期`。
 *    **8 天那一档是关键**：旧的第二份实现按 7 天切，8 天就会退回日期串；
 *    这里断言同一个输入仍是 `8 天前`，等于钉住"用的是共享的那一份"。
 *
 * 为什么不用快照：这组字符串是中文 UI 契约，快照只会让"改了文案"看起来像回归，
 * 而真正要守的是**档位边界**（E-125 证明必须有小时档）。
 */

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as libraryModule from '@/lib/library';
import { relativeTime } from '@/lib/format';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = join(HERE, '..');

/* ── 机械那一半：谁渲染相对时间，就必须引同一份实现 ───────────────────────────── */

interface ScannedSource {
  readonly label: string;
  /** 相对 `web/` 的路径（失败信息里可读）。 */
  readonly relPath: string;
  /** spec §23.3.3 / §23.3.7 / §23.3.8 明确写了要显示相对时间的那三处。 */
  readonly required: boolean;
}

const SOURCES: readonly ScannedSource[] = [
  { label: '库页每一行（§23.3.8）', relPath: 'components/library/ReportLibraryRow.tsx', required: true },
  { label: '历史版本横幅（§23.3.3 / AC-142）', relPath: 'components/report/HistoricalBanner.tsx', required: true },
  { label: '工作台报告头（§23.3.7）', relPath: 'components/report/ReportPanel.tsx', required: true },
  { label: '首页最近生成（§23.3.7）', relPath: 'components/library/RecentReports.tsx', required: false },
  { label: '版本切换器（§23.3.3）', relPath: 'components/report/VersionSwitcher.tsx', required: false },
];

function readSource(relPath: string): string | null {
  const absolute = join(WEB_ROOT, relPath);
  return existsSync(absolute) ? readFileSync(absolute, 'utf8') : null;
}

/** `import { a, b as c } from '@/lib/format'` → `['a', 'b']`。多行 import 也认。 */
function formatImportSpecifiers(source: string): string[] {
  const names: string[] = [];
  const importRe = /import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'@\/lib\/format'/g;
  for (const match of source.matchAll(importRe)) {
    for (const part of (match[1] ?? '').split(',')) {
      const name = part.trim().split(/\s+as\s+/)[0]?.trim();
      if (name) names.push(name);
    }
  }
  return names;
}

/** 任何"自己造一份"的形态：`function relativeTime` / `const relativeTime =` / `export const relativeTime`. */
const OWN_IMPL_RE = /(?:function|const|let|var)\s+relativeTime\b/;

describe('★P1-9: 相对时间只有一份实现（lib/format.ts）', () => {
  it('spec 点名的三处源码都在仓库里（否则下面的扫描是空转）', () => {
    const missing = SOURCES.filter((source) => source.required && readSource(source.relPath) === null);
    expect(missing.map((source) => source.relPath)).toEqual([]);
  });

  it('凡渲染相对时间的组件都从 @/lib/format 引 relativeTime，且不自建第二份', () => {
    const problems: string[] = [];
    let rendering = 0;

    for (const source of SOURCES) {
      const text = readSource(source.relPath);
      if (text === null) continue; // 并行开发中：文件还不存在就跳过（上面已断言 required 三处存在）

      const callsRelativeTime = text.includes('relativeTime(');
      const importsRelativeTime = formatImportSpecifiers(text).includes('relativeTime');

      if (callsRelativeTime) {
        rendering += 1;
        if (!importsRelativeTime) {
          problems.push(`${source.relPath}（${source.label}）用了 relativeTime 但没有 from '@/lib/format'`);
        }
      }
      if (OWN_IMPL_RE.test(text)) {
        problems.push(`${source.relPath}（${source.label}）自己声明了 relativeTime —— 第二真源`);
      }
    }

    expect(problems).toEqual([]);
    // 扫描非空：真的读到 ≥3 处渲染相对时间的地方。
    expect(rendering).toBeGreaterThanOrEqual(3);
  });

  it('required 三处确实在显示相对时间（去掉它就不是那个界面了）', () => {
    for (const source of SOURCES.filter((entry) => entry.required)) {
      const text = readSource(source.relPath) ?? '';
      expect(text, `${source.relPath} 没有引用 relativeTime`).toContain("from '@/lib/format'");
      expect(text, `${source.relPath} 没有调用 relativeTime()`).toContain('relativeTime(');
    }
  });

  it('web/lib/library.ts 里没有定义 relativeTime（P1-9 的正文）', () => {
    const text = readSource('lib/library.ts');
    expect(text, 'lib/library.ts 不见了 —— 这条断言会空转').not.toBeNull();
    const source = text ?? '';

    // 只看"定义/引入"两种形态：文件头注释里提到 relativeTime() 是**对的**（它说明实现在哪）。
    expect(OWN_IMPL_RE.test(source), 'lib/library.ts 自己声明了 relativeTime').toBe(false);
    expect(source, 'lib/library.ts 不该从 format.ts 引 relativeTime（它只做分组/游标/链接）').not.toMatch(
      /import\s+\{[^}]*\brelativeTime\b[^}]*\}\s+from\s+'@\/lib\/format'/,
    );

    // 运行时兜底：模块的导出面里没有这个成员。
    expect(Object.keys(libraryModule)).not.toContain('relativeTime');
  });
});

/* ── 行为那一半：档位边界 ────────────────────────────────────────────────────── */

const MINUTE = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

/** 固定"现在"，让"今天/刚刚"不是运气。 */
const NOW = new Date('2026-11-16T10:00:00Z').getTime();

function isoAgo(ms: number): string {
  return new Date(NOW - ms).toISOString();
}

describe('relativeTime(): 档位（刚刚 / 分钟 / 小时 / 天 / 本地日期）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('<60s → 刚刚（含"此刻"）', () => {
    expect(relativeTime(isoAgo(0))).toBe('刚刚');
    expect(relativeTime(isoAgo(30_000))).toBe('刚刚');
    expect(relativeTime(isoAgo(MINUTE - 1))).toBe('刚刚');
  });

  it('60s 起进入分钟档', () => {
    expect(relativeTime(isoAgo(MINUTE))).toBe('1 分钟前');
    expect(relativeTime(isoAgo(5 * MINUTE))).toBe('5 分钟前');
    expect(relativeTime(isoAgo(59 * MINUTE))).toBe('59 分钟前');
  });

  it('≥1h → 小时档（E-125：这一档是必需的）', () => {
    expect(relativeTime(isoAgo(HOUR))).toBe('1 小时前');
    expect(relativeTime(isoAgo(3 * HOUR))).toBe('3 小时前');
    expect(relativeTime(isoAgo(23 * HOUR))).toBe('23 小时前');
  });

  it('≥24h 且 <30 天 → N 天前', () => {
    expect(relativeTime(isoAgo(DAY))).toBe('1 天前');
    expect(relativeTime(isoAgo(2 * DAY))).toBe('2 天前');
    expect(relativeTime(isoAgo(29 * DAY))).toBe('29 天前');
  });

  it('★P1-9 关键档：8 天仍是「8 天前」——旧的第二份实现按 <7d 切，这里会变成日期', () => {
    const iso = isoAgo(8 * DAY);
    expect(relativeTime(iso)).toBe('8 天前');
    expect(relativeTime(iso)).toContain('天前');
    // 反证：同一时刻若走 `<7d` 的分档，就会渲染成本地日期（含 `/` 或"年月日"），不会含"天前"。
    expect(relativeTime(iso)).not.toContain('/');
    expect(relativeTime(iso)).not.toContain('年');
  });

  it('≥30 天 → 本地绝对日期（不再说"天前"）', () => {
    const at30 = isoAgo(30 * DAY);
    const text30 = relativeTime(at30);

    expect(text30).not.toContain('天前');
    expect(text30).not.toMatch(/刚刚|分钟前|小时前/);
    // 与"本地日期"这一档的实现对齐（同一时区、同一格式），而不是 ISO/UTC 串。
    expect(text30).toBe(
      new Date(NOW - 30 * DAY).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }),
    );
    expect(text30, '必须带年份，否则跨年的报告分不清').toMatch(/2026/);

    const text45 = relativeTime(isoAgo(45 * DAY));
    expect(text45).not.toContain('天前');
    expect(text45).not.toMatch(/刚刚|分钟前|小时前/);

    // 跨年也走同一档（2026-11-16 往回 400 天落在 2025 年）。
    const text400 = relativeTime(isoAgo(400 * DAY));
    expect(text400).not.toContain('天前');
    expect(text400).toMatch(/2025/);
  });

  it('30 天边界两侧是两档：29 天说「天前」，30 天说日期', () => {
    expect(relativeTime(isoAgo(29 * DAY))).toBe('29 天前');
    expect(relativeTime(isoAgo(29 * DAY))).not.toBe(relativeTime(isoAgo(30 * DAY)));
    expect(relativeTime(isoAgo(30 * DAY))).not.toContain('天前');
  });

  it('占位符：null / undefined / 非法时间串都返回 —', () => {
    expect(relativeTime(null)).toBe('—');
    expect(relativeTime(undefined)).toBe('—');
    expect(relativeTime('')).toBe('—');
    expect(relativeTime('not-a-date')).toBe('—');
  });
});

/* ── 与"现在"无关的一半：不需要假时钟也要成立 ────────────────────────────────── */

describe('relativeTime(): 不受系统时钟影响的形态', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('输入是 ISO 秒级 `Z` 串（服务端 `_iso()` 的形态）也能解析', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(relativeTime('2026-11-16T10:00:00Z')).toBe('刚刚');
    expect(relativeTime(new Date(NOW - 2 * HOUR).toISOString())).toBe('2 小时前');
  });
});
