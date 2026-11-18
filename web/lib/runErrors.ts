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
  // ── ★v12（S1/S2/S4 / §22.5.3）：三个追加的 run 码 ────────────────────────────
  // 与 `api/app/agent/failure.py::RUN_ERROR_CODES`、`contracts/run_error_codes.json`
  // 三处**逐字**相同（C-36）；任何一处漏改，`test_contract_codes.py` 与
  // `tests/runErrors.test.ts` 的集合相等断言直接红。
  // 事件循环不支持：重试没有意义（同一进程里循环类型不会自己变），用户要做的是去设置
  // 里看一眼启动参数 —— 这与 `aragon_unavailable` 的动作相同，但**原因不同**，
  // 所以是两个码而不是一个（"去设置"页里高亮的那一行不一样）。
  event_loop_unsupported: { action: 'openSettings', primary: true },
  agent_timeout: { action: 'retry', primary: true },
  output_truncated: { action: 'retry', primary: true },
  // ── ★v12（S6 / §22.3.7(b)）：**前端本地码**，服务端不产出 ──────────────────
  // SSE 重连耗尽（或看门狗判定静默）时由 `useGenerationStream` 派发。它过去**不在**
  // 这张表里 ⇒ 兜底动作是「查看日志」，而日志里什么都不会有（连接是前端自己放弃的）。
  // 主按钮是「再次生成」：那次生成可能仍在后台跑，重新点一次会走到 `run_conflict` 的
  // 接管分支（`useGenerationStream.start()`）**接回同一次生成**，而不是白付第二笔钱；
  // 若那次已经成功，`reconcileRun()` 已经把报告列表刷出来了。（S-9：按钮要真做事。）
  stream_closed: { action: 'retry', primary: true },
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
  // ── ★v14（§23.5.5）：三条新码全是 HTTP 码，**不进** `RUN_ERROR_CODES` ──────────
  // 与 `contracts/run_error_codes.json` 的 `httpCodes` 逐字相同（`runErrors.test.ts`
  // 断言 `HTTP_ERROR_ACTIONS ⊇ httpCodes` 并逐行比对 `action`）。
  // 三条的语义都是'换个参数 / 刷新一下就能继续'，与既有 `schema_drift` 的 retry 同类。
  invalid_cursor: { action: 'retry', primary: true }, // 422：游标不合法 → 从第一页重载
  report_not_deleted: { action: 'retry', primary: true }, // 409：状态被别的标签页改过了
  base_report_mismatch: { action: 'retry', primary: true }, // 422：改成独立生成重发
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
  // ★v12（S6 / §22.3.7(a)）：这个动作**现在真的会等**（8 秒后自动重试一次），
  // 所以按钮上必须写出代价。过去它写着"稍后重试"却什么都不做（E-99）。
  wait: '稍后重试（8s）',
  openLog: '查看日志',
  none: '',
};

/**
 * ★v12（S6 / §22.3.7(a) / AC-123）：`wait` 分支的等待秒数。
 *
 * 为什么是 8 秒：429 `too_many_runs` 的成因是"同时最多 2 个生成"（`MAX_CONCURRENT_RUNS`），
 * 而一次生成至少几十秒 ⇒ 8 秒不足以让出名额。所以这个动作的语义**不是"等到成功"**，
 * 而是"给我一个可点击的倒计时，让我知道它在做什么，并且真的再试一次"。真正的等待由
 * 界面的重试按钮承担（用户看到下一次 429 时不会再怀疑按钮坏了）。
 */
export const WAIT_RETRY_SECONDS = 8;
