/**
 * 草稿的保存队列（spec §3.10.5）：与 React 无关的单飞发送器。
 *
 * * **单飞**：同一时刻至多一个 `POST /ops` 在飞，`baseRevision` = 已确认的 revision；
 * * 结构性 op 立即发；`move_node` / `set_viewport` 合并并 400 ms 防抖；
 * * **409**：丢弃未确认队列，由 store 用权威文档整体替换；
 * * **断网**：批次留在队列里，`online` 事件或 5 s 定时器恢复后按序重放。
 */

import { ApiError } from '../api';
import { adminApi } from '../api.admin';
import type { OpsResponse, RevisionConflictDetail, ValidationReport, WorkflowOp } from './types';
import { NON_STRUCTURAL_OPS } from './types';

export const MOVE_DEBOUNCE_MS = 400;
export const OFFLINE_RETRY_MS = 5000;

export type SaveState = 'saved' | 'saving' | 'offline' | 'error';

export interface SyncCallbacks {
  onState: (state: SaveState) => void;
  onConfirmed: (response: OpsResponse) => void;
  onConflict: (detail: RevisionConflictDetail | null) => void;
  onRejected: (error: ApiError) => void;
  onReplayed: () => void;
}

interface Batch {
  ops: WorkflowOp[];
  structural: boolean;
}

/** 把一批纯位置/视口 op 并入另一批：同一节点只保留最后一次位置，视口只保留最后一次。 */
export function mergeNonStructural(into: WorkflowOp[], incoming: readonly WorkflowOp[]): WorkflowOp[] {
  const merged = [...into];
  for (const op of incoming) {
    const index = merged.findIndex(
      (existing) =>
        existing.op === op.op && (op.op === 'set_viewport' || (op.op === 'move_node' && existing.op === 'move_node' && existing.id === op.id)),
    );
    if (index >= 0) merged.splice(index, 1);
    merged.push(op);
  }
  return merged;
}

const isNetwork = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 0 || error.status === 408);

export class SyncEngine {
  private templateId = '';
  private revision = 1;
  private queue: Batch[] = [];
  private inflight = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private offline = false;
  /** 离线期间有批次被缓存：队列最终排空时提示"已同步"。 */
  private replayPending = false;
  private waiters: (() => void)[] = [];
  /** 每次 reset 自增：让在飞请求的迟到响应识别出"我已过期"。 */
  private epoch = 0;

  constructor(private readonly callbacks: SyncCallbacks) {}

  configure(templateId: string, revision: number): void {
    this.reset(revision);
    this.templateId = templateId;
  }

  /** 丢弃队列并以权威 revision 重新开始（409 对齐 / 重新加载）。 */
  reset(revision: number): void {
    this.epoch += 1;
    this.queue = [];
    this.inflight = false;
    this.offline = false;
    this.replayPending = false;
    this.clearTimers();
    this.revision = revision;
    this.callbacks.onState('saved');
    this.release();
  }

  setRevision(revision: number): void {
    this.revision = revision;
  }

  getRevision(): number {
    return this.revision;
  }

  /** 是否还有未确认的修改（`beforeunload` 判据）。 */
  hasPending(): boolean {
    return this.inflight || this.queue.length > 0;
  }

  push(ops: readonly WorkflowOp[]): void {
    if (ops.length === 0) return;
    const structural = ops.some((op) => !NON_STRUCTURAL_OPS.has(op.op));
    const last = this.queue[this.queue.length - 1];
    const mergeable = last !== undefined && !last.structural && !(this.inflight && last === this.queue[0]);
    if (!structural && last && mergeable) {
      last.ops = mergeNonStructural(last.ops, ops);
    } else {
      this.queue.push({ ops: [...ops], structural });
    }
    this.callbacks.onState(this.offline ? 'offline' : 'saving');
    if (structural) this.pump();
    else this.schedule();
  }

  /** 等到队列清空（或确定无法清空）。 */
  flush(): Promise<void> {
    if (!this.hasPending()) return Promise.resolve();
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.pump();
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  /** 网络恢复（`online` 事件）时由 hook 调用。 */
  resume(): void {
    if (!this.offline) return;
    this.offline = false;
    this.pump();
  }

  dispose(): void {
    this.epoch += 1;
    this.clearTimers();
    this.release();
  }

  // ── 内部 ───────────────────────────────────────────────────────────────────

  private schedule(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.pump();
    }, MOVE_DEBOUNCE_MS);
  }

  private clearTimers(): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.timer = null;
    this.retryTimer = null;
  }

  private release(): void {
    const waiters = this.waiters;
    this.waiters = [];
    waiters.forEach((resolve) => resolve());
  }

  private pump(): void {
    if (this.inflight || this.offline || this.queue.length === 0) {
      if (!this.hasPending()) this.release();
      return;
    }
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    const batch = this.queue[0] as Batch;
    this.inflight = true;
    const epoch = this.epoch;
    adminApi.workflows
      .ops(this.templateId, this.revision, batch.ops)
      .then((response) => this.onSuccess(epoch, response))
      .catch((error: unknown) => this.onFailure(epoch, error));
  }

  private onSuccess(epoch: number, response: OpsResponse): void {
    if (epoch !== this.epoch) return;
    this.queue.shift();
    this.inflight = false;
    this.revision = response.revision;
    this.callbacks.onConfirmed(response);
    if (this.queue.length > 0) this.pump();
    else {
      this.callbacks.onState('saved');
      if (this.replayPending) this.callbacks.onReplayed();
      this.replayPending = false;
      this.release();
    }
  }

  private onFailure(epoch: number, error: unknown): void {
    if (epoch !== this.epoch) return;
    this.inflight = false;
    if (isNetwork(error)) {
      this.offline = true;
      this.replayPending = true;
      this.callbacks.onState('offline');
      this.retryTimer = setTimeout(() => {
        this.offline = false;
        this.pump();
      }, OFFLINE_RETRY_MS);
      return;
    }
    this.queue = [];
    if (error instanceof ApiError && error.code === 'workflow_revision_conflict') {
      this.callbacks.onConflict(error.detail as unknown as RevisionConflictDetail);
    } else if (error instanceof ApiError) {
      this.callbacks.onState('error');
      this.callbacks.onRejected(error);
    } else {
      this.callbacks.onState('error');
    }
    this.release();
  }
}

export type { ValidationReport };
