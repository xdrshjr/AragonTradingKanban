'use client';

import { Eye, GitCompare, RotateCcw, Undo2 } from 'lucide-react';
import dynamic from 'next/dynamic';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Dialog, DialogContent } from '@/components/ui/Dialog';
import { notify } from '@/components/ui/Toast';
import { adminApi } from '@/lib/api.admin';
import { diffDocs } from '@/lib/workflow/canvasModel';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { VersionDetail, VersionSummary } from '@/lib/workflow/types';

import { VersionDiff } from './VersionDiff';

const VersionCanvas = dynamic(() => import('./VersionCanvas'), { ssr: false });

type Mode = 'view' | 'compare';
type Pending = { kind: 'restore' | 'rollback'; version: VersionSummary } | null;

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('zh-CN', { hour12: false });
}

interface ViewerProps {
  detail: VersionDetail;
  mode: Mode;
  onClose: () => void;
}

/** 版本查看 / 与草稿对比：只读画布 + 结构 diff。 */
function VersionViewer({ detail, mode, onClose }: ViewerProps) {
  const draft = useWorkflowStore((state) => state.doc);
  const v = canvasCopy.versions;
  const diff = mode === 'compare' && draft ? diffDocs(detail.doc, draft) : detail.diffVsPrevious;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={v.viewing(detail.summary.version)} className="w-[min(94vw,960px)]">
        <div className="flex h-[min(78vh,620px)] flex-col">
          <header className="border-b border-[var(--border-subtle)] px-4 py-2.5 pr-10">
            <h2 className="text-[13px] font-semibold">{v.viewing(detail.summary.version)}</h2>
            <p className="text-[11.5px] text-[var(--text-tertiary)]">
              {detail.summary.changeNote || '—'} · {formatTime(detail.summary.publishedAt)}
            </p>
          </header>
          <div className="grid min-h-0 flex-1 grid-cols-[1fr_240px]">
            <div className="min-h-0 border-r border-[var(--border-subtle)]">
              <VersionCanvas doc={detail.doc} />
            </div>
            <div className="overflow-auto p-3">
              {diff ? <VersionDiff diff={diff} firstVersion={mode === 'view' && detail.diffVsPrevious === null} /> : null}
            </div>
          </div>
          <footer className="flex justify-end border-t border-[var(--border-subtle)] px-4 py-2">
            <Button size="sm" onClick={onClose}>
              {v.closeView}
            </Button>
          </footer>
        </div>
      </DialogContent>
    </Dialog>
  );
}

interface RowProps {
  version: VersionSummary;
  isLatest: boolean;
  readOnly: boolean;
  busy: boolean;
  onOpen: (version: VersionSummary, mode: Mode) => void;
  onAsk: (kind: 'restore' | 'rollback', version: VersionSummary) => void;
}

function VersionRow({ version, isLatest, readOnly, busy, onOpen, onAsk }: RowProps) {
  const v = canvasCopy.versions;
  return (
    <tr className="border-t border-[var(--border-subtle)]" data-testid={`wf-version-${version.version}`}>
      <th scope="row" className="px-3 py-1 text-left font-mono font-medium">
        v{version.version}
        {isLatest ? <span className="ml-1 rounded-sm bg-[var(--accent-weak)] px-1 text-[10px] text-[var(--accent)]">最新</span> : null}
      </th>
      <td className="px-3 py-1 font-mono text-[11px] text-[var(--text-tertiary)]">{version.hash}</td>
      <td className="max-w-[220px] truncate px-3 py-1 text-[var(--text-secondary)]" title={version.changeNote}>
        {version.changeNote || '—'}
      </td>
      <td className="px-3 py-1 text-[var(--text-tertiary)]">{v.steps(version.stepCount)}</td>
      <td className="px-3 py-1 text-[var(--text-tertiary)]">{version.publishedByName ?? '—'}</td>
      <td className="px-3 py-1 text-[11px] text-[var(--text-tertiary)]">{formatTime(version.publishedAt)}</td>
      <td className="px-3 py-1">
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" disabled={busy} leadingIcon={<Eye className="size-3.5" />} onClick={() => onOpen(version, 'view')}>
            {v.view}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} leadingIcon={<GitCompare className="size-3.5" />} onClick={() => onOpen(version, 'compare')}>
            {v.compare}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy || readOnly} leadingIcon={<Undo2 className="size-3.5" />} onClick={() => onAsk('restore', version)}>
            {v.restore}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy || readOnly} leadingIcon={<RotateCcw className="size-3.5" />} onClick={() => onAsk('rollback', version)}>
            {v.rollback}
          </Button>
        </div>
      </td>
    </tr>
  );
}

export function VersionsPanel() {
  const templateId = useWorkflowStore((state) => state.templateId);
  const versions = useWorkflowStore((state) => state.versions);
  const readOnly = useWorkflowStore((state) => state.readOnly);
  const [viewer, setViewer] = useState<{ detail: VersionDetail; mode: Mode } | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const v = canvasCopy.versions;

  const open = async (version: VersionSummary, mode: Mode): Promise<void> => {
    setBusy(true);
    try {
      setViewer({ detail: await adminApi.workflows.version(templateId, version.version), mode });
    } catch (cause) {
      notify.error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (): Promise<void> => {
    if (!pending) return;
    const { kind, version } = pending;
    try {
      if (kind === 'restore') await adminApi.workflows.restore(templateId, version.version);
      else await adminApi.workflows.rollback(templateId, version.version);
      await useWorkflowStore.getState().refreshDetail();
      notify.success(kind === 'restore' ? v.restored : v.rolledBack);
    } catch (cause) {
      notify.error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(null);
    }
  };

  if (versions.length === 0) return <p className="px-3 py-3 text-[12px] text-[var(--text-tertiary)]">{v.empty}</p>;
  const latest = Math.max(...versions.map((version) => version.version));
  const ordered = [...versions].sort((a, b) => b.version - a.version);
  return (
    <>
      <table className="w-full text-[12px]">
        <tbody>
          {ordered.map((version) => (
            <VersionRow
              key={version.id}
              version={version}
              isLatest={version.version === latest}
              readOnly={readOnly}
              busy={busy}
              onOpen={(target, mode) => void open(target, mode)}
              onAsk={(kind, target) => setPending({ kind, version: target })}
            />
          ))}
        </tbody>
      </table>
      {viewer ? <VersionViewer detail={viewer.detail} mode={viewer.mode} onClose={() => setViewer(null)} /> : null}
      {pending ? (
        <ConfirmDialog
          open
          onOpenChange={(next) => !next && setPending(null)}
          tone="primary"
          title={pending.kind === 'rollback' ? v.confirmRollbackTitle : v.confirmRestoreTitle}
          description={
            pending.kind === 'rollback' ? v.confirmRollbackBody(pending.version.version) : v.confirmRestoreBody(pending.version.version)
          }
          confirmLabel={pending.kind === 'rollback' ? v.rollback : v.restore}
          onConfirm={confirm}
        />
      ) : null}
    </>
  );
}
