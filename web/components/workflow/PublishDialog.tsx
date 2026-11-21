'use client';

import { AlertTriangle, XOctagon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/Button';
import { Dialog, DialogContent } from '@/components/ui/Dialog';
import { Textarea } from '@/components/ui/Textarea';
import { notify } from '@/components/ui/Toast';
import { adminApi } from '@/lib/api.admin';
import { diffDocs } from '@/lib/workflow/canvasModel';
import { LIMITS } from '@/lib/workflow/contract';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { describeIssue } from '@/lib/workflow/copy.workflow';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { VersionDiffSummary, WorkflowDoc } from '@/lib/workflow/types';

import { VersionDiff } from './VersionDiff';

export interface PublishDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** 与最新已发布版本的结构 diff：取版本文档在客户端对比（首版 = 全部新增）。 */
function useDiffAgainstLatest(open: boolean, doc: WorkflowDoc | null): { diff: VersionDiffSummary | null; first: boolean; loading: boolean } {
  const templateId = useWorkflowStore((state) => state.templateId);
  const versions = useWorkflowStore((state) => state.versions);
  const latest = versions.length > 0 ? Math.max(...versions.map((version) => version.version)) : null;
  const [base, setBase] = useState<WorkflowDoc | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || latest === null) {
      setBase(null);
      return;
    }
    let alive = true;
    setLoading(true);
    adminApi.workflows
      .version(templateId, latest)
      .then((detail) => alive && setBase(detail.doc))
      .catch(() => alive && setBase(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [open, templateId, latest]);

  const diff = useMemo(() => (doc && (latest === null || base) ? diffDocs(base, doc) : null), [base, doc, latest]);
  return { diff, first: latest === null, loading };
}

export function PublishDialog({ open, onOpenChange }: PublishDialogProps) {
  const doc = useWorkflowStore((state) => state.doc);
  const meta = useWorkflowStore((state) => state.meta);
  const validation = useWorkflowStore((state) => state.validation);
  const revision = useWorkflowStore((state) => state.revision);
  const dirty = useWorkflowStore((state) => state.dirty);
  const [note, setNote] = useState('');
  const [setDefault, setSetDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { diff, first, loading } = useDiffAgainstLatest(open, doc);
  const p = canvasCopy.publish;

  useEffect(() => {
    if (open) {
      setError(null);
      setNote('');
      setSetDefault(false);
    }
  }, [open]);

  const errors = validation?.issues.filter((issue) => issue.severity === 'error') ?? [];
  const warnings = validation?.issues.filter((issue) => issue.severity === 'warning') ?? [];
  const titleOf = (id: string | null): string => doc?.nodes.find((node) => node.id === id)?.title ?? '';
  const unchanged = !first && !dirty;
  const blocked = errors.length > 0 || !validation?.publishable;
  const alreadyDefault = meta?.isDefault === true;

  const submit = async (): Promise<void> => {
    if (!meta) return;
    setBusy(true);
    setError(null);
    try {
      await useWorkflowStore.getState().flush();
      const rev = useWorkflowStore.getState().revision;
      const version = await adminApi.workflows.publish(meta.id, { baseRevision: rev, changeNote: note.trim(), setDefault: setDefault && !alreadyDefault });
      await useWorkflowStore.getState().refreshDetail();
      notify.success(p.success(version.version));
      onOpenChange(false);
    } catch (cause) {
      const code = (cause as { code?: string }).code;
      setError(code === 'workflow_revision_conflict' ? p.conflict : cause instanceof Error ? cause.message : p.failed);
      if (code === 'workflow_revision_conflict') void useWorkflowStore.getState().refreshDetail();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent title={p.title} description={p.description} className="w-[min(94vw,640px)]">
        <div className="flex max-h-[min(84vh,680px)] flex-col" data-rev={revision}>
          <header className="border-b border-[var(--border-subtle)] px-5 py-3 pr-10">
            <h2 className="text-[14px] font-semibold">{p.title}</h2>
            <p className="mt-0.5 text-[12px] text-[var(--text-tertiary)]">{p.description}</p>
          </header>
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-5 py-4">
            {loading ? <p role="status" className="text-[12px] text-[var(--text-tertiary)]">{p.loadingDiff}</p> : null}
            {diff ? <VersionDiff diff={diff} firstVersion={first} /> : null}

            <div className="flex flex-col gap-1">
              <label htmlFor="wf-publish-note" className="flex items-center justify-between text-[12px] font-medium text-[var(--text-secondary)]">
                {p.note}
                <span className="font-mono text-[10.5px] text-[var(--text-tertiary)]">{note.length}/{LIMITS.CHANGE_NOTE_MAX}</span>
              </label>
              <Textarea id="wf-publish-note" rows={2} value={note} maxLength={LIMITS.CHANGE_NOTE_MAX} placeholder={p.notePlaceholder} onChange={(event) => setNote(event.target.value)} />
            </div>

            <label className="flex cursor-pointer items-center gap-2 text-[12.5px]">
              <input type="checkbox" className="size-3.5 accent-[var(--accent)]" checked={setDefault && !alreadyDefault} disabled={alreadyDefault} onChange={(event) => setSetDefault(event.target.checked)} />
              <span>{alreadyDefault ? p.alreadyDefault : p.setDefault}</span>
            </label>

            {errors.length > 0 ? (
              <section role="alert" aria-label={p.blocked} className="rounded-[var(--radius-sm)] border border-[var(--danger)]/40 bg-[var(--danger-weak)] px-3 py-2">
                <h3 className="flex items-center gap-1 text-[12px] font-medium text-[var(--danger)]">
                  <XOctagon aria-hidden="true" className="size-3.5" />
                  {p.blocked}
                </h3>
                <ul className="mt-1 list-disc pl-5 text-[12px] text-[var(--text-primary)]">
                  {errors.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>{describeIssue(issue, titleOf)}</li>
                  ))}
                </ul>
                <p className="mt-1 text-[11.5px] text-[var(--text-tertiary)]">{p.blockedHint}</p>
              </section>
            ) : null}

            <section aria-label={p.warnings}>
              <h3 className="flex items-center gap-1 text-[12px] font-medium text-[var(--text-secondary)]">
                <AlertTriangle aria-hidden="true" className="size-3.5 text-[var(--warn)]" />
                {p.warnings} · {warnings.length}
              </h3>
              {warnings.length === 0 ? (
                <p className="mt-1 text-[12px] text-[var(--text-tertiary)]">{p.noWarnings}</p>
              ) : (
                <ul className="mt-1 list-disc pl-5 text-[12px] text-[var(--text-secondary)]">
                  {warnings.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>{describeIssue(issue, titleOf)}</li>
                  ))}
                </ul>
              )}
            </section>
            {unchanged ? <p className="text-[12px] text-[var(--text-tertiary)]">{p.unchanged}</p> : null}
            {error ? <p role="alert" className="text-[12px] text-[var(--danger)]">{error}</p> : null}
          </div>
          <footer className="flex justify-end gap-2 border-t border-[var(--border-subtle)] px-5 py-3">
            <Button variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>
              {p.cancel}
            </Button>
            <Button variant="primary" loading={busy} disabled={blocked || unchanged} title={blocked ? p.blocked : undefined} onClick={() => void submit()} data-testid="wf-publish-submit">
              {busy ? p.submitting : p.submit}
            </Button>
          </footer>
        </div>
      </DialogContent>
    </Dialog>
  );
}
