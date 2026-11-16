#!/usr/bin/env node
/**
 * AC-35 / §11.2 首屏 JS 预算门禁（`web/scripts/check-bundle.mjs`）。
 *
 * 两件事，任一条不过就退出码非 0：
 *   1. `/[symbol]` 首屏 JS（gzip 后）≤ 预算，默认 **240 kB**（§11.2 与 AC-35 同一个数，
 *      见 v4 的 V4-P1-11 修订）；
 *   2. 四块懒加载面板（AskPanel / CompareView / SearchDialog / ScheduleDialog）
 *      **不出现在**首屏 chunk 里（`next/dynamic` 化之后它们的代码不该被静态打进首屏）。
 *
 * 三种输入（都不需要外部依赖，也不 shell out 到 du/grep —— Windows 上也得跑）：
 *   a. 不给参数：读 `<next-dir>/build-manifest.json` + `app-build-manifest.json`
 *      （+ `react-loadable-manifest.json`，用于证明懒加载边界确实存在）；
 *   b. 给一个目录：同上，只是目录换成你给的；
 *   c. 给一个文本文件：解析 `next build` 的路由表输出（CI 里把构建日志存盘的场景）。
 *      文本模式下无法证明"面板不在首屏 chunk"，脚本会明确标注"仅校验体积"，
 *      而不是假装通过。
 *
 * 关于 gzip 口径：Next 自己打印的 First Load JS 也是 gzip 后大小，但它用的是内部
 * 统计口径；这里用 `zlib.gzipSync` 对首屏 chunk 逐个压一遍求和，可能差 1–2 kB。
 * 所以默认预算取规格给的 240，留出的这点差异已经在 20 kB 的余量里。
 *
 * 用法：
 *   node scripts/check-bundle.mjs                      # 读 .next 的 manifest
 *   node scripts/check-bundle.mjs --budget=240         # 显式预算（kB，gzip）
 *   node scripts/check-bundle.mjs build.log            # 解析 next build 输出
 *   node scripts/check-bundle.mjs --next-dir=.next
 *   node scripts/check-bundle.mjs --json               # 机器可读
 *   node scripts/check-bundle.mjs --help
 */

import { existsSync, readFileSync, statSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { gzipSync } from 'node:zlib';

/** 首屏预算（kB，gzip）。§11.2 与 AC-35 都是 240 —— 改这里的唯一理由是规格改数字。 */
const DEFAULT_BUDGET_KB = 240;

/** 必须懒加载的四个面板（§18.3.6 表格）。 */
const LAZY_PANELS = ['AskPanel', 'CompareView', 'SearchDialog', 'ScheduleDialog'];

/** 目标路由在 app-build-manifest 里的几种键形态。 */
const ROUTE_KEYS = ['/[symbol]/page', '/[symbol]', '/[symbol]/layout', '/[symbol]/route'];

const USAGE = `check-bundle · AC-35 / §11.2 首屏 JS 预算门禁

用法
  node scripts/check-bundle.mjs [options] [manifest-dir | build-log | manifest.json]

选项
  --budget=<kB>     首屏 JS 预算（gzip 后，kB）。默认 ${DEFAULT_BUDGET_KB}
  --budget <kB>     同上，空格分隔的写法也支持
  --next-dir=<dir>  构建产物目录。默认 .next
  --json            输出机器可读的 JSON（CI 里好接）
  --help, -h        打印本用法并退出 0

输入
  无位置参数     读 <next-dir>/app-build-manifest.json + build-manifest.json（+ react-loadable-manifest.json）
  目录           同上，目录由你指定
  文本文件       解析 \`next build\` 的路由表输出（只校验体积；面板边界无法从文本证明）

断言
  1. /[symbol] 首屏 JS（gzip）≤ --budget（默认 ${DEFAULT_BUDGET_KB} kB）
  2. ${LAZY_PANELS.join(' / ')} 不在首屏 chunk 里

退出码
  0 通过 · 1 违规或输入缺失 · 2 参数错误
`;

// ── 参数 ───────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const options = { budgetKb: DEFAULT_BUDGET_KB, nextDir: '.next', json: false, help: false, target: null };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      options.help = true;
    } else if (arg === '--json') {
      options.json = true;
    } else if (arg.startsWith('--budget=')) {
      options.budgetKb = Number(arg.slice('--budget='.length));
    } else if (arg === '--budget') {
      i += 1;
      options.budgetKb = Number(argv[i]);
    } else if (arg.startsWith('--next-dir=')) {
      options.nextDir = arg.slice('--next-dir='.length);
    } else if (arg === '--next-dir') {
      i += 1;
      options.nextDir = argv[i];
    } else if (arg.startsWith('-')) {
      throw new Error(`未知选项 ${arg}`);
    } else if (options.target === null) {
      options.target = arg;
    } else {
      throw new Error(`多余的参数 ${arg}（只接受一个 manifest 目录或 build 日志）`);
    }
  }
  if (!Number.isFinite(options.budgetKb) || options.budgetKb <= 0) {
    throw new Error(`--budget 需要正数（kB），收到 ${options.budgetKb}`);
  }
  return options;
}

// ── 小工具 ─────────────────────────────────────────────────────────────────────

function readJson(path) {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    throw new Error(`解析失败：${path}（${error.message}）`);
  }
}

function sizeOf(path) {
  const buffer = readFileSync(path);
  return { raw: buffer.length, gzip: gzipSync(buffer).length };
}

const kb = (bytes) => bytes / 1000;
const fmt = (bytes) => kb(bytes).toFixed(1);

/**
 * 把 manifest 里的 chunk 路径变成"首屏集合"。顺序无关，但去重必须做 ——
 * rootMainFiles 与 page 的 chunk 列表会有交集，重复求和会虚高。
 */
function uniquePaths(list) {
  const seen = new Set();
  const out = [];
  for (const item of list) {
    if (typeof item !== 'string' || !item) continue;
    const normalized = item.split('\\').join('/');
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

// ── manifest 模式 ─────────────────────────────────────────────────────────────

function collectFromManifests(nextDir) {
  const appManifest = readJson(join(nextDir, 'app-build-manifest.json'));
  const buildManifest = readJson(join(nextDir, 'build-manifest.json'));
  const loadableManifest = readJson(join(nextDir, 'react-loadable-manifest.json'));

  if (!appManifest && !buildManifest) {
    return {
      ok: false,
      reason:
        `在 ${nextDir} 下既没有 app-build-manifest.json 也没有 build-manifest.json。\n` +
        '先跑一次 `npm run build`，或把 `next build` 的输出存成文本再作为参数传进来。',
    };
  }

  const appPages = appManifest?.pages ?? {};
  const buildPages = buildManifest?.pages ?? {};

  const routeChunks = uniquePaths(
    ROUTE_KEYS.flatMap((key) => (Array.isArray(appPages[key]) ? appPages[key] : [])).concat(
      ROUTE_KEYS.flatMap((key) => (Array.isArray(buildPages[key]) ? buildPages[key] : [])),
    ),
  );

  // app router：rootMainFiles 是每次导航都要付的框架 + 运行时成本。
  const rootChunks = uniquePaths(
    (buildManifest?.rootMainFiles ?? []).concat(buildManifest?.polyfillFiles ?? []),
  );

  const firstLoad = uniquePaths([...rootChunks, ...routeChunks]);

  return {
    ok: true,
    mode: 'manifests',
    nextDir,
    routeChunks,
    rootChunks,
    firstLoad,
    loadableManifest,
    routeFound: routeChunks.length > 0,
  };
}

function measureChunks(nextDir, chunks) {
  return chunks.map((chunk) => {
    const diskPath = resolve(nextDir, chunk.split('/').join(sep));
    if (!existsSync(diskPath)) {
      return { chunk, missing: true, raw: 0, gzip: 0 };
    }
    const { raw, gzip } = sizeOf(diskPath);
    return { chunk, missing: false, raw, gzip };
  });
}

/** 在首屏 chunk 的**正文**里找面板标识 —— 静态 import 的名字会留在产物里。 */
function findPanelsInChunks(nextDir, chunks, lazyPanels = []) {
  // `next/dynamic` keeps the component *name* in the importing chunk (it is the
  // variable the loader assigns to), so "the string appears here" is not evidence
  // that the module is here. A panel with a react-loadable boundary and no overlap
  // with the first-load set is proven lazy; only the rest are content-scanned.
  const known = new Set(lazyPanels);
  const hits = [];
  for (const chunk of chunks) {
    const diskPath = resolve(nextDir, chunk.split('/').join(sep));
    if (!existsSync(diskPath)) continue;
    const source = readFileSync(diskPath, 'utf8');
    for (const panel of LAZY_PANELS) {
      if (known.has(panel)) continue;
      if (new RegExp(`\\b${panel}\\b`).test(source)) {
        hits.push({ panel, chunk });
      }
    }
  }
  return hits;
}

/** 懒加载边界应当出现在 react-loadable-manifest 里，且其 chunk 不属于首屏。 */
function checkLoadableBoundary(loadableManifest, firstLoad) {
  const entries = loadableManifest && typeof loadableManifest === 'object' ? Object.entries(loadableManifest) : [];
  const found = [];
  const violations = [];
  const firstLoadSet = new Set(firstLoad);

  for (const [key, value] of entries) {
    const files = Array.isArray(value?.files) ? value.files.map((f) => f.split('\\').join('/')) : [];
    for (const panel of LAZY_PANELS) {
      if (!new RegExp(`\\b${panel}\\b`).test(key) && !files.some((f) => f.includes(panel))) continue;
      found.push({ panel, key, files });
      const overlap = files.filter((file) => firstLoadSet.has(file));
      if (overlap.length > 0) violations.push({ panel, key, overlap });
    }
  }
  return { found, violations, hasManifest: Boolean(loadableManifest) };
}

// ── 文本模式（next build 输出）──────────────────────────────────────────────────

/**
 * 解析形如：
 *   ├ ƒ /[symbol]       3.4 kB          145 kB
 * 的表格行，取该行最后一个数字作为 First Load JS。
 * 只认含 `/[symbol]` 的 Route 行。
 */
function parseBuildText(text) {
  const lines = text.split(/\r?\n/);
  const rows = [];
  let inTable = false;

  for (const line of lines) {
    if (/First Load JS/i.test(line) && /Route|Size/i.test(line)) {
      inTable = true;
      continue;
    }
    if (!line.includes('/[symbol]')) continue;
    if (!inTable && !/\d/.test(line)) continue;

    const numbers = [...line.matchAll(/([\d.]+)\s*(kB|KB|B)\b/g)];
    if (numbers.length === 0) continue;
    const last = numbers[numbers.length - 1];
    const value = Number(last[1]);
    if (!Number.isFinite(value)) continue;
    const bytes = last[2].toLowerCase() === 'b' ? value : value * 1000;
    rows.push({ line: line.trim(), bytes });
  }

  const panelHits = LAZY_PANELS.filter((panel) => new RegExp(`\\b${panel}\\b`).test(text));
  return { rows, panelHits };
}

// ── 报告 ───────────────────────────────────────────────────────────────────────

function pad(text, width) {
  const value = String(text);
  return value.length >= width ? value : value + ' '.repeat(width - value.length);
}

function printTable(rows, budgetKb) {
  const headers = ['chunk', 'raw kB', 'gzip kB'];
  const widths = [
    Math.max(headers[0].length, ...rows.map((row) => row.chunk.length)),
    headers[1].length,
    headers[2].length,
  ];
  const line = (cells) => cells.map((cell, index) => pad(cell, widths[index])).join('  ');

  console.log(line(headers));
  console.log(widths.map((width) => '-'.repeat(width)).join('  '));
  for (const row of rows) {
    console.log(
      line([
        row.chunk,
        row.missing ? 'missing' : fmt(row.raw),
        row.missing ? 'missing' : fmt(row.gzip),
      ]),
    );
  }
  console.log(widths.map((width) => '-'.repeat(width)).join('  '));
  console.log(line(['TOTAL (gzip)', '', `${fmt(sumGzip(rows))} / ${budgetKb}`]));
}

function sumGzip(rows) {
  return rows.reduce((total, row) => total + (row.missing ? 0 : row.gzip), 0);
}

/** dev 产物（未分块、含 hot-reloader 的 main-app）会让体积判定失真，必须提示。 */
function looksLikeDevBuild(chunks) {
  return chunks.some((chunk) => /main-app\.js$/.test(chunk) || chunk.includes('hot-reloader') || chunk.includes('hmr'));
}

// ── main ──────────────────────────────────────────────────────────────────────

function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`参数错误：${error.message}\n`);
    console.log(USAGE);
    return 2;
  }

  if (options.help) {
    console.log(USAGE);
    return 0;
  }

  const result = { budgetKb: options.budgetKb, panels: LAZY_PANELS, violations: [] };

  // 决定输入模式：显式目标 > 默认 manifest 目录
  let manifestDir = options.nextDir;
  if (options.target) {
    const target = resolve(options.target);
    if (!existsSync(target)) {
      console.error(`找不到 ${options.target}。`);
      return 1;
    }
    if (statSync(target).isDirectory()) {
      manifestDir = target;
    } else {
      result.mode = 'text';
      result.source = options.target;
      const parsed = parseBuildText(readFileSync(target, 'utf8'));
      if (parsed.rows.length === 0) {
        console.error(`在 ${options.target} 里没有找到 /[symbol] 的构建行，无法判定。`);
        return 1;
      }
      // 多行（/page、/layout…）取最大值：首屏成本按最贵的那个算。
      const worst = parsed.rows.reduce((acc, row) => (row.bytes > acc.bytes ? row : acc), parsed.rows[0]);
      const measuredKb = kb(worst.bytes);
      console.log(`输入：${options.target}（next build 文本）`);
      console.log(`行：${worst.line}`);
      console.log(`首屏 JS（gzip, 取自构建输出）：${measuredKb.toFixed(1)} kB / 预算 ${options.budgetKb} kB`);
      console.log(`面板名出现在构建输出中：${parsed.panelHits.length ? parsed.panelHits.join(', ') : '无'}`);
      console.log('注意：文本模式无法证明懒加载边界，只校验体积；AC-35 的面板断言请用 manifest 模式。');
      if (measuredKb > options.budgetKb) {
        console.error(`✗ 首屏 JS ${measuredKb.toFixed(1)} kB 超出预算 ${options.budgetKb} kB`);
        return 1;
      }
      console.log('✓ 体积在预算内');
      return 0;
    }
  } else if (!existsSync(join(manifestDir, 'app-build-manifest.json')) && !existsSync(join(manifestDir, 'build-manifest.json'))) {
    console.error(`在 ${manifestDir} 下没有构建 manifest。先跑 \`npm run build\`，或把构建日志作为参数传入。\n`);
    console.log(USAGE);
    return 1;
  }

  const collected = collectFromManifests(manifestDir);
  if (!collected.ok) {
    console.error(collected.reason);
    return 1;
  }

  result.mode = 'manifests';
  result.source = manifestDir;
  result.rootChunks = collected.rootChunks;
  result.routeChunks = collected.routeChunks;

  if (collected.firstLoad.length === 0) {
    console.error(
      `manifest 里没有 /[symbol] 的首屏 chunk（找过 ${ROUTE_KEYS.join(' / ')}）。\n` +
        '这通常意味着这次构建没有生成该路由，或产物目录不是最新一次构建的。',
    );
    return 1;
  }

  // `polyfills-*.js` is only fetched by browsers without ES2015+; Next's own
  // "First Load JS" figure excludes it too, so counting it inflates the number by
  // ~40 kB and makes the budget impossible to reason about (§11.2).
  const budgeted = collected.firstLoad.filter((file) => !/polyfills[-.]/.test(file));
  const legacyOnly = collected.firstLoad.filter((file) => /polyfills[-.]/.test(file));
  const rows = measureChunks(manifestDir, budgeted);
  const totalGzip = sumGzip(rows);
  const totalKb = kb(totalGzip);

  console.log(`输入：${manifestDir}（app-build-manifest.json + build-manifest.json）`);
  if (looksLikeDevBuild(collected.firstLoad)) {
    console.log('⚠ 检测到未哈希的 dev chunk（main-app.js / hmr）：这是 `next dev` 的产物，');
    console.log('  体积不代表生产首屏。请用 `npm run build` 之后的 .next 或构建日志。');
  }
  if (legacyOnly.length > 0) {
    console.log(`（已排除 legacy-only polyfill：${legacyOnly.join(', ')} —— 现代浏览器不下载）`);
  }
  console.log('');
  printTable(rows, options.budgetKb);
  console.log('');

  if (!collected.routeFound) {
    result.violations.push('route-chunks-missing');
    console.error('✗ 没有找到路由级 chunk 列表（rootMainFiles 单独存在但页面 chunk 缺失）。');
  }

  if (totalKb > options.budgetKb) {
    result.violations.push('budget-exceeded');
    console.error(`✗ 首屏 JS ${totalKb.toFixed(1)} kB > 预算 ${options.budgetKb} kB（gzip）`);
    console.error('  手段见 §11.2：四块面板 next/dynamic、ECharts 动态加载、图标按需。');
  } else {
    console.log(`✓ 首屏 JS ${totalKb.toFixed(1)} kB ≤ 预算 ${options.budgetKb} kB（gzip）`);
  }

  const boundary = checkLoadableBoundary(collected.loadableManifest, collected.firstLoad);
  const contentHits = findPanelsInChunks(
    manifestDir,
    collected.firstLoad,
    boundary.found.filter((item) => item.files.every((file) => !collected.firstLoad.includes(file))).map((item) => item.panel),
  );

  result.loadableEntries = boundary.found.map((item) => item.panel);
  result.panelHits = contentHits;

  if (contentHits.length > 0) {
    result.violations.push('panel-in-first-load');
    for (const hit of contentHits) {
      console.error(`✗ ${hit.panel} 的代码出现在首屏 chunk ${hit.chunk} 里 —— 它必须是 next/dynamic。`);
    }
  }

  if (boundary.violations.length > 0) {
    result.violations.push('panel-chunk-shared-with-first-load');
    for (const violation of boundary.violations) {
      console.error(
        `✗ ${violation.panel} 的懒加载 chunk 与首屏共享文件：${violation.overlap.join(', ')}`,
      );
    }
  }

  if (contentHits.length === 0 && boundary.violations.length === 0) {
    const known = new Set(boundary.found.map((item) => item.panel));
    console.log('✓ 四个面板都不在首屏 chunk 里');
    if (!boundary.hasManifest) {
      console.log('  （没有 react-loadable-manifest.json，只能靠 chunk 正文检查——建议用生产构建复跑）');
    } else if (known.size === 0) {
      console.log(
        '  （react-loadable-manifest 里没看到这四个面板：要么还没实现，要么是别的异步方式；' +
          'chunk 正文里也没出现它们的名字，按"未被首屏引用"判过）',
      );
    }
  }

  if (options.json) {
    console.log('');
    console.log(
      JSON.stringify(
        {
          mode: result.mode,
          source: result.source,
          budgetKb: options.budgetKb,
          measuredKb: Number(totalKb.toFixed(1)),
          chunks: rows.map((row) => ({ chunk: row.chunk, gzipKb: Number(kb(row.gzip).toFixed(1)) })),
          lazyPanels: LAZY_PANELS,
          violations: result.violations,
        },
        null,
        2,
      ),
    );
  }

  if (result.violations.length > 0) {
    console.error(`\nAC-35 未通过（${result.violations.join(', ')}）`);
    return 1;
  }
  console.log('\nAC-35 通过');
  return 0;
}

process.exitCode = main();
