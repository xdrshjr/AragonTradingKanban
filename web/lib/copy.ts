/**
 * Every user-facing string, in one place so i18n stays a data change later (§4).
 * Interpolating components take a single object argument — no template surgery.
 */

export const copy = {
  brand: 'Aragon',
  brandSuffix: 'Research Terminal',
  tagline: '一次生成，反复查看的投研报告工作台',

  landing: {
    title: '输入一个投资标的',
    subtitle: 'A 股 / 港股 / 美股的代码、名称或拼音首字母都可以',
    placeholder: '试试「茅台」或 600519',
    hint: '回车直达 · ⌘K 打开命令面板',
    recent: '最近标的',
    // ★v14（U1）：首页主区块。报告的可发现性不该由「我的自选股」回答（U-0a / E-120）。
    recentReports: '最近生成的研报',
    recentReportsEmpty: '还没有生成的报告',
    recentReportsEmptyBody: '搜索任意标的，生成第一份报告',
    viewAllReports: '查看全部 {total} 份报告',
    watchlistIsNotReports: '自选≠报告：自选是你关注的，报告是你做过的',
    examples: '示例',
    examplesHint: '点击直接进入已生成的报告',
    empty: '还没有最近标的，搜索任意标的开始',
    shortcutHint: ['↑↓ 选择', '回车 进入', 'Esc 取消'],
  },

  rail: {
    search: '搜索标的',
    searchPlaceholder: '代码 / 名称 / 拼音',
    watchlist: '自选',
    recent: '最近',
    noResults: '没有匹配的标的',
    generate: '生成报告',
    remove: '移出自选',
    add: '加入自选',
    collapse: '收起标列表',
    expand: '展开标列表',
  },

  chart: {
    title: 'K 线',
    timeframe: '周期',
    range: '区间',
    adjust: '复权',
    indicators: '指标',
    volume: '成交量',
    open: '开',
    high: '高',
    low: '低',
    close: '收',
    loading: '正在加载行情…',
    error: '行情加载失败',
    retry: '重试',
    empty: '暂无行情数据',
    dataSource: '数据来源',
    cached: '缓存',
    synthetic: '合成数据',
    /** ★PCD-2：两个端点落在不同 provider 上时的提示（不是"数据陈旧"）。 */
    sourceDivergent: '报价与 K 线来自不同数据源',
    sourceDivergentHint: '该标的的报价与 K 线来自不同数据源，两个数字可能不同口径；以 K 线（图表）为准。',
    adjustApplied: {
      qfq: '前复权',
      none: '不复权',
      hfq: '后复权',
    },
    summaryTable: '最近 5 根 K 线（文本替代）',
    openDrawer: '打开 K 线',
    closeDrawer: '收起 K 线',
  },

  report: {
    emptyTitle: '这份报告还没有生成',
    emptyLead: '生成后会得到什么：',
    emptyBullets: [
      '5 个章节：核心逻辑、基本面、估值、风险、催化剂',
      '图表、指标卡、同业对比与三情景估值',
      '每条结论都带来源编号，可点开核对',
    ],
    emptyEta: '预计耗时 60–120 秒，期间可以继续阅读其他标的',
    generate: '生成投研报告',
    regenerate: '生成最新版本',
    generating: '生成中',
    tabs: '章节',
    toc: '目录',
    version: '版本',
    exportHtml: '导出 HTML',
    exportMarkdown: '导出 Markdown',
    exportPdf: '导出 PDF',
    exportPdfHint: '请使用浏览器打印（本页已适配打印样式）',
    sources: '来源',
    sourcesEmpty: '本报告没有引用来源',
    riskList: '风险清单',
    disclaimer: '免责声明',
    citations: '引用',
    openSource: '查看来源',
    jumpToChart: '在 K 线中定位',
    pinned: '已置顶',
    pin: '置顶',
    unpin: '取消置顶',
    archive: '归档',
    unarchive: '取消归档',
    deleteVersion: '删除该版本',
    provisional: '临时版',
    degraded: '降级',
    mock: '示例',
    generatedAt: '生成于',
    costUnknown: '成本未知',
    asOf: '数据时点',
    unknownBlock: '未识别的块类型',
    unknownBlockHint: '渲染器会跳过它，不会让整页崩溃',
    copyTable: '复制表格',
    copied: '已复制到剪贴板',
    stance: { buy: '看多', hold: '中性', sell: '看空', watch: '观察' },
    severity: { low: '低', medium: '中', high: '高' },
    scenarioName: { bear: '悲观', base: '中性', bull: '乐观' },
    tone: { info: '说明', warn: '注意', insight: '洞察' },
  },

  generation: {
    title: '正在生成投研报告',
    idle: '准备中',
    cancel: '取消',
    cancelling: '正在取消…',
    retry: '重试',
    close: '收起',
    stages: {
      resolve: '解析标的',
      snapshot: '抓取行情与财务',
      prepare: '组装资料与提示词',
      research: 'AI 研究与撰写',
      validate: '结构校验与接地检查',
      persist: '写入版本',
    },
    doneTitle: '生成完成',
    doneBody: '已切换到最新版本',
    failedTitle: '生成失败',
    /** ★v7（H7）：引擎在重试时的可见文案——"第 3/6 次重试"比一个转圈更能解释等待。 */
    retrying: '重试中',
    retryNotice: '引擎遇到 {errorType}，正在第 {attempt}/{max} 次重试',
    /** ★v9（K4）：本服务自己的重试（`layer:"runner"`）。与「引擎重试」分开说。 */
    retryNoticeRunner: '本服务正在第 {attempt}/{of} 次尝试（引擎那一次没有产出结果）',
    /** ★v7：被用户中止（与"失败"分开说，避免把主动行为说成故障）。 */
    aborted: '生成已中止',
    /** ★v7：失败卡片上那句"下一步做什么"的标签（内容由服务端下发，H-12）。 */
    hintLabel: '下一步',
    provisionalTitle: '已保存一版临时报告',
    provisionalBody: '内容未通过结构校验，但可以阅读原始产出。',
    logs: '实时日志',
    toolCalls: '工具调用',
    elapsed: '耗时',
    emptyLogs: '等待引擎输出…',
    mockNotice: '示例模式：未连接生成引擎，报告由本地规则生成',
    realNotice: '真实模式：由 aragon 生成引擎撰写',
    reconnect: '连接中断，正在重连…',
  },

  capabilities: {
    degraded: '示例模式',
    degradedBody: '未连接生成引擎，报告将由本地规则生成（可完整演示）',
    mockBody: '当前为示例模式（AGENT_MODE=mock）',
    reason: {
      aragon_unavailable: '未安装 aragon CLI',
      structured_result_unsupported: 'CLI 缺少结构化结果能力',
      submit_result_missing: 'CLI 缺少 submit_result 工具',
      no_api_key: '未配置模型 API Key',
      version_out_of_range: 'aragon 版本不在支持区间',
      // ★v12（S1 / §22.3.2(c)）：排在最前的那条降级原因。它与 `aragon_unavailable`
      // 的可操作方向相反——不是"去装 CLI"，而是"去掉 --reload 再重启"。
      event_loop_unsupported: '事件循环不支持子进程（去掉 --reload 后重启）',
      unknown: '生成引擎不可用',
    } as Record<string, string>,
    synthetic: '部分数据来自合成数据源，仅用于演示',
  },

  /**
   * ★v9（K6/K2）：端点徽标与自检的三态文案。
   * `verified/unverified/failed` 是 `endpointState()` 的取值域，组件不拼句子。
   */
  engine: {
    verified: '已验证',
    unverified: '未验证',
    failed: '凭证失败',
    // ★v12（S1 / AC-112）：第四态。它与"凭证失败"是两件事：钥匙没问题、端点也可能通，
    // 但这个进程**spawn 不了子进程**（Windows 上的 `--reload`），所以任何生成都做不成。
    // 混进"不可用"会让用户去查一个没坏的东西。
    loopBlocked: '事件循环不支持',
    probing: '自检中…',
    probeButton: '跑一次引擎自检',
    /** ★v9（K6）：自检超过 15 秒仍没回来（实测 10.23 s，E-86），别让用户以为卡死。 */
    probeSlow: '仍在进行（最长 90 秒），也可以稍后再打开这个面板看结论',
    probeHint: '点一次自检（约 10 秒，约 $0.002）即可确认这个端点此刻是通的',
    /** 自检结论：成功时把"通了多久"说清，失败时把码与下一步说清。 */
    probeOk: '自检通过 · {ms}ms',
    probeFailed: '自检未通过 · {code}',
    keyEnv: '密钥来自 .env（ARAGON_API_KEY）',
    keyCliProfile: '密钥来自 CLI 自己的配置（.env 里没有填）',
    keyNone: '两处都没有密钥',
    /** 密钥抽屉里那句必须说的话（红线 7）。 */
    keySafety: '密钥只在服务端子进程命令行里出现，从不写进日志 / 报告 / 数据库。',
    checkKeyTitle: '检查模型密钥',
    staleReasons: {
      never: '还没有自检过',
      expired: '上次自检已过期',
      identity_changed: '引擎配置已变更，尚未验证',
      failed: '上次自检未通过',
    } as Record<string, string>,
  },

  /** ★v9（K5）：抢救徽标。数字由服务端算好（两个计数器相加），前端不拼第二个真源。 */
  salvage: {
    title: '这份报告有 {total} 处格式问题已移除后展示',
    body: '模型产出的内容有 {total} 处格式不合法（{blocks} 个块 / {keys} 个多余字段），已移除后展示；建议重新生成。',
    unsalvageable: '模型产出的内容无法抢救，已退化为临时报告。',
    expand: '看看丢了什么',
  },

  errors: {
    generic: '出了点问题',
    notFoundTitle: '找不到这个标的',
    notFoundBody: '可能是代码写错了，或者该标的尚未解析。回到搜索重新输入即可。',
    backToSearch: '返回搜索',
    retry: '重试',
    requestId: '请求编号',
    runConflict: '该标的已有进行中的生成任务，已为你接管进度',
    tooManyRuns: '当前排队任务较多，请稍后重试',
    budgetExceeded: '生成超出预算，可提高预算后重试',
    cancelled: '已取消',
    /** ★v7（H3.5）：同一指纹出现 ≥3 次时把失败卡片换成更强的措辞。 */
    sameCauseThrice: '这不是偶发失败：最近 {count} 次生成因同一原因失败',
    /** ★v7：`usage_error` 的人话标签（hint 由服务端下发）。 */
    usageRejected: '参数不被接受',
    /**
     * ★v12（S6 / AC-125）：SSE 看门狗判定"静默 45 秒"时的那句话。
     *
     * 它必须同时说清两件事：**连接断了**（不是生成失败了）与**还可以继续看**
     * （那次生成可能仍在后台跑）。只说前半句会让人以为白花钱了，只说后半句会让人
     * 以为一切正常。
     */
    streamClosed: '与服务端的连接断了，这次生成可能仍在后台进行——点『再次生成』旁的按钮会重新接上',
    // ★v14（§23.5.5）：三条新 HTTP 码的文案。前端不拼文案、服务端不下发 UI 文案——既有纪律。
    invalidCursor: '翻页参数已失效，已经从第一页重新加载',
    reportNotDeleted: '这份报告没有被删除，列表已刷新',
    archiveOnlyVersion: '这是该标的唯一的版本，归档会让它没有首页；请联系管理员。',
    baseReportMismatch: '所选的基准版本不属于该标的，已改为独立生成',
    corruptDoc: '这份报告的正文无法解析，已按最小可渲染内容展示；建议先导出留档',
  },

  /* ── ★v14 报告库（U1/U7）────────────────────────────────────────────── */
  library: {
    title: '报告库',
    open: '报告库',
    backHome: '回到首页',
    subtitle: '你生成过的每一份报告都在这里，点进去看到的就是列表上那一份',
    searchPlaceholder: '搜索报告标题、标的或结论…',
    searchLabel: '搜索报告',
    count: '共 {total} 份',
    countsHint: '{count} 份',
    scopes: {
      latest: '最近',
      all: '全部版本',
      starred: '星标',
      archived: '归档',
      trash: '回收站',
    },
    groups: {
      today: '今天',
      yesterday: '昨天',
      thisWeek: '本周',
      unknown: '时间未知',
    },
    columns: {
      report: '报告',
      version: '版本',
      generated: '生成于',
      trust: '可信度',
    },
    actions: {
      star: '加星标',
      unstar: '取消星标',
      archive: '归档',
      unarchive: '取消归档',
      export: '导出 HTML',
      restore: '恢复',
      open: '打开',
    },
    empty: '还没有生成的报告',
    emptyBody: '搜索任意标的，生成第一份报告',
    emptySearch: '没有匹配的报告',
    emptySearchBody: '换个关键词，或清空搜索看全部版本',
    emptyTrash: '回收站是空的',
    emptyTrashBody: '删除的版本与系统自动清理的旧版本会出现在这里',
    trashNotice: '含系统自动清理的旧版本（30 天后永久清除）',
    trashNoticePurge: '永久清理会连同该版本的追问记录一并删除',
    trashDaysLeft: '还剩 {days} 天',
    loading: '正在加载报告…',
    loadMore: '加载更多',
    loaded: '已加载 {count} 份报告',
    allLoaded: '已经到底了',
    retry: '重试',
    keys: '↑↓ 选择 · ↵ 打开 · P 星标 · E 导出 · Delete 归档 · / 搜索',
    generating: '正在生成',
    generatingOpen: '查看进度',
    needsNewerBackend: '当前后端不支持报告库',
    integrityCorrupt: '正文损坏',
    integrityCorruptHint: '正文无法解析，已按最小可渲染内容展示',
  },

  /* ── ★v14 历史版本（U2）─────────────────────────────────────────────── */
  history: {
    banner: '正在查看历史版本 v{version} · 生成于 {when}',
    latestIs: '最新为 v{version}（{when}）',
    backToLatest: '回到最新 v{version}',
    baseThisVersion: '基于此版本更新',
    compare: '版本比较',
    deduped: '内容与 v{version} 完全一致，未新增版本',
    deletedFallback: '该版本已删除，已为你打开最新版',
    independent: '独立生成',
    basedOn: '基于 v{version} 更新',
  },

  command: {
    placeholder: '搜索标的、切换版本、生成、导出…',
    groups: {
      symbols: '切换标的',
      actions: '操作',
      versions: '切换版本',
      theme: '外观',
    },
    actions: {
      search: '搜索标的或报告内容',
      generate: '生成最新版本',
      exportHtml: '导出 HTML',
      exportMarkdown: '导出 Markdown',
      toggleChart: '切换 K 线面板',
      toggleTheme: '切换明暗主题',
      resetLayout: '重置布局',
    },
    empty: '没有匹配的命令',
    hint: '↑↓ 选择 · 回车执行 · Esc 关闭',
  },

  a11y: {
    skipToReport: '跳到报告正文',
    railNav: '标的列表',
    reportNav: '报告章节',
    liveRegion: '生成进度',
    askLiveRegion: '追问结果',
    onlineStatus: '网络状态',
    dragRail: '调整标的列表宽度',
    dragChart: '调整 K 线与报告宽度',
    resetByDoubleClick: '双击复位',
  },

  /* ── A1 追问 ───────────────────────────────────────────────────────────── */
  ask: {
    title: 'AI 助手',
    open: '追问',
    close: '收起 AI 助手',
    launcherOpen: '打开 AI 助手',
    launcherUnread: '打开 AI 助手，有新回答',
    launcherTitle: 'AI 助手',
    emptyTitle: '围绕这份报告继续研究',
    emptyBody: '回答以当前报告为依据，引用可点击核对',
    suggestions: [
      '总结核心结论',
      '这份报告的主要风险是什么？',
      '估值依赖哪些假设？',
    ],
    singleQuestion: '当前服务仅支持单次追问',
    citeContext: '仅检索本报告原文；连续指代请使用深度分析',
    deepContext: '参考最近 {count} 轮回答',
    historyWindow: '最近 20 条历史 · 本次最多保留 100 轮',
    recentMemoryWindow: '仅展示最近 100 轮',
    localHistory: '本地历史',
    personalHistory: '我的历史',
    loadingHistory: '正在恢复历史与任务状态…',
    historyError: '暂时无法读取历史',
    reloadHistory: '重试读取历史',
    startWithoutHistory: '仅开始新问题',
    newTopic: '新话题',
    newTopicHint: '新话题仅影响本次打开期间，不会删除历史',
    bottom: '有新回答 · 回到底部',
    offline: '离线时可阅读已有内容，联网后继续提问',
    overLimit: '问题超过字符上限，请缩短后发送',
    shortcut: 'Ctrl / ⌘ + Enter 发送',
    verifying: '正在确认最终结果…',
    disconnected: '连接中断，任务状态待确认',
    unverified: '结果待确认，已保留当前预览',
    submissionUnknown: '暂未确认是否受理，请刷新记录',
    duplicateRisk: '我知道可能重复，仍发送新请求',
    reconnect: '重新连接',
    reconnecting: '连接中断，正在重连…',
    refreshStatus: '刷新状态',
    invalidContext: '部分历史已不可用，可开始新话题',
    cancelUnconfirmed: '停止未确认，请重试停止或重新读取状态',
    authRequired: '身份或权限需要恢复，请重新登录后检查状态',
    duplicateAcknowledged: '已允许新请求；原请求仍可能受理并产生费用',
    historyTurnError: '暂时无法读取该轮状态，请重试',
    loadingPanel: '正在载入助手…',
    loadingFailed: '助手资源载入失败，已保留报告',
    reloadPage: '重新加载页面',
    started: '已发送问题',
    completed: '回答已完成',
    placeholder: '例如：毛利率下滑的主因是什么？',
    /** 追问输入框的可访问名（占位符是示例，不能当标签）。 */
    questionLabel: '追问内容',
    /** 模式切换（引用检索 / 深度追问）整组的可访问名。 */
    modeGroup: '追问模式',
    hint: '默认「引用检索」：直接取报告原文，不调用模型、不花钱',
    submit: '提问',
    deepSubmit: '深度追问',
    deepHint: '深度追问会调用一次 AI（约 20–60 秒），每日额度 {limit} 次',
    deepRemaining: '今日剩余深度追问 {remaining} 次',
    modeCite: '引用检索',
    modeDeep: '深度分析',
    counting: '{count}/{limit}',
    empty: '还没有追问记录，试着问一个和数字有关的问题',
    searching: '正在检索报告…',
    thinking: '正在组织答案…',
    hitsTitle: '命中 {count} 处（点击定位）',
    answerTitle: '深度回答',
    citationsTitle: '引用',
    droppedNotice: '{count} 处引用未能在来源清单中找到，已丢弃',
    notGrounded: '报告未涉及该问题',
    citeFallbackTitle: '深度追问不可用',
    quotaTitle: '今日深度追问额度已用完（{limit}/{limit}）',
    quotaBody: '是否改用引用式检索？它不消耗额度，也不会调用模型。',
    quotaAccept: '改用引用检索',
    quotaDecline: '取消',
    deepUnavailable: '当前生成引擎不可用，深度追问已关闭；引用式检索仍可用',
    provisionalHint: '这是一份临时版报告，没有来源清单，无法给出引用依据',
    cancel: '取消追问',
    cancelling: '正在取消…',
    cancelled: '追问已取消',
    failed: '追问失败',
    retry: '重试',
    costUnknown: '成本未知',
    latency: '耗时 {ms} ms',
    confidence: { high: '高置信', medium: '中置信', low: '低置信' },
  },

  /* ── A2 检索 ───────────────────────────────────────────────────────────── */
  search: {
    title: '搜索',
    placeholder: '搜索标的或报告内容（⌘K）',
    instruments: '标的',
    reports: '报告内容',
    noInstruments: '没有匹配的标的',
    emptyQuery: '输入代码、名称、拼音首字母，或报告里出现过的词',
    searching: '正在搜索…',
    degraded: '全文索引不可用，本次为文本扫描结果',
    tookMs: '{ms} ms',
    openSymbol: '打开标的',
    openSection: '定位到该段落',
    recent: '最近',
    hint: '↑↓ 选择 · 回车打开 · Esc 关闭',
    error: '搜索失败',
  },

  /* ── A3 版本比较 ───────────────────────────────────────────────────────── */
  compare: {
    title: '版本比较',
    open: '与上一版比较',
    close: '退出比较',
    left: '旧版 {version}',
    right: '新版 {version}',
    ratingChanged: '评级变化',
    metricsDelta: '指标变化',
    sectionsAdded: '新增章节',
    sectionsRemoved: '移除章节',
    blocksChanged: '内容变化',
    noChanges: '两个版本没有结构性差异',
    noBaseline: '这是第一个版本，没有可比对象',
    loadFailed: '版本比较加载失败',
    statusChanged: '状态变化',
    blocksCount: '{count} 处',
    tone: { up: '利好', down: '利空', neutral: '中性' },
    change: { added: '新增', removed: '删除', modified: '修改' },
    compareWith: '选择比较对象',
  },

  /* ── A4 定时刷新 ───────────────────────────────────────────────────────── */
  schedule: {
    title: '定时刷新',
    open: '定时刷新',
    create: '创建定时刷新',
    save: '保存',
    saving: '保存中…',
    delete: '删除定时刷新',
    deleteConfirm: '删除后不再自动生成，已生成的报告不受影响。',
    cron: '触发时间（cron，UTC）',
    cronPlaceholder: '0 9 * * 1-5',
    cronHint: '5 个字段：分 时 日 月 周；工作日 9 点写作 0 9 * * 1-5',
    cronInvalid: 'cron 表达式不合法',
    nextRunAt: '下次触发',
    preview: '预览',
    minAgeHours: '最小报告龄（小时）',
    minAgeHint: '报告比这个时间新就不再重复生成',
    enabled: '启用',
    disabled: '已停用',
    /** 标列表上的小时钟角标：这个标的有启用中的定时刷新（§18.4.5）。 */
    railBadge: '有定时刷新',
    empty: '这个标的还没有定时刷新',
    duplicate: '该标的已有启用中的定时刷新',
    failures: '连续失败 {count} 次',
    disabledAfterFailures: '连续失败已达上限，已自动停用',
    badge: '有定时刷新',
    timezoneNote: '按本地时区展示，服务端一律 UTC',
    loadFailed: '定时刷新加载失败',
  },

  /* ── A5 离线 ───────────────────────────────────────────────────────────── */
  offline: {
    badge: '离线副本',
    badgeWithTime: '离线副本 · 生成于 {time}',
    bannerTitle: '当前离线',
    bannerBody: '已读过的报告仍可查看；生成、追问与检索需要联网。',
    generateBlocked: '当前离线，无法生成',
    unreadTitle: '这份报告还没有离线副本',
    unreadBody: '联网后打开过一次，就会自动缓存到本地。',
    retryWhenOnline: '联网后重试',
    online: '已恢复联网',
  },

  /* ── G1 可信度（D2） ────────────────────────────────────────────────────── */
  trust: {
    /** 三态文案**按固定优先级**取值：legacy → skipped → 已核验 → 未接地。 */
    verified: '已核验 {ok}/{total}',
    /** ★A4：`{count}` 是**不同数值**的个数，不是引用次数（面板里另给次数）。 */
    missing: '{count} 个数字未找到依据',
    insufficient: '数字太少，未评估',
    /** ★A6：有判词但没有口径版本 —— 不给百分比，因为不可比。 */
    legacy: '早期版本 · 口径不可比',
    label: '数字可信度',
    expand: '查看未接地的数字',
    collapse: '收起',
    jump: '跳到该处',
    /** ★A4：面板顶部的一行汇总（数值个数 vs 引用次数）。 */
    counts: '{distinct} 个不同数值，出现 {references} 次',
    /**
     * ★R3-1：`{shown}` 是**已列出的块数**，`{total}` 也是**块数**（`sampledBlocks`）。
     * v1 写成「共 {total} 次引用」，而 `shown` 是块 —— 两个不同单位并列，读者会算差值
     * （"6 个块 14 次引用"读起来像还有 8 个没列）。同一句话里只许出现一个单位。
     */
    more: '仅列前 {shown} 个块，共 {total} 个块有未接地的数字',
    /**
     * ★R3-1：`{count}` = `distinctMissing − sampledValues`，**两个都是数值个数**。
     * v1 用 `distinctMissing − samples.length`（数值减块数），实测在一份报告上把 6 说成 3、
     * 另一份把 0 说成 2。
     */
    moreValues: '另有 {count} 个数值未展开（样本只列前 20 个块）',
    projections: '另有 {count} 个预测值（同业估值 / 目标价，不进核验）',
    identifiers: '另有 {count} 处是方法参数、日期或计数',
    derived: '其中 {count} 个为推算值（同比 / 轧差 / 空间等可命名推导）',
    /**
     * ★v12（S3 / §22.4.3）：格式收敛行。
     *
     * 它说的是一件**我们做过的事**（截短了模型的话），不是模型的错——所以措辞必须中性，
     * 并且带上字段名，让读者能去那一节复核。
     */
    clamped: '这份报告有 {count} 处字段超过格式上限，已按句界截短（{fields}）',
    /**
     * ★v12（S4 / §22.4.3）：从正文还原行。
     *
     * `how` 由调用方翻译成人话（`正文` / `正文（补齐括号）`）。这句话必须写在脸上：
     * 这份报告的 JSON 不是模型用 `submit_result` 交上来的，而是我们从它的正文里捡回来的。
     */
    rescued: '结构结果缺失，这份报告是从模型{how}中还原的',
    none: '所有数字都能在快照里找到依据',
    noData: '这份报告没有数字核验记录（早期版本）',
    /** ★A6：口径行。跨口径的版本只陈述口径，不并排比百分比。 */
    rules: '口径 {rules}',
    rulesUnknown: '口径未知（早期版本）',
    footnoteAll: '{count} 个数字 · 全部可追溯',
    footnoteMissing: '{count} 个数字 · {missing} 个未接地',
    sampleRaw: '原值 {raw}',
    dotMissing: '{value}：未在快照中找到依据',
    dotGrounded: '{value}：已在快照中找到依据',
  },

  /* ── G2b 全文视图（D4） ─────────────────────────────────────────────────── */
  reading: {
    mode: '阅读模式',
    tabs: '章节',
    full: '全文',
    outline: '大纲',
    outlineHint: '↑↓ 选择 · Home/End 首尾 · 回车跳转 · Esc 返回',
    outlineCount: '{count} 块',
    progress: '阅读进度 {pct}%',
    jumpToBlock: '跳到该段',
    emptySection: '本章暂无内容',
  },

  /* ── G2a 区间联动（D3） ─────────────────────────────────────────────────── */
  range: {
    title: '区间联动',
    hint: '{from} ~ {to} · 关联 {count} 段',
    empty: '这段行情在报告里没有被提及',
    clear: '清除区间',
    more: '仅显示前 {shown} 段（共 {count} 段）',
    invalid: '区间无效，已清除',
    loading: '正在反查相关段落…',
    timeout: '未找到关联段落',
    provisional: '这是一份临时版报告，没有可反查的段落',
    yearFolded: '另有 {count} 段仅涉及该年度',
    brushHint: '按住 Shift 在 K 线上拖拽框选一段，反查报告里的相关段落',
    jump: '在报告里定位',
    failed: '区间反查失败',
  },

  /* ── G3 成本治理（D5） ──────────────────────────────────────────────────── */
  cost: {
    title: '成本',
    open: '成本…',
    windows: { '7d': '近 7 天', '30d': '近 30 天', '90d': '近 90 天', mtd: '本月', ytd: '今年' },
    spent: '已知花费 ${usd}',
    unknownRuns: '另有 {count} 次成本未知',
    byKind: '按类型',
    byModel: '按模型',
    budget: '月度预算',
    budgetUnlimited: '未设上限',
    budgetRemaining: '本月剩余 ${usd}',
    budgetResetAt: '重置于 {time}',
    save: '保存预算',
    saved: '预算已保存',
    gap: '有 {count} 条账本记录未写入（报告不受影响）',
    empty: '暂无成本记录',
    loadFailed: '成本加载失败',
    estimate: '本次预估 ${usd}',
    estimateUnknown: '无法预估本次成本',
    gateTitle: '本月预算已用完（${spent}/${budget}）',
    gateBody: '本次预估 ${estimate}。仍然生成会把这次花费记进账本。',
    gateOverride: '仍然生成',
    gateCancel: '取消',
    overridden: '已按预算覆盖继续生成',
    kinds: { run: '生成', ask_deep: '深度追问' },
    state: { ok: '额度充足', near: '接近上限', exceeded: '已超上限', unlimited: '不限额' },
    /** ★v7（H7）：成本三态的三个标签 —— **永不合并成一个数**（R-31）。 */
    confirmed: '已确认成本',
    estimatedLabel: '估算成本',
    unknownLabel: '成本未知',
    includingEstimated: '含估算 ${usd}',
    estimatedRuns: '{count} 次按估算计价',
    estimatedNote: '其中 ${usd} 为按 token × 单价估算，不是引擎回执',
  },

  /**
   * ★X6（M51，spec §24.3.15）：**首屏会用到**的那一小部分认证文案——用户菜单、游客入口、生成按钮三态。
   *
   * 登录/注册/账号页/错误话术这些大头住在 `lib/copy.authPages.ts`（只被懒加载的路由与浮层 import），
   * 管理后台住在 `lib/copy.admin.ts`。拆开的原因是预算：`copy` 是一个对象，整份都会进首屏 chunk，
   * 而首屏 JS 是硬线（AC-35 / AC-202）。
   */
  auth: {
    signIn: '登录',
    signUp: '注册',
    signOut: '退出登录',
    guest: '游客',
    guestBrowse: '游客可浏览全部研报',
    generateGuest: '登录后生成投研报告',
    generateGuestShort: '登录后生成',
    signedIn: '已登录',
    engineReady: '引擎已就绪',
    generateGuestHint: '登录后即可生成投研报告；游客可浏览全部研报',
    watchlistGuest: '登录后可自定义自选',
    watchlistFeatured: '平台精选',
    sessionExpired: '登录已失效，请重新登录',
    signedOut: '已退出登录',
    openModeBanner: '开放模式：所有请求按服务账号处理，仅限本地演示。',
    menu: {
      label: '账号菜单',
      account: '账号与额度',
      admin: '管理后台',
      signedInAs: '当前账号',
    },
    roles: { root: '根管理员', admin: '管理员', user: '用户' },
    command: { account: '账号与额度', admin: '管理后台', signOut: '退出登录', signIn: '登录或注册' },
    library: { trashNeedsLogin: '登录后可查看回收站', pinnedReadonly: '已置顶' },
  },

  quota: {
    chipLabel: '生成额度',
    chip: '今日剩余 {left}/{limit}',
    chipBonus: ' +{bonus}',
    unlimited: '不限次',
    tooltip: '日额度 {left}/{limit} + 赠送 {bonus} · {reset}重置',
    tooltipNoBonus: '日额度 {left}/{limit} · {reset}重置',
    tooltipUnlimited: '当前账号生成不限次',
    exhausted: '今日额度已用完',
    exhaustedHint: '今日已用完 {used}/{limit} · {reset}重置 · 点击查看额度与申请方式',
    resetTomorrow: '明日 {time} ',
    resetToday: '今日 {time} ',
    resetOn: '{date} ',
    countdownHours: '{hours} 小时 {minutes} 分后',
    countdownMinutes: '{minutes} 分钟后',
  },
} as const;

export type Copy = typeof copy;

/**
 * Minimal `{key}` interpolation for the strings above.
 *
 * Kept deliberately tiny: no i18n library, no template surgery, one call site shape
 * (`fill(tpl, { count })`) that stays greppable when the copy changes.
 */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );
}

/**
 * ★v7（H7 / H-6）：成本三态的**唯一**标签映射。
 *
 * 面板、生成卡片、报告元信息都用它，所以「引擎回执」与「按 token × 单价估算」这两件事
 * 在界面上不会有第二种说法。`unknown` 不是 0：它说的是"我们不知道"，而不是"这次免费"。
 */
export function costSourceLabel(source: string | null | undefined): string {
  if (source === 'reported') return copy.cost.confirmed;
  if (source === 'estimated') return copy.cost.estimatedLabel;
  return copy.cost.unknownLabel;
}
