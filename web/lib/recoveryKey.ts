/**
 * 恢复码的**展示层**辅助（spec §25.3.3）：只做归一化与分组，真正的规范化在服务端 `normalize()`。
 *
 * 字母表是 Crockford Base32（`0-9 A-Z` 去 `I L O U`）。用户抄写时最常见的混淆：`O→0`、`I/L→1`——
 * 输入遮罩与服务端各自容忍，二者口径一致。
 */

export const RECOVERY_KEY_LENGTH = 20;
export const RECOVERY_KEY_GROUP = 5;

/** 去空白与连字符、转大写、`O→0`、`I/L→1`，并丢掉字母表之外的字符；最多 20 位。 */
export function normalizeRecoveryKey(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[\s-]+/g, '')
    .replace(/O/g, '0')
    .replace(/[IL]/g, '1')
    .replace(/[^0-9A-HJKMNP-TV-Z]/g, '')
    .slice(0, RECOVERY_KEY_LENGTH);
}

/** `K7QM4XNP2R…` → `K7QM4-XNP2R-…`（粘贴整串、小写、带空格都能被整理成分组形态）。 */
export function formatRecoveryKey(raw: string): string {
  const key = normalizeRecoveryKey(raw);
  const groups: string[] = [];
  for (let i = 0; i < key.length; i += RECOVERY_KEY_GROUP) groups.push(key.slice(i, i + RECOVERY_KEY_GROUP));
  return groups.join('-');
}

export function isCompleteRecoveryKey(raw: string): boolean {
  return normalizeRecoveryKey(raw).length === RECOVERY_KEY_LENGTH;
}

/** 注册时选择"跳过"的提醒标记（只在本浏览器里；账号页读取并常驻提示，用户可关闭）。 */
const UNSAVED_KEY = 'aragon.recovery.unsaved';

export function markRecoveryUnsaved(userId: string): void {
  try {
    window.localStorage.setItem(UNSAVED_KEY, userId);
  } catch {
    /* 隐私模式 / 禁用存储：提示条只是便利，丢了不影响功能 */
  }
}

export function readRecoveryUnsaved(userId: string): boolean {
  try {
    return window.localStorage.getItem(UNSAVED_KEY) === userId;
  } catch {
    return false;
  }
}

export function clearRecoveryUnsaved(): void {
  try {
    window.localStorage.removeItem(UNSAVED_KEY);
  } catch {
    /* 同上 */
  }
}

/** 下载用的 .txt 内容（含用途说明与"勿分享"）。 */
export function recoveryKeyFile(username: string, key: string, header: string, body: string): { name: string; text: string } {
  const safe = username.replace(/[^\w.-]+/g, '_') || 'account';
  return {
    name: `aragon-recovery-${safe}.txt`,
    text: `${header}\n\n账号：${username}\n恢复码：${key}\n\n${body}\n`,
  };
}
