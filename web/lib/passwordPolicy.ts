/**
 * 密码强度与规则的**客户端实时提示**（spec §24.3.5 / §24.3.15b）。
 *
 * 权威仍是服务端（`422 weak_password` + `detail.rules`）：这里只负责"边输边点亮清单"，
 * 并且**策略数字（长度、类别数）一律来自 `GET /auth/config`**，规则不在前端写死第二份。
 * 服务端有而这里无法复现的规则（内置弱口令表）只在服务端返回后才点亮（`serverFailed`）。
 */

export interface PasswordPolicy {
  min: number;
  max: number;
  minClasses: number;
}

export const DEFAULT_PASSWORD_POLICY: PasswordPolicy = { min: 8, max: 128, minClasses: 2 };

/** 规则 id（与 `copyAuthPages.password.rules` 的键一一对应）。 */
export type PasswordRuleId =
  | 'min_length'
  | 'max_length'
  | 'contains_username'
  | 'char_classes'
  | 'common_password'
  | 'repeated_chars'
  | 'sequential_chars';

export interface RuleResult {
  id: PasswordRuleId;
  ok: boolean;
}

export interface PasswordEvaluation {
  rules: RuleResult[];
  /** `-1` = 还没输入；`0..4` 对应 `copyAuthPages.password.levels`。 */
  score: -1 | 0 | 1 | 2 | 3 | 4;
  /** 所有**客户端可判定**的必选规则是否都满足（提交前的门）。 */
  ok: boolean;
}

export function countClasses(password: string): number {
  let count = 0;
  if (/[a-z]/.test(password)) count += 1;
  if (/[A-Z]/.test(password)) count += 1;
  if (/[0-9]/.test(password)) count += 1;
  if (/[^A-Za-z0-9]/.test(password)) count += 1;
  return count;
}

function isRepeated(password: string): boolean {
  return password.length > 1 && new Set(password).size === 1;
}

/** 连续序列：每个相邻字符的码点差恒为 +1 或恒为 -1（`12345678`、`abcdefgh`、`87654321`）。 */
function isSequential(password: string): boolean {
  if (password.length < 4) return false;
  const step = password.charCodeAt(1) - password.charCodeAt(0);
  if (step !== 1 && step !== -1) return false;
  for (let index = 1; index < password.length; index += 1) {
    if (password.charCodeAt(index) - password.charCodeAt(index - 1) !== step) return false;
  }
  return true;
}

export function evaluatePassword(
  password: string,
  username: string,
  policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY,
  serverFailed: readonly string[] = [],
): PasswordEvaluation {
  const nfkc = password.normalize('NFKC');
  const lowerName = username.trim().toLowerCase();
  const rules: RuleResult[] = [
    { id: 'min_length', ok: nfkc.length >= policy.min },
    { id: 'char_classes', ok: countClasses(nfkc) >= policy.minClasses },
    { id: 'contains_username', ok: lowerName.length === 0 || !nfkc.toLowerCase().includes(lowerName) },
    { id: 'repeated_chars', ok: !isRepeated(nfkc) },
    { id: 'sequential_chars', ok: !isSequential(nfkc) },
  ];
  if (nfkc.length > policy.max) rules.push({ id: 'max_length', ok: false });
  // 服务端才能判的规则：只在服务端报过才出现，并且在被报的那一刻标为未满足。
  for (const id of serverFailed) {
    if (id === 'common_password' && !rules.some((rule) => rule.id === id)) rules.push({ id, ok: false });
  }
  const ok = rules.every((rule) => rule.ok);

  if (nfkc.length === 0) return { rules, score: -1, ok: false };

  let score = 0;
  if (nfkc.length >= policy.min) score += 1;
  if (nfkc.length >= 10) score += 1;
  if (nfkc.length >= 12) score += 1;
  if (countClasses(nfkc) >= 3) score += 1;
  // 任何一条规则没满足，强度封顶在"较弱"——长但是 `aaaaaaaaaaaa` 不能显示成"很强"。
  if (!ok) score = Math.min(score, 1);
  return { rules, score: Math.min(4, score) as PasswordEvaluation['score'], ok };
}
