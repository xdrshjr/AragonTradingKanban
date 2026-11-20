import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RecoveryKeyDialog } from '@/components/auth/RecoveryKeyDialog';
import { FALLBACK_AUTH_CONFIG, RegisterForm } from '@/components/auth/RegisterForm';
import { RotateRecoveryKeyDialog } from '@/components/auth/RotateRecoveryKeyDialog';
import { api, ApiError } from '@/lib/api';
import { clearRecoveryUnsaved, readRecoveryUnsaved } from '@/lib/recoveryKey';
import type { CaptchaChallenge, RegisterResult } from '@/lib/types';

import { renderWithMe, userMe } from './authHarness';

/**
 * AC-224（恢复码弹窗 / 注册后展示）与 AC-250（轮换请求单飞）。
 * 明文恢复码的“只出现一次”：注册成功后它只存在于弹窗 state，**不会**随 Me 进入 onSuccess / ['me'] 缓存。
 */

const KEY = 'K7QM4-XNP2R-ABCDE-12345';
const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const challenge = (id: string): CaptchaChallenge => ({ challengeId: id, image: `${PIXEL}#${id}`, width: 160, height: 56, expiresIn: 300 });
const apiError = (status: number, code: string, detail: Record<string, unknown> = {}) =>
  new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });

beforeEach(() => {
  clearRecoveryUnsaved();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('RecoveryKeyDialog', () => {
  function setup(props: Partial<React.ComponentProps<typeof RecoveryKeyDialog>> = {}) {
    const onContinue = vi.fn();
    const onSkip = vi.fn();
    renderWithMe(<RecoveryKeyDialog open recoveryKey={KEY} username="alice" onContinue={onContinue} onSkip={onSkip} {...props} />);
    return { onContinue, onSkip };
  }

  it('展示分组恢复码；“继续”在勾选“我已保存”之前 disabled，并用 aria-describedby 指向原因', async () => {
    const user = userEvent.setup();
    const { onContinue } = setup();
    expect(screen.getByTestId('recovery-key-value')).toHaveTextContent(KEY);

    const next = screen.getByRole('button', { name: '继续' });
    expect(next).toBeDisabled();
    const reasonId = next.getAttribute('aria-describedby');
    expect(reasonId).toBeTruthy();
    expect(document.getElementById(reasonId!)).toHaveTextContent('请先保存恢复码并勾选确认');

    await user.click(screen.getByLabelText(/我已妥善保存/));
    expect(next).toBeEnabled();
    await user.click(next);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it('Esc 与点击遮罩都不关闭（防误触丢码）；也没有右上角关闭键', async () => {
    const user = userEvent.setup();
    const { onContinue, onSkip } = setup();
    await user.keyboard('{Escape}');
    expect(screen.getByTestId('recovery-key-dialog')).toBeInTheDocument();

    // 遮罩：Radix 的 DismissableLayer 监听 document 上的 pointerdown。
    fireEvent.pointerDown(document.body);
    fireEvent.mouseDown(document.body);
    fireEvent.click(document.body);
    expect(screen.getByTestId('recovery-key-dialog')).toBeInTheDocument();
    expect(onContinue).not.toHaveBeenCalled();
    expect(onSkip).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '关闭' })).toBeNull();
  });

  it('复制：把恢复码写进剪贴板；失败时给出可执行的提示', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    // user-event 的 setup 会安装自己的剪贴板桩：必须在它**之后**覆盖。
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    setup();
    await user.click(screen.getByRole('button', { name: '复制' }));
    expect(writeText).toHaveBeenCalledWith(KEY);
    expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument();

    writeText.mockRejectedValueOnce(new Error('denied'));
    await user.click(screen.getByRole('button', { name: '已复制' }));
    expect(await screen.findByText('复制失败，请手动选中')).toBeInTheDocument();
  });

  it('下载 .txt：文件名含用户名；内容含恢复码与“勿分享”说明', async () => {
    const blobs: Blob[] = [];
    const createObjectURL = vi.fn((blob: Blob) => {
      blobs.push(blob);
      return 'blob:mock';
    });
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const clicks: string[] = [];
    const original = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function click(this: HTMLAnchorElement) {
      clicks.push(this.download);
    };
    try {
      const user = userEvent.setup();
      setup();
      await user.click(screen.getByRole('button', { name: '下载 .txt' }));
      expect(clicks).toEqual(['aragon-recovery-alice.txt']);
      // jsdom 的 Blob 没有 `.text()`。
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(blobs[0]!);
      });
      expect(text).toContain(KEY);
      expect(text).toContain('alice');
      expect(text).toContain('请勿分享');
    } finally {
      HTMLAnchorElement.prototype.click = original;
    }
  });

  it('“跳过（不推荐）”是次级文字按钮，点击即回调（调用方记下“未保存”）', async () => {
    const user = userEvent.setup();
    const { onSkip } = setup();
    await user.click(screen.getByRole('button', { name: '跳过（不推荐）' }));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it('不传 onSkip 则没有跳过入口', () => {
    renderWithMe(<RecoveryKeyDialog open recoveryKey={KEY} username="alice" onContinue={() => {}} />);
    expect(screen.queryByRole('button', { name: /跳过/ })).toBeNull();
  });
});

describe('RegisterForm → 恢复码弹窗', () => {
  async function submitRegister(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText('用户名'), 'new_user');
    await user.type(screen.getByLabelText('密码'), 'Str0ng-pass-1');
    await user.type(screen.getByLabelText('确认密码'), 'Str0ng-pass-1');
    await user.type(await screen.findByLabelText('验证码'), 'ab12c');
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
  }

  it('注册成功先弹恢复码；继续之前不调用 onSuccess；onSuccess 拿到的 Me 不含 recoveryKey', async () => {
    const me = userMe('user');
    const result: RegisterResult = { ...me, recoveryKey: KEY };
    vi.spyOn(api, 'captcha').mockResolvedValue(challenge('c1'));
    vi.spyOn(api, 'register').mockResolvedValue(result);
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={FALLBACK_AUTH_CONFIG} onSuccess={onSuccess} />);
    await submitRegister(user);

    expect(await screen.findByTestId('recovery-key-dialog')).toHaveTextContent(KEY);
    expect(onSuccess).not.toHaveBeenCalled();

    await user.click(screen.getByLabelText(/我已妥善保存/));
    await user.click(screen.getByRole('button', { name: '继续' }));
    expect(onSuccess).toHaveBeenCalledTimes(1);
    const passed = onSuccess.mock.calls[0]![0] as Record<string, unknown>;
    expect(passed).not.toHaveProperty('recoveryKey');
    expect(JSON.stringify(passed)).not.toContain(KEY);
    expect(readRecoveryUnsaved(me.user!.id)).toBe(false);
  });

  it('选择“跳过（不推荐）”：照样进入工作台，并记下“尚未保存恢复码”供账号页提醒', async () => {
    const me = userMe('user');
    vi.spyOn(api, 'captcha').mockResolvedValue(challenge('c1'));
    vi.spyOn(api, 'register').mockResolvedValue({ ...me, recoveryKey: KEY });
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={FALLBACK_AUTH_CONFIG} onSuccess={onSuccess} />);
    await submitRegister(user);
    await user.click(await screen.findByRole('button', { name: '跳过（不推荐）' }));
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(readRecoveryUnsaved(me.user!.id)).toBe(true);
  });

  it('站点关闭自助找回（recoveryKey:null）：不弹窗，直接 onSuccess', async () => {
    const me = userMe('user');
    vi.spyOn(api, 'captcha').mockResolvedValue(challenge('c1'));
    vi.spyOn(api, 'register').mockResolvedValue({ ...me, recoveryKey: null });
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={FALLBACK_AUTH_CONFIG} onSuccess={onSuccess} />);
    await submitRegister(user);
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('recovery-key-dialog')).toBeNull();
  });
});

describe('RotateRecoveryKeyDialog（AC-250：单飞）', () => {
  function setup() {
    const onOpenChange = vi.fn();
    const onRotated = vi.fn();
    renderWithMe(<RotateRecoveryKeyDialog open onOpenChange={onOpenChange} username="alice" onRotated={onRotated} />);
    return { onOpenChange, onRotated };
  }

  it('密码为空：不发请求；错误当前密码：错误落在字段上并清空', async () => {
    const rotate = vi.spyOn(api, 'rotateRecoveryKey').mockRejectedValue(apiError(401, 'invalid_credentials'));
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole('button', { name: '验证并生成' }));
    expect(rotate).not.toHaveBeenCalled();
    expect(screen.getByText('请输入当前密码。')).toBeInTheDocument();

    await user.type(screen.getByLabelText('当前密码'), 'wrong-password');
    await user.click(screen.getByRole('button', { name: '验证并生成' }));
    expect(await screen.findByText('用户名或密码不正确。')).toBeInTheDocument();
    expect(screen.getByLabelText('当前密码')).toHaveValue('');
  });

  it('pending 时第二次点击 / 回车不再发请求；按钮 aria-busy + disabled；完成后展示新码', async () => {
    let release!: (value: { recoveryKey: string; setAt: string }) => void;
    const rotate = vi.spyOn(api, 'rotateRecoveryKey').mockImplementation(() => new Promise((resolve) => (release = resolve)));
    const user = userEvent.setup();
    const { onRotated, onOpenChange } = setup();
    await user.type(screen.getByLabelText('当前密码'), 'Str0ng-pass-1');
    await user.click(screen.getByRole('button', { name: '验证并生成' }));
    await waitFor(() => expect(rotate).toHaveBeenCalledTimes(1));

    const busy = screen.getByRole('button', { name: '验证并生成' });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute('aria-busy', 'true');
    await user.click(busy);
    await user.type(screen.getByLabelText('当前密码'), '{Enter}');
    expect(rotate).toHaveBeenCalledTimes(1);

    await act(async () => release({ recoveryKey: KEY, setAt: '2026-11-20T00:00:00Z' }));
    expect(await screen.findByTestId('recovery-key-dialog')).toHaveTextContent(KEY);
    expect(rotate).toHaveBeenCalledTimes(1);

    await user.click(screen.getByLabelText(/我已妥善保存/));
    await user.click(screen.getByRole('button', { name: '继续' }));
    expect(onRotated).toHaveBeenCalledWith('2026-11-20T00:00:00Z');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('429 / root_protected 等：显示一句人话 + 下一步，对话框保持打开', async () => {
    vi.spyOn(api, 'rotateRecoveryKey').mockRejectedValue(apiError(429, 'rate_limited', { retryAfterMs: 30_000 }));
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText('当前密码'), 'Str0ng-pass-1');
    await user.click(screen.getByRole('button', { name: '验证并生成' }));
    expect(await screen.findByText('操作过于频繁。')).toBeInTheDocument();
    expect(screen.getByText(/30 秒后再试/)).toBeInTheDocument();
    expect(screen.getByLabelText('当前密码')).toBeInTheDocument();
  });
});
