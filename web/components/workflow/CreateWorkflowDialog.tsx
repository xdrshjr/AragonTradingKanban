'use client';

import { useId, useState } from 'react';

import { LIMITS } from '@/lib/workflow/contract';
import { copyWorkflow } from '@/lib/workflow/copy.workflow';
import type { CreateFrom, TemplateSummary } from '@/lib/workflow/types';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { NativeSelect } from '../ui/Select';
import { Textarea } from '../ui/Textarea';

const t = copyWorkflow.create;

export interface CreateWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  templates: TemplateSummary[];
  /** 预选的起点（"复制"行操作传入）。 */
  initialFrom?: CreateFrom;
  initialName?: string;
  loading?: boolean;
  onSubmit: (body: { name: string; description: string; from: CreateFrom }) => void | Promise<void>;
}

type FromChoice = 'blank' | 'builtin' | 'template';

function choiceOf(from: CreateFrom | undefined): FromChoice {
  if (from === undefined || from === 'blank') return 'blank';
  return from === 'builtin' ? 'builtin' : 'template';
}

/** 新建 / 克隆对话框：名称 + 说明 + 起点（空白 | 内置 | 已有流程）。 */
export function CreateWorkflowDialog(props: CreateWorkflowDialogProps) {
  const { open, onOpenChange, templates, initialFrom, initialName = '', loading = false, onSubmit } = props;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t.title} className="p-5">
        {open ? (
          <CreateForm
            key={`${String(initialName)}-${JSON.stringify(initialFrom ?? 'blank')}`}
            templates={templates}
            initialFrom={initialFrom}
            initialName={initialName}
            loading={loading}
            onCancel={() => onOpenChange(false)}
            onSubmit={onSubmit}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

interface CreateFormProps {
  templates: TemplateSummary[];
  initialFrom?: CreateFrom;
  initialName: string;
  loading: boolean;
  onCancel: () => void;
  onSubmit: CreateWorkflowDialogProps['onSubmit'];
}

function CreateForm({ templates, initialFrom, initialName, loading, onCancel, onSubmit }: CreateFormProps) {
  const ids = { name: useId(), description: useId(), from: useId(), pick: useId(), hint: useId() };
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState('');
  const [choice, setChoice] = useState<FromChoice>(choiceOf(initialFrom));
  const [templateId, setTemplateId] = useState(
    typeof initialFrom === 'object' ? initialFrom.templateId : (templates[0]?.id ?? ''),
  );
  const [error, setError] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (loading) return;
    if (name.trim() === '') {
      setError(t.nameRequired);
      return;
    }
    setError(null);
    const from: CreateFrom = choice === 'template' ? { templateId } : choice;
    void onSubmit({ name: name.trim(), description: description.trim(), from });
  };

  return (
    <form onSubmit={submit} noValidate className="space-y-3.5">
      <h2 className="text-[15px] font-semibold tracking-tight">{t.title}</h2>
      <div>
        <label htmlFor={ids.name} className="mb-1 block text-[12px] font-medium text-[var(--text-secondary)]">
          {t.name}
        </label>
        <Input
          id={ids.name}
          value={name}
          maxLength={LIMITS.NAME_MAX}
          onChange={(event) => setName(event.target.value)}
          placeholder={t.namePlaceholder}
          invalid={error !== null}
          aria-describedby={ids.hint}
          autoFocus
        />
        <p id={ids.hint} className="mt-1 text-[11.5px] leading-relaxed text-[var(--text-tertiary)]">
          {error ? <span className="text-[var(--danger)]">{error} · </span> : null}
          {t.nameHint}
        </p>
      </div>
      <div>
        <label htmlFor={ids.description} className="mb-1 block text-[12px] font-medium text-[var(--text-secondary)]">
          {t.description}
        </label>
        <Textarea
          id={ids.description}
          rows={2}
          value={description}
          maxLength={LIMITS.DESCRIPTION_MAX}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      <div>
        <label htmlFor={ids.from} className="mb-1 block text-[12px] font-medium text-[var(--text-secondary)]">
          {t.from}
        </label>
        <NativeSelect
          id={ids.from}
          value={choice}
          onChange={(event) => setChoice(event.target.value as FromChoice)}
          className="w-full"
        >
          <option value="blank">{t.fromBlank}</option>
          <option value="builtin">{t.fromBuiltin}</option>
          {templates.length > 0 ? <option value="template">{t.fromTemplate}</option> : null}
        </NativeSelect>
      </div>
      {choice === 'template' ? (
        <div>
          <label htmlFor={ids.pick} className="mb-1 block text-[12px] font-medium text-[var(--text-secondary)]">
            {t.pickTemplate}
          </label>
          <NativeSelect
            id={ids.pick}
            value={templateId}
            onChange={(event) => setTemplateId(event.target.value)}
            className="w-full"
          >
            {templates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </NativeSelect>
        </div>
      ) : null}
      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t.cancel}
        </Button>
        <Button type="submit" variant="primary" loading={loading}>
          {t.submit}
        </Button>
      </div>
    </form>
  );
}
