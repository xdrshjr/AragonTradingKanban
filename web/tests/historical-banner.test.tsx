import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { HistoricalBanner, type HistoricalBannerProps } from '@/components/report/HistoricalBanner';
import { copy, fill } from '@/lib/copy';
import { relativeTime } from '@/lib/format';

/**
 * ★v14（U2 / spec §23.3.3 / AC-141 / AC-142）：历史版本横幅。
 *
 * 这条横幅有三种"安静地坏掉"的方式，本文件的每条用例各守一种：
 *
 * 1. **它自己猜最新版**（★P1-7）。猜错的后果是把最新版渲染成历史版（或反之），
 *    而这两种错误在界面上长得**一模一样**——只有点「回到最新」之后才发现跳回了原地。
 *    所以这里用两次不同 `latestVersion` 的渲染证明它只渲染收到的那一个值。
 * 2. **按钮变成装饰**（AC-141 的原话是"点了会变"）。`onCompare` 缺席时仍画一个
 *    「版本比较」，点下去什么都不发生——比没有按钮更坏，因为它把"没实现"说成了"已实现"。
 * 3. **它偷偷拿 URL**。`report` 参数的写入/删除只能有一个写者（`WorkspaceClient.selectVersion`，
 *    AC-142）；横幅自己 `useRouter` 就会变成第二个写者，于是"回到最新"删参数这件事
 *    会在两处各写一遍、迟早写出不一致。最后一条用例直接读源码守这件事。
 *
 * 文案一律从 `copy.history` 插值而来（`fill` 与组件内部的 `text()` 同键同形）：
 * 断言里不写死中文，改文案不该让用例变红，改**含义**才该。
 */

const VERSION = 12;
const LATEST_VERSION = 13;
/** 22 小时前 / 4 小时前：都落在 `relativeTime` 的"小时"档，不会因为跨档而抖。 */
const GENERATED_AT = new Date(Date.now() - 22 * 3_600_000).toISOString();
const LATEST_GENERATED_AT = new Date(Date.now() - 4 * 3_600_000).toISOString();

const BANNER_TEXT = fill(copy.history.banner, { version: VERSION, when: relativeTime(GENERATED_AT) });
const LATEST_TEXT = fill(copy.history.latestIs, {
  version: LATEST_VERSION,
  when: relativeTime(LATEST_GENERATED_AT),
});
const BACK_TO_LATEST = fill(copy.history.backToLatest, { version: LATEST_VERSION });

function renderBanner(overrides: Partial<HistoricalBannerProps> = {}) {
  const handlers = { onBackToLatest: vi.fn(), onBaseThisVersion: vi.fn(), onCompare: vi.fn() };
  const props: HistoricalBannerProps = {
    version: VERSION,
    generatedAt: GENERATED_AT,
    latestVersion: LATEST_VERSION,
    latestGeneratedAt: LATEST_GENERATED_AT,
    ...handlers,
    ...overrides,
  };
  const view = render(<HistoricalBanner {...props} />);
  return { ...handlers, ...view };
}

describe('HistoricalBanner · 只说有意义的话（AC-141 / §23.3.3）', () => {
  it('★AC-141：横幅是 role="status"，两句话都插了真实的值', () => {
    renderBanner();

    const status = screen.getByRole('status');
    // AC-141 的原话："文案包含`历史版本`与最新版号"。
    expect(status.textContent).toContain('历史版本');

    // 当前版：`copy.history.banner` 插 {version} + 相对时间（不是 ISO 原文）。
    expect(within(status).getByText(BANNER_TEXT)).toBeInTheDocument();
    // 最新版：必须由调用方给出（组件不猜），并且也带相对时间。
    expect(within(status).getByText(LATEST_TEXT)).toBeInTheDocument();
  });

  it('★AC-141：最新版的相对时间缺失时退回当前版的生成时间，而不是渲染 "—"', () => {
    renderBanner({ latestGeneratedAt: null });

    const status = screen.getByRole('status');
    expect(
      within(status).getByText(
        fill(copy.history.latestIs, { version: LATEST_VERSION, when: relativeTime(GENERATED_AT) }),
      ),
    ).toBeInTheDocument();
  });
});

describe('HistoricalBanner · 三个动作都真的接上了（AC-141）', () => {
  it('★AC-141：每个按钮各调用一次自己的回调，且只调用一次', async () => {
    const handlers = renderBanner();

    await userEvent.click(screen.getByRole('button', { name: BACK_TO_LATEST }));
    await userEvent.click(screen.getByRole('button', { name: copy.history.baseThisVersion }));
    await userEvent.click(screen.getByRole('button', { name: copy.history.compare }));

    expect(handlers.onBackToLatest).toHaveBeenCalledTimes(1);
    expect(handlers.onBaseThisVersion).toHaveBeenCalledTimes(1);
    expect(handlers.onCompare).toHaveBeenCalledTimes(1);
  });

  it('★AC-141：没有 onCompare 就不画「版本比较」——横幅不提供死按钮', () => {
    const onBackToLatest = vi.fn();
    const onBaseThisVersion = vi.fn();
    render(
      <HistoricalBanner
        version={VERSION}
        generatedAt={GENERATED_AT}
        latestVersion={LATEST_VERSION}
        latestGeneratedAt={LATEST_GENERATED_AT}
        onBackToLatest={onBackToLatest}
        onBaseThisVersion={onBaseThisVersion}
      />,
    );

    expect(screen.queryByRole('button', { name: copy.history.compare })).toBeNull();
    // 反面：另外两个按钮确实在（否则上面那条 null 可能只是"整个横幅没渲染"）。
    expect(screen.getByRole('button', { name: BACK_TO_LATEST })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: copy.history.baseThisVersion })).toBeInTheDocument();
  });
});

describe('HistoricalBanner · busy 只禁用会写数据的那一半（§23.3.3）', () => {
  it('★生成中：「基于此版本更新」禁用，「回到最新」仍可点', async () => {
    const handlers = renderBanner({ busy: true });

    const base = screen.getByRole('button', { name: copy.history.baseThisVersion });
    expect(base).toBeDisabled();

    // 「回到最新」只是切版本，禁用它会让人以为连历史都看不了。
    const back = screen.getByRole('button', { name: BACK_TO_LATEST });
    expect(back).not.toBeDisabled();
    await userEvent.click(back);
    expect(handlers.onBackToLatest).toHaveBeenCalledTimes(1);
    expect(handlers.onCompare).not.toHaveBeenCalled();
  });
});

describe('HistoricalBanner · 最新版是调用方的权威值（★P1-7）', () => {
  it('★P1-7：换一个 latestVersion 就换一套文案——组件从不自己挑最新版', () => {
    const first = renderBanner();
    const firstText = screen.getByRole('status').textContent ?? '';
    expect(firstText).toContain(LATEST_TEXT);
    expect(screen.getByRole('button', { name: BACK_TO_LATEST })).toBeInTheDocument();
    first.unmount();

    // 同一个当前版（v12），换一个权威的"最新"= v40：横幅必须跟着走。
    renderBanner({ latestVersion: 40, latestGeneratedAt: LATEST_GENERATED_AT });
    const secondText = screen.getByRole('status').textContent ?? '';
    expect(secondText).toContain(
      fill(copy.history.latestIs, { version: 40, when: relativeTime(LATEST_GENERATED_AT) }),
    );
    expect(secondText).not.toContain(`v${LATEST_VERSION}`);
    expect(screen.getByRole('button', { name: fill(copy.history.backToLatest, { version: 40 }) })).toBeInTheDocument();
  });
});

describe('HistoricalBanner · 它不该知道 URL（AC-142 的唯一写者）', () => {
  it('★AC-142：源码里没有 useRouter / useSearchParams / window.location', () => {
    // jsdom 环境下 `import.meta.url` 不是 file: 协议，所以按 vitest 的 cwd（= `web/`）取。
    const source = readFileSync(resolve(process.cwd(), 'components/report/HistoricalBanner.tsx'), 'utf8');

    // 前置：读到的确实是这条横幅（否则下面的"没有"毫无意义）。
    expect(source).toContain('role="status"');
    expect(source).toContain('copy.history.banner');

    // `report` 参数的增删只在 `selectVersion` 一处发生；横幅只调用回调。
    expect(source).not.toMatch(/from\s+['"]next\/navigation['"]/);
    for (const forbidden of ['useRouter', 'useSearchParams', 'window.location']) {
      expect(source).not.toContain(forbidden);
    }
  });
});
