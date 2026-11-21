/**
 * AI 流程设计助手（右 30% 面板）的界面文案（M64）。
 *
 * 与 `copy.workflow.ts`（FA）、`copy.canvas.ts`（FB）分开放，避免多人改同一个文件。
 * 约定同 H-12：服务端只给码与参数，所有人话在前端。
 */

export const assistCopy = {
  title: 'AI 流程设计助手',
  engine: { aragon: 'aragon', mock: 'mock' } as Record<string, string>,
  menu: '更多操作',
  clear: '清空对话',
  clearConfirm: '确定清空与助手的对话记录？画布不受影响。',
  empty: '用自然语言描述你想要的研究流程，我会改画布上的草稿；发布仍由你来做。',
  emptyHint: '例如：在估值前加一个同业对比步骤，并设一个检查点',
  composer: {
    placeholder: '描述你想怎么改这条研究流程…（Enter 发送，Shift+Enter 换行）',
    label: '发给 AI 助手的消息',
    send: '发送',
    sending: '设计中…',
    counter: '{n}/{max}',
    focusHint: '已选中 {n} 个节点，将作为焦点一并发送',
  },
  chips: {
    layout: '整理布局',
    criteria: '补全所有完成标准',
    coverage: '检查章节覆盖',
    restructure: '按卖方研报常规流程重排',
  },
  chipPrompts: {
    layout: '请整理一下画布布局',
    criteria: '请为所有缺少完成标准的步骤补全完成标准',
    coverage: '请检查五个章节是否都被步骤覆盖，缺的请补上',
    restructure: '请按卖方研报的常规流程帮我重排这条研究流程',
  },
  thinking: '思考中…',
  round2: '正在修正上一版方案（第 2 轮）…',
  cancel: '取消',
  cancelled: '已取消，草稿未改动。',
  busyBadge: 'AI 正在设计…',
  card: {
    added: '新增 {n} 步',
    addedGate: '{n} 检查点',
    updated: '改动 {n} 处',
    deleted: '删除 {n} 项',
    edges: '{n} 条连线',
    validation: '校验：⛔{errors} ⚠{warnings}',
    nothing: '没有修改画布',
    locate: '定位',
    undo: '撤销这次修改',
    undone: '已撤销',
    notApplied: '本次未应用修改',
  },
  reasons: {
    invalid_ops: '助手给出的修改不合法，草稿保持不变。',
    introduced_errors: '助手的方案会引入新的校验错误，草稿保持不变。',
    failed: '助手这次没有成功，草稿保持不变。',
    cancelled: '已取消，草稿未改动。',
    default: '这次没有应用修改，草稿保持不变。',
  } as Record<string, string>,
  conflict: {
    title: '你在 AI 思考期间改动了画布，已放弃这次修改。',
    entities: '涉及：{ids}',
    retry: '基于最新画布重试',
  },
  interrupted: {
    text: '上次回复被中断，可重试。',
    retry: '重试',
  },
  unavailable: {
    title: 'AI 助手当前不可用',
    body: '当前引擎不支持结构化提交。请到「能力诊断」查看引擎状态后再试。',
    link: '查看能力诊断',
  },
  budget: '本月成本预算已用完，AI 助手暂不可用（不影响用户生成）。',
  rateLimited: '操作太频繁（每小时最多 20 次），请稍后再试。',
  busy: '该流程已有进行中的助手任务，请稍候。',
  readOnly: '内置流程只读：请先「复制为可编辑副本」再使用 AI 助手。',
  genericError: '发送失败，请稍后重试。',
  followUps: '助手想确认',
  user: '你',
  assistant: '助手',
  announceApplied: 'AI 助手已修改画布：{summary}',
} as const;

/** `copy.ts` 风格的占位符填充（不依赖 `lib/copy` 以免把首屏 chunk 拖进编辑器）。 */
export function fillAssist(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in values ? String(values[key]) : match));
}
