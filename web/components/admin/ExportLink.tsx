'use client';

import { Download } from 'lucide-react';

import { notify } from '../ui/Toast';

/**
 * CSV 导出入口（§25.3.5a）：一个**直链** `<a href download>`——浏览器带着 Cookie 同源直取，
 * 由服务端流式输出，不经 fetch、不进前端内存；过滤条件已编码进 `href`。
 *
 * 每次导出服务端都会写审计（`admin.export`）并限频；超限返回 429，浏览器会得到一个错误页——
 * 所以按钮旁有一句说明。无权限的角色由调用方不渲染（体验层，服务端仍裁决）。
 */
export function ExportLink({
  href,
  label,
  hint,
  started,
}: {
  href: string;
  label: string;
  hint: string;
  started: string;
}) {
  return (
    <a
      href={href}
      download
      title={hint}
      aria-label={`${label}。${hint}`}
      onClick={() => notify.info(started)}
      className="inline-flex h-8 select-none items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] px-3 text-[13px] font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-surface-3)]"
    >
      <Download className="size-3.5" aria-hidden="true" />
      {label}
    </a>
  );
}
