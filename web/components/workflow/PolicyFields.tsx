'use client';

import { useState } from 'react';

import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { adminApi } from '@/lib/api.admin';
import { GLOBAL_CEILING, LIMITS } from '@/lib/workflow/contract';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { PolicyPatch, TemplateSummary, WorkflowPolicy } from '@/lib/workflow/types';

import { Field } from './fieldKit';
import { useFieldCommit } from './useFieldCommit';

/** 空串 → `null`（沿用全站值）；非整数或越界 → `undefined`（不提交）。 */
export function parseCeiling(raw: string, ceiling: number): number | null | undefined {
  const text = raw.trim();
  if (text === '') return null;
  if (!/^\d+$/.test(text)) return undefined;
  const value = Number(text);
  return value >= 1 && value <= ceiling ? value : undefined;
}

interface NumberFieldProps {
  label: string;
  value: number | null;
  ceiling: number;
  disabled: boolean;
  onCommit: (next: number | null) => void;
  testId: string;
}

function CeilingField({ label, value, ceiling, disabled, onCommit, testId }: NumberFieldProps) {
  const { draft, setDraft, flush } = useFieldCommit(value === null ? '' : String(value), (raw) => {
    const parsed = parseCeiling(raw, ceiling);
    if (parsed !== undefined) onCommit(parsed);
  });
  const invalid = parseCeiling(draft, ceiling) === undefined;
  return (
    <Field label={label} htmlFor={testId}>
      <Input
        id={testId}
        inputSize="sm"
        inputMode="numeric"
        placeholder={String(ceiling)}
        value={draft}
        invalid={invalid}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={flush}
      />
    </Field>
  );
}

export function PolicyFields({ policy, disabled }: { policy: WorkflowPolicy; disabled: boolean }) {
  const dispatch = useWorkflowStore((state) => state.dispatch);
  const set = (patch: PolicyPatch): void => {
    dispatch([{ op: 'set_policy', patch }]);
  };
  return (
    <>
      <CeilingField
        label={canvasCopy.inspector.policyMaxTurns}
        value={policy.maxTurns}
        ceiling={GLOBAL_CEILING.aragonMaxTurns}
        disabled={disabled}
        testId="wf-policy-turns"
        onCommit={(next) => set({ maxTurns: next })}
      />
      <CeilingField
        label={canvasCopy.inspector.policyMaxMinutes}
        value={policy.maxDurationMinutes}
        ceiling={GLOBAL_CEILING.aragonMaxDurationMinutes}
        disabled={disabled}
        testId="wf-policy-minutes"
        onCommit={(next) => set({ maxDurationMinutes: next })}
      />
      <p className="text-[11px] leading-snug text-[var(--text-tertiary)]">
        {canvasCopy.inspector.policyHint(GLOBAL_CEILING.aragonMaxTurns, GLOBAL_CEILING.aragonMaxDurationMinutes)}
      </p>
      <label className="flex cursor-pointer items-start gap-2 text-[12px]">
        <input
          type="checkbox"
          checked={policy.progressMarkers}
          disabled={disabled}
          onChange={(event) => set({ progressMarkers: event.target.checked })}
          className="mt-0.5 size-3.5 accent-[var(--accent)]"
        />
        <span>
          <span className="text-[var(--text-primary)]">{canvasCopy.inspector.policyMarkers}</span>
          <span className="block text-[11px] text-[var(--text-tertiary)]">{canvasCopy.inspector.policyMarkersHint}</span>
        </span>
      </label>
    </>
  );
}

/** 模板名称 / 说明：失焦或停顿后 PATCH；名称下方有 RW-16 提示（名称对用户可见）。 */
export function TemplateInfoFields({ meta, disabled }: { meta: TemplateSummary; disabled: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const save = async (body: { name?: string; description?: string }): Promise<void> => {
    try {
      const next = await adminApi.workflows.patch(meta.id, body);
      useWorkflowStore.setState({ meta: next });
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  const name = useFieldCommit(meta.name, (next) => {
    const trimmed = next.trim();
    if (trimmed !== '' && trimmed !== meta.name) void save({ name: trimmed });
  });
  const description = useFieldCommit(meta.description, (next) => {
    if (next !== meta.description) void save({ description: next });
  });
  return (
    <>
      <Field
        label={canvasCopy.inspector.fieldName}
        htmlFor="wf-meta-name"
        counter={canvasCopy.inspector.charCount(name.draft.length, LIMITS.NAME_MAX)}
        hint={canvasCopy.inspector.nameHint}
      >
        <Input
          id="wf-meta-name"
          inputSize="sm"
          value={name.draft}
          maxLength={LIMITS.NAME_MAX}
          disabled={disabled}
          onChange={(event) => name.setDraft(event.target.value)}
          onBlur={name.flush}
        />
      </Field>
      <Field
        label={canvasCopy.inspector.fieldDescription}
        htmlFor="wf-meta-desc"
        counter={canvasCopy.inspector.charCount(description.draft.length, LIMITS.DESCRIPTION_MAX)}
      >
        <Textarea
          id="wf-meta-desc"
          rows={3}
          value={description.draft}
          maxLength={LIMITS.DESCRIPTION_MAX}
          disabled={disabled}
          onChange={(event) => description.setDraft(event.target.value)}
          onBlur={description.flush}
        />
      </Field>
      {error ? (
        <p role="alert" className="text-[11.5px] text-[var(--danger)]">
          {error}
        </p>
      ) : null}
    </>
  );
}
