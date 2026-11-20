import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { QuotaChip } from '@/components/auth/QuotaChip';
import { QuotaRing } from '@/components/auth/QuotaRing';
import { useAuthUi } from '@/lib/auth';
import { bucketView, chipText, formatReset, resetCountdown, tooltipText } from '@/lib/quota';

import { GUEST, bucket, renderWithMe, resetAuthUi, userMe } from './authHarness';

beforeEach(resetAuthUi);

describe('QuotaChip（§24.3.15c）', () => {
  it('“今日剩余 2/3”，是指向账号页的链接；aria-label 带口径与重置时间', () => {
    renderWithMe(<QuotaChip />, { me: userMe('user', { usedToday: 1, remaining: 2 }) });
    const chip = screen.getByTestId('quota-chip');
    expect(chip).toHaveTextContent('今日剩余 2/3');
    expect(chip.tagName).toBe('A');
    expect(chip).toHaveAttribute('href', '/account');
    expect(chip.getAttribute('aria-label')).toMatch(/生成额度：今日剩余 2\/3。日额度 2\/3 · .*重置/);
  });

  it('剩 1 次转 warn 色；充足时是中性色（文字永远在，不只靠颜色）', () => {
    const { unmount } = renderWithMe(<QuotaChip />, { me: userMe('user', { usedToday: 2, remaining: 1 }) });
    expect(screen.getByTestId('quota-chip').className).toMatch(/--warn/);
    unmount();
    renderWithMe(<QuotaChip />, { me: userMe('user', { usedToday: 0, remaining: 3 }) });
    expect(screen.getByTestId('quota-chip').className).not.toMatch(/--warn/);
    expect(screen.getByTestId('quota-chip').className).not.toMatch(/--danger/);
  });

  it('日额度用完但有赠送额度：显示“今日剩余 0/3 +5”，仍是链接（remaining>0）', () => {
    renderWithMe(<QuotaChip />, { me: userMe('user', { usedToday: 3, bonus: 5, remaining: 5 }) });
    const chip = screen.getByTestId('quota-chip');
    expect(chip).toHaveTextContent('今日剩余 0/3 +5');
    expect(chip.tagName).toBe('A');
  });

  it('额度为 0：danger 色，变成“打开额度对话框”的按钮', async () => {
    renderWithMe(<QuotaChip />, { me: userMe('user', { usedToday: 3, remaining: 0 }) });
    const chip = screen.getByTestId('quota-chip');
    expect(chip.tagName).toBe('BUTTON');
    expect(chip.className).toMatch(/--danger/);
    expect(chip).toHaveAttribute('aria-haspopup', 'dialog');
    await userEvent.click(chip);
    expect(useAuthUi.getState().quotaOpen).toBe(true);
  });

  it('游客 / 无额度信息 / 不限次：不渲染（头部保持安静）；账号页 showUnlimited 才显示“不限次”', () => {
    const { container, unmount } = renderWithMe(<QuotaChip />, { me: GUEST });
    expect(container).toBeEmptyDOMElement();
    unmount();

    const admin = renderWithMe(<QuotaChip />, { me: userMe('admin') });
    expect(admin.container).toBeEmptyDOMElement();
    admin.unmount();

    renderWithMe(<QuotaChip showUnlimited />, { me: userMe('admin') });
    expect(screen.getByTestId('quota-chip')).toHaveTextContent('不限次');
  });
});

describe('lib/quota 纯函数', () => {
  it('bucketView：日额度剩余 = limit - used；remaining 是权威余额', () => {
    const view = bucketView(bucket({ limit: 3, usedToday: 5, bonus: 2, remaining: 2 }))!;
    expect(view.dailyLeft).toBe(0);
    expect(view.remaining).toBe(2);
    expect(view.exhausted).toBe(false);
    expect(bucketView(bucket({ remaining: 0 }))!.exhausted).toBe(true);
    expect(bucketView(bucket({ limit: null, remaining: null }))!.unlimited).toBe(true);
    expect(bucketView(null)).toBeNull();
  });

  it('chipText / tooltipText', () => {
    const view = bucketView(bucket({ limit: 3, usedToday: 1, bonus: 5, remaining: 7 }))!;
    expect(chipText(view)).toBe('今日剩余 2/3 +5');
    const now = new Date('2026-11-20T04:00:00Z'); // 上海 12:00
    expect(tooltipText(view, '2026-11-20T16:00:00Z', 'Asia/Shanghai', now)).toBe('日额度 2/3 + 赠送 5 · 明日 00:00 重置');
    expect(tooltipText(bucketView(bucket({ usedToday: 1, remaining: 2 }))!, '2026-11-20T16:00:00Z', 'Asia/Shanghai', now)).toBe(
      '日额度 2/3 · 明日 00:00 重置',
    );
  });

  it('formatReset 用额度时区而不是浏览器时区；resetCountdown 向上取整', () => {
    const now = new Date('2026-11-20T04:00:00Z');
    expect(formatReset('2026-11-20T16:00:00Z', 'Asia/Shanghai', now).trim()).toBe('明日 00:00');
    expect(formatReset('2026-11-20T10:00:00Z', 'Asia/Shanghai', now).trim()).toBe('今日 18:00');
    expect(formatReset(null)).toBe('');
    expect(resetCountdown('2026-11-20T16:00:00Z', now)).toBe('12 小时 0 分后');
    expect(resetCountdown('2026-11-20T04:38:30Z', now)).toBe('39 分钟后');
  });
});

describe('QuotaRing', () => {
  it('role=img，aria-label 说清日额度与赠送；不限次显示 ∞', () => {
    const { unmount } = renderWithMe(<QuotaRing bucket={bucket({ usedToday: 1, bonus: 4, remaining: 6 })} />);
    expect(screen.getByRole('img', { name: '日额度剩余 2，共 3，另有赠送 4' })).toBeInTheDocument();
    unmount();
    renderWithMe(<QuotaRing bucket={bucket({ limit: null, remaining: null })} />);
    expect(screen.getByRole('img', { name: '不限次' })).toBeInTheDocument();
  });
});
