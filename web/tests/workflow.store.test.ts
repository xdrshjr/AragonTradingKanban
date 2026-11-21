import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import golden from '../../contracts/workflow_ops.golden.json';
import { ApiError } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import { contentSignature, makeEdge, makeNode } from '@/lib/workflow/ops';
import { hasPendingWrites, resumeSync, useWorkflowStore } from '@/lib/workflow/store';
import { MOVE_DEBOUNCE_MS, mergeNonStructural } from '@/lib/workflow/syncEngine';
import type {
  OpsResponse,
  TemplateDetail,
  ValidationReport,
  WorkflowDoc,
  WorkflowOp,
} from '@/lib/workflow/types';

const BASE = (golden as unknown as { ops: { doc: WorkflowDoc }[] }).ops[0]!.doc;
const REPORT: ValidationReport = { issues: [], errorCount: 0, warningCount: 0, publishable: true, blank: false };

function detail(overrides: Partial<TemplateDetail['meta']> = {}): TemplateDetail {
  return {
    meta: {
      id: 'wf_t',
      name: '测试流程',
      description: '',
      isDefault: false,
      isBuiltin: false,
      status: 'active',
      latestVersion: null,
      stepCount: 1,
      dirty: false,
      updatedAt: '2026-11-19T00:00:00Z',
      updatedByName: null,
      runs30d: 0,
      ...overrides,
    },
    draft: { doc: structuredClone(BASE), revision: 1, validation: REPORT, dirty: false },
    versions: [],
  };
}

function ok(revision: number): OpsResponse {
  return { revision, validation: REPORT, changed: true };
}

const store = () => useWorkflowStore.getState();
const doc = (): WorkflowDoc => store().doc as WorkflowDoc;
const startNode = () => BASE.nodes.find((n) => n.kind === 'start')!;

/** 让所有已解决的 promise 回调跑完。 */
const settle = async (): Promise<void> => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
};

let opsMock: MockInstance<typeof adminApi.workflows.ops>;

beforeEach(() => {
  vi.useFakeTimers();
  opsMock = vi.spyOn(adminApi.workflows, 'ops');
  store().reset();
  store().load(detail());
});
afterEach(() => {
  store().reset();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('dispatch：乐观应用 + 单飞队列', () => {
  it('结构性 op 立即本地生效并以 baseRevision 发送', async () => {
    opsMock.mockResolvedValue(ok(2));
    const step = makeNode('step', { x: 5, y: 5 }, '同业对比');
    expect(store().dispatch([{ op: 'add_node', node: step }])).toBe(true);
    expect(doc().nodes.some((n) => n.id === step.id)).toBe(true);
    expect(opsMock).toHaveBeenCalledTimes(1);
    expect(opsMock.mock.calls[0]![1]).toBe(1);
    await settle();
    expect(store().revision).toBe(2);
    expect(store().saveState).toBe('saved');
    expect(store().canUndo).toBe(true);
  });

  it('本地 applyOps 失败：返回 false、不发请求、播报原因', () => {
    const ghost: WorkflowOp = { op: 'delete_node', id: 'does-not-exist', expectRev: 1 };
    expect(store().dispatch([ghost])).toBe(false);
    expect(opsMock).not.toHaveBeenCalled();
    expect(store().announce).toBe('节点不存在');
  });

  it('move_node 合并 + 400ms 防抖：同一节点的多次拖动只发最后一次', async () => {
    opsMock.mockResolvedValue(ok(1));
    const id = startNode().id;
    store().dispatch([{ op: 'move_node', id, position: { x: 1, y: 1 } }]);
    store().dispatch([{ op: 'move_node', id, position: { x: 2, y: 2 } }]);
    store().dispatch([{ op: 'move_node', id, position: { x: 3, y: 3 } }]);
    expect(opsMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(MOVE_DEBOUNCE_MS + 10);
    expect(opsMock).toHaveBeenCalledTimes(1);
    expect(opsMock.mock.calls[0]![2]).toEqual([{ op: 'move_node', id, position: { x: 3, y: 3 } }]);
    expect(doc().nodes.find((n) => n.id === id)?.position).toEqual({ x: 3, y: 3 });
  });

  it('mergeNonStructural：视口只保留最后一次', () => {
    const merged = mergeNonStructural(
      [{ op: 'set_viewport', viewport: { x: 0, y: 0, zoom: 1 } }],
      [{ op: 'set_viewport', viewport: { x: 9, y: 9, zoom: 2 } }],
    );
    expect(merged).toEqual([{ op: 'set_viewport', viewport: { x: 9, y: 9, zoom: 2 } }]);
  });

  it('单飞：前一个请求未返回时，后一个结构性批次排队等待', async () => {
    let release: (value: OpsResponse) => void = () => undefined;
    opsMock.mockImplementationOnce(() => new Promise<OpsResponse>((resolve) => (release = resolve)));
    opsMock.mockResolvedValueOnce(ok(3));
    store().dispatch([{ op: 'add_node', node: makeNode('gate', { x: 0, y: 0 }) }]);
    store().dispatch([{ op: 'add_node', node: makeNode('note', { x: 9, y: 9 }) }]);
    expect(opsMock).toHaveBeenCalledTimes(1);
    expect(hasPendingWrites()).toBe(true);
    release(ok(2));
    await settle();
    expect(opsMock).toHaveBeenCalledTimes(2);
    expect(opsMock.mock.calls[1]![1]).toBe(2);
    await settle();
    expect(store().revision).toBe(3);
    expect(hasPendingWrites()).toBe(false);
  });
});

describe('409 与离线', () => {
  it('409：丢弃队列，用权威文档整体替换并提示已同步', async () => {
    const authoritative = structuredClone(BASE);
    authoritative.nodes[1]!.title = '别人改的';
    opsMock.mockRejectedValue(
      new ApiError(409, {
        error: {
          code: 'workflow_revision_conflict',
          message: 'x',
          requestId: '-',
          detail: { currentRevision: 7, doc: authoritative },
        },
      }),
    );
    store().dispatch([{ op: 'add_node', node: makeNode('step', { x: 1, y: 1 }) }]);
    await settle();
    expect(store().revision).toBe(7);
    expect(doc().nodes[1]!.title).toBe('别人改的');
    expect(doc().nodes).toHaveLength(BASE.nodes.length);
    expect(store().canUndo).toBe(false);
    expect(store().notice?.text).toContain('已与服务器同步');
    expect(hasPendingWrites()).toBe(false);
  });

  it('断网：批次留在队列里显示离线，恢复后按序重放并提示', async () => {
    const network = new ApiError(0, null);
    opsMock.mockRejectedValueOnce(network);
    opsMock.mockResolvedValue(ok(2));
    store().dispatch([{ op: 'add_node', node: makeNode('step', { x: 1, y: 1 }) }]);
    await settle();
    expect(store().saveState).toBe('offline');
    expect(hasPendingWrites()).toBe(true);
    resumeSync();
    await settle();
    expect(opsMock).toHaveBeenCalledTimes(2);
    expect(store().saveState).toBe('saved');
    expect(store().notice?.text).toContain('已同步');
  });

  it('服务端拒绝（422）：保存失败态 + 重新拉取权威草稿', async () => {
    opsMock.mockRejectedValue(new ApiError(422, { error: { code: 'workflow_invalid_ops', message: '不合法', requestId: '-', detail: {} } }));
    const getMock = vi.spyOn(adminApi.workflows, 'get').mockResolvedValue(detail());
    store().dispatch([{ op: 'add_node', node: makeNode('step', { x: 1, y: 1 }) }]);
    await settle();
    expect(store().notice?.kind).toBe('error');
    expect(getMock).toHaveBeenCalledWith('wf_t');
  });
});

describe('撤销 / 重做', () => {
  it('撤销把差分 op 当作新写入发送；重做恢复', async () => {
    opsMock.mockResolvedValue(ok(2));
    const before = contentSignature(doc());
    const step = makeNode('step', { x: 5, y: 5 }, '同业对比');
    store().dispatch([
      { op: 'add_node', node: step },
      { op: 'add_edge', edge: makeEdge(startNode().id, step.id) },
    ]);
    await settle();
    const after = contentSignature(doc());
    store().undo();
    expect(contentSignature(doc())).toBe(before);
    expect(opsMock.mock.calls.at(-1)![2].map((op: WorkflowOp) => op.op)).toEqual(['delete_edge', 'delete_node']);
    expect(store().canRedo).toBe(true);
    await settle();
    store().redo();
    expect(contentSignature(doc())).toBe(after);
    expect(store().canUndo).toBe(true);
    expect(store().canRedo).toBe(false);
  });

  it('没有历史时撤销只播报，不发请求', () => {
    store().undo();
    expect(store().announce).toBe('没有可撤销的操作');
    expect(opsMock).not.toHaveBeenCalled();
  });

  it('新的写入清空重做栈', async () => {
    opsMock.mockResolvedValue(ok(2));
    store().dispatch([{ op: 'add_node', node: makeNode('gate', { x: 0, y: 0 }) }]);
    await settle();
    store().undo();
    await settle();
    expect(store().canRedo).toBe(true);
    store().dispatch([{ op: 'add_node', node: makeNode('note', { x: 0, y: 0 }) }]);
    expect(store().canRedo).toBe(false);
  });
});

describe('只读与 AI 应用', () => {
  it('内置模板：写入被拒并提示，视口仍可本地平移但不发送', () => {
    store().load(detail({ isBuiltin: true }));
    expect(store().readOnly).toBe(true);
    expect(store().dispatch([{ op: 'add_node', node: makeNode('step', { x: 0, y: 0 }) }])).toBe(false);
    expect(store().announce).toContain('只读');
    expect(store().dispatch([{ op: 'set_viewport', viewport: { x: 5, y: 5, zoom: 1 } }])).toBe(true);
    expect(opsMock).not.toHaveBeenCalled();
  });

  it('窄屏只读：setNarrow 切换且不改变模板自身的锁定状态', () => {
    store().setNarrow(true);
    expect(store().readOnly).toBe(true);
    expect(store().lockedByTemplate).toBe(false);
    store().setNarrow(false);
    expect(store().readOnly).toBe(false);
  });

  it('applyAssistResult 对齐本地文档并返回句柄；undoAssist 把差分写回服务器', async () => {
    opsMock.mockResolvedValue(ok(6));
    const before = contentSignature(doc());
    const step = makeNode('step', { x: 7, y: 7 }, 'AI 加的步骤');
    const handle = store().applyAssistResult({
      ops: [{ op: 'add_node', node: step }],
      revision: 5,
      validation: REPORT,
    });
    expect(handle).toMatch(/^assist-/);
    expect(store().revision).toBe(5);
    expect(doc().nodes.some((n) => n.id === step.id)).toBe(true);
    expect(store().highlightIds).toEqual([step.id]);
    expect(opsMock).not.toHaveBeenCalled();
    store().undoAssist(handle);
    expect(contentSignature(doc())).toBe(before);
    expect(opsMock.mock.calls[0]![1]).toBe(5);
  });

  it('高亮在时限后自动清除；面板偏好写入 panePrefs', async () => {
    store().setHighlight(['n1'], 500);
    expect(store().highlightIds).toEqual(['n1']);
    await vi.advanceTimersByTimeAsync(600);
    expect(store().highlightIds).toEqual([]);
    store().togglePane('structure');
    expect(store().panes.structure).toBe(false);
    expect(store().panePrefs.structure).toBe(false);
    store().setDockTab('plan');
    expect(store().panes.dock).toBe(true);
    expect(store().panes.dockTab).toBe('plan');
  });
});
