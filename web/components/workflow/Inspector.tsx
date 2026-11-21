'use client';

import { Copy, Lock, SlidersHorizontal, Trash2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { adminApi } from '@/lib/api.admin';
import { KIND_LABELS, canvasCopy } from '@/lib/workflow/copy.canvas';
import { edgeById, nodeById } from '@/lib/workflow/ops';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { WorkflowDoc, WorkflowEdge, WorkflowNode } from '@/lib/workflow/types';

import { Field, Section } from './fieldKit';
import { PolicyFields, TemplateInfoFields } from './PolicyFields';
import { RelationPicker } from './RelationPicker';
import { GateFields, NoteFields, StepFields, TitleField } from './StepFields';
import { ToolWindow } from './ToolWindow';
import { useFieldCommit } from './useFieldCommit';
import { useNodePatch } from './useNodePatch';

/** 内置/归档模板：顶部提示条 +【复制为可编辑副本】（调 create 后跳转到副本）。 */
export function ReadOnlyBanner() {
  const router = useRouter();
  const meta = useWorkflowStore((state) => state.meta);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!meta) return null;
  const duplicate = async (): Promise<void> => {
    setBusy(true);
    setFailed(false);
    try {
      const created = await adminApi.workflows.create({ name: `${meta.name} 副本`.slice(0, 60), from: { templateId: meta.id } });
      router.push(`/admin/workflows/${created.meta.id}`);
    } catch {
      setFailed(true);
      setBusy(false);
    }
  };
  return (
    <div role="note" className="flex flex-col gap-2 border-b border-[var(--border-subtle)] bg-[var(--warn-weak)] px-3 py-2">
      <div className="flex items-start gap-1.5 text-[11.5px] text-[var(--text-secondary)]">
        <Lock aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-[var(--warn)]" />
        <span>{canvasCopy.readOnlyBanner}</span>
      </div>
      <Button size="sm" variant="accent-ghost" loading={busy} leadingIcon={<Copy className="size-3.5" />} onClick={() => void duplicate()}>
        {busy ? canvasCopy.duplicating : canvasCopy.duplicateAsCopy}
      </Button>
      {failed ? (
        <p role="alert" className="text-[11px] text-[var(--danger)]">
          {canvasCopy.duplicateFailed}
        </p>
      ) : null}
    </div>
  );
}

function Relations({ doc, node, disabled }: { doc: WorkflowDoc; node: WorkflowNode; disabled: boolean }) {
  if (node.kind === 'note') return null;
  return (
    <Section>
      {node.kind !== 'start' ? (
        <Field label={canvasCopy.inspector.fieldUpstream}>
          <RelationPicker doc={doc} node={node} direction="upstream" disabled={disabled} />
        </Field>
      ) : null}
      {node.kind !== 'end' ? (
        <Field label={canvasCopy.inspector.fieldDownstream}>
          <RelationPicker doc={doc} node={node} direction="downstream" disabled={disabled} />
        </Field>
      ) : null}
    </Section>
  );
}

function NodeInspector({ doc, node, disabled }: { doc: WorkflowDoc; node: WorkflowNode; disabled: boolean }) {
  const patcher = useNodePatch(node.id);
  const dispatch = useWorkflowStore((state) => state.dispatch);
  const removable = node.kind !== 'start' && node.kind !== 'end';
  return (
    <>
      <Section title={`${KIND_LABELS[node.kind] ?? node.kind}`}>
        {node.kind === 'step' ? <StepFields node={node} doc={doc} disabled={disabled} patcher={patcher} /> : null}
        {node.kind === 'gate' ? <GateFields node={node} disabled={disabled} patcher={patcher} /> : null}
        {node.kind === 'note' ? <NoteFields node={node} disabled={disabled} patcher={patcher} /> : null}
        {node.kind === 'start' || node.kind === 'end' ? <TitleField node={node} disabled={disabled} patcher={patcher} /> : null}
      </Section>
      <Relations doc={doc} node={node} disabled={disabled} />
      {removable ? (
        <Section>
          <Button
            size="sm"
            variant="danger"
            disabled={disabled}
            leadingIcon={<Trash2 className="size-3.5" />}
            onClick={() => dispatch([{ op: 'delete_node', id: node.id, expectRev: node.rev }])}
          >
            {canvasCopy.inspector.deleteNode}
          </Button>
        </Section>
      ) : null}
    </>
  );
}

function EdgeInspector({ edge, disabled }: { edge: WorkflowEdge; disabled: boolean }) {
  const dispatch = useWorkflowStore((state) => state.dispatch);
  const { draft, setDraft, flush } = useFieldCommit(edge.label, (next) => {
    const current = useWorkflowStore.getState().doc;
    const latest = current ? edgeById(current, edge.id) : undefined;
    if (latest) dispatch([{ op: 'update_edge', id: edge.id, expectRev: latest.rev, patch: { label: next === '' ? null : next } }]);
  });
  return (
    <Section title="连线">
      <Field label={canvasCopy.inspector.fieldLabel} htmlFor={`wf-edge-${edge.id}`}>
        <Input id={`wf-edge-${edge.id}`} inputSize="sm" value={draft} maxLength={30} disabled={disabled} onChange={(event) => setDraft(event.target.value)} onBlur={flush} />
      </Field>
      <Button
        size="sm"
        variant="danger"
        disabled={disabled}
        leadingIcon={<Trash2 className="size-3.5" />}
        onClick={() => dispatch([{ op: 'delete_edge', id: edge.id, expectRev: edge.rev }])}
      >
        {canvasCopy.inspector.deleteEdge}
      </Button>
    </Section>
  );
}

function GraphInspector({ doc, disabled }: { doc: WorkflowDoc; disabled: boolean }) {
  const meta = useWorkflowStore((state) => state.meta);
  return (
    <>
      <Section title={canvasCopy.inspector.policyTitle}>
        <PolicyFields policy={doc.policy} disabled={disabled} />
      </Section>
      {meta ? (
        <Section title={canvasCopy.inspector.templateTitle}>
          <TemplateInfoFields meta={meta} disabled={disabled} />
        </Section>
      ) : null}
    </>
  );
}

/** 属性检查器：按选中对象分派（节点 / 连线 / 未选中 = 流程策略 + 模板信息）。内置模板全部禁用。 */
export function Inspector() {
  const doc = useWorkflowStore((state) => state.doc);
  const readOnly = useWorkflowStore((state) => state.readOnly);
  const selection = useWorkflowStore((state) => state.selection);
  if (!doc) return null;

  const onlyNode = selection.nodes.length === 1 && selection.edges.length === 0 ? nodeById(doc, selection.nodes[0] ?? '') : undefined;
  const onlyEdge = selection.nodes.length === 0 && selection.edges.length === 1 ? edgeById(doc, selection.edges[0] ?? '') : undefined;
  const total = selection.nodes.length + selection.edges.length;

  return (
    <ToolWindow title={canvasCopy.inspector.title} icon={<SlidersHorizontal className="size-3.5" />} label={canvasCopy.inspector.title}>
      {readOnly ? <ReadOnlyBanner /> : null}
      {onlyNode ? <NodeInspector key={onlyNode.id} doc={doc} node={onlyNode} disabled={readOnly} /> : null}
      {onlyEdge ? <EdgeInspector key={onlyEdge.id} edge={onlyEdge} disabled={readOnly} /> : null}
      {!onlyNode && !onlyEdge && total === 0 ? (
        <>
          <p className="px-3 pt-3 text-[11.5px] text-[var(--text-tertiary)]">{canvasCopy.inspector.noSelection}</p>
          <GraphInspector doc={doc} disabled={readOnly} />
        </>
      ) : null}
      {!onlyNode && !onlyEdge && total > 1 ? (
        <p className="px-3 py-3 text-[12px] text-[var(--text-secondary)]">{canvasCopy.inspector.multi(total)}</p>
      ) : null}
    </ToolWindow>
  );
}
