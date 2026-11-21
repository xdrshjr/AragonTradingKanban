'use client';

import { useMemo } from 'react';

import { Input } from '@/components/ui/Input';
import { NativeSelect } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { estimateTurns, scanPlaceholders, sectionCoverage } from '@/lib/workflow/canvasModel';
import { EFFORTS, FILES, LIMITS, PLACEHOLDERS, ROLES, SECTIONS } from '@/lib/workflow/contract';
import { canvasCopy, EFFORT_LABELS, EFFORT_TURNS, ROLE_LABELS } from '@/lib/workflow/copy.canvas';
import type { Effort, Role, WorkflowDoc, WorkflowNode } from '@/lib/workflow/types';

import { CheckGroup, Field, SegmentedChoice } from './fieldKit';
import { useFieldCommit } from './useFieldCommit';
import type { NodePatcher } from './useNodePatch';

interface FieldsProps {
  node: WorkflowNode;
  doc: WorkflowDoc;
  disabled: boolean;
  patcher: NodePatcher;
}

export function TitleField({ node, disabled, patcher }: Omit<FieldsProps, 'doc'>) {
  const { draft, setDraft, flush } = useFieldCommit(node.title, (next) => patcher.setTitle(next.trim()));
  return (
    <Field label={canvasCopy.inspector.fieldTitle} htmlFor={`wf-title-${node.id}`} counter={canvasCopy.inspector.charCount(draft.length, LIMITS.TITLE_MAX)}>
      <Input
        id={`wf-title-${node.id}`}
        inputSize="sm"
        value={draft}
        maxLength={LIMITS.TITLE_MAX}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={flush}
      />
    </Field>
  );
}

/** 提示词：占位符做词法提示——列出引用到的占位符，未定义的标红并写明原因（权威仍是服务端报告）。 */
function PromptField({ node, disabled, patcher }: Omit<FieldsProps, 'doc'>) {
  const { draft, setDraft, flush } = useFieldCommit(node.config.prompt ?? '', (next) => patcher.setConfig({ prompt: next }));
  const tokens = useMemo(() => scanPlaceholders(draft).filter((token) => !token.escaped), [draft]);
  const hasUndefined = tokens.some((token) => !token.defined);
  const id = `wf-prompt-${node.id}`;
  return (
    <Field
      label={canvasCopy.inspector.fieldPrompt}
      htmlFor={id}
      counter={canvasCopy.inspector.charCount(draft.length, LIMITS.PROMPT_MAX)}
      hint={
        <div className="flex flex-wrap items-center gap-1">
          <span>{canvasCopy.inspector.promptHint}</span>
          {PLACEHOLDERS.map((name) => (
            <code key={name} className="rounded-sm bg-[var(--bg-surface-3)] px-1 font-mono text-[10.5px]">{`{{${name}}}`}</code>
          ))}
        </div>
      }
    >
      <Textarea
        id={id}
        rows={6}
        value={draft}
        maxLength={LIMITS.PROMPT_MAX}
        invalid={hasUndefined}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={flush}
        className="font-[var(--font-mono)] text-[12px]"
      />
      {tokens.length > 0 ? (
        <ul aria-label="占位符检查" className="flex flex-wrap gap-1">
          {tokens.map((token, index) => (
            <li
              key={`${token.name}-${index}`}
              data-defined={token.defined}
              className="inline-flex items-center gap-1 rounded-sm px-1 font-mono text-[10.5px]"
              style={{
                color: token.defined ? 'var(--accent)' : 'var(--danger)',
                background: token.defined ? 'var(--accent-weak)' : 'var(--danger-weak)',
              }}
            >
              {token.defined ? '✓' : '✗'} {`{{${token.name}}}`}
              {token.defined ? null : <span className="font-sans">{canvasCopy.inspector.placeholderUndefined(token.name)}</span>}
            </li>
          ))}
        </ul>
      ) : null}
    </Field>
  );
}

function CriteriaField({ node, disabled, patcher }: Omit<FieldsProps, 'doc'>) {
  const { draft, setDraft, flush } = useFieldCommit(node.config.successCriteria ?? '', (next) => patcher.setConfig({ successCriteria: next === '' ? null : next }));
  const id = `wf-criteria-${node.id}`;
  return (
    <Field label={canvasCopy.inspector.fieldCriteria} htmlFor={id} counter={canvasCopy.inspector.charCount(draft.length, LIMITS.CRITERIA_MAX)}>
      <Textarea id={id} rows={3} value={draft} maxLength={LIMITS.CRITERIA_MAX} disabled={disabled} onChange={(event) => setDraft(event.target.value)} onBlur={flush} />
    </Field>
  );
}

export function StepFields({ node, doc, disabled, patcher }: FieldsProps) {
  const coverage = useMemo(() => sectionCoverage(doc), [doc]);
  const role: Role = node.config.role ?? 'analysis';
  const effort: Effort = node.config.effort ?? 'normal';
  return (
    <>
      <TitleField node={node} disabled={disabled} patcher={patcher} />
      <Field label={canvasCopy.inspector.fieldRole} htmlFor={`wf-role-${node.id}`}>
        <NativeSelect
          id={`wf-role-${node.id}`}
          selectSize="sm"
          value={role}
          disabled={disabled}
          onChange={(event) => patcher.setConfig({ role: event.target.value as Role })}
        >
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {ROLE_LABELS[value] ?? value}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <PromptField node={node} disabled={disabled} patcher={patcher} />
      <Field label={canvasCopy.inspector.fieldFiles}>
        <CheckGroup
          legend={canvasCopy.inspector.fieldFiles}
          disabled={disabled}
          value={node.config.files ?? []}
          options={FILES.map((file) => ({ value: file.name, label: file.name, hint: `${file.label} · ${file.hint}` }))}
          onChange={(files) => patcher.setConfig({ files: files.length === 0 ? null : files })}
        />
      </Field>
      <Field label={canvasCopy.inspector.fieldSections}>
        <CheckGroup
          legend={canvasCopy.inspector.fieldSections}
          disabled={disabled}
          value={node.config.sections ?? []}
          options={SECTIONS.map((section) => {
            const covered = (coverage[section.id]?.length ?? 0) > 0;
            return {
              value: section.id,
              label: section.label,
              status: { ok: covered, text: covered ? canvasCopy.inspector.covered : canvasCopy.inspector.uncovered },
            };
          })}
          onChange={(sections) => patcher.setConfig({ sections: sections.length === 0 ? null : sections })}
        />
      </Field>
      <CriteriaField node={node} disabled={disabled} patcher={patcher} />
      <Field
        label={canvasCopy.inspector.fieldEffort}
        hint={canvasCopy.inspector.estimatedTurns(EFFORT_TURNS[effort] ?? 4) + ` · 全流程约 ${estimateTurns(doc)} 回合`}
      >
        <SegmentedChoice<Effort>
          legend={canvasCopy.inspector.fieldEffort}
          value={effort}
          disabled={disabled}
          options={EFFORTS.map((value) => ({ value: value as Effort, label: EFFORT_LABELS[value] ?? value }))}
          onChange={(next) => patcher.setConfig({ effort: next })}
        />
      </Field>
    </>
  );
}

export function GateFields({ node, disabled, patcher }: Omit<FieldsProps, 'doc'>) {
  const { draft, setDraft, flush } = useFieldCommit(node.config.checklist ?? '', (next) => patcher.setConfig({ checklist: next === '' ? null : next }));
  const id = `wf-checklist-${node.id}`;
  return (
    <>
      <TitleField node={node} disabled={disabled} patcher={patcher} />
      <Field label={canvasCopy.inspector.fieldChecklist} htmlFor={id} counter={canvasCopy.inspector.charCount(draft.length, LIMITS.CHECKLIST_MAX)}>
        <Textarea id={id} rows={4} value={draft} maxLength={LIMITS.CHECKLIST_MAX} disabled={disabled} onChange={(event) => setDraft(event.target.value)} onBlur={flush} />
      </Field>
    </>
  );
}

export function NoteFields({ node, disabled, patcher }: Omit<FieldsProps, 'doc'>) {
  const { draft, setDraft, flush } = useFieldCommit(node.config.body ?? '', (next) => patcher.setConfig({ body: next === '' ? null : next }));
  const id = `wf-body-${node.id}`;
  return (
    <>
      <TitleField node={node} disabled={disabled} patcher={patcher} />
      <Field label={canvasCopy.inspector.fieldBody} htmlFor={id} counter={canvasCopy.inspector.charCount(draft.length, LIMITS.NOTE_MAX)}>
        <Textarea id={id} rows={4} value={draft} maxLength={LIMITS.NOTE_MAX} disabled={disabled} onChange={(event) => setDraft(event.target.value)} onBlur={flush} />
      </Field>
    </>
  );
}
