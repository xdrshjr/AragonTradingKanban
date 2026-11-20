import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { LoginForm } from '@/components/auth/LoginForm';
import { api, ApiError } from '@/lib/api';
import type { CaptchaChallenge, Me } from '@/lib/types';

import { renderWithMe, userMe } from './authHarness';

/**
 * AC-201：登录表单 a11y 与交互——autocomplete、错误后焦点、验证码自动换图、Enter 提交、防双击、统一话术。
 */

const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

function challenge(id: string): CaptchaChallenge {
  return { challengeId: id, image: `${PIXEL}#${id}`, width: 160, height: 56, expiresIn: 300 };
}

function apiError(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });
}

const ME: Me = userMe('user');

let login: MockInstance<typeof api.login>;
let captcha: MockInstance<typeof api.captcha>;

beforeEach(() => {
  login = vi.spyOn(api, 'login');
  captcha = vi.spyOn(api, 'captcha');
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function fill(user: ReturnType<typeof userEvent.setup>, name = 'alice', password = 'secret-pass') {
  await user.type(screen.getByLabelText('用户名'), name);
  await user.type(screen.getByLabelText('密码'), password);
}

describe('LoginForm', () => {
  it('每个输入有可见 label；autocomplete 为 username / current-password', () => {
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    const username = screen.getByLabelText('用户名');
    const password = screen.getByLabelText('密码');
    expect(username).toHaveAttribute('autocomplete', 'username');
    expect(password).toHaveAttribute('autocomplete', 'current-password');
    expect(password).toHaveAttribute('type', 'password');
    expect(screen.getByRole('button', { name: '登录' })).toHaveAttribute('type', 'submit');
    // 打开即聚焦用户名
    expect(username).toHaveFocus();
    // 第一次登录不带验证码
    expect(screen.queryByLabelText('验证码')).toBeNull();
  });

  it('Enter 提交；成功把 Me 交给 onSuccess；请求体带 trim 过的用户名与 remember', async () => {
    login.mockResolvedValueOnce(ME);
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={onSuccess} />);

    await user.type(screen.getByLabelText('用户名'), '  alice  ');
    await user.click(screen.getByLabelText(/保持登录 30 天/));
    await user.type(screen.getByLabelText('密码'), 'secret-pass{Enter}');

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(ME));
    expect(login).toHaveBeenCalledTimes(1);
    expect(login).toHaveBeenCalledWith({ username: 'alice', password: 'secret-pass', remember: true });
  });

  it('空用户名：不发请求，错误出现在 aria-live 区域并聚焦用户名；只有密码为空则聚焦密码', async () => {
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(login).not.toHaveBeenCalled();
    expect(screen.getByText('请输入用户名。')).toBeInTheDocument();
    expect(screen.getByLabelText('用户名')).toHaveFocus();
    expect(screen.getByLabelText('用户名')).toHaveAttribute('aria-invalid', 'true');

    await user.type(screen.getByLabelText('用户名'), 'alice');
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(screen.getByText('请输入密码。')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toHaveFocus();
  });

  it('invalid_credentials：统一话术、清空密码、焦点回到密码框', async () => {
    login.mockRejectedValueOnce(apiError(401, 'invalid_credentials'));
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByText('用户名或密码不正确。')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toHaveValue('');
    await waitFor(() => expect(screen.getByLabelText('密码')).toHaveFocus());
    // 提示区是 aria-live 的，且被表单 aria-describedby 关联
    const live = screen.getByTestId('login-error');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByTestId('login-form').getAttribute('aria-describedby')).toBe(live.id);
  });

  it('captcha_required：渲染验证码字段（用响应里附带的新图，不额外请求），焦点进验证码框', async () => {
    login.mockRejectedValueOnce(apiError(422, 'captcha_required', { challenge: challenge('c1') }));
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '登录' }));

    const input = await screen.findByLabelText('验证码');
    expect(input).toHaveAttribute('autocomplete', 'off');
    expect(screen.getByAltText(/验证码，5 位字母数字/)).toHaveAttribute('src', challenge('c1').image);
    expect(captcha).not.toHaveBeenCalled();
    await waitFor(() => expect(input).toHaveFocus());
  });

  it('验证码错（captcha_invalid）：自动换成响应附带的新图、清空输入、焦点回到验证码框；重试请求带 captchaId', async () => {
    login
      .mockRejectedValueOnce(apiError(422, 'captcha_required', { challenge: challenge('c1') }))
      .mockRejectedValueOnce(apiError(422, 'captcha_invalid', { challenge: challenge('c2') }))
      .mockResolvedValueOnce(ME);
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={onSuccess} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '登录' }));

    const input = await screen.findByLabelText('验证码');
    await user.type(input, 'ABCDE');
    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(screen.getByAltText(/验证码，5 位字母数字/)).toHaveAttribute('src', challenge('c2').image));
    expect(screen.getByLabelText('验证码')).toHaveValue('');
    await waitFor(() => expect(screen.getByLabelText('验证码')).toHaveFocus());
    expect(screen.getByText('验证码不正确，已为你换了一张。')).toBeInTheDocument();

    await user.type(screen.getByLabelText('验证码'), 'xyzzy');
    await user.click(screen.getByRole('button', { name: '登录' }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(login).toHaveBeenLastCalledWith({
      username: 'alice',
      password: 'secret-pass',
      remember: false,
      captchaId: 'c2',
      captchaAnswer: 'xyzzy',
    });
  });

  it('invalid_credentials 带 captchaRequired：随后出现验证码字段，并主动取一张新图（响应没附带时）', async () => {
    login.mockRejectedValueOnce(apiError(401, 'invalid_credentials', { captchaRequired: true }));
    captcha.mockResolvedValueOnce(challenge('c9'));
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByLabelText('验证码')).toBeInTheDocument();
    await waitFor(() => expect(captcha).toHaveBeenCalledWith('login'));
  });

  it('防双击：提交中按钮 loading + disabled，连点只发一次请求', async () => {
    let resolve!: (me: Me) => void;
    login.mockImplementationOnce(() => new Promise<Me>((r) => (resolve = r)));
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    await fill(user);
    const button = screen.getByRole('button', { name: '登录' });
    await user.dblClick(button);
    await user.type(screen.getByLabelText('密码'), '{Enter}');

    expect(login).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /登录中/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /登录中/ })).toHaveAttribute('aria-busy', 'true');
    resolve(ME);
  });

  it('account_locked：提示里带等待时长，焦点回到用户名', async () => {
    login.mockRejectedValueOnce(apiError(423, 'account_locked', { retryAfterMs: 15 * 60_000 }));
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    await fill(user);
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText(/暂时锁定/)).toBeInTheDocument();
    expect(screen.getByText(/15 分钟后再试/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('用户名')).toHaveFocus());
  });

  it('显示 / 隐藏密码按钮是 aria-pressed 开关', async () => {
    const user = userEvent.setup();
    renderWithMe(<LoginForm onSuccess={() => {}} />);
    const toggle = screen.getByRole('button', { name: '显示密码' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await user.click(toggle);
    expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: '隐藏密码' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('“注册”：弹窗里是切标签的按钮，页面里是指向 /register 的链接', async () => {
    const onSwitch = vi.fn();
    const { unmount } = renderWithMe(<LoginForm onSuccess={() => {}} onSwitchToRegister={onSwitch} />);
    await userEvent.click(screen.getByRole('button', { name: '注册' }));
    expect(onSwitch).toHaveBeenCalled();
    unmount();
    renderWithMe(<LoginForm onSuccess={() => {}} registerHref="/register?next=%2Faccount" />);
    expect(screen.getByRole('link', { name: '注册' })).toHaveAttribute('href', '/register?next=%2Faccount');
  });
});
