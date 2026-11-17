'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api } from '@/lib/api';
import { copy, fill } from '@/lib/copy';
import type { AragonCapability, ProbeStatus } from '@/lib/types';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';

export interface KeyDrawerProps {
  open: boolean;
  onClose: () => void;
  aragon?: AragonCapability | null;
}

/**
 * ★v9（K6 / K2 / §21.3.7）：`checkKey` / `openSettings` 打开的**说明抽屉**，不是裸链接。
 *
 * 为什么不做成"去 .env 里改一下"的一行提示：用户此刻不知道三件事——
 * ① 现在打的是哪个端点（host 与 provider）；② 这把密钥是从哪来的（`.env` 还是 CLI 的
 * profile）；③ 这个端点**此刻**通不通。前两件是 `capabilities.aragon` 里的
 * `baseUrlHost` 与 `keySource`（K1 的唯一取值域），第三件是 K2 的自检。
 *
 * 为什么探针的查询与 mutation 住在这里（而不是在 `RunStatusBadge` / `AppShell`）：
 * 本组件是 `next/dynamic` 懒加载的，于是"打开页面"不会多一次 `/agent/probe` 请求，
 * 而"跑一次自检"也只有一个入口。首屏预算（AC-35，240 kB gzip）因此不受影响。
 *
 * 抽屉里必须有一句话说清密钥的边界（红线 7）：它只在服务端子进程命令行里出现，
 * 从不写进日志 / 报告 / 数据库。否则用户会合理地怀疑"我填的密钥是不是被打进了报告"。
 */
export function KeyDrawer({ open, onClose, aragon }: KeyDrawerProps) {
  const [asked, setAsked] = useState(false);
  /** ★v9（K6）修订（I-105）：自检超过 15 秒仍没回来时要多说一句。 */
  const [slow, setSlow] = useState(false);
  const queryClient = useQueryClient();

  const status = useQuery<ProbeStatus>({
    queryKey: ['agent-probe'],
    queryFn: () => api.probeStatus(),
    enabled: open,
    staleTime: 15_000,
    retry: false,
  });

  const probe = useMutation({
    mutationFn: () => api.probeEngine(true),
    onSuccess: () => {
      setAsked(true);
      void queryClient.invalidateQueries({ queryKey: ['agent-probe'] });
      void queryClient.invalidateQueries({ queryKey: ['capabilities'] });
    },
  });

  // §21.3.7：实测 argv 路径 **10.23 s**（E-86），v9 按 8.7 s 定的 10 秒会不够。
  // 15 秒后按钮仍是 busy（真正的兜底是探针自己的 `--max-duration 90s`），但必须多说
  // 一句：否则用户面对一个转了很久的按钮只会再点第二下，而第二下拿到的是 409。
  const pending = probe.isPending;
  useEffect(() => {
    if (!pending) {
      setSlow(false);
      return;
    }
    const timer = window.setTimeout(() => setSlow(true), 15_000);
    return () => window.clearTimeout(timer);
  }, [pending]);

  const keySource = aragon?.keySource ?? 'none';
  const keyLine =
    keySource === 'env'
      ? copy.engine.keyEnv
      : keySource === 'cli_profile'
        ? copy.engine.keyCliProfile
        : copy.engine.keyNone;
  const stale = status.data?.staleReason
    ? (copy.engine.staleReasons[status.data.staleReason] ?? null)
    : null;
  const verified = Boolean(aragon?.verified ?? status.data?.verified);
  const lastProbe = status.data?.last ?? null;

  return (
    <Dialog open={open} onOpenChange={(next: boolean) => (next ? undefined : onClose())}>
      <DialogContent title={copy.engine.checkKeyTitle} className="w-[min(92vw,480px)] p-0">
        <div className="space-y-3 p-4 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">{copy.engine.checkKeyTitle}</h2>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-[var(--text-tertiary)]">端点</dt>
            <dd className="mono truncate text-[var(--text-primary)]">
              {aragon?.baseUrlHost ?? '—'}
              {aragon?.provider ? ` · ${aragon.provider}` : ''}
              {aragon?.model ? `/${aragon.model}` : ''}
            </dd>
            <dt className="text-[var(--text-tertiary)]">密钥来源</dt>
            <dd className="flex items-center gap-1 text-[var(--text-primary)]">
              <KeyRound className="size-3" aria-hidden="true" />
              {keyLine}
            </dd>
            <dt className="text-[var(--text-tertiary)]">验证</dt>
            <dd data-testid="key-drawer-verification">
              {verified
                ? fill(copy.engine.probeOk, { ms: String(lastProbe?.ms ?? 0) })
                : (stale ?? copy.engine.unverified)}
            </dd>
          </dl>

          <p className="flex items-start gap-1.5 rounded-[var(--radius-sm)] bg-[var(--bg-surface-1)] p-2 text-[11px] text-[var(--text-tertiary)]">
            <ShieldCheck className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
            {copy.engine.keySafety}
          </p>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={probe.isPending}
              data-testid="key-drawer-probe"
              onClick={() => probe.mutate()}
            >
              {probe.isPending ? copy.engine.probing : copy.engine.probeButton}
            </Button>
            {aragon?.verifiedAt ? (
              <span className="text-[10.5px] text-[var(--text-tertiary)]">{aragon.verifiedAt}</span>
            ) : null}
            {status.data?.probeCostHintUsd ? (
              <span className="text-[10.5px] text-[var(--text-tertiary)]">
                ≈ ${status.data.probeCostHintUsd.toFixed(4)}
              </span>
            ) : null}
          </div>

          {slow && pending ? (
            <p className="text-[10.5px] text-[var(--text-tertiary)]" data-testid="key-drawer-probe-slow">
              {copy.engine.probeSlow}
            </p>
          ) : null}

          {/* 探针失败的码只显示一行（不弹错、不阻断生成，硬约束 2）。 */}
          {asked && lastProbe && lastProbe.ok === false ? (
            <p className="text-[11px] text-[var(--warn)]" data-testid="key-drawer-probe-error">
              {fill(copy.engine.probeFailed, { code: lastProbe.code ?? 'internal' })}
              {lastProbe.message ? ` · ${lastProbe.message}` : ''}
            </p>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
