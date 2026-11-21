/**
 * 画布编辑器的界面文案（M63）。
 *
 * 与 `copy.workflow.ts`（FA：问题码→中文、列表页、状态栏）分开放，避免两个 fork 改同一个文件。
 * 约定同 H-12：服务端只给码与参数，所有人话在前端。
 */

export const ROLE_LABELS: Record<string, string> = {
  data: '数据核对',
  analysis: '分析',
  synthesis: '综合',
  review: '复核',
  custom: '自定义',
};

export const EFFORT_LABELS: Record<string, string> = {
  light: '轻',
  normal: '中',
  deep: '深',
};

export const EFFORT_TURNS: Record<string, number> = { light: 2, normal: 4, deep: 8 };

export const KIND_LABELS: Record<string, string> = {
  start: '开始',
  step: '步骤',
  gate: '检查点',
  note: '注释',
  end: '结束',
};

export const canvasCopy = {
  canvasLabel: '研究流程画布',
  empty: '画布还是空的：从工具条拖入或点击「步骤」开始。',
  readOnlyBanner: '内置流程只读。复制为可编辑副本后即可修改。',
  readOnlyMobile: '请在桌面端编辑流程',
  duplicateAsCopy: '复制为可编辑副本',
  duplicating: '正在复制…',
  duplicateFailed: '复制失败，请稍后重试',
  aiBusy: 'AI 正在设计…',

  toolbar: {
    select: '选择',
    pan: '平移',
    addStep: '步骤',
    addGate: '检查点',
    addNote: '注释',
    undo: '撤销',
    redo: '重做',
    layout: '整理布局',
    fit: '适应视图',
    zoomIn: '放大',
    zoomOut: '缩小',
    search: '搜索节点',
    dragHint: '拖到画布落点，或点击落在视口中心',
  },

  shortcut: {
    undo: '⌘Z',
    redo: '⌘⇧Z',
    delete: 'Del',
    duplicate: '⌘D',
    selectAll: '⌘A',
    rename: 'F2',
    search: '⌘F',
  },

  search: {
    placeholder: '按标题或键搜索节点',
    none: '没有匹配的节点',
    close: '关闭搜索',
  },

  connect: {
    self_loop: '不能连接到自身',
    duplicate_edge: '这两个节点之间已有连线',
    cycle: '这条连线会形成环，流程必须是有向无环图',
    note: '注释不参与连线',
    start_input: '开始节点不能有入边',
    end_output: '结束节点不能有出边',
    unknown: '无法建立这条连线',
  },

  announce: {
    added: (kind: string, title: string) => `已新增${kind}「${title}」`,
    removed: (count: number) => `已删除 ${count} 个节点`,
    connected: (from: string, to: string) => `已连接「${from}」到「${to}」`,
    disconnected: '已删除连线',
    renamed: (title: string) => `已重命名为「${title}」`,
    selected: (title: string) => `已选中「${title}」`,
    layout: '已整理布局',
    rejected: (reason: string) => `连线被拒绝：${reason}`,
  },

  node: {
    start: '开始',
    end: '提交',
    protected: '锚点节点不可删除',
    gateWaiting: '检查点',
    noFiles: '未指定资料',
    errors: (n: number) => `${n} 个错误`,
    warnings: (n: number) => `${n} 个警告`,
    untitled: '未命名',
  },

  menu: {
    duplicate: '复制节点',
    delete: '删除',
    disconnect: '断开所有连线',
    rename: '重命名',
    toStep: '设为步骤',
    toGate: '设为检查点',
  },

  structure: {
    title: '结构',
    phase: (n: number) => `阶段 ${n}`,
    empty: '暂无步骤',
    unreachable: '未连入流程',
    notes: '注释',
    collapse: '收起结构',
    expand: '展开结构',
  },

  inspector: {
    title: '属性',
    collapse: '收起属性',
    expand: '展开属性',
    noSelection: '未选中节点：显示流程策略与模板信息。',
    multi: (n: number) => `已选中 ${n} 个节点`,
    fieldTitle: '标题',
    fieldRole: '角色',
    fieldPrompt: '提示词（做什么并产出什么）',
    fieldFiles: '重点资料',
    fieldSections: '服务章节',
    fieldCriteria: '完成标准',
    fieldEffort: '预算档位',
    fieldChecklist: '检查清单',
    fieldBody: '注释内容',
    fieldLabel: '连线标签',
    fieldUpstream: '上游步骤',
    fieldDownstream: '下游步骤',
    fieldName: '模板名称',
    fieldDescription: '说明（仅管理员可见）',
    nameHint: '名称会显示在用户的研报元信息中（如『按〈名称〉v3 研究』），请勿写入内部备注。',
    promptHint: '可用占位符：',
    placeholderUndefined: (name: string) => `未定义的占位符 {{${name}}}`,
    charCount: (n: number, max: number) => `${n}/${max}`,
    estimatedTurns: (n: number) => `约 ${n} 回合（估算）`,
    covered: '已覆盖',
    uncovered: '未被任何步骤覆盖',
    addUpstream: '添加上游…',
    addDownstream: '添加下游…',
    none: '无',
    deleteEdge: '删除连线',
    deleteNode: '删除节点',
    policyTitle: '流程策略',
    policyMaxTurns: '最大回合',
    policyMaxMinutes: '最大时长（分钟）',
    policyHint: (turns: number, minutes: number) =>
      `全站上限 ${turns} 回合 / ${minutes} 分钟，仅可收紧，留空表示沿用全站值。`,
    policyMarkers: '要求 Agent 输出进度标记',
    policyMarkersHint: '关闭后遵循度记为「未汇报」。',
    templateTitle: '模板信息',
    sectionTitleFiles: (name: string) => name,
  },

  dock: {
    problems: '问题',
    plan: '预览',
    versions: '版本',
    usage: '使用',
    collapse: '收起',
    expand: '展开',
    ariaLabel: '底部工具窗',
  },

  problems: {
    none: '没有发现问题。',
    blank: '这是一张空白流程：从工具条添加步骤开始。',
    errorsTitle: '错误',
    warningsTitle: '警告',
    locate: '定位',
    graphLevel: '整张图',
    severityError: '错误',
    severityWarning: '警告',
  },

  plan: {
    loading: '正在编译…',
    failed: '预览失败',
    empty: '暂无内容',
    copy: '复制',
    copied: '已复制',
    refresh: '刷新',
    meta: (bytes: number, sha1: string) => `${(bytes / 1024).toFixed(1)} KiB · sha1 ${sha1}`,
    budget: (used: number, limit: number) => `提示词合计 ${used} / ${limit} B`,
    hint: '这是 Agent 实际读到的计划（以样例标的编译）。',
    indexTitle: '内联索引',
  },

  versions: {
    loading: '加载版本…',
    empty: '还没有发布过版本。',
    view: '查看',
    compare: '与草稿对比',
    restore: '恢复到草稿',
    rollback: '回滚（发布为新版本）',
    restored: '已恢复到草稿',
    rolledBack: '已回滚并发布为新版本',
    viewing: (v: number) => `正在查看 v${v}`,
    closeView: '返回草稿',
    steps: (n: number) => `${n} 步`,
    confirmRollbackTitle: '回滚到此版本？',
    confirmRollbackBody: (v: number) => `将以 v${v} 的内容发布一个新版本，并重置草稿。`,
    confirmRestoreTitle: '恢复到草稿？',
    confirmRestoreBody: (v: number) => `草稿会被 v${v} 的内容替换（可撤销）。`,
  },

  usage: {
    loading: '加载使用统计…',
    empty: '近 30 天没有使用记录。',
    version: '版本',
    runs: '生成次数',
    succeeded: '成功',
    failed: '失败',
    adherence: '遵循度',
    fallbacks: '回退',
    full: '完整',
    partial: '部分',
    none: '无',
  },

  diff: {
    title: '相对上一版本的变更',
    none: '没有结构变更（仅位置或注释）。',
    addedNodes: '新增节点',
    removedNodes: '删除节点',
    changedNodes: '修改节点',
    addedEdges: '新增连线',
    removedEdges: '删除连线',
    firstVersion: '首个版本：全部内容均为新增。',
    summary: (a: number, r: number, c: number) => `+${a} −${r} ~${c}`,
  },

  publish: {
    title: '发布新版本',
    description: '发布会把当前草稿固化为不可变版本；之后的编辑不影响已发布版本。',
    note: '变更说明',
    notePlaceholder: '这一版改了什么（可选，≤200 字）',
    setDefault: '发布后设为默认',
    alreadyDefault: '本模板已是默认',
    warnings: '未解决的警告',
    noWarnings: '没有警告。',
    blocked: '存在错误，无法发布',
    blockedHint: '请先在「问题」页签里修复全部错误。',
    unchanged: '草稿与最新版本相同，无需发布。',
    submit: '发布',
    submitting: '发布中…',
    cancel: '取消',
    success: (v: number) => `已发布 v${v}`,
    failed: '发布失败',
    conflict: '草稿已被他人修改，请刷新后重试',
    loadingDiff: '正在对比…',
  },
} as const;

/** 词法层面的占位符扫描：只做高亮提示，权威判定仍是服务端报告。 */
export const PLACEHOLDER_SCAN = /(\\)?\{\{\s*([^{}\s]+)\s*\}\}/g;
