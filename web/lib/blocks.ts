/**
 * 块 id 的**前端唯一实现**（C-9 单源纪律 / AC-41）。
 *
 * 规则来自 `api/app/services/block_text.py::block_id_for()`：
 *
 *     blockId = `${sectionId}#${index}`     // index 是块在 section 内的 **0 基**位置
 *
 * 两条实现（Python 与这里）**只允许存在这两个**，靠 AC-41 的 golden 契约测试锁死
 * （`web/tests/blocks.test.ts` 读后端 `test_block_text.py` 产出的
 * `api/tests/fixtures/block_ids.golden.json` 比对）。因此：
 *
 * - 不要在任何组件里自己拼 `${sectionId}#${index}`（那是第二个真源）；
 * - 不要把这些 id 落到数据库或 schema 里 —— 它们是**派生值**，块顺序变了 id 就变；
 * - 本模块是纯函数，没有副作用、不读 DOM、不读 `window`，服务端与客户端都可用。
 */

import type { Block, Section } from './types';

/** 文档的最小形状 —— 只要求 `sections`，这样 RSC 传下来的 `ReportDoc` 直接可用。 */
export interface DocumentWithSections {
  sections: Section[];
}

/** 一次文档遍历的结果项，顺序即文档顺序（章节顺序 → 章节内块顺序）。 */
export interface WalkedBlock {
  blockId: string;
  sectionId: string;
  block: Block;
}

/** 遍历时的中间形状：允许块的判别联合字段缺失（服务端校验前）。 */
type LooseSection = { id?: unknown; blocks?: unknown };

/**
 * 唯一的 id 派生规则。镜像 `block_text.block_id_for()`。
 *
 * 不做 sanitize：`sectionId` 来自受 schema 约束的文档，`#` 是约定分隔符。
 */
export function blockIdOf(sectionId: string, index: number): string {
  return `${sectionId}#${index}`;
}

/**
 * 按文档顺序展开全部块：`[{ blockId, sectionId, block }]`。镜像 `block_text.block_ids()`。
 *
 * 与后端一致：跳过非对象章节；`blocks` 缺失等价于空数组（**空章节照样不产出任何项**，
 * 但后续章节的 index 从 0 重新开始，不会因为前面的空章节而错位）。
 */
export function blockIds(doc: DocumentWithSections): WalkedBlock[] {
  const walk: WalkedBlock[] = [];
  const sections = (doc as { sections?: unknown } | null | undefined)?.sections;
  if (!Array.isArray(sections)) return walk;

  for (const raw of sections) {
    if (!raw || typeof raw !== 'object') continue;
    const section = raw as LooseSection;
    const sectionId = String(section.id ?? '');
    const blocks = section.blocks;
    if (!Array.isArray(blocks)) continue;
    blocks.forEach((block, index) => {
      if (!block || typeof block !== 'object') return;
      walk.push({ blockId: blockIdOf(sectionId, index), sectionId, block: block as Block });
    });
  }
  return walk;
}

/**
 * `blockId` → `{ sectionId, index }`；不是块 id 时返回 `null`。
 *
 * 严格（与 Python 侧 `str(int)` 的产出对齐）：序号必须是无前导零的十进制整数，
 * 所以 `'thesis#03'`、`'thesis#-1'`、`'thesis'`、`'#'` 都返回 `null`。
 * 章节 id 自身含 `#` 也没问题 —— 用**最后一个** `#` 切分。
 */
export function parseBlockId(blockId: string): { sectionId: string; index: number } | null {
  if (typeof blockId !== 'string') return null;
  const at = blockId.lastIndexOf('#');
  if (at <= 0 || at === blockId.length - 1) return null;

  const tail = blockId.slice(at + 1);
  if (!/^\d+$/.test(tail)) return null;
  // 前导零不是规范形态（Python 永远写 0/1/2…，不会写 007）
  if (tail.length > 1 && tail.startsWith('0')) return null;

  const index = Number(tail);
  if (!Number.isSafeInteger(index)) return null;
  return { sectionId: blockId.slice(0, at), index };
}

/**
 * 深链里最常见的两件事之一：拿到块所属的章节。
 *
 * 输入本来就是章节 id（例如搜索结果只有 `sectionId`）时**原样返回** —— 调用方
 * 不需要先判断"这是块 id 还是章节 id"。文本 `[S1]` 之类的非 id 输入同样原样返回，
 * 因为这里不是校验器；要校验请用 `parseBlockId()`。
 */
export function sectionIdOf(blockId: string): string {
  const parsed = parseBlockId(blockId);
  return parsed ? parsed.sectionId : blockId;
}
