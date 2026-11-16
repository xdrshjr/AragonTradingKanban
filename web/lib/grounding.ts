/**
 * 数字接地（grounding）的**客户端唯一判定层**（spec §19.3.2 / D2 / AC-49 / AC-50）。
 *
 * 为什么单独一个纯模块，而不是在三个组件里各写一遍：
 *
 * 1. **三态判定的优先级只能有一个实现**（★V6-P2-8）。`skipped === 'insufficient_sample'`
 *    必须压过任何漂亮的 `factMissing === 0`：一份只有 3 个数字的报告会同时命中"样本不足"
 *    与"已核验 3/3"，显示哪一条完全取决于 `if` 的先后 —— 而后者是**虚假的安心**。把顺序
 *    写死在一个函数里，组件就只能照抄结果。
 * 2. **样本只解析一次**。`quality.grounding.samples` 是"扁平数组 + blockId"，若每个块渲染时
 *    都 `samples.filter(...)`，300 块的报告就是 O(块数 × 样本数)，而且每次 re-render 都重来。
 *    `indexSamples()` 用 `WeakMap` 按 quality 对象缓存索引：同一份载荷永远只解析一次。
 * 3. **纯函数**：没有 React、没有 `window`、不发请求 —— RSC 与客户端都可用，测试不用渲染。
 *
 * 纪律：这里**不改数、不猜值**。缺数据就是 `absent`（组件据此不渲染徽标），绝不退化成"0 个未接地"。
 */

import { sectionIdOf } from './blocks';
import { copy, fill } from './copy';
import type { GroundingQuality, GroundingSample } from './types';

/**
 * 三态（严格优先级，见 `trustState()`）。
 *
 * `total` 一律是 `factClaims`（分母），`missing` 是 `factMissing`，`ok = total - missing`。
 * `'absent'` 与"全部接地"是两件事：早期报告没有 `grounding` 就是 `absent`。
 */
export type TrustState =
  | { kind: 'absent' }
  | { kind: 'insufficient'; factClaims: number }
  | { kind: 'verified'; ok: number; total: number }
  | { kind: 'missing'; missing: number; total: number };

/**
 * 已核验阈值 —— 与后端 `report_validator.GROUNDED_THRESHOLD` 同一个数（0.9）。
 *
 * 两处必须一致：后端用它决定 `degraded` 与否，前端用它决定徽标说"已核验 48/51"
 * 还是琥珀色提示。前端自己发明一个阈值，就会出现"报告没被判降级、徽标却在报警"。
 */
export const GROUNDED_THRESHOLD = 0.9;

/** 索引缓存：key 是 `quality` 对象本身（载荷是不可变快照，同一份 JSON 只有一个对象）。 */
const indexCache = new WeakMap<GroundingQuality, Map<string, GroundingSample[]>>();

/**
 * 把 `quality.samples` 按 `blockId` 分组。**同一份 `quality` 只解析一次**（`WeakMap` 缓存）。
 *
 * 返回的 `Map` 与其中的数组是**共享只读**的：调用方只能读，不要 push/sort —— 那会污染
 * 同一份载荷的所有读者。
 */
export function indexSamples(quality?: GroundingQuality | null): Map<string, GroundingSample[]> {
  if (!quality) return new Map<string, GroundingSample[]>();

  const cached = indexCache.get(quality);
  if (cached) return cached;

  const index = new Map<string, GroundingSample[]>();
  const samples = Array.isArray(quality.samples) ? quality.samples : [];
  for (const sample of samples) {
    // 防御：样本来自落库的 JSON，早期/半残数据可能出现空 blockId；它无处可挂，直接丢。
    const blockId = typeof sample?.blockId === 'string' ? sample.blockId : '';
    if (!blockId) continue;
    const bucket = index.get(blockId);
    if (bucket) bucket.push(sample);
    else index.set(blockId, [sample]);
  }

  indexCache.set(quality, index);
  return index;
}

/**
 * 某个块有几条失配样本。单点查询用（`BlockFootnote` 每次都问同一个块）。
 *
 * 需要遍历很多块时请先 `indexSamples()` 再查表 —— 这里为了"没有索引也能用"保持了线性扫描。
 */
export function blockSampleCount(
  quality: GroundingQuality | null | undefined,
  blockId: string,
): number {
  if (!quality || !blockId) return 0;
  const samples = Array.isArray(quality.samples) ? quality.samples : [];
  let count = 0;
  for (const sample of samples) {
    if (sample?.blockId === blockId) count += 1;
  }
  return count;
}

/**
 * 三态判定的**唯一实现**，优先级写死（AC-50 / ★V6-P2-8）：
 *
 * 1. 没有 `grounding` → `absent`（"不渲染徽标"，而不是"0 个未接地"）；
 * 2. `skipped === 'insufficient_sample'` → `insufficient`（**压过**任何 `factMissing === 0`）；
 * 3. `ratio >= 0.9` → `verified`（文案里仍然给 `ok/total`，所以"48/51"是可见的）；
 * 4. 其余 → `missing`。
 *
 * 注意 2 在 3 **之前**：样本不足的报告宁可说"数字太少，未评估"，也不给一个 1.0 的假安心。
 */
export function trustState(quality?: GroundingQuality | null): TrustState {
  if (!quality) return { kind: 'absent' };

  if (quality.skipped === 'insufficient_sample') {
    return { kind: 'insufficient', factClaims: quality.factClaims };
  }

  const total = quality.factClaims;
  const ok = Math.max(0, total - quality.factMissing);
  // 阈值判据与后端同源（AC-50 分支②）：48/51 = 0.94 → 「已核验 48/51」。
  // 未接地的 3 个数字不会因此消失 —— 展开面板、块脚注与表格圆点照常标出它们。
  const ratio = total > 0 ? ok / total : 1;
  if (ratio >= GROUNDED_THRESHOLD) return { kind: 'verified', ok, total };
  return { kind: 'missing', missing: quality.factMissing, total };
}

/**
 * 三态对应的一行中文（文案全部来自 `copy.trust`，组件不自己拼句子）。
 *
 * `absent` 返回 `null`：调用方必须据此**不渲染**，而不是渲染一句"暂无数据"。
 */
export function trustText(state: TrustState): string | null {
  switch (state.kind) {
    case 'absent':
      return null;
    case 'insufficient':
      return copy.trust.insufficient;
    case 'verified':
      return fill(copy.trust.verified, { ok: state.ok, total: state.total });
    case 'missing':
      return fill(copy.trust.missing, { count: state.missing });
  }
}

/**
 * 深链 href：`?report=&section=&block=` —— 与 `app/[symbol]/page.tsx` 读取的三个参数同名，
 * 所以"复制链接/在新标签打开"落地后仍然停在同一个数字上（§19.5.7 路由契约）。
 *
 * `section` 由块 id 派生（`lib/blocks.ts` 的单一派生规则），不在调用方重复拼 `#`。
 */
export function jumpHref(reportId: string, blockId: string): string {
  const params = new URLSearchParams();
  if (reportId) params.set('report', reportId);
  const sectionId = blockId ? sectionIdOf(blockId) : '';
  if (sectionId) params.set('section', sectionId);
  if (blockId) params.set('block', blockId);
  const query = params.toString();
  return query ? `?${query}` : '';
}

/**
 * 一条样本的可访问名：`原值 28.4 · …可比公司平均 28.4×…`。
 *
 * 样本行同时显示"原值"和"出处片段"，所以可访问名必须**两样都有** —— 只念 `28.4` 的读屏
 * 用户不知道它出现在哪句话里（AC-36 的"不只靠颜色/形状"）。
 */
export function sampleLabel(sample: GroundingSample): string {
  const raw = fill(copy.trust.sampleRaw, { raw: sample.raw });
  const text = (typeof sample.text === 'string' ? sample.text : '').trim();
  return text ? `${raw} · ${text}` : raw;
}

/**
 * 把 `1,672.00` / `1672.0` / `+12.3%` 归一到可比较的形态。
 *
 * 客户端只拿到"未接地的样本"（`samples`），没有"该块全部数字"——所以结构化组件
 * （`metrics` / `table`）只能做一件事：**把与样本首尾数字一致的格子标出来**。
 * 归一化放在这里而不是在每个组件里各写一遍，否则 `1672.0` 与 `1,672.00` 会被判成两个数。
 */
export function normalizeNumberToken(value: unknown): string {
  const text = String(value ?? '')
    .replace(/[,，\s]/g, '')
    .replace(/[%％]/g, '')
    .replace(/[−–—]/g, '-')
    .replace(/^\+/, '');
  const numeric = Number.parseFloat(text);
  if (!Number.isFinite(numeric)) return text;
  return String(numeric);
}

/**
 * 某个块里**已确认未接地**的数字集合（归一化后）。
 *
 * 语义边界必须说清楚：`samples` 有 20 个块的上限（`MAX_SAMPLES`），所以"不在这个集合里"
 * 不等于"一定接地"。因此调用方只能把它用于**标注缺失**（AC-49），绝不能反过来把
 * 其余格子渲染成"已核验"的盾牌 —— 那是 R-22 想避免的过度承诺。
 */
export function ungroundedValues(
  quality: GroundingQuality | null | undefined,
  blockId: string | null | undefined,
): Set<string> {
  const out = new Set<string>();
  if (!quality || !blockId) return out;
  const bucket = indexSamples(quality).get(blockId);
  if (!bucket) return out;
  for (const sample of bucket) out.add(normalizeNumberToken(sample.raw));
  return out;
}
