# 首页滚动、品牌标识与悬浮 AI 助手设计方案

> 版本：v2；日期：2026-11-20；阶段：方案评审与修复。
> 实施提示：后续工程师使用 `superpowers:executing-plans` 按本文任务顺序实施。

**目标：** 让首页内容始终可达，以 GPT Image 生成的正式品牌标识替换字母占位标，并把报告追问完善为可连续对话的悬浮助手。

**架构：** 首页采用受约束的局部滚动容器；助手沿用报告内追问的 REST、SSE 和 Aragon 子进程链路。多轮上下文只引用服务端已有、属于当前用户且属于同一报告版本的追问记录；新能力先在后端定义，再同步前端类型。

**技术栈：** Next.js 15 App Router、React 19、TypeScript、Tailwind CSS 4、Zustand、TanStack Query、Radix UI、lucide-react；FastAPI、Pydantic 2、SQLAlchemy 2、SQLite、Alembic、Jinja2、Aragon CLI。

---

## 评审记录

本轮按可行性、完整性、现有约定一致性和范围合理性逐节审查。
依据为当前工作区源码、`CLAUDE.md`、项目索引和前序设计交付；未运行应用。
发现 13 项 P1、3 项 P2，无 P0；以下问题均已在正文修订，未遗留未解决项。
“已修订”表示设计可实施，不表示代码、Logo 或产品验收已经完成。

| 编号 | 级别 | 问题及源码依据 | 正文修订与验证落点 | 状态 |
| --- | --- | --- | --- | --- |
| RV-01 | P1 | §3.8 将无 user_id 的 open 主体历史一律置空，但 `Principal.open_mode` 明确支持单人工具；与导航后恢复及 NULL 所有者上下文矛盾。 | §3.8/4.1 明确 user、open、service、guest 分支；AC-TE-17。 | 已修订 |
| RV-02 | P1 | 旧 `AskRequest` 忽略未知字段，旧历史接口忽略 scope；新前端连接旧 API 会丢上下文，管理员还会把全站记录当成“我的”。 | §3.1/4.1/8 增加实测能力开关与先 API 后 Web 的部署顺序；AC-TE-18。 | 已修订 |
| RV-03 | P1 | §3.7 未明确先验证全部所有权再判断就绪，16 KiB 预算也未覆盖外层 JSON；单轮过大可被删至空并收费，执行时再次裁剪可能改变已确认 id。 | §3.7/5.2 固定序列化、两遍授权校验、空集拒绝与执行快照规则；AC-TE-19。 | 已修订 |
| RV-04 | P1 | `run_deep` 的兜底异常分支会把上下文 helper 的异常记成 internal，缺少明确的业务错误、终态事件与退款映射。 | §3.7/9.3 显式捕获 InvalidAskContext 并走既有失败/退款路径；AC-TE-19。 | 已修订 |
| RV-05 | P1 | `AskPhase` 没有核验、失联或受理不明状态；现有 `phaseForResult` 把非 failed/cancelled 的 GET 结果都视作 done，不能直接承担 resume。 | §3.9/4.3/5.3 补齐状态与服务端状态映射；AC-TE-20。 | 已修订 |
| RV-06 | P1 | 历史加载与发送的先后顺序、迟到历史覆盖本轮、POST 结果不明后的解锁条件没有闭合。 | §3.8/3.9 以 epoch、服务端 id 和受理状态合并，未知受理期间禁止重复付费发送；AC-TE-20。 | 已修订 |
| RV-07 | P1 | “新话题”只有文字规则，没有客户端边界字段；历史刷新可能重新把旧轮次加入下一轮上下文，cite 降级也可能携带原 deep id。 | §3.7/4.3/5.3 增加显式候选 id 和话题边界，重试使用快照、cite 一律空上下文；AC-TE-21。 | 已修订 |
| RV-08 | P1 | 568×320 下 80% 仅 256px，header、模式栏和四行输入可能超过面板；仅移除次要文字不能保证关闭/发送可达。 | §3.4/3.5 低高度优先填满可用高度，压缩输入行数并规定极短视口退路；AC-TE-05。 | 已修订 |
| RV-09 | P1 | `useReturnFocus` 的副作用归还、Radix 的默认归还与工作台 Esc 并存；只增加 options 不能约定跳块和隐藏入口后的焦点时序。 | §3.6 明确一次性关闭原因、可见性检查及来源 preventDefault；AC-TE-12。 | 已修订 |
| RV-10 | P1 | `RunStatusBadge` 点击只设 overlayCollapsed=false；按原设计隐藏进度卡后，顶部入口无法再打开进度操作。Toast 改到左下也无法避开窄屏全宽面板。 | §3.6/7 增加进度显式恢复行为及紧凑通知避让；AC-TE-15/22。 | 已修订 |
| RV-11 | P1 | 原 textarea maxLength/JS length 按 UTF-16 计数，后端 normalize_question 用 NFKC 与 Python 字符数；直接改上限仍会误拒 emoji、漏判兼容字符展开。 | §3.5 定义相同规范化计数和 raw 硬上限，IME 完成后验证；AC-TE-08。 | 已修订 |
| RV-12 | P1 | fake API 当前 askDeep=false、无登录 Me、历史恒空且 POST 只有 cite；只加 deep 帧不能让浏览器进入多轮路径，且原文件已 1254 行。新 E2E 还需显式检查浏览器。 | §8 T6/9.3 明确独立场景模块、Me/权限/能力、签名与有界 SSE；AC-TE-06/23。 | 已修订 |
| RV-13 | P1 | `contracts/run_error_codes.json` 实有 21 项，`scripts/ci.mjs` 硬编码 18；CI 还不包含生产 build、bundle 和 E2E，原“完整门禁”说法不成立。 | §7/9.4 纳入最小 guard 修复并列出独立必跑门禁，不降低用例或预算；AC-TE-16。 | 已修订 |
| RV-14 | P2 | §3.3 推荐普通 img 与现有 next/core-web-vitals 规则冲突，会产生 no-img-element 警告。 | §3.3 使用已有 next/image 的 unoptimized 静态标，不新增依赖。 | 已修订 |
| RV-15 | P2 | 首页新增固定视口高度后，现有 print 规则只解除 html/body，不解除该 main，首页打印仍可能截断。 | §3.2/9.1 为 data-home-scroll 规定打印高度与 overflow 覆盖。 | 已修订 |
| RV-16 | P2 | §11/13 的“本节点”仍指设计节点，容易误导评审的修改范围和状态上报对象。 | §11/13 区分前序与本轮，保留本地定位副本并明确 Subtask #1 fallback。 | 已修订 |

逐节结论：§1–2 的目标与单 worker、Alembic、后端契约真源一致；§3–6 已补齐
身份、状态、布局和交互边界；§7–8 已同步最小实施清单；§9–10 已补齐验收及
部署风险；§11–13 已澄清交付与调度事实。保留 REST/SSE、单 ask 单执行链和
单列迁移；不增加通用聊天服务、持久化话题或额外基础设施，范围适当。

## 1. 概述（Overview）

首页承担搜索标的、回到最近研报和浏览最近标的的入口。当前首页设置了 `min-h-dvh` 和 `overflow-y-auto`，但没有限制容器高度；内容增多时容器随内容增长，而全局 `body` 的 `overflow: hidden` 又阻断了页面滚动。因此，在矮屏、浏览器放大和列表内容较长时，底部研报与页脚可能无法到达。本次将首页自身变成明确的视口高度滚动区域，让鼠标、触控和键盘用户都能访问完整内容。

品牌与助手应与现有投研终端保持一致：深色和浅色主题、绿色强调色、清晰边框、紧凑工具栏与可核验的引用。正式 Logo 使用 GPT Image 生成原创几何图形，经小尺寸与双主题验收后统一接入首页、工作台、认证页面、后台及浏览器图标。任务中“顶级、优雅”的目标落实为可辨识、布局稳定、反馈清楚和一致的交互细节，而不是增加装饰或改造整套设计系统。

报告页右下角新增圆形 AI 助手入口，点击后向上展开浮窗，常规桌面高度约为当前可视视口的 80%。面板包含报告版本上下文、消息历史、证据引用、流式回答和固定在底部的输入区。保留免费“引用检索”和消耗既有额度的“深度分析”两种模式，并为深度分析加入真实的多轮上下文，使“解释刚才的第二点”能够联系上一轮回答。报告阅读、版本切换、来源核对和现有权限边界贯穿整个流程。

## 2. 依据、事实与设计边界

### 2.1 已检查的项目依据

| 依据 | 现场事实与对方案的影响 |
| --- | --- |
| `README.md`、`api/README.md` | 项目以结构化报告为核心；API 必须单 worker；追问独立于报告生成 run。历史章节存在旧说明，接口签名以当前源码为准。 |
| `CLAUDE.md`、`.claude-index/index.md` | 后端 schema 是契约真源；数据库只经 Alembic 变更；低频浮层按需加载；复用既有运行目录。未发现适用于此目录的 `AGENTS.md`。 |
| `web/app/globals.css` | `html/body` 高度 100%，`body` 禁止滚动；已有明暗主题和动画 token。 |
| `web/app/page.tsx`、`RecentReports.tsx` | 首页为无高度上界的滚动容器；最近报告仍为最多 6 份，保留既有请求数与版本深链。 |
| `AppShell.tsx`、`ReportPanel.tsx` | 工作台已经是 `h-dvh` 固定壳；追问入口在报告工具栏；面板首次打开才挂载。 |
| `AskPanel.tsx`、`useAskStream.ts` | 当前是居中贴底、最高 74vh 的单次问答抽屉；已有取消、SSE、引用、429 显式降级。 |
| `api/app/routers/asks.py`、`schemas/ask.py` | `cite` 同步 200；`deep` 异步 202；历史默认对管理员可见范围较广，不能直接当作“我的对话”。 |
| `ask_service.py`、`ask_runner.py`、ask 提示词 | 当前每次仅提交本次问题，不包含上一轮上下文；报告原文是事实边界。 |
| `web/public/logo/` | 存在未跟踪候选 PNG 和旧说明，但没有被产品实际引用；不能假定其已获选，也不能删除或覆盖。 |
| `web/scripts/check-bundle.mjs` | 当前生效的首屏 JS 预算是 **245 kB gzip**，不是旧注释中的 240 kB。 |
| 参考本体 `n_BmWWbXBkyq` | “PyCharm 软件风格”为设计原则。解释为专业工具的克制、密度与操作确定性，并继承项目现有 token，不复制第三方品牌。 |

所有“已检查”均指静态源码检查；设计与评审节点没有启动浏览器或验证运行中的视觉效果。后面的产品效果和测试结果均是实施要求。

### 2.2 稳定需求编号

| 编号 | 必须满足的产品行为 |
| --- | --- |
| R1 | 首页内容溢出时可上下滚动；最终一份最近报告、“查看全部”及页脚均可达。 |
| R2 | 以 GPT Image 实际生成的原创品牌图形替换现有字母 A 占位图形，形成统一资产。 |
| R3 | 有可读报告的标的工作台提供始终可发现的右下角圆形 AI 助手按钮。 |
| R4 | 点击入口向上展开约 80% 可视高度的浮窗；小屏、高缩放、软键盘场景仍可使用输入与关闭按钮。 |
| R5 | 展示连续问答、当前标的与报告版本，并在深度模式中传递经过授权的前序上下文。 |
| R6 | 请求、流式回答、取消、错误、重连、额度不足和离线均有明确反馈及恢复路径。 |
| R7 | 引用可核对，命中段落可定位；键盘、焦点归还、减少动画、打印和主题均保持一致。 |
| R8 | 不突破权限、额度、事实边界和首屏体积预算；原有报告阅读及生成流程继续工作。 |

### 2.3 本轮明确决策

- 默认仍为 `cite`，避免打开新助手就改变既有费用语义；用户主动选择“深度分析”后，本报告当前挂载期间保留选择。
- 多轮上下文仅用于 `deep`；`cite` 仍是报告原文检索，并在模式说明中明确这一点。
- 前序节点交付设计及定位副本；本评审只修订 spec.md。真正生图、替换资产、数据库迁移、业务代码和浏览器验收属于后续实施节点。
- 不新增独立聊天模型 SDK、聊天服务、WebSocket、向量数据库、交易执行或全网搜索。Agent 继续只分析当前报告。
- 保留现有工具栏“追问”入口，作为同一个助手的第二入口；两者共享一个面板和一次请求状态。
- 未提交的 `api/app/security/deps.py`、`policy.py` 等现有工作不属于本需求；不得通过整体还原或整体暂存处理它们。

### 2.4 已比较的方案

| 方案 | 优点 | 代价 | 决策 |
| --- | --- | --- | --- |
| 全站解除 `body` 滚动锁 | 首页改动少 | 工作台固定壳、认证页和已有弹窗可能出现双滚动条 | 不采用 |
| 首页限定高度；沿用追问链路扩展 UI 与上下文 | 修改边界清楚，能保留权限和引用链 | 需小幅扩展 ask 契约与迁移 | **采用** |
| 新建通用聊天后端和独立对话数据库 | 可扩展更广泛的聊天 | 重复认证、取消、预算和事实校验，超出本轮需求 | 不采用 |

## 3. 技术设计（Technical design）

### 3.1 总体结构与调用路径

```text
首页 RSC
  loadHome() -> 原有 reportLibrary / watchlist
  <main data-home-scroll> -> 有限高度、原生纵向滚动
  BrandMark -> 本地静态正式资产

标的工作台 WorkspaceClient -> AppShell -> ReportPanel
  工具栏“追问” / AssistantLauncher
      -> store.assistantOpen
      -> 首次动态载入 AskPanel
      -> createPortal(document.body)
         上下文标题 / AskConversation / AskComposer
         useAskConversation -> useAskStream -> api.ask()

POST /reports/{reportId}/ask
  身份 / 报告 -> 问题与上下文校验 -> 既有额度预留
  cite -> 原文命中 -> 200 AskOut
  deep -> ReportAsk(有效上下文 id) -> 202 -> AskRegistry
       -> report.json + sources.md + conversation-context.json
       -> aragon exec -> ask.* SSE -> GET /asks/{askId}
       -> 既有引用校验 -> 最终回答与引用
```

`useAskStream` 负责**一个活动请求**的状态和流；新增 `useAskConversation` 负责已完成轮次、历史合并、输入草稿和上下文选择。网络状态不放进 Zustand；Zustand 只新增 `assistantOpen` 及开关方法，使工作台快捷键和生成浮卡能感知该浮层。

能力协商新增 `capabilities.features.askConversation`，语义为本 API 同时支持
`contextAskIds` 和本文 `scope=mine` 的隔离约定。后端 `FeaturesOut` 默认 false，
路由通过 `ask_context.available(db)` 实测 `report_asks.context_ask_ids_json`
列存在且本版本契约就绪后返回 true；不由 askDeep、登录态或模型可用性推断。
该测量只反射列，不先查询可能尚未迁移的 ORM 列，不能吞掉普通数据库错误。
前端明确要求 `=== true` 才加载历史、恢复或发送上下文；缺省/false 时显示
“当前服务仅支持单次追问”，仍可显式提交无上下文的 cite/deep。
不得向旧 API 发送 scope 后把未过滤的管理员历史渲染为自己的对话。
askDeep 继续单独决定能否调用模型；临时/降级报告继续禁用 deep。

### 3.2 首页滚动修复

1. `web/app/page.tsx` 的主容器由 `min-h-dvh` 改为 `h-dvh min-h-0 w-full overflow-y-auto`，保留纵向 flex、横向居中和现有内边距；增加 `data-home-scroll`，为 E2E 提供唯一定位点。
2. 唯一内容子容器保留 `w-full max-w-[760px]` 并加 `shrink-0 min-w-0`，防止 flex 为适应视口压缩整页内容。首页主容器 `tabIndex={0}`、`aria-label="首页内容"`，允许键盘直接滚动；不覆盖浏览器 PageDown、Home、End。
3. 首页 header 允许换行，左右两组均设置 `min-w-0`；品牌副标题在窄屏隐藏，品牌文本允许合理收缩，操作组不能被挤到屏幕外。长报告标题仍按既有卡片规则截断并保留完整标题属性。
4. 保持 `body` 的 `overflow: hidden` 及 `AppShell` 的 `h-dvh`。不使用页面挂载时修改 `document.body.style` 的副作用，避免路由切换残留。
5. 内容不足一屏时不制造纵向空白或强制滚动条；内容超过一屏时仅首页主容器滚动。报告数量、`limit=6`、缓存时效、两次首页加载请求和 `libraryHref(symbol, reportId)` 不变。
6. CSS 使用 `100vh` 回退、`100dvh` 覆盖的既有浏览器兼容思路；不设置固定像素最小高度。为 `[data-home-scroll]` 增加 print 覆盖：`height:auto !important; overflow:visible !important`，不能只解除 html/body 的高度和滚动锁。

根因复现与修复证明必须使用真实浏览器的 `scrollHeight > clientHeight`、用户滚动和元素可见性；jsdom 的 class 断言不能证明 R1。

### 3.3 品牌标识与 GPT Image 资产流程

采用一个原创“切面透镜”主标：少量厚实圆角切面围合负形焦点，传达研究、证据汇聚和判断。与周边品牌文字分离，不把整段品牌名烘焙进图片。正式主标不使用字母 A、细密 K 线、机器人头像、金币或复杂 3D 质感；助手按钮使用标准 lucide 图标，两者不混为同一语义。

实施节点先加载当前 `imagegen` 技能，使用可用的 **GPT Image / 内置 image_gen** 生图能力，不调用 Gemini/Nano Banana，不要求用户再次确认。新建图像不设置引用图片参数，`transparent_background=true`；尺寸目标写入提示词，实际尺寸以输出为准。当前 `public/logo/README.md` 关于透明底和模型参数的文字属于旧记录，不能替代实际工具支持情况。

**主提示词（交付内容，直接用于生成）：**

> 为 Aragon Research Terminal 投研工作台设计原创正式品牌图形。图形方向是“切面透镜”：三个厚实、克制的圆角几何切面围合一个清晰的负形焦点，表达研究证据汇聚与精准判断。专业软件产品的简洁几何气质，平面、轮廓稳定、视觉居中、留白均衡，缩小到 24 像素仍能辨认。主色使用项目绿色 #3ecf8e，可有极少量蓝色 #4c9aff 辅助，主体在深色 #0b0c0e 和浅色 #ffffff 背景上都清楚。输出一枚独立图形，透明背景，正方形高分辨率。不要文字、字母、数字、水印、样机、圆角应用底板、金融货币符号、机器人头像、复杂渐变、发光或 3D 立体效果。不得模仿任何已有公司的商标。

生成三个独立候选，每次一张：第二张只调整切面间距，第三张只调整负形开口。候选不直接覆盖既有 PNG。按 24/28/32/56/128px、明暗背景和 200% 缩放观察；小尺寸可辨识占 35%、原创轮廓 25%、项目风格 25%、双主题适配 15%，各项按 1–5 分记录，选择加权最高且各项至少 4 分的候选。没有合格候选则针对失败项用 GPT Image 定向修订；不得以现有字母占位图宣布 R2 完成。

资产由实施节点保存为以下固定文件：

| 资产 | 用途与限制 |
| --- | --- |
| `web/public/brand/aragon-mark-master.png` | 选中候选原始透明 PNG，保留 alpha；目标边长至少 1024px，记录实际尺寸。 |
| `web/public/brand/aragon-mark-128.png` | 网页通用主标，128×128，目标不超过 35 KiB。 |
| `web/public/brand/aragon-icon-32.png` | 浏览器图标，32×32，目标不超过 8 KiB。 |
| `web/public/brand/aragon-icon-192.png` | 高分辨率网页图标，192×192。 |
| `web/public/brand/aragon-apple-180.png` | Apple 图标，180×180；允许按平台要求导出纯底版本。 |
| `docs/plans/terminal-experience/brand-generation.md` | 生成工具、最终提示词、修订说明、候选评分、原始和导出尺寸、SHA-256、真实视觉验收记录。 |

导出仅做确定性的缩放和格式转换，可使用仓库已经安装的图像工具；构图、颜色和透明背景的修改继续通过 GPT Image，不用代码重画一个替代 Logo。不能把 PNG 嵌入 SVG 并宣称矢量源。候选和大图不进入首屏请求；master 仅用于溯源。

新增 `BrandMark` 统一消费 128px 文件，使用项目已有 `next/image` 的 `Image`
并设 `unoptimized`、明确 width/height，直接访问同源小型静态资源，避免
no-img-element 警告及额外优化请求。首页与认证页显示 28px，工作台及后台
显示 24px。邻接品牌文字存在时图片 `alt="" aria-hidden="true"`；独立图标的
可访问名由外层链接/按钮提供。根 `layout.tsx` 的 `metadata.icons` 引用上述
32、192、180px 文件。不新增图片依赖。

品牌文本沿用 `copy.brand` 和 `copy.brandSuffix`。替换范围包括首页、AppShell、AuthShell 的 bare 与普通分支、AdminShell；不改登录、注册与管理员的权限或跳转行为。既有 `web/public/logo/` 不删除、不混入新资产清单。

### 3.4 悬浮入口与面板空间规范

`AssistantLauncher` 在有 `detail.doc` 和明确 `detail.report.id` 时显示；无报告的空态不出现可点击的助手。临时版或降级报告仍可打开引用检索，但深度入口禁用并说明原因。入口相对**浏览器可视视口**固定，不相对报告列，也不随报告内部滚动。

| 元素 | 规范 |
| --- | --- |
| 圆形入口 | 56×56px，24px 桌面右/下间距；小屏 12px；触控目标不少于 44px。 |
| 入口图标 | lucide `Sparkles`，22px；按钮表面 `--bg-surface-2`，细边框，绿色图标与焦点环；不持续闪烁。 |
| 可访问名 | `打开 AI 助手`；打开态 `收起 AI 助手`，`aria-expanded`、`aria-controls` 与面板 id 一致；tooltip 为“AI 助手”。 |
| 桌面宽度 | 440px，最大不超过可视宽度减 48px；面板与入口右边缘对齐。 |
| 常规面板底边 | 位于入口上方 12px，即底部偏移 `24 + 56 + 12 = 92px`，另加安全区。 |
| 圆角与表面 | `--radius-lg`、`--bg-surface-2`、`--border-strong`、`--shadow-pop`；不新增另一套颜色。 |
| 展开动效 | 180ms，向上位移 8px 与透明度变化，使用 `--ease-panel`；减少动画时立即切换。 |
| 层级 | 助手与入口 z=30；来源和现有覆盖层 z=40；模态对话框 z=50。 |

设可视视口高度为 `V`、顶部偏移为 `T`、工作台头部高度为 `H`、安全区为 `S`。常规桌面面板底部空隙 `B=92+S`，高度取 `min(0.8*V, V-H-12-B)`；顶部为 `T+V-B-面板高度`。这样 900px 高视口中面板高 720px，768px 高视口中按头部约束略缩短。**不设置 500px、600px 一类固定最小高度。**

当可视宽度小于 640px 或可视高度小于 640px，进入紧凑形态：左右间距 12px，底部 `12+S`，高度 `min(0.8*V, V-24-S)`；打开期间隐藏底部圆形入口，关闭由面板 header 按钮完成。关闭后入口恢复，焦点可以返回它。紧凑态将模式切换移到独立一行，收起次要时间/成本信息和空态建议区，优先保障输入与关闭操作。

其中 `V < 480px`（包括键盘压缩到该高度）时覆盖 80% 规则，面板高度为
`V-24-S`，顶部/底部至少留 12px；输入为一行、最多两行且有内部滚动，
header 仅保留标题与 44px 关闭目标，版本与模式压为一条紧凑栏，操作区仅
保留模式、发送/停止。以上固定区域设 shrink-0；消息区 min-height 为 0。
568×320 和键盘后 390×320 必须保留可操作的关闭、输入和发送，约 80% 的
视觉目标让位于这些操作。若极端文本放大导致固定区域仍超过净高，以浮窗
自身纵向滚动作为退路，不能把关闭按钮裁掉；正常支持矩阵仅消息区滚动。

新增 `useVisualViewport` 读取 `window.visualViewport` 的 width、height、offsetTop、offsetLeft；无该接口时回退 `window.innerWidth/innerHeight`。仅在面板或入口挂载后监听 resize/scroll，卸载移除，用 rAF 合并更新；SSR 初值使用 CSS `100dvh`。固定元素的 top 使用上述公式，right 还需加 `layoutViewportWidth - (offsetLeft + visualViewportWidth)`，避免软键盘和可视视口平移时偏离屏幕。安全区用 `env(safe-area-inset-bottom, 0px)`。

`AskPanel` 通过 portal 进入 `document.body`，避免 ReportPanel 祖先的 overflow、变换或未来布局调整裁切浮窗。保持非模态 `role="region"`，名称为“AI 助手”；不加遮罩，不锁 body，不设 `aria-modal` 或焦点陷阱，桌面可继续阅读报告。

动态加载骨架和 chunk 错误态也使用相同浮窗位置，并保留标题和可用的关闭按钮，尤其不能在紧凑态隐藏入口后让用户失去退出路径。错误态的“重新加载页面”只恢复资源加载，不自动重发任何问题。

### 3.5 面板信息架构

从上到下分为四个 flex 区域：

1. **固定 header：** 助手图标、标题“AI 助手”、关闭按钮，下一行显示“标的名 · symbol · 报告 vN”。在历史报告中明确“基于当前历史版本”，不能显示最新版信息。
2. **固定模式/上下文栏：** “引用检索 / 深度分析”分段按钮；深度模式显示“参考最近 N 轮回答”。引用检索显示“仅检索本报告原文；连续指代请使用深度分析”。
3. **可滚动消息区：** `min-h-0 flex-1 overflow-y-auto overscroll-contain`；问题与回答按时间排序，一轮内保留问题、模式、状态、正文、合法引用、错误与重试入口。已有答案不被下一次提交替换。
4. **固定输入区：** 与消息区分隔，textarea 自动增长到 4 行后内部滚动，提交/停止按钮、字符数与简短模式说明。输入区域不跟随消息滚动到屏幕外。

空态标题“围绕这份报告继续研究”，说明“回答以当前报告为依据，引用可点击核对”。提供“总结核心结论”“这份报告的主要风险是什么？”“估值依赖哪些假设？”三个建议问题；点击只填入草稿并聚焦，用户点击发送后才发请求。已有历史时不再显示大块空态。

输入上限取 `capabilities.limits.askMaxQuestionChars`，缺失或非正整数时 500，
并夹到 Pydantic 硬上限 4000；由 WorkspaceClient 经 AppShell/ReportPanel 传入。
字符数按 `Array.from(raw.normalize('NFKC').replace(/[\r\n\t]/g, ' ').trim()).length`
计算，并另检查 raw 的 Unicode 码点数不超过 4000。不能用 JS length 或
textarea 的原生 maxLength 充当后端字符上限，也不截断输入法中间态；允许
编辑并显示超限反馈，compositionend 后及提交前验证。服务端继续用现有
normalize_question 拒绝控制字符并作最终判断。Enter 换行、Ctrl/Cmd+Enter
发送；忽略 nativeEvent.isComposing 和 keyCode=229。空白、超长、离线、
历史恢复中和活动请求期间不能重复提交；同步 ref 锁阻止同一事件周期双击。

流式阶段展示可公开的阶段标签与生成中的正文，不展示推理链。消息区仅在距底部不超过 64px 时自动跟随，新增内容经 rAF 合并滚动；读者上翻时显示“有新回答 · 回到底部”，不抢阅读位置。使用独立 `role="status" aria-live="polite"` 播报开始、完成和失败，正文不逐 token 播报。

### 3.6 焦点、Esc、来源与覆盖层协调

- 两个入口共用 `lastTriggerRef`，点击时记录实际按钮。首次打开成功加载后聚焦 textarea；收起不清除草稿、历史或活动请求。
- `lib/focus.ts` 的 `useReturnFocus` 增加可选 options 参数：`handleEscape?: boolean` 和 `returnFocus?: boolean`，默认均为 true，现有调用保持兼容。助手设 `handleEscape=false`，由工作台统一处理 Esc，避免两个监听器关闭两层。
- `lib/keyboard.ts` 的全局处理器先忽略 `event.defaultPrevented` 和输入法 composition。Radix、CompareView 等已处理的 Esc 不再冒泡触发第二次关闭。
- `WorkspaceClient.closeTopLayer` 保留已有较高层优先级，在来源抽屉、命令面板/比较层之后、K 线抽屉之前检查 `assistantOpen` 并收起助手。`ReportPanel` 使用相同 store 开关，不保留第二份 `askOpen`。
- 点击答案的命中段落时，先收起助手，再执行已有 `jumpToBlock(blockId, sectionId)`，定位并高亮报告块。此关闭原因设 `returnFocus=false`，下一帧把焦点交给目标报告块（临时 `tabIndex=-1`），避免焦点返回入口后又把页面滚走。
- 点击来源 chip 保持助手打开，来源抽屉覆盖在上层；Esc 只关闭来源。`SourceDrawer` 在 `onOpenAutoFocus` 记录触发元素，`onCloseAutoFocus` 调用统一导出的 `restoreFocus()`，元素仍连接时才归还焦点。
- 生成进度卡与入口都在右下角：助手关闭时，AppShell 为进度卡设置底边偏移至入口上方；助手打开时隐藏进度卡的展示层，任务与订阅继续存在，顶部 RunStatusBadge 仍可查看进度。关闭助手后按真实 run 状态恢复，不调用 generation.reset/cancel。
- 现有 Sonner toast 也位于右下角。`ToastProvider` 的常规位置改为 bottom-left，紧凑助手打开时按本节后述顶左避让规则覆盖；保留时长和关闭行为，实际触发通知验收。
- 打印时入口及整个面板均标记 `data-print="hide"`；不把聊天记录混进报告导出。

关闭原因明确为 dismiss、jump、context-change。仅 dismiss 归还 lastTrigger；
jump 与身份/版本销毁不归还旧触发元素。useReturnFocus 的 options 通过 ref
读取本次关闭原因，关闭处理和 effect 不能各归还一次。紧凑态先渲染恢复
入口，再下一帧归还；restoreFocus 需检查 connected、非 disabled 且具有
可见布局。跳块等 section/ReadingView 提交完成后再定位，目标不存在则在
报告区提示“段落已不可用”，不聚焦旧面板。SourceDrawer 在自动聚焦前记录
document.activeElement，关闭回调先 preventDefault 再归还；目标消失则交给
仍可见的报告来源按钮，不退回已卸载的消息 chip。Esc 处理者消费事件后
preventDefault；保留 compare→source→command→assistant→kline 的当前优先级，
同时验证 Radix 先消费的事件不进入 closeTopLayer。

RunStatusBadge 的活动进度按钮必须先 `setAssistantOpen(false)`，再设置
`overlayCollapsed=false`，让进度详情、取消和错误操作实际可达；该关闭原因
不把焦点拉回助手。GenerationOverlay 用展示 prop/CSS 隐藏，不以卸载或
generation.reset 实现；原任务及计时器仍按原契约运行。无报告/无入口时
进度卡用原底边；有入口且助手关闭才上移。此改动列入 RunStatusBadge 文件。
紧凑态通知改为 top-left、同时最多一条、限制宽度至 `Vw-92px`，右边缘须
位于 44px 关闭目标左侧至少 12px，避免 bottom-left 通知覆盖全宽输入区。
ToastProvider 只读取 assistantOpen 和轻量视口状态，不静态导入 AskPanel；
视口判断包含 visualViewport.height 的键盘压缩，相关监听仅打开期间启用。
关闭助手即恢复常规 bottom-left 设置，保留原时长及通知语义。

### 3.7 多轮上下文与后端执行

**新增字段选择：** 在现有 ask 请求增加可选 `contextAskIds`，最多 6 个 id；保留每条 ask 对应一次问题和一次答案。增加轻量 JSON 文本列保存最终生效的上下文 id，不新建 conversation/chat 表。

前端从当前话题中已完成、接地且有内容的轮次选最近最多 6 条，按时间从早
到晚发送 id。深度回答可提供 answerText；引用检索可提供 hits 原文。失败、
取消、排队、未接地和当前尚未完成的消息不进入上下文。首次历史加载成功
才初始化 contextCandidateIds；点“新话题”令该数组为空并设置
hasStartedNewTopic=true，同时清草稿。后续历史刷新只更新展示，不得重新
填回话题边界之前的 id；只有此后成功核验的新轮次能追加为上下文。
“新话题”不删历史、不收费，活动/受理不明期间禁用；此边界仅在当前挂载
期间有效，重新进入会按最近历史初始化，不宣称服务器保存了话题分组。
cite 提交及显式 cite 降级始终传空数组；重试使用原轮 question/mode/context
快照，不能改用下一条草稿。上下文失效时须先显式新话题再发送。

服务端不能信任客户端传来的消息正文或引用，在 `api/app/services/ask_context.py` 中实现：

1. 请求结构校验 id 为非空字符串且不超过 64 字符、数组不超过 6 条、不可重复；`cite` 带非空上下文返回 422。
2. 以一次 IN 查询加载全部 id，先完整检查存在性、报告版本和所有者。浏览器 user（含 admin/root）要求每条 `user_id == principal.user_id`；open/service 仅允许同报告且 `user_id IS NULL`，不能因 is_admin 宽放为他人记录。guest 不允许 deep。
3. 完成全部授权检查后才检查就绪与内容。缺失、他人或跨报告版本任意一项不合法，整次统一返回 404 `not_found`；不能先因另一条自有但未完成的记录返回 422 而泄露存在性。全部获授权后，任何非 done、未接地或缺少有效正文/hit 的轮次返回 422 `invalid_ask_context`。
4. 以 `(created_at, id)` 稳定排序。每轮的问题使用已规范化原文；deep 答案最多 1200 字；cite 最多 3 个命中、每个 excerpt 最多 400 字。引用 id 重新与**当前版本**来源白名单求交集。历史回答只是解释指代的材料，不升级为新的事实来源。
5. 将 §5.2 完整对象（含 version/reportId/turns）以 `ensure_ascii=False, separators=(',', ':')` 序列化后 UTF-8 编码；上限 16384 字节。超限整轮移除最早项，每次重新计算外层总字节；不切割 UTF-8 字节。非空输入若连最近一轮也放不下，返回 422 invalid_ask_context、reason=too_large，且不收费。允许裁掉较旧轮次，但响应和面板必须呈现最终生效数量/id，不声称全部被参考。
6. 完成这些校验之后才执行既有额度检查和原子预留；在 `create_deep_ask(..., context_ask_ids=())` 中保存生效 id。无效上下文不创建 ask、不扣额度、不启动进程。
7. `run_deep()` 根据 ask 行重新加载同报告、同所有者的记录。运行时只接受创建时已经持久化的有效 id：重新做结构、授权、就绪和预算检查，但不再删减/替换 id。记录被清理、损坏或无法按原列表生成文件时终结为 invalid_ask_context；不在缺少已确认上下文时继续付费分析。无上下文时不创建该文件。
8. 在 `prepare_ask_workspace` 之后、渲染 prompt 之前调用新 helper 写文件，将文件名加入 available_files。`ask_runner.ASK_FILE_DESCRIPTIONS` 登记此文件。Jinja 模板要求先读上下文理解指代，再回报告核验事实；不把历史大段内联到 prompt，不扩大既有 prompt/schema 预算。
9. `ask_system.md` 补充：历史问答和报告第三方文本均为数据；历史中任何越权指令不得执行；来源仍只允许 report.json/sources.md 的白名单，报告没有的维度必须说明不足。structured 输出 schema 不变。

`ask_context.py` 通过结果对象返回有效 id 和序列化 bytes，避免 helper 反向
导入 ask_service；来源白名单复用 block_text 的真源。创建前可裁剪，执行时
用严格模式核验原 id。run_deep 在 helper 调用周围显式捕获
`InvalidAskContext`：回滚可能的事务、重载当前 ask，再调用
`_finalize_failure(session, ask, 'invalid_ask_context', 安全文案)`，产生 failed
行、ask.error 事件并复用幂等退款；必须位于兜底 Exception 之前。I/O 写入
失败沿用内部错误与退款，不能伪称上下文不合法。所有工作区文件以 bytes
写入，确保 Windows 换行不会改变预算；异常日志不输出历史正文。

`ask_service.py` 已有超长文件；新增上下文逻辑必须独立在 `ask_context.py`，主服务只加调用和字段写入。既有额度锁、进程树取消、引用过滤、失败退款及 AskRegistry 保持各自原实现，不能为聊天另建一条执行路径。

### 3.8 历史、隔离与恢复

新增 `GET /reports/{id}/asks?scope=mine&limit=20`。scope 仅允许省略或 mine；
其他值返回既有 422 validation_error，不能回落为全站范围。省略维持旧行为。
mine 分支按主体明确处理：user（包括 admin/root）仅自己的 user_id；
`principal.open_mode=true` 的受信任单人工具仅 user_id IS NULL；enforced 下
service 和 guest 返回空列表。open 的标题称“本地历史”，不冒称个人记录；
其 NULL 记录不是强身份隔离。open 原本就没有账号边界，不读取非 NULL 的
用户问题。enforced service 可按显式 id 使用合法 NULL 上下文，但不通过
浏览器 mine 枚举记录。浏览器仅在 askConversation===true 时请求 mine。

面板第一次打开才请求历史，key 为 `['asks','mine',identityKey,reportId]`，`staleTime=0`，`gcTime=0`；关闭期间组件保留，已有数据不卸载。不要将问答写入 localStorage、sessionStorage、Service Worker 或全局持久化 store。响应按时间升序展示，按 ask.id 去重；历史失败显示“暂时无法读取历史”，保留当前已显示轮次和草稿，提供重试。

只显示最近 20 条，明确该窗口且不新增分页 API；内存可累计至 100 条。
历史加载/恢复完成前禁用发送。读取失败时保留草稿，允许用户显式选择
“仅开始新问题”；此操作清上下文并告知未恢复旧任务，不自动 POST。
历史合并只能追加未知 id 或以较新服务端终态更新同 id，不覆盖本地 draft、
mode、话题边界、已接收的终态和当前流式预览。已开始本地请求后到达的旧
历史快照不能抢占活动槽或把 queued/running 回写到 done；若无法判断新旧，
按 id GET 对账。新报告/身份的 epoch 在渲染 key 切换时作废，取消旧 query，
清除其 cache；queryFn 必须消费 AbortSignal，无法中断的响应仍须受 epoch
守卫。epoch 不写入全局持久化状态。

`identityKey` 从实际 `Me` 推导：有 user.id 用该 id，游客为 guest，open 为 open。`AskPanel` 使用 `identityKey + ':' + reportId` 作为 React key；身份或报告版本变化时旧面板的请求 epoch 立即失效、关闭 SSE/计时器、清空草稿与内存记录。新报告若助手已打开则保持浮窗可见，展示“已切换到报告 vN”，随后加载该版本自己的历史；没有报告时关闭入口与面板。

同一报告切换章节、全文视图、K 线范围和缩放不改变 key，不重置对话。工作台卸载时将 `assistantOpen=false` 并清除订阅；已经发起的后端任务继续运行，重新进入可通过自己的历史恢复。切换账号也必须清理旧身份 query key；不能等旧请求返回后再做过滤。

历史中有 queued/running/cancelling 记录时，默认恢复时间最新的一条为活动请求：读取详情，若仍活动则使用新 token 从 `after=0` 订阅并靠 seq 去重；若已结束直接展示终态。不通过重新 POST 恢复。其他活动记录只显示状态与“刷新状态”，不同时打开无上限连接。任何时刻当前面板最多一条 EventSource。

选定活动槽期间不再创建新 ask；GET 若返回 queued/running/cancelling 仍是
活动态，不能沿用 phaseForResult 将其判为 done。resume 前先退订旧连接并
清空该轮重放预览和 seq，再从 0 重放；普通重连才从已有最后 seq 接续。
ask.done 和 ask.error 均触发终态 GET 与额度刷新；流结束和详情暂时不可读
不等于本次已核验。历史“刷新状态”仅 GET，不改变上下文或发送收费请求。

### 3.9 请求状态机与故障处理

| 当前状态 / 动作 | 下一状态与反馈 | 保存与恢复 |
| --- | --- | --- |
| closed / 打开 | loading chunk → loading history → ready | 首次加载轻量骨架，失败可重新加载；不调用 ask。 |
| ready / 发送有效问题 | submitting；立即追加问题及回答占位，禁用重复发送 | 保存本轮 question/mode/context id 快照，草稿区可先清空。 |
| cite 返回 200 | done；原文命中、引用或无结果说明 | 合并为完整轮次，下一次提交不会清除。 |
| deep 返回 202 | streaming；阶段、正文、停止按钮 | 保存 askId 和生效 contextAskIds；签名绑定此 askId。 |
| 收到 ask.done / ask.error | verifying → done/error/cancelled | GET 完整 AskOut 对账；成功后才允许符合条件的 done 轮次进入上下文并刷新 Me。 |
| 关闭面板 | closed，但活动流继续 | 保留草稿和消息；完成时入口显示一个小点和“有新回答”的可访问提示。 |
| 停止 | cancelling → 服务端确认 cancelled 或已完成 | 不在请求失败时直接标成已取消；停止按钮等待响应，状态以服务端为准。 |
| 停止请求网络错误 | 保留活动状态，提示“停止未确认” | 允许重试停止或重新读取状态，不新建 ask。 |
| too_many_asks / quota_exceeded | 当前轮次显示额度原因 | 只有点击“改用引用检索”才发 cite；拒绝则保留原问题，可编辑。 |
| token 401/403 | 结束连接尝试，保留已显示内容 | 刷新 Me、显示登录/权限指引，不用空 token 无限重连。 |
| SSE 中断 | 显示“连接中断，正在重连” | 既有退避最多 3 次，带最后 seq、新签名，不重复 POST。 |
| 3 次重连仍失败 | disconnected；GET ask 对账，活动时显示“重新连接” | 保留活动槽和发送锁，不将失联误报为 Agent 失败。 |
| 最终详情获取失败 | unverified；保留预览，标记“结果待确认” | 仅只读 GET 重试，禁止加入上下文或开始下一轮，直到终态核验。 |
| POST 超时、结果不明 | submission-unknown；“暂未确认是否受理，请刷新记录” | 保留发送快照与锁，重取历史；不自动重发任何 POST。 |
| 明确的请求/Agent 错误 | 本轮错误卡、错误原因、重试入口 | 保留前轮与问题快照；用户主动重试形成新 attempt，旧失败不参与上下文。 |
| invalid_ask_context | “部分历史已不可用，可开始新话题” | 点击新话题后清空 context，再由用户发送；不静默移除上下文并收费。 |
| offline | 提示“离线时可阅读已有内容，联网后继续提问” | 禁用发送，保留内存草稿；联网不自动发问题。 |

扩展 `useAskStream` 时，所有 await 之后（包含 catch、cancel 和 refresh）均校验 epoch 和 askId；已切换身份/版本的迟到结果不得更新新面板。`submit` 使用快照字段，编辑下一条草稿不会改变当前回答所属问题。活动状态下禁用改变本轮模式和“新话题”。

重连次数按“上次收到有效新 seq 后的连续失败”计算，不能在 EventSource 的 onopen 中立即归零，否则“连接打开后立刻报错”会绕过三次上限。测试必须覆盖反复 open→error，以及重复 seq 不刷新进展的情形。

将 AskPhase 增加 `verifying / disconnected / unverified / submission-unknown`；
这是客户端状态，不改数据库 AskStatus。isActive/发送锁包含上述未确定态，
不能只判断 streaming。refresh 对 done、failed、cancelled 才 settled；活动
结果映射为 streaming 或 cancelling 并可恢复订阅。只有 GET 成功的结果可
进入上下文，syntheticResult 只作未核验预览，不得填 grounded=true。
401/403 结束重连并显示恢复身份的入口；Me 变化前不解锁再发。

POST 抛异常时区分明确拒绝（如 422、429）和受理未知（网络中断/5xx）。
submission-unknown 无 askId 时，历史窗口中仅当服务端 id、规范化问题、
mode 和提交时间窗口可唯一对应时才绑定已有 ask；有歧义不按“同问题”猜。
查不到也不能证明未受理，因为列表有窗口限制。提供继续刷新，或用户显式
选择“我知道可能重复，仍发送新请求”才释放该快照的锁；不得自动重试。
这是一条明确确认重复费用的产品动作，不是实施时向用户索要开发确认。
此轮不增加服务端幂等键、全局串行约束或新会话表。

## 4. 接口设计（Interface design）

### 4.1 REST 契约

所有路径均以 `/api/v1` 为前缀；继续使用 HttpOnly Cookie、现有 CSRF 与短签名。

| 方法和路径 | 入参 | 出参及兼容性 |
| --- | --- | --- |
| `POST /reports/{reportId}/ask` | `{question, mode?, contextAskIds?}` | cite=200 AskOut；deep=202 AskQueuedResponse；省略新字段完全兼容。 |
| `GET /reports/{reportId}/asks` | `limit=20`，1–100；scope 仅省略或 mine，分支见 §3.8 | AskListResponse，items 按 (created_at,id) 降序，前端转升序；total 为当前返回条数。 |
| `GET /asks/{askId}` | 无 | AskOut；对象级鉴权维持现状。 |
| `POST /asks/{askId}/events-token` | 无 | `{token, expiresIn}`；令牌绑定 askId。 |
| `GET /asks/{askId}/events` | `token`、`after>=0` | 既有 text/event-stream。 |
| `POST /asks/{askId}/cancel` | 无 | 继续使用源码现有 CancelResponse `{status:'cancelling',runId:askId}`；不要因改 UI 改名。实际状态需后续 GET/SSE 确认。 |
| `GET /capabilities` | 无 | `features.askConversation` 新增布尔值；缺省 false，实测列及本版本契约就绪后 true；不代表 askDeep 可用。 |

请求示例：

```json
{
  "question": "刚才第二点中的估值假设，有哪些报告证据？",
  "mode": "deep",
  "contextAskIds": ["ask_previous_1", "ask_previous_2"]
}
```

`AskRequest.question` 仍保留 Pydantic 4000 字硬上限，产品上限经现有 normalize_question 根据配置执行。新增 `contextAskIds: list[str]` 默认为空、最多 6 条，字段级 validator 检查空 id、长度和重复；结构错误沿用 422 `validation_error`。

`AskOut` 与 `AskQueuedResponse` 均增加 `contextAskIds: list[str]`（默认空），表示最终生效的上下文；旧客户端可忽略。新增语义错误 `InvalidAskContext`，HTTP 422、code=`invalid_ask_context`，统一错误封套不变：

```json
{
  "error": {
    "code": "invalid_ask_context",
    "message": "历史上下文已不可用，请开始新话题",
    "requestId": "server-request-id",
    "detail": {"reason": "not_ready"}
  }
}
```

reason 仅取 `mode_mismatch / not_ready / unavailable / too_large`，不含私有问题正文、用户信息或失败 id。跨用户/版本与不存在统一走 404，不返回该错误示例。既有 401、403、report_not_ready、ask_deep_unavailable、429 等码原样保留。

cite 的非空 contextAskIds 语义检查放在 cite/deep 分支分流之前，保证不创建
免费 ask 也不误入 deep。字段用 default_factory=list，拒绝 null、非字符串
元素和重复 id；响应两种 schema 和 to_ask_out 都写入生效数组。新能力关闭
时新前端禁止上下文及 mine 查询；旧前端省略字段仍兼容新 API。

新增 `scope` 是已有路由的参数，无需新增权限路径；不要改动 `security/policy.py` 和 `deps.py` 中的现场修改来实现此功能。

api.reportAsks 保留现有 `(reportId, limit=20)` 调用兼容，新增第三个 options
对象 `{scope?: 'mine', signal?: AbortSignal}`；scope 经 URLSearchParams 编码，
signal 传给已有 request 的 RequestOptions。新助手显式传入 mine 和 queryFn
提供的 signal，旧调用不传 scope；不为此更换通用请求封装。

### 4.2 SSE 契约

保留 `ask.stage / ask.delta / ask.usage / ask.done / ask.error`，帧的 id 继续是每 ask 的 seq：

```text
id: 3
event: ask.delta
data: {"text":"对应的报告依据是……"}

```

UI 层不发明 `chat.delta` 或第二条流。重连获取新签名并带最后 seq；终态关闭 EventSource。心跳不作为正文。收到 ask.done 的摘要不代表完整证据已读取，应立即 GET AskOut；预览与最终结果有明确区别。

### 4.3 前端组件和 hook 接口

```ts
interface BrandMarkProps {
  size?: 24 | 28 | 32 | 56;
  className?: string;
}

interface AssistantLauncherProps {
  open: boolean;
  isActive: boolean;
  hasUnread: boolean;
  controlsId: string;
  onToggle: (trigger: HTMLButtonElement) => void;
}

interface AssistantStatus {
  reportId: string;
  identityKey: string;
  isActive: boolean;
  hasUnread: boolean;
}

interface AskConversationOptions {
  reportId: string;
  identityKey: string;
  open: boolean;
  deepAvailable: boolean;
  conversationAvailable: boolean;
  dailyLimit: number;
  questionMaxChars: number;
}

interface AskSubmitInput {
  question?: string;
  mode?: AskMode;
  contextAskIds?: string[];
}
```

在现有 AskPanelProps 保留 reportId、open、deepAvailable、dailyLimit、onJumpToBlock、onJumpToSource、triggerRef，增加 `conversationAvailable`、`panelId`、`reportLabel`（name/symbol/version/isHistorical）、`questionMaxChars`、`onStatusChange(status: AssistantStatus)`。ReportPanel 保存仅供入口显示的 isActive/hasUnread 镜像；回调必须核对当前 identityKey/reportId，版本切换同步清零，不能接收旧实例清理阶段的状态。真正请求状态仍只属于 useAskStream，不在父组件启动第二份请求。账号身份从既有 useMe 读取，能力和报告标签来自当前 detail，不能从 meta 标签或 URL 字符串猜测版本。

`useAskStream` 保留原入口，新增 `resume(ask: AskOut)` 和只读 `refresh(askId)` 恢复方法。`useAskConversation` 对外给出 turns、draft、mode、active、historyStatus、hasUnread、send、retry、stop、startNewTopic、refreshHistory、reconnect 及显式允许新 attempt 的动作；传入 onDone/终态回调时只更新对应 askId。扩展前先更新 AskPhase、reducer 和 UseAskStreamResult，不能用类型断言伪造新状态。各公开函数不超过 5 个形参。

组件拆分为 Launcher（入口）、Panel（浮层和布局）、Conversation（历史滚动）、Answer（已有证据呈现逻辑）、Composer（输入）、Boundary（懒加载错误恢复）；避免把所有逻辑堆回 ReportPanel 或 800 行以上的大组件。

## 5. 数据模型（Data model）

### 5.1 数据库

迁移新增 `api/app/db/migrations/versions/0009_ask_context.py`：

```text
revision      = "0009_ask_context"
down_revision = "0008_account_recovery"

report_asks.context_ask_ids_json: TEXT NULL
NULL / "[]" -> 无前序上下文
其他值        -> 按时间排序的 JSON string[]，最多 6 项
```

只执行 `op.add_column`；不重建表、不新建 chat 表、不增加无查询用途的索引。ORM 增加可空 Text 列和 `context_ask_ids` 只读解析属性：NULL 为 []，JSON 损坏返回 [] 并记录不含正文的结构化警告；创建时只能写入已校验列表。旧记录无需回填，降级先停写并备份，再 `op.drop_column`。

上述容错属性只保障历史读路径；运行 queued ask 时 helper 必须检查原列的 JSON 结构，损坏的非 NULL 数据以 invalid_ask_context 失败并退款，不能将它当成无上下文继续执行。

这些 id 是上下文快照定位信息，不建自引用外键。历史保留期清理允许删掉旧 ask，新 ask 的既有答案仍能展示；不再重新运行已完成 ask。新请求引用已清理记录时返回明确错误。账号删除和报告删除继续走当前生命周期机制，新增列不产生第二份原始回答副本。

### 5.2 运行工作区

```json
{
  "version": 1,
  "reportId": "report-id",
  "turns": [
    {
      "askId": "ask_previous_1",
      "question": "这份报告的主要风险是什么？",
      "mode": "deep",
      "answerText": "仅来自已存回答的有限摘录……",
      "hits": [],
      "sourceIds": ["S1"]
    }
  ]
}
```

文件位于 `settings.asks_dir/<askId>/conversation-context.json`，最大 16 KiB，
包含完整外层 JSON 的 UTF-8 bytes，按 §3.7 的固定方式序列化并 write_bytes，
无 BOM/平台换行转换。创建阶段返回的 id 必须与 turns[].askId 及数据库列
完全同序；执行阶段无法保持则失败退款。文件不在 public 目录，不添加
日志正文；报告仍在 report.json，不复制整份报告进入每轮 question。

### 5.3 客户端状态

```ts
interface AskTurnView {
  localId: string;
  askId: string | null;
  question: string;
  mode: AskMode;
  contextAskIds: string[];
  phase: AskPhase;
  result: AskOut | null;
  preview: string;
  error: { code: string; message: string } | null;
}

interface AskConversationView {
  identityKey: string;
  reportId: string;
  turns: AskTurnView[];
  draft: string;
  mode: AskMode;
  activeLocalId: string | null;
  historyStatus: 'idle' | 'loading' | 'ready' | 'error';
  hasUnread: boolean;
  contextCandidateIds: string[];
  hasStartedNewTopic: boolean;
}
```

消息以 askId 合并；本地正在发送的轮次使用 localId，在服务端返回 id 后绑定，不另追加相同问题。历史合并按 id 更新，禁止用数组下标当 React key。当前挂载期间最多保留 100 轮，达到上限时移除最早的终态轮次并显示“仅展示最近 100 轮”，不删除服务器数据；发送上下文始终取最近最多 6 个有效 id，独立于可见消息上限。

UI store 只新增 `assistantOpen: boolean`、`setAssistantOpen(open)`。不持久化问题、回答、签名、身份或对话历史。

## 6. 交互流程与屏幕状态

### 6.1 主路径

首页滚动到最近报告 → 打开带 reportId 的版本深链 → 点击右下圆形入口 → 看见当前版本上下文及自己的历史 → 输入问题 → 引用检索返回原文，或主动选择深度分析获得流式回答 → 点击引用核对或继续追问 → 收起面板继续阅读。

### 6.2 屏幕映射

| 状态编号 | 对应需求 | 内容和关键操作 |
| --- | --- | --- |
| S01 | R1/R2 | 首页短屏滚到底部；正式品牌标与最近报告、“查看全部”可达。 |
| S02 | R3/R7 | 工作台助手关闭；圆形入口、tooltip、焦点环，报告原布局保持。 |
| S03 | R4/R5 | 助手空态；版本上下文、模式、建议问题与底部输入。 |
| S04 | R5/R6 | 至少两轮问答；第二轮理解上一轮指代，流式状态与停止入口可见。 |
| S05 | R6/R7 | 引用命中定位、来源抽屉覆盖助手、Esc 分层关闭。 |
| S06 | R6 | 额度/权限不足、网络失败或取消，保留已完成回答和草稿。 |
| S07 | R4 | 390×844 及低高度紧凑形态，模拟键盘后输入与关闭仍在可视区域。 |
| S08 | R7/R8 | 浅色主题、高缩放、减少动画、打印隐藏。 |

本节点交付文档，不生成 UI PNG。后续浏览器验收截图使用这些状态编号保存，并在验收说明中注明实际尺寸、主题和缩放；不得将示意图冒充实现截图。

## 7. 文件 / 模块变更计划

以下为**后续实施清单**；前序节点创建设计及定位副本，本评审仅修订 spec.md。

| 文件 | 操作 | 单行实施意图 |
| --- | --- | --- |
| `docs/plans/terminal-experience/spec.md` | 前序新增、本轮修订 | v2 设计、评审记录及验收真源。 |
| `docs/plans/terminal-experience/plan-current.json` | 前序已有、本轮只读 | 本地定位副本；不是服务器黑板，本评审不改写它。 |
| `web/app/page.tsx` | 修改 | 有限高度首页滚动、header 窄屏换行、复用 BrandMark。 |
| `web/app/globals.css` | 修改 | 集中助手尺寸/间距 token、低高度紧凑态、动效；保持 body 锁和原主题 token。 |
| `web/app/layout.tsx` | 修改 | metadata.icons 指向正式品牌资产。 |
| `web/components/ui/BrandMark.tsx` | 新增 | 带显式尺寸、装饰语义的同源品牌图形。 |
| `web/components/ui/Toast.tsx` | 修改 | 桌面通知左下；紧凑助手打开时顶左一条并避让关闭目标，不导入聊天代码。 |
| `web/components/shell/AppShell.tsx` | 修改 | 工作台品牌替换、传递问题上限、协调生成浮卡与助手位置。 |
| `web/components/shell/WorkspaceClient.tsx` | 修改 | 传递 capabilities 上限；Esc 单层关闭；离开工作台重置助手可见性。 |
| `web/components/shell/RunStatusBadge.tsx` | 修改 | 点击活动进度时收起助手并展开原进度卡，不改变任务状态。 |
| `web/components/auth/AuthShell.tsx` | 修改 | 两个品牌分支统一使用主标，不改变 bare 导航边界。 |
| `web/components/admin/AdminShell.tsx` | 修改 | 替换后台占位标，保留后台导航与权限。 |
| `web/components/report/ReportPanel.tsx` | 修改 | 工具栏和圆形入口共享状态，传入实际报告标签、portal 面板及段落跳转。 |
| `web/components/report/SourceDrawer.tsx` | 修改 | 明确来源 chip 的焦点归还并保持非模态来源核对。 |
| `web/components/ask/AssistantLauncher.tsx` | 新增 | 圆形入口、活动/未读状态及可访问属性。 |
| `web/components/ask/AskPanel.tsx` | 修改 | 约 80% 视口浮窗、portal、四区布局与焦点接入。 |
| `web/components/ask/AskPanelBoundary.tsx` | 新增 | 动态 chunk 失败显示可恢复提示；重新加载不触发 ask。 |
| `web/components/ask/AskConversation.tsx` | 新增 | 多轮记录、历史状态和受控自动滚动。 |
| `web/components/ask/AskAnswer.tsx` | 新增 | 从现有 AskPanel 提取 AnswerBody/Result/HitButton，保留引用和接地逻辑。 |
| `web/components/ask/AskComposer.tsx` | 新增 | 草稿、字符限制、IME、发送/停止和模式说明。 |
| `web/components/ask/useAskConversation.ts` | 新增 | 历史加载与合并、上下文选择、草稿隔离与未读状态。 |
| `web/components/ask/useVisualViewport.ts` | 新增 | 可视视口、软键盘与固定浮层位置计算和事件清理。 |
| `web/components/ask/useAskStream.ts` | 修改 | 上下文入参、恢复既有 ask、异步 epoch 保护、确认取消与结果重取。 |
| `web/components/generation/GenerationOverlay.tsx` | 修改 | 接受展示隐藏/底边偏移，不改变真实任务状态。 |
| `web/lib/store.ts` | 修改 | 只增加助手可见性及动作。 |
| `web/lib/focus.ts` | 修改 | 可选 Esc/焦点归还控制，导出共享 restoreFocus。 |
| `web/lib/keyboard.ts` | 修改 | 忽略已处理及 composition 事件，阻止双层 Esc。 |
| `web/lib/api.ts` | 修改 | ask body 扩展 contextAskIds；reportAsks 支持显式 scope=mine。 |
| `web/lib/types.ts` | 修改 | 镜像后端 ask 新字段和请求接口，不在这里发明后端字段。 |
| `web/lib/copy.ts` | 修改 | 集中新增助手、历史、恢复、模式和上下文文案。 |
| `api/app/schemas/ask.py` | 修改 | 可选 contextAskIds、验证规则及响应中的生效 id。 |
| `api/app/schemas/capabilities.py` | 修改 | FeaturesOut 增加默认 false 的 askConversation 契约能力。 |
| `api/app/routers/capabilities.py` | 修改 | 每次读取实测上下文列及本版本支持，公开 askConversation，不与 askDeep 混用。 |
| `api/app/models/ask.py` | 修改 | context_ask_ids_json 与解析属性。 |
| `api/app/db/migrations/versions/0009_ask_context.py` | 新增 | 从 0008 增量增加可空上下文列。 |
| `api/app/errors.py` | 修改 | 添加 422 invalid_ask_context，沿用封套。 |
| `api/app/routers/asks.py` | 修改 | 上下文授权校验、mine 查询参数及响应字段。 |
| `api/app/services/ask_context.py` | 新增 | 上下文加载、同用户同版本校验、预算裁剪与工作区文件写入。 |
| `api/app/services/ask_service.py` | 修改 | 创建时持久化有效 id，运行时写上下文文件，失败接既有退款。 |
| `api/app/agent/ask_runner.py` | 修改 | 登记上下文文件说明，保持同一 CLI 与 schema。 |
| `api/app/agent/prompts/ask_task.md.j2` | 修改 | 读取历史理解指代并回报告核验。 |
| `api/app/agent/prompts/ask_system.md` | 修改 | 明确历史不是新证据，版本号从 8 递增至 9。 |
| `web/public/brand/aragon-mark-master.png` | 新增 | 正式 GPT Image 原始透明资产。 |
| `web/public/brand/aragon-mark-128.png` | 新增 | 网页用缩小主标。 |
| `web/public/brand/aragon-icon-32.png` | 新增 | 小尺寸浏览器图标。 |
| `web/public/brand/aragon-icon-192.png` | 新增 | 高分辨率浏览器图标。 |
| `web/public/brand/aragon-apple-180.png` | 新增 | Apple 图标。 |
| `docs/plans/terminal-experience/brand-generation.md` | 实施新增 | 生图 provenance、评分和真实检查结果。 |
| `api/tests/test_ask_context.py` | 新增 | 所有权、版本、预算、上下文注入与失败不扣额度。 |
| `api/tests/test_ask_api.py` | 修改 | mine 作用域、兼容请求、上下文请求与终态。 |
| `api/tests/test_ask_schema.py` | 修改 | 请求边界和响应新增字段默认值。 |
| `api/tests/test_ask_service.py` | 修改 | 新字段持久化、运行时上下文与取消/退款回归。 |
| `api/tests/test_capabilities_api.py` | 修改 | 新列存在/缺失、脱敏响应中的 askConversation，与 askDeep 独立。 |
| `api/tests/test_ask_context_migration.py` | 新增 | 真实旧库迁移前后数据和回退检查。 |
| `web/tests/ask-panel.test.tsx` | 修改 | 面板分拆后保留既有引用、额度与键盘行为，补多轮。 |
| `web/tests/ask-conversation.test.tsx` | 新增 | 历史合并、epoch、身份与报告隔离、上下文提交、恢复及取消失败。 |
| `web/tests/brand-mark.test.tsx` | 新增 | 共享组件尺寸和可访问语义、品牌入口覆盖。 |
| `web/tests/e2e/landing-scroll.spec.ts` | 新增 | 真实 overflow、键盘/鼠标滚动和高缩放首页。 |
| `web/tests/e2e/ask.spec.ts` | 修改 | 圆形入口、80% 几何、多轮、引用/来源及低高度布局。 |
| `web/tests/e2e/fixtures/fake-api.mjs` | 修改 | 仅注册独立助手场景路由及首页足量报告数据，不继续堆入对话脚本。 |
| `web/tests/e2e/fixtures/ask-scenarios.mjs` | 新增 | 隔离 Me/能力、deep 202/SSE/历史及按测试重置的有界脚本，复用报告 fixture。 |
| `web/scripts/check-bundle.mjs` | 修改 | 保留 245 kB，并将 AskPanel 加入 MUST_BE_LOADABLE。 |
| `scripts/ci.mjs` | 修改 | 移除过时的 18 项硬编码，保留结构预检并由已有契约测试校验 21 项与后端一致。 |
| `docs/plans/terminal-experience/acceptance.md` | 实施新增 | 命令实测、截图索引、资产证据和剩余限制。 |

不新增 package 依赖；不变更运行 worker、认证权限矩阵、报告 schema 或交易相关模块。如实施发现必须增加未列文件，先在本文“实施过程发现的方案缺陷”中说明再纳入评审，不能无记录扩大范围。

## 8. 实施顺序与可交接任务

### T1：首页可达性与品牌共用组件

先用浏览器 fixture 建立矮屏首页无法滚到底的失败用例，再修改 page 容器与 header；验证最后一份研报和 footer 可见、工作台仍独立滚动。实现 BrandMark 的尺寸/语义和各消费点；正式资产生成完成之前不能把占位图当最终验收结果。

### T2：GPT Image 生成与资产接入

依 §3.3 生图、查看候选、定向修订、选中并导出，写入 provenance。接入 metadata 图标并检查实际网络请求和明暗主题。此任务失败时可继续其他独立开发，但 R2 保持未完成，不静默换模型或复用未经检查的旧候选。

### T3：后端上下文契约

先记录测试 collect 基线；写 schema/API 所有权失败用例和真实迁移用例。新增迁移/ORM/helper，再修改 ask 路由、服务与提示词；先跑定向测试。用 fake Aragon 验证第二轮上下文文件真的可读取、source 白名单未扩大、失败不扣额度，而不是只测试 request 多了数组字段。

### T4：消息与流状态

先同步后端类型和 api 客户端，再提取 AskAnswer；实现 useAskConversation 与 hook 恢复方法。用可控 EventSource 验证历史合并、迟到响应、关闭重开、版本/身份切换、取消失败与最终 GET 失败。保留既有 cite 和额度降级用例的语义。

先定义新增客户端 phase，再实现 resume/refresh；历史初始化、流恢复和发送
必须共享唯一活动槽。加入新话题后刷新历史、旧快照迟到、受理未知锁、cite
降级空上下文和规范化字符计数用例，不能只验证组件外观。

### T5：悬浮 UI 与工作台集成

实现 Launcher、viewport hook、Panel/Composer/Conversation 与 chunk 错误边界；ReportPanel 去掉局部 askOpen，接入 store 和实际报告标签。随后协调 Esc、来源焦点、生成卡占位、打印与减少动画。保留首次打开才加载的边界，打开前不调用 ask 历史接口。

### T6：端到端与最终交接

扩展 fake API 的 deep SSE 和历史场景，避免只在 mock 的 cite 模式中验收“Agent 对话”。运行 §9 命令和浏览器矩阵，将真正执行的结果与截图写入 acceptance.md；评审依据本文文件表逐项核对，再由后续代码评审节点按其权限提交。本设计节点不运行 git commit。

fake API 用测试专用 scenario cookie 隔离新增有界脚本，默认场景仍保持旧
askDeep=false，避免改变既有 E2E。新增场景同时提供匹配的 /me、权限、
askDeep=true、askConversation=true、同用户同版本历史及 POST 202、token、
seq/SSE、GET 终态、cancel；第二轮要保存并断言实际收到的 contextAskIds。
场景记录按测试重置，不依赖用例先后。真实后端的鉴权和退款另由 pytest
集成测试证明，不拿 fake 的行为替代。新增 landing-scroll.spec.ts 必须先调
failIfBrowserRequired()，再按 browserAvailable 决定 skip，复用既有 fixture。
原 fake-api.mjs 已超过代码行数约定，本轮新增行为全部放 ask-scenarios.mjs，
入口只做路由委托；不顺带拆分整个旧文件。新模块仍遵守 800 行/50 行函数
等现有约束，复用报告 fixture 与发送方法，不能复制另一份报告文档。

### T7：部署与回退

1. 实施前记录 pytest collect 基线及现有 CI guard 的 18/21 矛盾。仅修复该
   guard，不删除测试、不改变错误码契约、不扩大本轮到通用 CI 重构。
2. 备份 SQLite 及运行目录，确认 SQLite 支持当前既有 drop_column 路径。
   维护窗口内停止旧单 worker、执行 Alembic 到 0009，再启动新 API。
   不并行运行两个 API worker；先用旧形态请求确认兼容，再检查
   askConversation=true、mine 隔离、上下文请求和取消退款。
3. 最后部署 Web。模拟旧 API 缺少能力标志时，新 UI 必须禁用历史与上下文
   并提示单次模式；不能仅依赖发布时序保证隔离。能力 false 不是新 ORM
   兼容未迁移数据库的承诺，生产新 API 仍必须先迁移。
4. 优先回退 Web，再停止 API 并回退应用版本；新增 nullable 列先保留。
   确认无新 API 读写后才允许 downgrade/drop_column，降级会丢新增上下文
   id，但不得破坏原 ask/answer/events。重新升级后旧行按空上下文读取。
5. GPT Image 资产或任一必需验收未通过时，独立开发可继续，产品交付仍
   保持未完成；设计评审“通过”不构成跳过真实生图与验收的豁免。

## 9. 测试与验收标准（Testing & acceptance criteria）

### 9.1 浏览器几何与交互矩阵

| 条件 | 必须观察到的结果 |
| --- | --- |
| 首页 1440×900、6 份报告 + 活跃项 + 长标题 | 首页仅一个纵向滚动容器；滚动后最后卡片、“查看全部”及 footer 可见且可点击。 |
| 首页 1280×600、1024×480、390×844、320×568 | 无不可达底部，无内容被外壳裁切，无全页横向滚动；键盘可到全部链接。 |
| 桌面实际浏览器 125%、150%、200% 缩放 | 首页内容完整可达；工作台入口和面板可用。记录浏览器实际 zoom；deviceScaleFactor 不能冒充 CSS 缩放。 |
| 自动化缩放替代 | 按缩放后的 CSS viewport 缩小并验证相同边界；仍需另做一次真实 200% 人工检查。 |
| 桌面 1440×900 助手 | 高度约 720px（±2px），右边距 24px；panel bottom 与入口顶边相隔 12px。 |
| 1280×768 助手 | 高度受头部净空约束，上边缘不遮住必须可达的关闭控件，不硬撑 80%。 |
| 390×844 与 568×320 横屏 | 紧凑形态内输入和关闭按钮始终可见；长答案只滚动消息区。 |
| 390×320 键盘压缩与四行草稿 | 使用低高度形态，输入最多两行内部滚动；关闭、发送/停止均可点击，面板不强守 80%。 |
| 可视高度由 844 减至 420、offsetTop 改变 | 模拟软键盘后 panel 重新定位，textarea 与发送按钮在 visualViewport 内；监听器卸载后不残留。 |
| 明暗主题 + 减少动画 | 不出现深色底图片方块；边框/文本有清楚层次；reduce-motion 下不等待位移动画。 |
| 打印 / HTML 与 MD 导出 | 助手与入口不出现；原报告导出内容和版本不变。 |
| 首页打印 | data-home-scroll 解除固定高度和 overflow；页脚不被截断。 |

Playwright 首先断言 `scrollHeight > clientHeight`，再通过 wheel/键盘滚动并检查底部元素 bounding box 位于 viewport 内；不能仅 `scrollIntoViewIfNeeded()` 后就宣称用户能滚动。助手同样检查实际 bounding box、输入区可见性和引用跳转结果，而非只检查 class 文本。

### 9.2 稳定验收编号

| 编号 | 通过条件 |
| --- | --- |
| AC-TE-01 | §9.1 首页矩阵通过；正常短内容不强制产生第二条滚动条。 |
| AC-TE-02 | 正式 Logo 来自真实 GPT Image 输出，有提示词和 SHA-256；五处品牌分支及 metadata 均消费正式资产。 |
| AC-TE-03 | 24px 主标轮廓清楚；透明边缘、明暗主题、200% 缩放检查有实际记录。 |
| AC-TE-04 | 两个入口共享一个浮窗，只有一条活动 EventSource；无报告时无悬挂入口。 |
| AC-TE-05 | 普通桌面约 80% 可视高度，紧凑态/键盘出现时输入与关闭可用。 |
| AC-TE-06 | 两轮 deep 请求第二轮传前轮合法 id；fake runner 实际收到该轮内容，并且不能引用报告外来源。 |
| AC-TE-07 | 新旧版本、两个账号、管理员“mine”数据不串；跨用户/跨版本 id 的失败不可枚举。 |
| AC-TE-08 | 空白/超长/IME/双击不能误发送；Enter 换行、Ctrl/Cmd+Enter 发一次。 |
| AC-TE-09 | 关闭重开保留草稿与消息；导航后通过历史恢复已有活动 ask，恢复请求没有新 POST。 |
| AC-TE-10 | 取消请求失败不显示已停止；SSE 重连与终态 GET 失败不自动新建收费请求。 |
| AC-TE-11 | 429 及用户额度不足只在显式选择后改用 cite；额度数字取 Me 的真实 bucket，而不是 dailyLimit 减本页次数。 |
| AC-TE-12 | 点击命中段落可见定位和高亮；来源 Esc 只关来源，再按才关助手；焦点回到正确触发元素。 |
| AC-TE-13 | 未打开助手不请求历史、不下载聊天面板；生产首屏 ≤245 kB gzip，AskPanel 确实存在于异步 chunk。 |
| AC-TE-14 | 真迁移可保留旧 ask/answer/event 数据；NULL 上下文读为空；新字段可 round-trip，回退不损坏其他列。 |
| AC-TE-15 | 开助手时生成任务继续运行，顶部状态有效；关助手后进度卡正确恢复，入口与卡片不叠。 |
| AC-TE-16 | 当前全部检查通过、测试 collect 数量不低于基线，结果是实际输出而非沿用共享黑板旧数字。 |
| AC-TE-17 | mine 的 user/admin/root 只含自己的记录；open 只含 NULL 所有者本地记录；enforced service/guest 为空，非法 scope 不回退。 |
| AC-TE-18 | 旧 API 缺少 askConversation 时不请求 mine、不发送上下文；新 API 能力与实际列一致，旧请求在升级后仍有效。 |
| AC-TE-19 | 混合非法所有权与自有未就绪 id 一律 404；完整 JSON ≤16384 bytes，单轮过大创建前 422；运行时上下文失效以原码失败且退款一次。 |
| AC-TE-20 | 历史未完成不能发送；迟到旧快照不覆盖新消息；resume 活动 GET 不误判 done；终态待核验和受理不明不自动解锁/重发，重复 POST 为零。 |
| AC-TE-21 | 新话题后刷新历史仍不引用旧话题；重试取原快照；显式 cite 降级 contextAskIds=[]。 |
| AC-TE-22 | 打开助手后点击顶部活动进度，面板收起且原进度卡可操作；任务 id 不变。紧凑通知不覆盖关闭及输入操作。 |
| AC-TE-23 | 新 E2E 缺浏览器且 PW_REQUIRED=1 时真实失败；deep 场景具备 Me/权限/能力，第二轮实际走 202/SSE/GET，普通场景不受影响。 |

额度文案读取 `Me.quota.deepAsk.remaining`（null 表示不限），辅以每报告上限说明；完成、失败/取消退款后刷新 Me。`capabilities.limits.askDailyLimit` 仅作每报告上限说明，`useAskStream.deepRemaining` 的旧页面内估算不能作为“今日真实剩余额度”。

### 9.3 后端重点用例

需覆盖无新字段的旧请求、空列表、6/7 个 id、重复/空/超长 id、cite 非空上下文、同报告不同用户、同用户不同报告版本、管理员试图引用别人的记录、未完成/未接地轮次、旧记录清理、16 KiB 整轮裁剪、输出来源白名单和注入文本。验证无效请求前后的 quota ledger 与 ask 数量一致；运行时上下文丢失则终态失败并退款。

另外覆盖 null/非字符串 id、先授权后就绪的混合错误列表、相同 created_at
的稳定排序、全角/兼容字符 NFKC 展开、emoji 码点计数、Windows 序列化总
字节、单轮超过预算、运行时不得二次裁掉 id。用真实服务实例和 fake CLI
把记录失效注入 run_deep 的 helper 调用前，断言 failed/errorCode、ask.error
和额度余额均正确，并重复对账证明仅退一次；不能只断言 helper 抛异常。
能力测试在真实迁移前后反射列，覆盖非管理员脱敏结果仍保留该布尔值。
前端补 history/send 竞争、模式/草稿快照、token 获取失败、cancel 返回
ask_not_active 后只读 GET 对账，以及来源关闭焦点与紧凑进度入口用例。

用真实 Alembic 迁移临时 SQLite 旧库到 0009，插入包含 answer/events 的旧数据后核对内容；测试降级再升级。数据库迁移和鉴权不得 mock，模型执行使用已有 fake CLI。

### 9.4 运行命令（实施节点逐条执行）

工作目录为仓库根，PowerShell 不串 `&&`。以下是待执行检查，本节点未声称已运行：

```powershell
npm run test:api -- --collect-only
npm run test:api
npm run test:web
npm run lint
npm run typecheck
npm run build
node web/scripts/check-bundle.mjs --next-dir=web/.next --json
npm run ci
```

上述命令均须检查退出码。实施 T7 先消除 scripts/ci.mjs 过时的 18 项 guard；
现有契约文件为 21 项，不能靠减少契约内容让 CI 过关。npm run lint 只运行
Web ESLint；API Ruff 需使用项目实际 Python 单独执行，不能依赖 CI 的
optional 步骤把 lint 失败当作通过。根目录有 api/.venv 时命令为：

```powershell
api/.venv/Scripts/python.exe -m ruff check api/app api/tests
```

无该虚拟环境则使用 npm run test:api 实际选用的 Python 执行同一 ruff 命令，
并记录路径。当前 npm run ci 仅包含 API/Web 测试与类型/lint，不含 build、
bundle 或浏览器，不能用它代替本节其他门禁。上述 bundle 的 --next-dir=
已按当前脚本核对。浏览器门禁：

```powershell
$env:PW_REQUIRED='1'
npm --prefix web run test:e2e -- landing-scroll.spec.ts ask.spec.ts workspace.spec.ts
Remove-Item Env:PW_REQUIRED
```

`PW_REQUIRED=1` 必须配合每个 spec 的 failIfBrowserRequired() 才能确保缺失
浏览器时报错；实现时不得漏掉。必需门禁是本节各命令、§9.1 真实缩放和
Logo 视觉记录的合取。默认 E2E 按现有开发服务配置运行，不能把该结果
写成生产首屏验证；首屏预算另以上面的生产 build/manifest 为证据。
恢复 PW_REQUIRED 的既有值，原来未设置时才 Remove-Item，避免污染环境。
长运行按任务协议登记等待并输出进度。产物写 acceptance.md 与截图子目录，
不把 .next、数据库、运行日志或候选原图整体加入提交。

## 10. 风险与缓解（Risks & mitigations）

| 风险 | 具体缓解措施 |
| --- | --- |
| 首页修复改变其他页面滚动 | 限定首页容器高度，不解除 body 锁；验证工作台列滚动与路由往返。 |
| “80%”在软键盘/短屏中不可用 | 百分比基于 visualViewport；净空优先、紧凑断点、无固定 min-height。 |
| 助手遮住生成卡或来源 | 明确 z 层；生成卡临时隐藏展示但任务继续；来源覆盖助手并单层 Esc。 |
| 管理员历史意外混入他人问题 | 新增显式 scope=mine；上下文也强制同所有者，不能直接复用管理员可读范围。 |
| 历史回答被当新事实或注入指令 | 只用 id 拉服务端原文，限同版本、限大小，提示词明确数据边界并继续来源校验。 |
| 多轮上下文无限增加成本 | 最多 6 轮、16 KiB；只传工作区只读文件，既有 turn/time/token 预算不变。 |
| 请求与执行使用了不同上下文 | 保存创建时有效 id；执行严格复核且不二次裁剪，失败按原码终结并幂等退款。 |
| 迟到响应写入新账号/版本 | identity/report key、同步 epoch 作废、所有 await 后守卫、私有 query 不持久化。 |
| 超时自动重试导致双扣 | 发送同步锁；恢复只读 GET/SSE；不自动重发不明确的付费 POST。 |
| 新 Web 连接旧 API | askConversation 缺省 false；能力未就绪不读 mine、不传上下文；先迁移/API 后 Web。 |
| 新列部署顺序造成旧库报错 | 先备份并执行 Alembic，再更新服务；旧行 NULL，旧请求兼容，回退停写后执行。 |
| Logo “好看”但小尺寸不可识别 | 先按实际 24px 和双主题评分，单项不达标则修订；不以大图预览替代产品验收。 |
| 生图工具不可用 | 记录真实失败，独立部分继续；R2 保持未完成，不能替换供应商或伪造 provenance。 |
| Logo/消息逻辑增大首屏 | 小静态标、面板首次打开才 import，AskPanel 纳入双向异步检查，不提高 245 kB 预算。 |
| 现有脏工作区被一起提交 | 使用精确文件清单与差异核对，保留用户修改，代码评审节点仅暂存本次文件。 |
| 参考文档包含旧接口说明 | 签名以实际 schema/router 为准，例如 cancel 响应沿用 runId 字段；有冲突记录再处理。 |
| 现有 CI 预检先于测试失败 | 修复 18/21 过时 guard，保留既有契约测试；构建、bundle、Ruff 与浏览器均独立验收。 |

## 11. 评审验证与调度交接

前序设计节点创建本目录设计及定位副本；本轮评审仅允许修改 spec.md。
任务状态工具和 task-board 指定的 fallback 属于运行元数据，不是业务交付。
本评审没有生成 Logo、修改业务代码、运行应用测试或执行 git commit。
后续实施不能把方案评审通过解释为产品已经完成。

本轮产物定位为：

```json
{
  "kind": "plan",
  "id": "current",
  "path": "docs/plans/terminal-experience",
  "slug": "terminal-experience",
  "doc": "spec.md",
  "deliverable": "docs/plans/terminal-experience/spec.md"
}
```

应写共享黑板的命令：

```powershell
python .agentmesh/.task-tools.py put --kind plan --id current --set path=docs/plans/terminal-experience --set slug=terminal-experience --set doc=spec.md --set deliverable=docs/plans/terminal-experience/spec.md
```

本轮实际 get、put、progress 均在连接前返回 TASK_SERVER_URL is not set，
与前序交接一致；不能将不可用解释为记录缺失，不能猜测服务器地址。
本评审使用前序输出和已读取的 plan-current.json 精确定位，未扫描目录猜
当前方案，也未改写该只读副本。已在本轮开始尝试 put，并累计尝试三次；
没有成功写入服务器。恢复调度环境后须重新 put 再 get 验证。
评审完成后执行 complete，失败最多三次，再按任务提示只更新 task-board
的 Subtask #1 并运行 status。若 status 也无法连接，仅验证本地 fallback，
在 Completion Notes 明确服务器状态未验证，不宣称调度已触发下一节点。

设计交付验收：本 spec 包含概述、技术设计、文件表、接口、数据模型、测试验收和风险；篇幅超过 800 词/字要求；git status 能看到新文档。文档内“实施过程发现的方案缺陷”保留给后续节点，不虚构已发生的实现或测试结果。

## 12. 实施过程发现的方案缺陷

### IM-01：迁移 head 回归预期需随新 revision 更新

§7 未列 `api/tests/test_migration_0008.py`，该文件把当前 head 固定为
0008，增加 0009 后 head 断言必然失败。相同问题还存在于
`test_cost_ledger_migration.py`、`test_migration_0004.py`、
`test_migration_0006.py`、`test_migration_0007.py`。仅同步 latest-head 预期，
保留原数据迁移与回退检查；新增 0009 集成测试独立验证上下文列。
将该测试文件纳入本轮最小变更清单，补充验收为完整 pytest 通过。

### IM-02：提示词版本断言分开核对

`api/tests/test_prompt_discipline.py` 原先要求 research/ask 都为 8；
§7 仅将 ask 递增为 9。将该测试纳入最小清单，分别断言 8/9，
保留 research 提示词不变，验收为提示词纪律及完整 pytest 通过。

### IM-03：浏览器 fixture 的凭据跨源支持

§8 的 fake API 原本只允许 CORS `*`，而客户端现已使用
`credentials: include`，真实浏览器会拒绝响应。测试夹具限定本地
3010 来源并允许凭据；用现有 `aragon_sid` 携带测试专用场景标记，
使 RSC 的既有 cookie 转发也能识别隔离场景，无需更改产品认证。
新增 `brand-preview.png` 为确定性缩放的明暗背景视觉验收联图。

### IM-04：工作台助手集成抽离

直接在 `ReportPanel.tsx` 实施 §7 会使现有文件超过 800 行，故增加
`web/components/ask/ReportAssistant.tsx` 承接懒加载、未读、活动状态、
身份 key 与关闭原因，ReportPanel 只传报告标签、触发 ref 与段落定位。
不增加依赖或新业务能力；验收仍覆盖共享单面板、焦点及异步 chunk。

### IM-05：版本加载期间保留实际展示上下文

`WorkspaceClient` 新 report query key 在未命中缓存时暂时 data 为空，
会被当作“无报告”关闭并卸载助手。为 report query 添加同标的的
placeholderData，保持原展示报告、版本标签与提交 reportId 绑定，
直到新版本到达再整体切换并使旧请求 epoch 作废；不借用其他标的。
补充验收：打开助手切换未缓存版本，浮窗保持，新标签与历史独立。

### IM-06：既有快捷键 E2E 等待客户端就绪

`web/tests/e2e/workspace.spec.ts` 在 goto 后立即发 Ctrl+K，Next 开发
服务尚未水合时不会有快捷键监听，造成非确定性失败。将该文件纳入
最小清单：等待同一页面助手入口（客户端挂载信号）可见后再发快捷键，
保留原来命令面板及 Esc 的行为断言，不跳过、不删除用例。

### IM-07：共享焦点 hook 的默认同步语义

全量 Vitest 揭示 `useReturnFocus` 默认调用仍要求 Esc 后立即归还焦点。
助手需要下一帧等待紧凑入口恢复，不能把这一时序套给所有既有调用。
保持默认调用同步且只归还一次，仅 handleEscape=false 的助手走下一帧。
`web/tests/a11y.test.tsx` 纳入最小清单：可见触发者 fixture 提供真实
布局的测试矩形（jsdom 不做布局），保留现有行为断言及所有用例。

实施在用户指定的共享工作区原位进行；既有未提交修改与设计文档不适合
转入干净 worktree，且本任务禁止额外目录及提交，故不创建工作树。

## 13. 评审验证范围与交接证据

前序记录：v1 为 668 行，静态检查过 34 个修改目标与定位副本；这些是设计
节点的记录，不沿用为本轮验证数字。本轮读取现有实现后完成 v2 评审，
实际验证项目如下，最终数量和退出码以本轮命令输出及 Completion Notes 为准：

- 用 UTF-8 解析 spec，检查 v2、唯一评审记录/结论、16 条问题闭合、全部
  必需章节、代码围栏配对及文内新增跨节引用。
- 逐项确认 §7 所有“修改”目标存在，规划新增项未冒充已落地；检查能力、
  错误码、状态、文件清单与 AC-TE-01…23 的对应关系。
- 本地定位 JSON 的 path/doc/deliverable 与本 spec 一致，registration 仍为
  unavailable；读取不等于服务器黑板 get 成功。
- 检查文档空白与 Git 差异，并以开始时文件哈希清单核对工作区：除本 spec
  外的已跟踪和未跟踪业务文件应保持原样，保留用户已有修改。
- 核实 CI 基线矛盾：契约实际 21 项、脚本 guard 仍为 18；这里只记录并
  修订实施方案，不在本节点修改该脚本或运行全套应用门禁。
- 本轮静态命令已通过：16 条评审项均标为已修订，23 个验收编号唯一，
  39 个“修改”目标均存在，13 组代码围栏配对，UTF-8/空白检查通过。
  git diff --check 退出 0；开始/结束 626 个工作区文件的 SHA-256 对比，
  唯一内容变化为本 spec，定位副本与业务源码均保持原样。
- 应用测试、浏览器截图、GPT Image 正式资产、迁移及生产部署均待实施；
  本轮静态评审不宣称这些已通过，也不执行 git commit。

## 评审结论

**通过。** v2 已在正文修复全部 13 项 P1 和 3 项 P2，未发现 P0，
无未解决的 P0/P1 设计问题；架构和范围与当前项目约定一致。

后续按 §7–8 实施、按 §9 完成真实验证；正式 GPT Image 资产和产品验收
仍是代码实施的必需交付。调度环境缺少 TASK_SERVER_URL 属于状态同步限制，
按 §11 的指定 fallback 记录，不以本地完成状态代替服务器确认。
