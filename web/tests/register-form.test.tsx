import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { FALLBACK_AUTH_CONFIG, RegisterForm } from '@/components/auth/RegisterForm';
import { api, ApiError } from '@/lib/api';
import { evaluatePassword } from '@/lib/passwordPolicy';
import type { AuthConfig, CaptchaChallenge, Me } from '@/lib/types';

import { renderWithMe, userMe } from './authHarness';

const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

function challenge(id: string): CaptchaChallenge {
  return { challengeId: id, image: `${PIXEL}#${id}`, width: 160, height: 56, expiresIn: 300 };
}

function apiError(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });
}

const ME: Me = userMe('user');
let register: MockInstance<typeof api.register>;
let captcha: MockInstance<typeof api.captcha>;

beforeEach(() => {
  register = vi.spyOn(api, 'register');
  captcha = vi.spyOn(api, 'captcha').mockResolvedValue(challenge('c1'));
});

afterEach(() => vi.restoreAllMocks());

function config(overrides: Partial<AuthConfig> = {}): AuthConfig {
  return { ...FALLBACK_AUTH_CONFIG, ...overrides };
}

async function fillValid(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('用户名'), 'new_user');
  await user.type(screen.getByLabelText('密码'), 'Str0ng-pass-1');
  await user.type(screen.getByLabelText('确认密码'), 'Str0ng-pass-1');
}

describe('passwordPolicy.evaluatePassword（实时规则）', () => {
  it('逐规则判定；任一规则不满足时强度封顶“较弱”', () => {
    expect(evaluatePassword('', 'alice').score).toBe(-1);
    const short = evaluatePassword('Ab1', 'alice');
    expect(short.rules.find((r) => r.id === 'min_length')?.ok).toBe(false);
    expect(short.ok).toBe(false);
    const repeated = evaluatePassword('aaaaaaaaaaaa', 'alice');
    expect(repeated.rules.find((r) => r.id === 'repeated_chars')?.ok).toBe(false);
    expect(repeated.score).toBeLessThanOrEqual(1);
    expect(evaluatePassword('12345678', 'alice').rules.find((r) => r.id === 'sequential_chars')?.ok).toBe(false);
    expect(evaluatePassword('xxALICExx9', 'alice').rules.find((r) => r.id === 'contains_username')?.ok).toBe(false);
    expect(evaluatePassword('lowercaseonly', 'bob').rules.find((r) => r.id === 'char_classes')?.ok).toBe(false);
    const strong = evaluatePassword('Str0ng-pass-1!', 'alice');
    expect(strong.ok).toBe(true);
    expect(strong.score).toBe(4);
  });

  it('策略数字来自配置（不写死）；服务端才能判的规则只在服务端报过后出现', () => {
    expect(evaluatePassword('Ab1xyz', 'u', { min: 6, max: 64, minClasses: 2 }).ok).toBe(true);
    expect(evaluatePassword('Ab1xyz', 'u', { min: 12, max: 64, minClasses: 2 }).ok).toBe(false);
    const flagged = evaluatePassword('Passw0rd-ok', 'u', undefined, ['common_password']);
    expect(flagged.rules.some((r) => r.id === 'common_password' && !r.ok)).toBe(true);
    expect(flagged.ok).toBe(false);
  });
});

describe('RegisterForm', () => {
  it('label / autocomplete：username、两个 new-password；规则提示来自 config', async () => {
    renderWithMe(<RegisterForm config={config({ usernamePolicy: { min: 4, max: 12, pattern: '^[a-z]+$' } })} onSuccess={() => {}} />);
    expect(screen.getByLabelText('用户名')).toHaveAttribute('autocomplete', 'username');
    expect(screen.getByLabelText('密码')).toHaveAttribute('autocomplete', 'new-password');
    expect(screen.getByLabelText('确认密码')).toHaveAttribute('autocomplete', 'new-password');
    expect(screen.getByText('4–12 位，仅限字母、数字与下划线。')).toBeInTheDocument();
    await waitFor(() => expect(captcha).toHaveBeenCalledWith('register'));
    expect(await screen.findByLabelText('验证码')).toBeInTheDocument();
  });

  it('强度条与规则清单随输入实时点亮', async () => {
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    const list = screen.getByRole('list', { name: '密码要求' });
    const rule = (id: string) => list.querySelector(`[data-rule="${id}"]`) as HTMLElement;

    expect(rule('min_length')).toHaveAttribute('data-ok', 'false');
    await user.type(screen.getByLabelText('密码'), 'Str0ng-pass-1');
    expect(rule('min_length')).toHaveAttribute('data-ok', 'true');
    expect(rule('char_classes')).toHaveAttribute('data-ok', 'true');
    expect(within(rule('min_length')).getByText('，已满足')).toBeInTheDocument();
    expect(screen.getByTestId('password-strength')).toHaveTextContent('密码强度：很强');

    await user.clear(screen.getByLabelText('密码'));
    await user.type(screen.getByLabelText('密码'), 'abc');
    expect(rule('min_length')).toHaveAttribute('data-ok', 'false');
    expect(within(rule('min_length')).getByText('，未满足')).toBeInTheDocument();
  });

  it('确认密码不一致：提交被拦下，错误关联到该字段并聚焦', async () => {
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    await screen.findByLabelText('验证码');
    await user.type(screen.getByLabelText('用户名'), 'new_user');
    await user.type(screen.getByLabelText('密码'), 'Str0ng-pass-1');
    await user.type(screen.getByLabelText('确认密码'), 'Str0ng-pass-2');
    await user.type(screen.getByLabelText('验证码'), 'ABCDE');
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));

    expect(register).not.toHaveBeenCalled();
    expect(screen.getByText('两次输入的密码不一致。')).toBeInTheDocument();
    expect(screen.getByLabelText('确认密码')).toHaveFocus();
    expect(screen.getByLabelText('确认密码')).toHaveAttribute('aria-invalid', 'true');
  });

  it('非法用户名 / 未勾选风险提示：各自聚焦第一个非法字段', async () => {
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    await user.type(screen.getByLabelText('用户名'), 'a b');
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(screen.getByText(/用户名需为 3–20 位字母、数字或下划线。/)).toBeInTheDocument();
    expect(screen.getByLabelText('用户名')).toHaveFocus();

    await user.clear(screen.getByLabelText('用户名'));
    await fillValid(user);
    await user.type(await screen.findByLabelText('验证码'), 'ABCDE');
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(screen.getByText('请先勾选风险提示。')).toBeInTheDocument();
    expect(screen.getByLabelText(/我已阅读/)).toHaveFocus();
    expect(register).not.toHaveBeenCalled();
  });

  it('成功：请求体含 captchaId / captchaAnswer / issuedAt（ms）/ 空蜜罐；Me 交给 onSuccess', async () => {
    register.mockResolvedValueOnce(ME);
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    const before = Date.now();
    renderWithMe(<RegisterForm config={config()} onSuccess={onSuccess} />);
    await fillValid(user);
    await user.type(await screen.findByLabelText('验证码'), 'ab12c');
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(ME));
    const body = register.mock.calls[0]![0] as unknown as Record<string, unknown>;
    expect(body).toMatchObject({ username: 'new_user', password: 'Str0ng-pass-1', website: '', captchaId: 'c1', captchaAnswer: 'ab12c' });
    expect(typeof body.issuedAt).toBe('number');
    expect(body.issuedAt as number).toBeGreaterThanOrEqual(before);
    expect(body).not.toHaveProperty('inviteCode');
  });

  it('蜜罐字段：aria-hidden、Tab 不到、默认空', () => {
    const { container } = renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    const trap = container.querySelector('input[name="website"]') as HTMLInputElement;
    expect(trap).not.toBeNull();
    expect(trap.tabIndex).toBe(-1);
    expect(trap.value).toBe('');
    expect(trap.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it('填了邀请码：验证码字段消失、请求体只带邀请码（免验证码通道）', async () => {
    register.mockResolvedValueOnce(ME);
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    await screen.findByLabelText('验证码');
    await user.click(screen.getByRole('button', { name: '我有邀请码' }));
    await user.type(screen.getByLabelText('邀请码'), 'k7qm-x2pd');
    expect(screen.queryByLabelText('验证码')).toBeNull();
    await fillValid(user);
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));

    await waitFor(() => expect(register).toHaveBeenCalled());
    const body = register.mock.calls[0]![0] as unknown as Record<string, unknown>;
    expect(body.inviteCode).toBe('K7QM-X2PD');
    expect(body).not.toHaveProperty('captchaId');
  });

  it('invite 模式：邀请码必填、展开、不请求验证码', async () => {
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config({ registrationMode: 'invite' })} onSuccess={() => {}} />);
    expect(screen.getByLabelText('邀请码')).toBeVisible();
    expect(screen.getByText('当前仅限邀请注册，请填写管理员提供的邀请码。')).toBeInTheDocument();
    expect(captcha).not.toHaveBeenCalled();
    await fillValid(user);
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(register).not.toHaveBeenCalled();
    expect(screen.getByLabelText('邀请码')).toHaveFocus();
  });

  it('closed 模式：显示“注册已关闭”与联系管理员提示，没有表单', () => {
    renderWithMe(<RegisterForm config={config({ registrationMode: 'closed', registrationOpen: false })} onSuccess={() => {}} />);
    expect(screen.getByTestId('register-closed')).toHaveTextContent('注册已关闭');
    expect(screen.getByText(/请联系管理员获取账号或邀请码/)).toBeInTheDocument();
    expect(screen.queryByLabelText('用户名')).toBeNull();
  });

  it('验证码不可用（registrationOpen=false 而模式不是 closed）：提示 + 仍可用邀请码注册，不请求验证码', () => {
    renderWithMe(<RegisterForm config={config({ registrationOpen: false })} onSuccess={() => {}} />);
    expect(screen.getByText('注册暂不可用')).toBeInTheDocument();
    expect(screen.getByLabelText('邀请码')).toBeVisible();
    expect(captcha).not.toHaveBeenCalled();
  });

  it('服务端 username_taken：错误挂在用户名字段并聚焦；weak_password：点亮服务端规则并聚焦密码', async () => {
    register.mockRejectedValueOnce(apiError(409, 'username_taken'));
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    await fillValid(user);
    await user.type(await screen.findByLabelText('验证码'), 'ABCDE');
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(await screen.findByText('该用户名已被占用。')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('用户名')).toHaveFocus());

    register.mockRejectedValueOnce(apiError(422, 'weak_password', { rules: ['common_password'] }));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));
    expect(await screen.findByText('密码强度不足。')).toBeInTheDocument();
    const list = screen.getByRole('list', { name: '密码要求' });
    expect(list.querySelector('[data-rule="common_password"]')).toHaveAttribute('data-ok', 'false');
    await waitFor(() => expect(screen.getByLabelText('密码')).toHaveFocus());
  });

  it('captcha_invalid：采用响应附带的新图、清空输入、聚焦验证码', async () => {
    register.mockRejectedValueOnce(apiError(422, 'captcha_invalid', { challenge: challenge('c2') }));
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    await fillValid(user);
    await user.type(await screen.findByLabelText('验证码'), 'WRONG');
    await user.click(screen.getByLabelText(/我已阅读/));
    await user.click(screen.getByRole('button', { name: '注册并登录' }));

    await waitFor(() => expect(screen.getByAltText(/验证码，5 位字母数字/)).toHaveAttribute('src', challenge('c2').image));
    expect(screen.getByLabelText('验证码')).toHaveValue('');
    await waitFor(() => expect(screen.getByLabelText('验证码')).toHaveFocus());
  });

  it('“换一张”按钮取新图并清空输入', async () => {
    captcha.mockResolvedValueOnce(challenge('c1')).mockResolvedValueOnce(challenge('c5'));
    const user = userEvent.setup();
    renderWithMe(<RegisterForm config={config()} onSuccess={() => {}} />);
    await user.type(await screen.findByLabelText('验证码'), 'ABC');
    await user.click(screen.getAllByRole('button', { name: '换一张' })[0]!);
    await waitFor(() => expect(screen.getByAltText(/验证码，5 位字母数字/)).toHaveAttribute('src', challenge('c5').image));
    expect(screen.getByLabelText('验证码')).toHaveValue('');
  });
});
