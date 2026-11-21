import { describe, expect, it } from 'vitest';

import golden from '../../contracts/workflow_ops.golden.json';
import { layoutLevels } from '@/lib/workflow/layout';
import {
  applyOps,
  cloneNode,
  contentSignature,
  diffOps,
  invertOps,
  makeEdge,
  makeNode,
  wouldCreateCycle,
} from '@/lib/workflow/ops';
import type { WorkflowDoc, WorkflowOp } from '@/lib/workflow/types';

interface OpCase {
  name: string;
  doc: WorkflowDoc;
  ops: WorkflowOp[];
  expect: { ok: boolean; doc?: WorkflowDoc; structural?: boolean; reason?: string; opIndex?: number };
}
interface LayoutCase {
  name: string;
  doc: WorkflowDoc;
  expect: Record<string, { x: number; y: number }>;
}

const OP_CASES = (golden as unknown as { ops: OpCase[] }).ops;
const LAYOUT_CASES = (golden as unknown as { layout: LayoutCase[] }).layout;

describe('workflow ops — shared golden (AC-W8, TS 侧)', () => {
  it('golden 夹具非空', () => {
    expect(OP_CASES.length).toBeGreaterThan(40);
    expect(LAYOUT_CASES.length).toBeGreaterThan(5);
  });

  it.each(OP_CASES.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const result = applyOps(c.doc, c.ops);
    expect(result.ok).toBe(c.expect.ok);
    if (c.expect.ok && result.ok) {
      expect(result.doc).toEqual(c.expect.doc);
      if (c.expect.structural !== undefined) expect(result.structural).toBe(c.expect.structural);
    } else if (!result.ok) {
      expect(result.failure.reason).toBe(c.expect.reason);
      if (c.expect.opIndex !== undefined) expect(result.failure.opIndex).toBe(c.expect.opIndex);
    }
  });

  it.each(LAYOUT_CASES.map((c) => [c.name, c] as const))('layout: %s', (_name, c) => {
    expect(layoutLevels(c.doc)).toEqual(c.expect);
  });
});

describe('workflow ops — 前端专用纯函数', () => {
  const base = OP_CASES[0]!.doc;

  it('不修改入参（整批原子，输入不可变）', () => {
    const before = JSON.stringify(base);
    applyOps(base, [{ op: 'delete_edge', id: 'does-not-exist', expectRev: 1 }]);
    expect(JSON.stringify(base)).toBe(before);
  });

  it('wouldCreateCycle：自环与回边被预判', () => {
    const chain = LAYOUT_CASES.find((c) => c.name === 'linear_chain')!.doc;
    expect(wouldCreateCycle(chain, 'b', 'b')).toBe(true);
    expect(wouldCreateCycle(chain, 'c', 'b')).toBe(true);
    expect(wouldCreateCycle(chain, 'b', 'c')).toBe(false);
    expect(wouldCreateCycle(chain, 'c', 'a')).toBe(true);
  });

  it('invertOps：应用再逆应用后内容与应用前相等（增删改移动）', () => {
    const step = makeNode('step', { x: 10, y: 20 }, '同业对比');
    const start = base.nodes.find((n) => n.kind === 'start')!;
    const first = base.nodes.find((n) => n.kind === 'step')!;
    const ops: WorkflowOp[] = [
      { op: 'add_node', node: step },
      { op: 'add_edge', edge: makeEdge(start.id, step.id) },
      { op: 'update_node', id: first.id, expectRev: first.rev, patch: { title: '改名', config: { prompt: null } } },
      { op: 'move_node', id: first.id, position: { x: 999, y: 1 } },
      { op: 'delete_node', id: base.nodes.find((n) => n.kind === 'gate')!.id, expectRev: 1 },
    ];
    const applied = applyOps(base, ops);
    expect(applied.ok).toBe(true);
    if (!applied.ok) return;
    const inverse = invertOps(base, ops);
    const restored = applyOps(applied.doc, inverse);
    expect(restored.ok).toBe(true);
    if (!restored.ok) return;
    expect(contentSignature(restored.doc)).toBe(contentSignature(base));
    expect(restored.doc.nodes.find((n) => n.id === first.id)?.position).toEqual(first.position);
  });

  it('diffOps：相同文档没有差分；策略差分可往返', () => {
    expect(diffOps(base, base)).toEqual([]);
    const next = applyOps(base, [{ op: 'set_policy', patch: { maxTurns: 12 } }]);
    expect(next.ok).toBe(true);
    if (!next.ok) return;
    const back = applyOps(next.doc, diffOps(next.doc, base));
    expect(back.ok && back.doc.policy.maxTurns).toBe(base.policy.maxTurns);
  });

  it('cloneNode：新 id、平移、rev 归 1', () => {
    const source = base.nodes.find((n) => n.kind === 'step')!;
    const copy = cloneNode(source);
    expect(copy.id).not.toBe(source.id);
    expect(copy.rev).toBe(1);
    expect(copy.position.x).toBe(source.position.x + 40);
  });

  it('makeNode 生成合法 id（n_ + 8 位 base36）', () => {
    expect(makeNode('gate', { x: 0, y: 0 }).id).toMatch(/^n_[0-9a-z]{8}$/);
    expect(makeEdge('a', 'b').id).toMatch(/^e_[0-9a-z]{8}$/);
  });
});
