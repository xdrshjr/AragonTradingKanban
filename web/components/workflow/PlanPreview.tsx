'use client';

import { Check, Copy, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { PlanPreview as PlanPreviewData } from '@/lib/workflow/types';

const REFRESH_DEBOUNCE_MS = 800;

/** 编译后的 `workflow.md`（Agent 实际读到的内容，以样例标的编译）。只读、可复制；草稿 revision 变化后自动刷新。 */
export function PlanPreview() {
  const templateId = useWorkflowStore((state) => state.templateId);
  const revision = useWorkflowStore((state) => state.revision);
  const [data, setData] = useState<PlanPreviewData | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const sequence = useRef(0);

  const load = useCallback(async () => {
    if (!templateId) return;
    const mine = (sequence.current += 1);
    setLoading(true);
    try {
      const next = await adminApi.workflows.preview(templateId);
      if (mine === sequence.current) {
        setData(next);
        setFailed(null);
      }
    } catch (cause) {
      if (mine === sequence.current) setFailed(cause instanceof Error ? cause.message : canvasCopy.plan.failed);
    } finally {
      if (mine === sequence.current) setLoading(false);
    }
  }, [templateId]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), REFRESH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [load, revision]);

  const copy = async (): Promise<void> => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.planMd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用：静默，按钮状态不变 */
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-[var(--border-subtle)] px-3 text-[11px] text-[var(--text-tertiary)]">
        <span className="min-w-0 flex-1 truncate">{canvasCopy.plan.hint}</span>
        {data ? (
          <span className="shrink-0 font-mono">
            {canvasCopy.plan.meta(data.bytes, data.sha1)} · {canvasCopy.plan.budget(data.budget.promptTotal, data.budget.limit)}
          </span>
        ) : null}
        <button type="button" aria-label={canvasCopy.plan.refresh} title={canvasCopy.plan.refresh} onClick={() => void load()} className="rounded p-0.5 hover:text-[var(--text-primary)]">
          <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <button type="button" disabled={!data} aria-label={canvasCopy.plan.copy} title={canvasCopy.plan.copy} onClick={() => void copy()} className="rounded p-0.5 hover:text-[var(--text-primary)] disabled:opacity-40">
          {copied ? <Check className="size-3.5 text-[var(--accent)]" /> : <Copy className="size-3.5" />}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {failed ? (
          <p role="alert" className="px-3 py-3 text-[12px] text-[var(--danger)]">
            {canvasCopy.plan.failed}：{failed}
          </p>
        ) : data ? (
          <pre data-testid="wf-plan-md" className="whitespace-pre-wrap break-words px-3 py-2 font-[var(--font-mono)] text-[11.5px] leading-relaxed text-[var(--text-primary)]">
            {data.planMd}
          </pre>
        ) : (
          <p role="status" className="px-3 py-3 text-[12px] text-[var(--text-tertiary)]">
            {canvasCopy.plan.loading}
          </p>
        )}
      </div>
    </div>
  );
}
