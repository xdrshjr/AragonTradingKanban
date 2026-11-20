'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Search, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import { useCan } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { money } from '@/lib/format';
import type { AdminSettings, RegistrationMode, SymbolSearchHit, WatchlistEntry } from '@/lib/types';

import { Field, fieldDescribedBy } from '../auth/Field';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Skeleton } from '../ui/Skeleton';
import { notify } from '../ui/Toast';
import { AdminCard, AdminPageHeader, EmptyState, ErrorState } from './AdminPrimitives';
import { adminKeys } from './keys';
import { notifyFailure } from './UserActionDialogs';

const t = copyAdmin.settings;

const MODES: RegistrationMode[] = ['open', 'invite', 'closed'];

type NumericKey = 'defaultDailyReport' | 'defaultDailyDeepAsk' | 'signupBonus' | 'siteDailyCap';

interface NumericField {
  key: NumericKey;
  label: string;
  hint: string;
  min: number;
  max: number;
}

const NUMERIC_FIELDS: NumericField[] = [
  { key: 'defaultDailyReport', label: t.quota.defaultDailyReport, hint: t.quota.hintReport, min: 0, max: 100 },
  { key: 'defaultDailyDeepAsk', label: t.quota.defaultDailyDeepAsk, hint: t.quota.hintDeepAsk, min: 0, max: 200 },
  { key: 'signupBonus', label: t.quota.signupBonus, hint: t.quota.hintBonus, min: 0, max: 20 },
  { key: 'siteDailyCap', label: t.quota.siteDailyCap, hint: t.quota.siteDailyCapHint, min: 0, max: 10000 },
];

/** `invalid_setting.detail.field` 的写法（驼峰 / 下划线）不确定：归一后比较，见 F-19。 */
function normalizeField(name: unknown): string {
  return typeof name === 'string' ? name.replace(/[^a-z]/gi, '').toLowerCase() : '';
}

function saveSuccess(): void {
  notify.success(t.savedAudited);
}

/* ── 注册模式 ──────────────────────────────────────────────────────────────── */

function RegistrationSection({ settings }: { settings: AdminSettings }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<RegistrationMode>(settings.registrationMode);
  useEffect(() => setMode(settings.registrationMode), [settings.registrationMode]);
  const dirty = mode !== settings.registrationMode;

  const save = useMutation({
    mutationFn: () => adminApi.adminPatchSettings({ registrationMode: mode }),
    onSuccess: (next) => {
      queryClient.setQueryData(adminKeys.settings, next);
      saveSuccess();
    },
    onError: notifyFailure,
  });

  return (
    <AdminCard title={t.registration.title} headingId="settings-registration">
      <fieldset>
        <legend className="mb-3 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{t.registration.body}</legend>
        <div role="radiogroup" aria-label={t.registration.groupLabel} className="grid gap-2.5 min-[900px]:grid-cols-3">
          {MODES.map((value) => {
            const checked = mode === value;
            const info = t.registration.modes[value];
            return (
              <label
                key={value}
                className={cn(
                  'relative flex cursor-pointer flex-col gap-1 rounded-[var(--radius-md)] p-3.5 ring-1 transition-colors',
                  'has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--accent-ring)]',
                  checked
                    ? 'bg-[var(--accent-weak)] ring-[var(--accent)]'
                    : 'bg-[var(--bg-surface-2)] ring-[var(--border-strong)] hover:bg-[var(--bg-surface-3)]',
                )}
              >
                <input
                  type="radio"
                  name="registration-mode"
                  value={value}
                  checked={checked}
                  onChange={() => setMode(value)}
                  className="sr-only"
                />
                <span className="flex items-center justify-between gap-2 text-[13px] font-medium text-[var(--text-primary)]">
                  {info.label}
                  {checked ? <Check className="size-4 text-[var(--accent)]" aria-hidden="true" /> : null}
                </span>
                <span className="text-[11.5px] leading-relaxed text-[var(--text-tertiary)]">{info.body}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <div className="mt-4 flex justify-end">
        <Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
          {copyAdmin.common.save}
        </Button>
      </div>
    </AdminCard>
  );
}

/* ── 自助找回密码（★Y3）──────────────────────────────────────────────────── */

/**
 * `auth.recovery_enabled`：严格布尔（后端对 `1` / `"true"` / `null` 一律 `422 invalid_setting`）。
 * 独立保存、写审计、立即生效。后端未返回该键（旧版本）时整区不渲染。
 */
function RecoverySection({ settings }: { settings: AdminSettings }) {
  const queryClient = useQueryClient();
  const current = settings.authRecoveryEnabled;
  const [on, setOn] = useState(current === true);
  useEffect(() => setOn(current === true), [current]);
  const dirty = current !== undefined && on !== current;

  const save = useMutation({
    mutationFn: () => adminApi.adminPatchSettings({ authRecoveryEnabled: on }),
    onSuccess: (next) => {
      queryClient.setQueryData(adminKeys.settings, next);
      notify.success(fill(t.recovery.saved, { state: on ? t.recovery.on : t.recovery.off }));
    },
    onError: notifyFailure,
  });

  if (current === undefined) return null;

  return (
    <AdminCard title={t.recovery.title} headingId="settings-recovery">
      <p className="mb-3 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{t.recovery.body}</p>
      <label htmlFor="settings-recovery-switch" className="flex cursor-pointer items-center gap-3 text-[12.5px] text-[var(--text-primary)]">
        <input
          id="settings-recovery-switch"
          type="checkbox"
          role="switch"
          checked={on}
          onChange={(event) => setOn(event.target.checked)}
          className="size-4 accent-[var(--accent)]"
        />
        <span>{t.recovery.label}</span>
        <span className="text-[11.5px] text-[var(--text-tertiary)]">{on ? t.recovery.on : t.recovery.off}</span>
      </label>
      <div className="mt-4 flex justify-end">
        <Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
          {copyAdmin.common.save}
        </Button>
      </div>
    </AdminCard>
  );
}

/* ── 额度与上限 ────────────────────────────────────────────────────────────── */

function QuotaSection({ settings }: { settings: AdminSettings }) {
  const queryClient = useQueryClient();
  const initial = (): Record<NumericKey, string> => ({
    defaultDailyReport: String(settings.defaultDailyReport),
    defaultDailyDeepAsk: String(settings.defaultDailyDeepAsk),
    signupBonus: String(settings.signupBonus),
    siteDailyCap: String(settings.siteDailyCap),
  });
  const [values, setValues] = useState<Record<NumericKey, string>>(initial);
  const [errors, setErrors] = useState<Partial<Record<NumericKey, string>>>({});
  const refs = useRef<Partial<Record<NumericKey, HTMLInputElement | null>>>({});

  useEffect(() => {
    setValues(initial());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.defaultDailyReport, settings.defaultDailyDeepAsk, settings.signupBonus, settings.siteDailyCap]);

  const changed = NUMERIC_FIELDS.filter((field) => values[field.key] !== String(settings[field.key]));

  const save = useMutation({
    mutationFn: (patch: Partial<AdminSettings>) => adminApi.adminPatchSettings(patch),
    onSuccess: (next) => {
      queryClient.setQueryData(adminKeys.settings, next);
      setErrors({});
      saveSuccess();
    },
    onError: (error) => {
      // 越界：服务端按字段回 `422 invalid_setting(detail.field)`，把错误落回对应输入框。
      if (error instanceof ApiError && error.code === 'invalid_setting') {
        const bad = NUMERIC_FIELDS.find((field) => normalizeField(field.key) === normalizeField(error.detail.field));
        if (bad) {
          setErrors({
            [bad.key]: fill(t.quota.invalid, { label: bad.label, min: bad.min, max: bad.max }),
          });
          refs.current[bad.key]?.focus();
          return;
        }
      }
      notifyFailure(error);
    },
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (save.isPending || changed.length === 0) return;
    const nextErrors: Partial<Record<NumericKey, string>> = {};
    const patch: Partial<AdminSettings> = {};
    for (const field of changed) {
      const raw = values[field.key];
      const n = Number(raw);
      if (raw.trim() === '' || !Number.isInteger(n) || n < field.min || n > field.max) {
        nextErrors[field.key] = fill(t.quota.invalid, { label: field.label, min: field.min, max: field.max });
      } else {
        patch[field.key] = n;
      }
    }
    setErrors(nextErrors);
    const firstBad = NUMERIC_FIELDS.find((field) => nextErrors[field.key]);
    if (firstBad) return refs.current[firstBad.key]?.focus();
    save.mutate(patch);
  };

  return (
    <AdminCard title={t.quota.title} headingId="settings-quota">
      <form onSubmit={submit} noValidate aria-labelledby="settings-quota">
        <p className="mb-3 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{t.quota.body}</p>
        <div className="grid gap-x-5 gap-y-1 min-[900px]:grid-cols-2">
          {NUMERIC_FIELDS.map((field) => {
            const id = `settings-${field.key}`;
            return (
              <Field key={field.key} id={id} label={field.label} hint={field.hint} error={errors[field.key]}>
                <Input
                  id={id}
                  ref={(node) => {
                    refs.current[field.key] = node;
                  }}
                  type="number"
                  inputMode="numeric"
                  min={field.min}
                  max={field.max}
                  value={values[field.key]}
                  onChange={(event) => setValues((current) => ({ ...current, [field.key]: event.target.value }))}
                  invalid={Boolean(errors[field.key])}
                  aria-describedby={fieldDescribedBy(id, { hint: true, error: Boolean(errors[field.key]) })}
                  className="w-32"
                />
              </Field>
            );
          })}
        </div>
        <div className="mt-2 flex justify-end">
          <Button type="submit" variant="primary" disabled={changed.length === 0} loading={save.isPending}>
            {copyAdmin.common.save}
          </Button>
        </div>
      </form>
    </AdminCard>
  );
}

/* ── 月度预算（仅 root）────────────────────────────────────────────────────── */

function BudgetSection() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: adminKeys.budget, queryFn: () => api.costSummary('mtd') });
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const budget = query.data?.budget.monthlyUsd;

  useEffect(() => {
    if (budget !== undefined) setValue(String(budget));
  }, [budget]);

  const save = useMutation({
    mutationFn: (n: number) => api.setBudget(n),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: adminKeys.budget });
      notify.success(t.budget.saved, t.savedAudited);
    },
    onError: notifyFailure,
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const n = Number(value);
    if (value.trim() === '' || !Number.isFinite(n) || n < 0) {
      setError(t.budget.invalid);
      inputRef.current?.focus();
      return;
    }
    setError(null);
    save.mutate(n);
  };

  return (
    <AdminCard title={t.budget.title} headingId="settings-budget">
      <p className="mb-3 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{t.budget.body}</p>
      {query.isError ? (
        <ErrorState error={query.error} title={t.loadFailed} onRetry={() => void query.refetch()} />
      ) : (
        <form onSubmit={submit} noValidate>
          <Field id="settings-budget-input" label={t.budget.label} error={error}>
            <Input
              id="settings-budget-input"
              ref={inputRef}
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              invalid={Boolean(error)}
              aria-describedby={fieldDescribedBy('settings-budget-input', { error: Boolean(error) })}
              className="w-40"
            />
          </Field>
          {query.data ? (
            <p className="mt-1 text-[11.5px] text-[var(--text-tertiary)]">
              {fill(t.budget.current, {
                spent: money(query.data.spentIncludingEstimatedUsd, 'USD'),
                budget: query.data.budget.monthlyUsd > 0 ? money(query.data.budget.monthlyUsd, 'USD') : t.budget.unlimited,
              })}
            </p>
          ) : null}
          <div className="mt-3 flex justify-end">
            <Button type="submit" variant="primary" loading={save.isPending} disabled={query.isPending}>
              {copyAdmin.common.save}
            </Button>
          </div>
        </form>
      )}
    </AdminCard>
  );
}

/* ── 精选自选（仅 root）────────────────────────────────────────────────────── */

function FeaturedSection() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: adminKeys.featured, queryFn: () => adminApi.adminFeatured() });
  const [term, setTerm] = useState('');
  const [hits, setHits] = useState<SymbolSearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const items: WatchlistEntry[] = query.data?.items ?? [];

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    const q = term.trim();
    if (!q || searching) return;
    setSearching(true);
    try {
      const result = await api.searchSymbols(q, 6);
      setHits(result.hits);
    } catch (error) {
      notifyFailure(error);
    } finally {
      setSearching(false);
    }
  };

  const add = useMutation({
    mutationFn: async (hit: SymbolSearchHit) => {
      const resolved = await api.resolveSymbol(hit.symbol);
      await adminApi.adminAddFeatured(resolved.instrument.id);
      return resolved.instrument.name || hit.name;
    },
    onSuccess: (name) => {
      notify.success(fill(t.featured.added, { name }), t.savedAudited);
      void queryClient.invalidateQueries({ queryKey: adminKeys.featured });
      setHits(null);
      setTerm('');
    },
    onError: notifyFailure,
  });

  const remove = useMutation({
    mutationFn: (entry: WatchlistEntry) => adminApi.adminRemoveFeatured(entry.instrument.id),
    onSuccess: (_data, entry) => {
      notify.success(fill(t.featured.removed, { name: entry.instrument.name }), t.savedAudited);
      void queryClient.invalidateQueries({ queryKey: adminKeys.featured });
    },
    onError: notifyFailure,
  });

  return (
    <AdminCard title={t.featured.title} headingId="settings-featured">
      <p className="mb-3 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{t.featured.body}</p>

      {query.isError ? (
        <ErrorState error={query.error} title={t.loadFailed} onRetry={() => void query.refetch()} />
      ) : query.isPending ? (
        <Skeleton className="h-20 w-full" />
      ) : items.length === 0 ? (
        <EmptyState title={t.featured.empty} className="py-5" />
      ) : (
        <ul className="mb-4 flex flex-wrap gap-2" aria-label={t.featured.title}>
          {items.map((entry) => (
            <li
              key={entry.instrument.id}
              className="flex items-center gap-2 rounded-full bg-[var(--bg-surface-2)] py-1 pl-3 pr-1.5 text-[12px] ring-1 ring-[var(--border-strong)]"
            >
              <span className="mono text-[var(--text-primary)]">{entry.instrument.symbol}</span>
              <span className="text-[var(--text-tertiary)]">{entry.instrument.name}</span>
              <button
                type="button"
                aria-label={fill(t.featured.remove, { name: entry.instrument.name })}
                disabled={remove.isPending}
                onClick={() => remove.mutate(entry)}
                className="flex size-5 items-center justify-center rounded-full text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--danger)] disabled:opacity-45"
              >
                <X className="size-3" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={(event) => void search(event)} noValidate className="mt-2">
        <label htmlFor="featured-search" className="mb-1.5 block text-[12px] font-medium text-[var(--text-secondary)]">
          {t.featured.searchLabel}
        </label>
        <div className="flex items-center gap-2">
          <Input
            id="featured-search"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            placeholder={t.featured.searchPlaceholder}
            autoComplete="off"
            leadingIcon={<Search className="size-3.5" />}
            className="min-w-0 max-w-[320px] flex-1"
          />
          <Button type="submit" variant="secondary" loading={searching} disabled={term.trim() === ''}>
            {t.featured.search}
          </Button>
        </div>
      </form>

      {hits ? (
        hits.length === 0 ? (
          <p className="mt-3 text-[12px] text-[var(--text-tertiary)]">{t.featured.noHits}</p>
        ) : (
          <ul className="mt-3 divide-y divide-[var(--border-subtle)] rounded-[var(--radius-md)] border border-[var(--border-subtle)]">
            {hits.map((hit) => (
              <li key={`${hit.market}:${hit.symbol}`} className="flex items-center gap-3 px-3 py-2 text-[12.5px]">
                <span className="mono text-[var(--text-primary)]">{hit.symbol}</span>
                <span className="min-w-0 flex-1 truncate text-[var(--text-tertiary)]">{hit.name}</span>
                <Button
                  size="sm"
                  variant="secondary"
                  aria-label={`${t.featured.add} ${hit.symbol} ${hit.name}`}
                  loading={add.isPending && add.variables?.symbol === hit.symbol}
                  onClick={() => add.mutate(hit)}
                >
                  {t.featured.add}
                </Button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </AdminCard>
  );
}

/**
 * 系统设置（§24.3.15d）：三选一的注册模式卡片、默认日额度 / 新用户赠送 / 全站日总量上限，
 * root 额外看到月度预算与精选自选。**每个区域独立保存**，每次保存都写审计，立即生效（服务端每次请求读库）。
 */
export function SettingsForm() {
  const isRoot = useCan('admin.admins.manage');
  const query = useQuery({ queryKey: adminKeys.settings, queryFn: () => adminApi.adminSettings() });
  const settings = query.data;

  return (
    <div>
      <AdminPageHeader title={t.title} subtitle={t.subtitle} />
      {query.isError ? (
        <ErrorState error={query.error} title={t.loadFailed} onRetry={() => void query.refetch()} />
      ) : !settings ? (
        <div role="status" aria-busy="true" aria-label={copyAdmin.common.loading} className="space-y-4">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-56 w-full" />
        </div>
      ) : (
        <div className="space-y-4">
          <RegistrationSection settings={settings} />
          <RecoverySection settings={settings} />
          <QuotaSection settings={settings} />
          {isRoot ? <BudgetSection /> : null}
          {isRoot ? <FeaturedSection /> : null}
        </div>
      )}
    </div>
  );
}
