import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { blockIdOf, blockIds, parseBlockId, sectionIdOf } from '@/lib/blocks';
import type { Block, ReportDoc } from '@/lib/types';

/**
 * AC-41（必过门槛）：块 id 规则单源。
 *
 * `blockId = f"{sectionId}#{index}"`，index 是块在章节内的 **0 基**位置。
 * 真源是 `api/app/services/block_text.py::block_ids()`，前端镜像就是
 * `web/lib/blocks.ts`（C-9 只说这两处实现允许存在）。
 *
 * golden 文件由后端测试 `api/tests/test_block_text.py` 产出：
 * `api/tests/fixtures/block_ids.golden.json`，内容是 `blockId -> sha1(block 投影文本)`。
 * 后端还没写出来时本文件的 golden 断言会 **skip 并说明原因**，而不是红着走 ——
 * 前端这几条本地断言（id 形态、0 基、往返）不依赖后端，任何时候都必须绿。
 */

const FIXTURES = join(process.cwd(), '..', 'api', 'tests', 'fixtures');
const SAMPLE = join(FIXTURES, 'sample_report.json');
// 规范位置由后端测试产出；env 覆盖只用于本地验证（CI/交接时偶尔要指到别处）。
const GOLDEN = process.env.ARAGON_BLOCK_IDS_GOLDEN
  ? resolve(process.cwd(), process.env.ARAGON_BLOCK_IDS_GOLDEN)
  : join(FIXTURES, 'block_ids.golden.json');

// ── 投影文本的测试侧镜像 ───────────────────────────────────────────────────────
// 为什么测试里要再写一份：golden 的 value 是 `sha1(block_to_text(block))`，
// 只核对 key 集合证明不了"同一份文本"，而 AC-41 要求的是 `(blockId → 文本 sha1)`
// 映射完全一致。投影真源仍在后端；这里这份只服务断言，一旦两边漂移，
// golden 比对就会红 —— 这正是这条测试存在的意义（不是第三个实现，是它的探针）。

function num(value: unknown): string {
  if (typeof value === 'boolean') return String(value);
  // Python `f"{v:g}"`：整数不带小数尾巴，浮点去掉多余的 0
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(12)));
  return String(value);
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

/** 镜像 `block_text.block_to_text()`（12 种块，逐分支对齐）。 */
function projectBlock(block: Block): string {
  const anyBlock = block as unknown as Record<string, unknown>;
  const type = anyBlock.type;

  switch (type) {
    case 'paragraph':
    case 'heading':
      return text(anyBlock.text);

    case 'callout':
      return [text(anyBlock.title), text(anyBlock.text)].filter(Boolean).join(' ');

    case 'bullets':
      return ((anyBlock.items as { text?: unknown }[] | undefined) ?? [])
        .map((item) => text(item.text))
        .join('\n');

    case 'metrics':
      return ((anyBlock.items as Record<string, unknown>[] | undefined) ?? [])
        .map((item) => {
          let chunk = `${text(item.label)} ${text(item.value)}`.trim();
          if (item.deltaPct !== null && item.deltaPct !== undefined) chunk = `${chunk}（${num(item.deltaPct)}%）`;
          return chunk;
        })
        .filter(Boolean)
        .join('；');

    case 'table': {
      const columns = ((anyBlock.columns as Record<string, unknown>[] | undefined) ?? []).map((col) =>
        text(col.label ?? col.key),
      );
      const lines: string[] = [];
      if (anyBlock.caption) lines.push(text(anyBlock.caption));
      if (columns.length > 0) lines.push(columns.join(' '));
      for (const row of (anyBlock.rows as Record<string, unknown>[] | undefined) ?? []) {
        lines.push(Object.values(row).map((value) => (value === null ? '' : String(value))).join(' '));
      }
      return lines.filter(Boolean).join('\n');
    }

    case 'chart': {
      const chart = (anyBlock.chart as Record<string, unknown> | undefined) ?? {};
      const bits = [text(chart.title)];
      for (const series of (chart.series as Record<string, unknown>[] | undefined) ?? []) {
        bits.push(text(series.name));
      }
      if (chart.note) bits.push(text(chart.note));
      return bits.filter(Boolean).join(' ');
    }

    case 'quote': {
      const body = text(anyBlock.text);
      const attribution = text(anyBlock.attribution);
      return attribution ? `${body} —— ${attribution}` : body;
    }

    case 'timeline':
      return ((anyBlock.events as Record<string, unknown>[] | undefined) ?? [])
        .map((event) => {
          const bits = [text(event.date), text(event.title)];
          if (event.detail) bits.push(text(event.detail));
          return bits.filter(Boolean).join(' ');
        })
        .join('\n');

    case 'comparables':
      return ((anyBlock.peers as Record<string, unknown>[] | undefined) ?? [])
        .map((peer) => {
          const bits = [text(peer.name), text(peer.symbol)];
          if (peer.pe !== null && peer.pe !== undefined) bits.push(`PE ${num(peer.pe)}`);
          if (peer.pb !== null && peer.pb !== undefined) bits.push(`PB ${num(peer.pb)}`);
          if (peer.growthPct !== null && peer.growthPct !== undefined) bits.push(`增速 ${num(peer.growthPct)}%`);
          if (peer.note) bits.push(text(peer.note));
          return bits.filter(Boolean).join(' ');
        })
        .filter(Boolean)
        .join('；');

    case 'scenario':
      return ((anyBlock.scenarios as Record<string, unknown>[] | undefined) ?? [])
        .map((scenario) => {
          const bits = [text(scenario.name)];
          if (scenario.targetPrice !== null && scenario.targetPrice !== undefined) {
            bits.push(`目标价 ${num(scenario.targetPrice)}`);
          }
          if (scenario.probability !== null && scenario.probability !== undefined) {
            bits.push(`概率 ${num(scenario.probability)}`);
          }
          if (scenario.assumptions) bits.push(text(scenario.assumptions));
          return bits.filter(Boolean).join(' ');
        })
        .filter(Boolean)
        .join('；');

    default:
      return '';
  }
}

const sha1 = (value: string) => createHash('sha1').update(value, 'utf8').digest('hex');

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, 'utf8')) as T;
}

/**
 * golden 的容器形态由后端决定，这里接受三种常见写法，避免格式小改动就红：
 *   { "<id>": "<sha1>" }   |   { blockIds: {…} }   |   { "<id>": { sha1 } }
 */
function unwrapGolden(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object') return {};
  const looksLikeMap = (value: unknown) =>
    Boolean(value) &&
    typeof value === 'object' &&
    Object.keys(value as Record<string, unknown>).some((key) => /#\d+$/.test(key));

  if (looksLikeMap(raw)) {
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      if (!/#\d+$/.test(key)) continue;
      if (typeof value === 'string') out[key] = value;
      else if (value && typeof value === 'object') {
        const digest =
          (value as Record<string, unknown>).sha1 ?? (value as Record<string, unknown>).hash ?? null;
        if (typeof digest === 'string') out[key] = digest;
      }
    }
    return out;
  }

  for (const value of Object.values(raw as Record<string, unknown>)) {
    if (looksLikeMap(value)) return unwrapGolden(value);
  }
  return {};
}

// ── 本地断言（不依赖后端）──────────────────────────────────────────────────────

describe('blockIdOf: 唯一的派生规则（镜像 Python）', () => {
  it('拼出 `${sectionId}#${index}`', () => {
    expect(blockIdOf('business', 3)).toBe('business#3');
    expect(blockIdOf('thesis', 0)).toBe('thesis#0');
  });

  it('不 sanitize 章节 id：原样保留（含下划线、中文、点号）', () => {
    expect(blockIdOf('风险_1', 12)).toBe('风险_1#12');
  });
});

describe('parseBlockId / sectionIdOf', () => {
  it('往返一致', () => {
    const cases: Array<[string, number]> = [
      ['thesis', 0],
      ['fundamentals', 7],
      ['valuation', 42],
      ['risks', 1000],
    ];
    for (const [sectionId, index] of cases) {
      expect(parseBlockId(blockIdOf(sectionId, index))).toEqual({ sectionId, index });
    }
  });

  it('章节 id 自身含 # 时按最后一个 # 切分', () => {
    expect(parseBlockId('a#b#2')).toEqual({ sectionId: 'a#b', index: 2 });
    expect(sectionIdOf('a#b#2')).toBe('a#b');
  });

  it('非块 id 返回 null（严格，不猜）', () => {
    for (const value of ['thesis', 'thesis#', '#3', 'thesis#x', 'thesis#-1', 'thesis#1.5', 'thesis#03', '']) {
      expect(parseBlockId(value), value).toBeNull();
    }
  });

  it('sectionIdOf 对章节 id / 普通文本原样返回', () => {
    expect(sectionIdOf('thesis')).toBe('thesis');
    expect(sectionIdOf('thesis#4')).toBe('thesis');
    expect(sectionIdOf('[S1]')).toBe('[S1]');
  });
});

describe('blockIds: 文档顺序 + 每章节 0 基', () => {
  it('index 在章节内从 0 开始，章节之间不累加', () => {
    const doc = {
      sections: [
        { id: 'a', title: 'A', blocks: [{ type: 'divider' } as Block, { type: 'divider' } as Block] },
        { id: 'b', title: 'B', blocks: [{ type: 'divider' } as Block] },
      ],
    };
    expect(blockIds(doc).map((item) => item.blockId)).toEqual(['a#0', 'a#1', 'b#0']);
  });

  it('空章节不产出任何项，也不影响后续章节的编号', () => {
    const doc = {
      sections: [
        { id: 'empty', title: '空', blocks: [] },
        { id: 'next', title: '下一个', blocks: [{ type: 'divider' } as Block] },
      ],
    };
    const walk = blockIds(doc);
    expect(walk.map((item) => item.blockId)).toEqual(['next#0']);
    expect(walk.some((item) => item.sectionId === 'empty')).toBe(false);
  });

  it('块顺序与 blocks 数组一致，且带回 sectionId 与块本体', () => {
    const doc = {
      sections: [
        {
          id: 'thesis',
          title: '核心逻辑',
          blocks: [{ type: 'heading', level: 2, text: '标题' } as Block, { type: 'divider' } as Block],
        },
      ],
    };
    const walk = blockIds(doc);
    expect(walk).toHaveLength(2);
    expect(walk[0]).toMatchObject({ blockId: 'thesis#0', sectionId: 'thesis' });
    expect(walk[0]?.block).toEqual({ type: 'heading', level: 2, text: '标题' });
    expect(walk[1]?.blockId).toBe('thesis#1');
  });

  it('脏输入不抛：sections 缺失 / 非数组 / 块不是对象', () => {
    expect(blockIds({ sections: [] })).toEqual([]);
    expect(blockIds({ sections: undefined as unknown as [] })).toEqual([]);
    expect(blockIds({} as { sections: [] })).toEqual([]);
    expect(
      blockIds({ sections: [{ id: 'x', title: 'x', blocks: [null as unknown as Block] }] }),
    ).toEqual([]);
  });
});

// ── golden（AC-41，依赖后端产出）───────────────────────────────────────────────

const goldenReady = existsSync(GOLDEN) && existsSync(SAMPLE);
/** 后端还没产出 golden 时用 `it.skip`，让这条断言"明确地不跑"，而不是红着走。 */
const itGolden = goldenReady ? it : it.skip;

describe('AC-41: 与后端 golden 的 (blockId → 文本 sha1) 完全一致', () => {
  itGolden(
    'golden 映射与前端 blockIds + 投影 sha1 逐项相等',
    () => {
      const doc = readJson<ReportDoc>(SAMPLE);
      const golden = unwrapGolden(readJson<unknown>(GOLDEN));

      const mine: Record<string, string> = {};
      for (const { blockId, block } of blockIds(doc)) {
        mine[blockId] = sha1(projectBlock(block));
      }

      // key 集合先比：多一个/少一个都说明遍历规则或 0 基约定漂了
      expect(Object.keys(mine).sort()).toEqual(Object.keys(golden).sort());
      // 再看每份文本的 sha1：文本投影漂了就在这里炸
      expect(mine).toEqual(golden);
    },
  );

  it('golden 文件到位后本文件才会执行上面的比对', () => {
    // 这条永远跑：把"为什么 skip 了"写在测试输出里，而不是让人去猜。
    if (!goldenReady) {
      const missing = [!existsSync(SAMPLE) ? SAMPLE : null, !existsSync(GOLDEN) ? GOLDEN : null].filter(Boolean);
      console.info(
        `[AC-41] 跳过 golden 比对：缺少 ${missing.join(' 与 ')}。` +
          '该文件由后端 `api/tests/test_block_text.py` 产出（前端不得自行生成）。',
      );
    }
    expect(true).toBe(true);
  });
});
