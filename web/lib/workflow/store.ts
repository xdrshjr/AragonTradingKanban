/**
 * 研究流程编辑器的客户端状态（zustand，spec §3.10.5）。
 *
 * 所有写入——画布手势与 AI 应用——都经 `dispatch(ops)`：本地 `applyOps` 乐观应用 → 入 `SyncEngine`
 * 单飞队列。撤销/重做 = 把差分 op 当作新的普通写入再发一遍，所以撤销同样走原子应用与冲突检测，
 * 不存在"本地撤销了但服务器没撤销"。
 *
 * FB（画布 / 检查器 / 问题面板…）与 FC（AI 面板）只通过本文件导出的 `useWorkflowStore` 读写状态。
 */

import { create } from 'zustand';

import { adminApi } from '../api.admin';
import { copyWorkflow, describeOps, fillText } from './copy.workflow';
import { applyOps, diffOps, nodeById } from './ops';
import { SyncEngine, type SaveState } from './syncEngine';
import { loadPanePrefs, savePanePrefs, type PanePrefs } from './layout';
import type {
  DockTab,
  OpsResponse,
  RevisionConflictDetail,
  TemplateDetail,
  TemplateSummary,
  ValidationReport,
  VersionSummary,
  WorkflowDoc,
  WorkflowOp,
} from './types';

export interface Selection {
  nodes: string[];
  edges: string[];
}

export interface PanesState {
  structure: boolean;
  inspector: boolean;
  dock: boolean;
  dockTab: DockTab;
}

export type PaneName = 'structure' | 'inspector' | 'dock';

export interface Notice {
  /** 递增序号：UI 用它判断"又来了一条"。 */
  seq: number;
  kind: 'info' | 'success' | 'error';
  text: string;
}

interface HistoryEntry {
  before: WorkflowDoc;
  after: WorkflowDoc;
}

export interface DispatchOptions {
  /** 是否进入撤销栈，默认 true。撤销/重做自身与 AI 的应用传 false。 */
  record?: boolean;
}

export interface AssistResultPayload {
  ops: WorkflowOp[];
  revision: number;
  validation: ValidationReport;
}

export interface WorkflowState {
  templateId: string;
  meta: TemplateSummary | null;
  doc: WorkflowDoc | null;
  revision: number;
  versions: VersionSummary[];
  /** 内置 / 已归档 / 窄屏（<768px）⇒ 只读。 */
  readOnly: boolean;
  /** 仅由模板本身造成的只读（内置或已归档）；窄屏只读不算。 */
  lockedByTemplate: boolean;
  narrow: boolean;
  selection: Selection;
  validation: ValidationReport | null;
  saveState: SaveState;
  /** 草稿内容 ≠ 最新版本（服务端给）。 */
  dirty: boolean;
  canUndo: boolean;
  canRedo: boolean;
  aiBusy: boolean;
  highlightIds: string[];
  panes: PanesState;
  /** 用户亲手切换过的面板偏好（`undefined` = 跟随默认）；`computeInner` 的输入。 */
  panePrefs: PanePrefs;
  /** aria-live 播报文案。 */
  announce: string;
  notice: Notice | null;
  loaded: boolean;
}

export interface WorkflowActions {
  load: (detail: TemplateDetail) => void;
  dispatch: (ops: WorkflowOp[], options?: DispatchOptions) => boolean;
  undo: () => void;
  redo: () => void;
  setSelection: (selection: Selection) => void;
  setAiBusy: (busy: boolean) => void;
  /** 视口 <768px：只读查看（画布仍可平移缩放）。 */
  setNarrow: (narrow: boolean) => void;
  /** assist.ops 事件：服务端已应用，这里只对齐本地文档；返回撤销句柄。 */
  applyAssistResult: (payload: AssistResultPayload) => string;
  undoAssist: (handle: string) => void;
  setHighlight: (ids: string[], ms?: number) => void;
  togglePane: (name: PaneName) => void;
  setDockTab: (tab: DockTab) => void;
  /** 409 之后（或外部变更）用权威文档整体对齐；清空撤销栈与发送队列。 */
  applyServerState: (payload: { doc: WorkflowDoc; revision: number; validation?: ValidationReport }) => void;
  refreshDetail: () => Promise<void>;
  flush: () => Promise<void>;
  reset: () => void;
  notify: (kind: Notice['kind'], text: string) => void;
}

export type WorkflowStore = WorkflowState & WorkflowActions;

const EMPTY_SELECTION: Selection = { nodes: [], edges: [] };
const HISTORY_LIMIT = 100;
const DEFAULT_HIGHLIGHT_MS = 1600;

function initialPanes(): { panes: PanesState; panePrefs: PanePrefs } {
  const prefs = typeof window === 'undefined' ? {} : loadPanePrefs();
  return {
    panes: {
      structure: prefs.structure !== false,
      inspector: prefs.inspector !== false,
      dock: prefs.dock === true,
      dockTab: 'problems',
    },
    panePrefs: prefs,
  };
}

function initialState(): WorkflowState {
  return {
    templateId: '',
    meta: null,
    doc: null,
    revision: 1,
    versions: [],
    readOnly: true,
    lockedByTemplate: true,
    narrow: false,
    selection: EMPTY_SELECTION,
    validation: null,
    saveState: 'saved',
    dirty: false,
    canUndo: false,
    canRedo: false,
    aiBusy: false,
    highlightIds: [],
    ...initialPanes(),
    announce: '',
    notice: null,
    loaded: false,
  };
}

let undoStack: HistoryEntry[] = [];
let redoStack: HistoryEntry[] = [];
const assistHandles = new Map<string, HistoryEntry>();
let highlightTimer: ReturnType<typeof setTimeout> | null = null;
let engineRef: SyncEngine | null = null;
let noticeSeq = 0;
let assistSeq = 0;

function pruneSelection(selection: Selection, doc: WorkflowDoc): Selection {
  const nodes = selection.nodes.filter((id) => doc.nodes.some((node) => node.id === id));
  const edges = selection.edges.filter((id) => doc.edges.some((edge) => edge.id === id));
  if (nodes.length === selection.nodes.length && edges.length === selection.edges.length) return selection;
  return { nodes, edges };
}

function titleLookup(...docs: (WorkflowDoc | null)[]): (id: string) => string {
  return (id) => {
    for (const doc of docs) {
      const node = doc ? nodeById(doc, id) : undefined;
      if (node) return node.title;
    }
    return '';
  };
}

function touchedIds(ops: readonly WorkflowOp[]): string[] {
  const ids = new Set<string>();
  for (const op of ops) {
    if (op.op === 'add_node') ids.add(op.node.id);
    else if (op.op === 'update_node' || op.op === 'move_node') ids.add(op.id);
  }
  return [...ids];
}

export const useWorkflowStore = create<WorkflowStore>()((set, get) => {
  const engine = new SyncEngine({
    onState: (saveState) => set({ saveState }),
    onConfirmed: (response: OpsResponse) =>
      set((state) => ({ revision: response.revision, validation: response.validation, dirty: state.dirty || response.changed })),
    onConflict: (detail: RevisionConflictDetail | null) => {
      if (detail?.doc) {
        get().applyServerState({ doc: detail.doc, revision: detail.currentRevision });
        get().notify('info', copyWorkflow.toasts.synced);
      } else {
        void get().refreshDetail();
      }
    },
    onRejected: (error) => {
      get().notify('error', fillText(copyWorkflow.toasts.invalidOps, { message: error.message }));
      void get().refreshDetail();
    },
    onReplayed: () => get().notify('success', copyWorkflow.toasts.replayed),
  });
  engineRef = engine;

  const applyDetail = (detail: TemplateDetail, keepPanes: boolean): void => {
    undoStack = [];
    redoStack = [];
    assistHandles.clear();
    engine.configure(detail.meta.id, detail.draft.revision);
    set((state) => ({
      templateId: detail.meta.id,
      meta: detail.meta,
      doc: detail.draft.doc,
      revision: detail.draft.revision,
      versions: detail.versions,
      lockedByTemplate: detail.meta.isBuiltin || detail.meta.status === 'archived',
      readOnly: detail.meta.isBuiltin || detail.meta.status === 'archived' || state.narrow,
      validation: detail.draft.validation,
      dirty: detail.draft.dirty,
      selection: keepPanes ? pruneSelection(state.selection, detail.draft.doc) : EMPTY_SELECTION,
      canUndo: false,
      canRedo: false,
      saveState: 'saved',
      loaded: true,
    }));
  };

  const pushHistory = (entry: HistoryEntry): void => {
    undoStack.push(entry);
    if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    redoStack = [];
    set({ canUndo: true, canRedo: false });
  };

  return {
    ...initialState(),

    load: (detail) => applyDetail(detail, false),

    dispatch: (ops, options = {}) => {
      const { doc, readOnly } = get();
      if (!doc || ops.length === 0) return false;
      const viewportOnly = ops.every((op) => op.op === 'set_viewport');
      if (readOnly && !viewportOnly) {
        set({ announce: copyWorkflow.toasts.readonly });
        return false;
      }
      const result = applyOps(doc, ops);
      if (!result.ok) {
        const reason = copyWorkflow.opRejects[result.failure.reason] ?? result.failure.message;
        set({ announce: reason });
        return false;
      }
      const record = options.record !== false && !viewportOnly;
      if (record) pushHistory({ before: doc, after: result.doc });
      set((state) => ({
        doc: result.doc,
        selection: pruneSelection(state.selection, result.doc),
        announce: describeOps(ops, titleLookup(result.doc, doc)),
      }));
      if (!readOnly) engine.push(ops);
      return true;
    },

    undo: () => {
      const entry = undoStack.pop();
      const { doc } = get();
      if (!entry || !doc) {
        set({ announce: copyWorkflow.announce.nothing, canUndo: undoStack.length > 0 });
        return;
      }
      get().dispatch(diffOps(doc, entry.before), { record: false });
      redoStack.push(entry);
      set({ canUndo: undoStack.length > 0, canRedo: true, announce: copyWorkflow.announce.undone });
    },

    redo: () => {
      const entry = redoStack.pop();
      const { doc } = get();
      if (!entry || !doc) return;
      get().dispatch(diffOps(doc, entry.after), { record: false });
      undoStack.push(entry);
      set({ canUndo: true, canRedo: redoStack.length > 0, announce: copyWorkflow.announce.redone });
    },

    setSelection: (selection) => set({ selection }),
    setAiBusy: (aiBusy) => set({ aiBusy }),
    setNarrow: (narrow) => set((state) => ({ narrow, readOnly: state.lockedByTemplate || narrow })),

    applyAssistResult: ({ ops, revision, validation }) => {
      const { doc } = get();
      if (!doc) return '';
      const result = applyOps(doc, ops);
      if (!result.ok) {
        // 本地与服务端对不上：以服务端为准，整体重新拉取。
        void get().refreshDetail();
        return '';
      }
      const handle = `assist-${(assistSeq += 1)}`;
      const entry = { before: doc, after: result.doc };
      assistHandles.set(handle, entry);
      pushHistory(entry);
      engine.setRevision(revision);
      set({ doc: result.doc, revision, validation, dirty: true, announce: describeOps(ops, titleLookup(result.doc, doc)) });
      get().setHighlight(touchedIds(ops));
      return handle;
    },

    undoAssist: (handle) => {
      const entry = assistHandles.get(handle);
      const { doc } = get();
      if (!entry || !doc) return;
      assistHandles.delete(handle);
      get().dispatch(diffOps(doc, entry.before), { record: false });
      undoStack = undoStack.filter((candidate) => candidate !== entry);
      set({ canUndo: undoStack.length > 0, announce: copyWorkflow.announce.undone });
    },

    setHighlight: (ids, ms = DEFAULT_HIGHLIGHT_MS) => {
      if (highlightTimer) clearTimeout(highlightTimer);
      set({ highlightIds: ids });
      if (ids.length > 0) highlightTimer = setTimeout(() => set({ highlightIds: [] }), ms);
    },

    togglePane: (name) =>
      set((state) => {
        const next = !state.panes[name];
        const panePrefs = { ...state.panePrefs, [name]: next };
        savePanePrefs(panePrefs);
        return { panes: { ...state.panes, [name]: next }, panePrefs };
      }),

    setDockTab: (dockTab) => set((state) => ({ panes: { ...state.panes, dockTab, dock: true } })),

    applyServerState: ({ doc, revision, validation }) => {
      undoStack = [];
      redoStack = [];
      assistHandles.clear();
      engine.reset(revision);
      set((state) => ({
        doc,
        revision,
        validation: validation ?? state.validation,
        selection: pruneSelection(state.selection, doc),
        canUndo: false,
        canRedo: false,
      }));
    },

    refreshDetail: async () => {
      const { templateId } = get();
      if (!templateId) return;
      try {
        applyDetail(await adminApi.workflows.get(templateId), true);
      } catch {
        set({ saveState: 'error' });
      }
    },

    flush: () => engine.flush(),

    reset: () => {
      engine.dispose();
      undoStack = [];
      redoStack = [];
      assistHandles.clear();
      set(initialState());
    },

    notify: (kind, text) => set({ notice: { seq: (noticeSeq += 1), kind, text } }),
  };
});

/** 同步引擎是否有未确认修改（`beforeunload` 判据）。 */
export function hasPendingWrites(): boolean {
  return engineRef?.hasPending() ?? false;
}

/** 网络恢复时让队列继续（`useWorkflowSync` 调用）。 */
export function resumeSync(): void {
  engineRef?.resume();
}
