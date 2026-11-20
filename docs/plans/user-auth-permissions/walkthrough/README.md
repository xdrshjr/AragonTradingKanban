# 真实浏览器走查记录（spec §25.3.6 / AC-235）

> 本目录的截图与 `result-*.json` 由 `walkthrough.mjs` **在最后一次前端改动之后**实跑产生（可复跑：见文末）。
> 不是 e2e 的替代品，也不是 curl 级冒烟：每一步都是真实浏览器里的真实点击、真实后端、真实 SQLite。

## 环境

| 项 | 值 |
| --- | --- |
| 浏览器 | Microsoft Edge 157.0.4341.60（Chromium 内核，`playwright-core` 的 `channel: 'msedge'`，headless） |
| 后端 | `uvicorn app.main:app`（单 worker），`AUTH_MODE=enforced`、`AUTH_TEST_HOOKS=1`（**仅走查期**，取验证码答案）、`AGENT_MODE=mock`、`DATA_PROVIDERS=synthetic`；全新 SQLite（迁移到 `0008_account_recovery`），端口 `8100` |
| 前端 | `next build` 后 `next start --port 3100`（生产构建，不是 dev）；首屏 JS 209.0 kB ≤ 245 kB |
| 端口说明 | 开发机上 `8000` 已被另一个常驻 `uvicorn --reload` 占用（不是本次走查启动的），所以用 `8100/3100` 另起一套隔离栈和独立数据库，没有碰用户自己的库 |
| 主机写法 | `http://localhost:3100` 与 `http://127.0.0.1:3100` **各完整走一遍**（同一个库，先 `localhost` 后 `127.0.0.1`） |
| 视口 | `1440×900`、`1024×768`、`390×844`；另在 `360×800` 下量 `scrollWidth − innerWidth` |

## 结果

| 主机 | 通过 | 步骤数 | 控制台 error | 游客 `POST /reports` | 360 px 横向溢出 |
| --- | --- | --- | --- | --- | --- |
| `localhost` | ✅ | 29 | **0** | **0** | 无（全部截图点位 = 0） |
| `127.0.0.1` | ✅ | 29 | **0** | **0** | 无（全部截图点位 = 0） |

走查步骤（对应 §25.3.6 的 6 步）：

1. **游客**：首页 / 报告库 / 标的页可读；生成入口（按钮、热键 `G`、命令面板「生成」）都弹登录框，Network 里生成类 `POST` 为 0。
2. **注册**：取验证码（`AUTH_TEST_HOOKS` 答案端点）→ 注册 → 恢复码弹窗（`Esc` 不关闭、未勾选时「继续」为 `disabled`）→ 进入工作台；额度芯片 `3/3`；依次生成 3 个标的，芯片 `2/3 → 1/3 → 0/3`。
3. **额度耗尽**：对话框文案与重置时间正确；「申请额外额度」提交成功（后台「额度申请」出现 1 条待处理，概览红点同步）。
4. **找回**：登出后用恢复码（小写、空格分隔，验证输入遮罩的归一化）走 `/recover` → 展示**新**恢复码 → 跳登录页；旧密码失效、新密码可用；**旧恢复码再用被拒**（统一文案）。
5. **root**：`localhost` 一轮用初始口令登录并走强制改密（截图 `08-forced-password-change-*`），`127.0.0.1` 一轮直接用新密码登录；后台六页（概览 / 用户 / 邀请码 / 额度申请 / 审计 / 设置）逐页打开；用户抽屉；对走查用户做一次**批量赠送额度**；用户 CSV 导出（`200`，`X-Content-Type-Options: nosniff`，表头无任何凭据列）；概览的「安全事件（24 小时）」带着本次走查产生的真实数字（登录失败 / 找回失败 / 找回成功 …）。
6. **注销**：走查用户在账号页「安全」→ 危险区，输入密码 + 用户名确认 → 回首页；旧账号不能再登录。

## 截图矩阵

文件名 `<步骤>-<主机>-<视口>.png`，共 57 张（`08-forced-password-change` 只在第一个主机产生，因为 root 只会被强制改密一次）：

`01-guest-login-dialog` · `02-login` · `03-register` · `04-recovery-dialog` · `05-quota-exhausted` · `06-account` · `07-recover` · `08-forced-password-change` · `09-admin-overview` · `10-user-drawer`

## 走查中发现并已修复的问题（先红后绿）

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| `/admin/users` 在 360 px 下整页横向滚动 177 px | 表头里有 `sr-only`（`position:absolute`）的「操作」文字；`overflow-x-auto` 的包裹层不是定位祖先，绝对定位子元素**不被它裁剪**，把文档宽度撑大 | `UsersTable` 的滚动包裹层加 `relative`（`web/components/admin/UsersTable.tsx`） |
| `127.0.0.1` 一轮里游客访问标的页时控制台出现 1 条 `Minified React error #418`（水合文本不一致） | 「生成于 N 分钟前」是**相对时间**：服务端渲染与客户端水合若跨过一个分钟边界，文本不同。既有隐患（与认证无关），走查把它撞了出来 | 相对时间文本节点加 `suppressHydrationWarning`（`ReportPanel`、`ReportLibraryRow`、`RecentReports`、`HistoricalBanner`）；修复后两主机各整轮 0 个 error |
| 走查脚本第一版在 root 改密后又走一次登录页，被「已登录用户访问 `/login` 重定向」拦住 | 脚本假设错误（改密不会登出当前会话） | 脚本改为改密后直接进后台；产品行为无误 |

## 没有覆盖到的（诚实记录）

* `web/tests/e2e/auth.spec.ts` **没有**在 Playwright 自带的 Chromium 上实跑（本机没有，且下载被封）——它按设计在缺浏览器时 skip；本目录的走查是 AC-235 的证据，二者不互相替代。
* 走查只用 `AGENT_MODE=mock` 的示例生成，不涉及真实 `aragon` 引擎（那是迭代前的既有能力，不属于本轮）。
* 无障碍清单里「颜色对比 ≥ 4.5:1」「焦点环对比 ≥ 3:1」只做了肉眼核对（深色主题下的截图），没有跑自动化对比度工具。

## 复跑

```text
# 终端 A（api/）：AUTH_MODE=enforced AUTH_TEST_HOOKS=1 AGENT_MODE=mock DATA_PROVIDERS=synthetic API_TOKEN=<任意> \
#                ALLOW_ORIGINS=http://localhost:3100,http://127.0.0.1:3100 DATABASE_URL=sqlite:///<全新库> \
#                python -m alembic upgrade head && python -m uvicorn app.main:app --port 8100 --workers 1
# 终端 B（web/）：NEXT_PUBLIC_API_BASE=http://127.0.0.1:8100 npx next build && npx next start --port 3100
# 终端 C（仓库根）：NEXT_PUBLIC_API_BASE=http://127.0.0.1:8100 API_TOKEN=<同上> node scripts/seed.mjs
# 终端 D（本目录）：node walkthrough.mjs localhost 3100 8100 && node walkthrough.mjs 127.0.0.1 3100 8100
```

`AUTH_TEST_HOOKS=1` 会交出验证码答案，**只能在走查 / e2e 的临时栈上开，生产永远不要开**。
