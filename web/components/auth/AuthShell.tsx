import Link from 'next/link';

import { copy } from '@/lib/copy';
import { BrandMark } from '../ui/BrandMark';

export interface AuthShellProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  /** 卡片下方的次要内容（如“返回首页”）。 */
  below?: React.ReactNode;
  /** 全屏纯净模式（强制改密页）：不显示“返回首页”之类的出口。 */
  bare?: boolean;
}

/**
 * 认证类独立页面（登录 / 注册 / 强制改密）的统一外壳。
 *
 * 视觉克制：深色画布上只有一束来自顶部的 accent 微光与一层几乎看不见的网格——
 * 让视线落在卡片本身；不放插画、不放营销文案。卡片宽度 `min(92vw, 400px)`，窄屏不横向滚动。
 */
export function AuthShell({ title, subtitle, children, below, bare = false }: AuthShellProps) {
  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center overflow-hidden px-4 py-10">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{ background: 'radial-gradient(60% 45% at 50% 0%, var(--accent-weak), transparent 70%)' }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-60"
        style={{
          backgroundImage:
            'linear-gradient(var(--border-subtle) 1px, transparent 1px), linear-gradient(90deg, var(--border-subtle) 1px, transparent 1px)',
          backgroundSize: '56px 56px',
          maskImage: 'radial-gradient(ellipse 70% 55% at 50% 0%, #000 0%, transparent 75%)',
          WebkitMaskImage: 'radial-gradient(ellipse 70% 55% at 50% 0%, #000 0%, transparent 75%)',
        }}
      />

      <div className="relative w-[min(92vw,400px)]">
        <div className="mb-6 flex items-center justify-center gap-2">
          {bare ? (
            <span className="flex items-center gap-2">
              <BrandMark size={28} />
              <span className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{copy.brand}</span>
            </span>
          ) : (
            <Link href="/" className="flex items-center gap-2" aria-label={`${copy.brand} ${copy.brandSuffix}`}>
              <BrandMark size={28} />
              <span className="flex items-baseline gap-1.5">
                <span className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{copy.brand}</span>
                <span className="text-[11.5px] text-[var(--text-tertiary)]">{copy.brandSuffix}</span>
              </span>
            </Link>
          )}
        </div>

        <section
          aria-labelledby="auth-title"
          className="rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] p-6 shadow-[var(--shadow-pop)]"
        >
          <header className="mb-5">
            <h1 id="auth-title" className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">
              {title}
            </h1>
            {subtitle ? (
              <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{subtitle}</p>
            ) : null}
          </header>
          {children}
        </section>

        {below ? <div className="mt-4 text-center text-[12px] text-[var(--text-tertiary)]">{below}</div> : null}
        <p className="mt-8 text-center text-[10.5px] leading-relaxed text-[var(--text-tertiary)]">
          本产品由 AI 生成研究报告，仅供研究参考，不构成投资建议。
        </p>
      </div>
    </main>
  );
}
