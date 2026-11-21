import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AssistPanel from '@/components/workflow/AssistPanel';
import { ApiError } from '@/lib/api';
import {
  assistReducer,
  initialAssistState,
  markInterrupted,
  messageFromChat,
  type AssistEvent,
  type AssistState,
} from '@/lib/workflow/assistState';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { ChatMessage, TemplateDetail, WorkflowDoc } from '@/lib/workflow/types';

/** `noUncheckedIndexedAccess`：先断言存在再收窄，失败时给出可读的报错。 */
function must<T>(value: T | undefined | null): T {
  expect(value).toBeDefined();
  expect(value).not.toBeNull();
  return value as T;
}


const workflowsApi = vi.hoisted(() => ({
  assist: vi.fn(),
  assistToken: vi.fn(),
  assistEventsUrl: vi.fn(() => 'http://sse.test/events'),
  assistCancel: vi.fn(),
  chat: vi.fn(),
  clearChat: vi.fn(),
  validate: vi.fn(),
  get: vi.fn(),
  ops: vi.fn(),
}));

vi.mock('@/lib/api.admin', () => ({ adminApi: { workflows: workflowsApi } }));
vi.mock('@/lib/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/api')>();
  return { ...original, api: { ...original.api, capabilities: vi.fn(async () => ({ agentMode: 'mock' })) } };
});

type Listener = (event: MessageEvent<string>) => void;
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, Listener[]>();
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {}
  emit(type: string, payload: Record<string, unknown>, seq: number) {
    const frame = { data: JSON.stringify(payload), lastEventId: String(seq) } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) listener(frame);
  }
}

function doc(): WorkflowDoc {
  return {
    schemaVersion: 1,
    nodes: [
      { id: 'n_start', kind: 'start', title: '开始', position: { x: 0, y: 0 }, rev: 1, config: {} },
      { id: 'n_end', kind: 'end', title: '提交', position: { x: 600, y: 0 }, rev: 1, config: {} },
    ],
    edges: [{ id: 'e_1', source: 'n_start', target: 'n_end', label: '', rev: 1 }],
    policy: { maxTurns: null, maxDurationMinutes: null, progressMarkers: true },
    viewport: { x: 0, y: 0, zoom: 1 },
  };
}

function detail(builtin = false): TemplateDetail {
  return {
    meta: {
      id: 'wf_1',
      name: '副本',
      description: '',
      isDefault: false,
      isBuiltin: builtin,
      status: 'active',
      latestVersion: null,
      stepCount: 0,
      dirty: false,
      updatedAt: '2026-01-01T00:00:00Z',
      updatedByName: null,
      runs30d: 0,
    },
    draft: {
      doc: doc(),
      revision: 3,
      validation: { issues: [], errorCount: 0, warningCount: 0, publishable: true, blank: false },
      dirty: false,
    },
    versions: [],
  };
}

const NEW_STEP = {
  id: 'n_new00001',
  kind: 'step' as const,
  title: '同业对比',
  position: { x: 300, y: 0 },
  rev: 1,
  config: { prompt: '对比同业' },
};
const OPS = [
  { op: 'add_node', node: NEW_STEP },
  { op: 'add_node', node: { ...NEW_STEP, id: 'n_gate0001', kind: 'gate', title: '检查点', config: {} } },
  { op: 'add_edge', edge: { id: 'e_2', source: 'n_start', target: 'n_new00001', label: '', rev: 1 } },
];

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AssistPanel />
    </QueryClientProvider>,
  );
}

function ev(type: AssistEvent['type'], payload: Record<string, unknown>, seq = 1): AssistEvent {
  return { type, payload, seq };
}

function run(state: AssistState, events: AssistEvent[]): AssistState {
  return events.reduce((acc, event) => assistReducer(acc, { type: 'event', event }), state);
}

describe('assistReducer（纯状态机）', () => {
  const sent = assistReducer(initialAssistState, { type: 'send', localId: 'u1', text: '加一个同业对比步骤' });

  it('一次完整对话：start → text → ops → reply → end', () => {
    const state = run({ ...sent, assistId: 'as_1' }, [
      ev('assist.start', { assistId: 'as_1' }),
      ev('assist.text', { delta: '正在分析…' }),
      ev('assist.ops', { revision: 4, ops: OPS, summary: { added: 2, updated: 0, deleted: 0, edges: 1 }, validation: { errorCount: 0, warningCount: 1 } }),
      ev('assist.reply', { reply: '已新增', openQuestions: ['要不要并行？'], messageId: 'm1' }),
      ev('assist.end', { state: 'done', applied: true }),
    ]);
    expect(state.busy).toBe(false);
    expect(state.thinking).toBe('');
    const last = must(state.messages.at(-1));
    expect(last.content).toBe('已新增');
    expect(last.openQuestions).toEqual(['要不要并行？']);
    expect(last.applied?.applied).toBe(true);
    expect(last.applied?.summary.added).toBe(2);
    expect(last.applied?.affectedIds).toEqual(['n_new00001', 'n_gate0001']);
    expect(last.applied?.validation).toEqual({ errorCount: 0, warningCount: 1 });
  });

  it('assist.round 清空思考文字并标记第 2 轮', () => {
    const state = run(sent, [ev('assist.text', { delta: 'abc' }), ev('assist.round', { round: 2, reason: 'invalid_ops' })]);
    expect(state.round).toBe(2);
    expect(state.thinking).toBe('');
  });

  it('assist.conflict 产生冲突卡，保留原文以便重试', () => {
    const state = run(sent, [ev('assist.conflict', { entityIds: ['n_a'] }), ev('assist.end', { state: 'done', applied: false })]);
    const card = must(state.messages.at(-1));
    expect(card.kind).toBe('conflict');
    expect(card.retryText).toBe('加一个同业对比步骤');
    expect(card.conflictIds).toEqual(['n_a']);
    expect(state.busy).toBe(false);
  });

  it('失败且没有回复时补一条原因，草稿提示 applied=false', () => {
    const state = run(sent, [ev('assist.end', { state: 'done', applied: false, reason: 'invalid_ops' })]);
    const last = must(state.messages.at(-1));
    expect(last.kind).toBe('error');
    expect(last.content).toBe('invalid_ops');
  });

  it('取消不追加提示；清空保留"不可用"标记', () => {
    const cancelled = run(sent, [ev('assist.end', { state: 'cancelled', applied: false })]);
    expect(cancelled.messages).toHaveLength(1);
    const unavailable = assistReducer(initialAssistState, { type: 'failed', error: 'x', unavailable: true });
    expect(assistReducer(unavailable, { type: 'cleared' }).unavailable).toBe(true);
  });

  it('最后一条是用户消息（无助手回复）⇒ 标记被中断，可重试', () => {
    const rows: ChatMessage[] = [
      { id: 1, role: 'user', content: '重排', applied: null, assistId: 'as_9', createdAt: 't' },
    ];
    const messages = markInterrupted(rows.map(messageFromChat));
    expect(must(messages.at(-1)).kind).toBe('interrupted');
    expect(must(messages.at(-1)).retryText).toBe('重排');
    const loaded = assistReducer(initialAssistState, { type: 'loaded', items: rows });
    expect(loaded.messages.at(-1)?.kind).toBe('interrupted');
  });

  it('历史助手消息的 applied 还原为变更卡片（无撤销句柄）', () => {
    const row: ChatMessage = {
      id: 2,
      role: 'assistant',
      content: '好了',
      applied: { ops: OPS, summary: { added: 2, updated: 0, deleted: 0, edges: 1 }, revision: 4, applied: true },
      assistId: 'as_1',
      createdAt: 't',
    };
    const message = messageFromChat(row);
    expect(message.applied?.undoHandle).toBeNull();
    expect(message.applied?.summary.added).toBe(2);
  });

  it('消息数不超过 100 条', () => {
    let state = initialAssistState;
    for (let i = 0; i < 130; i += 1) state = assistReducer(state, { type: 'send', localId: `u${i}`, text: `t${i}` });
    expect(state.messages.length).toBeLessThanOrEqual(100);
  });
});

describe('AssistPanel', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
    Object.values(workflowsApi).forEach((fn) => fn.mockReset());
    workflowsApi.assistEventsUrl.mockReturnValue('http://sse.test/events');
    workflowsApi.chat.mockResolvedValue({ items: [] });
    workflowsApi.assistToken.mockResolvedValue({ token: 't', expiresIn: 60 });
    workflowsApi.validate.mockResolvedValue({ issues: [], errorCount: 0, warningCount: 1, publishable: true, blank: false });
    useWorkflowStore.getState().reset();
    useWorkflowStore.getState().load(detail());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function sendMessage(text: string) {
    fireEvent.change(screen.getByLabelText('发给 AI 助手的消息'), { target: { value: text } });
    await act(async () => {
      fireEvent.keyDown(screen.getByLabelText('发给 AI 助手的消息'), { key: 'Enter' });
    });
  }

  it('发送 → 变更卡片 → 撤销这次修改（只对本地/服务端各一次，不重复应用 assist.ops）', async () => {
    workflowsApi.assist.mockResolvedValue({ assistId: 'as_1' });
    workflowsApi.ops.mockResolvedValue({ revision: 5, validation: {}, changed: true });
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    await sendMessage('在估值前加同业对比');

    await waitFor(() => expect(workflowsApi.assist).toHaveBeenCalledWith('wf_1', { message: '在估值前加同业对比', baseRevision: 3 }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const source = must(FakeEventSource.instances[0]);
    act(() => {
      source.emit('assist.start', { assistId: 'as_1' }, 1);
      source.emit('assist.text', { delta: '分析中' }, 2);
    });
    expect(screen.getByTestId('assist-thinking')).toBeInTheDocument();
    act(() => {
      source.emit('assist.ops', { revision: 4, ops: OPS, summary: { added: 2, updated: 0, deleted: 0, edges: 1 }, validation: { errorCount: 0, warningCount: 1 } }, 3);
      source.emit('assist.reply', { reply: '已新增同业对比', openQuestions: [], messageId: 'm1' }, 4);
      source.emit('assist.end', { state: 'done', applied: true }, 5);
    });

    // 服务端已写入：本地只同步，不会向服务端再发这批 op。
    expect(workflowsApi.ops).not.toHaveBeenCalled();
    expect(useWorkflowStore.getState().doc?.nodes.some((node) => node.id === 'n_new00001')).toBe(true);
    expect(useWorkflowStore.getState().revision).toBe(4);
    const card = await screen.findByTestId('assist-change-card');
    expect(card.textContent).toContain('新增 1 步');
    expect(card.textContent).toContain('1 检查点');
    expect(card.textContent).toContain('1 条连线');
    expect(card.textContent).toContain('⛔0 ⚠1');

    fireEvent.click(screen.getByRole('button', { name: /撤销这次修改/ }));
    expect(useWorkflowStore.getState().doc?.nodes.some((node) => node.id === 'n_new00001')).toBe(false);
    expect(screen.getByText('已撤销')).toBeInTheDocument();
  });

  it('【定位】选中并高亮受影响节点', async () => {
    workflowsApi.assist.mockResolvedValue({ assistId: 'as_1' });
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    await sendMessage('加步骤');
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    act(() => {
      must(FakeEventSource.instances[0]).emit('assist.ops', { revision: 4, ops: OPS, summary: { added: 2, updated: 0, deleted: 0, edges: 1 } }, 1);
      must(FakeEventSource.instances[0]).emit('assist.reply', { reply: 'ok', messageId: 'm' }, 2);
    });
    fireEvent.click(await screen.findByRole('button', { name: '定位' }));
    expect(useWorkflowStore.getState().selection.nodes).toEqual(['n_new00001', 'n_gate0001']);
  });

  it('冲突卡 + 【基于最新画布重试】重发同一条消息', async () => {
    workflowsApi.assist.mockResolvedValue({ assistId: 'as_1' });
    workflowsApi.get.mockResolvedValue(detail());
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    await sendMessage('改一下');
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    act(() => {
      must(FakeEventSource.instances[0]).emit('assist.conflict', { entityIds: ['n_a'] }, 1);
      must(FakeEventSource.instances[0]).emit('assist.end', { state: 'done', applied: false }, 2);
    });
    expect(await screen.findByTestId('assist-conflict')).toBeInTheDocument();
    workflowsApi.assist.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /基于最新画布重试/ }));
    await waitFor(() => expect(workflowsApi.assist).toHaveBeenCalledTimes(1));
    expect(must(must(workflowsApi.assist.mock.calls[0])[1]).message).toBe('改一下');
  });

  it('引擎不可用（409 workflow_assist_unavailable）⇒ 禁用输入并给出诊断入口', async () => {
    workflowsApi.assist.mockRejectedValue(
      new ApiError(409, { error: { code: 'workflow_assist_unavailable', message: 'x', requestId: 'r', detail: {} } }),
    );
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    await sendMessage('加步骤');
    expect(await screen.findByTestId('assist-unavailable')).toBeInTheDocument();
    expect(screen.getByLabelText('发给 AI 助手的消息')).toBeDisabled();
    expect(screen.getByRole('link', { name: '查看能力诊断' })).toHaveAttribute('href', '/capabilities');
  });

  it('内置只读模板 ⇒ 输入框与快捷提示禁用', async () => {
    useWorkflowStore.getState().load(detail(true));
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    expect(screen.getByLabelText('发给 AI 助手的消息')).toBeDisabled();
    expect(screen.getByRole('button', { name: '整理布局' })).toBeDisabled();
  });

  it('选中的节点作为 focusNodeIds 随消息发送；Shift+Enter 不发送', async () => {
    workflowsApi.assist.mockResolvedValue({ assistId: 'as_1' });
    useWorkflowStore.getState().setSelection({ nodes: ['n_end'], edges: [] });
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('发给 AI 助手的消息'), { target: { value: '把这一步改深' } });
    fireEvent.keyDown(screen.getByLabelText('发给 AI 助手的消息'), { key: 'Enter', shiftKey: true });
    expect(workflowsApi.assist).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.keyDown(screen.getByLabelText('发给 AI 助手的消息'), { key: 'Enter' });
    });
    await waitFor(() => expect(workflowsApi.assist).toHaveBeenCalled());
    expect(must(must(workflowsApi.assist.mock.calls[0])[1]).focusNodeIds).toEqual(['n_end']);
  });

  it('历史里最后一条是用户消息 ⇒ 显示"上次回复被中断，可重试"', async () => {
    workflowsApi.chat.mockResolvedValue({
      items: [{ id: 1, role: 'user', content: '重排', applied: null, assistId: 'as_9', createdAt: 't' }],
    });
    renderPanel();
    expect(await screen.findByTestId('assist-interrupted')).toBeInTheDocument();
    expect(screen.getByText('上次回复被中断，可重试。')).toBeInTheDocument();
  });

  it('字数超过 2000 不可发送并显示计数', async () => {
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('发给 AI 助手的消息'), { target: { value: 'a'.repeat(2001) } });
    expect(screen.getByText('2001/2000')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
  });

  it('可访问性结构契约：面板有名称，每个按钮与输入都有可访问名（AC-W35）', async () => {
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    expect(screen.getByRole('complementary', { name: 'AI 流程设计助手' })).toBeInTheDocument();
    for (const button of screen.getAllByRole('button')) {
      const name = (button.textContent ?? '').trim() || button.getAttribute('aria-label') || button.getAttribute('title');
      expect(name, button.outerHTML).toBeTruthy();
    }
    for (const input of screen.getAllByRole('textbox')) expect(input).toHaveAccessibleName();
    for (const element of document.querySelectorAll('[tabindex]')) {
      expect(Number(element.getAttribute('tabindex'))).toBeLessThanOrEqual(0);
    }
  });

  it('助手面板没有任何发布 / 设为默认的入口（AC-W25）', async () => {
    renderPanel();
    await waitFor(() => expect(workflowsApi.chat).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /发布|设为默认/ })).toBeNull();
  });
});
