'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { describeAuthError } from '@/lib/authErrorText';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import type { AdminUser } from '@/lib/types';

import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Textarea } from '../ui/Textarea';
import { notify } from '../ui/Toast';
import { SecretBox } from './AdminPrimitives';
import { adminKeys } from './keys';
import { needsTypedConfirm, type UserAction } from './userActions';

const t = copyAdmin.drawer;
const done = copyAdmin.users.done;

export interface UserActionRequest {
  action: UserAction;
  user: AdminUser;
}

/** 失败：一句人话 + 排障用的 `requestId`；对话框保持打开，用户可以重试或取消。 */
export function notifyFailure(error: unknown): void {
  const view = describeAuthError(error);
  notify.error(view.message, view.requestId ? fill(copyAdmin.common.withRequestId, { id: view.requestId }) : undefined);
}

/**
 * 用户行动作的**唯一执行处**：行菜单与用户抽屉的危险区都只是“发起请求”，确认与执行在这里。
 *
 * * 停用 / 启用 / 全部下线 / 任命：普通确认；
 * * 删除用户、降级 admin、重置 admin 密码：**输入用户名**才能确认（GitHub 式，§24.3.14）；
 * * 重置密码：成功后在**第二个对话框**里显示 12 位临时密码——只显示这一次，带复制；
 * * 解锁：无破坏性，直接执行，不弹确认。
 *
 * 前端的二次确认只是体验层；后端对 `delete` 另行校验 `confirm=<username>`，对权限矩阵另行裁决。
 */
export function UserActionDialogs({
  request,
  onClose,
}: {
  request: UserActionRequest | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [secret, setSecret] = useState<{ username: string; password: string } | null>(null);
  const lastRequest = useRef<UserActionRequest | null>(null);
  if (request) lastRequest.current = request;
  // 关闭动画期间 `request` 已是 null，但对话框内容仍要渲染完整的标题。
  const active = request ?? lastRequest.current;
  const open = request !== null;

  useEffect(() => {
    if (!request) setReason('');
  }, [request]);

  const refresh = (user: AdminUser) => {
    void queryClient.invalidateQueries({ queryKey: adminKeys.usersRoot });
    void queryClient.invalidateQueries({ queryKey: adminKeys.user(user.id) });
    void queryClient.invalidateQueries({ queryKey: adminKeys.overview });
  };

  const run = async (action: UserAction, user: AdminUser): Promise<boolean> => {
    try {
      switch (action) {
        case 'disable':
          await adminApi.adminPatchUser(user.id, {
            status: 'disabled',
            ...(reason.trim() ? { disabledReason: reason.trim() } : {}),
          });
          notify.success(fill(done.disabled, { name: user.username }));
          break;
        case 'enable':
          await adminApi.adminPatchUser(user.id, { status: 'active' });
          notify.success(fill(done.enabled, { name: user.username }));
          break;
        case 'resetPassword': {
          const result = await adminApi.adminResetPassword(user.id);
          setSecret({ username: user.username, password: result.temporaryPassword });
          break;
        }
        case 'revokeSessions': {
          const result = await adminApi.adminRevokeSessions(user.id);
          notify.success(fill(done.revoked, { name: user.username, n: result.revoked }));
          break;
        }
        case 'unlock':
          await adminApi.adminUnlockUser(user.id);
          notify.success(fill(done.unlocked, { name: user.username }));
          break;
        case 'delete':
          await adminApi.adminDeleteUser(user.id, user.username);
          notify.success(fill(done.deleted, { name: user.username }));
          break;
        case 'promote':
          await adminApi.adminPatchUser(user.id, { role: 'admin' });
          notify.success(fill(done.promoted, { name: user.username }));
          break;
        case 'demote':
          await adminApi.adminPatchUser(user.id, { role: 'user' });
          notify.success(fill(done.demoted, { name: user.username }));
          break;
        default:
          return false;
      }
      refresh(user);
      return true;
    } catch (error) {
      notifyFailure(error);
      return false;
    }
  };

  // 解锁没有确认步骤：请求一到就直接执行，然后立刻关掉请求。
  const unlockHandled = useRef<UserActionRequest | null>(null);
  useEffect(() => {
    if (request?.action === 'unlock' && unlockHandled.current !== request) {
      unlockHandled.current = request;
      void run('unlock', request.user).finally(onClose);
    }
    // `run` 每次渲染都是新函数；这里只关心“新的解锁请求到了”。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  const confirmFor = (action: UserAction, user: AdminUser) => {
    const name = user.username;
    const typed = needsTypedConfirm(action, user);
    const base = {
      open: open && request?.action === action,
      onOpenChange: (next: boolean) => {
        if (!next) onClose();
      },
      requireText: typed ? name : undefined,
      requireTextLabel: typed ? fill(t.typeLabel, { name }) : undefined,
    };
    const finish = async () => {
      if (await run(action, user)) onClose();
    };

    switch (action) {
      case 'disable':
        return (
          <ConfirmDialog
            {...base}
            title={fill(t.disableTitle, { name })}
            description={
              <div className="space-y-3">
                <p>{t.disableBody}</p>
                <div>
                  <label
                    htmlFor="disable-reason"
                    className="mb-1.5 block text-[12px] font-medium text-[var(--text-secondary)]"
                  >
                    {t.disableReasonLabel}
                  </label>
                  <Textarea
                    id="disable-reason"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    maxLength={200}
                    rows={2}
                    placeholder={t.disableReasonPlaceholder}
                  />
                </div>
              </div>
            }
            confirmLabel={t.disableConfirm}
            onConfirm={finish}
          />
        );
      case 'enable':
        return (
          <ConfirmDialog
            {...base}
            tone="primary"
            title={fill(t.enableTitle, { name })}
            description={t.enableBody}
            confirmLabel={t.enableConfirm}
            onConfirm={finish}
          />
        );
      case 'resetPassword':
        return (
          <ConfirmDialog
            {...base}
            title={fill(t.resetTitle, { name })}
            description={
              <div className="space-y-2">
                <p>{t.resetBody}</p>
                {user.role === 'admin' ? <p className="text-[var(--warn)]">{t.resetBodyAdmin}</p> : null}
              </div>
            }
            confirmLabel={t.resetConfirm}
            onConfirm={finish}
          />
        );
      case 'revokeSessions':
        return (
          <ConfirmDialog
            {...base}
            title={fill(t.revokeTitle, { name })}
            description={t.revokeBody}
            confirmLabel={t.revokeConfirm}
            onConfirm={finish}
          />
        );
      case 'delete':
        return (
          <ConfirmDialog
            {...base}
            title={fill(t.deleteTitle, { name })}
            description={t.deleteBody}
            confirmLabel={t.deleteConfirm}
            onConfirm={finish}
          />
        );
      case 'promote':
        return (
          <ConfirmDialog
            {...base}
            tone="primary"
            title={fill(t.promoteTitle, { name })}
            description={t.promoteBody}
            confirmLabel={t.promoteConfirm}
            onConfirm={finish}
          />
        );
      case 'demote':
        return (
          <ConfirmDialog
            {...base}
            title={fill(t.demoteTitle, { name })}
            description={t.demoteBody}
            confirmLabel={t.demoteConfirm}
            onConfirm={finish}
          />
        );
      default:
        return null;
    }
  };

  return (
    <>
      {active && active.action !== 'unlock' ? confirmFor(active.action, active.user) : null}

      {/* 临时密码：只在这里出现一次。关闭后无法再次查看。 */}
      <Dialog open={secret !== null} onOpenChange={(next) => (next ? undefined : setSecret(null))}>
        <DialogContent title={t.resetDoneTitle} description={t.resetDoneBody} hideClose className="w-[min(92vw,440px)] p-5">
          <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">
            {t.resetDoneTitle}
            {secret ? <span className="mono ml-2 text-[12px] font-normal text-[var(--text-tertiary)]">{secret.username}</span> : null}
          </h2>
          <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{t.resetDoneBody}</p>
          <div className="mt-3">
            <SecretBox value={secret?.password ?? ''} label={t.resetDoneTitle} />
          </div>
          <div className="mt-5 flex justify-end">
            <Button variant="primary" onClick={() => setSecret(null)}>
              {t.resetDoneClose}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
