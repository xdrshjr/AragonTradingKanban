/**
 * 研究流程的全部中文文案（H-12：服务端只给码与参数，人话在前端）。
 * 组件不内联业务文案；公共约定同 `lib/copy.admin.ts`。
 */

import { ROLES, SECTIONS } from './contract';
import type { Adherence, Effort, NodeKind, Role, ValidationIssue, WorkflowOp } from './types';

export const copyWorkflow = {
  nav: '研究流程',
  list: {
    title: '研究流程',
    subtitle: '用拖拽画布配置 Agent 生成研报的流程。其他用户每次生成都会按「默认流程」的最新已发布版本研究。',
    enabledLabel: '使用自定义研究流程',
    enabledOn: '已启用：新的生成按默认流程研究',
    enabledOff: '已关闭：新的生成使用内置提示词（不应用任何流程）',
    enabledSaved: '开关已保存（立即生效，已在飞的生成不受影响）',
    create: '新建流程',
    columns: { name: '名称', status: '状态', version: '最新版本', steps: '步骤数', updated: '最近编辑', usage: '近 30 天使用' },
    badges: { default: '默认', builtin: '内置', unpublished: '草稿未发布', dirty: '有未发布修改' },
    actions: { edit: '编辑', clone: '复制', setDefault: '设为默认', archive: '归档' },
    empty: '还没有流程',
    emptyHint: '新建一个流程，或复制内置的「标准五段式研究」开始。',
    loadFailed: '研究流程加载失败',
    neverPublished: '未发布',
    noVersion: '—',
    runs: '{n} 次',
    versionLabel: 'v{n}',
    defaultSaved: '已设为默认流程：{name}',
    archived: '已归档：{name}',
    archiveTitle: '归档「{name}」？',
    archiveBody: '归档后不再出现在选择里；历史生成仍可追溯到它。默认与内置流程不能归档。',
    archiveConfirm: '归档',
    setDefaultTitle: '把「{name}」设为默认？',
    setDefaultBody: '从下一次生成起，所有用户都按它的最新已发布版本研究；已在飞的生成不受影响。',
    setDefaultConfirm: '设为默认',
    cloned: '已创建副本：{name}',
    stepCount: '{n} 步',
  },
  settingsCard: {
    title: '研究流程',
    loading: '正在读取当前默认研究流程…',
    unavailable: '暂时无法读取当前默认研究流程。',
    disabled: '自定义研究流程已关闭：新的生成使用内置提示词。',
    current: '当前默认研究流程：{name} {version}',
    version: 'v{n}',
    noVersion: '（未发布）',
    manage: '管理',
  },
  create: {
    title: '新建研究流程',
    name: '名称',
    namePlaceholder: '例如：估值加强版',
    nameHint: '名称会显示在用户的研报元信息中（如「按〈名称〉v3 研究」），请勿写入内部备注。',
    description: '说明（仅管理员可见）',
    from: '起点',
    fromBlank: '空白流程（只有开始与结束）',
    fromBuiltin: '复制内置「标准五段式研究」',
    fromTemplate: '复制已有流程',
    pickTemplate: '选择流程',
    submit: '创建并编辑',
    cancel: '取消',
    nameRequired: '请填写名称',
  },
  editor: {
    back: '研究流程',
    loading: '正在加载流程…',
    loadFailed: '流程加载失败',
    retry: '重试',
    notFound: '流程不存在或已被归档',
    draftDirty: '草稿有未发布修改',
    draftClean: '草稿与最新版本一致',
    neverPublished: '尚未发布',
    versions: '版本',
    previewPrompt: '预览提示词',
    publish: '发布…',
    builtinReadonly: '内置流程只读',
    builtinBanner: '这是内置流程，由应用代码维护，不能直接修改。',
    cloneToEdit: '复制为可编辑副本',
    archivedBanner: '该流程已归档，只读。',
    mobileReadonly: '请在桌面端编辑流程（当前为只读查看）',
    split: '调整流程画布与 AI 助手的宽度',
    leftRegion: '流程规划区',
    rightRegion: 'AI 流程设计助手',
    openAssist: 'AI 助手',
    closeAssist: '收起 AI 助手',
    canvasLoading: '正在加载画布…',
  },
  status: {
    saved: '已自动保存',
    saving: '保存中…',
    offline: '离线，已缓存本地',
    error: '保存失败',
    rev: 'rev {n}',
    steps: '步骤 {n}',
    gates: '检查点 {n}',
    budget: '估算 ≈ {est}/{max} 回合',
    readonly: '只读',
    problems: '问题',
    errors: '{n} 个错误',
    warnings: '{n} 个警告',
  },
  toasts: {
    synced: '已与服务器同步（有人改动了流程）',
    saveFailed: '保存失败，已重新同步',
    invalidOps: '这次修改不合法：{message}',
    readonly: '内置流程只读，请先复制为可编辑副本',
    offlineQueued: '网络不可用，修改已缓存，恢复后自动重放',
    replayed: '网络已恢复，修改已同步',
  },
  unsaved: '有未保存的修改，确定离开吗？',
  roles: { data: '数据核对', analysis: '分析', synthesis: '综合', review: '复核', custom: '自定义' } as Record<Role, string>,
  efforts: { light: '轻', normal: '中', deep: '深' } as Record<Effort, string>,
  kinds: { start: '开始', step: '步骤', gate: '检查点', note: '注释', end: '结束' } as Record<NodeKind, string>,
  adherence: { full: '完全遵循', partial: '部分遵循', none: '未汇报', unreported: '未要求汇报' } as Record<Adherence, string>,
  sections: Object.fromEntries(SECTIONS.map((section) => [section.id, section.label])) as Record<string, string>,
  opRejects: {
    unknown_node: '节点不存在',
    unknown_edge: '连线不存在',
    duplicate_id: 'id 重复',
    immutable_field: '该字段不可修改',
    bad_patch: '内容不合法',
    reserved_id: 'id 不合法',
    limit_exceeded: '已达到数量或体积上限',
    invalid_edge: '连线不合法',
    protected_node: '起点与终点不可删除',
    bad_kind: '节点类型不合法',
    rev_conflict: '该实体刚被修改，请重试',
  } as Record<string, string>,
  announce: {
    added: '已新增{kind}「{title}」',
    deleted: '已删除{kind}「{title}」',
    updated: '已更新「{title}」',
    moved: '已移动「{title}」',
    edgeAdded: '已连接「{source}」到「{target}」',
    edgeDeleted: '已断开一条连线',
    policy: '已更新流程策略',
    undone: '已撤销',
    redone: '已重做',
    nothing: '没有可撤销的操作',
  },
};

export const ROLE_LABELS = copyWorkflow.roles;
export { ROLES };

/** `{name}` 占位替换（与 `lib/copy.ts::fill` 同语义，避免 lib 之间互相 import 的环）。 */
export function fillText(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? ''));
}

// ── 校验问题码 → 句子 ────────────────────────────────────────────────────────

type IssueFormatter = (params: NonNullable<ValidationIssue['params']>, title: string) => string;

const text = (value: unknown, fallback = ''): string => (value === undefined || value === null ? fallback : String(value));

const ISSUE_TEXT: Record<string, IssueFormatter> = {
  no_start: () => '缺少开始节点',
  multiple_start: () => '开始节点不止一个',
  no_end: () => '需要恰好一个结束节点',
  no_step: () => '至少需要一个研究步骤',
  cycle: (p) => `流程中存在环${Array.isArray(p.path) ? `：${p.path.join(' → ')}` : ''}`,
  self_loop: () => '连线不能连到自身',
  dangling_edge: () => '连线的端点不存在',
  duplicate_edge: () => '存在重复连线',
  note_has_edge: () => '注释节点不能连线',
  start_has_input: () => '开始节点不能有入边',
  end_has_output: () => '结束节点不能有出边',
  unreachable_node: (_, title) => `「${title}」从开始节点不可达`,
  empty_prompt: (_, title) => `「${title}」的提示词为空`,
  invalid_file: (p, title) => `「${title}」引用了不支持的资料文件${p.file ? `：${text(p.file)}` : ''}`,
  invalid_section: (p, title) => `「${title}」引用了未知章节${p.section ? `：${text(p.section)}` : ''}`,
  section_not_covered: (p) => `章节「${copyWorkflow.sections[text(p.section)] ?? text(p.section)}」没有任何步骤服务`,
  undefined_placeholder: (p, title) => `「${title}」使用了未定义的占位符${p.placeholder ? ` {{${text(p.placeholder)}}}` : ''}`,
  node_limit_exceeded: () => '节点数量超过上限',
  edge_limit_exceeded: () => '连线数量超过上限',
  step_limit_exceeded: () => '步骤数量超过上限',
  doc_too_large: () => '流程文档体积超过上限',
  plan_too_large: () => '编译后的执行计划过大，请精简提示词或步骤',
  policy_out_of_range: () => '流程策略的回合/时长超出范围（只能收紧全站上限）',
  node_not_reaching_end: (_, title) => `「${title}」无法到达结束节点`,
  orphan_note: (_, title) => `注释「${title}」没有关联内容`,
  high_fanout: (_, title) => `「${title}」的下游过多，建议用检查点汇合`,
  long_prompt: (_, title) => `「${title}」的提示词较长`,
  missing_success_criteria: (_, title) => `「${title}」没有完成标准`,
  isolated_step: (_, title) => `「${title}」没有任何连线`,
  empty_gate: (_, title) => `检查点「${title}」没有检查清单`,
  duplicate_title: (_, title) => `存在重名节点「${title}」`,
  plan_large: () => '执行计划较大，会增加每次生成的成本',
  budget_tight: (p) => `估算回合偏紧${p.estimated ? `（约 ${text(p.estimated)} 回合）` : ''}，仅供参考`,
};

/** 一条校验问题的中文句子；未知码原样显示码本身，不隐瞒。 */
export function describeIssue(issue: ValidationIssue, titleOf: (id: string | null) => string = () => ''): string {
  const format = ISSUE_TEXT[issue.code];
  if (!format) return issue.code;
  return format(issue.params ?? {}, titleOf(issue.entityId));
}

// ── 无障碍播报 ───────────────────────────────────────────────────────────────

/** 一批 op 的 aria-live 播报（取第一条有信息量的）。`titleOf` 取操作前后任一文档里的标题。 */
export function describeOps(ops: readonly WorkflowOp[], titleOf: (id: string) => string): string {
  const a = copyWorkflow.announce;
  for (const op of ops) {
    switch (op.op) {
      case 'add_node':
        return fillText(a.added, { kind: copyWorkflow.kinds[op.node.kind], title: op.node.title });
      case 'delete_node':
        return fillText(a.deleted, { kind: '节点', title: titleOf(op.id) });
      case 'update_node':
        return fillText(a.updated, { title: titleOf(op.id) });
      case 'move_node':
        return fillText(a.moved, { title: titleOf(op.id) });
      case 'add_edge':
        return fillText(a.edgeAdded, { source: titleOf(op.edge.source), target: titleOf(op.edge.target) });
      case 'delete_edge':
        return a.edgeDeleted;
      case 'set_policy':
        return a.policy;
      default:
        break;
    }
  }
  return '';
}
