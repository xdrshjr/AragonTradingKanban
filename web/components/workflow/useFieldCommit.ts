'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export const COMMIT_DELAY_MS = 600;

export interface FieldCommit<T> {
  draft: T;
  /** 输入中：更新草稿，并在停顿 600 ms 后提交。 */
  setDraft: (value: T) => void;
  /** 失焦：立即提交（若有待提交的修改）。 */
  flush: () => void;
}

/**
 * 属性检查器的文本输入策略（§3.10.6）：**失焦或 600 ms 停顿提交**，一次提交一个 op，
 * 避免逐字符写库。外部值变化（AI 改了这个字段）且本地没有未提交修改时，草稿跟随外部值。
 */
export function useFieldCommit<T>(value: T, commit: (next: T) => void, delay = COMMIT_DELAY_MS): FieldCommit<T> {
  const [draft, setDraftState] = useState<T>(value);
  const dirty = useRef(false);
  const latest = useRef(draft);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commitRef = useRef(commit);
  commitRef.current = commit;

  useEffect(() => {
    if (!dirty.current) {
      latest.current = value;
      setDraftState(value);
    }
  }, [value]);

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (!dirty.current) return;
    dirty.current = false;
    commitRef.current(latest.current);
  }, []);

  const setDraft = useCallback(
    (next: T) => {
      dirty.current = true;
      latest.current = next;
      setDraftState(next);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, delay);
    },
    [delay, flush],
  );

  // 卸载（切换选中节点）时把没提交的修改提交掉，不丢字。
  useEffect(() => () => flush(), [flush]);

  return { draft, setDraft, flush };
}
