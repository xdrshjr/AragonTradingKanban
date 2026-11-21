'use client';

import { useCallback, useEffect, useReducer, useRef } from 'react';

import { ApiError } from '../api';
import { adminApi } from '../api.admin';
import { retryDelay } from '../sse';
import {
  ASSIST_EVENT_TYPES,
  assistReducer,
  initialAssistState,
  type AssistEvent,
  type AssistEventType,
  type AssistState,
  type AssistUiMessage,
} from './assistState';
import { assistCopy } from './copy.assist';
import { useWorkflowStore } from './store';
import type { ValidationReport, WorkflowOp } from './types';

/**
 * AI 助手的 SSE 客户端 + 面板控制器（spec §3.8.6 / §3.10.7）。
 *
 * 流程：`POST /assist` → `{assistId}` → `POST …/events-token`（60 s，绑定 assistId）→ `EventSource`
 * （重连带 `?after=<lastSeq>`，服务端从环形缓冲续传）→ 事件进 reducer。
 *
 * **关键纪律**：`assist.ops` 到达时服务端**已经**把整批 op 原子写进草稿，前端只调
 * `store.applyAssistResult`（本地同步 + 高亮 + 撤销栈登记），**绝不**再 `dispatch` 一遍。
 */

export interface AssistSubscribeOptions {
  after?: number;
  onEvent: (event: AssistEvent) => void;
  onError?: (error: Error) => void;
  maxRetries?: number;
}

const DEFAULT_MAX_RETRIES = 3;

/** 订阅一次助手任务。返回退订函数；`assist.end` 是终态，收到即关流。 */
export function subscribeToAssist(assistId: string, options: AssistSubscribeOptions): () => void {
  const { onEvent, onError, maxRetries = DEFAULT_MAX_RETRIES } = options;
  let after = options.after ?? 0;
  let attempt = 0;
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const close = () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    source?.close();
    source = null;
  };

  const handle = (type: AssistEventType, raw: MessageEvent<string>) => {
    if (closed || type === 'ping') return;
    let payload: Record<string, unknown> = {};
    try {
      payload = raw.data ? (JSON.parse(raw.data) as Record<string, unknown>) : {};
    } catch {
      payload = { raw: raw.data };
    }
    const seq = Number.parseInt(raw.lastEventId || '0', 10) || after;
    // 重连会从 `after` 续传：按 seq 去重。
    if (seq > 0 && seq <= after) return;
    after = Math.max(after, seq);
    attempt = 0;
    onEvent({ seq, type, payload });
    if (type === 'assist.end') close();
  };

  const retry = (error: Error) => {
    source?.close();
    source = null;
    if (closed) return;
    if (attempt >= maxRetries) {
      close();
      onError?.(error);
      return;
    }
    const delay = retryDelay(attempt);
    attempt += 1;
    retryTimer = setTimeout(() => void connect(), delay);
  };

  const connect = async () => {
    if (closed) return;
    let token: string;
    try {
      token = (await adminApi.workflows.assistToken(assistId)).token;
    } catch (error) {
      if (closed) return;
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        close();
        onError?.(error);
        return;
      }
      retry(error instanceof Error ? error : new Error(assistCopy.genericError));
      return;
    }
    if (closed) return;
    source?.close();
    source = new EventSource(adminApi.workflows.assistEventsUrl(assistId, token, after > 0 ? after : undefined));
    for (const type of ASSIST_EVENT_TYPES) {
      source.addEventListener(type, (event) => handle(type, event as MessageEvent<string>));
    }
    source.onerror = () => retry(new Error(assistCopy.genericError));
  };

  void connect();
  return close;
}

/** 把 `assist.ops` 的校验计数合并进 store 当前报告（完整报告随后由 `validate` 刷新）。 */
function mergedValidation(current: ValidationReport | null, payload: Record<string, unknown>): ValidationReport {
  const raw = (payload.validation ?? {}) as Partial<ValidationReport>;
  const errorCount = typeof raw.errorCount === 'number' ? raw.errorCount : (current?.errorCount ?? 0);
  const warningCount = typeof raw.warningCount === 'number' ? raw.warningCount : (current?.warningCount ?? 0);
  return {
    issues: Array.isArray(raw.issues) ? raw.issues : (current?.issues ?? []),
    errorCount,
    warningCount,
    publishable: errorCount === 0,
    blank: current?.blank ?? false,
  };
}

/** 服务端返回的错误 → 面板提示（引擎不可用时同时禁用输入框）。 */
export function describeAssistError(error: unknown): { text: string; unavailable: boolean } {
  if (error instanceof ApiError) {
    if (error.code === 'workflow_assist_unavailable') return { text: assistCopy.unavailable.body, unavailable: true };
    if (error.code === 'workflow_assist_busy') return { text: assistCopy.busy, unavailable: false };
    if (error.code === 'workflow_builtin_readonly') return { text: assistCopy.readOnly, unavailable: false };
    if (error.status === 429) return { text: assistCopy.rateLimited, unavailable: false };
    if (error.status === 402) return { text: assistCopy.budget, unavailable: false };
    return { text: error.message || assistCopy.genericError, unavailable: false };
  }
  return { text: assistCopy.genericError, unavailable: false };
}

export interface UseAssistStreamResult {
  state: AssistState;
  send: (text: string, focusNodeIds?: string[]) => Promise<void>;
  cancel: () => Promise<void>;
  clear: () => Promise<void>;
  undo: (message: AssistUiMessage) => void;
  locate: (ids: string[]) => void;
}

export function useAssistStream(): UseAssistStreamResult {
  const [state, dispatch] = useReducer(assistReducer, initialAssistState);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const assistIdRef = useRef<string | null>(null);
  const templateId = useWorkflowStore((store) => store.templateId);

  const teardown = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);
  useEffect(() => teardown, [teardown]);

  // 载入历史对话（当前管理员，每模板 ≤100 条）。
  useEffect(() => {
    if (!templateId) return;
    let cancelled = false;
    adminApi.workflows
      .chat(templateId)
      .then((response) => {
        if (!cancelled) dispatch({ type: 'loaded', items: response.items });
      })
      .catch(() => {
        if (!cancelled) dispatch({ type: 'loaded', items: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  // 画布的 `aiBusy` 与面板同源（右上角"AI 正在设计…"胶囊读它）。
  useEffect(() => {
    useWorkflowStore.getState().setAiBusy(state.busy);
  }, [state.busy]);

  const onEvent = useCallback((event: AssistEvent) => {
    let undoHandle: string | null = null;
    if (event.type === 'assist.ops') {
      const store = useWorkflowStore.getState();
      const ops = Array.isArray(event.payload.ops) ? (event.payload.ops as WorkflowOp[]) : [];
      const revision = typeof event.payload.revision === 'number' ? event.payload.revision : store.revision;
      undoHandle =
        store.applyAssistResult({ ops, revision, validation: mergedValidation(store.validation, event.payload) }) || null;
      // 校验报告的明细以服务端为准：补一次（不走 `applyServerState`，那会清空撤销句柄）。
      void adminApi.workflows
        .validate(store.templateId)
        .then((report) => useWorkflowStore.setState({ validation: report }))
        .catch(() => undefined);
    }
    if (event.type === 'assist.conflict') void useWorkflowStore.getState().refreshDetail();
    if (event.type === 'assist.end') {
      teardown();
      assistIdRef.current = null;
    }
    dispatch({ type: 'event', event, undoHandle });
  }, [teardown]);

  const send = useCallback(
    async (text: string, focusNodeIds: string[] = []) => {
      const store = useWorkflowStore.getState();
      const trimmed = text.trim();
      if (!trimmed || !store.templateId) return;
      teardown();
      dispatch({ type: 'send', localId: `u-${Date.now()}`, text: trimmed });
      try {
        // 先把本地未确认的写入送达服务端：助手基于"服务端草稿"工作，revision 才对得上。
        await store.flush();
        const fresh = useWorkflowStore.getState();
        const { assistId } = await adminApi.workflows.assist(fresh.templateId, {
          message: trimmed,
          baseRevision: fresh.revision,
          ...(focusNodeIds.length > 0 ? { focusNodeIds } : {}),
        });
        assistIdRef.current = assistId;
        dispatch({ type: 'started', assistId });
        unsubscribeRef.current = subscribeToAssist(assistId, {
          onEvent,
          onError: (error) => {
            dispatch({ type: 'failed', error: error.message || assistCopy.genericError });
            assistIdRef.current = null;
          },
        });
      } catch (error) {
        const described = describeAssistError(error);
        dispatch({ type: 'failed', error: described.text, unavailable: described.unavailable });
      }
    },
    [onEvent, teardown],
  );

  const cancel = useCallback(async () => {
    const assistId = assistIdRef.current;
    if (!assistId) return;
    try {
      await adminApi.workflows.assistCancel(assistId);
    } catch {
      /* 任务可能已经结束：事件流会给出终态 */
    }
  }, []);

  const clear = useCallback(async () => {
    const id = useWorkflowStore.getState().templateId;
    if (!id) return;
    try {
      await adminApi.workflows.clearChat(id);
      dispatch({ type: 'cleared' });
    } catch (error) {
      dispatch({ type: 'failed', error: describeAssistError(error).text });
    }
  }, []);

  const undo = useCallback((message: AssistUiMessage) => {
    const handle = message.applied?.undoHandle;
    if (!handle) return;
    useWorkflowStore.getState().undoAssist(handle);
    dispatch({ type: 'undone', messageId: message.id });
  }, []);

  const locate = useCallback((ids: string[]) => {
    const store = useWorkflowStore.getState();
    store.setSelection({ nodes: ids, edges: [] });
    store.setHighlight(ids);
  }, []);

  return { state, send, cancel, clear, undo, locate };
}
