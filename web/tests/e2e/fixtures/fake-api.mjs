#!/usr/bin/env node
/**
 * 零依赖 fake API（spec §19.3.7 / D7 / AC-59）。
 *
 * 目的只有一个：让 e2e 套件**不再依赖** Python、`aragon`、数据库与网络。
 * 因此本文件只允许 import `node:` 内置模块 —— 没有 express，没有 zod，
 * 没有 mock 框架。`playwright.config.ts` 用它起 8010，Next 起 3010。
 *
 * 三条纪律：
 *
 * 1. **确定性**：同一次请求永远得到同一份响应。所有数值都从 `api/tests/fixtures/
 *    sample_report.json`（报告文档的唯一真源）与固定公式推导，不读 `Date.now()`、
 *    不摇随机数。刷新页面不该让断言变红。
 * 2. **单一真源**：报告文档与块 id 全部来自那份 fixture。块 id 的规则是
 *    `${sectionId}#${index}`（`web/lib/blocks.ts::blockIdOf()`），**不在本文件里
 *    另写一份推导**——需要某个块的 id 时从文档里数出来（见 `blockIdOf`）。
 * 3. **诚实降级**：不认识的路径一律 `404` + 仓库统一的错误信封
 *    `{error:{code,message,requestId,detail}}`（`api/app/errors.py`）。
 *    空列表就返回空列表，不编造"有一条数据"来让断言变绿。
 *
 * 启动：
 *
 *     node tests/e2e/fixtures/fake-api.mjs
 *     FAKE_API_PORT=8011 node tests/e2e/fixtures/fake-api.mjs
 *
 * 手工核对：`curl http://127.0.0.1:8010/api/v1/capabilities`
 */

import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOST = '127.0.0.1';
const PORT = Number(process.env.FAKE_API_PORT ?? 8010);
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── 报告文档：唯一真源是后端 fixture ─────────────────────────────────────── */

/**
 * 从 cwd 向上找到仓库根。
 *
 * Playwright 的 `webServer.command` 以 `web/` 为 cwd 启动（config 所在目录），
 * 而 fixture 在仓库根的 `api/tests/fixtures/` 下，所以必须向上走几层；
 * 直接从 `web/` 读会得到一个 404 的 fake API，那比报错更难查。
 */
function locateFixture(relativePath) {
  for (const start of [process.cwd(), HERE]) {
    let dir = resolve(start);
    for (let depth = 0; depth < 8; depth += 1) {
      const candidate = join(dir, relativePath);
      if (existsSync(candidate)) return candidate;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  return null;
}

const FIXTURE_RELATIVE = join('api', 'tests', 'fixtures', 'sample_report.json');
const fixturePath = locateFixture(FIXTURE_RELATIVE);
if (!fixturePath) {
  console.error(`[fake-api] 找不到报告 fixture：${FIXTURE_RELATIVE}（cwd=${process.cwd()}）`);
  process.exit(1);
}

/** 报告文档（`aragon.research.report/v1`）。整份文件只读一次。 */
const DOC_V1 = JSON.parse(readFileSync(fixturePath, 'utf8'));

/** 块 id 的唯一规则，镜像 `lib/blocks.ts::blockIdOf()`。 */
const blockIdOf = (sectionId, index) => `${sectionId}#${index}`;

/** 按 id 取章节里的块，顺便校验它真的存在（否则 fake 与 fixture 已经漂移）。 */
function blockAt(doc, sectionId, index) {
  const section = (doc.sections ?? []).find((item) => item.id === sectionId);
  const block = section?.blocks?.[index];
  if (!block) {
    throw new Error(`[fake-api] fixture 里没有块 ${sectionId}#${index}，块 id 已漂移`);
  }
  return { section, block, blockId: blockIdOf(sectionId, index) };
}

/**
 * 第二版报告（v2）：**从 v1 派生**，不写第二份文档。
 *
 * 只改三处（标题、目标价、PE 指标），让 `report_diff` 一定有话可说——
 * compare.spec 要证明的是"报告头有比较入口、打开后读得出指标变化"。
 */
const DOC_V2 = (() => {
  const clone = JSON.parse(JSON.stringify(DOC_V1));
  clone.headline = '高端白酒需求见底，直营渠道改革兑现利润弹性（更新版）';
  clone.rating.targetPrice = 2060;
  clone.rating.upsidePct = 23.2;
  clone.keyMetrics[0] = { ...clone.keyMetrics[0], value: '23.2×', deltaPct: -3.8 };
  const thesisParagraph = clone.sections[0]?.blocks?.[1];
  if (thesisParagraph && thesisParagraph.type === 'paragraph') {
    thesisParagraph.text = `${thesisParagraph.text}（本版把 2026 年 PE 假设由 26 倍上调至 27 倍。）`;
  }
  return clone;
})();

/** instrument id 形如 `CN:600519.SH`（`api/app/models/instrument.py:19`）。 */
const INSTRUMENT = {
  id: 'CN:600519.SH',
  symbol: '600519.SH',
  name: '贵州茅台',
  market: 'CN',
  exchange: 'SSE',
  currency: 'CNY',
  sector: '食品饮料',
  tz: 'Asia/Shanghai',
  createdAt: '2026-01-05T00:00:00Z',
  updatedAt: '2026-02-10T01:00:00Z',
};

const GENERATED_AT = {
  1: '2026-02-08T09:30:00Z',
  2: '2026-02-10T01:30:00Z',
  3: '2026-02-10T02:10:00Z',
};

/**
 * 进程内可变状态 —— **只有"生成过一版"这一件事**。
 *
 * 生成流程（POST /instruments/{id}/reports → SSE → run.done）必须能让前端拿到一个
 * 新的版本号，否则 workspace.spec 的"六阶段"用例永远停在同一版。除此之外一切都是常量。
 */
const state = { latestVersion: 2, budgetUsd: 5, docById: new Map() };

const reportId = (version) => `rpt-600519-v${version}`;

for (const version of [1, 2, 3]) {
  const base = version === 2 ? DOC_V2 : DOC_V1;
  const doc = version === 3 ? JSON.parse(JSON.stringify(DOC_V1)) : base;
  if (version === 3) doc.headline = `${DOC_V1.headline}（第 3 版）`;
  state.docById.set(reportId(version), doc);
}

/* ── 质量 / 引擎（v6 G1 / G4） ─────────────────────────────────────────────── */

/** 未接地样本：块 id 必须**真的存在**于文档里（AC-48：可以用 `?block=` 定位）。 */
const GROUNDING_SAMPLE = (() => {
  const { section, block, blockId } = blockAt(DOC_V1, 'valuation', 2);
  const series = block.chart?.series?.[0];
  return {
    blockId,
    path: 'sections[2].blocks[2].chart.series[0].data[0]',
    raw: String(series?.data?.[0] ?? '24.1'),
    text: `${block.chart?.title ?? section.title} · ${section.title} ${series?.name ?? ''} ${series?.data?.[0] ?? ''}`.trim(),
  };
})();

/**
 * 可信度徽标的三态由 `web/lib/grounding.ts::trustState()` 唯一裁决，判据是
 * **`factMissing === 0` → 「已核验 ok/total」**，否则琥珀「N 个数字未找到依据」。
 *
 * 因此 fixture 刻意给两版不同的 grounding，五条 e2e 才能同时覆盖两态：
 *
 * - **v1**：`factClaims 12 / factMissing 1` + 一条可点击的样本 → 琥珀态 + 展开跳转；
 * - **v2 / v3**（`isLatest`）：`factClaims 12 / factMissing 0` → 「已核验 12/12」。
 *
 * 两版的 `ungroundedCount` 都等于 `grounding.factMissing`（AC-61），`samples` 只出现在
 * 真的有未接地数字的那一版里 —— 否则"所有数字都能追溯"和一条样本会互相打脸。
 */
const GROUNDING_V1 = {
  factClaims: 12,
  factMissing: 1,
  totalMissing: 1,
  projectionClaims: 3,
  derivedClaims: 2,
  identifierClaims: 4,
  skipped: null,
  samples: [GROUNDING_SAMPLE],
};

const GROUNDING_CLEAN = {
  factClaims: 12,
  factMissing: 0,
  totalMissing: 0,
  projectionClaims: 3,
  derivedClaims: 2,
  identifierClaims: 4,
  skipped: null,
  samples: [],
};

function quality(version) {
  const grounding = version === 1 ? GROUNDING_V1 : GROUNDING_CLEAN;
  return {
    groundedRatio: (grounding.factClaims - grounding.factMissing) / grounding.factClaims,
    missingSections: [],
    prunedBlocks: 0,
    // ★V6：`ungroundedCount` 与 `grounding.factMissing` 必须相等（AC-61）。
    ungroundedCount: grounding.factMissing,
    source: 'aragon',
    grounding,
    engine: {
      fingerprint: 'test-fingerprint',
      cliVersion: '0.6.68',
      allowTools: ['read_file', 'submit_result'],
      schemaBytes: 1234,
    },
  };
}

/** 镜像 `web/lib/types.ts::ReportSummary`。 */
function summaryFor(version) {
  const id = reportId(version);
  const doc = state.docById.get(id);
  return {
    id,
    instrumentId: INSTRUMENT.id,
    version,
    status: 'ready',
    title: `${INSTRUMENT.name} 投研报告`,
    headline: doc.headline,
    stance: doc.rating.stance,
    asOf: doc.asOf,
    generatedAt: GENERATED_AT[version] ?? GENERATED_AT[3],
    isLatest: version === state.latestVersion,
    pinned: version === state.latestVersion,
    archived: false,
    provisional: false,
    groundedRatio: (12 - (version === 1 ? 1 : 0)) / 12,
    quality: quality(version),
    provider: 'mock',
    model: 'mock-rulebased',
    costUsd: 0.21,
    costKnown: true,
    tokensIn: 18400,
    tokensOut: 2100,
    durationMs: 41200,
    docBytes: Buffer.byteLength(JSON.stringify(doc), 'utf8'),
  };
}

const allVersions = () => Array.from({ length: state.latestVersion }, (_, index) => index + 1);
const summaries = () => allVersions().map(summaryFor).sort((a, b) => b.version - a.version);
const detailFor = (version) => ({ report: summaryFor(version), doc: state.docById.get(reportId(version)) });
const versionOfReportId = (id) => {
  const match = /^rpt-600519-v(\d+)$/.exec(String(id));
  return match ? Number(match[1]) : null;
};

/* ── 能力 ─────────────────────────────────────────────────────────────────── */

const CAPABILITIES = {
  aragon: {
    available: false,
    version: null,
    core: null,
    versionOk: false,
    provider: null,
    model: null,
    hasApiKey: false,
    ajv: false,
    canStructured: false,
    hasSubmitResult: false,
    extraAllowTools: [],
    resolvedBinary: null,
    degradedReason: 'aragon_unavailable',
    errors: [],
  },
  agentMode: 'mock',
  dataProviders: [],
  features: {
    mock: true,
    exportHtml: true,
    exportMd: true,
    pdf: false,
    ask: true,
    askDeep: false,
    fullTextSearch: true,
    pinyinSearch: false,
    // 定时刷新在 e2e 里没有用例：置 false，前端就不去查询 /schedules（少一条噪声请求）。
    schedule: false,
    offline: true,
    realtimeQuote: false,
    semanticSearch: false,
    grounding: true,
    rangeQuery: true,
    readingMode: true,
    costLedger: true,
    engineFingerprint: true,
  },
  limits: {},
  engine: {
    fingerprint: 'test-fingerprint',
    drift: false,
    schemaBytes: 1234,
    allowTools: ['read_file', 'submit_result'],
  },
};

/* ── 行情：120 根确定性日线 + 指标 ────────────────────────────────────────── */

const BAR_COUNT = 120;

const round2 = (value) => Math.round(value * 100) / 100;

function tradingDays(count) {
  // 以 fixture 自己的 `asOf` 为最后一根（不用 Date.now()，否则快照每天在变）。
  const end = Date.parse(DOC_V1.asOf ?? '2026-02-10T01:00:00Z');
  const day = 86_400_000;
  const out = [];
  let cursor = end;
  while (out.length < count) {
    const weekday = new Date(cursor).getUTCDay();
    if (weekday !== 0 && weekday !== 6) out.push(cursor);
    cursor -= day;
  }
  return out.reverse();
}

function buildBars() {
  return tradingDays(BAR_COUNT).map((ts, index) => {
    const o = round2(1_560 + Math.sin(index / 9) * 42 + index * 1.15);
    const c = round2(o + Math.sin(index / 5) * 14 + 1.2);
    const h = round2(Math.max(o, c) + 6 + Math.abs(Math.sin(index / 3)) * 6);
    const l = round2(Math.min(o, c) - 6 - Math.abs(Math.cos(index / 4)) * 6);
    const v = Math.round(1_800_000 + Math.abs(Math.sin(index / 6)) * 1_200_000);
    return { ts, o, h, l, c, v, amount: round2(v * ((o + c) / 2)) };
  });
}

const BARS = buildBars();

function movingAverage(values, window) {
  return values.map((_, index) => {
    if (index + 1 < window) return null;
    const slice = values.slice(index + 1 - window, index + 1);
    return round2(slice.reduce((sum, value) => sum + value, 0) / window);
  });
}

function standardDeviation(values, window, mean) {
  return values.map((_, index) => {
    if (index + 1 < window) return null;
    const slice = values.slice(index + 1 - window, index + 1);
    const variance = slice.reduce((sum, value) => sum + (value - mean[index]) ** 2, 0) / window;
    return Math.sqrt(variance);
  });
}

function exponentialMovingAverage(values, window) {
  const k = 2 / (window + 1);
  let previous = null;
  return values.map((value, index) => {
    if (index + 1 < window) return null;
    previous = previous === null ? value : value * k + previous * (1 - k);
    return round2(previous);
  });
}

function buildIndicators(bars) {
  const closes = bars.map((bar) => bar.c);
  const ma5 = movingAverage(closes, 5);
  const ma10 = movingAverage(closes, 10);
  const ma20 = movingAverage(closes, 20);
  const ma60 = movingAverage(closes, 60);
  const deviation = standardDeviation(closes, 20, ma20);
  const bollUpper = ma20.map((mean, index) =>
    mean === null || deviation[index] === null ? null : round2(mean + 2 * deviation[index]),
  );
  const bollLower = ma20.map((mean, index) =>
    mean === null || deviation[index] === null ? null : round2(mean - 2 * deviation[index]),
  );
  const ema12 = exponentialMovingAverage(closes, 12);
  const ema26 = exponentialMovingAverage(closes, 26);
  const macd = closes.map((_, index) =>
    ema12[index] === null || ema26[index] === null ? null : round2(ema12[index] - ema26[index]),
  );
  const macdFilled = macd.map((value) => value ?? 0);
  const macdSignal = exponentialMovingAverage(macdFilled, 9).map((value, index) =>
    macd[index] === null ? null : value,
  );
  const rsi14 = closes.map((value, index) => {
    if (index < 14) return null;
    let gain = 0;
    let loss = 0;
    for (let i = index - 13; i <= index; i += 1) {
      const delta = closes[i] - closes[i - 1];
      if (delta >= 0) gain += delta;
      else loss -= delta;
    }
    if (loss === 0) return 100;
    return round2(100 - 100 / (1 + gain / loss));
  });
  return { ma5, ma10, ma20, ma60, bollUpper, bollLower, macd, macdSignal, rsi14 };
}

const INDICATORS = buildIndicators(BARS);

function barsResponse(id, query) {
  const timeframe = query.get('timeframe') ?? '1d';
  const adjust = query.get('adjust') ?? 'qfq';
  return {
    instrument: INSTRUMENT,
    bars: BARS,
    indicators: INDICATORS,
    meta: {
      source: 'synthetic',
      barsCount: BARS.length,
      fromTs: BARS[0].ts,
      toTs: BARS[BARS.length - 1].ts,
      adjust,
      timeframe,
      cached: false,
      stale: false,
      warnings: ['fake-api：确定性 fixture，非真实行情'],
    },
  };
}

/* ── 追问（cite 档：命中块 + 校验过的引用） ───────────────────────────────── */

function citeAnswer(id, question) {
  const metrics = blockAt(DOC_V1, 'fundamentals', 0);
  const table = blockAt(DOC_V1, 'fundamentals', 2);
  return {
    id: 'ask-test-0001',
    reportId: id,
    question,
    mode: 'cite',
    status: 'done',
    grounded: true,
    confidence: 0.82,
    citationsDropped: 0,
    answer: {
      answerText: null,
      hint: null,
      citations: [
        { sourceId: 'S3', title: '2025 年第三季度报告', publisher: '上交所' },
        { sourceId: 'S1', title: '行情与估值快照', publisher: 'akshare' },
      ],
      hits: [
        {
          blockId: metrics.blockId,
          sectionId: 'fundamentals',
          sectionTitle: metrics.section.title,
          blockType: metrics.block.type,
          score: 3.42,
          excerpt: '营业收入 1,234.6 亿 · 归母净利 608.0 亿 · 毛利率 91.3% [S3]',
          citations: ['S3'],
        },
        {
          blockId: table.blockId,
          sectionId: 'fundamentals',
          sectionTitle: table.section.title,
          blockType: table.block.type,
          score: 2.18,
          excerpt: '近四个季度收入与利润（单位：亿元）· 2025Q3 营业收入 373.5，同比 16.1%',
          citations: ['S3'],
        },
      ],
    },
    costUsd: 0,
    costKnown: true,
    tokensIn: null,
    tokensOut: null,
    latencyMs: 42,
    errorCode: null,
    errorMessage: null,
    createdAt: DOC_V1.asOf,
    finishedAt: DOC_V1.asOf,
  };
}

/* ── 区间反查（G2a）：日期来自文档正文，不是编的 ──────────────────────────── */

/**
 * 文档里真正出现的日期 → 块。`blockId` 全部由 `blockAt()` 数出来。
 *
 * `spanDays` 是"这段文字覆盖的期间"：单个日期 = 1 天，一个月/季度按天数量级取。
 */
const DATED_BLOCKS = [
  { ...blockAt(DOC_V1, 'thesis', 1), dates: ['2026-02-10'], matchKind: 'day', spanDays: 1 },
  { ...blockAt(DOC_V1, 'thesis', 5), dates: ['2025-10-28'], matchKind: 'month', spanDays: 30 },
  {
    ...blockAt(DOC_V1, 'fundamentals', 4),
    dates: ['2025-04-29', '2025-08-14', '2025-10-28'],
    matchKind: 'day',
    spanDays: 1,
  },
];

const DAY_MS = 86_400_000;
const asDay = (value) => Date.parse(`${value}T00:00:00Z`);

function overlapDaysOf(entry, from, to) {
  let total = 0;
  for (const date of entry.dates) {
    const start = asDay(date);
    const end = start + (entry.spanDays - 1) * DAY_MS;
    const left = Math.max(start, from);
    const right = Math.min(end, to);
    if (right >= left) total += Math.round((right - left) / DAY_MS) + 1;
  }
  return total;
}

function textOf(sectionId, index) {
  const { block } = blockAt(DOC_V1, sectionId, index);
  if (block.type === 'paragraph') return block.text.slice(0, 160);
  if (block.type === 'timeline') {
    return block.events.map((event) => `${event.date} ${event.title}`).join('；').slice(0, 160);
  }
  if (block.type === 'quote') return `${block.text}（${block.attribution}）`.slice(0, 160);
  return JSON.stringify(block).slice(0, 160);
}

function blocksResponse(id, query) {
  const from = query.get('from') ?? '';
  const to = query.get('to') ?? '';
  const fromTs = asDay(from);
  const toTs = asDay(to);
  const base = {
    reportId: id,
    range: { from, to },
    truncated: false,
    asOf: DOC_V1.asOf,
    reason: null,
  };
  if (!Number.isFinite(fromTs) || !Number.isFinite(toTs) || fromTs > toTs) {
    return {
      ...base,
      items: [],
      total: 0,
      reason: 'invalid_range',
    };
  }
  const items = DATED_BLOCKS.map((entry) => ({ entry, overlapDays: overlapDaysOf(entry, fromTs, toTs) }))
    .filter(({ overlapDays }) => overlapDays > 0)
    .sort((left, right) => right.overlapDays - left.overlapDays)
    .map(({ entry, overlapDays }) => ({
      blockId: entry.blockId,
      sectionId: entry.section.id,
      sectionTitle: entry.section.title,
      kind: entry.block.type,
      overlapDays,
      matchKind: entry.matchKind,
      text: textOf(entry.section.id, Number(entry.blockId.split('#')[1])),
    }));
  return { ...base, items, total: items.length };
}

/* ── 成本账本（G3） ───────────────────────────────────────────────────────── */

const COST_WINDOWS = { '7d': 7, '30d': 30, '90d': 90, mtd: 10, ytd: 41 };

function costSummary(window) {
  const days = COST_WINDOWS[window] ?? 30;
  const end = Date.parse(DOC_V1.asOf);
  const from = new Date(end - days * DAY_MS).toISOString().slice(0, 10);
  const spentUsd = 0.84;
  const unlimited = state.budgetUsd === 0;
  const remaining = unlimited ? null : round2(state.budgetUsd - spentUsd);
  return {
    window: COST_WINDOWS[window] ? window : '30d',
    from,
    to: DOC_V1.asOf.slice(0, 10),
    spentUsd,
    knownRuns: 3,
    // 未知成本**单列**，不计入 spentUsd（R-26）。
    unknownRuns: 1,
    byKind: { run: 0.63, ask_deep: 0.21 },
    byModel: [
      { model: 'claude-sonnet-4', usd: 0.63, runs: 2 },
      { model: 'claude-opus-4', usd: 0.21, runs: 1 },
    ],
    budget: {
      monthlyUsd: state.budgetUsd,
      remainingUsd: remaining,
      resetAt: '2026-03-01T00:00:00Z',
      state: unlimited ? 'unlimited' : remaining !== null && remaining <= 0 ? 'exceeded' : 'ok',
    },
    ledgerGap: 0,
  };
}

const emptyEntries = () => ({ items: [], total: 0 });

/* ── 版本比较（G4：`comparability` 追加键，既有键不改名） ──────────────────── */

function numericOf(value) {
  const match = /-?\d+(?:\.\d+)?/.exec(String(value ?? ''));
  return match ? Number(match[0]) : null;
}

function diffResponse(fromId, toId) {
  const fromVersion = versionOfReportId(fromId);
  const toVersion = versionOfReportId(toId);
  const from = fromVersion ? state.docById.get(fromId) : null;
  const to = toVersion ? state.docById.get(toId) : null;
  if (!from || !to) return null;

  const metricsDelta = [];
  to.keyMetrics.forEach((after, index) => {
    const before = from.keyMetrics[index];
    if (!before || before.label !== after.label) return;
    if (before.value === after.value && before.deltaPct === after.deltaPct) return;
    const beforeNumber = numericOf(before.value);
    const afterNumber = numericOf(after.value);
    const deltaPct =
      beforeNumber && afterNumber ? round2(((afterNumber - beforeNumber) / beforeNumber) * 100) : null;
    // `polarity` 缺省即 neutral（AC-42：绝不猜"PE 上升是坏事"）。
    const polarity = after.polarity ?? 'neutral';
    const direction = deltaPct === null ? 'flat' : deltaPct > 0 ? 'up' : deltaPct < 0 ? 'down' : 'flat';
    const tone =
      polarity === 'neutral' || direction === 'flat' ? 'neutral' : (direction === 'up') === (polarity === 'bullish') ? 'up' : 'down';
    metricsDelta.push({ label: after.label, before: beforeNumber, after: afterNumber, deltaPct, direction, polarity, tone });
  });

  const ratingChanged =
    from.rating.stance !== to.rating.stance || from.rating.targetPrice !== to.rating.targetPrice
      ? {
          from: { stance: from.rating.stance, target: from.rating.targetPrice ? String(from.rating.targetPrice) : null },
          to: { stance: to.rating.stance, target: to.rating.targetPrice ? String(to.rating.targetPrice) : null },
        }
      : null;

  const blocksChanged = [];
  from.sections.forEach((section) => {
    const other = to.sections.find((candidate) => candidate.id === section.id);
    section.blocks.forEach((block, index) => {
      const after = other?.blocks?.[index];
      if (!after) {
        blocksChanged.push({ sectionId: section.id, blockType: block.type, change: 'removed', blockIdA: blockIdOf(section.id, index), blockIdB: null });
        return;
      }
      if (JSON.stringify(block) !== JSON.stringify(after)) {
        blocksChanged.push({
          sectionId: section.id,
          blockType: block.type,
          change: 'modified',
          blockIdA: blockIdOf(section.id, index),
          blockIdB: blockIdOf(section.id, index),
        });
      }
    });
  });

  const summaryParts = [];
  if (metricsDelta.length) summaryParts.push(`${metricsDelta.length} 处指标变化`);
  if (ratingChanged) summaryParts.push('评级/目标价变化');
  if (blocksChanged.length) summaryParts.push(`${blocksChanged.length} 处块变化`);

  return {
    a: fromId,
    b: toId,
    statusChanged: null,
    ratingChanged,
    metricsDelta,
    sectionsAdded: [],
    sectionsRemoved: [],
    blocksChanged,
    summary: summaryParts.length ? summaryParts.join('，') : '没有结构性差异',
    // ★V6 G4：只追加，不改名。两版都来自同一个 fake 引擎，所以 same=true 且不渲染横幅。
    comparability: {
      same: true,
      changed: [],
      deltaUnaffected: true,
      note: '两版由同一引擎（test-fingerprint）生成',
    },
  };
}

/* ── 运行（runs） ─────────────────────────────────────────────────────────── */

function runOut(id) {
  return {
    id,
    instrumentId: INSTRUMENT.id,
    status: 'succeeded',
    stage: 'persist',
    pct: 100,
    startedAt: GENERATED_AT[3],
    finishedAt: GENERATED_AT[3],
    exitCode: 0,
    errorCode: null,
    errorMessage: null,
    reportId: reportId(state.latestVersion),
    costUsd: 0.21,
    costKnown: true,
    usage: { inputTokens: 18400, outputTokens: 2100, totalTokens: 20500, turns: 6, toolCalls: 11 },
  };
}

/** 六阶段事件流：`run.done` 必须是最后一条，否则前端会一直转圈。 */
const RUN_EVENTS = [
  ['run.stage', { stage: 'resolve', pct: 5, label: '解析标的' }],
  ['run.stage', { stage: 'snapshot', pct: 15, label: '抓取行情与财务' }],
  ['run.stage', { stage: 'prepare', pct: 20, label: '组装资料与提示词' }],
  ['run.log', { level: 'info', message: 'fake-api：确定性 fixture，无模型调用' }],
  ['run.stage', { stage: 'research', pct: 70, label: 'AI 研究与撰写' }],
  ['run.progress', { pct: 78 }],
  ['run.stage', { stage: 'validate', pct: 85, label: '结构校验与接地检查' }],
  ['run.stage', { stage: 'persist', pct: 100, label: '写入版本' }],
];

/* ── HTTP 基础设施 ────────────────────────────────────────────────────────── */

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'access-control-allow-headers': 'content-type,authorization',
  'access-control-max-age': '600',
};

let requestSeq = 0;
const nextRequestId = () => `fake-${(requestSeq += 1).toString().padStart(4, '0')}`;

function send(res, status, payload, extraHeaders = {}) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    ...CORS,
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
    ...extraHeaders,
  });
  res.end(body);
}

/** 仓库统一的错误信封（`api/app/errors.py:5`）。 */
function sendError(res, status, code, message, detail = {}) {
  send(res, status, { error: { code, message, requestId: nextRequestId(), detail } });
}

function notFound(res, path) {
  sendError(res, 404, 'not_found', `fake-api 没有实现这个路径：${path}`, { path });
}

function readJson(req) {
  return new Promise((resolveBody) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolveBody({});
      try {
        resolveBody(JSON.parse(raw));
      } catch {
        resolveBody({});
      }
    });
  });
}

function streamRunEvents(req, res, runId) {
  res.writeHead(200, {
    ...CORS,
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache, no-transform',
    connection: 'keep-alive',
    'x-accel-buffering': 'no',
  });
  // 一次性把"生成"演完：40ms 一步，总时长 ~320ms，测试不必等 60 秒。
  const events = [
    ...RUN_EVENTS,
    ['run.done', { reportId: reportId(state.latestVersion), version: state.latestVersion, costUsd: 0.21, costKnown: true }],
  ];
  let index = 0;
  const timer = setInterval(() => {
    if (index >= events.length) {
      clearInterval(timer);
      res.end();
      return;
    }
    const [type, payload] = events[index];
    index += 1;
    res.write(`id: ${index}\nevent: ${type}\ndata: ${JSON.stringify({ runId, ...payload })}\n\n`);
  }, 40);
  req.on('close', () => clearInterval(timer));
}

/* ── 路由表 ───────────────────────────────────────────────────────────────── */

/** 每条路由：`[method, 正则, handler(ctx)]`。`ctx.params` 是正则捕获组。 */
const routes = [
  ['GET', /^\/api\/v1\/health$/, (ctx) =>
    send(ctx.res, 200, { status: 'ok', version: '0.0.0-e2e', db: 'fake', uptimeSec: Math.round(process.uptime()) })],

  ['GET', /^\/api\/v1\/capabilities$/, (ctx) => send(ctx.res, 200, CAPABILITIES)],

  ['GET', /^\/api\/v1\/symbols\/search$/, (ctx) => {
    const query = (ctx.query.get('q') ?? '').trim();
    const hit = symbolHit();
    const matched =
      query.length > 0 &&
      (INSTRUMENT.symbol.toLowerCase().includes(query.toLowerCase()) ||
        INSTRUMENT.name.includes(query) ||
        INSTRUMENT.id.toLowerCase().includes(query.toLowerCase()));
    send(ctx.res, 200, { hits: matched ? [hit] : [] });
  }],

  ['POST', /^\/api\/v1\/symbols\/resolve$/, (ctx) => {
    const query = String(ctx.body?.query ?? '').trim();
    const matched =
      query.length > 0 &&
      (INSTRUMENT.symbol.toLowerCase().includes(query.toLowerCase()) ||
        INSTRUMENT.name.includes(query) ||
        INSTRUMENT.id.toLowerCase().includes(query.toLowerCase()));
    if (!matched) {
      sendError(ctx.res, 404, 'instrument_not_found', `找不到这个标的：${query}`, { query });
      return;
    }
    send(ctx.res, 200, { instrument: INSTRUMENT, created: false, needsConfirm: false, candidates: [symbolHit()] });
  }],

  ['GET', /^\/api\/v1\/instruments$/, (ctx) => {
    const ids = (ctx.query.get('ids') ?? '').split(',').map((id) => id.trim()).filter(Boolean);
    if (ids.length === 0) {
      send(ctx.res, 200, { items: [] });
      return;
    }
    const items = ids.filter((id) => id === INSTRUMENT.id).map(() => INSTRUMENT);
    if (items.length === 0) {
      // RSC 靠这个 404 进入 `not-found`（workspace.spec 的"未知标的"用例）。
      sendError(ctx.res, 404, 'instrument_not_found', `找不到这个标的：${ids.join(',')}`, { ids });
      return;
    }
    send(ctx.res, 200, { items });
  }],

  ['GET', /^\/api\/v1\/instruments\/([^/]+)\/quote$/, (ctx) => {
    if (decodeURIComponent(ctx.params[0]) !== INSTRUMENT.id) {
      sendError(ctx.res, 404, 'instrument_not_found', '找不到这个标的', { id: ctx.params[0] });
      return;
    }
    const last = BARS[BARS.length - 1];
    const previous = BARS[BARS.length - 2];
    send(ctx.res, 200, {
      instrumentId: INSTRUMENT.id,
      last: last.c,
      changePct: round2(((last.c - previous.c) / previous.c) * 100),
      prevClose: previous.c,
      dayHigh: last.h,
      dayLow: last.l,
      volume: last.v,
      updatedAt: new Date(last.ts).toISOString(),
      source: 'synthetic',
    });
  }],

  ['GET', /^\/api\/v1\/instruments\/([^/]+)\/bars$/, (ctx) =>
    send(ctx.res, 200, barsResponse(decodeURIComponent(ctx.params[0]), ctx.query))],

  ['GET', /^\/api\/v1\/instruments\/([^/]+)\/reports$/, (ctx) =>
    send(ctx.res, 200, { items: summaries(), total: summaries().length })],

  ['GET', /^\/api\/v1\/instruments\/([^/]+)\/reports\/latest$/, (ctx) =>
    send(ctx.res, 200, detailFor(state.latestVersion))],

  ['POST', /^\/api\/v1\/instruments\/([^/]+)\/reports$/, (ctx) => {
    const instrumentId = decodeURIComponent(ctx.params[0]);
    // 每次生成 +1 版（上限 3：fixture 只准备了 3 份文档，多出来的版本没有正文）。
    state.latestVersion = Math.min(3, state.latestVersion + 1);
    send(
      ctx.res,
      202,
      { runId: 'run-test', status: 'queued', instrumentId, estimateUsd: 0.21 },
      { 'retry-after': '1' },
    );
  }],

  ['GET', /^\/api\/v1\/reports\/([^/]+)\/blocks$/, (ctx) =>
    send(ctx.res, 200, blocksResponse(decodeURIComponent(ctx.params[0]), ctx.query))],

  ['GET', /^\/api\/v1\/reports\/([^/]+)\/asks$/, (ctx) =>
    send(ctx.res, 200, { items: [], total: 0 })],

  ['POST', /^\/api\/v1\/reports\/([^/]+)\/ask$/, (ctx) => {
    const id = decodeURIComponent(ctx.params[0]);
    const question = String(ctx.body?.question ?? '').slice(0, 500);
    send(ctx.res, 200, citeAnswer(id, question));
  }],

  ['GET', /^\/api\/v1\/reports\/([^/]+)\/diff\/([^/]+)$/, (ctx) => {
    const diff = diffResponse(decodeURIComponent(ctx.params[0]), decodeURIComponent(ctx.params[1]));
    if (!diff) {
      sendError(ctx.res, 404, 'not_found', '没有可比较的版本', { a: ctx.params[0], b: ctx.params[1] });
      return;
    }
    send(ctx.res, 200, diff);
  }],

  ['GET', /^\/api\/v1\/reports\/([^/]+)\/export$/, (ctx) => {
    const id = decodeURIComponent(ctx.params[0]);
    const version = versionOfReportId(id);
    if (!version) {
      sendError(ctx.res, 404, 'not_found', '报告不存在', { reportId: id });
      return;
    }
    const doc = state.docById.get(id);
    const format = ctx.query.get('format') ?? 'html';
    const body =
      format === 'md'
        ? `# ${doc.headline}\n\n${doc.summary}\n`
        : `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>${doc.headline}</title></head><body><h1>${doc.headline}</h1><p>${doc.summary}</p></body></html>`;
    ctx.res.writeHead(200, {
      ...CORS,
      'content-type': format === 'md' ? 'text/markdown; charset=utf-8' : 'text/html; charset=utf-8',
      'content-disposition': `attachment; filename="${id}.${format === 'md' ? 'md' : 'html'}"`,
      'cache-control': 'no-store',
    });
    ctx.res.end(body);
  }],

  ['GET', /^\/api\/v1\/reports\/([^/]+)$/, (ctx) => {
    const version = versionOfReportId(decodeURIComponent(ctx.params[0]));
    if (!version || version > state.latestVersion) {
      sendError(ctx.res, 404, 'report_not_found', '报告不存在', { reportId: ctx.params[0] });
      return;
    }
    send(ctx.res, 200, detailFor(version));
  }],

  ['PATCH', /^\/api\/v1\/reports\/([^/]+)$/, (ctx) => {
    const version = versionOfReportId(decodeURIComponent(ctx.params[0]));
    if (!version) {
      sendError(ctx.res, 404, 'report_not_found', '报告不存在', { reportId: ctx.params[0] });
      return;
    }
    send(ctx.res, 200, summaryFor(version));
  }],

  ['DELETE', /^\/api\/v1\/reports\/([^/]+)$/, (ctx) => {
    ctx.res.writeHead(204, { ...CORS, 'cache-control': 'no-store' });
    ctx.res.end();
  }],

  ['GET', /^\/api\/v1\/search$/, (ctx) => {
    const query = ctx.query.get('q') ?? '';
    send(ctx.res, 200, {
      query,
      // 全文本检索在 fake 里只保证"不 500、形状正确"：命中集合留空，
      // 让 SearchDialog 的降级文案有明确的来源，而不是靠编造的假命中。
      instruments: [],
      reports: [],
      tookMs: 1,
      degraded: false,
    });
  }],

  ['GET', /^\/api\/v1\/watchlist$/, (ctx) => send(ctx.res, 200, { items: [] })],
  ['POST', /^\/api\/v1\/watchlist$/, (ctx) => send(ctx.res, 200, { items: [] })],
  ['GET', /^\/api\/v1\/watchlist\/reorder$/, (ctx) => send(ctx.res, 200, { items: [] })],
  ['PATCH', /^\/api\/v1\/watchlist\/reorder$/, (ctx) => send(ctx.res, 200, { items: [] })],
  ['DELETE', /^\/api\/v1\/watchlist\/([^/]+)$/, (ctx) => {
    ctx.res.writeHead(204, { ...CORS, 'cache-control': 'no-store' });
    ctx.res.end();
  }],

  ['GET', /^\/api\/v1\/schedules$/, (ctx) => send(ctx.res, 200, { items: [], total: 0 })],

  ['GET', /^\/api\/v1\/cost\/summary$/, (ctx) => send(ctx.res, 200, costSummary(ctx.query.get('window') ?? '30d'))],
  ['GET', /^\/api\/v1\/cost\/entries$/, (ctx) => send(ctx.res, 200, emptyEntries())],
  ['POST', /^\/api\/v1\/cost\/budget$/, (ctx) => {
    const monthlyUsd = Number(ctx.body?.monthlyUsd ?? 0);
    state.budgetUsd = Number.isFinite(monthlyUsd) && monthlyUsd >= 0 ? monthlyUsd : state.budgetUsd;
    // 回显当前汇总（`api.setBudget()` 期望 CostSummary）：预算改完立刻看得见。
    send(ctx.res, 200, costSummary(ctx.query.get('window') ?? '30d'));
  }],

  ['POST', /^\/api\/v1\/runs\/estimate$/, (ctx) =>
    send(ctx.res, 200, {
      estimatedUsd: 0.21,
      reason: null,
      basis: { samples: 6, medianTokens: 20500, model: 'claude-sonnet-4', priceVersion: 'e2e' },
    })],

  ['POST', /^\/api\/v1\/runs\/([^/]+)\/events-token$/, (ctx) =>
    send(ctx.res, 200, { token: 'fake-token', expiresIn: 60 })],

  ['POST', /^\/api\/v1\/runs\/([^/]+)\/cancel$/, (ctx) =>
    send(ctx.res, 200, { status: 'cancelling', runId: decodeURIComponent(ctx.params[0]) })],

  ['GET', /^\/api\/v1\/runs\/([^/]+)\/events$/, (ctx) =>
    streamRunEvents(ctx.req, ctx.res, decodeURIComponent(ctx.params[0]))],

  ['GET', /^\/api\/v1\/runs\/([^/]+)$/, (ctx) => send(ctx.res, 200, runOut(decodeURIComponent(ctx.params[0])))],

  ['GET', /^\/api\/v1\/asks\/([^/]+)$/, (ctx) => {
    send(ctx.res, 200, citeAnswer(reportId(state.latestVersion), '（历史追问）'));
  }],
];

function symbolHit() {
  return {
    symbol: INSTRUMENT.symbol,
    name: INSTRUMENT.name,
    market: INSTRUMENT.market,
    currency: INSTRUMENT.currency,
    score: 1,
    existing: true,
  };
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${HOST}:${PORT}`);
  const path = url.pathname.replace(/\/+$/, '') || '/';

  if (req.method === 'OPTIONS') {
    res.writeHead(204, CORS);
    res.end();
    return;
  }

  const body = req.method === 'GET' ? {} : await readJson(req);
  const ctx = { req, res, query: url.searchParams, body, params: [] };

  for (const [method, pattern, handler] of routes) {
    if (method !== req.method) continue;
    const match = pattern.exec(path);
    if (!match) continue;
    ctx.params = match.slice(1);
    try {
      handler(ctx);
    } catch (error) {
      console.error(`[fake-api] ${req.method} ${path} 处理器抛错`, error);
      sendError(res, 500, 'internal', error instanceof Error ? error.message : '处理器抛错');
    }
    return;
  }

  notFound(res, `${req.method} ${path}`);
});

server.listen(PORT, HOST, () => {
  // 一行可预期的启动日志：Playwright 等的是 `url` 探活，不靠这行。
  // 用 `warn` 而不是 `log`：仓库的 eslint 只放行 error/warn（no-console）。
  console.warn(`[fake-api] 就绪 http://${HOST}:${PORT}/api/v1（fixture: ${fixturePath}）`);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
  });
}
