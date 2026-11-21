import { act, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { buildDeleteOps, buildDisconnectOps, buildLayoutOps } from '@/components/workflow/useCanvasActions';
import { connectionVerdict, navigationOrder, planKeys } from '@/lib/workflow/canvasModel';
import { layoutLevels } from '@/lib/workflow/layout';
import { useWorkflowStore } from '@/lib/workflow/store';

import { makeWorkflowsApi, sampleDetail, sampleDoc } from './workflowHarness';

const workflowsApi = vi.hoisted(() => ({}) as ReturnType<typeof makeWorkflowsApi>);
vi.mock('@/lib/api.admin', () => ({ adminApi: { workflows: workflowsApi } }));

// 首次 import xyflow 在 jsdom 里要十几秒：放进 beforeAll，不占用例超时。
beforeAll(async () => {
  await import('@/components/workflow/WorkflowCanvas');
}, 120_000);

// xyflow 需要 DOMMatrixReadOnly 与可度量的元素；jsdom 都没有。
beforeEach(() => {
  Object.assign(workflowsApi, makeWorkflowsApi());
  if (!('DOMMatrixReadOnly' in globalThis)) {
    class Matrix {
      m22 = 1;
      constructor(readonly transform = '') {}
    }
    (globalThis as unknown as Record<string, unknown>).DOMMatrixReadOnly = Matrix;
  }
  useWorkflowStore.getState().reset();
  useWorkflowStore.getState().load(sampleDetail());
});

afterEach(() => {
  useWorkflowStore.getState().reset();
});

describe('connectionVerdict —— 连线时即时预判（AC-W27）', () => {
  const doc = sampleDoc();

  it('rejects_self_loop', () => {
    expect(connectionVerdict(doc, 'n_s1', 'n_s1')).toEqual({ ok: false, reason: 'self_loop' });
  });

  it('rejects_duplicate_edge', () => {
    expect(connectionVerdict(doc, 'n_s1', 'n_s2')).toEqual({ ok: false, reason: 'duplicate_edge' });
  });

  it('rejects_edge_that_closes_a_cycle', () => {
    expect(connectionVerdict(doc, 'n_g1', 'n_s1')).toEqual({ ok: false, reason: 'cycle' });
  });

  it('rejects_note_start_input_and_end_output', () => {
    expect(connectionVerdict(doc, 'n_note', 'n_s1')).toEqual({ ok: false, reason: 'note' });
    expect(connectionVerdict(doc, 'n_s1', 'n_start')).toEqual({ ok: false, reason: 'start_input' });
    expect(connectionVerdict(doc, 'n_end', 'n_s1')).toEqual({ ok: false, reason: 'end_output' });
  });

  it('accepts_a_valid_parallel_link', () => {
    expect(connectionVerdict(doc, 'n_s2', 'n_s3')).toEqual({ ok: true });
  });
});

describe('画布手势 → op 的纯函数', () => {
  const doc = sampleDoc();

  it('assigns_plan_keys_by_level_then_document_order', () => {
    const keys = planKeys(doc);
    expect(keys.n_s1?.key).toBe('S1');
    expect(keys.n_s2?.key).toBe('S2');
    expect(keys.n_s3?.key).toBe('S3');
    expect(keys.n_g1?.key).toBe('G1');
    expect(keys.n_start).toBeUndefined();
  });

  it('navigates_in_level_order_with_notes_last', () => {
    const order = navigationOrder(doc);
    expect(order[0]).toBe('n_start');
    expect(order[order.length - 1]).toBe('n_note');
  });

  it('never_deletes_anchor_nodes_and_skips_cascaded_edges', () => {
    const ops = buildDeleteOps(doc, ['n_start', 'n_s1'], ['e_2', 'e_6']);
    expect(ops.map((op) => op.op)).toEqual(['delete_edge', 'delete_node']);
    expect(ops[0]).toMatchObject({ id: 'e_6' });
    expect(ops[1]).toMatchObject({ id: 'n_s1', expectRev: 1 });
  });

  it('disconnects_every_edge_of_a_node', () => {
    const ids = buildDisconnectOps(doc, 'n_s1').map((op) => (op.op === 'delete_edge' ? op.id : ''));
    expect(ids.sort()).toEqual(['e_1', 'e_2', 'e_3']);
  });

  it('layout_ops_match_layoutLevels_points', () => {
    const points = layoutLevels(doc);
    const ops = buildLayoutOps(doc);
    expect(ops.length).toBeGreaterThan(0);
    for (const op of ops) {
      if (op.op === 'move_node') expect(op.position).toEqual(points[op.id]);
    }
    expect(ops.some((op) => op.op === 'move_node' && op.id === 'n_note')).toBe(false);
  });
});

async function renderCanvas() {
  const { default: WorkflowCanvas } = await import('@/components/workflow/WorkflowCanvas');
  render(
    <div style={{ width: 1000, height: 600 }}>
      <WorkflowCanvas />
    </div>,
  );
}

describe('WorkflowCanvas', () => {
  it('renders_every_node_with_its_plan_key', async () => {
    await renderCanvas();
    for (const id of ['n_start', 'n_s1', 'n_s2', 'n_s3', 'n_g1', 'n_end', 'n_note']) {
      expect(await screen.findByTestId(`wf-node-${id}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('wf-node-n_s1')).toHaveTextContent('S1');
    expect(screen.getByTestId('wf-node-n_g1')).toHaveTextContent('G1');
  });

  it('adds_a_step_when_clicking_the_palette_button', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s1');
    fireEvent.click(screen.getByTestId('wf-palette-step'));
    const { doc } = useWorkflowStore.getState();
    expect(doc?.nodes.filter((node) => node.kind === 'step')).toHaveLength(4);
    expect(useWorkflowStore.getState().selection.nodes).toHaveLength(1);
  });

  it('adds_a_gate_via_drag_and_drop', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s1');
    const region = screen.getByTestId('wf-canvas-surface');
    // jsdom 没有 DragEvent：坐标要手工挂到合成事件上。
    const drop = createEvent.drop(region, { dataTransfer: { getData: () => 'gate' } });
    Object.assign(drop, { clientX: 400, clientY: 300 });
    fireEvent(region, drop);
    expect(useWorkflowStore.getState().doc?.nodes.filter((node) => node.kind === 'gate')).toHaveLength(2);
  });

  it('deletes_the_selected_node_with_the_Delete_key_and_undo_restores_it', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s3');
    act(() => useWorkflowStore.getState().setSelection({ nodes: ['n_s3'], edges: [] }));
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'Delete' });
    expect(useWorkflowStore.getState().doc?.nodes.some((node) => node.id === 'n_s3')).toBe(false);
    expect(useWorkflowStore.getState().doc?.edges.some((edge) => edge.target === 'n_s3')).toBe(false);
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'z', ctrlKey: true });
    expect(useWorkflowStore.getState().doc?.nodes.some((node) => node.id === 'n_s3')).toBe(true);
  });

  it('refuses_to_delete_anchor_nodes', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_start');
    act(() => useWorkflowStore.getState().setSelection({ nodes: ['n_start'], edges: [] }));
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'Delete' });
    expect(useWorkflowStore.getState().doc?.nodes.some((node) => node.id === 'n_start')).toBe(true);
  });

  it('moves_focus_between_nodes_with_arrow_keys_in_level_order', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_start');
    act(() => useWorkflowStore.getState().setSelection({ nodes: ['n_start'], edges: [] }));
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'ArrowRight' });
    expect(useWorkflowStore.getState().selection.nodes).toEqual(['n_s1']);
  });

  it('opens_inline_rename_on_F2_and_commits_a_title_update', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s1');
    act(() => useWorkflowStore.getState().setSelection({ nodes: ['n_s1'], edges: [] }));
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'F2' });
    const input = await screen.findByRole('textbox', { name: '重命名' });
    fireEvent.change(input, { target: { value: '事实底座 v2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(useWorkflowStore.getState().doc?.nodes.find((node) => node.id === 'n_s1')?.title).toBe('事实底座 v2'));
  });

  it('opens_search_with_ctrl_f_and_selects_a_match', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s1');
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'f', ctrlKey: true });
    const box = await screen.findByRole('textbox', { name: '按标题或键搜索节点' });
    fireEvent.change(box, { target: { value: '风险' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(useWorkflowStore.getState().selection.nodes).toEqual(['n_s3']));
  });

  it('announces_changes_in_a_polite_live_region', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s1');
    act(() => useWorkflowStore.setState({ announce: '已新增步骤「X」' }));
    const live = screen.getByTestId('wf-live');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveTextContent('已新增步骤「X」');
  });

  it('blocks_every_write_gesture_on_a_read_only_template', async () => {
    useWorkflowStore.getState().load(sampleDetail({ meta: { isBuiltin: true } }));
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s3');
    expect(screen.getByTestId('wf-palette-step')).toBeDisabled();
    act(() => useWorkflowStore.getState().setSelection({ nodes: ['n_s3'], edges: [] }));
    fireEvent.keyDown(screen.getByTestId('wf-canvas-surface'), { key: 'Delete' });
    expect(useWorkflowStore.getState().doc?.nodes.some((node) => node.id === 'n_s3')).toBe(true);
  });

  it('pulses_highlighted_nodes_from_ai_changes', async () => {
    await renderCanvas();
    await screen.findByTestId('wf-node-n_s2');
    act(() => useWorkflowStore.getState().setHighlight(['n_s2'], 10_000));
    await waitFor(() => expect(screen.getByTestId('wf-node-n_s2').className).toContain('wf-pulse'));
  });
});
