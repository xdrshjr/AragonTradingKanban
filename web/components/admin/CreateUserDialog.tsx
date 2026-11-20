'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { useCan } from '@/lib/auth';
import { describeAuthError } from '@/lib/authErrorText';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import type { AdminUser } from '@/lib/types';

import { Field, fieldDescribedBy } from '../auth/Field';
import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { NativeSelect } from '../ui/Select';
import { notify } from '../ui/Toast';
import { SecretBox } from './AdminPrimitives';
import { adminKeys } from './keys';

const t = copyAdmin.create;

/** 与 `/auth/config` 兜底一致：3–20 位，`[A-Za-z0-9_]`。服务端另有保留词与占用校验。 */
const USERNAME_PATTERN = /^[A-Za-z0-9_]{3,20}$/;

interface Created {
  user: AdminUser;
  temporaryPassword?: string;
}

type FieldKey = 'username' | 'bonus' | 'form';

/**
 * 创建账号（§24.5.3 `POST /admin/users`）。
 *
 * * 角色默认 `user`；只有持 `admin.admins.manage`（root）才看得到“管理员”选项；
 * * 密码留空 → 服务端生成 12 位临时密码，**只在这次响应里出现**，成功后在本对话框里显示（带复制）；
 * * 错误逐字段落位：用户名规则 / 占用 / 弱口令回到对应输入框并聚焦。
 */
export function CreateUserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const canAppoint = useCan('admin.admins.manage');
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<'user' | 'admin'>('user');
  const [password, setPassword] = useState('');
  const [bonus, setBonus] = useState('0');
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const bonusRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    if (!open) {
      setUsername('');
      setDisplayName('');
      setRole('user');
      setPassword('');
      setBonus('0');
      setErrors({});
      setBusy(false);
      setCreated(null);
    }
  }, [open]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busyRef.current) return;

    const name = username.trim();
    const next: Partial<Record<FieldKey, string>> = {};
    const bonusNumber = Number(bonus === '' ? 0 : bonus);
    if (!USERNAME_PATTERN.test(name)) next.username = t.invalidUsername;
    if (!Number.isInteger(bonusNumber) || bonusNumber < 0 || bonusNumber > 20) next.bonus = t.invalidBonus;
    setErrors(next);
    if (next.username) return usernameRef.current?.focus();
    if (next.bonus) return bonusRef.current?.focus();

    busyRef.current = true;
    setBusy(true);
    try {
      const result = await adminApi.adminCreateUser({
        username: name,
        role: canAppoint ? role : 'user',
        ...(password ? { password } : {}),
        ...(displayName.trim() ? { displayName: displayName.trim() } : {}),
        ...(bonusNumber > 0 ? { bonusCredits: bonusNumber } : {}),
      });
      setCreated(result);
      notify.success(fill(t.done, { name: result.user.username }));
      void queryClient.invalidateQueries({ queryKey: adminKeys.usersRoot });
      void queryClient.invalidateQueries({ queryKey: adminKeys.overview });
    } catch (failure) {
      const view = describeAuthError(failure);
      if (view.code === 'username_taken' || view.code === 'invalid_username') {
        setErrors({ username: view.message });
        usernameRef.current?.focus();
      } else if (view.code === 'weak_password') {
        setErrors({ form: view.message });
        passwordRef.current?.focus();
      } else {
        setErrors({ form: view.requestId ? `${view.message} ${fill(copyAdmin.common.withRequestId, { id: view.requestId })}` : view.message });
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (busy ? undefined : onOpenChange(next))}>
      <DialogContent title={t.title} description={t.body} hideClose className="w-[min(92vw,460px)] p-5">
        {created ? (
          <div>
            <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{t.doneTitle}</h2>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
              <span className="mono text-[var(--text-primary)]">{created.user.username}</span>
              {' · '}
              {created.temporaryPassword ? t.doneBodyTemp : t.doneBodyOwn}
            </p>
            {created.temporaryPassword ? (
              <div className="mt-3">
                <SecretBox value={created.temporaryPassword} label={copyAdmin.drawer.resetDoneTitle} />
              </div>
            ) : null}
            <div className="mt-5 flex justify-end">
              <Button variant="primary" onClick={() => onOpenChange(false)}>
                {copyAdmin.invites.form.doneClose}
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={(event) => void submit(event)} noValidate>
            <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{t.title}</h2>
            <p className="mt-1 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{t.body}</p>

            <div className="mt-4 space-y-3">
              <Field id="create-username" label={t.username} hint={t.usernameHint} error={errors.username}>
                <Input
                  id="create-username"
                  ref={usernameRef}
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  invalid={Boolean(errors.username)}
                  aria-describedby={fieldDescribedBy('create-username', { hint: true, error: Boolean(errors.username) })}
                  className="font-mono"
                />
              </Field>
              <Field id="create-display" label={t.displayName}>
                <Input
                  id="create-display"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  maxLength={40}
                  autoComplete="off"
                />
              </Field>
              {canAppoint ? (
                <Field id="create-role" label={t.role}>
                  <NativeSelect
                    id="create-role"
                    value={role}
                    onChange={(event) => setRole(event.target.value as 'user' | 'admin')}
                    className="w-full"
                  >
                    <option value="user">{t.roleUser}</option>
                    <option value="admin">{t.roleAdmin}</option>
                  </NativeSelect>
                </Field>
              ) : null}
              <Field id="create-password" label={t.password} hint={t.passwordHint}>
                <Input
                  id="create-password"
                  ref={passwordRef}
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  aria-describedby={fieldDescribedBy('create-password', { hint: true })}
                />
              </Field>
              <Field id="create-bonus" label={t.bonus} error={errors.bonus}>
                <Input
                  id="create-bonus"
                  ref={bonusRef}
                  type="number"
                  inputMode="numeric"
                  value={bonus}
                  onChange={(event) => setBonus(event.target.value)}
                  invalid={Boolean(errors.bonus)}
                  aria-describedby={fieldDescribedBy('create-bonus', { error: Boolean(errors.bonus) })}
                  className="w-28"
                />
              </Field>
            </div>

            <div aria-live="polite" className="mt-2 min-h-0 text-[12px] text-[var(--danger)]">
              {errors.form}
            </div>

            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
                {copyAdmin.common.cancel}
              </Button>
              <Button type="submit" variant="primary" loading={busy}>
                {t.submit}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
