import { describe, expect, it } from 'vitest';

import { safeNext } from '@/lib/auth';
import { safeNext as safeNextPure } from '@/lib/safeNext';

/** AC-201：`?next=` 只允许站内相对路径；开放重定向的各种写法一律拒绝，退回 fallback。 */
describe('safeNext（开放重定向守卫）', () => {
  it('lib/auth 重新导出的就是同一个函数', () => {
    expect(safeNext).toBe(safeNextPure);
  });

  it.each([
    '/',
    '/600519.SH',
    '/reports?scope=trash',
    '/admin/users?q=ab#top',
    '/%E8%8C%85%E5%8F%B0',
  ])('放行站内相对路径：%s', (value) => {
    expect(safeNext(value)).toBe(value);
  });

  it.each([
    ['协议相对 //evil.com', '//evil.com'],
    ['协议相对带路径', '//evil.com/login'],
    ['反斜杠 /\\evil.com', '/\\evil.com'],
    ['绝对 URL', 'http://evil.com'],
    ['https 绝对 URL', 'https://evil.com/x'],
    ['javascript:', 'javascript:alert(1)'],
    ['data:', 'data:text/html;base64,AAAA'],
    ['不以 / 开头', 'evil.com'],
    ['空串', ''],
    ['编码后的 //', '/%2F/evil.com'],
    ['编码后的反斜杠', '/%5Cevil.com'],
    ['含换行（头注入）', '/ok\r\nSet-Cookie: x=1'],
    ['含 Tab', '/ok\t//evil'],
    ['含 NUL', '/ok\u0000'],
    ['畸形百分号编码', '/%E0%A4%A'],
    ['登录页本身（死循环）', '/login'],
    ['登录页带查询', '/login?next=/'],
    ['注册页本身', '/register'],
  ])('拒绝：%s', (_name, value) => {
    expect(safeNext(value, '/fallback')).toBe('/fallback');
  });

  it('缺省 fallback 是 /；非字符串输入同样退回', () => {
    expect(safeNext(undefined)).toBe('/');
    expect(safeNext(null)).toBe('/');
    expect(safeNext(['//evil.com', '/ok'])).toBe('/');
    expect(safeNext(['/ok', '//evil.com'])).toBe('/ok');
  });

  it('不做猜测性修复：/login-help 这种前缀相似的路径不被误伤', () => {
    expect(safeNext('/login-help')).toBe('/login-help');
  });

  it('超长输入拒绝', () => {
    expect(safeNext(`/${'a'.repeat(3000)}`)).toBe('/');
  });
});
