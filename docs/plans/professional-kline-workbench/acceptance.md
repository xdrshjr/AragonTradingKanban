# 专业 K 线工作台实施验收记录

日期：2026-11-20。依据：`spec.md` v2，实施节点 Subtask #2。

## 实施范围

第 8 节的 44 项文件全部创建或更新。后端先定义契约，前端镜像后消费；
未引入依赖、数据库迁移或新权限，未修改共享 `indicators.py`、报告快照或生成协议。
本节点没有执行 `git commit`。既有 security、CLAUDE、索引、其他功能文档及 logo
改动保留，未纳入本功能的实施范围。

图表支持完整行情读数、19 项后端指标、独立成交量及单一振荡副图、共享时间索引、
日期与键盘检视、视区控制、报告框选、本地区间测量、触控、扩展视图、分页数据表
及 CSV/PNG。新旧服务有明确兼容分支，错误刷新保留上次校验成功的同口径数据。

## 自动化门禁

| 命令 / 检查 | 实测结果 |
| --- | --- |
| API 实施前 collect | 2277 项 |
| `python -m pytest --collect-only -q -o addopts=''`（api 目录） | 2330 项，增加 53 项，无收集回退 |
| 图表算法 / API / standard 定向回归 | 全部通过；API 图表 44 项、图表算法 8 项，另补原算法兼容断言 |
| 9 个前端图表 / 面板 / 左栏 / URL 专项文件 | 81 项；由最终 CI 一并验证 |
| `npm run ci` | 第二轮完整退出 0，输出 `CI green` |
| CI · API Ruff | 通过 |
| CI · API pytest | 2320 passed、10 skipped，533.33 秒 |
| CI · TypeScript | 通过 |
| CI · ESLint | 通过 |
| CI · Vitest | 58 个文件全部通过，781 passed、1 skipped，38.46 秒 |
| `npm run test:web`（收尾修改后全量复验） | 58 文件通过，781 passed、1 skipped，34.36 秒 |
| `npm run build`（最终 production） | 退出 0，Next.js 15.5.29，编译及类型检查通过 |
| `node web/scripts/check-bundle.mjs --next-dir=web/.next --json` | 退出 0，首屏 226.0 kB gzip ≤245 kB；11 个既有异步面板边界均通过 |
| `PW_REQUIRED=1 E2E_PRODUCTION=1 npm --prefix web run test:e2e -- tests/e2e/chart.spec.ts tests/e2e/workspace.spec.ts --headed` | 退出 0；24/24 通过，15 项 chart +9 项原有 workspace，约 1.1 分钟；0 失败、0 flaky、0 skipped |

API 跳过为 4 项缺少 `TEST_POSTGRES_URL`、5 项既有 policy 空参数集、
1 项缺少 pypinyin；图表新增用例没有跳过。API 的 9 个 warnings 为 Windows
Proactor 管道清理警告，未出现测试失败。Vitest 的 1 个跳过为原有环境条件用例。
没有降低断言、提高体积预算或删除旧测试来通过门禁。

第一轮 CI 的后端、tsc、lint 已通过；新 URL 测试的 `/reports/{id}/blocks?`
被夹具的一般 report 规则误匹配，导致缺失 `items`。补齐合法区间查询响应后，
相关 28 项连续两轮通过，再运行第二轮完整 CI，结果如上。

## 验收矩阵

“自动”指真实计算 / SQLite / 数据边界 / 组件测试；“浏览器”指 headed Chromium
的真实 canvas，最终命令及截图在后续段落汇总。

| 编号 | 实施与证据 |
| --- | --- |
| AC-01–04 | 后端前收统计及非正基准测试；组件验证符号、null、0、量额与报价隔离 |
| AC-05 | 同一计算起点跨 range 重叠值及窗口外前收 API 回归 |
| AC-06 | 后端周/月按日线聚合；浏览器 fake API 真实重采样及末收一致断言 |
| AC-07 | 四组共同黄金 fixture、seed、null 边界、MACD×2、KDJ 与旧 RSI 隔离 |
| AC-08–09 | 真实 canvas 开启 EMA、BOLL 三轨、KDJ；各指标设置和图例组件测试 |
| AC-10–11 | 三窗竖线偏差 ≤1 CSS px；控制器范围锁、跨窗待清理与程序事件隔离回归 |
| AC-12 | 全 null whitespace/carrier、历史不足与 leadingPartial 自动测试 |
| AC-13 | 指标 / 主题不重建主图；扩展视区、唯一实例及查询去重浏览器回归 |
| AC-14 | 共享 query 校验请求身份；参数 key 隔离并清理临时状态 |
| AC-15–18 | 输入状态机、捕获丢失、取消、工具互斥；区间测量纯函数及日期输入 / URL 隔离 |
| AC-19 | URL 测试保留 report/section/block/view/range，图表 tf/win/adj 各自写入 |
| AC-20 | v1→v2 单向迁移、坏 JSON、未知版本、隐私模式及初始化不覆盖测试 |
| AC-21–23 | 窄容器、移动/低高/200% 真浏览器；键盘、日期输入 Escape 与焦点恢复 |
| AC-24–25 | 错误、空包、legacy、未知版本自动及浏览器回归；来源/复权单位 API 测试 |
| AC-26 | CSV 全字段、元数据、BOM/CRLF/注入测试；PNG DPR、失败清理、版本取消及真实下载 |
| AC-27 | 连续 20 次扩展切换的真实 canvas 数量与 pageerror 断言 |
| AC-28 | 强制认证测试客户端游客 GET chart profile 返回 200 |
| AC-29 | 图表全平 RSI=50；原 bundle 九 key、standard 与快照全平 RSI=100 保持不变 |
| AC-30 | SQLite 写前验证、重复会话、聚合溢出、撤回柱替换、事务失败及 refill 时间戳回滚 |
| AC-31 | 新上市唯一周/月桶、边界桶排除计算但保留显示、空窗口计数等式 |
| AC-32 | 稳定 PricePanel 拥有者，portal 承载点优先级及扩展跨断点的唯一图表 |
| AC-33 | focus 对象消费一次；空包确认无交集；历史定位后刷新/扩展不重放 |
| AC-34 | 空白释放临时鼠标、跨窗不闪回、触摸固定与拖动/双指不误选 |
| AC-35 | 交易日期后端及共享浏览器边界校验；轴标签直接取 stats.periodEnd |
| AC-36 | 微价整包精度、MACD 三线共用精度、KDJ J 不截断及 null 窗清水平光标 |
| AC-37 | 刷新按 ts 恢复选择和视区、同 index 新版本清旧水平光标、测量失效清理 |
| AC-38 | Ctrl/Meta/Alt、Shift、IME、输入 Escape、工具及固定检视逐层退出测试 |
| AC-39 | 可选 nullable 类型承接旧服务；新包未知版本/错位/身份错误拒绝入缓存 |
| AC-40 | 面板与选中行共享尾部 stats；独立报价整对及来源回退，缺价无孤立涨幅 |

## 性能与截图

后端环境：Windows 10.0.26200、Intel64 Family 6 Model 170、Python 3.11.7。
2600 根纯指标计算运行 20 次，中位数 16.25 ms、最大 17.798 ms，低于 100 ms；
该数不含 provider、网络或数据库时间。

浏览器性能开发模式记录见 `chart-performance-dev.json`，包含原始各 20 次样本、
CPU、DPR、UA 与视口。2600 根计算历史裁出 1306 根五年窗口；
dev 绘图 P95 为 175.4 ms、hover P95 为 20.9 ms。
开发模式的绘图数字不作为 production 150 ms 门槛通过证据。
最终生产模式记录见 [chart-performance-production.json](chart-performance-production.json)：
同一 CPU、Chromium 159、1440×900、DPR≈1；2600 根真实计算、1306 根显示，
绘图与 hover 各 20 次真实采样，最终评审复验 P95 为 **75.5 ms / 5.8 ms**，
满足 ≤150 ms / ≤32 ms，原始数组完整保留。
绘图测量包含响应返回、数据版本 DOM 更新及真实 canvas 双 rAF，
hover 测量由实际指针输入至读数 MutationObserver，不是空回调或固定 sleep。

窄屏适配硬证据：380px 布局、实际绘图区 288px、1306 根；
首根 x=-0.780069px、末根 x=286.23px，均在允许的 1px 边界内。
相邻真实柱间距 0.22px，反推视区跨度 1309.09 根 ≥1306；
首末平均反推为 1309.50 根，两种检查均通过。
生产构建只在本次验收进程注入 `NEXT_PUBLIC_API_BASE` / `INTERNAL_API_BASE`
为本机 fake API `http://127.0.0.1:8010`；未改 `.env`、部署配置或运行真实生成引擎。
验收服务退出后已再执行不注入 fake API 环境变量的常规 `npm run build`，
退出 0，并再次通过首屏 226.0 kB 的 bundle 门禁；
本地构建产物已恢复项目原有 API 配置，源码和 `.env` 均未改变。
最终 `npm run lint` 及差异检查通过；11 张截图的链接全部有效，测试服务已退出。

截图位于 [screenshots/](screenshots/)：

- [1440×900 深色桌面](screenshots/chart-desktop.png)
- [浅色桌面](screenshots/chart-light.png)
- [扩展图与断点切换](screenshots/chart-expanded.png)
- [390×844 触控](screenshots/chart-mobile.png)
- [568×320 横屏](screenshots/chart-landscape-568x320.png)
- [低高度布局](screenshots/chart-low-height.png)
- [200% 等效视口与 DPR](screenshots/chart-200-percent.png)
- [380px 容器与 yfinance 后复权说明](screenshots/chart-yfinance-hfq-380.png)
- [短历史月线与历史不足](screenshots/chart-short-monthly.png)
- [区间测量](screenshots/chart-measurement.png)
- [窄容器完整适配 1306 根](screenshots/chart-fit-1306.png)

200% 图采用 CDP 几何等效设置：720×450 CSS 视口、DPR=2，
对应 1440×900 物理像素；没有把它描述为实际点击 Chrome 菜单缩放。

## 复核与修复

独立复核及真实浏览器验证捕获并修复：指针丢捕获后的手势集合、
有效绘图区边界、日期输入 Escape、无来源程序 crosshair 事件、跨窗延迟清理、
同 index 刷新及设置切换的旧水平光标、异步 range 应用后的竖线位置、
同 datasetVersion portal 移交原样保留视区、短序列定位全部数据、
空包 focus 消费、独立报价价格/涨幅/来源整对回退、微价/量轴中文单位、
旧服务正前收涨幅兼容与低 DPR 导出。每项有对应自动回归或真浏览器复现。

新 chart 业务模块均不超过 800 行，TypeScript AST 检查新图表组件与控制器
函数均不超过 50 行；图表库仅从动态 CandleChart 依赖进入。
后端 Registry 提前排序的问题作为 I-01 记录于 spec，不修改公共 standard 行为。
生产浏览器另捕获 I-02：库默认最小柱间距 0.5px 使 379px 面板中
1306 根窗口的“适配”实际只展示约 576 根。先记录规格，再在各图窗显式设为
0.01px；7 项控制器/交互测试与重新 production build 通过。
最终浏览器增加首末柱及实际相邻柱间距反推完整窗口跨度的硬断言，
不以 Home/End 自动平移或隐藏超界竖线冒充完整适配。
I-03 的生产 bbox 红测进一步确认：缺数提示和固定检视按钮使读数增高，
canvas 在 hover 后下移 46.60px。按 spec 记录后固定读数块高为
`clamp(180px,24dvh,240px)`，保留所有内容的内部滚动及低高屏静态定位；
最终 E2E 对有效/缺数切换前后 canvas y 偏差 ≤1px 做硬断言。
该回归实际偏差为 0px，Shift/显式框选/取消在生产模式连续三次通过，
随后完整 24 项通过。调试记录已删除 React fiber / history 包装；
URL 断言使用标准异步 `toHaveURL`，失败时才记录真实 location/RSC 事件，
未改应用路由、不跳过手势、未放宽任何阈值。
排查期间 URL 提交断言曾间歇超时，事件日志同时确认 draft 已提交且界面范围正确；
没有取得应用自动覆盖 range 的证据。最终当前构建的连续手势回归及完整 24 项均通过，
仍保留失败时的 location/RSC 被动日志供后续复核，不将推测写成已证实的路由缺陷。
最终独立两阶段只读审查均通过：先核对规格符合性，再核对代码质量与安全。
44 项文件边界齐全，无未解决 P0/P1；测试与最终 production 浏览器证据单独记录，
没有将文件存在或审查通过替代真实运行验收。
最终工作区边界检查：44 个计划文件均存在，除开工前已记录的既有改动之外，
没有发现超出计划的新增或修改文件；`git diff --check` 通过。

## 调度交接

当前子进程未注入 `TASK_SERVER_URL`，get/put/progress/wait_async 均在联网前失败。
已按前置节点给出的 `plan-current.json` 和任务板定位方案，没有扫描猜测目录。
完成上报按任务协议执行，结果记录在本节点 task-board.md 的 Subtask #2。
当前缺少任务服务配置，远端 plan/current 和完成状态未验证；
不得把本地文件中的 completed 当作远端状态或下游调度已经确认。

## 最终代码评审（Subtask #3）

2026-11-20，以 `02260d0` 为基线，对照 v2 的 44 项文件计划和
AC-01–40 逐项核对覆盖、相邻功能回归、命名、风格及错误处理。
后端与前端分别完成独立只读审查，另复核工作台集成、报价口径与提交边界。
未发现未解决 P0/P1，发现并修复以下一项 P2：

| 编号 | 问题、原因与修复 | 验证 |
| --- | --- | --- |
| CR-01 | 数据表同根数刷新时，重置 effect 把分页移至末页，但历史键盘选择未变，选择同步 effect 不会重跑；`ChartDataTable.tsx` 的选择同步依赖加入 `dataset`，保证刷新后历史选中行可见 | 先新增 45 根数据、固定 index=5、替换数据版本的回归，修复前在刷新后的历史行断言失败；修复后面板 21 项全通过，独立复核通过 |

本轮实测证据：

| 命令 / 检查 | 结果 |
| --- | --- |
| 后端图表、API、旧指标定向回归 | 72 passed，退出 0 |
| `npm --prefix web run test -- tests/price-panel.test.tsx` | 21 passed，退出 0；包含 CR-01 |
| `python -m pytest --collect-only -q -o addopts=`（api 目录） | 2330 collected，退出 0；不低于实施前 2277 |
| 最终完整 `npm run ci` | 退出 0，`CI green`；Ruff、pytest、TypeScript、ESLint 全通过；Vitest 58 文件、782 passed / 1 skipped |
| 修复后 production build + headed Chromium：chart 与 workspace | 24 passed，退出 0；15 项图表与 9 项原工作台，无失败、重试或跳过 |
| production 性能样本 | 2600 根计算历史、1306 根显示，各 20 个原始样本；绘图 P95 75.5ms，hover P95 5.8ms |
| 不注入 fake API 变量的最终 `npm run build` | 退出 0，恢复项目正常 API 配置 |
| `node web/scripts/check-bundle.mjs --next-dir=web/.next --json` | 退出 0；226.0kB ≤245kB，11 个既有异步边界通过 |
| 工作区边界及差异检查 | 44 项计划文件齐全；新业务模块均 ≤800 行；28 个既有无关文件哈希不变；`git diff --check` 通过 |

生产截图与 `chart-performance-production.json` 已由本轮真实浏览器复验更新；
开发模式样本保留为实施阶段记录。E2E 测试服务已退出，3010/8010 无监听。
验证在保留既有改动的工作树运行；既有鉴权修复、CLAUDE、索引、logo、
其他方案/探针均不属于本次提交。
本功能精确提交范围为 44 项计划文件、spec、本轮方案定位记录
`plan-current.json`、2 份性能样本和 11 张截图，共 59 个文件；
不含依赖变动、迁移、构建产物或临时文件。
首次暂存新增规格文档时，`git diff --cached --check` 检出 5 处 Markdown
行尾双空格；已改为段间空行，复查通过，未改变技术要求。

本轮 CI 的 pytest 仍有 Windows Proactor 管道清理警告，既有鉴权组件
测试仍有 React act 提示及 Vite CJS 弃用提示；均未导致门禁失败，
未修改无关模块或降低断言来消除这些既有提示。
