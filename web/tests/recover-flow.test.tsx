import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { LoginForm } from '@/components/auth/LoginForm';
import { RecoverForm } from '@/components/auth/RecoverForm';
import { useCaptcha } from '@/components/auth/useCaptcha';
import { api, ApiError, createApi } from '@/lib/api';
import { formatRecoveryKey, isCompleteRecoveryKey, normalizeRecoveryKey } from '@/lib/recoveryKey';
import type { CaptchaChallenge } from '@/lib/types';

import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { renderWithMe } from './authHarness';

/**
 * AC-224（找回页）/ AC-239（前端部分：recover 验证码）/ AC-250（找回表单单飞）。
 * 后端行为（顺序、CAS、逐字节一致）在 api/tests/test_recovery.py；这里只证明前端不破坏它。
 */

const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const challenge = (id: string): CaptchaChallenge => ({ challengeId: id, image: `${PIXEL}#${id}`, width: 160, height: 56, expiresIn: 300 });
const apiError = (status: number, code: string, detail: Record<string, unknown> = {}) =>
  new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });

const KEY = 'K7QM4-XNP2R-ABCDE-12345';
const NEW_KEY = 'ZZZZZ-YYYYY-XXXXX-WWWWW';

let recover: MockInstance<typeof api.recover>;
let captcha: MockInstance<typeof api.captcha>;

beforeEach(() => {
  recover = vi.spyOn(api, 'recover');
  captcha = vi.spyOn(api, 'captcha').mockResolvedValue(challenge('c1'));
});
afterEach(() => vi.restoreAllMocks());

async function fill(user: ReturnType<typeof userEvent.setup>, key = KEY) {
  await user.type(screen.getByLabelText('用户名'), 'alice');
  await user.click(screen.getByLabelText('恢复码'));
  await user.paste(key);
  await user.type(screen.getByLabelText('新密码'), 'Str0ng-pass-1');
  await user.type(screen.getByLabelText('确认新密码'), 'Str0ng-pass-1');
  await user.type(await screen.findByLabelText('验证码'), 'ab12c');
}

describe('恢复码输入遮罩（lib/recoveryKey）', () => {
  it('粘贴整串 / 小写 / 空格 / 连字符 / O→0 / I,L→1 都被整理成 4×5 分组', () => {
    expect(formatRecoveryKey('k7qm4 xnp2r-abcde 12345')).toBe(KEY);
    expect(formatRecoveryKey('  K7QM4XNP2RABCDE12345  ')).toBe(KEY);
    expect(normalizeRecoveryKey('O0Il-LlIi')).toBe('00111111');
    expect(formatRecoveryKey('K7QM4XNP2RABCDE1234599999')).toBe(KEY); // 超长截断到 20 位
    expect(isCompleteRecoveryKey(KEY)).toBe(true);
    expect(isCompleteRecoveryKey('K7QM4-XNP2R')).toBe(false);
  });

  it('不在 Crockford 字母表里的字符被丢弃（U 与符号）', () => {
    expect(normalizeRecoveryKey('UU-!!-ab')).toBe('AB');
  });
});

describe('AC-239（前端）：recover 验证码用途', () => {
  it('api.captcha("recover") 请求 /auth/captcha?purpose=recover', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(challenge('x')), { status: 200, headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      await createApi({}).captcha('recover');
      const [url] = fetchMock.mock.calls[0] as unknown as [string];
      expect(url).toMatch(/\/api\/v1\/auth\/captcha\?purpose=recover$/);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('useCaptcha("recover", true) 取图时传 recover', async () => {
    const client = new QueryClient();
    const { result } = renderHook(() => useCaptcha('recover', true), {
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    });
    await waitFor(() => expect(result.current.challenge?.challengeId).toBe('c1'));
    expect(captcha).toHaveBeenCalledWith('recover');
  });

  it('/recover 表单渲染验证码图，且永远要求验证码（不像登录那样自适应）', async () => {
    renderWithMe(<RecoverForm onDone={() => {}} />);
    expect(await screen.findByLabelText('验证码')).toBeInTheDocument();
    expect(screen.getByAltText(/验证码/)).toBeInTheDocument();
    expect(captcha).toHaveBeenCalledWith('recover');
  });
});

describe('RecoverForm（AC-224）', () => {
  it('autocomplete：username / 两个 new-password；恢复码输入框不让浏览器记忆', async () => {
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await screen.findByLabelText('验证码');
    expect(screen.getByLabelText('用户名')).toHaveAttribute('autocomplete', 'username');
    expect(screen.getByLabelText('新密码')).toHaveAttribute('autocomplete', 'new-password');
    expect(screen.getByLabelText('确认新密码')).toHaveAttribute('autocomplete', 'new-password');
    expect(screen.getByLabelText('恢复码')).toHaveAttribute('autocomplete', 'off');
  });

  it('粘贴小写带空格的整串：输入框里是分组大写；提交的是规范化后的 20 位', async () => {
    recover.mockResolvedValueOnce({ recoveryKey: NEW_KEY, setAt: '2026-11-20T00:00:00Z' });
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user, ' k7qm4 xnp2r abcde 12o45 ');
    expect(screen.getByLabelText('恢复码')).toHaveValue('K7QM4-XNP2R-ABCDE-12045');

    await user.click(screen.getByRole('button', { name: '重置密码' }));
    await waitFor(() => expect(recover).toHaveBeenCalledTimes(1));
    expect(recover).toHaveBeenCalledWith({
      username: 'alice',
      recoveryKey: 'K7QM4XNP2RABCDE12045',
      newPassword: 'Str0ng-pass-1',
      captchaId: 'c1',
      captchaAnswer: 'ab12c',
    });
  });

  it('成功：先展示**新**恢复码（不自动登录）；勾选“已保存”并继续后才交给 onDone', async () => {
    recover.mockResolvedValueOnce({ recoveryKey: NEW_KEY, setAt: '2026-11-20T00:00:00Z' });
    const onDone = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={onDone} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '重置密码' }));

    const dialog = await screen.findByTestId('recovery-key-dialog');
    expect(dialog).toHaveTextContent(NEW_KEY);
    expect(onDone).not.toHaveBeenCalled();
    // 找回流程里没有“跳过”——此时用户未登录，丢了码就真丢了。
    expect(screen.queryByRole('button', { name: /跳过/ })).toBeNull();
    await user.click(screen.getByLabelText(/我已妥善保存/));
    await user.click(screen.getByRole('button', { name: '继续' }));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('recovery_invalid：统一话术 + 联系管理员出口；换一张验证码；焦点回用户名', async () => {
    recover.mockRejectedValueOnce(apiError(401, 'recovery_invalid'));
    captcha.mockResolvedValueOnce(challenge('c1')).mockResolvedValueOnce(challenge('c2'));
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '重置密码' }));

    const box = await screen.findByTestId('recover-error');
    await waitFor(() => expect(box).toHaveTextContent('用户名或恢复码不正确'));
    expect(box).toHaveTextContent('联系管理员');
    expect(box).toHaveTextContent('仍无法找回');
    await waitFor(() => expect(captcha).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText('验证码')).toHaveValue('');
    expect(screen.getByLabelText('用户名')).toHaveFocus();
  });

  it('weak_password：点亮规则清单、不指向恢复码；验证码被消耗所以换新', async () => {
    recover.mockRejectedValueOnce(apiError(422, 'weak_password', { rules: ['common_password'] }));
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '重置密码' }));
    await waitFor(() => expect(screen.getByText('密码强度不足。')).toBeInTheDocument());
    expect(screen.getByLabelText('新密码')).toHaveFocus();
    await waitFor(() => expect(captcha).toHaveBeenCalledTimes(2));
  });

  it('captcha_invalid：聚焦验证码并换图', async () => {
    recover.mockRejectedValueOnce(apiError(422, 'captcha_invalid'));
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '重置密码' }));
    await waitFor(() => expect(screen.getByLabelText('验证码')).toHaveFocus());
    expect(screen.getByLabelText('验证码')).toHaveAttribute('aria-invalid', 'true');
  });

  it('服务端返回 recovery_disabled：整表单换成“联系管理员”面板', async () => {
    recover.mockRejectedValueOnce(apiError(403, 'recovery_disabled'));
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '重置密码' }));
    expect(await screen.findByTestId('recover-disabled')).toHaveTextContent('联系管理员');
    expect(screen.queryByTestId('recover-form')).toBeNull();
  });

  it('站点关闭自助找回（disabled 属性）：不取验证码、不渲染表单', () => {
    renderWithMe(<RecoverForm disabled onDone={() => {}} />);
    expect(screen.getByTestId('recover-disabled')).toBeInTheDocument();
    expect(captcha).not.toHaveBeenCalled();
  });

  it('客户端校验：恢复码不完整 / 两次密码不一致 → 不发请求，聚焦第一个问题字段', async () => {
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await user.type(screen.getByLabelText('用户名'), 'alice');
    await user.click(screen.getByLabelText('恢复码'));
    await user.paste('K7QM4-XNP2R');
    await user.type(screen.getByLabelText('新密码'), 'Str0ng-pass-1');
    await user.type(screen.getByLabelText('确认新密码'), 'Str0ng-pass-2');
    await user.click(screen.getByRole('button', { name: '重置密码' }));
    expect(recover).not.toHaveBeenCalled();
    expect(screen.getByText(/恢复码应为 20 位/)).toBeInTheDocument();
    expect(screen.getByLabelText('恢复码')).toHaveFocus();
  });

  it('§25.3.6 无障碍：每个输入都有可见 label；错误在 aria-live 区；在验证码框按 Enter 即提交', async () => {
    recover.mockRejectedValueOnce(apiError(401, 'recovery_invalid'));
    const user = userEvent.setup();
    const { container } = renderWithMe(<RecoverForm onDone={() => {}} />);
    await screen.findByLabelText('验证码');
    container.querySelectorAll<HTMLInputElement>('input:not([type="hidden"])').forEach((input) => {
      expect(input.labels?.length ?? 0, input.name || input.id).toBeGreaterThan(0);
    });
    await user.type(screen.getByLabelText('用户名'), 'alice');
    await user.click(screen.getByLabelText('恢复码'));
    await user.paste(KEY);
    await user.type(screen.getByLabelText('新密码'), 'Str0ng-pass-1');
    await user.type(screen.getByLabelText('确认新密码'), 'Str0ng-pass-1');
    await user.type(screen.getByLabelText('验证码'), 'ab12c{Enter}');
    await waitFor(() => expect(recover).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('recover-error')).toHaveAttribute('aria-live', 'polite');
  });

  it('AC-250：请求 pending 时第二次点击/回车不再发请求；按钮 aria-busy + disabled；结束后可再次提交', async () => {
    let release!: (value: { recoveryKey: string; setAt: string }) => void;
    recover.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    recover.mockRejectedValueOnce(apiError(401, 'recovery_invalid'));
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user);

    const button = screen.getByRole('button', { name: '重置密码' });
    await user.click(button);
    await waitFor(() => expect(recover).toHaveBeenCalledTimes(1));
    const busy = screen.getByRole('button', { name: /正在重置/ });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute('aria-busy', 'true');

    // 再点两次、再按回车：仍只有 1 次请求。
    await user.click(busy);
    await user.click(busy);
    await user.type(screen.getByLabelText('用户名'), '{Enter}');
    expect(recover).toHaveBeenCalledTimes(1);

    await act(async () => release({ recoveryKey: NEW_KEY, setAt: 'x' }));
    await screen.findByTestId('recovery-key-dialog');
    expect(recover).toHaveBeenCalledTimes(1);
  });

  it('pending 结束（失败）后可以再次提交', async () => {
    recover.mockRejectedValueOnce(apiError(401, 'recovery_invalid'));
    recover.mockResolvedValueOnce({ recoveryKey: NEW_KEY, setAt: 'x' });
    captcha.mockResolvedValue(challenge('c2'));
    const user = userEvent.setup();
    renderWithMe(<RecoverForm onDone={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '重置密码' }));
    await waitFor(() => expect(screen.getByTestId('recover-error')).toHaveTextContent('不正确'));
    await user.type(screen.getByLabelText('验证码'), 'zz99z');
    await user.click(screen.getByRole('button', { name: '重置密码' }));
    await waitFor(() => expect(recover).toHaveBeenCalledTimes(2));
  });
});

describe('登录页 · “忘记密码？”与找回成功提示（AC-224）', () => {
  it('recoveryHref 给出时渲染指向 /recover 的链接；关闭自助找回（不传）时没有链接', () => {
    const view = renderWithMe(<LoginForm onSuccess={() => {}} recoveryHref="/recover" />);
    expect(screen.getByRole('link', { name: '忘记密码？' })).toHaveAttribute('href', '/recover');
    view.unmount();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    expect(screen.queryByRole('link', { name: '忘记密码？' })).toBeNull();
  });

  it('?recovered=1 对应的 notice 以 role=status 显示在表单顶部', () => {
    renderWithMe(<LoginForm onSuccess={() => {}} notice="密码已重置，其它设备已退出登录。请用新密码登录。" />);
    expect(screen.getByTestId('login-notice')).toHaveAttribute('role', 'status');
    expect(screen.getByTestId('login-notice')).toHaveTextContent('密码已重置');
  });

  it('弹窗里点“忘记密码？”先触发 onRecoveryClick（关弹窗）', async () => {
    const onRecoveryClick = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} recoveryHref="/recover" onRecoveryClick={onRecoveryClick} />);
    const link = screen.getByRole('link', { name: '忘记密码？' });
    link.addEventListener('click', (event) => event.preventDefault());
    await user.click(link);
    expect(onRecoveryClick).toHaveBeenCalledTimes(1);
  });
});
