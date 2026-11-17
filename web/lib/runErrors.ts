/**
 * ★v9（K6 / §21.3.7）：run 级错误码 → **下一步动作**。
 *
 * 为什么需要它：`GenerationOverlay.tsx` 过去原样打印 `{code} · {message}`，唯一的码级
 * 特判是 `usage_error`；而 `copy.errors` 里已经写好了 5 条文案（`runConflict` /
 * `tooManyRuns` / `budgetExceeded` / `cancelled` / `generation.reconnect`）却从未被使用。
 * 用户看到一串英文码，不知道自己该改密钥、该等、还是该看日志。
 *
 * 三条纪律：
 * 1. **纯数据 + 纯函数**：可以被 vitest 直接断言，不需要渲染任何组件。
 * 2. 真源在服务端：`RUN_ERROR_CODES`（`api/app/agent/failure.py`）与这里的表由
 *    `contracts/run_error_codes.json` **双向**约束（AC-104）。任何一侧新增码而另一侧
 *    漏改，CI 直接红——这套码表历史上已漂移两次（v7 的 V8-P1-3、`run_conflict` 静默）。
 * 3. run 码与 HTTP 码**分开两张表**：AC-104 的"集合相等"只对上面对 `RUN_ERROR_CODES`，
 *    把 402/409/503 混进来会让那条断言永远红。
 */

export type RunErrorAction = 'retry' | 'openSettings' | 'checkKey' | 'wait' | 'openLog' | 'none';

export interface RunErrorActionSpec {
  action: RunErrorAction;
  /** 主按钮：一张卡片上只出现一个（其余收进次要位置）。 */
  primary: boolean;
}

/**
 * ★v10 修订（V10-P1-7）：v9 的表只有 15 条，实测 `RUN_ERROR_CODES` 有 **18** 条。
 * 少的那几条里 `aragon_unavailable` 恰恰是首次上手最可能撞的码。
 */
export const RUN_ERROR_ACTIONS: Record<string, RunErrorActionSpec> = {
  // K3 让这个码第一次真的会出现（`stream_error` + 凭证类 message）。
  provider_auth: { action: 'checkKey', primary: true },
  provider_unavailable: { action: 'wait', primary: false },
  usage_error: { action: 'openSettings', primary: true }, // 含 provider / base-url 填错
  aragon_unavailable: { action: 'openSettings', primary: true }, // 没装 CLI / 路径不对
  structured_result_unsupported: { action: 'openSettings', primary: true }, // CLI 版本过旧
  schema_over_budget: { action: 'openLog', primary: false },
  schema_drift: { action: 'retry', primary: true }, // 重生成一次即恢复
  prompt_over_budget: { action: 'openLog', primary: false },
  result_schema_unsatisfied: { action: 'retry', primary: true },
  result_missing: { action: 'retry', primary: true },
  snapshot_missing: { action: 'retry', primary: true }, // 服务端拼快照失败
  budget_exceeded: { action: 'openSettings', primary: false },
  agent_error: { action: 'retry', primary: false },
  cancelled: { action: 'none', primary: false },
  interrupted: { action: 'retry', primary: true },
  internal: { action: 'retry', primary: false },
  data_provider_unavailable: { action: 'wait', primary: false },
  instrument_not_found: { action: 'none', primary: false },
};

/**
 * HTTP 级码（**不是** run 码，但前端同样要动作）。
 *
 * 与上面分开两个字典：AC-104 的"集合相等"只对上面对 `RUN_ERROR_CODES`。
 */
export const HTTP_ERROR_ACTIONS: Record<string, RunErrorActionSpec> = {
  monthly_budget_exceeded: { action: 'openSettings', primary: true }, // 402
  probe_in_progress: { action: 'wait', primary: false }, // 409（K2 单飞）
  probe_busy: { action: 'wait', primary: false }, // 409（V10-P2-11：有生成在跑）
  probe_disabled: { action: 'openSettings', primary: false }, // 503
  agent_mock_mode: { action: 'openSettings', primary: true }, // 503（mock 下探针无意义）
  too_many_runs: { action: 'wait', primary: true }, // 429
  run_conflict: { action: 'none', primary: false }, // 已自动接管，只说一句
};

/** 未知码的兜底：**不猜动作**，只给"看日志"这一条真正有用的路。 */
export const UNKNOWN_ERROR_ACTION: RunErrorActionSpec = { action: 'openLog', primary: false };

/** 查一次动作：先查 run 码表，再查 HTTP 码表，最后兜底。 */
export function actionFor(code: string | null | undefined): RunErrorActionSpec {
  if (!code) return UNKNOWN_ERROR_ACTION;
  return RUN_ERROR_ACTIONS[code] ?? HTTP_ERROR_ACTIONS[code] ?? UNKNOWN_ERROR_ACTION;
}

/**
 * 一张失败卡片上唯一的那个主按钮；`none` 表示这次失败不需要用户做任何事
 * （取消 / 标的被删），此时不该出现按钮。
 */
export function primaryActionFor(code: string | null | undefined): RunErrorAction | null {
  const spec = actionFor(code);
  if (!spec.primary) return null;
  return spec.action === 'none' ? null : spec.action;
}

/** 动作 → 按钮上的字（文案集中在这里，组件不拼句子）。 */
export const ACTION_LABELS: Record<RunErrorAction, string> = {
  retry: '再次生成',
  openSettings: '去设置',
  checkKey: '检查密钥',
  wait: '稍后重试',
  openLog: '查看日志',
  none: '',
};
