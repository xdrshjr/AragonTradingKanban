/**
 * `?next=` 的**开放重定向**守卫（spec §24.3.15b / AC-201）。
 *
 * 只允许**站内相对路径**：必须以单个 `/` 开头，不得是 `//host`（协议相对）、不得含反斜杠
 * （浏览器把 `/\evil.com` 当成 `//evil.com`）、不得含协议、不得含控制字符。登录/注册页本身
 * 也不是合法落点（登录后又回登录页是死循环）。任何不合格的值一律退回 `fallback`，**不做猜测性修复**。
 *
 * 这个文件刻意无 React 依赖：服务端 `page.tsx`、`middleware.ts` 与客户端表单共用同一份判定。
 */

const BACKSLASH = String.fromCharCode(92);

/** 控制字符（含 CR/LF/Tab/DEL）：既是头注入向量，也让 URL 解析器"帮你修正"出别的目标。 */
function hasControlChars(text: string): boolean {
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

export function safeNext(value: string | string[] | null | undefined, fallback = '/'): string {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 2048) return fallback;
  if (hasControlChars(raw)) return fallback;
  if (!raw.startsWith('/')) return fallback;
  if (raw.startsWith('//')) return fallback;
  if (raw.includes(BACKSLASH)) return fallback;
  // `/%2f/evil` 与 `/%5cevil`：解码后同样是协议相对 / 反斜杠，一并拒绝。
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return fallback;
  }
  if (decoded.startsWith('//') || decoded.includes(BACKSLASH) || hasControlChars(decoded)) return fallback;
  if (/^\/(login|register)(?:[/?#]|$)/.test(raw)) return fallback;
  return raw;
}
