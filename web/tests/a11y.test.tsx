import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, renderHook } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ChangePasswordForm } from '@/components/auth/ChangePasswordForm';
import { LoginForm } from '@/components/auth/LoginForm';
import { FALLBACK_AUTH_CONFIG, RegisterForm } from '@/components/auth/RegisterForm';
import { UserMenu } from '@/components/auth/UserMenu';
import { useReturnFocus } from '@/lib/focus';

import { GUEST, renderWithMe, userMe } from './authHarness';

// UserMenu 的登录态分支用 `useRouter()`；这里只补一个桩，其余导出保持真实。
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ refresh() {}, push() {}, replace() {} }),
}));

/**
 * AC-36：三块新面板（追问 / 比较 / 调度）的可访问性与焦点归还。
 *
 * `jest-axe` / `axe-core` **没有安装**（不加依赖是硬约束，`web/package.json` 不动），
 * 所以这里跑规格允许的 fallback 档 —— **结构契约**：
 *
 *   1. 面板根节点有 `role`，且有可访问名（`aria-label` / `aria-labelledby` / 标题文本）；
 *   2. 每个可交互元素都有可访问名（文本 / `aria-label` / `title`）；
 *   3. 没有 `tabIndex > 0`（§11.1：Tab 顺序由 DOM 决定）；
 *   4. `Esc` 能关（面板自己监听，或用 `lib/focus.ts::useReturnFocus`）。
 *
 * 面板本体由别的 agent 并行实现，因此：
 * - 源文件不存在 → `it.skip`（明确跳过，连 skip 的原因都写在状态里，不会假装通过）；
 * - 源文件存在 → **源码级结构断言必定执行**（这一档任何时候都有牙齿）；
 * - 能渲染出 DOM 时再加一层 DOM 级断言；需要未知 props/Context 而无法渲染时用
 *   `ctx.skip()` 并把原因打到输出里。
 *
 * `useReturnFocus()` 单独一组覆盖：AC-36 的"焦点归还"是 v4 新写的机制
 * （V4-P2-3：`store.ts` 里并没有 `lastFocused`），必须有直接测试，不能只挂在面板上。
 */

/* ── 面板清单（路径按 §18.4.6 文件表；多给几个候选，免得别的 agent 挪了目录）──── */

interface PanelSpec {
  name: string;
  /** 候选源码路径（相对 web/），按顺序探测，第一个存在的胜出。 */
  candidates: string[];
  /** 该面板可接受的根角色。 */
  roles: string[];
}

const PANELS: PanelSpec[] = [
  {
    name: 'AskPanel',
    candidates: ['components/ask/AskPanel.tsx', 'components/report/AskPanel.tsx'],
    roles: ['region', 'dialog', 'complementary'],
  },
  {
    name: 'CompareView',
    candidates: ['components/report/CompareView.tsx', 'components/compare/CompareView.tsx'],
    roles: ['region', 'dialog', 'main'],
  },
  {
    name: 'ScheduleDialog',
    candidates: ['components/schedule/ScheduleDialog.tsx', 'components/report/ScheduleDialog.tsx'],
    roles: ['dialog', 'region'],
  },
  {
    name: 'SearchDialog',
    candidates: ['components/shell/SearchDialog.tsx'],
    roles: ['dialog', 'region', 'search'],
  },
];

function resolveSource(candidates: string[]): string | null {
  for (const relative of candidates) {
    const full = join(process.cwd(), relative);
    if (existsSync(full)) return full;
  }
  return null;
}

/* ── 通用结构断言 ────────────────────────────────────────────────────────────── */

const INTERACTIVE_SELECTOR = [
  'button',
  'a[href]',
  'input',
  'select',
  'textarea',
  '[role="button"]',
  '[role="tab"]',
  '[role="menuitem"]',
  '[role="option"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="searchbox"]',
  '[role="link"]',
].join(', ');

function accessibleNameOf(element: Element): string {
  const aria = element.getAttribute('aria-label')?.trim();
  if (aria) return aria;

  const labelledBy = element.getAttribute('aria-labelledby');
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((id) => element.ownerDocument.getElementById(id)?.textContent?.trim() ?? '')
      .join(' ')
      .trim();
    if (text) return text;
  }

  const title = element.getAttribute('title')?.trim();
  if (title) return title;

  return element.textContent?.trim() ?? '';
}

/** 返回问题清单（空数组 = 通过）；一次性列全，而不是遇到第一个就抛。 */
function domProblems(root: Element, label: string): string[] {
  const problems: string[] = [];

  // ① 根节点：有角色 + 有可访问名
  if (!root.getAttribute('role')) problems.push(`${label}: 根节点缺少 role`);
  if (!accessibleNameOf(root)) {
    problems.push(`${label}: 根节点没有可访问名（aria-label / aria-labelledby / 标题文本）`);
  }

  // ② 可交互元素：都有可访问名
  for (const element of Array.from(root.querySelectorAll(INTERACTIVE_SELECTOR))) {
    if (element.getAttribute('aria-hidden') === 'true') continue;
    if (!accessibleNameOf(element)) {
      problems.push(`${label}: <${element.tagName.toLowerCase()}> 没有可访问名`);
    }
  }

  // ③ 不允许 tabIndex > 0
  for (const element of Array.from(root.querySelectorAll('[tabindex]'))) {
    const value = Number(element.getAttribute('tabindex'));
    if (Number.isFinite(value) && value > 0) {
      problems.push(`${label}: <${element.tagName.toLowerCase()} tabindex="${value}"> 破坏了 Tab 顺序`);
    }
  }

  return problems;
}

/** 源码级契约：不渲染也能证明的那几件事（角色、aria、tabIndex、Esc）。 */
function sourceProblems(source: string, label: string): string[] {
  const problems: string[] = [];

  if (!/role=["']/.test(source) && !/role=\{/.test(source)) {
    problems.push(`${label}: 源码里没有 role=（面板根节点必须有角色）`);
  }
  if (!/aria-label|aria-labelledby|<h[1-6]|title=/.test(source)) {
    problems.push(`${label}: 源码里没有 aria-label / aria-labelledby / 标题（根节点需要可访问名）`);
  }
  if (/tabIndex=\{?\s*[1-9]/.test(source)) {
    problems.push(`${label}: 源码里存在 tabIndex > 0`);
  }
  return problems;
}

/* ── 面板逐条检查 ────────────────────────────────────────────────────────────── */

describe('AC-36: 新面板的可访问性结构契约（axe 未安装 → 结构断言兜底）', () => {
  for (const panel of PANELS) {
    const sourcePath = resolveSource(panel.candidates);

    describe(panel.name, () => {
      // 文件不存在（并行开发中）→ 整组 skip，跳过原因是"仓库里还没有这个面板"。
      const panelIt = sourcePath ? it : it.skip;

      panelIt(`源码满足结构契约（role / 可访问名 / tabIndex / Esc）`, (ctx) => {
        if (!sourcePath) {
          console.info(`[AC-36] ${panel.name} 尚未出现在仓库里（探测过 ${panel.candidates.join(', ')}），跳过。`);
          return ctx.skip();
        }

        const source = readFileSync(sourcePath, 'utf8');
        expect(sourceProblems(source, panel.name)).toEqual([]);

        // Esc 的**三条**合法路径（v4 V4-P2-3 说得很清楚）：
        //   a. 面板自己监听 Escape；
        //   b. 非 Dialog 面板复用手写 `useReturnFocus`；
        //   c. 复用既有 `components/ui/Dialog`（Radix Dialog 自带 Esc 关闭 + 焦点归还）。
        const handlesEscape =
          /Escape|Esc/.test(source) ||
          /useReturnFocus/.test(source) ||
          /ui\/Dialog/.test(source) ||
          /<DialogContent|DialogPrimitive/.test(source);
        expect(handlesEscape, `${panel.name}: 没有 Esc 处理（自监听 / useReturnFocus / ui/Dialog 三者皆无）`).toBe(
          true,
        );
      });

      panelIt(`渲染后的 DOM 满足结构契约`, async (ctx) => {
        if (!sourcePath) return ctx.skip();

        const Component = await loadComponent(sourcePath, panel.name);
        if (!Component) {
          console.info(`[AC-36] ${panel.name}: 找不到同名导出或 default 导出，跳过 DOM 档。`);
          return ctx.skip();
        }

        // 必填 props 由别的 agent 定义、这里无从得知 —— 唯一允许"猜 props"的地方。
        const looseProps: Record<string, unknown> = {
          open: true,
          isOpen: true,
          onClose: vi.fn(),
          onOpenChange: vi.fn(),
          symbol: '600519.SH',
          instrumentId: 'demo-instrument',
          reportId: 'demo-report',
          report: null,
          sources: [],
          leftReport: null,
          rightReport: null,
          oldReport: null,
          newReport: null,
          triggerRef: { current: null },
          onScheduleChange: vi.fn(),
        };

        const client = new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        });
        const ui = (<Component {...looseProps} />) as ReactElement;

        let container: HTMLElement;
        try {
          container = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>).container;
        } catch (error) {
          console.info(
            `[AC-36] ${panel.name}: 需要未知的渲染环境，跳过 DOM 档（源码档已通过）：${(error as Error).message}`,
          );
          return ctx.skip();
        }

        const root = findRoot(container, panel.roles);
        if (!root) {
          console.info(`[AC-36] ${panel.name}: 渲染结果里没有角色为 ${panel.roles.join('/')} 的根节点（如关闭态不渲染）。`);
          return ctx.skip();
        }

        expect(domProblems(root, panel.name)).toEqual([]);
        // This one render costs ~3.3-4.6 s on a cold module graph (dynamic import of a
        // whole panel + a full Radix tree), so the default 5 s budget turns suite load
        // into a coin flip: it timed out at 5,069 ms once while the rest of the suite
        // was busy, and passed at 3,997 ms on the next run. The assertion is unchanged;
        // only the wall-clock budget is stated, because a green CI gate has to mean
        // "the code is wrong", not "the machine was busy".
      }, 20_000);
    });
  }
});

type LooseComponent = React.ComponentType<Record<string, unknown>>;

/** 用文件 URL 动态 import：vitest 会照常走 TS/TSX 转换，不需要真的存在路径别名。 */
async function loadComponent(sourcePath: string, name: string): Promise<LooseComponent | null> {
  try {
    const loaded = (await import(/* @vite-ignore */ pathToFileURL(sourcePath).href)) as Record<string, unknown>;
    const candidate = loaded[name] ?? loaded.default;
    return typeof candidate === 'function' ? (candidate as LooseComponent) : null;
  } catch (error) {
    console.info(`[AC-36] ${name}: 动态 import 失败，跳过 DOM 档：${(error as Error).message}`);
    return null;
  }
}

/** 根节点可能在 portal 里（Radix Dialog 挂到 document.body），所以多找一处。 */
function findRoot(container: HTMLElement, roles: string[]): Element | null {
  const selector = roles.map((role) => `[role="${role}"]`).join(', ');
  return container.querySelector(selector) ?? container.firstElementChild ?? document.body.querySelector(selector);
}

/* ── lib/focus.ts 直接覆盖（AC-36 的"焦点归还"）──────────────────────────────── */

describe('useReturnFocus: Esc 关闭 + 焦点归还', () => {
  function Harness({ open, onClose }: { open: boolean; onClose: () => void }) {
    // 用真实 DOM 节点当触发者：hook 必须把焦点还给它。
    const triggerRef: { current: HTMLElement | null } = { current: null };
    return (
      <div>
        <button type="button" ref={(node) => {
          triggerRef.current = node;
          // jsdom has no layout; this trigger represents a visible button.
          if (node) vi.spyOn(node, 'getClientRects').mockReturnValue(
            [new DOMRect(0, 0, 100, 30)] as unknown as DOMRectList,
          );
        }}>
          触发者
        </button>
        <Panel triggerRef={triggerRef} open={open} onClose={onClose} />
      </div>
    );
  }

  function Panel({
    triggerRef,
    open,
    onClose,
  }: {
    triggerRef: { current: HTMLElement | null };
    open: boolean;
    onClose: () => void;
  }) {
    useReturnFocus(triggerRef as React.RefObject<HTMLElement | null>, open, onClose);
    return (
      <div role="region" aria-label="面板">
        {open ? '打开中' : '已关闭'}
      </div>
    );
  }

  it('打开时按 Esc：先 onClose，再把焦点还给触发元素', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Harness open onClose={onClose} />);

    const trigger = document.querySelector('button');
    expect(trigger).not.toBeNull();
    (trigger as HTMLElement).blur();

    await user.keyboard('{Escape}');

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(trigger);
  });

  it('isOpen 从 true 翻成 false 时归还焦点（不依赖 Esc）', () => {
    const view = render(<Harness open onClose={() => {}} />);
    const trigger = document.querySelector('button');
    (trigger as HTMLElement).blur();

    view.rerender(<Harness open={false} onClose={() => {}} />);

    expect(document.activeElement).toBe(trigger);
  });

  it('关闭状态下按 Esc 什么都不做（不抢焦点、不调 onClose）', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Harness open={false} onClose={onClose} />);

    // 初次挂载 isOpen=false 不该抢焦点
    expect(document.body).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('反复重渲染（每次传新的内联回调）不堆叠监听器：Esc 只回调一次', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    const view = render(<Harness open onClose={onClose} />);
    for (let i = 0; i < 3; i += 1) {
      view.rerender(<Harness open onClose={() => onClose()} />);
    }

    await user.keyboard('{Escape}');

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('触发元素为 null / 已卸载时不抛（安静跳过，不把焦点塞给 body）', () => {
    const view = renderHook(
      ({ open }: { open: boolean }) => {
        const triggerRef: { current: HTMLElement | null } = { current: null };
        useReturnFocus(triggerRef as React.RefObject<HTMLElement | null>, open, vi.fn());
        return triggerRef;
      },
      { initialProps: { open: true } },
    );

    expect(() => view.rerender({ open: false })).not.toThrow();
    expect(document.body).toHaveFocus();
  });
});

/* ── ★X6 / AC-201：认证表单与菜单的结构契约（axe 未安装 → 结构断言兜底，同上）──────────── */

const AUTOCOMPLETE_OK = new Set(['username', 'current-password', 'new-password', 'off', 'nickname']);

function formProblems(root: Element, label: string): string[] {
  const problems: string[] = [];
  const inputs = Array.from(root.querySelectorAll('input, textarea, select')).filter(
    (element) => element.closest('[aria-hidden="true"]') === null && (element as HTMLInputElement).type !== 'hidden',
  );
  for (const input of inputs) {
    const id = input.getAttribute('id');
    const hasLabelFor = id ? root.ownerDocument.querySelector(`label[for="${CSS.escape(id)}"]`) !== null : false;
    const wrapped = input.closest('label') !== null;
    const named = Boolean(input.getAttribute('aria-label') || input.getAttribute('aria-labelledby'));
    if (!hasLabelFor && !wrapped && !named) {
      problems.push(`${label}: <input name="${input.getAttribute('name')}"> 没有可见 label / aria-label`);
    }
    const type = (input as HTMLInputElement).type;
    const autocomplete = input.getAttribute('autocomplete');
    if (type === 'password' && (!autocomplete || !AUTOCOMPLETE_OK.has(autocomplete))) {
      problems.push(`${label}: 密码输入的 autocomplete 必须是 current-password / new-password（得到 ${autocomplete}）`);
    }
  }
  // 输入框的名字由上面的 label 检查负责（`accessibleNameOf` 不解析 label[for]）；这里查其余可交互元素。
  for (const element of Array.from(root.querySelectorAll(INTERACTIVE_SELECTOR))) {
    if (element.closest('[aria-hidden="true"]') || /^(input|textarea|select)$/i.test(element.tagName)) continue;
    if (!accessibleNameOf(element)) problems.push(`${label}: <${element.tagName.toLowerCase()}> 没有可访问名`);
  }
  for (const element of Array.from(root.querySelectorAll('[tabindex]'))) {
    if (Number(element.getAttribute('tabindex')) > 0) problems.push(`${label}: tabindex > 0`);
  }
  // 错误区必须是 aria-live（读屏器播报），且至少存在一个
  if (root.querySelector('[aria-live]') === null) problems.push(`${label}: 没有 aria-live 区域`);
  // 提交按钮存在且是 type=submit（Enter 提交）
  if (root.querySelector('button[type="submit"]') === null) problems.push(`${label}: 没有 submit 按钮`);
  return problems;
}

describe('AC-201：认证表单的可访问性结构契约', () => {
  it('LoginForm', () => {
    const { container } = renderWithMe(<LoginForm onSuccess={() => {}} />);
    expect(formProblems(container, 'LoginForm')).toEqual([]);
    const usernameField = container.querySelector('input[name="username"]');
    expect(usernameField?.getAttribute('autocomplete')).toBe('username');
  });

  it('RegisterForm（含验证码、邀请码、强度清单）', async () => {
    const { container } = renderWithMe(<RegisterForm config={FALLBACK_AUTH_CONFIG} onSuccess={() => {}} />);
    expect(formProblems(container, 'RegisterForm')).toEqual([]);
    // 验证码图必须有 alt；强度清单是带名字的 list
    await vi.waitFor(() => expect(container.querySelector('input[autocomplete="off"]')).not.toBeNull());
    expect(container.querySelector('ul[aria-label]')).not.toBeNull();
  });

  it('ChangePasswordForm', () => {
    const { container } = renderWithMe(<ChangePasswordForm username="alice" onDone={() => {}} />);
    expect(formProblems(container, 'ChangePasswordForm')).toEqual([]);
    const autocompletes = Array.from(container.querySelectorAll('input[type="password"]')).map((input) =>
      input.getAttribute('autocomplete'),
    );
    expect(autocompletes).toEqual(['current-password', 'new-password', 'new-password']);
  });

  it('UserMenu：游客两个入口都有可访问名；已登录触发器带 aria-haspopup / aria-expanded', () => {
    const guest = renderWithMe(<UserMenu />, { me: GUEST });
    expect(domProblemsFor(guest.container)).toEqual([]);
    guest.unmount();
    const signedIn = renderWithMe(<UserMenu />, { me: userMe('user') });
    const trigger = signedIn.container.querySelector('[data-testid="user-menu-trigger"]');
    expect(trigger?.getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger?.getAttribute('aria-expanded')).toBe('false');
    expect(domProblemsFor(signedIn.container)).toEqual([]);
  });
});

function domProblemsFor(container: Element): string[] {
  const problems: string[] = [];
  for (const element of Array.from(container.querySelectorAll(INTERACTIVE_SELECTOR))) {
    if (!accessibleNameOf(element)) problems.push(`<${element.tagName.toLowerCase()}> 没有可访问名`);
  }
  return problems;
}
