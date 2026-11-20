# 24. Iteration 8 设计：用户与权限系统（X1–X8）——登录 / 注册验证码 / 根管理员 / 角色权限 / 生成额度 / 管理后台 / 游客只读

> **本文档在全项目 spec 中的位置**：`docs/plans/investment-research-report/spec.md`（主 spec，§1–§22）与
> `docs/plans/report-library/spec.md`（§23，M38–M44 / U1–U7 / AC-137–AC-152）是既有真源。
> 本文档是 **§24 增量**：只追加，不改它们一行。§23 的 V 层写过一句"本轮明确不做：多用户/权限"，
> **本轮正是把这一句翻过来**——所以凡是 §1–§23 里隐含"全站只有一个人在用"的地方（自选、定时刷新、成本预算、追问、
> 报告管理、`API_TOKEN` 整站闸门），本文档都逐条给出新语义（见 §24.3.9 与 附录 A 的**逐路由策略表**）。
> 下游实现节点请把本文档当施工图，把主 spec 当字典（既有错误码、既有 AC、既有契约在那边）。

| 项 | 值 |
| --- | --- |
| 需求原文 | 给项目加一套用户与权限系统：**登录 / 注册（注册要验证码）**；管理员有管理界面；**根管理员 `jdqqjr` / `13519126390`**，其余管理员由根管理员分配；**普通用户生成投研有次数限制**；界面与现有项目统一；**游客可看投研、不能生成**；"我没考虑到的相关功能你来完善" |
| 已有能力（不可回退） | 三栏工作台、结构化报告、报告库（§23）、追问 cite/deep、全文检索、定时刷新、成本面板、引擎自检、SSE 进度流、离线只读 SW、首屏 JS ≤ 240 kB gzip（AC-35）、972 条后端 + 260 条前端既有用例全绿 |
| 本轮要做 | X1 账号与会话　X2 注册验证码与反滥用　X3 角色/权限（RBAC）与**默认拒绝**的路由策略表　X4 生成额度引擎（预留/退还/赠送/申请）　X5 管理员后台（用户/邀请/额度申请/审计/设置/概览）　X6 前端接入（游客态、登录/注册、账号页、额度芯片、按权限裁剪的入口）　X7 既有单租户能力的多用户改造（自选、定时刷新、成本、追问、报告管理、SW 缓存隔离）　X8 根管理员引导、离线恢复 CLI 与上线开关 |
| 本轮明确不做 | 邮箱/短信验证与找回密码邮件（无外部通道；改走"管理员重置 + 离线 CLI"）；第三方 OAuth / SSO；多因素认证（TOTP）——只在 §24.9 留扩展点；按用户私有的报告（报告库仍是**全站共享**的公共资料）；按用户的收藏（`pinned` 仍是全局旗标，仅限拥有者/管理员改）；计费/支付；多租户/组织 |
| 交付物 | `api/` + `web/` 代码；迁移 `0007_users_auth`；`contracts/permissions.json`；`AC-153`~`AC-208`；`M45`~`M52` |
| **迭代 2（v4）** | 见文末 **§25**：Y1–Y6（交付卫生与数据保留 / 遗留风险清零 / 恢复码找回密码 / 自助注销 / 管理效率 / 真实浏览器走查），迁移 `0008_account_recovery`，`AC-209`~`AC-251`（v3 为 209–238，评审追加 239–251），`M53`~`M58`。§24 一字未改 |
| **文档版本** | **v4**（§25 迭代 2：v3 = 设计节点稿；**v4 = 评审节点修订稿**，2026-11-20：9 条 P1 + 8 条 P2 已就地改入 §25 正文，见下方"评审记录（迭代 2）"）；§24 本身仍为 **v2**（v1 = 设计节点原始稿；v2 = 评审节点修订稿：2 条 P0 + 9 条 P1 已就地改入正文，P2 见评审记录；AC 由 153–205 增至 153–208） |

---

## 评审记录（迭代 2：§25 / v3 → v4）

> **评审口径**：逐条对照**当前工作树**取证后再下结论（不是对照 §25.0 的转述）。取证对象：`services/session_service.py`（全文）、`services/audit_service.py`（`ACTIONS`、`record_login_failure`）、`services/auth_service.py`（`login/register/issue_challenge`）、`services/challenge.py` + `services/captcha.py`（`Purpose`、`verify`）、`routers/auth.py`、`services/admin_user_service.py`（`quota_lite/set_quota/delete_user/create_user`）、`services/quota_service.py`（`grant`、`GRANT_MIN/MAX`）、`services/user_service.py`（`anonymize`）、`security/objects.py`（`assert_can_manage_user`、`SELF_FORBIDDEN`）、`services/report_service.py`（`set_flags/soft_delete`）、`services/settings_service.py`（`validate_field/get_all`）、`errors.py`（`RootProtected`、`Exception` 处理器）、`main.py`（中间件、`_sweep_loop`）、`logging.py`、`deploy/nginx/aragon-trading.conf`、`web/lib/{api,authErrors}.ts`、`web/components/auth/useCaptcha.ts`、`git status`。严重度同迭代 1：**P0** 上线即事故；**P1** AC 不可达 / 越权 / 数据错误 / 实现者必然踩坑；**P2** 加固或可读性。**本轮无 P0**（恢复码的熵、哈希、轮换、顺序性质与"不自动登录"均成立）。

| ID | 级别 | 位置 | 问题（已核实的证据） | 处置（v4 落点） |
| --- | --- | --- | --- | --- |
| RV-Y1 | **P1** | §25.3.3 步骤 3、§25.4 | v3 说 `purpose="recover"` 的验证码"永远要求"，但**签发口不认这个用途**：`services/challenge.py:14` `Purpose = Literal["register","login"]`；`routers/auth.py` 的 `GET /auth/captcha` 参数是 `Literal["register","login"]`（传 `recover` ⇒ `422`）；前端 `api.ts:474` 与 `useCaptcha.ts:26` 同样只接受两值。**恢复页因此拿不到验证码，整个 Y3 不可达**，而 v3 的文件清单一个都没列 | `Purpose` 加 `"recover"`；`captcha` 路由 Literal、`api.ts`、`useCaptcha` 同步；§25.4 补 4 行；`captcha.verify` 的"过快"陷阱仍只对 `register`；用途绑定（`entry.purpose != purpose`）天然保证"recover 验证码不能拿去登录/注册"；新增 **AC-239** |
| RV-Y2 | **P1** | §25.3.4、§25.5.1、AC-223/225/227/228、D-12 | ①`assert_can_manage_user(actor, target, "delete")` 对**自己**一律 `SelfActionForbidden(422)`（`objects.py:138`，`SELF_FORBIDDEN` 含 `delete`）。v3 让 `account_lifecycle.delete_account(via="self")` 与管理员路径"共用"，若原样复用矩阵，**自助注销永远 422**；②v3 给 root 的拒绝是 `403 forbidden{reason:"root_protected"}`，但 `root_protected` **早已是一个独立错误码**（`errors.py:505 RootProtected`，`authErrors.ts`/`copy` 均已登记），第二种表达会让前端出现两套分支 | `delete_account` 拆成"**授权**（调用方做）+ **副作用**（唯一实现）"：自助路径用专用的 `assert_can_self_delete(principal, user)`（root → `RootProtected(action="self_delete")` = `403 root_protected`；`kind!="user"` → `Forbidden(permission="account.self")`），管理员路径照旧过矩阵（admin 删自己仍是既有的 `422 self_action_forbidden`，AC-197 不变）；所有"root 被拒"一律用既有码 `root_protected`；AC-223/227 改写 |
| RV-Y3 | **P1** | §25.3.3 步骤 6 | `recover` 的"校验码 → 设新密码 → 轮换码"之间没有比较-交换。用户**双击/超时重试**时两个请求都通过校验，各自生成一把新码，后写者覆盖前写者——**用户屏幕上保存的那把码可能不是库里的那把**，下一次真要找回时 `recovery_invalid`，而且没有任何报错能事先提醒他 | 轮换用 CAS：`UPDATE users SET recovery_hash=:new … WHERE id=:id AND recovery_hash=:old`，`rowcount != 1` ⇒ 整个事务回滚并按 `401 recovery_invalid`（与其它失败同形）；前端提交即 `disabled` 并单飞；新增 **AC-240** |
| RV-Y4 | **P1** | §25.3.5(c)、AC-233、`audit_service.py:73-146` | `auth.login.failure` 是**合并写**：同 `(username, ip)` 60 s 内只一行，`detail.count` 递增（`FAILURE_MERGE_WINDOW`）。v3 对它做 `GROUP BY action` 的**行数**统计 ⇒ 一次 50 次的暴力尝试显示为"1"，AC-233"制造 N 次失败数字对上"对 N>1 **必然失败**，概览还会系统性低估攻击强度 | `loginFailures` 定义为**尝试次数** = `Σ detail.count`（只扫 24 h 内该动作的行，`LIMIT 5001`，超出则 `loginFailuresCapped:true`）；同时给出 `loginFailureBursts`（行数）；AC-233 改写 |
| RV-Y5 | **P1** | §25.3.3、§25.3.5(c)、`audit_service.ACTIONS` | 恢复入口是一个**新的凭据猜测面**，登录失败有审计 + 概览，恢复失败却**完全不可见**：攻击者可在限速内持续试错，管理员没有任何信号（v3 的"异常可见"缓解只覆盖成功路径） | 新增动作 `auth.recovery.failure`（按 `(username, ip)` 60 s 合并，复用 `record_login_failure` 的合并逻辑，参数化动作名）；`ACTIONS` **31 → 36**（v3 写的是 35）；`security24h.recoveryFailures`；审计只写**声称的用户名**（与登录失败一致，不区分存在与否）；新增 **AC-241** |
| RV-Y6 | **P1** | §25.3.5(b)、AC-232 | ①v3 的 `grant: -100..100`；而 `quota_service.GRANT_MIN, GRANT_MAX = -50, 50`（`quota_service.py:63`），`grant()` 对越界抛 `ValueError` ⇒ **500**，AC-232 的"100 个目标一次成功"在 `grant=100` 时直接崩；②`grant()` 对负数是**夹紧到 bonus 余额**，夹成 0 时不写账本也不审计——"每人恰一条账本行 + 一条 `quota.grant`"对"收回"**不成立**；③v3 没说**未知/重复/已注销**的 `userIds` 怎么办（`set_quota` 对已注销会在**第二遍中途**抛 `NotFound`，靠回滚才"全败"，且 404/403 的优先级未定义）；④`set_quota` 写的审计 `detail` 没有 `bulk/batch` | 范围改为 `-50..50`（复用 `GRANT_MIN/MAX`，越界 `422`）；第 1 遍**先**去重、批量 `SELECT … IN` 取人（未知或已注销 ⇒ 整体 `404 not_found`，什么都不写），**再**过管理矩阵；`applied` = 实际写入账本的人数（收回被夹成 0 者不计），响应追加 `skipped`；`set_quota` 增关键字参数 `audit_extra`；AC-232 改写 |
| RV-Y7 | **P1** | §25.3.1(c)、AC-212 | ①nginx 仓库文件**只有一个 `server { listen 80; … }` 块**（注释写明"`certbot --nginx` 在此插入 ACME 与 301"）——**没有**"443 server 块"可加；certbot 会把 443 监听直接并入这个块、另建一个只含 301 的 80 块。HSTS 头在明文 HTTP 上被浏览器忽略（RFC 6797 §8.1），所以正确位置就是**这个 server 级**；v3 的"HTTP 301 块不含"断言对仓库文件**无对象可断言**；②nginx 的 `add_header` **不继承**：`/_next/static/` 与 `= /sw.js` 两个 location 自带 `add_header`，会**整体覆盖** server 级的头——对静态资源无害，但这是实现者必踩的坑；③`X-Content-Type-Options` 挂在 `_observability_and_security` 中间件上，而 `Exception` 兜底处理器（`errors.py:625`）由 Starlette 最外层 `ServerErrorMiddleware` 调用，**在用户中间件之外** ⇒ **500 响应不会带该头**，AC-212 的"含 500"不可达 | HSTS 加在 server 级并写注释（`certbot` 之后自然落入 443 块）；AC-212 改为静态断言"server 级含 HSTS、`/api/` location 未覆盖、无 `includeSubDomains/preload`"，并注明两个 `add_header` location 不继承；500 的头改由 `_unhandled_handler` 直接写入；新增 **AC-242** |
| RV-Y8 | **P1** | §25.3.3、§25.4.1 `settings_service` 行、§25.5.2 | `validate_field` 只认 `registrationMode`（字符串枚举）与 `INT_FIELDS`（严格 int，且**明确拒绝 bool**，`settings_service.py:142`）；`get_all/update` 同构。v3 一句"新设置 `auth.recovery_enabled`（bool）"没有任何落点：直接塞进去会被 `invalid_setting` 拒掉，或被当成 int 吞掉 `true`。另外 `get_str` 的缺省来自**静态** `DEFAULTS`，而 v3 要求"缺省取 `AUTH_RECOVERY_ENABLED`" | 新增 `BOOL_FIELDS`（严格 `isinstance(value,bool)`，存 `"1"/"0"`），`get_all/update/validate_field` 同步，审计 `from/to` 用布尔；缺省 = `get_settings().auth_recovery_enabled`（运行期读 env，不进静态 `DEFAULTS`）；`schemas/admin.py` 的 Settings 读/写模型追加；新增 **AC-243** |
| RV-Y9 | **P1** | §25.3.5(a)、AC-229 | 流式导出的取数方式未规定：`Depends(get_db)` 的会话是**请求作用域**（`session.py:65`），生成器里继续用它依赖框架的退出时序；`quota_lite` 对一页用户做 `IN (…)`，v3 的块大小 1000 超过旧版 SQLite 的 999 变量上限；审计行（`admin.export`）与限速如果在流开始**之后**才写，导出失败也会漏记；`COUNT` 与读取之间新增的行会让"头里说没截断、实际截断"不一致 | 生成器**自己**开 `SessionLocal()`、每块一个短会话；块大小 **500**；`admin.export` 审计与限速在**第一个字节之前**提交（记 `rows` 为预估上限，结束后不回写）；截断判定 = `COUNT > 50000` 时预置头，读取处**硬上限**同样 50 000，二者独立成立；用户导出的 `usedToday/bonus` 复用 `quota_lite`（每块一次 `GROUP BY`），**禁止**逐行查询 |
| RV-Y10 | P2 | §25.3.2(a)、R-Y9 | v3 引用的"`latest_ready_non_archived_version`"在代码里**不存在**：后继者选择是 `report_service.soft_delete` 里的内联查询（条件 = 同标的、未删、`archived=0`、`status != 'provisional'`、`version` 降序取第一，**没有** `status='ready'`）；`set_flags` 自己 `commit`，且 `archived=True` 当前把 `is_latest=False` + `status="superseded"` 后**不交接**（Q-9 的根因，已核实）。交接时若先写后继者再清自己，会撞 `ux_reports_latest`（需先 `flush` 降级） | 抽出 `_pick_successor(session, report)`（**原样**搬运上述条件），`soft_delete` 与 `set_flags` 共用；`only_version` 判定必须在**任何字段被改之前**；降级 `flush` 先于升级；AC-213 增"同一后继者"断言（R-Y9 已要求） |
| RV-Y11 | P2 | §25.3.3 步骤 2、R-Y2 | `auth.recover.user`（`username_lc`，5 次/小时）在**验证码之前**就计数：任何人不用解一次验证码就能把**任意**用户名的找回通道封一小时（DoS 受害者的找回）；登录侧用的是"`peek` 检查 + 失败后 `record`"（`auth_service.login`） | 步骤 2 只 `peek`；**通过验证码后**才 `record`（攻击者每封一次要先解一张验证码）；对不存在的用户名同样 `record`（RV-3 同构）；AC-220 的计数口径同步 |
| RV-Y12 | P2 | §25.3.3、AC-217 | 日志脱敏器（`logging.py:44,55`）的键名表里有 `password/newPassword/currentPassword/token/secret…`，**没有** `recoveryKey`。当前没有路径会把 body 打进日志，但任何一次 `logger.exception`/调试日志打印请求体都会泄露明文；§25.4.1 又没列 `logging.py` | `logging.py` 两处正则加 `recovery[_-]?key`；`audit_service._SENSITIVE_KEY` 加 `recovery`（v3 已写）；单测：`redact('recoveryKey=ABCDE-…')` 被打码 |
| RV-Y13 | P2 | §25.5.1 | ①`register` 现在 `response_model=MeOut`；"201 体 + `recoveryKey`"若直接给 `MeOut` 加字段，`login/me` 也会带一个恒为 `null` 的 `recoveryKey`，把"明文只出现一次"的契约搅浑；②`recover` 的请求模型若给 `recoveryKey` 加格式正则，格式不对会走 `422 validation_error` 而不是 `401 recovery_invalid` | 新增 `RegisterOut(MeOut)`（只有它带 `recoveryKey`）；`RecoverRequest.recoveryKey` 仅 `max_length=128`，**不**做格式校验（格式错误与码错同属 `401`） |
| RV-Y14 | P2 | §25.3.1(b)、R-Y7 | `_sweep_loop` 里的清扫（`sweep_stale_runs`、`_purge_audit_if_due`…）是**在事件循环线程上同步执行**（`main.py:125-150`）。v3 R-Y7 把 `purge_dead` 描述为"与请求线程竞争 SQLite 短写"——实际是它**直接阻塞事件循环**直到 5 批删完 | 清扫调用用 `await asyncio.to_thread(session_service.purge_dead_once)`（独立 `SessionLocal()`）；批量上限不变；R-Y7 改写 |
| RV-Y15 | P2 | §25.3.1(a)、AC-211、§25.8 #5 | 迭代 1 的图（见黑板 F-REVIEW*）是"设计 → 评审 → 实现 → 终审+提交"，v3 把"提交"写成"评审节点"的职责，但**本设计评审节点被明令不得提交**，实现节点也不应提交。"谁提交"不明会导致要么没人提交、要么两处各提一次 | 明确：**M58 的一次提交由本轮最后的"终审 + 提交"节点执行**；设计评审与实现节点**不提交**；AC-211 的 `git status` 干净以提交完成后为准 |
| RV-Y16 | P2 | §25.8 #3 | "`service` 对 `recover` 恒 403/401"：`recover` 是**公开**路由，不靠主体；`service` 只对 `recovery-key/account`（`_require_user` ⇒ `Forbidden(permission="account.self")`）受限 | 文字改正；矩阵测试按"公开路由不看主体"写 |
| RV-Y17 | P2 | §25.3.3 前端 | `POST /auth/recovery-key`（登录态轮换）同样有"双击 ⇒ 后写者生效、屏幕上是先返回的那把"的风险，且无法用 CAS（它不需要旧码） | 前端单飞（提交即 `disabled`，忽略重复点击）；服务端对该路由按用户 `Limit(60, 3)` 限速（新 scope `auth.recovery_rotate`）；AC-222 补充 |

**已核实、无需改动的事项**（评审同样要给出"为什么放行"）：

* **E-17 的数字属实**：`git status --short` = **165** 行；`api/pytest-out.txt`、`scripts/_patch_tmp.py`、`docs/plans/report-library/probe_db.py` 三个游离文件均**存在**；`web/npm-install.log`、`playwright-report/`、`test-results/` 已被忽略。
* **E-16 属实**：`session_service` 的函数表（`create/resolve/revoke/revoke_by_token/revoke_all_for_user/get_owned/list_live` + 私有 `_touch/_expiries`）里确无 `DELETE`；`purge_dead` 的两条谓词成立——`_expiries()` 返回 `min(idle, absolute)`、`_touch()` 同样 `min(moment+window, absolute)`，所以 `idle_expires_at ≤ expires_at` 恒成立，"空闲过期"子查询已覆盖绝对过期；`sessions` 无子表，`revoked_reason` 列宽 24，`"password_recover"`（16）放得下。
* **E-20 的 Q-9 根因属实**（`set_flags(archived=True)` 清 `is_latest` 不交接）；A-18 的那一行（`must_change_password=temporary is not None`）属实；F-23 的 22 个 `admin*` 方法在 `api.ts` 内属实。
* **E-23 的复用件都在**：`RATE_LIMITS` 的 scope→`Limit`、`ratelimit.peek/hit/record`、`audit_service.ACTIONS`（**31** 个，`test_audit.py:230` 断言 `== 31`，须同步改为 36）、`sanitize_detail`、`ConfirmDialog`、Radix `Dialog`（`onEscapeKeyDown` 可拦）、`AUTH_TEST_HOOKS` 答案端点（`routers/auth.py:249`）、`web/scripts/check-bundle.mjs`。
* **恢复码本身的安全设计成立**：100 bit 熵配 `sha256` 无需慢哈希；`hmac.compare_digest`；`recover` 成功后吊销全部会话并**不**自动登录；`root` 排除；"新密码策略先于码校验且不消耗码"的顺序理由成立（策略只依赖声称的用户名）。
* **0008 迁移**：只 `add_column`/`create_index`，部分索引双写 `sqlite_where/postgresql_where`，与 0006/0007 纪律一致；`ix_sessions_revoked` 的子查询谓词含 `revoked_at IS NOT NULL`，SQLite 部分索引可被选中。
* **`/auth` 前缀已是 `private, no-store` 家族**（`main.py:85`），恢复码响应无需另设缓存头；`admin` 前缀同理，CSV 同样覆盖。

---

## 评审记录（迭代 1：§24 / v1 → v2）

> **评审口径**：逐节对照既有代码取证后再下结论。取证对象：`routers/reports.py:132-170`、`routers/asks.py:59-175`、`main.py:274-301`、`run_manager.py:267-380`、`db/session.py`、`sse_token.py:35-56`、`models/run.py` 状态集、`schemas/report.py:439`（`ReportPatch`）、`web/lib/api.ts:79-135`、`web/lib/sse.ts`、`web/public/sw.js`、`web/next.config.ts`、`web/tsconfig.json`、`deploy/nginx/aragon-trading.conf`；并用 `create_app()` **枚举真实路由表**（46 条业务路由 + `/` + 4 条 FastAPI 文档路由）与附录 A 逐条比对。严重度：**P0** = 上线即安全/可用性事故；**P1** = 会导致 AC 不可达、并发/数据错误或明显越权；**P2** = 加固或可读性。

| ID | 级别 | 位置 | 问题（已核实的证据） | 处置（v2 落点） |
| --- | --- | --- | --- | --- |
| RV-1 | **P0** | §24.0 D-7、§24.3.3、§24.3.4、§24.4.2 | `web/lib/api.ts:81,94` 读 `NEXT_PUBLIC_API_TOKEN`，并给**每个**浏览器请求加 `Authorization: Bearer`；`NEXT_PUBLIC_*` 在构建期内联进**公开 JS**。v1 把"带正确 Bearer"定义为 `service`（≈admin）主体，且解析顺序排在 Cookie 之前 ⇒ 只要部署者按旧 README 设了该变量：①任何访客从 JS 抄出令牌即是管理员；②所有浏览器请求都被它"盖过"，登录形同虚设。v1 完全没提这个变量 | 新增事实 **E-15**；D-7 改写；`api.ts` **删除**令牌读取与 `authHeaders()`；`service` 权限收窄（§24.3.4）；构建期/测试期守卫；新增 **AC-206** |
| RV-2 | **P0** | §24.3.2(b) CSRF | "Origin 不在白名单**且不等于请求自身 origin** → 403"在生产**不可实现**：nginx→uvicorn 是明文 http（`request.url.scheme=http`），浏览器 `Origin=https://aragontrading.online`；而生产 `ALLOW_ORIGINS` 为空 ⇒ **所有**生产写请求（含登录、注册）一律 `403 csrf_rejected` | 改为**只比 netloc**：`Host`（nginx 已 `proxy_set_header Host $host`，已核）或 `TRUST_PROXY=1` 时的 `X-Forwarded-Host`，忽略 scheme；新增 **AC-208** |
| RV-3 | P1 | §24.3.7、AC-169/170 | 失败计数与锁定只存在于 `users` 行，**不存在的用户名永远得 401** ⇒ 连错 5 次后，存在的账号变 `423`/要验证码、不存在的仍 `401`，AC-170 要求的"响应完全一致"被自己破坏，枚举口重开 | 增**影子节流器**：对不存在的用户名在进程内按同阈值计数并返回同形状响应；AC-169/170 改写 |
| RV-4 | P1 | §24.3.3 启动期集合校验、附录 A | ①豁免清单漏 `/docs/oauth2-redirect`（实测存在）⇒ 进程**起不来**；②`app.routes` 的 `path_format` 含 `/api/v1` 前缀，附录 A 省略前缀 ⇒ 键永远不相等 | 豁免规则改为"不在 `/api/v1` 下的路由"；`POLICIES` 键**带前缀**，附录 A 由 `_api()` 助手在加载时统一补前缀 |
| RV-5 | P1 | §24.3.9(h)、附录 A、AC-179 | 追问 `…/events` 既要"自带 `?token=` 签名"又要"拥有者或管理员，否则 404"。但 `EventSource` 不能带 `Authorization`，开发期跨源也不带 Cookie（§24.3.2(e) 自己写的）⇒ **拥有者本人开发期也会 404**；两处规则互相打架 | 对象级校验**前移到 `events-token` 签发处**；`events` 只验令牌（令牌绑 ask_id，60 s）；AC-179 改写 |
| RV-6 | P1 | §24.3.2(c)、§24.3.9(g) | `api.capabilities(revalidate=30)`、`probeStatus(revalidate)` 走 **Next Data Cache**（`api.ts:134,181,261`）。`Capabilities` 现在随主体而异（脱敏），缓存若按 URL 复用，会把 admin 的全量响应回放给游客页面（或反之）——脱敏被缓存层绕过 | `createApi(ctx)` 的**服务端**实例一律 `cache:'no-store'` 并忽略 `revalidate`；加 vitest 断言 |
| RV-7 | P1 | §24.3.5、§24.3.7 | 免认证入口 `/auth/login`/`register` 每次 scrypt ≈ 32 MiB + 60–100 ms，线程池默认 40 线程 ⇒ 并发 40 个请求瞬时 ≈ 1.3 GB；单 IP 10/min 挡不住分布式 | 全局 `BoundedSemaphore(4)`（排队而非并发）＋全站登录总限速；超时 `503 busy`（不泄露） |
| RV-8 | P1 | §24.5.3 `GET /admin/users`、AC-195 | `username_lc LIKE 'q%'` 在 SQLite 默认配置（LIKE 大小写不敏感 + BINARY 排序规则的索引）下**不会走索引**；AC-195 要求 `EXPLAIN QUERY PLAN` 含索引 ⇒ 按原文**不可达**；且 `q` 里的 `%`/`_` 未转义 | 改为范围查询 `username_lc >= :q AND username_lc < :q_next`（BINARY 索引可用，`q` 已 `lower()`） |
| RV-9 | P1 | §24.3.8(b)、§24.3.9(a)、B-2 | 锁契约自相矛盾：(b) 第 5 步在 INSERT 后释放锁，而 `Run` 的 `commit` 在 `start()` 末尾（`run_manager.py` 的 `session.commit()`）⇒ 锁早于提交释放，后一线程读不到未提交的扣减 ⇒ **超发**；(b) 又要求"锁内无 I/O"，而 `mkdir` 恰在预留与写 Run 之间。另外 `create_report`/`ask_report` 是 `async def` 并**同步**调用 `start()`（`reports.py:133,162`、`asks.py:59`），事件循环线程会被锁等待阻塞 | 契约改为 `with quota_service.reservation(...)` **覆盖到 commit/rollback**；明确允许锁内**一次**本地 `mkdir`；说明 async 路由在事件循环线程上天然串行，锁主要保护线程池路径（深度追问、后续同步化）与测试的 20 线程用例 |
| RV-10 | P1 | §24.3.9(c)、附录 A | `ReportPatch` 有 `pinned / archived / setLatest` 三个**全站可见**的旗标（`schemas/report.py:439`）。v1 让 `report.manage_own` 对自己的报告都能设 ⇒ 任一注册用户可把自己的版本**设为全站该标的"最新"/置顶**，覆盖管理员的版本 | `pinned`、`setLatest` 改为需 `report.manage_any`；用户仅可 `archived`/删除/恢复自己的 |
| RV-11 | P1 | §24.3.8、§24.6.3、§24.5.3 | 额度只有"每用户"口径，而注册默认 `open`、验证码可被 OCR（R-A8 自认）、每 IP 每 10 分钟可注 5 号 ⇒ 注册农场能在月度预算 402 触发**之前**烧掉真金白银，并让正常用户整月吃 402 | 新增**站点日总量上限** `quota.site_daily_cap`（默认 60，`0` = 不限；仅统计受额度约束的用户）；设置页可调；新增 **AC-207** |
| RV-12 | P2 | §24.3.10、`main.py:277` | `X-Request-Id` 由客户端原样透传（既有行为），现在会落进 `audit_log.request_id VARCHAR(16)` 与日志：超长（PG 报错）、含换行（日志注入） | 中间件把入站值校验为 `^[A-Za-z0-9_-]{1,32}$` 否则丢弃重生成；落审计时截 16 |
| RV-13 | P2 | §24.3.7 限速表 | 限速键用 /24（IPv4）与 /48（IPv6）：办公室/校园 NAT 下 10 人共用 10/min 登录额度；/48 又让攻击者有 2¹⁶ 个 /64 可轮换 | **存储**仍截断（隐私），**限速键**改为 IPv4 精确地址 / IPv6 `/64` |
| RV-14 | P2 | §24.3.7 客户端 IP | `TRUST_PROXY` 默认 `0`：若部署者忘记设，nginx 后所有访客都是 `127.0.0.1`，共用同一组限速桶 ⇒ 第 11 个人登录就被限 | 启动后**首次**见到 `X-Forwarded-For` 而 `TRUST_PROXY=0` 时打一条 WARNING（只打一次） |
| RV-15 | P2 | §24.3.9(d) | `GET /watchlist` 带写副作用（首次播种）：并发两次 GET 会双写 | 播种用 `INSERT … ON CONFLICT DO NOTHING` + 先置 `settings` 旗标；响应 `Cache-Control: private, no-store` |
| RV-16 | P2 | §24.3.4 TS 侧 | `web/lib/permissions.ts` 直接 import 仓库根 `contracts/permissions.json`（`web/` 之外）；`next.config.ts` 钉了 `outputFileTracingRoot=cwd`，`tsc` 能过但 `next build` 的越界解析未实测 | M45 出口准则加一条"`next build` 实测通过"；失败则退为 `scripts/sync-permissions.mjs` 复制进 `web/lib/` + vitest 断言字节相等 |
| RV-17 | P2 | §24.3.3 `service` | 同 RV-1：`service` 权限在 v1 里仍含 `admin.users.manage` 等"用户行写操作"，与 D-7 正文"− 与用户行相关的写操作"**不一致** | 并入 RV-1：`service.minus` 补齐 |

**已核实、无需改动的事项**（评审同样要给出"为什么放行"）：

* 附录 A 与真实路由表**逐条一致**：46 条业务路由无遗漏、无多余（`POST /runs` 恒 405 的占位路由也在表内）。
* `request.scope["route"]` 在本仓库的 FastAPI 0.136.1 / Starlette 1.0.0 里由 `APIRoute.matches` 写入（已读源码），`path_format` 取模板成立。
* 三个页面 `app/page.tsx`、`reports/page.tsx`、`[symbol]/page.tsx` **早已** `force-dynamic`，引入 `cookies()` 不会让任何静态页变动态。
* 引擎层 `sessionmaker` 默认 pysqlite 行为：`SELECT` 不开显式事务，所以"锁内重算余额"读到的是最新已提交数据（RV-9 的问题只在于锁释放过早，不在隔离级别）。
* `runs.status` 终态集 `succeeded/failed/cancelled/timeout/budget_exceeded` 与 §24.3.8(d) 的退款集合一一对应；`ux_runs_active` 冲突路径的 `session.rollback()` 会连带回滚同事务的账本行（原子性成立）。
* nginx 已 `proxy_set_header Host/X-Real-IP/X-Forwarded-For/X-Forwarded-Proto`，两个 `location` 都有；Set-Cookie 默认透传；`/docs`、`/openapi.json` 在生产**不被 nginx 转发**（只转发 `/api/`），无需另行关闭。
* 迁移纪律（只 `add_column`、不 batch、`downgrade` 先删索引再删列）与 0006 一致。

---

## 24.0 已核实的事实与关键假设（实现者先读这一节）

### A. 对既有代码的取证（全部在本机读过，行号以 2026-11-20 的 master `00db6a9` 为准）

| # | 事实 | 出处 | 对本设计的约束 |
| --- | --- | --- | --- |
| E-1 | **整站只有一道闸门**：`API_TOKEN` 非空时，中间件要求**所有**非公开路径带 `Authorization: Bearer <token>`；公开的只有 `/health`、`/docs`、`/openapi.json`、`/redoc`，SSE `…/events?token=` 走自带签名 | `api/app/main.py:62-63, 271-283, 316-324` | 这个闸门是"二元的"（有钥匙 = 全权），与"游客只读 / 用户限额 / 管理员"三层语义不兼容 → 被**策略表**取代（§24.3.3）；`API_TOKEN` 降级为"服务主体"凭据 |
| E-2 | CORS 是 `allow_credentials=False`，且**刻意放在鉴权中间件之后**（让 401 带 CORS 头）；`ALLOW_ORIGINS` 显式白名单，空表 = 仅同源 | `api/app/main.py:288-301` | Cookie 会话需要 `allow_credentials=True`（白名单本来就是显式的，满足"不能与 `*` 同用"）；顺序**不得改** |
| E-3 | Web 以 **跨源** 直连 API（默认 `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000`，页面在 `localhost:3000`）；生产由 nginx 同源 `location /api/`；RSC 首屏用 `INTERNAL_API_BASE` 服务端直取 | `web/lib/api.ts:79-87`、`deploy/nginx/aragon-trading.conf:20-45`、`web/next.config.ts` | ★**Cookie 的主机维度陷阱**：`localhost` 与 `127.0.0.1` 是两个不同的 Cookie 主机。开发期必须用**回环别名对齐**（§24.3.2(d)）；RSC 服务端 fetch 不带浏览器 Cookie，必须**显式转发**（§24.3.2(c)） |
| E-4 | 生成唯一入口是 `POST /instruments/{id}/reports`（`routers/reports.py:132`），其内 `manager.start()` 先后过 **409 `run_conflict` → 429 `too_many_runs` → 402 `monthly_budget_exceeded` → 422 `base_report_mismatch`** 四道闸（`run_manager.py:269-370`）；`scheduler_ticker` 也直接调 `manager.start(session, instrument, reason="scheduled")`（`scheduler_ticker.py:98`） | 如左 | 额度闸**必须落在 `manager.start()` 内**（否则定时刷新会绕过或误扣）；并用参数 `actor` 区分"用户触发 / 系统触发"（§24.3.9(a)） |
| E-5 | run 的终态写入点有三处：成功 `run_manager.py:753`；失败/取消/预算 `_finalize_failure` `:977-1047`；重启恢复与活性清扫 `:1158-1190` | 如左 | **不在三处各插一行退款**（会漏、会重复）；改为**幂等对账函数** `reconcile_refunds()` + 账本部分唯一索引（§24.3.8(d)） |
| E-6 | `watchlist` 表主键是 `instrument_id` 单列（全站一份）；`schedules` 有"每标的至多一条启用"的唯一索引；`settings` 是 `(key, value)` KV 表；`cost_ledger` 无用户列 | `models/watchlist.py`、`models/schedule.py`、`0003_cost_ledger.py` | 自选需新表（改主键要重建表，违背既有迁移纪律）；`settings` KV 直接复用为"后台设置"的存储；`cost_ledger` 加可空 `user_id` |
| E-7 | 迁移纪律（0003–0006 反复强调）：**只 `add_column` + `create_index`，不 batch 重建；新列可空且无默认值；不回填**；`downgrade()` 必须可回滚 | `0006_report_library.py` 文档串 | `0007` 同纪律：旧行的 `created_by` 为 NULL，语义是"系统 / 历史数据"（不是"某个人"） |
| E-8 | 单 worker 是**硬不变量**（`assert_single_worker()`），run 编排/订阅队列都在进程内 | `main.py:67-86`、README | 验证码仓库、限流桶、额度的每用户互斥锁**可以放进程内**（并在文档里写明依赖这条不变量）；但账号锁定计数、额度账本、会话**必须落库**（重启不能清零） |
| E-9 | 错误统一信封 `{"error":{code,message,requestId,detail}}`；`AppError` 子类各带 code/status；`Unauthorized(401, code="unauthorized")` 已存在 | `errors.py:22-52, 219-222` | 新错误码全部走这套（§24.5.4）；`unauthorized` 保留为"未登录/会话失效"的唯一 401 码，用 `detail.reason` 细分 |
| E-10 | 前端设计语言：Supabase 控制台式暗色画布 + 绿色 `--accent`、`--radius-sm/md/lg`、13px 基准、`Button/Input/Dialog/Tabs/Toast/Badge/Skeleton` 原语齐全；所有文案集中在 `web/lib/copy.ts`；浮层一律 `next/dynamic` 懒加载以守住首屏 240 kB | `globals.css:1-70`、`components/ui/*`、`AppShell.tsx:46-96` | 登录/注册/后台**复用这些原语，不引新 UI 库**；新组件除 `UserMenu`/`QuotaChip` 外全部懒加载；后台是独立路由，不计入工作台首屏预算 |
| E-11 | 离线 SW 缓存**同源导航文档（含整份报告 HTML）**，network-first、失败回缓存，缓存名 `aragon-offline-v1` | `web/public/sw.js:1-60`、`web/lib/offline.ts` | 页面一旦含"当前用户是谁"，SW 就会把**某个用户的 HTML 缓存给下一个用同一浏览器的人**——必须隔离（§24.3.12，★本设计最容易漏的一条） |
| E-12 | 契约测试模式：`contracts/run_error_codes.json` 由 Python 与 TS 两侧测试**双向**约束 | `contracts/`、`api/tests/test_contract_codes.py`、`web/tests/runErrors.test.ts` | 权限矩阵同样做成 `contracts/permissions.json`（§24.3.4），避免前后端各写一份漂移 |
| E-13 | `test_boundaries.py` 机器化四条依赖规则：`services/` 不 import `routers/`、`agent/` 不 import `services/`、`app/` 内无 `create_all` | `api/tests/test_boundaries.py` | 新增 `app/security/`（纯 HTTP 无关的主体/策略/密码/限流）与 `services/`（用户、会话、额度、审计）；**`services/` 可 import `security/`，反之不可**；路由才 import `security/deps.py` |
| E-15 | ★**（v2 评审补）浏览器端当前就带着一个"公开的 Bearer"**：`web/lib/api.ts:81,94` 读 `NEXT_PUBLIC_API_TOKEN`，并给每个浏览器请求加 `Authorization: Bearer`；`NEXT_PUBLIC_*` 在构建期内联进**公开 JS**。`scripts/seed.mjs:8` 读的是**服务端环境变量** `API_TOKEN`（脚本，合法） | 如左 | `API_TOKEN` 一旦被定义为 `service` 主体凭据，**绝不能**再出现在任何会下发给浏览器的产物里：`api.ts` 删除这两处（§24.4.2）；`sse.ts` 不再以"是否配置了令牌"决定要不要取 SSE 令牌（一律取）；README 对 `NEXT_PUBLIC_API_TOKEN` 标注"已废弃，设置它等于公开管理员凭据"；AC-206 守卫 |
| E-14 | 环境里已装 `Pillow 13.0`、`argon2-cffi`、`bcrypt`、`passlib`，但 `pyproject.toml` **只声明**了 fastapi/uvicorn/pydantic/SQLAlchemy/alembic/jsonschema/Jinja2/pypinyin | `api/pyproject.toml:10-23` | 密码哈希用**标准库 `hashlib.scrypt`**（零新依赖，本机实测 `n=2**15,r=8,p=1,maxmem=64MiB` 可用）；验证码图片需要 **`Pillow>=10.1`**（唯一新增依赖，须写进 `pyproject.toml`，不能靠"本机恰好装了"） |

### B. 需求歧义的裁决（每条都可由配置翻转，且在界面/文档里说清楚）

| # | 歧义 | 裁决 | 理由 / 翻转方式 |
| --- | --- | --- | --- |
| D-1 | "根管理员：`jdqqjr/13519126390`"是 **账号/密码** 还是 账号/手机号？ | 按 **用户名 `jdqqjr` / 初始密码 `13519126390`** 引导（`ROOT_ADMIN_USERNAME` / `ROOT_ADMIN_PASSWORD` 可覆盖；**仅在库里没有 root 时首次落库一次**，之后永不被环境变量覆盖） | 项目无短信通道，"手机号登录"无法实现验证码闭环；若原意是手机号，改 `ROOT_ADMIN_USERNAME` 即可（用户名规则允许 3–20 位字母数字下划线，11 位数字合法） |
| D-2 | 初始密码是公开写在需求里的弱口令，线上域名 `aragontrading.online` 已部署 | 引导后 root 带 `password_is_default=1`；**默认强制首次登录改密**（`AUTH_FORCE_ROOT_PASSWORD_CHANGE=1`）。会话进入 `must_change_password` 态：只放行 `/auth/me`、`/auth/password`、`/auth/logout`，其余 403 `password_change_required`；若设 `=0`，则不强制但后台与账号页顶部出现**不可关闭**的红色横幅 | 这是对"需求里写了密码"的**最小安全折中**，不是违抗：用户依然用 `jdqqjr/13519126390` 登录，只是第一次要换个新密码 |
| D-3 | "注册需要输入验证码"是图形验证码、邮件码还是短信码？ | **图形验证码**（服务端 Pillow 生成 PNG，5 位、去歧义字符集、一次性、5 分钟、最多 5 次尝试）。抽象成 `ChallengeProvider` 接口，`captcha` 为唯一实现，`email`/`sms` 只留接口位不实现 | 没有外部通道就没有"发码"；图形码不依赖任何第三方且可离线测试 |
| D-4 | "管理员由根管理员分配" | **只有 root 能把 `user` 升为 `admin`、把 `admin` 降为 `user`、停用/删除/重置 admin**；admin 只能管理 `role='user'` 的账号；注册入口**永远只能产生 `user`**（邀请码也不能带 `admin`）；root **唯一**（部分唯一索引）、不可停用、不可删除、不可改名、不可被降级 | 对应 `admin.admins.manage` 权限只属于 root |
| D-5 | "次数限制"按什么口径？ | **每用户每日 N 次生成**（默认 `N=3`，后台可改；时区 `QUOTA_TZ=Asia/Shanghai`，与 A 股业务日对齐）＋ **可叠加的"赠送额度"**（管理员发放/申请通过/邀请码附带，不过期）。扣除顺序：**先日额度、后赠送额度**。admin/root 默认**不限次**（可被单独设限）。**失败不收费**：run 以非 `succeeded` 终态结束一律退还；成功但内容去重（`deduped`）**不退**（AI 成本已发生）。`deep` 追问另有独立日额度（默认 10）。`cite` 追问不耗 AI，**不计额度**（仅按 IP 限速） | 日额度给"稳定预期"，赠送额度给"运营弹性"，二者分开记账才解释得清"我今天为什么只剩 1 次" |
| D-6 | 游客"能查看"到什么程度？ | **全部只读**：首页、`/reports` 报告库、`/{symbol}` 工作台（K 线、全部版本、版本比较、全文检索、`cite` 追问、导出 HTML/MD、离线副本）。**不能**：生成、`deep` 追问、自选、定时刷新、报告管理、回收站、成本面板、引擎面板 | 与"游客可以查看投研但不能生成投研"逐字对应；导出是"查看"的延伸，不设门槛 |
| D-7 | `API_TOKEN` 怎么办？ | 保留，但语义从"整站闸门"改为 **服务主体凭据**：带正确 Bearer 的请求被视为 `role=service`（权限见 §24.3.4：admin 全集**减去**一切"用户账号/额度/邀请/设置/预算"类写权限，所以它是"脚本与运维探针用的只读 + 生成 + 维护精选"凭据，**不是**第二个 root），审计里记 `actor=service`；**不带它的请求不再被整站拒绝**，按 cookie 会话/游客处理。★v2：**该令牌只允许存在于服务端环境变量与运维脚本**，浏览器侧不得持有（E-15）；Bearer 与 Cookie 并存时，**以 Bearer 为准**的前提是令牌正确——错误 Bearer 不影响 Cookie 会话的解析（降级为按 Cookie 处理，而不是 401） | 避免"设了令牌游客也看不了"的冲突；脚本与运维探针（`deploy/remote/*.sh`、`scripts/seed.mjs`）仍可用 |
| D-8 | 既有 972+260 条测试怎么保持绿？ | 新增开关 `AUTH_MODE=enforced|open`：默认 **`enforced`**；`open` 时所有请求视为 `service` 主体（等价旧"本地优先"），启动日志打 **ERROR 级横幅**，`/auth/me` 返回 `authMode:"open"`，前端显示黄条。`tests/conftest.py` 设 `AUTH_MODE=open`；新增鉴权用例用 fixture 切到 `enforced` | **中间件/依赖在请求时读取 `settings.auth_mode`，不得在导入期缓存**（否则 monkeypatch 无效，守卫形同虚设） |

---

## 24.1 Overview

### 要做什么

Aragon Research Terminal 至今是**单人本地工具**：没有"人"的概念，谁能打开页面谁就能花钱生成研报、改预算、删报告。
本轮把它升级为**可对外开放的多用户产品**，同时不破坏"一次生成、反复查看"的核心体验——三类访客：

* **游客**（不登录）：只读。能看到所有已生成的研报、K 线、搜索、版本比较、导出，能做零成本的 `cite` 追问；一切会**花钱或写数据**的动作都被拦下，并且界面上不是"灰掉不说话"，而是一个"登录后即可生成"的明确入口。
* **普通用户**：自助**注册**（用户名 + 密码 + **图形验证码**；也可凭管理员发的**邀请码**）。可以生成研报，但有**每日次数限制**；额度用完时看到的不是一个报错，而是"今日已用完 3/3 · 明日 00:00 重置 · 可申请额外额度"。有自己的自选列表、自己的追问记录、账号页（改密码、查看登录设备、额度流水）。
* **管理员 / 根管理员**：有独立的**管理后台**（概览 / 用户 / 邀请码 / 额度申请 / 审计日志 / 系统设置）。**根管理员 `jdqqjr` 是唯一最高权限账号，其余管理员只能由它任命。**

### 为什么这样做（三条设计主张）

1. **默认拒绝 + 单一策略表**，而不是"每个路由自己记得加一行 `Depends`"。现有 40 余条路由全部写进 `security/policy.py` 的一张表（附录 A），启动时与 `app.routes` 做**集合相等**校验——新增路由忘了登记，进程**起不来**；测试里再断言一遍。权限类 bug 的典型成因是"漏了一条"，所以把"漏"变成编译期错误。
2. **额度是账本，不是计数器**。每次扣/退/赠都是 `quota_ledger` 里一行不可变记录；余额由 `SUM` 推出。这样"退还"可以幂等（部分唯一索引 `(ref_id, reason)`）、"为什么只剩 1 次"可以逐行解释、管理员调整有留痕、并发下用"每用户互斥锁 + 事务内复核"保证**绝不超发**。
3. **安全默认值要正确，而不是文档里写"生产请修改"**：会话是 HttpOnly Cookie（JS 读不到）、密码 scrypt、登录失败锁定 + 自适应验证码、统一错误话术防用户枚举、CSRF 用 Origin 校验、SW 不缓存任何"有人登录"的页面。根管理员的公开弱口令被**强制首登改密**兜住。

### 与现有体系的关系（不破坏什么）

* **公共资料不变**：报告库仍是全站共享；`GET` 类读接口对游客保持原响应形状（只有"带 viewer 视角"的少数字段**追加**：`ReportSummary.author/canManage/canCurate`、`Capabilities.redacted`）。
* **生成管线不变**：`RunManager` 的六阶段、SSE、预算闸门、去重一律不动；本轮只在 `start()` 的**闸门链上插一道额度闸**并给 `Run` 打 `created_by` 标。
* **既有错误优先级不变**：`409 run_conflict → 429 too_many_runs → 402 monthly_budget_exceeded` 的顺序一个字不改，额度闸的位置见 §24.3.9(a)（先做只读预检、最后原子预留）。

---

## 24.2 需求分解与验收口径（X1–X8）

| 编号 | 需求 | 一句话验收 | 对应 AC |
| --- | --- | --- | --- |
| **X1** | 账号与会话 | 注册/登录/登出/改密/多设备会话；Cookie 会话；失效即时生效（改角色、停用、改密都立刻反映在下一次请求） | AC-153–AC-164 |
| **X2** | 注册验证码与反滥用 | 注册必须带正确验证码；验证码一次性；登录连错 3 次起要验证码；连错 5 次锁 15 分钟；IP 限速；统一话术 | AC-165–AC-172 |
| **X3** | RBAC 与默认拒绝 | 四种主体（guest/user/admin/root）+ service；每条路由必须登记；对象级规则（拥有者/管理员）；root 唯一不可动 | AC-173–AC-184 |
| **X4** | 生成额度 | 日额度 + 赠送额度；预留原子、并发不超发；失败退还；`deep` 独立额度；申请额外额度闭环 | AC-185–AC-193 |
| **X5** | 管理后台 | 用户管理（建/查/停/启/删/重置密码/踢下线/调额度）、邀请码、额度申请、审计、系统设置、概览；admin 与 root 的能力边界 | AC-194–AC-199 |
| **X6** | 前端接入 | 游客态、登录/注册页与弹窗、用户菜单、额度芯片、生成按钮三态、账号页、后台页、403/401 统一处理、首屏预算不破 | AC-200–AC-203 |
| **X7** | 多用户改造 | 自选分人、定时刷新仅管理员、成本/探针仅管理员、追问分人、报告管理分人、`Capabilities` 对非管理员脱敏、SW 隔离 | AC-204–AC-205 + 贯穿；AC-206（令牌不下发浏览器）、AC-207（站点日总量）、AC-208（生产形态 CSRF）为 v2 新增 |
| **X8** | 引导与恢复 | root 首次引导、强制改密、`python -m app.cli reset-root-password`、`AUTH_MODE` 开关、迁移可回滚 | AC-153（子项）、AC-205 |

---

## 24.3 技术设计

### 24.3.1 身份模型（角色、状态、主体）

```
            ┌─────────┐  注册/邀请/管理员创建        ┌────────┐
 访客 ──────►  guest  │──────────────────────────────►  user  │
 (无会话)    └─────────┘                              └───┬────┘
                                          root 任命 ▲     │ root 撤销
                                                    │     ▼
                                              ┌─────┴──┐
                                              │ admin  │
                                              └─────┬──┘
                          唯一，库内引导，不可任命/删除/降级 │
                                              ┌─────▼──┐
                                              │  root  │ = jdqqjr
                                              └────────┘
   特殊主体：service（带正确 API_TOKEN 的脚本）、system（调度器/清扫器，无请求）
```

* **角色**（`users.role`）：`root | admin | user`。`guest` 不是数据库角色，是"没有有效会话"。
* **状态**（`users.status`）：`active | disabled | deleted`。`disabled` 的账号**不能登录且所有会话立即失效**（读会话时 join 用户行校验，不靠"踢人时记得删"）；`deleted` = 匿名化墓碑（用户名释放、密码哈希置 `!`、保留 id 以维持报告/审计的外键语义）。
* **主体 `Principal`**（`app/security/principal.py`，纯 dataclass，无 HTTP 依赖，`services/` 可用）：

```python
@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["guest", "user", "service", "system"]
    user_id: str | None            # kind == "user" 时非空
    username: str | None
    role: Literal["guest", "user", "admin", "root", "service", "system"]
    permissions: frozenset[str]    # 由角色查 contracts/permissions.json 派生，不单独存
    session_id: str | None
    must_change_password: bool     # 为 True 时 enforce() 只放行 PASSWORD_CHANGE_ALLOWED 三条路由
    ip: str                        # 已按 TRUST_PROXY 解析并截断到 /24（IPv6 /48）用于落库
    @property
    def is_admin(self) -> bool: return self.role in ("admin", "root", "service")
    def can(self, permission: str) -> bool: return permission in self.permissions
```

`system` 主体用于调度器触发的 run：**不扣额度**、`created_by` 取 `schedule.owner_id`，审计里 `actor=system`。

### 24.3.2 认证传输：HttpOnly Cookie 会话（含四个跨源陷阱）

**会话令牌**：`secrets.token_urlsafe(32)`（256 bit）；库里只存 `sha256(token)`（`sessions.token_hash`），所以数据库泄露不等于会话泄露。`Set-Cookie: aragon_sid=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=…; Secure(见下)`。`remember=false` 不设 `Max-Age`（浏览器会话 Cookie），服务端绝对有效期 12 h；`remember=true` 绝对 30 天、**空闲 7 天**（滑动）。`last_seen_at` 至多每 5 分钟写一次（SQLite WAL 下每请求一次写会放大锁竞争，同 `scheduler` 的写入克制）。每用户最多 10 个活会话，超出踢最旧。

**`Secure` 判定**（`COOKIE_SECURE=auto|1|0`，默认 `auto`）：请求是 https，或 `TRUST_PROXY=1` 且 `X-Forwarded-Proto=https` → 加 `Secure`。否则（本地 http）不加——加了浏览器会直接丢掉 Cookie，登录"成功但立刻掉线"。

**(a) CORS**：`allow_credentials=True`；启动时若 `ALLOW_ORIGINS` 含 `*` 则 **raise**（凭据 + 通配是浏览器会拒绝的组合，且是真实漏洞）；响应加 `Vary: Origin, Cookie`；`/auth/*`、`/admin/*`、`/watchlist`、`/me*` 响应加 `Cache-Control: private, no-store`。中间件顺序（E-2）保持：鉴权在内、CORS 在外。

**(b) CSRF**：`SameSite=Lax` 挡跨站 POST，但 `localhost:3000` 与 `localhost:8000` 是 **same-site**（站点不看端口），同机其它端口的页面可以带上 Cookie。所以对 **POST/PUT/PATCH/DELETE** 加 Origin 校验中间件（`security/csrf.py`）：

1. `Origin` **缺失**（curl、服务端脚本、同源老浏览器）→ 放行（非浏览器客户端不构成 CSRF）；`Origin: null`（沙箱 iframe、`file://`）→ **拒绝**。
2. `Origin` 存在时，满足下列**任一**即放行，否则 `403 csrf_rejected`：①`Origin` 的完整串 ∈ `cors_origins`；②★**`Origin` 的 netloc（`host[:port]`，小写）== 请求的 `Host` 头 netloc**；③`TRUST_PROXY=1` 且 netloc == `X-Forwarded-Host` 的第一个值（Next 的 `/py` 重写代理场景）。
3. ★**比较时一律忽略 scheme**（v2 评审 RV-2，P0）。原因：生产链路是 浏览器 `https://aragontrading.online` → nginx（终止 TLS）→ uvicorn **明文 http**，`request.url.scheme` 恒为 `http`，而 `Origin` 是 `https://…`；`ALLOW_ORIGINS` 在生产为空（同源）。若按"完整 origin 相等"判断，**所有生产写请求（含登录）都会 403**。忽略 scheme 不削弱 CSRF 防护——攻击者页面的 `Origin` 的 **host** 必然不是我们的 host；https→http 降级不是 CSRF 向量（Cookie 的 `Secure` 另行处理）。
4. 带正确 `API_TOKEN` Bearer 的服务请求跳过此检查（它不依赖 Cookie，本就不可被 CSRF）。
5. 依赖：nginx 必须 `proxy_set_header Host $host`（`deploy/nginx/aragon-trading.conf` 的 `/api/` 与 `/api/v1/reports` 两个 location 都已有，已核）；`tests/test_csrf_cors.py` 增一条"`Host: aragontrading.online` + `Origin: https://aragontrading.online`、`request.url.scheme=http`、`cors_origins=[]` → 放行"的**生产形态用例**（AC-208）。

**(c) RSC 服务端转发**：Next 服务端 fetch 不会自动带浏览器 Cookie。`web/lib/api.ts` 重构为工厂 `createApi(ctx: { cookie?: string })`（`request` 变闭包，端点表一字不改地移入），导出 `api = createApi({})`（浏览器/无身份）；新增 `web/lib/serverApi.ts`（`import 'server-only'`）：

```ts
export async function serverApi() {
  const jar = await cookies();                       // next/headers，只能在服务端组件里调
  const sid = jar.get('aragon_sid')?.value;
  return createApi({ cookie: sid ? `aragon_sid=${sid}` : undefined });
}
```

所有 `app/**/page.tsx` 与根 `layout.tsx` 里的 `api.xxx()` 改为 `const api = await serverApi()`（共 4 个文件，见 §24.4）。**为什么不在 `api.ts` 里动态 `import('next/headers')`**：`api.ts` 同时被客户端组件引用，Next 会对客户端依赖图里的 `next/headers` 报构建错；拆成 `server-only` 文件是确定可行的做法。

★**v2（RV-6）服务端缓存必须关掉**：现有 `api.capabilities(revalidate = 30)`、`api.probeStatus(revalidate = 0)`（`api.ts:134,181,261`）借 `fetch(..., {next:{revalidate}})` 走 Next **Data Cache**。`Capabilities` 现在随主体而异（§24.3.9(g) 脱敏），若缓存按 URL 复用，会把 admin 的全量响应回放给游客页面，脱敏被缓存层绕过。规则：**`createApi(ctx)` 的服务端实例（`typeof window === 'undefined'`）一律 `cache: 'no-store'`，并忽略 `revalidate` 参数**；`revalidate` 只在浏览器侧保留语义（浏览器 `fetch` 本就忽略 `next` 选项，等于无效参数，不破坏签名）。`web/tests/api-credentials.test.ts` 增断言：对任一端点调用 `createApi({cookie:'aragon_sid=x'})`，传给 `fetch` 的 init 含 `cache:'no-store'` 且**不含** `next.revalidate`。同时：`createApi` 内**不得**读取任何 `NEXT_PUBLIC_API_TOKEN`（E-15）。

**(d) 回环别名对齐**（开发期必踩）：浏览器里 `apiBase()` 若发现 `PUBLIC_BASE` 的 hostname 与 `location.hostname` **都属于** `{localhost, 127.0.0.1, [::1]}` 且不相等，就把 API 的 hostname 替换成 `location.hostname`（端口、协议不变）。这样 `http://localhost:3000` 页面调 `http://localhost:8000`，Cookie 就落在同一主机上；`ALLOW_ORIGINS` 默认已含两种写法。生产走 nginx 同源，该分支不触发。

**(e) 浏览器侧 fetch**：`request()` 在 `typeof window !== 'undefined'` 时加 `credentials: 'include'`；SSE 的 `EventSource` 继续用 `?token=`（见 §24.3.11），**不依赖 Cookie**。

### 24.3.3 主体解析与"默认拒绝"策略表

**解析顺序**（`security/deps.py::get_principal`，同步依赖，在 FastAPI 线程池里跑，不阻塞事件循环；结果缓存在 `request.state.principal`）：

1. `settings.auth_mode == "open"` → `service` 主体（权限全集，**不**受 §24.3.4 `service.minus` 收窄——它是"旧本地优先行为"的等价物）。
2. `Authorization: Bearer <t>` 且 `settings.api_token` 非空且 `hmac.compare_digest` 相等 → `service`（权限按 §24.3.4 收窄）。Bearer 不存在或不相等 → **不报错，继续第 3 步**（错误 Bearer 不应让公开路由 401，也不应遮蔽合法 Cookie）。
3. Cookie `aragon_sid` → `SELECT … FROM sessions JOIN users` 一次查询：`revoked_at IS NULL AND expires_at > now AND idle_expires_at > now AND users.status='active'` → `user`；命中后按 §24.3.2 的节流写 `last_seen_at` 并滑动 `idle_expires_at`。失效的 Cookie → 响应里 `Set-Cookie: aragon_sid=; Max-Age=0`（清掉死 Cookie），主体降级为 `guest`，**不**对公开路由报 401。
4. 以上都不是 → `guest`。

**策略表**（`security/policy.py`，附录 A 是它的完整内容）：键为 `(HTTP 方法, FastAPI 路径模板)`，值为 `Policy(permission, object_rule=None, rate=None, public_note=None)`。`permission` 取自 §24.3.4 的**固定词表**；`"public"` 表示任何主体（含 guest）。

**强制点**（`security/deps.py::enforce`）：所有路由器在 `include_router(..., dependencies=[Depends(enforce)])` 统一挂载，`enforce` 从 `request.scope["route"].path_format` 取模板、查表，顺序是：

1. 表里**没有** → `500 policy_missing`（正常不会发生，因为下面的启动校验）；
2. `principal.must_change_password` 且路由不在 `PASSWORD_CHANGE_ALLOWED` → `403 password_change_required`；
3. 权限不满足：`guest` → `401 unauthorized(reason="no_session")`；已登录 → `403 forbidden(detail.permission=…)`；
4. 限速（若声明 `rate`）→ `429 rate_limited`；
5. 通过后，**对象级规则**不在 `enforce` 里做（它需要先加载对象），而是由路由调用 `security/objects.py` 的 `assert_can_manage_report(principal, report)` 等纯函数（见 §24.3.9）。

**启动期集合校验**（`main.py::create_app` 末尾）：遍历 `app.routes` 取所有 `(method, path_format)`，与 `POLICIES` 的键做**双向差集**，任何一边非空 → `raise RuntimeError`（列出差集）。`tests/test_policy_table.py` 再断言一遍（防止有人把校验整段删了）。★v2（RV-4）两处必须写死，否则进程起不来或校验永远不等：

1. **豁免规则是"不在 `/api/v1` 前缀下的路由"，不是一张手写清单**。实测 `create_app()` 的路由里有 `GET /`、`/docs`、`/docs/oauth2-redirect`、`/openapi.json`、`/redoc` 共 5 条根级路由（v1 的清单漏了 `/docs/oauth2-redirect`）；它们都不挂 `enforce`、不进策略表。
2. **键带前缀**：`path_format` 是 `/api/v1/reports/{report_id}`，附录 A 为可读性省略了 `/api/v1`。`policy.py` 用助手 `_api(method, path_tail, policy)` 在加载时统一补前缀，`verify_against_app` 比较的是补全后的键。另外附录 A 的路径参数名写的是 `{id}`，**真实模板用的是各路由自己的名字**（`{report_id}`、`{instrument_id}`、`{run_id}`、`{ask_id}`、`{other_id}`、`{schedule_id}`），实现时以 `app.routes` 为准（校验会逼出差异）。

### 24.3.4 权限词表与角色矩阵（单一真源 `contracts/permissions.json`）

```json
{
  "version": 1,
  "roles": {
    "guest": ["report.read", "market.read", "search.read", "ask.cite", "export.read"],
    "user":  ["@guest", "report.generate", "ask.deep", "watchlist.own", "report.manage_own", "account.self", "quota.request"],
    "admin": ["@user", "report.manage_any", "schedule.manage", "cost.read", "engine.probe",
              "admin.overview.read", "admin.users.read", "admin.users.manage", "admin.invites.manage",
              "admin.quota.manage", "admin.audit.read", "admin.settings.manage", "admin.featured.manage"],
    "root":  ["@admin", "admin.admins.manage", "cost.manage"]
  },
  "service": { "inherits": "admin", "minus": [
    "admin.admins.manage", "account.self", "quota.request", "watchlist.own",
    "admin.users.manage", "admin.quota.manage", "admin.invites.manage", "admin.settings.manage",
    "cost.manage", "report.manage_own"
  ] }
}
```

★v2（RV-1/RV-17）：`service` 的收窄是**权限词表层面**的，不是"路由里再判一次"：它保留 `report.generate`（脚本可触发生成，且 `reserve()` 对 service 直接放行不记账本）、`report.manage_any`、`schedule.manage`、`cost.read`、`engine.probe`、`admin.overview.read`、`admin.users.read`、`admin.audit.read`、`admin.featured.manage`；**去掉**所有能改"人、钱、门"的权限（用户账号、额度、邀请码、注册模式/默认额度、月度预算）。结论：令牌泄露的最坏后果是"有人能触发生成/改精选/读审计"，不是"有人能接管账号体系"。`report.manage_own` 去掉是因为 service 没有"自己的报告"，它走 `manage_any`。`policy.py` 里 `/admin/users`（POST/PATCH/DELETE/…）等路由的权限串本就是 `admin.users.manage`，所以 service 对它们自然得 403 `forbidden`。

`@role` 表示继承。Python 侧 `security/permissions.py` 读这份 JSON 展开成 `frozenset`；TS 侧 `web/lib/permissions.ts` 同样读（构建时用 `import permissions from '../../contracts/permissions.json'`；`web/tsconfig.json` 已 `resolveJsonModule: true`，已核）。★v2（RV-16）注意该文件在 `web/` **之外**，而 `next.config.ts` 钉了 `outputFileTracingRoot=process.cwd()`：`tsc`/vitest 能过不代表 `next build`（webpack）的越界解析能过，M45 出口准则要求**实测 `next build`**；若失败，退为 `web/scripts/sync-permissions.mjs`（`prebuild`/`pretest` 钩子里把 JSON 复制到 `web/lib/permissions.json`）+ vitest 断言两份**字节相等**；`api/tests/test_contract_permissions.py` 与 `web/tests/permissions.test.ts` 双向断言**展开后集合逐角色相等**，并断言 `policy.py` 里用到的每个权限串都在词表里（防拼写漂移，同 E-12）。

关键裁决：**`cost.manage`（改月度预算）只给 root**——它决定"这个站一个月最多花多少钱"，是资金级开关；`engine.probe`（≈$0.0016/次的自检）给 admin。

### 24.3.5 密码（`security/passwords.py`，零新依赖）

* **哈希**：`hashlib.scrypt(password_nfkc.encode(), salt=os.urandom(16), n=2**15, r=8, p=1, maxmem=2**26, dklen=32)`；存储格式 `scrypt$15$8$1$<salt_b64url>$<hash_b64url>`（参数自描述，将来调参只需新哈希用新参数，`verify` 按存储串里的参数算；`needs_rehash()` 在登录成功后若参数落后则透明升级）。输入先做 `unicodedata.normalize("NFKC")`，避免全角/半角造成"同一个密码输不进"。
* **校验**：`hmac.compare_digest`。**登录对"用户不存在"也执行一次等价的 scrypt**（对一个固定的假哈希），否则响应时间差会泄露"用户名是否存在"。
* **耗时与线程**：一次 scrypt ≈ 60–100 ms，必须在**线程池**里（同步路由默认就在）；`/auth/login` 与 `/auth/register` 声明为 `def` 而非 `async def`——**否则会阻塞事件循环并拖死 SSE**（既有 `test_event_loop_guard.py` 的同一类问题）。
* ★**并发上限（v2，RV-7）**：一次 scrypt（`n=2**15, r=8`）占 ≈ 32 MiB、60–100 ms CPU。免认证入口在 FastAPI 默认 40 线程的线程池里并发 40 次 ⇒ 瞬时 ≈ 1.3 GB，单 IP 限速挡不住分布式。`security/passwords.py` 持有模块级 `_KDF_SLOTS = threading.BoundedSemaphore(4)`，`hash_password()`/`verify_password()`/假哈希计时**统一**经它（排队，不并发）；等待超过 3 s 抛 `503 busy`（`Retry-After: 2`，不区分是登录还是注册）。同时加一个**全站**维度的限速（`auth.login.global`：120/分钟，`auth.register` 已有 60/小时），防分布式耗尽 4 个槽位导致合法用户长期排队。持有者管理员的已登录请求**不经过**该信号量（它只在哈希函数里）。测试：用 8 线程并发调用 `verify_password`，断言同一时刻进入 `hashlib.scrypt` 的线程数 ≤ 4（monkeypatch 计数）。
* **策略**（`validate_new_password(pw, username)`，仅用于注册/改密/重置/管理员建号；root 的引导口令**豁免**，由 D-2 的强制改密兜底）：长度 8–128；不得等于用户名（大小写不敏感）或包含用户名；字符类别至少 **2 类**（小写/大写/数字/符号）；不在内置弱口令表（`123456`、`password`、`qwerty123`、`aragon123`… 约 50 条，放 `security/weak_passwords.py`）；不得是单字符重复或连续序列。失败 → `422 weak_password`，`detail.rules` 列出**未满足的规则 id**（前端据此点亮强度条下的清单）。
* **永不记录**：密码、`Set-Cookie` 值、会话令牌不进日志/审计/错误信封。`api/app/logging.py` 目前**没有**任何脱敏过滤器（已核对，只有 `request_id_var` 与格式化），本轮新增一个 `RedactFilter`：对日志记录的 `args`/`msg` 里形如 `password=…`、`aragon_sid=…`、`Authorization: Bearer …`、`token=…` 的片段打码为 `***`；并有测试断言一次完整登录请求后日志里不出现明文密码（AC-163）。

### 24.3.6 验证码与注册（X2）

**`ChallengeProvider` 接口**（`services/challenge.py`）：

```python
class ChallengeProvider(Protocol):
    kind: str                                     # "captcha"
    def issue(self, purpose: Literal["register", "login"], ip: str) -> IssuedChallenge: ...
    def verify(self, challenge_id: str, answer: str, purpose: str, ip: str) -> bool: ...
```

**图形验证码实现**（`services/captcha.py`）：

* 字符集 `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`（去掉 0/O/1/I/L 等歧义字符），**5 位，大小写不敏感**；答案存 `sha256(salt + answer)`，不存明文。
* 绘制：Pillow `ImageFont.load_default(size=34)`（Pillow ≥ 10.1 自带可缩放内置字体，**不依赖系统字体**——Windows 开发机与 Ubuntu 部署机行为一致）；每个字符独立画到透明小图 → 随机旋转 ±22° → 垂直抖动 → 贴回；叠 3 条抗锯齿贝塞尔干扰线 + 60 个噪点 + 轻微 `GaussianBlur(0.6)`；**自带深色圆角底**（`#17181c`，字符用 `--accent` 系浅绿/浅蓝/浅黄三色轮换），因为 `<img>` 里无法继承页面的 CSS 变量，亮/暗主题下都可读；输出 PNG（约 3–5 kB）→ `data:image/png;base64,…`。尺寸 `160×56`，`@2x` 出图 `320×112`，前端 `width=160 height=56` 渲染（清晰且稳定布局）。
* 存储：**进程内**字典 `{challenge_id: (answer_hash, purpose, ip_bucket, expires_at, attempts)}`，容量上限 5000（LRU 淘汰最旧），TTL 300 s，**一次性**（校验无论对错都递增 `attempts`，超过 5 次或成功后立即删除）；依赖 E-8 的单 worker 不变量，重启使在途验证码失效（无害：页面点一次"换一张"即可）。`challenge_id = secrets.token_urlsafe(12)`。
* **绑定 IP 桶**（/24）：不同网段拿别人的 `challenge_id` 来校验直接失败，防"验证码农场"把一张图分发给多机破解后回填。
* 反机器人的两个廉价信号（不是安全边界，只是提高成本）：蜜罐字段 `website`（人类看不见，非空即 `422 captcha_invalid` 且不泄露原因）；`issuedAt` 到提交少于 **1.2 s** 视为脚本（同样返回通用错误并计入 IP 限速）。
* 不可用降级：`import PIL` 失败或字体加载失败 → `issue()` 抛 `ChallengeUnavailable` → `503 captcha_unavailable`，**注册入口关闭并在 `/auth/config` 里 `registrationOpen=false`**（"宁可暂时不能注册，也不放行无验证码注册"）。启动日志打一行 `captcha: ready|unavailable(reason)`。
* **无障碍**：图片 `alt="验证码，5 位字母数字，不区分大小写"`；"换一张"按钮可键盘操作；若用户确实无法识别，页面文案引导"联系管理员获取邀请码"——邀请码注册**同样需要验证码**吗？裁决：**邀请码注册免图形验证码**（邀请码本身是一次性高熵凭据，且这是给视障/受限用户的合规通道），但邀请码尝试按 IP 限速（5 次/10 分钟），防暴力枚举。

**注册流程**（`POST /auth/register`，同步 `def`）：

1. 限速：IP 5 次 / 10 分钟、全站 60 次 / 小时（`rate_limited`）。
2. 读 `settings["auth.registration_mode"]`：`closed` → `403 registration_closed`；`invite` 且无邀请码 → `422 invite_required`；`open` 则邀请码可选（有则附带赠送额度）。
3. 验证码（无邀请码时必填）：`verify()` 失败 → `422 captcha_invalid`（不区分"错/过期/已用"，**但前端会自动换一张**）。
4. 用户名：`^[A-Za-z0-9_]{3,20}$`，保留词（`admin root system aragon support official api null undefined jdqqjr` 及其 `lc` 形式，含以它们**开头**后接数字的变体如 `admin01`）→ `422 invalid_username`；`username_lc` 唯一 → `409 username_taken`。**注意这里不对"是否被占"做时序对齐**（用户名占用本来就必须告知注册者）。
5. 密码策略 §24.3.5。
6. **一个事务**内：建 `users`（`role='user'`、`status='active'`、`quota_daily_limit=NULL`（跟随默认）、`invite_id`）→ 邀请码 `used_count+1`（`UPDATE … SET used_count=used_count+1 WHERE id=? AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?) AND disabled_at IS NULL`，**受影响行数为 0 → 回滚并 `422 invite_invalid`**，这是邀请码并发耗尽的唯一正确写法）→ 赠送额度账本行（`settings["quota.signup_bonus"]` + 邀请码 `bonus_credits`）→ 审计 `auth.register`。
7. 注册成功**直接登录**（建会话、Set-Cookie），省一步；响应体是 `Me`。

### 24.3.7 登录、锁定与限速

**`POST /auth/login`（同步 `def`）**：

```
限速(IP 10/min, 同用户名 10/min) → 取用户(不存在也走假哈希耗时) →
若 locked_until > now → 423 account_locked(retryAfterMs)            ← 不进入密码比对，不再叠加失败
若 failed_logins ≥ 3 → 要求 captcha（缺失 → 422 captcha_required；错 → 422 captcha_invalid）
比对密码 → 失败: failed_logins+=1; 达 5 次 → locked_until=now+15min（并写审计 auth.lockout）;
          统一 401 invalid_credentials "用户名或密码不正确"（不区分不存在/密码错/已停用）
→ status='disabled' 的账号：**密码正确才**返回 403 account_disabled(reason)，否则仍是 invalid_credentials
          （防止用"返回码不同"探测哪些用户名存在且被停用）
→ 成功: failed_logins=0, locked_until=NULL, last_login_at, 建会话, needs_rehash 透明升级, 审计 auth.login.success
```

* ★**影子节流器——不存在的用户名也要"被锁"（v2，RV-3）**：上面的失败计数与 `locked_until` 存在 `users` 行里，**不存在的用户名没有行**，于是连错 5 次后，存在的账号变 `423`/`422 captcha_required`、不存在的账号仍是 `401` —— 这就是一个枚举口，并且直接违反 AC-170 的"响应完全一致"。裁决：`security/ratelimit.py` 增 `ShadowThrottle`（进程内，键 `username_lc`，容量 20000、TTL 30 分钟，依赖 E-8 的单 worker），对**数据库里不存在**的用户名走**同一套阈值与同形状响应**：`failed ≥ 3` → 同样 `422 captcha_required`（带一张真实的新验证码图）；`failed ≥ 5` → 同样 `423 account_locked(retryAfterMs)`；计数窗口与真实账号一致（成功不会发生，故只增不减，到 TTL 自然清）。登录处理流程里**先**调用 `throttle_state(username_lc, user_row_or_None)`，由它统一给出 `(captcha_required, locked_until)`，其后的分支**不再区分**用户是否存在。副作用说明：攻击者可以"锁住"一个**尚未注册**的用户名 30 分钟——但注册路径**不查**该节流器（注册靠 `username_taken` 自己判断），所以不会影响真实用户注册。测试：对"存在/不存在"两个用户名各连续发 6 次错误密码，断言**每一次**的状态码、`error.code`、`detail` 键集合逐次相同（AC-170）。
* **自适应验证码**：`GET /auth/login-challenge?username=` **不**暴露"该用户是否在失败态"（否则又是枚举口）——前端的做法是：第一次登录不带验证码；若收到 `422 captcha_required`（响应里已包含 `detail.challenge` 一张新图），渲染验证码输入框并重试。每个失败响应都**可能**带 `detail.captchaRequired=true`，由服务端决定。
* **锁定对 root 同样生效**（root 是最大的攻击目标）；锁定是**账号级**的，会让攻击者能"锁住别人"（经典 DoS）——权衡后接受：15 分钟、且 root 可在后台"立即解锁"，而被锁账号的合法用户仍可通过**另一台设备已登录的会话**继续使用（锁定只拦登录，不拦既有会话）。
* **IP 限速器**（`security/ratelimit.py`）：进程内滑动窗口（`deque` 时间戳），键 `(scope, ip_bucket)`，容量上限 20000 键 + 惰性清理；`RATE_LIMIT_ENABLED=1`。限速表：

| scope | 窗口 | 上限 | 备注 |
| --- | --- | --- | --- |
| `auth.login` | 60 s | 10 / IP；10 / 用户名 | 两个维度各自计 |
| `auth.login.global` | 60 s | 120 / 全站 | ★v2：配合 scrypt 并发上限（§24.3.5），防分布式耗尽 KDF 槽位 |
| `auth.register` | 600 s | 5 / IP | 另有全站 60/小时 |
| `auth.captcha` | 60 s | 30 / IP | 防刷图 CPU（Pillow 渲染约 3–6 ms） |
| `auth.invite_probe` | 600 s | 5 / IP | 邀请码试错 |
| `guest.resolve` | 60 s | 30 / IP | `POST /symbols/resolve` 会触发外部数据源 |
| `guest.ask_cite` | 60 s | 20 / IP | `cite` 免费但吃 CPU（FTS 查询） |
| `guest.export` | 60 s | 20 / IP | 导出渲染 |

* **客户端 IP**：`request.client.host`；仅当 `TRUST_PROXY=1`（部署在 nginx 后，`deploy` 的 systemd 单元里设）才取 `X-Forwarded-For` **最右侧可信跳**——取最左值是经典伪造口，nginx 的 `$proxy_add_x_forwarded_for` 会把真实 IP 追加在**最右**。`TRUST_PROXY=0` 时忽略该头。★v2（RV-13/RV-14）：
  * **限速键与存储值分开**：落库/审计/会话列表用截断值（IPv4 `/24`、IPv6 `/48`，隐私）；**限速桶的键**用 IPv4 **精确地址**、IPv6 **`/64`**——`/24` 会让办公室/校园 NAT 后的 10 个人共用 10 次/分钟的登录额度，而 IPv6 `/48` 又给攻击者 2¹⁶ 个 `/64` 可轮换。验证码的 IP 绑定（§24.3.6）同样改用限速键，但**允许同一 `/24` 内漂移**（移动网络换基站）：绑定值取 `/24`（IPv4）/ `/48`（IPv6），**限速**仍用精确键，二者各司其职。
  * **配置自检**：`TRUST_PROXY=0` 时，进程**首次**在请求里看到 `X-Forwarded-For` 头，打一条 `WARNING`（只打一次）："检测到反向代理头但 TRUST_PROXY=0：所有访客将共用 127.0.0.1 的限速桶，请在 systemd 单元里设置 TRUST_PROXY=1"。部署脚本（`deploy/ops/status.sh`）同时打印 `trust_proxy: 0|1`。

### 24.3.8 额度引擎（X4）

**(a) 口径**（D-5 的落地）：两种资源 `report`（生成）、`deep_ask`（深度追问）。对每个 `(user, resource)`：

```
limit        = users.quota_daily_limit ?? 角色默认      # NULL=跟随；-1=不限；0=禁用；N=每日 N 次
               角色默认: user→ settings["quota.default_daily.report"](默认 3) / ["…deep_ask"](默认 10)
                        admin/root→ -1（不限）
daily_used   = -SUM(delta) WHERE source='daily' AND day_key=today(QUOTA_TZ)         （当日净消耗，已含退款）
bonus        =  SUM(delta) WHERE source='bonus'                                      （赠送余额：+发放 −消耗 +退款）
remaining    = ∞ 若 limit==-1；否则 max(0, limit − daily_used) + max(0, bonus)
resetAt      = 次日 00:00（QUOTA_TZ）的 UTC ISO 串
site_used    = -SUM(delta) WHERE resource='report' AND reason IN ('run','run_refund') AND day_key=today   （★v2：站点当日净生成数，仅含受额度约束的用户）
site_cap     = settings["quota.site_daily_cap"]（默认 60；0 = 不限）
```

★**站点日总量上限（v2，RV-11）**：每用户额度防不住"批量注册"——注册默认 `open`、图形验证码可被 OCR（R-A8 自认）、每 IP 每 10 分钟可注 5 号，农场可在**月度预算 402 触发之前**烧掉真金白银，并让正常用户整月都吃 402。`reserve()` 在每用户检查通过后追加一道：`site_cap > 0 且 site_used >= site_cap` → `429 quota_exceeded`，`detail.scope='site'`、`detail.canRequest=false`、`detail.resetAt` 同上（**不是** `rate_limited`：它是"今日全站名额已满"，前端对话框文案为"今日全站生成名额已用完，明日 00:00 重置"，且**不显示"申请额外额度"**，因为批准也无济于事）。管理员/root/service/system 不受限也不计入（它们不写账本）。`precheck()` 同样检查。理由与 `-1` 不写账本同源：无限额度用户不占站点名额。该上限与月度预算（402）是**两个层次**：402 是"钱"，站点上限是"人头"，二者都保留。默认 60 的依据与边界：`config.py:184` 的 `budget_monthly_usd` **默认是 `0.0`**（月度预算闸门默认不设上限），所以在没人设预算的部署里，站点上限是**唯一**的总量护栏，这正是它必须有非零默认的原因；具体数值 60 是一个保守起点（约为"每个普通用户默认日额度 3"的 20 个用户量），**不是**实测成本推出的结论——部署者应结合真实单次成本在"系统设置"里调（AC-207）。

**(b) 预留**（`services/quota_service.py::reserve(session, principal, resource, ref_id)`）：

1. `principal.kind in ("system","service")` 或 `limit == -1` → 直接放行，**不写账本**（无限额度不记流水，避免账本无限膨胀；审计里有 run 记录）。
2. 取该用户的互斥锁（`_locks[hash(user_id) % 64]`，`threading.Lock`；依赖单 worker，E-8）。
3. 锁内重算 `remaining`（**复核**，不信任调用前的预检；读到的是最新已提交数据——pysqlite 对 `SELECT` 不开显式事务，已核）与 `site_used`；`remaining <= 0` → `429 quota_exceeded`；站点上限满 → `429 quota_exceeded(scope='site')`。
4. 决定 `source`：`daily_used < limit` → `daily`，否则 `bonus`；写一行 `delta=-1, reason='run'|'ask', ref_id, source, day_key`（只 `flush`，**不** `commit`）。**同一个事务**里随后写 `runs` 行（见 §24.3.9(a)），任一步失败整体回滚，不会"扣了额度没开成 run"。
5. ★**v2（RV-9）锁的释放点是"调用方 `commit()`/`rollback()` 之后"，不是"INSERT 之后"**。v1 在第 5 步于 INSERT 后释放锁，而 `Run` 的 `session.commit()` 在 `start()` 末尾（`run_manager.py`）：锁早于提交释放 ⇒ 后一个线程的第 3 步读不到这条**未提交**的扣减 ⇒ 同一用户并发超发。契约因此改为**上下文管理器**，由调用方的 `try/finally` 保证释放：

```python
with quota_service.reservation(session, principal, "report", ref_id=run_id) as r:   # 进入时：取锁→复核→flush 账本行
    workspace.mkdir(parents=True, exist_ok=True)     # 锁内允许且仅允许这一次本地 mkdir（微秒级）
    session.add(run)
    session.commit()                                  # IntegrityError(ux_runs_active) 时 rollback，账本行一并回滚
# 退出时（无论正常/异常）：释放锁
```

   `reservation()` 对 `system`/`service`/`limit == -1` 返回一个**空上下文**（不取锁、不写账本）。锁内**不得**出现网络、子进程、`sleep`；允许的 I/O 只有同一事务内的 SQL 与上面那次 `mkdir`。★**线程模型说明**：`create_report`/`ask_report` 是 `async def` 且**同步**调用 `start()`/`create_deep_ask()`（`routers/reports.py:133,162`、`routers/asks.py:59`，因为 `manager.schedule` 需要事件循环）——它们在事件循环线程上天然串行，所以对**这两条路径**而言锁几乎总是无竞争的；锁真正保护的是线程池里的调用方（`def` 路由、`reconcile_refunds` 的定向对账、测试里的 20 线程用例）。不要因此把锁删掉：把某条路由改成 `def` 是常见的重构，到那时没有锁就是静默超发。

**(c) 预检**（只读，`quota_service.precheck()`）：在 `POST /instruments/{id}/reports` **最前面**（闸门链之前）调用，目的是**错误优先级**：一个额度已为 0 的用户，不该先被告知"该标的正在生成中"（409）然后点了重试才发现没额度——额度是用户级的、确定的，409/429/402 是系统级的、瞬时的。预检失败同样是 `429 quota_exceeded`；预检通过不保证预留成功（并发），所以 (b) 仍会复核。

**(d) 退还**（幂等对账，E-5 的解法）：

* 账本部分唯一索引 `ux_quota_ref_reason ON quota_ledger(ref_id, reason) WHERE reason IN ('run','run_refund','ask','ask_refund')`：同一个 run 至多一条扣、一条退。
* `reconcile_refunds(session)`：找出「有 `reason='run'` 行、对应 `runs.status IN ('failed','cancelled','timeout','budget_exceeded')`（以及 `ask` ↔ `report_asks.status IN ('failed','cancelled')`）、且尚无 `*_refund` 行」的记录，逐条插入 `delta=+1`、**复制原行的 `source` 与 `day_key`**（退到"当初扣的那一天/那个池子"，而不是退到今天）。用 `INSERT … ON CONFLICT DO NOTHING`（SQLite ≥ 3.24 / PG 均支持）保证并发调用安全。
* **调用点（三处，缺一不可）**：①`main._sweep_loop` 每 60 s（与活性清扫同循环，**不依赖** `SCHEDULER_ENABLED`，理由同 V11-P1-4）；②启动时 `recover_interrupted()` 之后立即一次；③`quota_service.status()` 读额度前先对**该用户**做一次定向对账（用户刷新页面看到的永远是退款后的数）。**不**在 `run_manager` 的三个终态写入点里插调用（E-5：会漏会重）；但 `_finalize_failure` 收尾处**允许额外**调用一次定向对账作为"尽快退款"的优化——对账幂等，所以这是纯加速，非正确性依赖。
* "成功但 `deduped`" **不退**（D-5）；"`cancelled` 但已产出版本"不可能（取消即未产出）。

**(e) 赠送与调整**：管理员 `POST /admin/users/{id}/quota`：`{"grant": n, "note"}`（`n` ∈ [-50, 50] 非零；负数 = 扣回，`reason='admin_revoke'`，余额不会被扣到负数——扣回量夹到当前赠送余额）、`{"dailyLimit": N|null|-1, "resource"}`（改日额度覆盖值）。两类操作都写审计 `quota.grant` / `quota.limit.set`，并在账本里留 `actor_id`。

**(f) 额度申请闭环**（`quota_requests`）：用户在"额度用完"对话框里填理由 → `POST /me/quota-requests`（每个用户同一时刻只能有 1 条 `pending`，部分唯一索引兜底；提交时限 200 字）；管理员在"额度申请"标签页一键「通过（+N）/ 拒绝（附理由）」→ 通过即写 `reason='request_approved'` 的赠送行。用户的账号页能看到申请状态与处理备注（拒绝理由）。

**(g) 时区**：`day_key` 由服务端用 `zoneinfo.ZoneInfo(settings.quota_tz)` 计算（`Asia/Shanghai` 无夏令时，仍按 zoneinfo 写以便换区）。**日界在退款时不重算**（用原行 `day_key`）。测试里通过注入 `now()` 时钟（沿用既有 `fixed clock` fixture）跨零点验证。

### 24.3.9 既有能力的多用户改造（X7，逐项裁决）

**(a) 生成入口**（`routers/reports.py::create_report` 与 `run_manager.start`）

* 路由依赖 `Depends(enforce)`（策略 `report.generate`）→ guest 得 401，user/admin 放行。
* `RunManager.start()` 新增关键字参数 `actor: Principal | None = None`（默认 `None` ⇒ 视为 `system`，所以 `scheduler_ticker` **一行不改**仍然通；`create_report` 路由传 `actor=principal`）。`start()` 内位置：**第 0 步 spawn 能力 → `active_run_for` 409 → `count_active_runs` 429 → `can_start` 402 → `base_report_id` 422 → ★进入 `with quota_service.reservation(...)`（取锁、复核、`flush` 扣减行）→ `workspace.mkdir()` → 写 `Run` → `commit()` → 退出上下文释放锁**（契约见 §24.3.8(b) 第 5 步，v2 修订）。预留放在 422 之后、`mkdir` 之前，理由与 V6-P1-7 同构：被拒绝的请求不得在磁盘上留工作区，也不得吃掉额度。预检（§24.3.8(c)）在路由层、`start()` 之前，**只读**。`start()` 的既有同步签名与 `IntegrityError → RunConflict` 分支**不变**，只是 `session.add(run); session.commit()` 被包进了上下文。
* `Run.created_by = actor.user_id`；`create_version()` 写 `Report.created_by = run.created_by`（沿用 `run_id` 链路，不新增传参）。`cost_ledger.user_id` 同步写。
* 响应 202 体**追加** `quota`（`QuotaStatus.report` 子对象），让前端不必再请求一次 `/me` 就能更新芯片。

**(b) 取消/查看 run**：`POST /runs/{id}/cancel` → 拥有者或 `report.manage_any`；`GET /runs/{id}`、`/events-token`、`/events` → 公开读（报告生成过程本就公开，登录页前已有的进度可见性不收窄），但 `RunOut` 对"非拥有者且非管理员"**脱敏**：`costUsd/costUsdEst/costSource/agentProvider/agentModel/failureFingerprint/exitCode` 置空（成本与引擎细节是运营信息）。`POST /runs/estimate` → `report.generate`（它暴露成本估算）。

**(c) 报告管理**：`PATCH /reports/{id}`（`pinned` 置顶 / `archived` 归档 / `setLatest` 设最新，见 `schemas/report.py:439` 的 `ReportPatch`）、`DELETE`、`POST /restore`、`/reports?scope=trash` → `report.manage_own` + 对象级：`report.created_by == principal.user_id` 或 `report.manage_any`。`created_by IS NULL`（历史/系统数据）→ **仅管理员可管**。★**v2（RV-10）全站可见旗标只给管理员**：`pinned` 与 `setLatest` 会改变**所有访客**看到的置顶与"最新版本"（报告库是全站共享的公共资料），所以即便是自己生成的报告，**普通用户也不得设置**——请求体含 `pinned` 或 `setLatest` 而主体没有 `report.manage_any` → `403 forbidden(detail.permission='report.manage_any')`（对象级判定之前先判，且整个请求原子失败，不做"部分生效"）。普通用户对自己的报告只可 `archived`（仅影响"全部"视图的归类）、`DELETE`、`restore`。前端 `canManage` 之外再给 `canCurate`（= `report.manage_any`）控制置顶/设最新按钮的显隐。**`ReportSummary` 的追加键因此是三个**：`author`、`canManage`、`canCurate`（v1 写的是两个，"23 → 25"改为"23 → 26"，见 §24.4.1）。`ReportSummary` **追加**三个键（这是 §23 硬约束 2 之后第一次破例，且只追加不改）：`author: string|null`（用户名；已注销显示 `"已注销用户"`；NULL 为 `null`）、`canManage: boolean`、`canCurate: boolean`（均**按请求者视角**由服务端算好，前端不自行推断）。`tests/test_report_library_api.py:100` 的"23 个键"断言改为 26 并**同时**断言新增键的类型。软删后的报告对 guest/非拥有者 `404 report_not_found`（回收站内容不外泄）。`GET /reports?scope=trash|starred` 中 `trash` 仅返回"我的"（admin 返回全部）。

**(d) 自选（watchlist）**：旧表 `watchlist` **保留为"平台精选"**（只读给所有人，管理员经 `/admin/featured-watchlist` 维护）；新增 `user_watchlist(user_id, instrument_id, sort_order, pinned, added_at)`，主键 `(user_id, instrument_id)`。`GET /watchlist`：登录用户返回**个人自选**；若该用户自选为空且 `settings["watchlist.seeded:<uid>"]` 未置位，则**一次性**把精选复制为其个人自选并置位（新用户一进来左栏不空，而不是一片荒原）；guest 返回精选（只读）。`POST/DELETE/PATCH /watchlist*` → `watchlist.own`。★v2（RV-15）播种是 `GET` 里的写副作用，必须幂等：先对 `settings['watchlist.seeded:<uid>']` 做 `INSERT … ON CONFLICT DO NOTHING`，**仅当该次插入成功**才复制精选（`user_watchlist` 同样 `ON CONFLICT DO NOTHING`）；响应 `Cache-Control: private, no-store`。`web` 的 `SymbolRail` 在 guest 态隐藏"加入自选/拖拽排序"并显示一条细提示"登录后可自定义自选"。

**(e) 定时刷新**：`/schedules*` 全部 → `schedule.manage`（admin）。`schedules.owner_id` = 创建者；ticker 触发的 run `actor=system`、`created_by=owner_id`、**不扣额度**。成本在 `cost_ledger.user_id` 记 owner，管理员概览里可见"定时任务成本"。

**(f) 成本/探针**：`GET /cost/summary|entries` → `cost.read`（admin）；`POST /cost/budget` → `cost.manage`（root）；`GET/POST /agent/probe` → `engine.probe`（admin）。

**(g) `Capabilities` 脱敏**：`GET /capabilities` 对 guest/user **返回同形状**但 `aragon` 子对象只保留 `{available, versionOk, canStructured, degradedReason}`，其余（`baseUrlHost/keyPresent/keySource/verified/verifiedAt/provider/model/extraAllowTools/errors`）置 `null/false/[]`；`engine`、`runtime` 子对象整体置 `null`；`limits.monthlyBudgetUsd` 置 `0`；追加顶层 `redacted: boolean`。**同形状**是为了不改 SSR 的兜底对象与既有 `Capabilities` 类型（`web/app/[symbol]/page.tsx` 的回退字面量依赖它）。前端 `EndpointBadge`/`KeyDrawer` 仅对 `can('engine.probe')` 渲染；非管理员只看到"引擎已就绪 / 示例模式"一个状态点。

**(h) 追问**：`cite`（`POST /reports/{id}/ask` mode=cite）公开，按 IP 限速；`deep` → `ask.deep` + 额度 `deep_ask`（`create_deep_ask()` 增参 `principal`，在其事务内 `reserve(...)`）；`ASK_DAILY_LIMIT`（每报告每日上限）改为"每 **(报告, 用户)**"计数（否则一个用户能耗尽全站对某份报告的追问名额）。`report_asks.user_id` 新列；`GET /reports/{id}/asks` 只返回**自己的**（admin 返回全部，guest 返回空列表 200）；`GET /asks/{id}`、`POST /asks/{id}/events-token`、`POST /asks/{id}/cancel` → 拥有者或管理员，否则 `404 not_found`（不是 403：不泄露"存在但不是你的"）。★**v2（RV-5）`GET /asks/{id}/events` 是例外，它按"令牌即授权"处理**：`EventSource` 不能带 `Authorization`，开发期跨源也不带 Cookie（§24.3.2(e)），所以 `events` 在请求里**拿不到可信主体**——若再要求"拥有者或管理员"，拥有者本人在开发期也会 404。裁决：**对象级校验只在 `events-token` 签发处做一次**（上面已 404 掉非拥有者），`events` 只验 `?token=`（§24.3.11：HMAC、60 s、**绑定 ask_id**，令牌里不含用户信息、不需要）；令牌错/过期/不属于该 ask → 既有的 `401`/`403` 行为不变。非拥有者**拿不到令牌**，所以拿不到流；令牌 60 秒过期且绑定单个 ask，泄露面可接受。`GET /runs/{id}/events` 同理（run 本就公开，令牌只是防滥用）。`user_id IS NULL` 的历史追问仅管理员可见。

**(i) 搜索/行情/标的/报告读取/导出/版本比较/块范围**：全部公开读（附录 A）。`POST /symbols/resolve` 对 guest 限速（会触发外部数据源与库写入，所以是"公开但贵"的那一类）。

### 24.3.10 审计日志（`audit_log`，只追加）

* 写入统一经 `services/audit_service.py::record(session, principal, action, target=None, detail=None)`：快照 `actor_name/actor_role/target_name`（用户改名/注销后历史仍可读）、`ip_prefix`、`request_id`（取自 `request_id_var`，与日志可串联）、`detail_json`（**白名单字段**，永不含密码/令牌/验证码答案）。
* ★**v2（RV-12）`request_id` 清洗**：`main.py` 现有中间件把入站 `X-Request-Id` 原样当作请求 ID（`main.py:277`），它会进日志与 `audit_log.request_id VARCHAR(16)`：超长（Postgres 报错）、含换行（日志注入）。改为：入站值必须匹配 `^[A-Za-z0-9_-]{1,32}$`，否则丢弃并重新生成 `uuid4().hex[:16]`；落审计时截断到 16。
* **不提供任何更新/删除接口**；保留期 `AUDIT_RETENTION_DAYS=365`，由 `_sweep_loop` 每天清一次过期行（与回收站清理同位置、同"失败只记日志"的纪律）。
* 动作词表（固定，前端筛选下拉直接用）：`auth.login.success|failure|lockout` `auth.logout` `auth.register` `auth.password.change|reset` `auth.session.revoke` `user.create|role.change|disable|enable|delete|unlock` `quota.grant` `quota.limit.set` `quota.request.create|approve|reject` `invite.create|revoke` `settings.update` `featured.add|remove` `schedule.create|update|delete` `report.manage_other`（管理员改/删了别人的报告）`run.cancel_other` `root.bootstrap` `root.password.cli_reset`。
* 登录失败**记审计但限频**：同 `(用户名, IP)` 60 秒内只记一条并带 `count`，防止暴力破解把审计表灌爆（讽刺的是，日志本身是攻击面）。

### 24.3.11 SSE 令牌

`services/sse_token.py` 现状：`API_TOKEN` 为空时 `verify_sse_token` **恒真**（E-1 的"本地优先"）。多用户后，`deep` 追问的事件流含用户私有内容，必须真校验。改动：

* 签名密钥 `signing_secret()` = `settings.session_secret`（env `SESSION_SECRET`；为空则首次启动生成 32 字节随机值并落 `settings["auth.session_secret"]`，重启不变）。`API_TOKEN` 不再参与签名。
* `AUTH_MODE=open` 时保留旧行为（占位令牌、恒真），其余模式**永远真校验**。
* 受影响的既有测试四处（E-1 取证）：`test_ask_api.py:266`、`test_reports_api.py:336/355/380`——它们 `monkeypatch api_token` 来"打开校验"；改为 monkeypatch `auth_mode='enforced'` 并设 `session_secret`。这是**有意的测试语义迁移**，不是回归，评审节点不要当成"改了测试让它过"。

### 24.3.12 离线 Service Worker 与缓存隔离（E-11，★最易漏）

问题：SW 缓存 `/[symbol]` 导航 HTML；页面一旦在服务端渲染了"当前用户"（用户名、额度芯片、自选），**下一个使用同一浏览器的人离线时会看到上一个人的 HTML**。裁决（三道，缺一不可）：

1. `web/middleware.ts`（Next Middleware，边缘轻量）对所有页面响应加头 `X-Aragon-Viewer: guest|user`（仅依据 `aragon_sid` Cookie **是否存在**判定，不做有效性校验）；`Vary: Cookie`。
2. `public/sw.js`：缓存名升到 `aragon-offline-v2`（`activate` 已会清理旧前缀）；导航策略改为：**仅当响应头 `X-Aragon-Viewer === 'guest'` 才写缓存**；登录态响应只走 network，不写缓存；离线回退时若有缓存则返回（那是某次 guest 渲染的公共内容，安全）。`/login`、`/register`、`/account`、`/admin*` 一律不缓存（路径黑名单，纵深防御）。
3. 登出 / 会话被服务端判失效时，前端向 SW `postMessage({type:'purge-user-cache'})`，SW 删除整个 `aragon-offline-` 命名空间下的导航缓存（`/_next/static` 的不可变资源保留）。

### 24.3.13 根管理员引导与离线恢复（X8）

* `services/bootstrap.py::ensure_root_admin(session)`（`main.lifespan` 里**迁移之后、`recover_interrupted` 之前**调用）：`SELECT … WHERE role='root'`；**不存在**则按 `ROOT_ADMIN_USERNAME`（默认 `jdqqjr`）/`ROOT_ADMIN_PASSWORD`（默认 `13519126390`）创建，`password_is_default = (pw == 内置默认值)`，`must_change_password = AUTH_FORCE_ROOT_PASSWORD_CHANGE and password_is_default`，审计 `root.bootstrap`；**已存在**则**不动**（环境变量只管"第一次"，否则运维改个 env 就能悄悄重置 root 密码，是后门）。若发现同名的非 root 账号（理论上被保留词挡住，但库可能被手工改过）→ **启动失败并说明**，不静默"接管"。
* 唯一性：`CREATE UNIQUE INDEX ux_users_root ON users(role) WHERE role='root'`（SQLite/PG 部分唯一索引）+ 服务层所有"改角色"路径对 `root` 一律 `403 root_protected`。
* **离线恢复 CLI**（忘记 root 密码是必然会发生的事，而邮件找回不存在）：`python -m app.cli reset-root-password [--password …]`（缺省交互式 `getpass`）、`python -m app.cli create-admin <username>`、`python -m app.cli unlock <username>`。仅能在**能访问数据库文件的机器**上执行（这就是它的授权模型），执行后写审计 `root.password.cli_reset`、吊销该用户全部会话、置 `must_change_password=1`。`api/README.md` 与根 `README.md` 新增"忘记 root 密码"一节。
* `start-all.ps1` 首次启动后打印一行提示："根管理员 jdqqjr 的初始密码见 .env.example / 需求说明；首次登录将要求修改"。**不**把密码打印到日志里。

### 24.3.14 管理后台的能力边界（X5）

对 `admin` 与 `root` 的差异一览（后端在服务层统一用 `security/objects.py::assert_can_manage_user(actor, target, action)` 校验，前端只做"隐藏不可用按钮"的体验层，**不作为安全依据**）：

| 动作 | admin 对 `user` | admin 对 `admin` | admin 对 `root` | root 对 `admin` | root 对 `user` |
| --- | --- | --- | --- | --- | --- |
| 查看详情/会话/额度 | ✓ | ✓（只读） | ✓（只读） | ✓ | ✓ |
| 停用/启用 | ✓ | ✗ | ✗ | ✓ | ✓ |
| 重置密码 | ✓ | ✗ | ✗ | ✓ | ✓ |
| 踢下线（吊销会话） | ✓ | ✗ | ✗ | ✓ | ✓ |
| 调整额度/赠送 | ✓ | ✗ | ✗ | ✓ | ✓ |
| 解锁 | ✓ | ✗ | ✗ | ✓ | ✓ |
| 删除（匿名化） | ✓ | ✗ | ✗ | ✓ | ✓ |
| **任命为 admin / 降为 user** | ✗ | ✗ | ✗ | ✓ | ✓ |
| 创建账号（角色 user） | ✓ | — | — | ✓（可直接建 admin） | ✓ |

任何人对 `root` 的写操作（除 root 本人改自己的密码/显示名）一律 `403 root_protected`；对自己执行"停用/删除/降级"一律 `422 self_action_forbidden`（防自杀式误操作）。**所有"危险动作"前端需要二次确认**，其中**删除用户、降级 admin、重置 admin 密码**要求**输入目标用户名确认**（GitHub 式 type-to-confirm）。

### 24.3.15 前端架构与交互设计（X6）

#### a) 状态与数据流

* 单一真源 `['me']`（TanStack Query）：`Me = { authMode, authenticated, user|null, permissions[], quota|null }`。根 `layout.tsx`（服务端）用 `serverApi().me()` 取一次（失败 → 视为 guest，**不让站点因 API 冷启动而白屏**），经 `Providers initialMe` 作为 `initialData` 注入；`staleTime: 60s`，窗口聚焦不重取（沿用 `Providers` 既有默认），登录/登出/改密/申请额度/发起生成后 `invalidateQueries(['me'])`。
* `lib/auth.ts`：`useMe()`、`useCan(permission)`、`useRequireAuth(intent)`（未登录时打开 `AuthDialog` 并记住"登录后想做什么"，登录成功后**回到原页面原位置**，不跳走）。
* 全局 401 处理：`request()` 在浏览器里收到 `401 unauthorized(reason!='no_session')`（即**会话失效**而非"本来就没登录"）→ 清 `['me']`、toast「登录已失效，请重新登录」、通知 SW purge、打开 `AuthDialog`；**不**自动整页刷新（会丢掉用户正在读的报告位置）。
* `403 password_change_required` → 全局重定向到 `/account/password?forced=1`（独立全屏页，只有改密表单与登出）。

#### b) 页面与路由

| 路由 | 访问 | 内容 |
| --- | --- | --- |
| `/login` | 公开（已登录则跳 `next` 或 `/`） | 居中卡片：用户名 / 密码（显示切换、CapsLock 提示）/ 记住我 / 验证码（仅被要求时出现）/ 登录；底部"没有账号？注册"；`?next=` 仅允许**站内相对路径**（`^/(?!/)` 且不含 `\`、不含协议，防开放重定向） |
| `/register` | 公开（`registrationMode=closed` 时显示"注册已关闭"与联系管理员提示） | 用户名（实时规则提示）/ 密码 + **强度条与规则清单** / 确认密码 / 验证码（图 + 换一张）/ 邀请码（折叠，`invite` 模式下展开且必填）/ 勾选"已阅读风险提示"（与首页页脚同一句 AI 免责声明）/ 注册 |
| `/account` | 登录 | 三个标签：**概览**（资料、角色徽标、额度环 + 今日/赠送、申请额度）、**安全**（改密码、活动会话列表 + "退出其他设备"）、**额度流水**（最近 50 条：时间/事件/变化/余额说明） |
| `/account/password` | 登录 | 强制改密全屏页 |
| `/admin` 及 `/admin/{users,invites,quota,audit,settings}` | `admin.*`（服务端 `layout.tsx` 校验：guest → `redirect('/login?next=…')`；user → 渲染"无权访问"（403 视图，**不是** 404，让用户知道是权限问题）） | 左侧导航 + 右侧内容，见 d) |
| `/`、`/reports`、`/{symbol}` | 公开 | 头部右上新增 `UserMenu`；工作台生成按钮三态（见 c） |

#### c) 工作台里的"游客 → 用户 → 额度"体验（最关键的一条用户旅程）

`GenerateButton` 由 `useMe()` 决定形态（**文案与可点击性是产品，不是装饰**）：

| 状态 | 按钮 | 点击 | 附加 |
| --- | --- | --- | --- |
| guest | `登录后生成投研报告`（主色保留，图标换 `LogIn`） | 打开 `AuthDialog`（登录/注册两个标签，保留 `intent: generate:<symbol>`）；成功后**自动回到该标的并聚焦按钮**，不自动发起生成（花钱的动作必须由人二次点击） | 副文案 `游客可浏览全部研报` |
| user，`remaining > 0` | `生成投研报告` | 照旧 | 按钮旁 `QuotaChip`：`今日剩余 2/3`；`≤1` 时转 `--warn` 色；悬停 Tooltip：`日额度 2/3 + 赠送 5 · 明日 00:00 重置` |
| user，`remaining == 0` | 按钮禁用（`aria-disabled`，**仍可聚焦**，聚焦/悬停说明原因） | 点击（禁用态下的点击区域是 `QuotaChip`）打开 `QuotaExceededDialog` | 对话框：已用 3/3、重置倒计时、`申请额外额度`（理由输入 ≤200 字 + 提交）、已有 pending 申请则显示状态；**不是一个红色 toast** |
| admin/root | `生成投研报告`，无芯片（或显示 `不限次`） | 照旧 | — |
| 服务端兜底 `429 quota_exceeded`（并发/多标签页导致预检过时） | 同上对话框 | — | `useGenerationStream` 在 `createReport` 的 catch 里识别该码，更新 `['me']` 并弹出对话框，**不进入**"生成失败"卡片（那是 run 级失败，这是准入级拒绝） |

`runErrors.ts` 的 `RUN_ERROR_ACTIONS` **不改**（它约束的是 run 级码集合，AC-104 的双向集合相等不能被 HTTP 码污染）；准入级错误码在新文件 `lib/authErrors.ts` 里单独成表（`unauthorized / forbidden / quota_exceeded / account_disabled / password_change_required / rate_limited / csrf_rejected` → 文案与动作）。

#### d) 管理后台界面（沿用工作台的暗色 Supabase 控制台语言）

* **布局**：`AdminShell`——顶部 52px 头（品牌 + 面包屑 + `UserMenu`），左 200px 导航（概览 / 用户 / 邀请码 / 额度申请〔带红点数字〕 / 审计日志 / 系统设置〔root 才显示"预算"区块〕），右侧内容区 `max-w-[1120px]`。窄屏（< 900px）导航收成顶部横向 Tabs（沿用 `Tabs` 原语）。仅 `/admin/**` 路由使用，**不进工作台首屏包**。
* **概览**：6 张指标卡（总用户 / 近 7 日新增 / 今日生成次数 / 今日成功率 / 待处理额度申请 / 被锁定账号）、近 14 日"注册 & 生成"双折线（复用 `echarts`，已在依赖里；**`next/dynamic` 懒加载**）、"近 7 日用量 Top 5 用户"、"最近 8 条审计"。所有数字来自**一次** `GET /admin/overview`（后端 5 条聚合 SQL；10 万用户规模下 p95 < 80 ms，AC-199）。
* **用户**：表格（用户名+显示名 / 角色徽标 / 状态点 / 今日用量 `2/3` + 赠送 / 最近登录 / 注册时间 / ⋯ 行菜单）；顶部搜索（用户名前缀）+ 角色/状态筛选；**游标分页**（沿用 §23 的 `before` 游标口径，键 `(created_at, id)`）；点行开右侧 480px **用户抽屉**：资料、额度调整（`+N 赠送`、`日额度覆盖`）、活动会话（可逐条吊销）、近 20 次生成（标的/状态/时间）、危险区（停用并填原因 / 重置密码〔生成 12 位临时密码，**仅显示一次**，带"复制"按钮〕/ 删除〔type-to-confirm〕/ 任命或降级〔仅 root 可见〕）。
* **邀请码**：创建（次数 1–100 / 有效期 1–90 天或永久 / 附带赠送额度 0–20 / 备注）→ 表格显示 `XXXX-XXXX`（等宽字体）、已用/上限、状态、复制按钮、撤销。
* **额度申请**：待处理队列（用户、理由、申请时间、该用户近 7 日用量），行内「通过 +N（默认 3）」「拒绝…（必填理由）」；已处理标签页只读。
* **审计日志**：时间 / 操作者 / 动作（中文化标签）/ 对象 / IP / 详情（可展开 JSON 的白名单字段）；筛选（动作、操作者、时间范围）；游标分页；**只读**。
* **系统设置**：注册模式（开放 / 仅邀请 / 关闭，三选一单选卡片，各附一句后果说明）、默认日额度（生成 / 深度追问）、新用户赠送额度、★v2：**全站日生成总量上限**（附说明"月度预算默认不设上限，这是唯一的人头护栏"）；root 额外看到"月度预算"（复用既有 `POST /cost/budget`）与"精选自选列表"管理。保存按钮分区域，每次保存写审计，成功 toast「已保存 · 已写入审计」。
* **空态/错误态/加载态**：每个表格有骨架屏（沿用 `Skeleton`）、空态一句话 + 主操作、错误态显示 `requestId`（便于排障，沿用 `ApiError.requestId`）。

#### e) 设计一致性与无障碍（"与当前项目统一"的可检查口径）

* **只用既有 token 与原语**：颜色/圆角/阴影/字号走 `globals.css` 变量；按钮 `Button`、输入 `Input`、浮层 `Dialog`、徽标 `Badge`、提示 `Toast/Tooltip`；**不新增 UI 依赖**。新增的 8 个视觉原件（`PasswordField`、`PasswordStrength`、`CaptchaField`、`QuotaChip`、`QuotaRing`、`RoleBadge`、`StatusDot`、`ConfirmDialog`）各自带 vitest + a11y 断言。
* **角色徽标配色**：root＝`--accent` 实心 + 皇冠图标（`lucide Crown`）；admin＝`--info`；user＝中性；guest 不显示徽标。状态点：active＝`--accent`、disabled＝`--text-tertiary`、locked＝`--warn`。**不靠颜色单独传达信息**（旁带文字/图标）。
* **表单**：每个输入有可见 `<label>`；`autocomplete` 正确（`username` / `current-password` / `new-password`），以便密码管理器工作；错误用 `aria-live="polite"` 区域 + `aria-describedby` 关联；提交失败后**焦点移到第一个非法字段**；`Enter` 提交；按钮提交中 `loading` 态（沿用 `Button.loading`，防双击）；验证码错 → 自动换图并清空输入、焦点回到验证码框。
* **键盘**：命令面板（⌘K）新增「账号」「管理后台」「退出登录」三项（按权限出现，沿用 `onOpenLibrary` 的"不传即不出现"模式）；所有对话框 Esc 关闭并还原焦点（Radix 默认）。
* **文案**：全部进 `web/lib/copy.ts` 的 `copy.auth / copy.admin / copy.quota` 三个新命名空间；语气沿用现有（克制、陈述事实、给下一步动作；不用感叹号）。**错误话术不泄露内部**：登录失败统一"用户名或密码不正确"。
* **响应式**：登录/注册卡片 `w-[min(92vw,400px)]`；后台表格在 < 900px 折叠次要列（最近登录、注册时间）；抽屉在窄屏变全屏 Sheet。
* **首屏预算（AC-35）**：`UserMenu` + `QuotaChip` 是首屏唯二新增静态代码，目标合计 ≤ 3 kB gzip；`AuthDialog`、`QuotaExceededDialog` 用 `next/dynamic`；`/login` `/register` `/account` `/admin/**` 是独立路由 chunk。`web/scripts/check-bundle.*` 的阈值**不得上调**（沿用 §11.2 口径：逼人做懒加载选择）。

---

## 24.4 文件 / 模块变更计划

> 约定：**新增**＝新建；**改**＝在既有文件上最小改动；括号内是该文件在哪个里程碑落地（§24.8）。所有路径相对仓库根。

### 24.4.1 后端 `api/`

| 文件 | 动作 | 一句话意图 |
| --- | --- | --- |
| `app/security/__init__.py` | 新增 | 包说明与依赖方向声明（services 可 import security，反之不可）（M45） |
| `app/security/principal.py` | 新增 | `Principal` dataclass、`GUEST`/`SYSTEM` 常量，纯数据无 HTTP（M45） |
| `app/security/permissions.py` | 新增 | 读 `contracts/permissions.json` 展开角色→权限 `frozenset`；`PASSWORD_CHANGE_ALLOWED`（M45） |
| `app/security/policy.py` | 新增 | `POLICIES: dict[(method, path_format), Policy]` 逐路由策略表 + `verify_against_app(app)`（M46） |
| `app/security/deps.py` | 新增 | `get_principal`、`enforce`、`require(permission)`、客户端 IP 解析、Cookie 读写助手（M46） |
| `app/security/objects.py` | 新增 | 对象级纯函数：`assert_can_manage_report/ask/run/user`、`root_protected`、`self_action_forbidden`（M46） |
| `app/security/passwords.py` | 新增 | scrypt 哈希/校验/`needs_rehash`、`validate_new_password`、假哈希计时对齐；★v2：`BoundedSemaphore(4)` KDF 并发上限（RV-7）（M45） |
| `app/security/weak_passwords.py` | 新增 | 弱口令表（≈50 条）与序列检测（M45） |
| `app/security/ratelimit.py` | 新增 | 进程内滑动窗口限速器 + `RATE_LIMITS` 表 + ★v2 `ShadowThrottle`（不存在用户名的失败计数，RV-3）；限速键 IPv4 精确 / IPv6 /64（RV-13）（M47） |
| `app/security/csrf.py` | 新增 | Origin 校验中间件；★v2：只比 netloc、忽略 scheme（RV-2）（M46） |
| `app/models/user.py` | 新增 | `User`、`UserSession`、`InviteCode`、`AuditLog`、`UserWatchlistItem`（M45） |
| `app/models/quota.py` | 新增 | `QuotaLedger`、`QuotaRequest`（M48） |
| `app/models/__init__.py` | 改 | 导出新模型（供 Alembic 与测试建表看见）（M45） |
| `app/models/run.py` | 改 | `Run.created_by`（可空 String(32)，索引）（M45） |
| `app/models/report.py` | 改 | `Report.created_by`（同上）（M45） |
| `app/models/ask.py` | 改 | `ReportAsk.user_id`（同上）（M45） |
| `app/models/schedule.py` | 改 | `Schedule.owner_id`（同上）（M45） |
| `app/models/cost.py` | 改 | `CostLedger.user_id`（同上）（M45） |
| `app/db/migrations/versions/0007_users_auth.py` | 新增 | 唯一一次迁移：建 7 张新表 + 5 个 `add_column` + 部分唯一索引；`downgrade()` 完整对称（M45） |
| `app/config.py` | 改 | 新增 `auth_*` / `quota_*` / `cookie_*` / `trust_proxy` / `root_admin_*` / `session_secret` / `rate_limit_enabled` 设置（§24.6.4）（M45） |
| `app/errors.py` | 改 | 新增 18 个错误类（§24.5.4）；`Unauthorized` 增 `reason`（M45） |
| `app/logging.py` | 改 | 新增 `RedactFilter`（M46） |
| `app/main.py` | 改 | ①去掉整站 Bearer 闸门，改为 CSRF 中间件 + 响应头中间件；②CORS `allow_credentials=True` + 通配守卫；③`include_router(..., dependencies=[Depends(enforce)])`；④新增 `auth`/`admin`/`me` 路由；⑤lifespan 里 `ensure_root_admin` + 首次对账；⑥`_sweep_loop` 增 `reconcile_refunds()` 与 `purge_audit()`；⑦启动期策略表校验；⑧`AUTH_MODE=open` 横幅；★v2：⑨`X-Request-Id` 入站校验（RV-12）⑩`TRUST_PROXY=0` 见到 `X-Forwarded-For` 的一次性告警（RV-14）⑪启动期集合校验的豁免规则="非 `/api/v1` 前缀"（RV-4）（M46/M48） |
| `app/cli.py` | 新增 | `reset-root-password` / `create-admin` / `unlock`（M52） |
| `app/services/user_service.py` | 新增 | 建/查/改/停/启/删（匿名化）用户、角色变更、解锁、用户名规则与保留词（M45） |
| `app/services/session_service.py` | 新增 | 建/校验/滑动/吊销会话、每用户 10 会话上限、节流写 `last_seen_at`（M45） |
| `app/services/auth_service.py` | 新增 | 注册/登录/登出/改密编排：把验证码、密码、锁定、会话、审计串起来（M47） |
| `app/services/challenge.py` | 新增 | `ChallengeProvider` 协议 + 注册表 + `ChallengeUnavailable`（M47） |
| `app/services/captcha.py` | 新增 | Pillow 图形验证码与进程内仓库（M47） |
| `app/services/quota_service.py` | 新增 | `status/precheck/reserve/reconcile_refunds/grant/set_limit`、每用户互斥锁、`day_key`（M48） |
| `app/services/quota_requests.py` | 新增 | 额度申请的创建/列出/处理（M48） |
| `app/services/invite_service.py` | 新增 | 邀请码生成（`XXXX-XXXX`，去歧义字符集）、原子核销、撤销（M47） |
| `app/services/audit_service.py` | 新增 | `record()`、查询（游标）、失败限频合并、过期清理（M46） |
| `app/services/settings_service.py` | 新增 | 对 `settings` KV 的**带类型读写**（注册模式/默认额度/赠送额度…），带校验与默认值（M48） |
| `app/services/bootstrap.py` | 新增 | `ensure_root_admin()`（M45） |
| `app/services/admin_overview.py` | 新增 | 概览的 5 条聚合 SQL（M50） |
| `app/services/watchlist_service.py` | 新增 | 个人自选/精选的读写与"首次播种"（M49） |
| `app/services/sse_token.py` | 改 | 签名密钥换 `signing_secret()`；`open` 模式保留旧行为（M46） |
| `app/services/run_manager.py` | 改 | `start(..., actor=None)`：额度预留位于 422 之后、`mkdir` 之前；写 `created_by`；`_finalize_failure` 尾部定向对账（M48） |
| `app/services/report_service.py` | 改 | `create_version` 写 `created_by`；`to_summary` 追加 `author/canManage/canCurate`；`set_flags` 对 `pinned/setLatest` 要求 `manage_any`（RV-10）；回收站/星标按拥有者过滤；`set_flags/delete/restore` 前置对象级校验（M49） |
| `app/services/ask_service.py` | 改 | `create_deep_ask(..., principal)` 内预留；`daily_count` 增 `user_id`；列表按拥有者过滤（M49） |
| `app/services/cost_service.py` | 改 | 记账时写 `user_id`（M48） |
| `app/services/scheduler.py` / `scheduler_ticker.py` | 改 | 触发 run 时 `actor=SYSTEM`；创建时写 `owner_id`（M49） |
| `app/routers/auth.py` | 新增 | `/auth/*`（M47） |
| `app/routers/me.py` | 新增 | `/me`、`/me/quota`、`/me/quota-ledger`、`/me/quota-requests`（M48） |
| `app/routers/admin.py` | 新增 | `/admin/*`（M50）——**一个文件一个路由器**，内部再按资源分段，避免"路由器 import 路由器" |
| `app/routers/{reports,runs,asks,watchlist,schedules,cost,agent,capabilities,symbols}.py` | 改 | 取 `principal`、调对象级校验、`Capabilities` 脱敏、`RunOut` 脱敏、202 体追加 `quota`（M49） |
| `app/schemas/auth.py` / `me.py` / `admin.py` | 新增 | 请求/响应 Pydantic 模型（M47–M50） |
| `app/schemas/report.py` | 改 | `ReportSummary` 追加 `author`、`canManage`、`canCurate`（M49） |
| `app/schemas/capabilities.py` | 改 | 追加顶层 `redacted: bool = False`（M49） |
| `pyproject.toml` | 改 | 依赖加 `"Pillow>=10.1"`（M47） |
| `.env.example`（仓库根） | 改 | 新增 §24.6.4 的全部变量并逐条注释；**`ROOT_ADMIN_PASSWORD` 行写明"仅首次引导读取"**（M52） |
| `tests/conftest.py` | 改 | `AUTH_MODE=open`；新增 `auth_client`/`login_as`/`make_user`/`frozen_now` fixture（M45） |
| `tests/test_*.py`（新增 14 个，见 §24.7） | 新增 | 各里程碑对应测试 |
| `tests/test_ask_api.py:266`、`tests/test_reports_api.py:336/355/380` | 改 | 签名密钥来源迁移（§24.3.11，有意语义迁移）（M46） |
| `tests/test_report_library_api.py:100` | 改 | `ReportSummary` 23 → 26 键（M49） |
| `tests/test_capabilities_api.py` | 改 | 新增"脱敏同形状"断言；既有 17 键精确相等断言**不动**（脱敏不改 `features` 的键集）（M49） |

### 24.4.2 前端 `web/`

| 文件 | 动作 | 一句话意图 |
| --- | --- | --- |
| `middleware.ts` | 新增 | `X-Aragon-Viewer` 头；`/admin`、`/account` 无 Cookie 时重定向 `/login?next=`（M51） |
| `app/layout.tsx` | 改 | 服务端取 `me` → `Providers initialMe`（失败降级 guest）（M51） |
| `app/login/page.tsx`、`app/register/page.tsx` | 新增 | 认证页（M51） |
| `app/account/page.tsx`、`app/account/password/page.tsx` | 新增 | 账号页与强制改密页（M51） |
| `app/admin/layout.tsx` + `page.tsx` + `users/` `invites/` `quota/` `audit/` `settings/` 各 `page.tsx` | 新增 | 管理后台（M52） |
| `app/page.tsx`、`app/reports/page.tsx`、`app/[symbol]/page.tsx` | 改 | `api` → `await serverApi()`；首页头部加 `UserMenu`（M51） |
| `lib/api.ts` | 改 | 重构为 `createApi(ctx)` 工厂；浏览器 `credentials:'include'`；回环别名对齐；追加 `auth/me/admin/quota` 端点；★v2：**删除** `NEXT_PUBLIC_API_TOKEN`（`:81`）与 `authHeaders()`（`:93-95`，E-15/RV-1）；服务端实例一律 `no-store`、忽略 `revalidate`（RV-6）（M51） |
| `lib/sse.ts` | 改 | ★v2：不再以"是否配置了 API_TOKEN"决定要不要取 SSE 令牌——**一律**先 `POST …/events-token`（`enforced` 模式下令牌是真签名；`open` 模式服务端返回占位令牌，行为不变）（M51） |
| `lib/serverApi.ts` | 新增 | `import 'server-only'` 的 Cookie 转发（M51） |
| `lib/auth.ts` | 新增 | `useMe / useCan / useRequireAuth / safeNext()`（M51） |
| `lib/permissions.ts` | 新增 | 读 `contracts/permissions.json`；`can(me, perm)`（M51） |
| `lib/authErrors.ts` | 新增 | 准入级错误码 → 文案/动作表（与 `runErrors.ts` 分表，M51） |
| `lib/types.ts` | 改 | 追加 `Me/User/QuotaStatus/AdminUser/Invite/AuditRow/…`；`ReportSummary` 加 `author/canManage/canCurate`；`Capabilities.redacted?`（M51） |
| `lib/copy.ts` | 改 | 新增 `copy.auth / copy.admin / copy.quota`（M51/M52） |
| `lib/offline.ts` + `public/sw.js` | 改 | SW 缓存名 v2、仅缓存 guest 响应、`purge-user-cache` 消息（M51） |
| `components/shell/Providers.tsx` | 改 | 接收 `initialMe`；挂 `MeProvider`；监听 401 全局事件（M51） |
| `components/auth/{UserMenu,QuotaChip,AuthDialog,LoginForm,RegisterForm,CaptchaField,PasswordField,PasswordStrength,QuotaExceededDialog,QuotaRing,RequireAuth}.tsx` | 新增 | 认证与额度 UI（M51） |
| `components/admin/{AdminShell,OverviewCards,UsersTable,UserDrawer,CreateUserDialog,InvitesPanel,QuotaRequestsPanel,AuditTable,SettingsForm,ConfirmDialog,RoleBadge,StatusDot}.tsx` | 新增 | 后台 UI（M52） |
| `components/generation/GenerateButton.tsx` | 改 | 三态（guest / 有额度 / 无额度）（M51） |
| `components/generation/useGenerationStream.ts` | 改 | `createReport` 的 catch 识别 `quota_exceeded/unauthorized`（M51） |
| `components/shell/AppShell.tsx` | 改 | 头部加 `UserMenu` + `QuotaChip`；`EndpointBadge`/`KeyDrawer` 按 `can('engine.probe')` 裁剪（M51） |
| `components/shell/{SymbolRail,WorkspaceClient,CommandPalette}.tsx` | 改 | guest 隐藏自选写操作；报告管理入口按 `report.canManage`；命令面板新增账号/后台/退出（M51） |
| `components/library/*` | 改 | 回收站标签仅登录可见；行内操作按 `canManage`；置顶/设最新按钮按 `canCurate`（RV-10）（M51） |
| `components/schedule/ScheduleDialog.tsx`、`components/cost/CostPanel.tsx` | 改 | 入口仅 `can('schedule.manage')` / `can('cost.read')`（M51） |
| `next.config.ts` | 改 | 安全响应头（`X-Frame-Options: DENY`、`Referrer-Policy: same-origin`、`X-Content-Type-Options: nosniff`）（M51） |
| `tests/*`（新增，见 §24.7） | 新增 | vitest 与 Playwright（M51/M52） |
| `tests/e2e/*.spec.ts`、`playwright.config.ts` | 改 | 既有 e2e 以 `AUTH_MODE=open` 的 webServer 运行；新增 `auth.spec.ts`/`admin.spec.ts` 用 `enforced`（独立 project）（M52） |

### 24.4.3 仓库级

| 文件 | 动作 | 一句话意图 |
| --- | --- | --- |
| `contracts/permissions.json` | 新增 | 角色→权限的单一真源（§24.3.4）（M45） |
| `README.md` | 改 | 新增「用户与权限」「忘记 root 密码」「回环主机对齐」三节；环境变量表追加（M52） |
| `docs/plans/user-auth-permissions/spec.md` | 新增 | 本文 |
| `deploy/systemd/*`、`deploy/ops/*.sh` | 改 | 单元里 `Environment=TRUST_PROXY=1`、`COOKIE_SECURE=1`；`status.sh` 增一行 `auth: enforced/open`（M52） |
| `deploy/nginx/aragon-trading.conf` | 核对（预计不改） | `location /api/` 已同源代理且透传 `X-Forwarded-*`；需确认 `proxy_pass_header Set-Cookie` 默认可用，并**不要**加 `proxy_cache`（M52） |
| `scripts/*`、`start-all.ps1` | 改（小） | 首启后提示根管理员首登改密；`-Mock` 不影响 `AUTH_MODE`（M52） |

---

## 24.5 接口设计

所有响应走既有错误信封（E-9）。时间一律 UTC ISO `…Z`（`db.base.iso()`）。`✱` = 需要登录；`A` = admin 及以上；`R` = 仅 root。**Cookie 会话是唯一的用户认证方式**，下表不再重复。

### 24.5.1 `/api/v1/auth`（公开认证面）

| 方法 路径 | 权限 | 请求 → 响应 | 说明 |
| --- | --- | --- | --- |
| `GET /auth/config` | 公开 | → `{authMode, registrationMode:'open'\|'invite'\|'closed', registrationOpen, passwordPolicy:{min:8,max:128,minClasses:2}, usernamePolicy:{min:3,max:20,pattern:'^[A-Za-z0-9_]+$'}, captchaKind:'captcha', forceRootPasswordChange:boolean}` | 前端渲染注册页规则提示的唯一来源（规则不在前端写死第二份） |
| `GET /auth/captcha?purpose=register\|login` | 公开·限速 | → `{challengeId, image:'data:image/png;base64,…', width:160, height:56, expiresIn:300}` | `Cache-Control: no-store`；503 `captcha_unavailable` |
| `POST /auth/register` | 公开·限速 | `{username, password, captchaId?, captchaAnswer?, inviteCode?, displayName?, website?(蜜罐), issuedAt?}` → `201 Me` + `Set-Cookie` | §24.3.6 |
| `POST /auth/login` | 公开·限速 | `{username, password, remember?:boolean, captchaId?, captchaAnswer?}` → `200 Me` + `Set-Cookie` | §24.3.7；`422 captcha_required` 的 `detail.challenge` 内带新验证码图 |
| `POST /auth/logout` | 公开（幂等） | → `204` + 清 Cookie | 无会话也返回 204；审计 `auth.logout` 仅在确有会话时写 |
| `GET /auth/me` | 公开 | → `Me`（guest 返回 `{authenticated:false,user:null,permissions:[...guest],quota:null}`） | **永不 401**：让前端用同一个调用初始化 |
| `POST /auth/password` | ✱ | `{currentPassword, newPassword}` → `204` | 成功后吊销**除当前会话外**的全部会话；清 `must_change_password`/`password_is_default`；强制改密态下唯一可用的写接口之一 |
| `GET /auth/sessions` | ✱ | → `{items:[{id, current, createdAt, lastSeenAt, ip, userAgent}]}` | `ip` 为 /24 截断 |
| `DELETE /auth/sessions/{id}` | ✱ | → `204` | 只能删自己的；删当前会话等价登出 |
| `POST /auth/sessions/revoke-others` | ✱ | → `{revoked:n}` | |
| `PATCH /auth/profile` | ✱ | `{displayName?: string(1..40)\|null}` → `Me` | 用户名不可改（审计与外键语义依赖它） |

`Me` 形状：

```ts
interface Me {
  authMode: 'enforced' | 'open';
  authenticated: boolean;
  user: null | {
    id: string; username: string; displayName: string | null;
    role: 'user' | 'admin' | 'root'; status: 'active';
    createdAt: string; lastLoginAt: string | null;
    mustChangePassword: boolean; passwordIsDefault: boolean;
  };
  permissions: string[];            // 展开后的权限串（前端 can() 用，**不是**授权依据）
  quota: null | { report: QuotaBucket; deepAsk: QuotaBucket; tz: string };
}
interface QuotaBucket {
  limit: number | null;             // null = 不限
  usedToday: number;                // 当日净消耗
  bonus: number;                    // 赠送余额
  remaining: number | null;         // null = 不限
  resetAt: string;                  // 下一个 QUOTA_TZ 零点（UTC ISO）
  pendingRequest: null | { id: string; createdAt: string };
}
```

### 24.5.2 `/api/v1/me`（个人额度）

| 方法 路径 | 权限 | 说明 |
| --- | --- | --- |
| `GET /me/quota` | ✱ | → `QuotaStatus`（读前先对该用户做定向对账） |
| `GET /me/quota-ledger?limit=50&before=` | ✱ | → `{items:[{id, at, resource, delta, reason, source, refId, note}], nextCursor}`；`reason` 用中文标签表映射在前端 |
| `POST /me/quota-requests` | ✱ `quota.request` | `{resource:'report'\|'deep_ask', amount:1..20, reason:string(1..200)}` → `201 {id,status:'pending',…}`；已有 pending → `409 quota_request_pending` |
| `GET /me/quota-requests` | ✱ | → 自己的申请（含处理结果与备注） |

### 24.5.3 `/api/v1/admin`（管理后台）

| 方法 路径 | 权限 | 请求 → 响应 / 说明 |
| --- | --- | --- |
| `GET /admin/overview` | A `admin.overview.read` | → `{users:{total,byRole,active7d,disabled,locked}, signups:[{day,count}×14], runs:{today,succeeded,failed,successRate}, runsDaily:[{day,count}×14], quota:{consumedToday,refundedToday}, topUsers7d:[{userId,username,runs}×5], pendingQuotaRequests, recentAudit:[…8]}` |
| `GET /admin/users?q=&role=&status=&limit=50&before=` | A `admin.users.read` | → `{items:[AdminUser], total, nextCursor}`；`q` 用户名前缀。★v2（RV-8）**不用 `LIKE`**：SQLite 默认的 `LIKE` 大小写不敏感，而 `ux_users_username_lc` 是 BINARY 排序规则的索引，二者不匹配 ⇒ 优化器**不会**走索引（`LIKE` 前缀优化只在 `PRAGMA case_sensitive_like=ON` 或列声明 `COLLATE NOCASE` 时成立），AC-195 的 `EXPLAIN QUERY PLAN` 断言按 v1 写法**不可达**；且 `q` 里的 `%`/`_` 会被当通配符。改为范围查询：`username_lc >= :q AND username_lc < :q_hi`，其中 `q = q.strip().lower()`（限 1–20 个 `[a-z0-9_]` 字符，其它字符 → 空结果而非报错），`q_hi = q[:-1] + chr(ord(q[-1]) + 1)`。显示名**不参与**前缀检索（`display_name` 无索引；要搜显示名属于后续增强） |
| `POST /admin/users` | A `admin.users.manage` | `{username, role?:'user'\|'admin'(仅 root 可指定 admin), password?, displayName?, bonusCredits?}` → `201 {user, temporaryPassword?}`；缺省 `password` 则生成 12 位临时密码（**只在本响应出现一次**）并置 `must_change_password` |
| `GET /admin/users/{id}` | A | → `AdminUserDetail`（含 `quota`、近 20 次 run、活动会话、`failedLogins`、`lockedUntil`） |
| `PATCH /admin/users/{id}` | A（`role` 字段 R） | `{displayName?, role?, status?:'active'\|'disabled', disabledReason?}`；矩阵见 §24.3.14 |
| `POST /admin/users/{id}/reset-password` | A（对 admin 目标 R） | → `{temporaryPassword}`（一次性展示）+ 吊销该用户全部会话 + 置 `must_change_password` |
| `POST /admin/users/{id}/revoke-sessions` | A | → `{revoked:n}` |
| `POST /admin/users/{id}/unlock` | A | → `204`（清 `failed_logins/locked_until`） |
| `POST /admin/users/{id}/quota` | A `admin.quota.manage` | `{resource, grant?: -50..50 非零, dailyLimit?: int\|null(跟随默认)\|-1(不限), note?}` → `QuotaStatus` |
| `DELETE /admin/users/{id}` | A | → `204`（匿名化，§24.3.1）；body 或 query 需 `confirm=<username>`（后端也校验，不只靠前端） |
| `GET /admin/invites` · `POST /admin/invites` · `DELETE /admin/invites/{id}` | A `admin.invites.manage` | 创建 `{maxUses:1..100, expiresInDays:1..90\|null, bonusCredits:0..20, note?}` → `{id, code:'K7QM-X2PD', …}`；DELETE = 撤销（置 `disabled_at`，已注册用户不受影响） |
| `GET /admin/quota-requests?status=pending` | A | → `{items:[{id,user,resource,amount,reason,createdAt,userRuns7d}], total}` |
| `POST /admin/quota-requests/{id}/resolve` | A | `{decision:'approve'\|'reject', grant?:1..20, note?(reject 必填)}` → 申请对象；approve 同事务内写赠送账本行 |
| `GET /admin/audit?action=&actor=&target=&from=&to=&limit=50&before=` | A `admin.audit.read` | → `{items:[AuditRow], nextCursor}` |
| `GET /admin/settings` · `PATCH /admin/settings` | A `admin.settings.manage` | `{registrationMode?, defaultDailyReport?:0..100, defaultDailyDeepAsk?:0..200, signupBonus?:0..20, siteDailyCap?:0..10000}`（★v2：`siteDailyCap` = 全站普通用户日生成总量上限，`0` = 不限，RV-11）；每个字段独立校验，非法 → `422 invalid_setting(detail.field)` |
| `GET/POST/DELETE /admin/featured-watchlist` | A `admin.featured.manage` | 维护"平台精选"（旧 `watchlist` 表）；POST `{instrumentId}`；DELETE `/admin/featured-watchlist/{instrumentId}` |

`AdminUser`：`{id, username, displayName, role, status, createdAt, lastLoginAt, quota:{report:QuotaBucket-lite}, locked:boolean, invitedBy?:string}`。**列表页的 `quota` 用一条 `GROUP BY user_id` 聚合 SQL 对当前页的用户批量算**，不得逐行 N+1。

### 24.5.4 新增错误码（与 `errors.py` 一一对应；前端 `authErrors.ts` 逐码有文案）

| code | HTTP | 触发 | `detail` |
| --- | --- | --- | --- |
| `unauthorized`（既有） | 401 | 需要登录 / 会话失效 | `{reason:'no_session'\|'expired'\|'revoked'\|'disabled'}` |
| `forbidden` | 403 | 已登录但权限不足 | `{permission}` |
| `invalid_credentials` | 401 | 用户名或密码不对（含不存在） | `{captchaRequired?:boolean}` |
| `account_disabled` | 403 | 密码正确但账号被停用 | `{reason?}` |
| `account_locked` | 423 | 连错 5 次 | `{retryAfterMs}` |
| `password_change_required` | 403 | 强制改密态访问其它接口 | — |
| `csrf_rejected` | 403 | 不可信 Origin 的写请求 | `{origin}` |
| `rate_limited` | 429 | 任一限速桶 | `{scope, retryAfterMs}`（同时 `Retry-After` 头，秒） |
| `quota_exceeded` | 429 | 额度不足（预检或预留）；★v2：或全站日总量已满 | `{resource, scope:'user'\|'site', limit, usedToday, bonus, resetAt, canRequest:boolean}`（`scope='site'` 时 `canRequest=false`） |
| `captcha_required` | 422 | 登录需要验证码 | `{challenge:{challengeId,image,…}}` |
| `captcha_invalid` | 422 | 验证码错/过期/已用/蜜罐/过快 | `{challenge?}`（附新图，前端自动换） |
| `captcha_unavailable` | 503 | Pillow/字体不可用 | — |
| `registration_closed` | 403 | 注册模式 `closed` | — |
| `invite_required` / `invite_invalid` | 422 | 缺邀请码 / 无效、过期、用尽、已撤销 | — |
| `invalid_username` / `username_taken` | 422 / 409 | 规则不符 / 占用 | `{rule?}` |
| `weak_password` | 422 | 未满足策略 | `{rules:[ruleId…]}` |
| `root_protected` / `self_action_forbidden` / `admin_protected` | 403 / 422 / 403 | §24.3.14 | `{action}` |
| `quota_request_pending` | 409 | 已有待处理申请 | `{requestId}` |
| `invalid_setting` | 422 | 设置值越界 | `{field}` |
| `policy_missing` | 500 | 路由未登记（理论不可达） | `{route}` |

`GET /auth/me`、`/auth/config`、`/health` 是仅有的"永不 401/403"的 `/auth` 面。**`WWW-Authenticate` 不设**（浏览器会弹原生 Basic 对话框）。

### 24.5.5 既有接口的契约变化（只追加，不改不删）

| 接口 | 变化 |
| --- | --- |
| `POST /instruments/{id}/reports` | 新增失败：401（guest）、429 `quota_exceeded`；202 体追加 `quota:{remaining, resetAt}` |
| `GET /reports*`、`GET /reports/{id}` | `ReportSummary` 追加 `author`、`canManage`、`canCurate`；软删对象对非拥有者 404；`PATCH` 含 `pinned/setLatest` 需 `report.manage_any`（否则 403） |
| `GET /capabilities` | 对非 `engine.probe` 主体脱敏（同形状），追加 `redacted` |
| `GET /runs/{id}`、`/instruments/{id}/runs` | 对非拥有者非管理员的成本/引擎字段置空 |
| `GET /watchlist` | 返回"我的"或"精选"；新增响应字段 `scope:'personal'\|'featured'` |
| `/schedules*`、`/cost*`、`/agent/probe` | 由"无门槛"变为 admin / root（§24.3.9(e)(f)）；对 guest/user 是**新增的 401/403** |
| `GET /reports/{id}/asks` | 只返回自己的（admin 全部；guest 空） |

---

## 24.6 数据模型

### 24.6.1 新表（迁移 `0007_users_auth`，全部 `op.create_table`；时间列沿用 `Text` ISO，主键沿用 uuid4 hex）

```sql
CREATE TABLE users (
  id                  VARCHAR(32)  PRIMARY KEY,
  username            VARCHAR(20)  NOT NULL,                 -- 展示用，保留大小写
  username_lc         VARCHAR(40)  NOT NULL,                 -- 唯一键；匿名化后改为 'deleted:'||id
  display_name        VARCHAR(40),
  password_hash       TEXT         NOT NULL,                 -- 'scrypt$15$8$1$salt$hash'；墓碑为 '!'
  role                VARCHAR(8)   NOT NULL CHECK (role IN ('root','admin','user')),
  status              VARCHAR(12)  NOT NULL CHECK (status IN ('active','disabled','deleted')) DEFAULT 'active',
  disabled_reason     VARCHAR(200),
  disabled_at         TEXT,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  password_is_default INTEGER  NOT NULL DEFAULT 0,
  password_changed_at TEXT,
  failed_logins       INTEGER  NOT NULL DEFAULT 0,
  locked_until        TEXT,
  last_login_at       TEXT,
  last_login_ip       VARCHAR(48),                           -- 已截断 /24 或 /48
  quota_daily_limit      INTEGER,                            -- NULL 跟随默认；-1 不限；0 禁用；N
  quota_deep_daily_limit INTEGER,                            -- 同上，深度追问
  invite_id           VARCHAR(32),                           -- 来源邀请码（无 FK 约束，见 0.E-7 注）
  created_by          VARCHAR(32),                           -- NULL=自助注册 / 引导；否则创建它的管理员 id
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_users_username_lc ON users(username_lc);
CREATE UNIQUE INDEX ux_users_root ON users(role) WHERE role = 'root';           -- 至多一个 root
CREATE INDEX ix_users_list ON users(created_at, id);                             -- 游标分页
CREATE INDEX ix_users_status_role ON users(status, role);

CREATE TABLE sessions (
  id            VARCHAR(32) PRIMARY KEY,                    -- 对外可见的"设备句柄"，不是令牌
  token_hash    VARCHAR(64) NOT NULL,                       -- sha256(token) hex
  user_id       VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  remember      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  idle_expires_at TEXT NOT NULL,
  expires_at    TEXT NOT NULL,                              -- 绝对到期
  ip            VARCHAR(48),
  user_agent    VARCHAR(200),
  revoked_at    TEXT,
  revoked_reason VARCHAR(24)                                -- logout|password_change|admin|disabled|evicted|cli
);
CREATE UNIQUE INDEX ux_sessions_token ON sessions(token_hash);
CREATE INDEX ix_sessions_user_live ON sessions(user_id, created_at) WHERE revoked_at IS NULL;

CREATE TABLE invite_codes (
  id VARCHAR(32) PRIMARY KEY, code VARCHAR(16) NOT NULL, note VARCHAR(80),
  max_uses INTEGER NOT NULL DEFAULT 1, used_count INTEGER NOT NULL DEFAULT 0,
  bonus_credits INTEGER NOT NULL DEFAULT 0, expires_at TEXT, disabled_at TEXT,
  created_by VARCHAR(32), created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_invite_code ON invite_codes(code);

CREATE TABLE quota_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id VARCHAR(32) NOT NULL,                            -- 无 FK：用户匿名化后账本必须保留
  resource VARCHAR(10) NOT NULL CHECK (resource IN ('report','deep_ask')),
  delta INTEGER NOT NULL,
  reason VARCHAR(20) NOT NULL,   -- run|run_refund|ask|ask_refund|admin_grant|admin_revoke|request_approved|signup_bonus|invite_bonus
  source VARCHAR(6) NOT NULL CHECK (source IN ('daily','bonus')),
  day_key VARCHAR(10) NOT NULL,                            -- 'YYYY-MM-DD'（QUOTA_TZ）；赠送行记发放当日
  ref_id VARCHAR(32),                                      -- run id / ask id / request id
  actor_id VARCHAR(32), note VARCHAR(120),
  created_at TEXT NOT NULL
);
CREATE INDEX ix_quota_user_daily ON quota_ledger(user_id, resource, source, day_key);
CREATE UNIQUE INDEX ux_quota_ref_reason ON quota_ledger(ref_id, reason)
  WHERE reason IN ('run','run_refund','ask','ask_refund');   -- 幂等：一个 run 至多一扣一退

CREATE TABLE quota_requests (
  id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL,
  resource VARCHAR(10) NOT NULL, amount INTEGER NOT NULL, reason VARCHAR(200) NOT NULL,
  status VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  granted INTEGER, resolved_by VARCHAR(32), resolved_at TEXT, resolution_note VARCHAR(200),
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_quota_req_pending ON quota_requests(user_id) WHERE status = 'pending';
CREATE INDEX ix_quota_req_queue ON quota_requests(status, created_at);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  actor_id VARCHAR(32), actor_name VARCHAR(40), actor_role VARCHAR(8),
  action VARCHAR(32) NOT NULL, target_type VARCHAR(16), target_id VARCHAR(32), target_name VARCHAR(40),
  ip VARCHAR(48), request_id VARCHAR(16), detail_json TEXT
);
CREATE INDEX ix_audit_at ON audit_log(at, id);
CREATE INDEX ix_audit_action ON audit_log(action, at);
CREATE INDEX ix_audit_actor ON audit_log(actor_id, at);

CREATE TABLE user_watchlist (
  user_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  instrument_id VARCHAR(32) NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL,
  PRIMARY KEY (user_id, instrument_id)
);
```

### 24.6.2 既有表的 `add_column`（5 处，全部 `nullable=True`、**无默认值**、**不回填**——E-7）

| 表 | 新列 | 索引 | 旧行语义 |
| --- | --- | --- | --- |
| `runs` | `created_by VARCHAR(32)` | `ix_runs_created_by(created_by, started_at)` | NULL＝系统/历史 |
| `reports` | `created_by VARCHAR(32)` | `ix_reports_created_by(created_by) WHERE deleted_at IS NULL`（部分索引，与 0006 同纪律） | NULL＝历史，仅管理员可管 |
| `report_asks` | `user_id VARCHAR(32)` | `ix_asks_user(user_id, report_id)` | NULL＝历史，仅管理员可见 |
| `schedules` | `owner_id VARCHAR(32)` | — | NULL＝历史 |
| `cost_ledger` | `user_id VARCHAR(32)` | `ix_cost_user(user_id, created_at)` | NULL＝系统/历史 |

**为什么这 5 列不挂外键**：SQLite 的 `ADD COLUMN … REFERENCES` 只在默认值为 NULL 时允许，且 Alembic 对命名约束的支持在 SQLite 上要走 `batch_alter_table`（整表重建，违反 E-7 的纪律，而 `reports.doc_json` 是最大的列）。用户是**软删**（匿名化）从不物理删除，所以悬空引用在语义上不会出现；完整性由 `user_service` 保证并有一条测试守卫（AC-184）。

### 24.6.3 `settings` KV 新键（复用既有表，无迁移）

| key | 类型/默认 | 含义 |
| --- | --- | --- |
| `auth.registration_mode` | `open\|invite\|closed`，默认 `open` | 注册模式 |
| `quota.default_daily.report` | int 0–100，默认 `3` | 普通用户日生成额度 |
| `quota.default_daily.deep_ask` | int 0–200，默认 `10` | 普通用户日深度追问额度 |
| `quota.signup_bonus` | int 0–20，默认 `0` | 新注册赠送额度 |
| `auth.session_secret` | 32 字节 hex，自动生成 | SSE 签名密钥（env 优先） |
| `quota.site_daily_cap` | int 0–10000，默认 `60`（`0` = 不限） | ★v2：全站受额度约束用户的日生成总量上限（§24.3.8(a)） |
| `watchlist.seeded:<uid>` | `1` | 该用户已从精选播种过自选（★v2：先 `INSERT … ON CONFLICT DO NOTHING` 置旗标，**仅当插入成功**才播种，并发双 GET 不会双写，RV-15） |

### 24.6.4 新增环境变量（`.env.example` 逐条加注释）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AUTH_MODE` | `enforced` | `open` ＝ 旧行为（所有请求视为 service），仅限本地演示/测试，启动打 ERROR 横幅 |
| `ROOT_ADMIN_USERNAME` / `ROOT_ADMIN_PASSWORD` | `jdqqjr` / `13519126390` | **仅库里没有 root 时读取一次** |
| `AUTH_FORCE_ROOT_PASSWORD_CHANGE` | `1` | 默认口令首登强制改密（D-2） |
| `SESSION_SECRET` | 空（自动生成并持久化） | SSE 令牌签名密钥 |
| `COOKIE_SECURE` | `auto` | `auto\|1\|0` |
| `TRUST_PROXY` | `0` | `1` 时信任 `X-Forwarded-For/Proto`（nginx 后必须设） |
| `QUOTA_TZ` | `Asia/Shanghai` | 额度"自然日"所在时区 |
| `AUTH_MAX_SESSIONS_PER_USER` | `10` | 超出踢最旧 |
| `AUTH_SESSION_ABS_DAYS` / `AUTH_SESSION_IDLE_DAYS` | `30` / `7` | `remember=true` 的有效期；非 remember 绝对 12 h |
| `RATE_LIMIT_ENABLED` | `1` | `0` 仅供测试 |
| `AUDIT_RETENTION_DAYS` | `365` | 审计保留期 |

### 24.6.5 迁移与回滚

`0007_users_auth`（`down_revision="0006_report_library"`）。`upgrade()`：建 7 表与全部索引 → 5 个 `add_column` + 3 个索引。`downgrade()`：**逆序** `drop_index` → `drop_column`（SQLite ≥ 3.35，本机 3.41.2 已核）→ `drop_table`（`user_watchlist` → … → `users`）。`tests/test_migration_0007.py` 做"升 → 降 → 再升"三连并断言：①`ux_users_root` 阻止第二个 root；②`ux_quota_ref_reason` 阻止重复扣/退；③5 列确为 NULL 可空；④`alembic heads` 只有一个。**root 的种子行不在迁移里写**（需要读环境变量并哈希，且要幂等），见 §24.3.13。

---

## 24.7 测试与验收

### 24.7.1 后端测试文件（新增 14 个；既有 972 条保持绿）

| 文件 | 覆盖 |
| --- | --- |
| `test_security_passwords.py` | ★v2：8 线程并发 `verify_password` 时进入 `hashlib.scrypt` 的线程数 ≤ 4（RV-7）；scrypt 往返、参数自描述与 `needs_rehash`、NFKC 归一、策略 8 条规则逐条、弱口令表、**不存在用户也耗时一次**（计时断言用"调用次数"而非墙钟） |
| `test_auth_register.py` | 验证码缺/错/过期/重放/跨 IP 桶/蜜罐/过快；用户名规则与保留词；占用；邀请码并发核销（线程并发 8 个抢 `max_uses=3` → 恰 3 成功）；`closed`/`invite` 模式；注册后自动登录 |
| `test_auth_login.py` | ★v2：存在/不存在用户名各连发 6 次错误密码，逐次响应形状相同（影子节流，AC-169/170）；成功/失败统一话术；第 3 次失败要验证码；第 5 次锁 15 分钟（注入时钟）；锁定期间**正确密码也被拒**；管理员解锁；停用账号只有密码正确才返回 `account_disabled`；`remember` 两种 Cookie 属性 |
| `test_sessions.py` | 令牌只存哈希；idle/绝对过期；滑动写节流（5 分钟内不重复写）；10 会话上限踢最旧；改密吊销其它会话；停用/降级**下一次请求即生效**；死 Cookie 被清除 |
| `test_policy_table.py` | **启动期集合相等**（构造一个漏登记的路由 → `RuntimeError`）；对附录 A 每一行用 `guest/user/admin/root` 四主体各打一次，断言状态码与表一致（参数化，约 45×4 条） |
| `test_contract_permissions.py` | 展开后角色集合与 JSON 一致；`policy.py` 里所有权限串 ∈ 词表；`root ⊃ admin ⊃ user ⊃ guest` 严格包含 |
| `test_authz_objects.py` | 报告：拥有者/他人/管理员/`created_by IS NULL`；追问：他人 → **404 不是 403**；admin 对 admin/root 目标全 403；root 唯一、不可降/停/删；自我操作 422 |
| `test_quota_engine.py` | ★v2：站点日总量（AC-207）；锁覆盖到 commit 的契约（在 `reservation()` 内 `commit` 之前，另一线程的 `reserve` 必须阻塞——用事件同步而非 sleep）；日额度先于赠送；**20 线程并发预留，limit=3 → 恰 3 成功**；失败/取消/超时/预算超限退还且退回原 `source/day_key`；`succeeded+deduped` 不退；重复对账不重复退；跨零点（注入时钟）；`-1` 不限不写账本；`0` 禁用；预检优先于 409 |
| `test_quota_requests.py` | 单 pending 约束；通过 → 赠送行；拒绝必填理由；用户可见处理结果 |
| `test_admin_api.py` | 概览聚合正确性；用户列表游标/筛选/N+1 守卫（SQL 语句计数 ≤ 常数）；创建用户临时密码仅一次；重置密码吊销会话；type-to-confirm 后端校验；设置边界 |
| `test_audit.py` | 每个动作词表项都有写入点（用词表反查 grep）；失败登录限频合并；无任何更新/删除路由；detail 不含 `password/token`（对序列化结果做正则断言） |
| `test_bootstrap_root.py` | 空库引导；已存在则**不覆盖**（改 env 重启无效）；同名非 root → 启动失败；`password_is_default` 与强制改密；CLI 三个命令 |
| `test_csrf_cors.py` | ★v2：**生产形态用例**（`Host`=`Origin` 的 netloc、scheme 不同、`cors_origins=[]` → 放行，AC-208）；`Origin: null` 拒绝；恶意 Origin 写 → 403；无 Origin 放行；`*` + 凭据启动即 raise；`Vary`、`no-store` 头；回环主机两种写法都在白名单 |
| `test_migration_0007.py` | §24.6.5 四条 |

既有测试的**必要改动**已在 §24.4.1 列出（SSE 签名来源 4 处、`ReportSummary` 键数 1 处、conftest）。**不得**为了让既有测试通过而放宽新守卫。

### 24.7.2 前端测试

vitest：`permissions.test.ts`（与 JSON 契约一致）、`authErrors.test.ts`（每个新码有文案与动作）、`safeNext.test.ts`（开放重定向：`//evil.com`、`/\evil`、`http://x`、`javascript:` 全拒）、`api-credentials.test.ts`（浏览器端 `credentials:'include'`；回环别名替换的 6 种组合）、`generate-button.test.tsx`（guest / 有额度 / 0 额度 / admin 四态与 `aria-disabled`）、`quota-chip.test.tsx`、`login-form.test.tsx`（错误后焦点、验证码自动换图、Enter 提交、防双击）、`register-form.test.tsx`（规则实时提示、强度条、确认密码不一致）、`user-menu.test.tsx`、`admin-users.test.tsx`（行菜单按角色裁剪、type-to-confirm）、`sw-isolation.test.ts`（对 `sw.js` 的缓存决策函数做单测：仅 `guest` 响应写缓存）、既有 `a11y.test.tsx` 扩展覆盖新页面（`axe` 无 serious/critical）。
Playwright（`enforced` project，后端用 `AGENT_MODE=mock`，不花钱）：`auth.spec.ts`（游客浏览全站 → 点生成弹登录 → 注册（读验证码答案走测试钩子 `GET /auth/_test/captcha-answer?challengeId=`：**仅当 `AUTH_TEST_HOOKS=1` 时才挂载**，不在附录 A 的策略表内、`verify_against_app` 对该前缀豁免，但 `test_policy_table` 另有一条断言「默认配置下该路由 404」，防止它被带进生产）→ 生成成功 → 额度从 3 变 2 → 连点至 0 → 对话框 → 申请）、`admin.spec.ts`（root 登录强制改密 → 任命 admin → admin 登录看不到"任命"入口 → 给用户发额度 → 审计里可见）、`session.spec.ts`（登出后离线打开不见上一个用户的页面——SW 隔离）。

### 24.7.3 验收标准（AC-153 – AC-208，逐条可自动化；AC-206–208 为 v2 评审新增，置于 X7/X8 小节末尾）

**X1 账号与会话**
* **AC-153** 空库启动后存在且仅存在 1 个 root（`jdqqjr`），`password_is_default=1`；用 `jdqqjr/13519126390` 登录成功，且会话处于 `must_change_password` 态，除 `/auth/me|password|logout` 外全部 403 `password_change_required`；改密后正常。设 `AUTH_FORCE_ROOT_PASSWORD_CHANGE=0` 时不强制。
* **AC-154** 注册成功即登录；Cookie 为 `HttpOnly; SameSite=Lax; Path=/`；https/`TRUST_PROXY` 场景带 `Secure`；`remember=false` 无 `Max-Age`。
* **AC-155** 数据库 `sessions.token_hash` 为 64 位十六进制且 ≠ Cookie 值；拿数据库内容无法伪造会话。
* **AC-156** 会话空闲 7 天 / 绝对 30 天（`remember=true`）、12 h（否则）到期；注入时钟可测；到期请求按 guest 处理并清 Cookie。
* **AC-157** 停用用户、改其角色、改其密码：**下一次请求立即生效**（无需等会话过期）。
* **AC-158** 同一用户第 11 个会话创建时最旧会话被吊销（`revoked_reason='evicted'`）。
* **AC-159** `POST /auth/password` 成功后其它会话全部失效、当前会话保持；旧密码不可再登录。
* **AC-160** `GET /auth/me` 对 guest 返回 200 与 guest 权限集，**永不 401**。
* **AC-161** `GET /auth/sessions` 的 `ip` 已截断；只能管理自己的会话。
* **AC-162** `last_seen_at` 写入节流：5 分钟内 100 次请求至多 1 次写。
* **AC-163** 一次完整登录/注册请求后，应用日志与审计 `detail_json` 中**不出现**明文密码、会话令牌、验证码答案（正则扫描）。
* **AC-164** 登出后原 Cookie 立即失效（回放 → guest）；登出幂等。

**X2 验证码与反滥用**
* **AC-165** 注册缺验证码/错验证码/过期/重放同一 `challengeId`/换 IP 桶使用，**全部** `422 captcha_invalid`；正确一次后该 id 立即作废。
* **AC-166** 同一 `challengeId` 第 6 次尝试（无论对错）作废；TTL 300 s（注入时钟）。
* **AC-167** 验证码图为 PNG、尺寸 160×56、`Cache-Control: no-store`；5 位、字符集不含 `0 O 1 I L`；答案不区分大小写；同一进程连续 200 张不出现重复 id。
* **AC-168** 蜜罐字段非空、或 `issuedAt` 距提交 < 1.2 s 被拒且不泄露原因（响应与验证码错误**字节级相同**除 `requestId`）。
* **AC-169** 登录连错 3 次后要求验证码；连错 5 次锁 15 分钟（`423 account_locked` 带 `retryAfterMs`）；锁定期间正确密码仍被拒；成功登录清零计数。★v2：**对数据库里不存在的用户名同样成立**（影子节流器，§24.3.7）——连错 3/5 次后同样出现 `422 captcha_required` / `423 account_locked`。
* **AC-170** 不存在的用户与错误密码的响应**状态码、body、关键头完全一致**；两条路径各调用一次 scrypt（调用次数断言）。★v2：对"存在 / 不存在"两个用户名**各连续发 6 次**错误密码，**逐次**比较 `status`、`error.code`、`detail` 键集合，必须逐次相同（含第 3 次起的验证码要求与第 5 次起的锁定）。
* **AC-171** 限速：同 IP 第 11 次登录 / 第 6 次注册 / 第 31 次验证码 → `429 rate_limited` 且带 `Retry-After`；`TRUST_PROXY=0` 时伪造 `X-Forwarded-For` **不能**绕过；`=1` 时取最右可信跳。
* **AC-172** `Pillow` 不可用（monkeypatch `ImportError`）→ `/auth/config.registrationOpen=false`、`POST /auth/register` 503；登录不受影响；邀请码注册免图形验证码（§24.3.6），**仍可用**——测试断言这一点。

**X3 RBAC**
* **AC-173** 启动期：`POLICIES` 与 `app.routes` 集合不等 → 进程 `RuntimeError`；`test_policy_table` 对附录 A 全部行 × 4 主体 的状态码断言全绿。
* **AC-174** guest 对任何"写 / 花钱 / 管理"路由得 401，对公开读路由得 200；user 对 admin 路由得 403（不是 401，也不是 404）。
* **AC-175** `contracts/permissions.json` 与 Python/TS 展开结果逐角色相等；`root ⊋ admin ⊋ user ⊋ guest`。
* **AC-176** root 唯一：`INSERT` 第二个 `role='root'` 被索引拒绝；任何 API 路径改 root 角色/状态/删除 → `403 root_protected`；root 对自己停用/删除/降级 → `422 self_action_forbidden`。
* **AC-177** 只有 root 能任命/降级 admin；admin 对 admin/root 的写操作全部 `403 admin_protected|root_protected`；注册与邀请码永远只能产生 `user`。
* **AC-178** 报告对象级：拥有者可 PATCH/DELETE/restore；他人 403；admin 可；`created_by IS NULL` 仅 admin；软删报告对 guest/他人 `404`。
* **AC-179** 追问对象级：他人访问 `GET /asks/{id}`、`POST …/events-token`、`POST …/cancel` → `404`（★v2：`GET …/events` 按"令牌即授权"，他人拿不到令牌即拿不到流；拥有者在**无 Cookie 的跨源**环境下凭令牌可正常订阅）；`GET /reports/{id}/asks` 只含自己的；guest 得空列表 200。
* **AC-180** `Capabilities` 对非 `engine.probe` 主体脱敏：JSON **键集合与完整响应相同**（同形状），但敏感值为 null/false/[]，`redacted=true`；admin 得全量。
* **AC-181** `RunOut` 对非拥有者非管理员脱敏 6 个字段；拥有者与管理员全量。
* **AC-182** `API_TOKEN` 非空时：带正确 Bearer → `service` 主体可访问 admin 读写（审计 `actor=service`）；**不带**不再被整站拒绝（按 guest/cookie 处理）；错误 Bearer 按 guest（或其合法 Cookie 会话）处理，**不**因此 401 公开路由；★v2：`service` 对 `/admin/users*`（写）、`/admin/settings`、`/admin/invites*`、`/admin/quota-requests*`、`/cost/budget` 得 `403 forbidden`，对 `/admin/audit`、`/admin/overview`、`/admin/featured-watchlist` 可访问。
* **AC-183** `AUTH_MODE=open`：所有请求为 service；启动日志含 ERROR 横幅；`/auth/me.authMode=='open'`；切回 `enforced`（monkeypatch，**请求时读取**）立即生效。
* **AC-184** 完整性守卫：对库中全部 `runs/reports/report_asks/schedules/cost_ledger` 的非空 `created_by/user_id/owner_id`，均能在 `users` 找到（含匿名化墓碑）。

**X4 额度**
* **AC-185** 默认用户日额度 3：连续成功预留 3 次，第 4 次 `429 quota_exceeded`，`detail` 含 `limit/usedToday/bonus/resetAt/canRequest`。
* **AC-186** 并发：20 线程同时预留（limit=3）→ **恰好 3 成功、17 个 429**，账本恰 3 行 `run`；`runs` 表恰 3 行（原子性：失败预留不留 run 行，失败 run 不留悬空扣除）。
* **AC-187** 扣除顺序：先 `daily` 后 `bonus`；赠送 5 + 日额度 3 → 共可生成 8 次；账本 `source` 分布 3 daily + 5 bonus。
* **AC-188** 退还：`failed / cancelled / timeout / budget_exceeded / interrupted(failed)` 的 run 对账后恰有一条 `run_refund`，且 `source/day_key` 复制自原扣行；重复对账不产生第二条（`ux_quota_ref_reason`）；`succeeded`（含 `deduped`）不退。
* **AC-189** 预检优先：额度为 0 的用户对"同标已有运行中"的标的发起生成，得 `429 quota_exceeded` 而非 409；额度充足时 409/429/402 的既有优先级**一字不变**（既有用例全绿）。
* **AC-190** 被 409/429/402/422 拒绝的请求**不扣额度、不建 run、不建工作区目录**。
* **AC-191** 跨零点：注入时钟 23:59:59 → 00:00:00（`QUOTA_TZ`），`usedToday` 归零、`resetAt` 前移；昨日的退款仍退回昨日的 `day_key`。
* **AC-192** 额度 `-1` 不写账本；`0` 一律 429；admin/root 默认 `-1`；`system`（定时刷新）不扣额度也不受限。
* **AC-193** 额度申请：每用户单 pending；通过 → 同事务写赠送行并可见于 `/me/quota`；拒绝必填理由且用户可读；`deep` 额度与 `report` 额度互不影响；`cite` 追问不计额度。

**X5 管理后台**
* **AC-194** `GET /admin/overview` 的 5 个聚合与手工 SQL 对账一致；种子 10 万用户时 p95 < 80 ms（CI 以 2 万用户 < 40 ms 为门）。
* **AC-195** 用户列表：游标无重复无遗漏（含 `created_at` 相同秒的并列）；`q` 前缀走**范围查询**并命中索引（`EXPLAIN QUERY PLAN` 含 `ux_users_username_lc`；★v2：不用 `LIKE`，RV-8；`q` 含 `%`/`_` 时按字面处理，返回空而非全表）；一次列表请求的 SQL 条数 ≤ 6（无 N+1）。
* **AC-196** 创建用户/重置密码返回的临时密码**仅此一次**可见（再读不可得）；并置 `must_change_password`、吊销既有会话。
* **AC-197** 删除用户 = 匿名化：用户名可被再次注册、报告 `author` 显示"已注销用户"、账本与审计保留；`confirm` 不等于用户名 → 422（后端校验）。
* **AC-198** 审计：词表里每个动作都至少有一个测试触发并落库；审计表无任何更新/删除路由（路由表扫描）；`detail_json` 无敏感键。
* **AC-199** 设置：三个注册模式的行为（open/invite/closed）各一条端到端用例；越界值 `422 invalid_setting(field)`；改设置后**无需重启**立即生效（每次请求读库，不缓存）。

**X6 前端**
* **AC-200** 工作台生成按钮四态（guest / 有额度 / 0 额度 / admin）文案与 `aria-*` 正确；guest 点击弹出 `AuthDialog` 且**不发任何 POST /reports**；登录成功后**不自动生成**。
* **AC-201** 登录/注册表单 a11y：`axe` 无 serious/critical；`autocomplete` 三值正确；错误后焦点移至第一个非法字段；验证码错后自动换图并聚焦；`?next=` 开放重定向用例全拒。
* **AC-202** 首屏 JS 预算不破：`check-bundle` 对 `/[symbol]` 仍 ≤ 240 kB gzip（新增静态部分 ≤ 3 kB）；`AuthDialog/QuotaExceededDialog` 为异步 chunk。
* **AC-203** 后台：user 访问 `/admin` 得 403 视图（非 404、非空白）；guest 被重定向到 `/login?next=/admin`；admin 看不到"任命/降级"控件；危险操作有二次确认，删除/降级/重置 admin 需输入用户名。

**X7/X8**
* **AC-204** SW 隔离：`sw.js` 仅在响应头 `X-Aragon-Viewer: guest` 时写导航缓存；登出触发 `purge-user-cache` 后缓存命名空间清空；`/login|/register|/account|/admin` 永不缓存（Playwright 离线用例）。
* **AC-205** 恢复与回滚：`python -m app.cli reset-root-password` 重置后旧会话全失效、需强制改密、写审计；`alembic downgrade 0006_report_library` 后既有 972 条后端用例（`AUTH_MODE=open`）仍全绿。

**v2 评审新增**
* **AC-206** 令牌不下发浏览器（RV-1）：①仓库内 `web/lib/**`、`web/components/**`、`web/app/**` 对 `NEXT_PUBLIC_API_TOKEN` 的引用数为 0（vitest 用 `fs` 扫描源码断言）；②`next build` 产物 `.next/static/**` 不含字符串 `NEXT_PUBLIC_API_TOKEN`；③`API_TOKEN` 非空且浏览器请求**不带** Authorization 时，登录用户的 Cookie 会话仍按 `user` 解析（Bearer 不遮蔽 Cookie）；④带正确 Bearer 的 `service` 对 §24.3.4 `service.minus` 所列权限的路由得 `403 forbidden`。
* **AC-207** 站点日总量上限（RV-11）：`quota.site_daily_cap=N` 时，受额度约束用户当日第 N+1 次成功预留得 `429 quota_exceeded(detail.scope='site', canRequest=false)`；失败 run 退款后名额回补；admin/root/service/system 的生成**不占**名额；`0` = 不限；改设置后无需重启立即生效。
* **AC-208** 生产形态 CSRF（RV-2）：`Host: aragontrading.online`、`Origin: https://aragontrading.online`、`request.url.scheme='http'`、`cors_origins=[]` 的 `POST /auth/login` 放行；`Origin: https://evil.example` 与 `Origin: null` → `403 csrf_rejected`；`Origin` 缺失放行；`TRUST_PROXY=1` 时 `X-Forwarded-Host` 的 netloc 匹配也放行。

---

## 24.8 里程碑与依赖

依赖图：`M45 → M46 → {M47, M48} → M49 → M50 → M51 → M52`（M47 与 M48 可并行；M51 的认证部分只依赖 M47，其额度/后台部分分别依赖 M48/M50）。每个里程碑的**出口准则**是"门禁全绿 + 列出的 AC 通过"，门禁沿用既有口径：`pytest`、`ruff`、`tsc --noEmit`、`eslint`、`vitest`、`next build`、`check-bundle`。

| 里程碑 | 内容 | 出口准则 |
| --- | --- | --- |
| **M45 数据与身份底座** | 迁移 `0007`、新模型、`config`、`errors`、`security/{principal,permissions,passwords,weak_passwords}`、`user_service`、`session_service`、`bootstrap`、`contracts/permissions.json`、conftest 改造 | AC-153(部分) 154–156 175 176；既有 972 绿（`open` 模式）；★v2：`next build` 实测能 import 仓库根 `contracts/permissions.json`（RV-16），否则按 §24.3.4 的 fallback 改为复制文件 |
| **M46 策略与传输层** | `policy/deps/objects/csrf`、`main.py` 改造（去整站闸门、CORS 凭据、挂 `enforce`、启动期校验）、`audit_service`、`RedactFilter`、`sse_token` 迁移 | AC-173 174 182 183 163 206(①③④) 208 + 4 处既有测试迁移 |
| **M47 注册登录与验证码** | `challenge/captcha/auth_service/invite_service`、`routers/auth.py`、限速器、锁定、`Pillow` 依赖 | AC-157–162 164–172 177(部分)（★v2：AC-169/170 含影子节流；含 scrypt 并发上限用例，RV-7） |
| **M48 额度引擎** | `quota_service/quota_requests/settings_service`、`routers/me.py`、`run_manager.start(actor)`、预检/预留/对账、`_sweep_loop` 增项 | AC-185–193 207 |
| **M49 既有能力改造** | 报告/追问/自选/定时/成本/探针/Capabilities/RunOut 的策略与对象级、`ReportSummary` 追加键、`watchlist_service`、`created_by` 链路 | AC-174 178–181 184 + 既有测试键数迁移 |
| **M50 管理后台 API** | `routers/admin.py`、`admin_overview`、全部 admin 端点与矩阵、审计动作覆盖 | AC-176 177 194–199 |
| **M51 前端认证与工作台接入** | `api.ts` 工厂化、`serverApi`、`auth/permissions/authErrors`、`/login /register /account`、`UserMenu/QuotaChip/AuthDialog/QuotaExceededDialog`、`GenerateButton` 三态、SW 隔离、`middleware.ts`、安全头 | AC-200–202 204 206(①②)；首屏预算 |
| **M52 管理后台前端 + CLI + 文档 + e2e** | `/admin/**` 全部页面、`app/cli.py`、README/`.env.example`/deploy 更新、Playwright 三个 spec | AC-203 205 + 全量门禁 + 一次真实浏览器走查（游客→注册→生成→用尽→申请→admin 通过） |

---

## 24.9 风险与缓解

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R-A1 | **Cookie 主机不一致**（`localhost` vs `127.0.0.1`）导致"登录成功但立刻掉线" | 开发期/首次上手最常见的故障，且表现为玄学 | §24.3.2(d) 回环别名对齐 + README 一节 + `/auth/me` 失败时前端给出"请使用同一主机名访问"的具体提示；Playwright 用两种主机各跑一次登录 |
| R-A2 | RSC 服务端 fetch 不带 Cookie → 页面首屏按 guest 渲染，客户端 hydrate 后又变用户（闪烁/不一致） | 体验差、SSR 与 CSR 数据不一致 | `serverApi()` 转发；根 layout 注入 `initialMe`；e2e 断言首屏 HTML 已含用户名 |
| R-A3 | **SW 缓存泄露他人页面** | 隐私事故 | §24.3.12 三道隔离 + AC-204 |
| R-A4 | 漏登记新路由 → 默认放行 | 越权 | 启动期集合校验 + 测试；**默认拒绝**而非默认放行（未登记即 500 `policy_missing`） |
| R-A5 | scrypt 在事件循环里执行 → 拖死 SSE | 全站卡顿 | `/auth/login|register|password` 一律 `def`（线程池）；复用 `test_event_loop_guard` 思路加一条"登录期间 SSE 心跳不中断"用例 |
| R-A6 | 额度并发超发 / 退款重复 / 扣了没开 run | 资金与信任 | 互斥锁 + 事务内复核 + 部分唯一索引 + 对账幂等；AC-186/188 |
| R-A7 | 依赖"单 worker"的进程内状态（验证码、限速、额度锁） | 若将来改多 worker 则静默失效 | 在代码注释与 spec 里显式绑定 `assert_single_worker()`；账号锁定/会话/账本均落库，**只有**验证码与限速在内存；多 worker 迁移须先把二者换成库/Redis（已留 `ChallengeProvider` 接口） |
| R-A8 | 图形验证码可被 OCR 破解 | 机器注册 | 它不是唯一防线：IP 限速 + 全站小时上限 + 蜜罐 + 最小耗时 + 邀请码模式一键收紧；`registration_mode=invite` 是紧急刹车；`ChallengeProvider` 可替换为 Turnstile/hCaptcha |
| R-A9 | root 弱口令公开在需求里 | 站点被接管 | D-2：默认强制首登改密；锁定对 root 同样生效；README 红字提示；`AUTH_FORCE_ROOT_PASSWORD_CHANGE=0` 时出现不可关闭横幅 |
| R-A10 | 账号锁定被用作 DoS（有人故意连错锁住 root） | root 暂时不能登录 | 锁 15 分钟自动解；已登录会话不受影响；离线 CLI `unlock`；审计记录来源 IP |
| R-A11 | 退款的"失败"定义与用户感知不一致（run 成功但内容被去重） | 争议 | D-5 明文规定；界面在"未新增版本"提示里附带"本次已消耗 1 次额度，因为分析已完成"（沿用 `run.done.deduped` 的既有提示位） |
| R-A12 | `ReportSummary` 追加键触发既有契约/快照测试 | 门禁红 | 已在 §24.4.1 列出需同步修改的断言；`canManage` 是**按视角**字段，缓存层（SW 只缓存 guest 页）不会串用 |
| R-A13 | 首屏 JS 预算（AC-35）被新代码顶破 | 门禁红 | 只有 `UserMenu/QuotaChip` 静态；其余全部 `next/dynamic`/独立路由；阈值不上调 |
| R-A14 | `Pillow` 在部署机缺字体/缺 FreeType | 注册不可用 | 用内置可缩放字体（Pillow ≥ 10.1，无系统字体依赖）；启动自检并打日志；不可用时**关闭注册**而非放行；邀请码通道保底 |
| R-A15 | 既有脚本（`deploy/remote/*.sh`、运维探针）依赖"无鉴权/或 `API_TOKEN`" | 运维脚本失效 | `API_TOKEN` 作为 service 凭据保留（D-7）；公开读路由本来就免凭据；写路由脚本改带 Bearer（M52 同步更新脚本） |
| R-A16 | 多标签页：A 页登出，B 页仍显示已登录并可点生成 | 体验不一致、误扣 | 任何写请求失败为 401 时全局回落 guest（§24.3.15a）；`BroadcastChannel('aragon-auth')` 同步登出/登录（不支持则退化为 401 回落） |
| R-A17 | 扩展点：MFA/OAuth/邮件验证 | 将来的返工 | `ChallengeProvider` 抽象 + `Principal` 与传输解耦 + 会话表可加 `amr` 列；**本轮不实现**，避免半成品 |

---

## 附录 A：逐路由策略表（`security/policy.py` 的完整内容；`public` 表示 guest 起即可）

> 路径均省略 `/api/v1` 前缀。"对象级"一栏的规则由路由内调用 `security/objects.py` 执行；"限速"指 §24.3.7 的 scope。

| 方法 | 路径模板 | 权限 | 对象级 / 限速 / 备注 |
| --- | --- | --- | --- |
| GET | `/health` | public | 运维探针 |
| GET | `/capabilities` | public | 对非 `engine.probe` 脱敏（同形状） |
| GET | `/symbols/search` | public | — |
| POST | `/symbols/resolve` | public | 限速 `guest.resolve`（user 及以上不限） |
| GET | `/instruments` · `/instruments/{id}/bars` · `/instruments/{id}/quote` | public | — |
| GET | `/reports` | public | `scope=trash` 需登录且仅"我的"（admin 全部）；`starred` 公开 |
| GET | `/instruments/{id}/reports` · `/instruments/{id}/reports/latest` | public | 软删对象不返回 |
| GET | `/reports/{id}` · `/reports/{id}/blocks` · `/reports/{id}/diff/{other}` | public | 软删对象对非拥有者/非管理员 404 |
| GET | `/reports/{id}/export` | `export.read`（public） | 限速 `guest.export` |
| POST | `/instruments/{id}/reports` | `report.generate` | 预检额度 → `start(actor)` 预留；202 追加 `quota` |
| PATCH | `/reports/{id}` | `report.manage_own` | 对象级：拥有者 或 `report.manage_any`；`created_by IS NULL` 仅管理员；★v2：body 含 `pinned`/`setLatest` 需 `report.manage_any`（否则整个请求 403，RV-10） |
| DELETE | `/reports/{id}` | `report.manage_own` | 同上；管理员改他人的 → 审计 `report.manage_other` |
| POST | `/reports/{id}/restore` | `report.manage_own` | 同上 |
| GET | `/instruments/{id}/runs` | public | `RunOut` 脱敏 |
| POST | `/runs` | public | 恒 405（既有占位路由，不泄露存在性差异） |
| POST | `/runs/estimate` | `report.generate` | 暴露成本估算 |
| GET | `/runs/{id}` | public | `RunOut` 脱敏 |
| POST | `/runs/{id}/events-token` | public | — |
| GET | `/runs/{id}/events` | public | 自带 `?token=` 真签名校验 |
| POST | `/runs/{id}/cancel` | `report.manage_own` | 对象级：`run.created_by == me` 或 `manage_any`；他人 → 审计 `run.cancel_other` |
| POST | `/reports/{id}/ask` | `ask.cite`（public） | `mode=cite`：限速 `guest.ask_cite`；`mode=deep`：额外要求 `ask.deep` + 额度 `deep_ask`（在路由内二次判定，guest → 401） |
| GET | `/reports/{id}/asks` | public | 返回"我的"（admin 全部，guest 空） |
| GET | `/asks/{id}` · POST `/asks/{id}/events-token` · POST `/asks/{id}/cancel` | `ask.cite`→对象级 | 拥有者或管理员；否则 404 |
| GET | `/asks/{id}/events` | `ask.cite`（public） | ★v2（RV-5）**只验 `?token=`**（绑定 ask_id，60 s）；对象级校验在 `events-token` 签发处，非拥有者拿不到令牌 |
| GET | `/search` | `search.read`（public） | — |
| GET | `/watchlist` | public | 登录→个人（首次播种）；guest→精选；追加 `scope` |
| POST/DELETE/PATCH | `/watchlist` · `/watchlist/{id}` · `/watchlist/reorder` | `watchlist.own` | 仅个人表 |
| GET/POST/PATCH/DELETE | `/schedules` · `/schedules/{id}` | `schedule.manage` | 写入审计 `schedule.*` |
| GET | `/cost/summary` · `/cost/entries` | `cost.read` | — |
| POST | `/cost/budget` | `cost.manage` | root |
| GET/POST | `/agent/probe` | `engine.probe` | — |
| GET | `/auth/config` · `/auth/captcha` · `/auth/me` | public | `captcha` 限速 |
| POST | `/auth/register` · `/auth/login` · `/auth/logout` | public | 限速；`logout` 幂等 |
| POST/GET/DELETE/PATCH | `/auth/password` · `/auth/sessions*` · `/auth/profile` | `account.self` | 强制改密态仅放行 `/auth/password`、`/auth/me`、`/auth/logout` |
| GET/POST | `/me/quota` · `/me/quota-ledger` · `/me/quota-requests` | `account.self` / `quota.request` | — |
| GET | `/admin/overview` | `admin.overview.read` | — |
| GET/POST/GET/PATCH/DELETE/POST×4 | `/admin/users` 族（含 `reset-password` `revoke-sessions` `unlock` `quota`） | `admin.users.read` / `admin.users.manage` / `admin.quota.manage` | 对象级矩阵 §24.3.14（`assert_can_manage_user`） |
| GET/POST/DELETE | `/admin/invites*` | `admin.invites.manage` | — |
| GET/POST | `/admin/quota-requests*` | `admin.quota.manage` | — |
| GET | `/admin/audit` | `admin.audit.read` | 只读 |
| GET/PATCH | `/admin/settings` | `admin.settings.manage` | — |
| GET/POST/DELETE | `/admin/featured-watchlist*` | `admin.featured.manage` | 写入审计 `featured.*` |

> 实现者注意：表中"POST ×4"等合并行在 `policy.py` 里**必须展开成逐条 `(方法, 路径)`**；`verify_against_app` 的集合相等是按展开后的键比较的，合并写法只是为了阅读。

---

## 附录 B：关键时序（实现对照）

**B-1 游客 → 注册 → 首次生成**

```
Browser                    Next(RSC)                 FastAPI
  │ GET /AAPL ───────────────►│ serverApi()+cookie(无) ─►│ GET /auth/me → guest
  │◄── HTML(guest, X-Aragon-Viewer: guest) ◄───────────│
  │ 点「登录后生成」→ AuthDialog(注册)                   │
  │ GET /auth/captcha ─────────────────────────────────►│ issue(): PNG + 内存仓库
  │ POST /auth/register {…, captchaAnswer} ────────────►│ 限速→模式→验证码→规则→事务建用户(+赠送)→建会话
  │◄── 201 Me + Set-Cookie: aragon_sid ◄────────────────│
  │ invalidate['me'] → 回到 /AAPL，聚焦「生成」按钮        │
  │ 点「生成」POST /instruments/{id}/reports ───────────►│ enforce(report.generate) → precheck
  │                                                      │ start(actor): 闸门链 → ★reserve(同事务写 Run)
  │◄── 202 {runId, quota:{remaining:2,…}} ◄──────────────│
  │ SSE …/events?token= ───────────────────────────────►│（既有流程不变）
```

**B-2 额度预留与退款**

```
reserve():  lock(user) → remaining=f(ledger) → <=0? 429 : INSERT ledger(-1,'run',source,day_key,ref=run_id) ; INSERT runs ; COMMIT ; unlock
run 终态非 succeeded ──► reconcile_refunds()（sweep 60s / 启动 / 读额度前定向 / _finalize_failure 尾部加速）
                         INSERT ledger(+1,'run_refund', source=原, day_key=原, ref=run_id) ON CONFLICT DO NOTHING
```

**B-3 强制改密态**

```
login(jdqqjr/13519126390) → 200 Me{mustChangePassword:true} + Cookie
任意其它请求 → 403 password_change_required → 前端全局跳 /account/password?forced=1
POST /auth/password → 204（吊销其它会话，清 must_change_password/password_is_default）→ 回到来路
```

---

## 评审结论

**结论：有条件通过**（v2）。

**四个维度的判断**

* **可行性**：通过。栈内可实现——`scrypt` 来自标准库；唯一新增依赖 `Pillow>=10.1` 已声明；`scope["route"]`/`path_format` 在本仓库 FastAPI 0.136.1 / Starlette 1.0.0 上已读源码确认；迁移纪律与 0006 同构；三个页面早已 `force-dynamic`，引入 `cookies()` 不改变渲染模式。
* **完整性**：v1 在**安全边界**上有两处会直接造成上线事故的缺口（RV-1 公开 Bearer、RV-2 生产 CSRF 全拒），以及一组 P1（枚举口、启动校验自毁、SSE 授权自相矛盾、Data Cache 脱敏被绕过、KDF 内存放大、AC-195 不可达、锁契约超发、全站旗标越权、注册农场耗预算）。以上 **2 条 P0 + 9 条 P1 已全部改入正文并新增 AC-206–208**，没有遗留的 P0/P1。
* **一致性**：附录 A 与真实路由表逐条一致（46 条业务路由）；与 E-1~E-15 既有约定（错误信封、单 worker 不变量、迁移纪律、契约测试模式、`runErrors.ts` 不被污染、首屏 240 kB 预算）无冲突；"测试语义迁移"（SSE 签名来源 4 处）已在 §24.3.11 明示为有意变更。
* **适度性**：规模偏大但每块都有对应的需求条目（登录/注册验证码、根管理员、管理后台、次数限制、游客只读）。v2 **仅新增一个必要机制**（站点日总量，一个设置项 + 一条判断），其余均为改正或收紧；未引入新依赖、未新增表、未新增路由。

**通过的条件**（均为实施期的门禁，而非未决的设计缺陷；任一不满足，对应里程碑不得标记完成）：

1. **M45**：`next build` 实测能 import 仓库根 `contracts/permissions.json`；否则按 §24.3.4 的 fallback 改为复制文件 + 字节相等断言（RV-16）。
2. **M46**：`test_csrf_cors.py` 必须含"生产形态"用例（AC-208）；`test_policy_table.py` 的启动校验必须以"非 `/api/v1` 前缀即豁免"为规则，**不得**回退为手写清单（RV-4）。
3. **M47**：AC-169/170 必须按"存在/不存在用户名逐次比较响应"的写法实现（RV-3）；`passwords.py` 的 KDF 并发上限必须有并发用例（RV-7）。
4. **M48**：`reservation()` 的"锁覆盖到 commit"必须有用事件同步（而非 `sleep`）的并发用例；该用例**不得**因"async 路由天然串行"而省略（RV-9）。
5. **M51**：`api.ts` 删除 `NEXT_PUBLIC_API_TOKEN` 后，AC-206 的源码扫描与构建产物扫描必须进入 vitest/CI（RV-1）。部署侧同步：README 与 `.env.example` 把 `NEXT_PUBLIC_API_TOKEN` 标为"已废弃，设置它等于公开管理员凭据"。
6. **M52**：真实浏览器走查必须覆盖**两种主机写法**（`localhost` 与 `127.0.0.1`）各登录一次（R-A1）；生产部署前确认 systemd 单元已设 `TRUST_PROXY=1`、`COOKIE_SECURE=1`，并用 `deploy/ops/status.sh` 核对输出的 `auth: enforced`、`trust_proxy: 1`（RV-14）。
7. **上线前（非代码）**：`API_TOKEN` 若已在生产环境变量中设置，需确认它**从未**以 `NEXT_PUBLIC_API_TOKEN` 的形式构建进过已部署的前端；若曾构建过，视为已泄露，**先轮换令牌再上线**（RV-1 的运维侧后果，设计无法替部署者回溯）。

**未采纳 / 保持原判的点**（评审也需要写明为什么不改）：

* **root 初始密码首次登录强制修改**（D-2）：它与"需求里写明了 `jdqqjr/13519126390`"存在张力，但**用户仍用这组凭据登录**，只是第一次要换密码；这是对"公开弱口令 + 公网域名已部署"的最小折中，且可由 `AUTH_FORCE_ROOT_PASSWORD_CHANGE=0` 关闭。保持。
* **邀请码注册免图形验证码**（§24.3.6）：邀请码是一次性高熵凭据，且是受限用户的保底通道；已有 IP 限速（5 次/10 分钟）兜住暴力枚举。保持。
* **进程内验证码仓库与限速桶**：依赖 E-8 的单 worker 不变量，R-A7 已显式绑定；账号锁定、会话、账本均落库。保持。


---

## 实施过程发现的方案缺陷

> 本节由各实施节点追加（后端节点与前端节点各写各的小节；追加前先重读文件再 Edit，避免覆盖）。
> 口径：写"发现了什么 / 证据 / 采用的修正"，不静默偏离正文。

### 前端（M51）

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| F-1 | §24.3.15e 首屏预算、AC-202、§24.8 M51 | **基线在加入任何认证代码之前就已超预算**：未改动的 `master 00db6a9` 上 `next build` + `check-bundle` = **249.1 kB > 245 kB（AC-35 默认阈值）**。规格假设"新增静态部分 ≤ 3 kB"，在一个已红的基线上不成立。进一步实测：只要 `QuotaChip` 静态引入 `ui/Tooltip`（Radix Tooltip → popper + floating-ui，约 130 kB 源码）首屏就涨到 **272.3 kB**（+23 kB） | ①`QuotaChip` 的提示改为**纯 CSS**（`group-hover` / `group-focus-within` + `aria-describedby`），不引 `ui/Tooltip`；②`PricePanel` 里的 `CandleChart`（lightweight-charts，≈155 kB 源码，本来就只能在浏览器 canvas 里画）改 `next/dynamic`，SSR 的 HTML 无差别、占位骨架同形；③阈值**未上调**。最终首屏见完成报告（认证代码合计约 +10 kB，被 ② 的约 −65 kB 吸收） |
| F-2 | §24.4.2 `lib/copy.ts`（`copy.auth / copy.admin / copy.quota`） | `copy` 是**一个**对象常量，整份进首屏 chunk；登录/注册/账号/错误话术/后台文案合计约 8–10 kB 源码，放进去就是纯增量的首屏成本 | 拆成三份：`lib/copy.ts` 只放首屏会用到的 `copy.auth`（菜单/游客入口/按钮态）与 `copy.quota`（芯片）；`lib/copy.authPages.ts` 的 `copyAuthPages`（登录/注册/账号/改密/额度对话框/错误话术，只被懒加载路由与浮层 import）；`lib/copy.admin.ts` 的 `copyAdmin`（后台，M52 填充）。命名空间一半在 `copy.*`、一半在独立模块——这是对"都进 copy.ts"的**有意偏离**，理由是预算 |
| F-3 | §24.3.15 `UserMenu` | 首屏静态的菜单若用 Radix DropdownMenu，会把 menu + roving-focus + collection + popper 拖进首屏 | `UserMenu` 手写（`role=menu`/`menuitem`、↑↓/Home/End、Esc 还焦点、点外部关闭、Tab 关闭），约 3 kB 源码；浮层（`AuthDialog`/`QuotaExceededDialog`）才用 Radix 并 `next/dynamic` |
| F-4 | §24.3.15a 401 全局处理 | "`reason != 'no_session'` 即会话失效"没说**本来就是游客**时怎么办：游客命中任何 401（`expired/revoked` 等也可能出现在死 Cookie 被降级的那一次）会弹"登录已失效"，是误报 | `Providers` 只在 `['me'].authenticated === true` 且非 `open` 模式时才按"会话失效"处理；游客的 401 是调用方自己的"请先登录"分支 |
| F-5 | §24.3.15c 生成入口 | 规格只写了 `GenerateButton` 三态，没覆盖**其它生成入口**：热键 `G/R`、左栏 ✨、命令面板「生成」、覆盖层「重试」、历史横幅「基于此版本更新」。若只改按钮，游客按热键会发出一个必然 401 的 `POST /reports`（并被当成"生成失败"） | 新增 `useGenerationGate()`（`lib/auth.ts`）：游客 → 登录弹窗，额度为 0 → 额度对话框，否则放行；上述五个入口全部过同一道闸。`useGenerationStream` 另对服务端兜底的 `quota_exceeded` / `unauthorized` 打开对应对话框，**不进"生成失败"卡片、不触发 `onError`** |
| F-6 | §24.3.12 SW 三道隔离 | 三道里漏了**安装期**：`sw.js` 的 `install` 用 `cache.add('/')` 预缓存壳，安装时的 `fetch` 会带上浏览器当前 Cookie——此刻若已登录，缓存下来的是**某个人的首页** | `install` 改为 `fetch` + `shouldCacheNavigation()`（只收 `X-Aragon-Viewer: guest`）+ `cache.put`；决策函数导出给 vitest（`tests/sw-isolation.test.ts`，含 install 用例） |
| F-7 | §24.5.4 `weak_password.detail.rules` | 规格说"列出未满足的规则 id"，**没有给 id 词表**，前端无从映射文案 | 前端使用 `min_length / max_length / contains_username / char_classes / common_password / repeated_chars / sequential_chars`（`lib/passwordPolicy.ts`），未知 id 退化为通用一句，不会坏；已向后端节点发消息请其对齐或回告差异。**后端落地后需核对一次** |
| F-8 | §24.5.1 `registrationOpen` | `registrationOpen=false` 同时表示"模式 closed"与"验证码不可用（但邀请码仍可用，AC-172）"，前端无法仅凭它区分 | 前端用 `registrationMode` 区分：`closed` → "注册已关闭"；其余且 `registrationOpen=false` → "注册暂不可用"提示 + 邀请码通道（免验证码）仍可提交 |
| F-9 | §24.3.6 步骤 2/3 | `invite` 模式下无邀请码注册必然 `422 invite_required`，此时取验证码图是浪费一次 Pillow 渲染（且占 `auth.captcha` 限速额度） | `invite` 模式（及验证码不可用）前端不请求验证码；填了邀请码也立即隐藏验证码字段，与 §24.3.6 "邀请码注册免图形验证码"一致 |
| F-10 | §24.5.1 `Me` 在 `AUTH_MODE=open` | 规格没写 open 模式下 `authenticated/user` 的取值 | 前端约定：`authMode==='open'` 一律不算游客（`isGuest = !authenticated && authMode !== 'open'`），`user:null` 时 `UserMenu` 不渲染；`AppShell` 顶部显示黄条。无 `MeProvider` 的孤立渲染（单测）等价于 open——既有 325 条用例因此无需改动 |
| F-11 | §24.4.2 `AppShell` 的 QuotaChip 与 §24.3.15c "按钮旁 QuotaChip" | 两处都放会在同屏出现两个芯片 | 头部放全局芯片；`GenerateButton` 仅在 `showQuota`（空态的大按钮）时在旁边放芯片，头部的小"重新生成"按钮不重复 |
| F-12 | 全局样式 | `globals.css` 的 `:focus-visible { outline }` 与 `button { color: inherit }` 是**非分层**规则，Tailwind 工具类（分层）永远压不过：`Input` 的 `outline-none` 失效（聚焦时叠两圈）、强调色文字按钮显示成继承色。这是既有缺陷，认证表单是第一批大量使用它们的页面 | `Input` 加 `data-input-field` + 非分层规则 `[data-input-field]:focus-visible{outline:none}`；强调色文字按钮用内联 `color`（`ACCENT_TEXT`）。**未**改全局 `button` 规则（会改变全站既有按钮观感，另案） |
| F-13 | 测试基础设施 | `import 'server-only'` 由 Next 构建期内置别名解析，vitest 没有，`serverApi.ts` 无法被单测 import | `vitest.config.ts` 增别名 `server-only → tests/stubs/server-only.ts`（空替身） |
| F-14 | §24.3.2(c) 根布局取 `me` | 根 `layout.tsx` 每次渲染都多一次 `/auth/me` 服务端请求（并因 `cookies()` 使 `/_not-found` 也变为动态）；API 冷启动时若沿用通用的 8 s 超时，**每个页面**都会被拖慢 | 布局里的 `me()` 超时收紧为 2.5 s，失败即降级 guest（`initialMe=null`，客户端随后自己重取）；`createApi().me(timeoutMs?)` 增可选参数 |
| F-15 | RV-16 | `next build` 能否 import 仓库根 `contracts/permissions.json`：**实测通过**（`outputFileTracingRoot=cwd` 下 webpack 正常解析 JSON），无需 `sync-permissions.mjs` fallback | `lib/permissions.ts` 直接 `import '../../contracts/permissions.json'`；`tests/permissions.test.ts` 独立展开 JSON 与 TS 展开逐角色比对 |

### 前端（M52 管理后台）

> ID 前缀 `F-`，接 M51 的编号。

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| F-16 | §24.3.15d 用户抽屉 × §24.5.3 | 抽屉要求"活动会话可逐条吊销"，契约里只有 `POST /admin/users/{id}/revoke-sessions`（全部吊销） | 前端按"逐条 + 全部下线"接线（`api.adminRevokeSession(userId, sessionId)` → `DELETE /admin/users/{id}/sessions/{sessionId}`）；后端已在 B-19 追加该端点。单会话时不显示"全部下线"（与逐条吊销等价） |
| F-17 | §24.5.3 `GET /admin/quota-requests?status=` | M51 预写的前端类型是 `pending\|resolved\|all`，后端实际只接受 `pending\|approved\|rejected`；响应里的处理说明叫 `resolutionNote`（M51 类型写成 `note`），且 `user` 在账号被清理的极端情形下可为 `null` | `api.adminQuotaRequests('resolved')` 在前端并行取 `approved`、`rejected` 后按处理时间倒序合并（每个状态至多 50 条）；`AdminQuotaRequest` 增 `resolutionNote/granted/resolvedBy`，`user` 改可空，界面对空申请人显示 `—`。`tests/admin-console.test.tsx` 钉住"不会向后端发 `status=resolved`" |
| F-18 | `lib/types.ts` 的 admin 类型（M51 按 §24.5.3 文字猜的形状）× 后端 M50 的真实 schema | 四处不符：①`AdminUserDetail.recentRuns[].createdAt` 实为 `startedAt`（`symbol` 可空，另有 `finishedAt/costUsd`）；②`AuditRow.ipPrefix` 实为 `ip`，`id` 是整数；③`InviteOut` 带服务端推好的 `status`（撤销 > 过期 > 用尽 > 可用）与 `createdBy`；④会话行没有 `current` | 类型对齐后端；`ipPrefix` 留作可选以免旧夹具出错；邀请码状态**以服务端 `status` 为准**，缺省才按字段推。以后端 `app/schemas/admin.py` 为单一真源 |
| F-19 | §24.3.15d "root 额外看到预算与精选自选" × `contracts/permissions.json` | 契约里 `admin.featured.manage` 属于 `admin`（A-15 也让 `service` 持有它），所以"精选自选只给 root"与权限矩阵不一致 | 按规格正文与任务要求：月度预算与精选自选区块**只在 root 视角渲染**（判据 `admin.admins.manage`，root 独有）。服务端并不会因此拒绝 admin——若产品决定 admin 也可管理精选自选，把 `SettingsForm` 里 `FeaturedSection` 的判据换成 `admin.featured.manage` 一行即可 |
| F-20 | §24.3.15b `/admin/**` 的 403 视图与 `?next=` | ①Next 15 在 layout 里无法设置 HTTP 状态码（`forbidden()` 属实验特性 `authInterrupts`，不引入实验开关）；②layout 拿不到子路径，`redirect('/login?next=…')` 只能带 `/admin` | ①403 视图以 200 渲染（文案 + `data-testid="admin-forbidden"`），仍然是"权限视图"而非 404；真正的数据在各接口处被 403 拒绝，状态码对安全无影响；②无 Cookie 时 `middleware.ts` 本就带完整 `next`（含子路径与查询串）；"有 Cookie 但已失效"才落到 layout 的 `/admin` |
| F-21 | §24.3.15d 窄屏导航"沿用 `Tabs` 原语" | Radix `Tabs` 是 `role=tab` 的面板切换，不能承载路由跳转（读屏器会把链接读成标签页且没有对应面板） | 窄屏仍是顶部横向 Tabs 的**外观**，但元素是 `<nav>` 里的真链接 + `aria-current="page"`；同一个 DOM 在 ≥900px 变侧栏（`min-[900px]:flex-col`），不渲染两份导航 |
| F-22 | 全局样式（F-12 的延伸） | 非分层的 `* { border-color }` 与 `button { color: inherit }` 继续压过 Tailwind 工具类：①导航选中态的强调色下划线 / 侧栏强调条画不出来；②**`Button variant="primary"` 的深色文字（`--accent-text`）被覆盖成继承的浅色，绿底白字对比度约 1.6:1**（登录页、工作台的主按钮同样如此，是既有缺陷） | ①`globals.css` 末尾补非分层规则 `[data-admin-nav][aria-current='page']`；②`ui/Button.tsx` 的 primary 变体改为 `text-[var(--accent-text)]!`（Tailwind 重要性修饰符）——**全站主按钮文字由浅变深，与 Logo 的"A"、根管理员徽标一致**；若不想要这条全局视觉变化，去掉那个 `!` 即可（仅此一处） |
| F-23 | AC-202 / check-bundle 预算 | 在**移除全部 M52 代码**的工作树上实测 `/[symbol]` 首屏 = **207.2 kB**；加入后端管理后台后 = **209.2 kB**（+2.0）。后台页面本身不进首屏（`/admin/*` 是独立路由 chunk，echarts 在 `next/dynamic` 的 async chunk 里；首屏 manifest 里没有任何 `components/admin`）。差值来自：`lib/api.ts` 多一个方法、以及新增路由使共享模块被重新切分成更多小 chunk（gzip 按 chunk 分别压，碎片化本身就多 ~1–2 kB） | 阈值**未上调**（245，余量 35.8）。若要把这 2 kB 还回去：把 `api.ts` 里 20 来个 `admin*` 方法挪进一个只被后台 import 的模块（`lib/api.admin.ts`，M51 当时为"一处端点表"放在了 `api.ts`），首屏可再省约 1.5 kB——属于结构调整，未在 M52 做 |
| F-24 | §24.5.3 `GET /admin/audit?from=&to=` | 规格没给时间格式；后端（A-12）对非 ISO 回 `422 invalid_query` | 界面用 `<input type="date">`（本地日），发送时转为**本地日界的 ISO UTC**：`from` = 当天 00:00:00，`to` = 当天 23:59:59.999，所以"选 11-20 到 11-20"覆盖整个本地日 |
| F-25 | §24.5.4 `invalid_setting.detail.field` | 没写字段名用驼峰还是下划线 | 前端把两侧去掉非字母字符并小写后比较（`signup_bonus` ≡ `signupBonus`），命中则把错误落回对应输入框并聚焦；未命中退化为通用 toast |
| F-26 | §24.3.15d 概览"被锁定账号"卡片 | 用户列表的筛选只有 `role/status`，没有"已锁定"过滤（`locked` 是 `active` 账号上的派生标志），所以这张卡无处可跳 | 该卡不做链接，只显示数字与说明；待办注册卡因为有 `/admin/quota` 才可点。后续若给 `GET /admin/users` 加 `locked=1` 过滤，再让它可点 |

### §25 前端（M54–M57）

> ID 前缀 `Y-F`。取证口径：实现期间对照当前工作树与后端 `api/app/schemas/{auth,admin}.py` 的最新模型（以后端 schema 为单一真源）。

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| Y-F1 | §25.3.2(d) / AC-216 拆包 | 拆分前基线（`next build` + `check-bundle`）= **209.3 kB**；`api.admin.ts` 拆出后（并加入全部 §25 前端代码）= **209.0 kB**（−0.3），预算 245 未动。`api.ts` 的 `request` 原是 `createApi` 闭包内的函数，无法被另一个模块复用 | `request` 提为模块级 `makeRequest(ctx)`（`api.ts` 导出，连同 `queryString` / `RequestOptions`）；`createApi` 与 `createAdminApi` 各自调用它。组件与测试由 `api.adminX` 迁到 `adminApi.adminX`（机械替换，行为不变）。`tests/api-credentials.test.ts` 新增：AC-206 的 `Authorization`/`authHeaders` 扫描同时覆盖 `api.admin.ts`；`api.admin` 只被 `components/admin/*`、`app/admin/*` import；**构建产物**里 `/[symbol]` 的首屏 chunk 不含 `/admin/users/bulk-quota`、`export.csv`、`/admin/overview`（无构建产物时 skip） |
| Y-F2 | §25.3.5(a) `exportUrl` | `api.ts` 里已有 `exportUrl(reportId, format)`（**报告**导出）；§25.4.2 写的"导出 URL"若也叫 `exportUrl` 会撞名 | 管理端导出直链命名为 `adminUsersExportUrl(params)` / `adminAuditExportUrl(params)`（`api.admin.ts`），过滤参数名与对应列表接口完全一致（`q/role/status/locked`、`action/actor/target/from/to`），`limit/before` 不适用 |
| Y-F3 | §25.3.3 "`RecoveryKeyDialog`（`next/dynamic`）" | 三个使用方（`RegisterForm`、`RecoverForm`、账号页）本身都不在首屏 chunk（登录/注册是懒加载的 `AuthDialog` 或独立路由；账号页是独立路由），对它再套一层 `next/dynamic` 只会让"注册成功 → 弹窗"多一次异步等待（弹窗是同步 state 驱动的，不该闪） | 静态 import。首屏 manifest 里依旧没有它（AC-216 的构建产物断言覆盖了 admin；恢复码组件不在 `/[symbol]` 的 chunk 里，见 `next build` 路由表：`/[symbol]` 209 kB 未增） |
| Y-F4 | §25.3.3 路径 1 与 `['me']` 缓存 | `api.register()` 的响应体是 `RegisterOut = Me + recoveryKey`。若把整个响应交给 `onSuccess(me)`，下游 `completeSignIn(me)` 会 `queryClient.setQueryData(['me'], me)`——**明文恢复码就进了 React Query 缓存**（还会被 devtools/持久化插件看到），违背"只出现一次" | `RegisterForm` 在 `await api.register()` 之后立刻解构 `const { recoveryKey, ...me } = result`：明文只进组件 state（`issued`）与弹窗；`onSuccess` 拿到的是不含该键的 `Me`。`tests/recovery-dialog.test.tsx` 断言 `onSuccess` 的参数里没有 `recoveryKey` 键、序列化后不含明文 |
| Y-F5 | §25.3.3 账号页"已设置 · 2026-11-20" | 后端 `MeUser` 只有 `hasRecoveryKey: bool`，**没有** `recoverySetAt`（`/auth/me` 不暴露时间）。要显示日期就得多一个字段或多一次请求 | 前端不要求新字段：状态徽标常态显示"已设置"；**本次会话内**生成过新码才显示"已设置 · {本地时间}"（来自 `POST /auth/recovery-key` 的 `setAt`）。若产品想让日期常驻，后端在 `MeUser` 追加可选 `recoverySetAt` 即可，前端类型已按"缺省兼容"写好（改文案一行） |
| Y-F6 | §25.3.4 "`ConfirmDialog` 要求同时输入密码与用户名" | `ui/ConfirmDialog` 只有 type-to-confirm（`requireText`），没有密码输入；密码框需要 `PasswordField`（CapsLock 提示 / 显示切换 / `autocomplete=current-password`） | 新增 `DeleteAccountDialog`（密码 + 键入用户名，`danger` 变体，单飞）与 `RotateRecoveryKeyDialog`（当前密码 → 新码），均建立在 `ui/Dialog` 上；**未改** `ConfirmDialog` 原语（后台 20 余处在用，改它风险大于收益）。"确认注销"在两栏都满足（密码非空 **且** 用户名逐字相等、区分大小写）前 `disabled` |
| Y-F7 | §25.4.2 "`ReportLibraryRow` 的 `only_version` 提示" | `ReportLibraryRow` 是纯展示行，归档的 `catch` 在调用方：`ReportLibraryClient.archive()`（库页）与 `WorkspaceClient.toggleArchive()`（工作台版本切换器）两处——只改 Row 抓不到错误 | 两处 `catch` 都识别 `ApiError(code==='forbidden' && detail.reason==='only_version')`，提示 `copy.errors.archiveOnlyVersion`（info，不是"归档失败"）；这一句放 `copy.ts`（≈60 字节，因 `WorkspaceClient` 在首屏）；`authErrorText` 同时有 `forbiddenOnlyVersion` 供懒加载面使用 |
| Y-F8 | §25.3.2(c) `locked` chip 的"清除" | `UsersTable` 在既有测试里**不在** Next App Router 上下文中渲染（`useRouter()` 会抛 invariant）；而页面是 RSC，`?locked=1` 可由 `searchParams` 直接传入 | `app/admin/users/page.tsx` 读 `searchParams.locked` 传 `initialLocked`；chip 的"清除"是本地 state + `window.history.replaceState(null,'',pathname)`（Next 15 会把它并入路由器状态），不引入 `useRouter`。审计页同理：`?action=` → `initialAction`，只认词表内动作（未知值静默忽略，不报错） |
| Y-F9 | §25.3.6 / AC-236 e2e | `fake-api.mjs` 不实现认证，`playwright.config.ts` 的 webServer 又固定指向它；auth 的 e2e 必须打**真实** enforced 后端并用 `AUTH_TEST_HOOKS` 取验证码答案 | `tests/e2e/auth.spec.ts` 仅在 `E2E_AUTH=1`（+ `E2E_BASE_URL`/`E2E_API_BASE`）且装了 chromium 时运行，否则 skip 并写明原因；文件头给出三段启动命令。**走查证据（AC-235）不由它代替**，由 M58 的真实浏览器走查产出 |
| Y-F10 | `public/sw.js` | 离线壳的 `NEVER_CACHE_PREFIXES` 是 `['/login','/register','/account','/admin']`；`/recover` 页面含恢复码输入与新码展示，同样不得被 SW 缓存 | 追加 `'/recover'`（既有 `tests/sw-isolation.test.ts` 通过 `NEVER_CACHE_PREFIXES` 导出做断言，不受影响） |
| Y-F11 | §25.3.6 无障碍：360 px 无横向滚动 | 恢复码 `K7QM4-XNP2R-ABCDE-12345`（23 字符）在 19–21 px 等宽 + 字距下约 306 px，超过 360 px 屏的弹窗内宽（≈266 px），`break-all` 会把它从一组中间折断，抄写时极易看错 | 字号 `15px / 0.06em`，≥420 px 屏才放大到 `18px / 0.1em`（≈207 px 与 ≈282 px，均不折行）；`break-all` 保留作最后兜底 |
| Y-F12 | §25.3.3 `/recover` 表单字段 | 规格列出"用户名、恢复码、新密码、验证码"，**没有确认密码**；找回是"改密码"且成功即吊销全部会话，新密码打错一个字符＝把自己锁在外面（要再走一遍恢复码，而它已被轮换） | 加"确认新密码"一栏（与注册 / 改密一致；不一致时不发请求，不消耗验证码与恢复码）。这是对规格的追加，不影响任何 AC |
| Y-F13 | §25.3.5(c) "不使用红色以外的警示色" | 既有概览卡用 `--warn`（琥珀）表示"需要注意"；若安全事件卡也用琥珀，同一屏会出现两套"警示"语义 | 失败 / 锁定类（登录失败、账号锁定、找回失败）非零时数字用 `--danger`；找回成功 / 自助注销 / 导出是**信息性**事件，不上色（避免把正常运营动作渲染成告警）。数字旁永远有文字标签，不靠颜色单独表达；失败类显示"尝试次数"，`loginFailuresCapped` 时数字后缀 `+`、提示"5000+ 组" |
| Y-F14 | §25.3.5(b) 复选框的可选范围 | 后端对批量目标是"任一越权 ⇒ 整体 `403`，什么都不写"；若前端让 admin 勾选 admin / root 行，用户只会得到一个整体失败 | 可选性复用 `userActionsFor(me, user).actions.has('adjustQuota')`（§24.3.14 矩阵的体验层）：admin 视角下 admin / root / 已注销行的复选框直接禁用；root 视角可选 admin。筛选条件变化时清空选择（不对看不见的人批量操作）；选满 100 后未选中的框禁用并提示上限。服务端仍是最终裁决 |
| Y-F15 | §25.3.3 步骤 3/4 与验证码一次性 | 服务端在 `verify` **成功**时即消耗验证码；其后第 4 步 `weak_password`、第 5 步 `recovery_invalid` 都发生在"验证码已用掉"之后。前端若只清空输入而不换图，用户的下一次提交必然 `captcha_invalid`（一次无谓的失败 + 一条被浪费的限速额度） | `RecoverForm` 对 `weak_password` / `recovery_invalid` / 其它服务端拒绝一律**换一张新验证码**并清空答案；`weak_password` 聚焦密码框（恢复码仍有效，AC-221），`recovery_invalid` 聚焦用户名并附"联系管理员"出口 |

**测试**：新增 `tests/recover-flow.test.tsx`（20：遮罩归一化 / AC-239 前端 / 找回表单全部失败分支 / AC-250 单飞 / 登录页"忘记密码？"）、`tests/recovery-dialog.test.tsx`（12：Esc 与遮罩不关闭 / 复制 / 下载 / 跳过 / 注册后先弹窗且明文不进 `onSuccess` / 轮换单飞）、`tests/account-delete.test.tsx`（13：恢复码卡 / 未保存提示条 / 危险区双确认 / root 不渲染）、`tests/admin-bulk.test.tsx`（23：复选与工具条 / ±50 / 上限 100 / locked chip / 导出直链 / 安全事件卡 / `?action=` 还原 / 设置开关 / 创建文案）；改 `api-credentials`（+8）、`authErrors`、`admin-console`（审计词表 31→36）。`tests/e2e/auth.spec.ts` 新增（需真实栈，见 Y-F9）。

### §25 后端（M55–M56）

> ID 前缀 `Y-B`（迭代 2 / 后端 / 恢复码与自助注销）。本小节只含 M55–M56 的实现发现；M53/M54/M57 的发现由 be-admin 节点另起小节。

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| Y-B-1 | §25.3.3「`_SENSITIVE_KEY` + `recovery` 关键字」× AC-243 | 照字面把 `recovery` 加进 `audit_service._SENSITIVE_KEY`，`sanitize_detail` 会**递归丢弃所有含 `recovery` 的键**——包括 AC-243 要求审计 `from/to` 里的 `authRecoveryEnabled`（`settings.update` 的 `detail.changes`），以及 §25.3.5(c) 的 `recoveryFailures / recoveries` 一类键。实测：设置变更审计里该字段整个消失，`KeyError` | 关键字收窄为 `recovery[_-]?(key\|hash)`：仍挡住 `recoveryKey / recovery_key / recovery_hash`（AC-249 的 `sanitize_detail({"recoveryKey": …})` 照丢），但不再误伤开关名与统计名。`logging.py` 的两条正则本来就是 `recovery[_-]?key`，无此问题 |
| Y-B-2 | §25.3.3 步骤 2「`auth.recover`（IP，`hit`）」 | `auth.recover` 的 IP 桶在 `security/deps.enforce()` 里按策略表 `rate="auth.recover"` 命中（与 `register/login` 同构），**不在 service 里**。所以"限速"这一步被拆成两处：IP 桶在路由依赖里（先于 `recovery_disabled` 判断），全站桶与用户名桶在 `auth_service.recover()` 里。service 里再 `hit` 一次 IP 会让每次请求记两次，第 3 次就 429 | service 只做 `auth.recover.global`（`check`）与 `auth.recover.user`（`guard` → 验证码通过后 `record`）；IP 桶留给策略表。副作用：总开关关闭时仍会消耗 IP 配额，无安全影响 |
| Y-B-3 | §25.3.4「`anonymize` 之后的终态」 | `user_service.anonymize()` 不清 `recovery_hash/recovery_set_at`（它是迭代 1 的函数，不认识这两列）。墓碑行若留着 hash，`export.csv` 的 `hasRecoveryKey` 对已注销用户为真，也违背"注销即忘" | 清除放在 `account_lifecycle.delete_account` 里（紧跟 `anonymize`），所以管理员路径与自助路径**同时**生效；未改 `user_service`。`test_account_delete::test_self_and_admin_deletion_leave_the_same_final_state` 的快照含 `recovery_hash` |
| Y-B-4 | §25.7 AC-240「在第 5 步与第 6 步之间插入屏障的测试钩子」 | 没给钩子形状。用"两个线程 + 屏障"需要两条 SQLite 写连接互等，易死锁/不确定 | `auth_service._after_recovery_verify`（模块级可置换的 `Callable \| None`，生产恒 `None`）。测试把它换成"跑一个完整的第二次 `recover`"的函数：外层请求已校验码、尚未 CAS 时，内层请求完整完成（赢家），外层随后 CAS 失败。**单线程、确定性**，且正好是"双击"的交错。断言：内层 200、外层 `401 recovery_invalid`、库里 hash = 赢家新码的 sha256、密码 = 赢家的、审计 `auth.password.recover` 恰 1 条 + `auth.recovery.failure` 恰 1 条 |
| Y-B-5 | §25.5.4 `POST /auth/recovery-key  rate=auth.password` × §25.3.3 | 策略表的 `rate` 是**按 IP** 命中，service 里的 `auth.password` 与 `auth.recovery_rotate` 是**按用户 id**——同一个 scope 名、两种 key，互不干扰；`DELETE /auth/account` 在 §25.5.4 里没有 `rate`，但"猜密码"口子同 `change_password` | `recovery-key` 路由按规格登记 `rate="auth.password"`（IP），service 另按用户 id 检查 `auth.recovery_rotate`（3 次/60 s）与 `auth.password`；`DELETE /auth/account` 保持策略表无 `rate`，service 内按用户 id 检查 `auth.password`（10 次/10 分钟）。顺序：限速 → root（`403 root_protected{action:"recovery_key"}`）→ 总开关（`403 recovery_disabled`）→ 当前密码。root 与开关判断放在验证密码**之前**：它们不依赖秘密，且让 root 总是得到确定的 403 而不是"密码对不对"的旁路 |
| Y-B-6 | §25.3.4 `assert_can_self_delete` 与 `_require_user` | 路由层 `_require_user` 已对 `service` 抛 `Forbidden(account.self)`、对游客抛 401，`assert_can_self_delete` 里的 `kind != "user"` 分支因此在 HTTP 上不可达 | 保留该分支（纵深防御，直接调 service 层/CLI 不经路由时仍成立）；两处语义一致，已由 `test_the_service_principal_and_guests_are_refused` 覆盖 HTTP 行为 |
| Y-B-7 | §25.4.1 `cli.py clear-recovery`「审计 `actor=system`」 | 动作词表只加了 5 个（31→36），没有"清除恢复码"的专用动作 | 复用 `auth.recovery.rotate`，`detail = {via:"cli", osUser, cleared:true, hadKey}`、actor=`system`。不为它新增词条（保持 36） |
| Y-B-8 | 测试基础设施：注册验证码的 1.2 s 下限 | `solve_captcha` 只在**冻结时钟**下才会把时钟拨过 1.2 s；`register` 的"过快"陷阱对真实时钟必然拒绝一张刚签发的验证码，于是不冻结时钟的恢复码测试连注册这一步都过不去 | `tests/test_recovery.py`、`test_account_delete.py` 各加 autouse `frozen_now`。AC-239 的"`recover` 验证码不受 1.2 s 约束"用 `solve_captcha(settle=False)` 在冻结时钟下 0 s 提交来证明 |
| Y-B-9 | 既有测试的必要迁移 | 总数断言随新增而变：`test_policy_table`（`len(POLICIES)` 82 → 88，+3 auth +3 admin）、`test_audit`（`ACTIONS` 31 → 36；"每个动作被真实流程触发"场景追加 recover 失败/成功、轮换、自助注销、`admin.export`）、`test_admin_api::test_settings_defaults_and_roundtrip`（`GET /admin/settings` 多一个 `authRecoveryEnabled`）。三处都只追加，没有改其它断言 | 同左；`admin.export` 那一步依赖 be-admin 的 `GET /admin/users/export.csv`，在两个 fork 都落地后一起实跑为绿 |
| Y-B-10 | AC-219「耗时差在同一量级」 | 墙钟断言在 CI 上是噪声源 | 用**工作量**断言代替：对 `recovery.verify` 打桩计数，八类失败各恰好一次比较，且"不存在"路径比较的是 `recovery.DUMMY_HASH`；另有一条对纯函数的粗粒度计时（0.2×–5×）。失败路径本身不含 scrypt（成功路径才哈希新密码），所以六类失败之间没有数量级差 |
| Y-B-11 | 测试 / 前端：`DELETE /auth/account` 带请求体 | Starlette `TestClient.delete()` 不接受 `json=`；浏览器 `fetch(…, {method:'DELETE', body})` 可以 | 测试用 `client.request("DELETE", url, json=…)`；`api.ts` 的 `deleteAccount` 须走通用 `request()`（带 body 的 DELETE）。FastAPI 端无需特殊处理 |

### §25 后端（M53/M54/M57）

> ID 前缀 `Y-A`（迭代 2 / 后端 / 清理·遗留风险·管理效率），由 be-admin 节点追加；M55–M56 的发现见上一小节（`Y-B`）。

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| Y-A1 | §25.3.1(a)、E-17：游离文件 `docs/plans/report-library/probe_db.py` | 默认处置是"移出仓库"，但**已提交**的 `docs/plans/report-library/spec.md` 有 5 处把它当作 E-119~E-125 的**可复跑证据**引用（O-32/O-40：「本文档的每个数字都能用一条命令重算」）。删除它会让一份已提交文档的复核命令悬空；它是只读探针（`mode=ro`），并非垃圾产物 | **保留**该文件（未删、未改）；删除了 `api/pytest-out.txt` 与 `scripts/_patch_tmp.py`，`.gitignore` 追加 `api/pytest-out*.txt`、`**/_patch_tmp.py`。终审节点提交时请**把 `probe_db.py` 一并提交**（它是 §23 spec 的一部分），或在 §23 spec 里删去引用后再移出——二选一，不要留成游离 `??` |
| Y-A2 | §25.5.4「`rate=admin.export`」× `security/deps.py:204` | 策略表的 `rate` 由 `enforce()` 按**客户端 IP**（`principal.ip_key`）计数；而 `admin.export` 的语义是**按用户**（§25.3.5a：60 s/6）。两处都挂会让同一次导出在 IP 桶与用户桶各计一次（同一个 scope 名、两种 key），且 NAT 后的多个管理员会互相挤占 | 两条导出路由在策略表里**不写 `rate=`**（`object_rule` 里写明"服务内按用户限速"），限速只在 `export_service._begin` 里 `ratelimit.check("admin.export", user_id)` 一次，且发生在审计 + commit **之前**（被拒绝的导出什么都不写）。`test_admin_export::test_export_is_audited_and_rate_limited_per_user` 钉住（第 7 次 429、审计恰 6 行、另一个管理员不受牵连） |
| Y-A3 | §25.3.5(a) 审计导出 × AC-229/AC-247 | `admin.export` 审计行在**第一个字节之前**写入（RV-Y9），而审计导出读的正是 `audit_log`——于是导出会把"自己的那一行"也带出来，`COUNT`（事先取）≠ 输出行数，AC-247 的"未超限时输出 == COUNT"对审计导出不成立 | `audit_service.filtered()/query()` 增 `max_id` 快照上界：先取 `MAX(id)`，`COUNT` 与读取都带 `id <= max_id`。导出是**一致快照**，不含自己那行（列表接口之后读才有）；`test_audit_export_matches_the_list_and_excludes_its_own_row` 钉住。顺带把 `query()` 的 WHERE 抽成 `filtered()`——列表与导出共用同一个过滤函数（§25.3.5a 要求"不复制 WHERE 逻辑"），`query(max_limit=)` 让导出的块大小（500）不被列表的 200 上限卡住 |
| Y-A4 | §25.3.1(b)「单轮上限 `batch×5`」 | 朴素实现里两条谓词各自循环、各 5 批 ⇒ 一次调用最多删 20 000 行，AC-209 的"12 000 死行一次调用后剩余 > 0"不成立；而把"空探测批"也算进 5 批则一次只能删 8 000 行（第一条谓词没有命中行，却吃掉 1 批预算） | 两条谓词**共享**一个 5 批的预算，且**只有删到行的批才计数**（空探测不花预算）。`purge_dead(12 000 行) → 10 000，再 → 2 000，再 → 0`；`PURGE_BATCH/PURGE_MAX_BATCHES` 是模块常量（测试直接引用）。`retain_days<1` 夹到 1，避免配置成 0 时把"刚过期"的行也清掉 |
| Y-A5 | §25.3.5(a)「生成器自己开 `SessionLocal()`」× 测试夹具 | `from ..db.session import SessionLocal` 在导入时绑定了**模块级旧工厂**；测试夹具 `_wire_app` 只 monkeypatch `app.db.session.SessionLocal` 与 `main.SessionLocal`，于是导出会连到真实库而不是测试库（生产里两者是同一个，只有测试里不同） | `export_service` 用 `from ..db import session as db_session` 并在**调用时**取 `db_session.SessionLocal()`。`_purge_sessions_if_due` 在 `main.py` 里按名取 `SessionLocal`（夹具本来就会替换它） |
| Y-A6 | §25.3.5(a) `X-Export-Truncated` × CORS | 开发期前端（`:3000`）与 API（`:8000`）跨源，浏览器只暴露"安全清单"里的响应头；`X-Export-Truncated` 不在其中，前端读不到"被截断"这个提示（`Content-Disposition` / `X-Request-Id` 已在 `expose_headers`） | `CORSMiddleware(expose_headers=…)` 追加 `X-Export-Truncated`（同源生产无碍）。前端可据此提示"仅导出了前 50 000 行" |
| Y-A7 | §25.3.2(c) `locked` 与 `status` 同时给出 | `locked=1` 的含义是 `status='active' AND locked_until > now`；若同时传 `status=disabled`，两个条件互斥 | 互斥组合直接返回空（不报错、不查库）；`locked` 与 `q/role` 可组合；`locked=0/缺省` 的行为一字未变（`user_filters()` 是列表与导出共用的唯一 WHERE 构造器，AC-195 的语句数仍 ≤ 6，且与页大小无关） |
| Y-A8 | §25.3.5(b)「第 2 遍逐个调用既有 `set_quota` 的 `grant` 分支」 | 需要知道每个目标"是否真的写了账本"才能区分 `applied/skipped`，而 `set_quota` 返回的是 `QuotaStatus`，看不出被夹成 0 的人 | 把 `set_quota` 里的 `grant` 分支原样抽成 `_apply_grant(…) -> bool`（返回"是否写了账本行"），`set_quota` 与 `bulk_grant` **共用**它——语义一字未改（夹成 0 不写账本也不审计），`set_quota` 增 `audit_extra`（合并进 `quota.grant` 的 `detail`）。`BulkQuotaRequest` 在 schema 层与服务层**各校验一次** `-50..50 且 ≠0`（服务层引用 `quota_service.GRANT_MIN/MAX`，不另写数字），所以直接调用服务也不会走到 `quota_service.grant()` 的 `ValueError` ⇒ 500 |
| Y-A9 | AC-247「在第一个字节时审计已落库」的可测性 | Starlette `TestClient` 在返回响应对象前就把整个 body 读完，**观察不到"第一个字节"这个时刻** | 测试直接调用 `export_service.users_csv()`：它返回 `StreamingResponse` 但生成器**尚未被迭代**——此刻断言审计行已落库、限速已记录（再 5 次后桶满），然后才消费 `body_iterator`。比"读到第一个字节"更严格（还证明了生成器自己开会话，与请求会话无关） |
| Y-A10 | `tests/test_policy_table.py` 的总数/分族计数 | 总数（82 → 88）由 be-recovery 先改，分族计数（`admin` 22、`auth` 11）没有同步，导致本节点新增的 3 条 `/admin` 路由使 `test_pending_routes_are_only_the_me_and_admin_families` 变红 | 分族计数改为 `admin: 25`、`auth: 14`（与 `POLICIES` 实际一致）；只改计数，不改其它断言 |
| Y-A11 | `test_authz_routes.py::test_q9_owner_cannot_archive_the_current_latest_but_admin_can` | 迭代 1 的"Q-9 临时方案"（归档最新版 ⇒ 要求 `report.manage_any`）被 D-11 取代；该用例钉的就是被取代的行为 | 用例重写为 4 条：①有后继者 ⇒ 指针交接（`older` 成为 `is_latest`，状态恢复 `ready`）；②唯一版本 ⇒ `403 forbidden{reason:"only_version"}` 且库无变化、admin 仍可归档；③同一夹具下"归档最新版"与"删除最新版"选出同一个后继者 v3（含 `deleted` v1 / `archived` v2 / `provisional` v5 干扰项，无 `IntegrityError`）；④`{archived, pinned}` 非 `manage_any` ⇒ 403 且三个旗标都未变。`routers/reports.py` 里原来那段临时的 `assert_can_set_curation_flags` 删除，判定下沉到 `report_service.set_flags`（在任何字段被改之前，原子失败） |

**验证**：`ruff check .` 干净；新增 `test_session_purge.py`（7）、`test_migration_0008.py`（7）、`test_admin_export.py`（40）、`test_admin_hardening.py`（9）；既有 `test_authz_routes / test_admin_api / test_audit / test_policy_table / test_security_boundaries / test_boundaries / test_report_library_api / test_reports_api / test_quota_engine` 与四个迁移测试文件保持绿（迁移文件只改了 head 断言 `0007 → 0008`，`test_cost_ledger_migration` 的链断言前置 `0008_account_recovery`，B-18 先例）。

### 后端（M45–M47）

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| B-1 | §24.5.4 `weak_password.detail.rules`、§24.7.1 "策略 8 条规则" | 规格要求"列出未满足的规则 id"，**没有给 id 词表**；前端（F-7）已按自己的 7 个 id 实现清单。另外，把"等于用户名"与"包含用户名"拆成两个 id，前端清单里没有对应行 | **采用前端词表，后端对齐**：`min_length / max_length / contains_username / char_classes / common_password / repeated_chars / sequential_chars`（`security/passwords.py::ALL_RULES`，顺序即返回顺序）。"等于用户名"是"包含用户名"的特例，同一个 id；`sequential_chars` 取与 `web/lib/passwordPolicy.ts` **同一个定义**（整串是严格 ±1 的码点连续段、长度 ≥ 4），键盘行（`qwertyuiop` 等）改放弱口令表，于是前端的实时清单与服务端判定不会在这一条上互相矛盾。`tests/test_security_passwords.py::test_the_rule_ids_are_the_ones_the_front_end_renders` 在 `web/lib/passwordPolicy.ts` 存在时逐个比对联合类型 |
| B-2 | §24.3.4 `service.minus` × 附录 A | `service` 的收窄去掉了 `report.manage_own`，而附录 A 把 `PATCH/DELETE /reports/{id}`、`POST …/restore`、`POST /runs/{id}/cancel` 的**路由级**权限写成 `report.manage_own`。按字面实现，"可以整理报告"的 service 主体在路由门口就被 `403`，§24.3.4 正文自己说它"走 `manage_any`"——两处互相打架 | 在权限层加蕴含：`security/permissions.py::IMPLIED_BY = {"report.manage_own": {"report.manage_any"}}`，`Principal.can()` 用它；`contracts/permissions.json` **一字未改**（TS 侧不受影响）。`test_contract_permissions.py` 钉住"service 无 `manage_own` 但路由级通过" |
| B-3 | §24.6.2 `ix_reports_created_by` | 设计稿谓词 `WHERE deleted_at IS NULL` 与 0006 的 `ix_reports_feed` **谓词相同**；SQLite 对"无 ORDER BY 的 all/starred 扫描"改选这个更窄的索引，既有的 `test_migration_0006.py::test_each_scope_uses_its_partial_index`（O-44）随即变红——真实的计划回退，不是测试脆弱 | 谓词改为 `WHERE created_by IS NOT NULL AND deleted_at IS NULL`（模型与迁移同步）：`created_by = ?` 的查询照样蕴含它，feed/starred 的扫描不再蕴含（NULL 的历史行本来也不需要这个索引）。既有测试**未改** |
| B-4 | §24.6.1 `quota_ledger.ref_id VARCHAR(32)` | 追问 id 是 `"ask_" + uuid4 hex`（36 字符，`report_asks.id` 是 `String(40)`）。SQLite 不在乎，Postgres 会拒绝/截断，且 `ux_quota_ref_reason` 的幂等键 `(ref_id, reason)` 会因截断而撞车 | `quota_ledger.ref_id` 用 `String(40)`（模型 + 迁移）。`test_migration_0007.py::test_ask_ids_fit_the_ledger_ref_column` 钉住 |
| B-5 | E-13 / §24.4.1 `security/deps.py` | E-13 写"`security/` 不可 import `services/`"，而 §24.3.3 的 `get_principal` 必须经 `session_service` 把 Cookie 解析成会话——两条规则对 `deps.py` 不能同时成立 | `security/deps.py` 是 HTTP 适配层，视为路由同侧，**唯一**允许 import `services/` 的 `security` 模块；其余 `security/*` 与 `services/` 不得 import 它。新增 `tests/test_security_boundaries.py`（AST 判定 + 正控）：security 除 deps 外不 import services/routers；services 不 import `security.deps`；`agent/` 不认识 security；只有 routers 与 `main.py` import `security.deps`。`security/__init__.py` 不做急切导入以免成环 |
| B-6 | §24.5.4 `captcha_invalid.detail?:{challenge?}` × AC-168 | AC-168 要求蜜罐 / 过快 / 验证码错的响应"**字节级相同**（除 requestId）"；若 `captcha_invalid` 附一张新图，每次响应都不同（随机 id 与像素），AC 不可达 | `captcha_invalid` 的 `detail` **恒为 `{}`**，前端用 `GET /auth/captcha` 换图（`detail.challenge` 在规格里本就是可选）。`422 captcha_required`（登录）仍带 `detail.challenge`，因为那是"第一次被要求"，没有可对比的第二个响应。`test_auth_register.py::test_honeypot_and_too_fast_look_exactly_like_a_wrong_captcha` 比对整份响应 |
| B-7 | §24.3.6 与 AC-167 的"尺寸 160×56" | 同一份规格一处说"尺寸 160×56，@2x 出图 320×112"，AC-167 写"PNG、尺寸 160×56"。字面逐处照做不可能同时成立 | 以 §24.3.6 的设计意图为准：**PNG 为 320×112（@2x），JSON 的 `width/height` 为 160×56（CSS 像素）**——高分屏清晰且布局稳定。AC-167 的"尺寸"按 JSON 字段与 CSS 渲染尺寸解读。另：实测 PNG ≈ 19 kB、渲染 ≈ 18 ms（规格估计 3–5 kB / 3–6 ms），仍在 `auth.captcha` 30 次/分钟的预算内 |
| B-8 | AC-166 × §24.3.7 限速表 | AC-166"同一 `challengeId` 第 6 次尝试作废"若经 HTTP 打 6 次 `POST /auth/register`，先撞上 `auth.register` 的 **5 次/10 分钟/IP** 限速（`429`），验证的不再是验证码 | AC-166 在**提供者层**断言（`CaptchaProvider.verify` 连发 6 次）；HTTP 层单独断言"第 5 次仍可成功"与"第 6 次注册 429"。两条守卫各自可测 |
| B-9 | §24.3.7 锁定的边界行为 | 规格没写：①触发锁定的**那一次**失败回 401 还是 423；②锁到期后计数是否清零 | ①触发锁定的那次即回 `423 account_locked`（用户立刻知道被锁了，而不是再点一次才知道）；存在/不存在用户名同样（AC-170）。②**不清零**：锁到期后 `failed_logins` 仍 ≥ 3，所以先要验证码，再错一次就重新锁 15 分钟；登录成功才清零。影子节流器逐字同构 |
| B-10 | §24.3.6 `issuedAt` | 规格"`issuedAt` 到提交少于 1.2 s 视为脚本"，前端按"验证码**签发**时刻（客户端 `Date.now()`）"传。客户端钟比服务端**快**时差值为负，按字面 `< 1.2` 会把所有快钟用户永久挡在注册之外 | 客户端 `issuedAt` 只在 `0 ≤ 差值 < 1.2 s` 时算过快；负值放行。权威的"过快"是服务端自己量的——验证码仓库里存了签发时刻，`purpose=register` 时答案早于 1.2 s 一律按错误处理（不可伪造）。登录验证码不设此下限 |
| B-11 | §24.5.1 `registrationOpen`、AC-172 | AC-172 要求 Pillow 不可用时 `registrationOpen=false` **而邀请码注册仍可用**：这个布尔值同时承载"模式 closed"与"验证码不可用"两种意思（前端 F-8 已用 `registrationMode` 区分） | `registrationOpen = (mode == 'open' 且验证码可用)`；**追加**两个字段（只追加，不改既有键）：`captchaAvailable`、`inviteRegistrationOpen`（`mode ∈ {open, invite}`）。前端无需改动，可选择性使用 |
| B-12 | D-8 / §24.3.13 `lifespan` | 规格写"`ensure_root_admin` 在 lifespan 里调用"，未区分模式。`AUTH_MODE=open` 下没有人需要登录；而每个用到 `client` 夹具的既有用例（≈ 数百条）若都付一次 scrypt（≈ 80 ms）建 root，全量测试多出数十秒且毫无意义 | `open` 模式**跳过**引导（启动日志里已有 ERROR 横幅）；改回 `enforced` 重启时才引导。`ensure_root_admin()` 函数本身与模式无关，`test_bootstrap_root.py` 直接调用它 |
| B-13 | 测试基础设施 | `reports ↔ runs` 是故意的外键环（"Can't sort tables for DROP" 已在 pyproject 过滤），`drop_all` 因此无法拓扑排序。新表 `user_watchlist` 有**两个**父表（`users`、`instruments`）：SQLite 外键开启时，`DROP TABLE users` 的隐式 `DELETE` 会撞上"另一个父表 `instruments` 已被先删"并报 `no such table: main.instruments` | `tests/conftest.py` 的建库夹具改用 `_drop_all()`：仅在该次清理的连接上 `PRAGMA foreign_keys=OFF`，清完再 `ON` 后归还连接池。应用代码与迁移不受影响（迁移的 `downgrade()` 有显式的逆序） |
| B-14 | `services/sse_token.py`（既有缺陷，M46 顺手发现） | `verify_sse_token` 对 `?token=<非 ASCII>` 调 `hmac.compare_digest(str, str)`，**抛 `TypeError`**——一个乱码令牌得到 500 而不是 401（`test_sse_token.py::test_signature_comparison_survives_non_ascii_garbage` 复现） | 两侧先 `.encode("utf-8")` 再比较（字节比较） |
| B-15 | §24.3.9(a)/§24.3.11 与既有 4 处 SSE 用例 | 迁移后 `test_ask_api.py::test_events_token_is_bound_to_the_ask` 的 `events-token` 在 M49 会受"拥有者或管理员，否则 404"约束；该追问是 `open` 模式下由 service 创建的（`user_id IS NULL`） | 该用例迁移时**保留** `api_token` + `Authorization: Bearer`（service 主体对追问对象级规则天然通过），同时加 `auth_mode=enforced` + `session_secret`。M49 无需再改这一条 |
| B-16 | §24.3.7 限速表 | "改密码"是"已被盗会话猜当前密码"的口子，限速表里没有对应 scope | 追加 scope `auth.password`（10 次 / 10 分钟 / 用户 id）。纯增量，不影响其它 scope |
| B-17 | §24.7.2 e2e 钩子 | `GET /auth/_test/captcha-answer` 要"**不在策略表内**、`verify_against_app` 对该前缀豁免"，但规格没给豁免的写法 | `policy.EXEMPT_PREFIXES = ("/api/v1/auth/_test/",)`；路由由 `create_app()` 在 `AUTH_TEST_HOOKS=1` 时才 `include_router`（**不**挂 `enforce`）。验证码的明文答案只在 `AUTH_TEST_HOOKS=1`（或测试夹具显式打开）时才进侧表。`test_policy_table.py` 有"默认 404"与"开关后可用"两条 |
| B-18 | §24.7 既有测试的"必要改动" | 迁移使 head 前移，`test_migration_0004/0006.py`、`test_cost_ledger_migration.py` 里 7 处"head == `0006_report_library`"必然失效（前几轮每加一个迁移都改过同类断言，`★v7：head 已经是 0004` 的注释即是先例） | 仅把 head 断言改为 `0007_users_auth`，并让 `test_migration_0006.py` 里"只验 0006 本身"的两处改为 `upgrade(config, "0006_report_library")`；`walk_revisions` 的期望链前置 `0007_users_auth`。除此之外与 §24.4.1 列的 4 处 SSE 用例外，**没有**改任何既有断言 |
| B-19 | §24.3.15d 用户抽屉 × §24.5.3 | 抽屉要求"活动会话可逐条吊销"，§24.5.3 只有 `revoke-sessions`（全部吊销）。前端（M52）已按此接线 | 追加 `DELETE /admin/users/{user_id}/sessions/{session_id}` → 204（`admin.users.manage`，对象级矩阵同 `revoke-sessions`；会话须属于该用户否则 404；审计 `auth.session.revoke`）。已登记进策略表（`PENDING_ROUTES`，M50 实现时删除）。 |

### 后端（M50 管理后台 API + M52 的后端 / 运维部分）

> ID 前缀 `A-`（admin）以免与 M48–M49 小节的编号撞车。

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| A-1 | §24.5.3 `GET /admin/users` | 规格没说**注销（墓碑）用户**是否出现在列表。墓碑的 `username_lc` 是 `deleted:<id>`，而 `q` 的范围查询 `username_lc >= 'd' AND < 'e'` 会把**所有**墓碑当成 `d…` 前缀命中，日常搜索会被它们淹没 | 默认 `status != 'deleted'`；显式 `status=deleted` 才列出墓碑。`total` 与列表同一过滤条件。概览的 `users.total/byRole` 同样不含墓碑 |
| A-2 | §24.5.3 `AdminUser.invitedBy` | 没说内容。完整邀请码是一次注册凭据，而 `admin.users.read` 也授给了 `service`（它按 §24.3.4 看不到 `/admin/invites`） | `invitedBy` = 邀请码的备注 `note`，无备注则 `K7QM-****`（打码）。审计里邀请码同样打码（`target_name`） |
| A-3 | §24.5.3 `AdminUserDetail` | 只写了"含 quota、近 20 次 run、活动会话、failedLogins、lockedUntil"，没有形状 | 平铺：`AdminUser` 全部字段 + `disabledReason/disabledAt/mustChangePassword/passwordIsDefault/failedLogins/lockedUntil/lastLoginIp/createdBy(用户名)`；`quotaStatus` = 额度引擎的完整 `QuotaStatus`（读前会定向对账）；`recentRuns`（20 条，含 `symbol/status/costUsd`）；`sessions`（活动会话，**无** `token_hash`）。`lockedUntil` 仅在仍处于锁定时给出（过期的锁定时间戳对管理员是噪声） |
| A-4 | §24.5.3 `DELETE /admin/users/{id}` × §24.5.4 | AC-197 要求 `confirm` 不符 → `422`，但 §24.5.4 码表里**没有**对应的码；为它新增一个码意味着前端 `authErrors.ts` 要同步（该文件有"每个码有文案"的守卫） | 复用既有的通用码 `validation_error`（422），`detail={"field":"confirm"}`。`confirm` 既可在 query 也可在 body（两处都收，优先 query）。删除还会：吊销其全部会话、清其个人自选（`user_watchlist`）、把其 `pending` 额度申请自动置 `rejected`（备注"账号已注销"，否则申请队列里会永远挂着一个无主申请）；账本与审计保留 |
| A-5 | §24.5.3 `POST /admin/users/{id}/reset-password` | 规格没提锁定。重置密码的最常见原因是"忘记密码"，而忘记密码的人此刻多半已被 §24.3.7 锁了 15 分钟——管理员刚给了新密码，用户却登不进去 | 重置同时 `unlock`（清 `failed_logins/locked_until`）。审计仍是 `auth.password.reset`（detail 含被吊销的会话数）。**这是对规格的追加，不是改写** |
| A-6 | §24.5.3 `PATCH /admin/users/{id}` | 一个请求可同时带 `displayName/role/status`，规格没说其中一项不被允许时怎么办 | **两遍制**：先对**每一项**过矩阵（`assert_can_manage_user`），全部通过才写；任一项被拒则整个请求失败、什么都不生效（`test_a_half_allowed_patch_applies_nothing`）。"改成当前值"是静默的 no-op（不写审计）。停用同时吊销其会话（`revoked_reason='disabled'`；`resolve()` 的 join 本来就会让它们失效，这里是让库里的状态也诚实） |
| A-7 | §24.5.3 `POST /admin/users/{id}/quota` | `dailyLimit` 要区分"没传"/"`null`（跟随默认）"/`-1`/`N`；`grant` 扣回量夹到当前赠送余额，夹成 0 时账本无新行 | 用 Pydantic 的 `model_fields_set` 区分"没传"与 `null`；至少要有 `grant` 或 `dailyLimit` 之一（否则 422）；二者可同请求。夹成 0 的扣回**不写审计**（审计记录"发生了什么"而不是"想做什么"；`detail.requested` 保留请求量）。额度引擎自己不写审计（`quota_service` 文档："caller audits"），所以 `quota.grant` / `quota.limit.set` / `quota.request.approve|reject` 的写入点在 admin 层；`quota.request.create` 在 `/me` 路由 |
| A-8 | §24.5.3 `PATCH /admin/settings` | 规格要求"每个字段独立校验，非法 → `422 invalid_setting(detail.field)`"。若用带类型的 Pydantic 请求体，类型错（`"3"`、`3.5`、`true`）会变成通用的 `validation_error`，`detail.field` 丢失；而且 Python 里 `True` 是 `int`，会被悄悄当成 `1` | 请求体收**原始 dict**，由 `settings_service.validate_field()` 严格校验：`bool/str/float` 一律拒绝，未知字段名也是 `invalid_setting(field=<它>)`；**先全部校验再写**（任一坏字段整个请求不生效）；空体 → `invalid_setting(field="")`；仅在**确有变化**时写审计（`detail.changes={field:{from,to}}`）。每次请求读库，无缓存（AC-199） |
| A-9 | §24.5.3 `GET /admin/overview` | 规格没给口径：什么叫"今天""失败""活跃" | **"天"= `QUOTA_TZ` 的自然日**（与额度引擎同一个日界，上海的管理员看到的"今天"就是用户眼里的今天；窗口边界用日历日算，不是 `+24h`）。`failed` = `failed|timeout|budget_exceeded`；`cancelled` 计入 `today` 但既不算成功也不算失败（那是用户自己的选择）；`successRate = succeeded/(succeeded+failed)`，无已完成 run 时为 `null`。`active7d` = `last_login_at` 在 7 天内（不是会话活跃度——不新增写路径）。`consumedToday` = 当日 `day_key` 的 `run/ask` 扣减总额；`refundedToday` = `day_key` 为今日的退款行——**退款沿用原扣行的 `day_key`**（§24.3.8(d)），所以"今天退了昨天的 run"不计入今天的退款，这是该口径的必然结果而非遗漏 |
| A-10 | AC-194 性能 | 第一版 14 天序列用"一次扫描 + 14 个 `SUM(CASE…)`"，2 万用户 **45.7 ms**，**超过** 40 ms 的门（逐项实测：`users` 11 ms、`signups` 23 ms、`runs` 2 ms） | `signups` 改为 **14 个标量子查询**（每个是 `ix_users_list` 上的纯索引范围计数，开销随"天数"而不是"行数"）：23 → 4.7 ms；`users` 改为 `GROUP BY status, role`（`ix_users_status_role` 即可满足，不读表行）+ 一次仅含 2 个时间条件的扫描：11 → 9 ms。整体 **≈ 20 ms / 2 万用户**。`test_overview_is_fast_on_20k_users` 取 5 次中最好一次 < 40 ms |
| A-11 | AC-195 `EXPLAIN QUERY PLAN` | 担心带 `ORDER BY created_at DESC` 的列表会让规划器改走 `ix_users_list` 而不是 `ux_users_username_lc` | 实测规划器选了 `ux_users_username_lc`（范围约束优先于有序扫描），`test_the_prefix_query_uses_the_username_index_and_is_not_a_like` 钉住"计划里含该索引且语句里没有 `LIKE`"。列表的语句数：会话解析 1 + `COUNT` 1 + 页 1（`LEFT JOIN invite_codes` 取邀请标签，不是第二条查询）+ 账本 `GROUP BY` 1 + 默认额度设置 1 = **5**，与页大小无关（≤ 6 的门） |
| A-12 | §24.5.3 `GET /admin/audit` | 规格没说未知 `action` / 非法 `from`,`to` 怎么办；静默返回空会掩盖拼写错误 | 未知 `action`、不是 ISO 的 `from`/`to` → `422 invalid_query(detail.field)`；坏游标 → `422 invalid_cursor`。`detail_json` 在响应里是**解析后的对象**（`detail`），不是字符串 |
| A-13 | §24.3.10 审计覆盖 | "词表里每个动作都有写入点"若只靠 grep，证明的是字符串**写在**某处，不是它**会被执行** | `tests/test_audit.py` 的 `PENDING_ACTIONS` 清空（机制保留），并新增 `test_every_action_in_the_vocabulary_is_written_by_a_real_flow`：一个场景经真实 HTTP / CLI / 引导把 **31 个动作全部**各触发一次并断言落库，同时断言没有写入词表之外的动作。`quota.request.create`、`schedule.*`、`report.manage_other`、`run.cancel_other` 的写入点在 backend-2 的范围内，此用例一并覆盖到了 |
| A-14 | §24.3.13 CLI | 规格只写了三个命令的名字与 `reset-root-password` 的副作用 | 实现：`reset-root-password`（同时解除锁定；**同样**要满足密码策略——"默认口令免策略"只适用于**引导**那个公开口令，不适用于运维现在敲进去的）；`create-admin`（无 `--password` 且无终端时生成 12 位临时密码并只打印一次；写审计 `user.create`，`detail.via='cli'`）；`unlock`（审计 `user.unlock`）。审计的 `actor` 是 `system`，`detail.osUser` 记录执行命令的系统账号。退出码 0/1/2（成功 / 被拒 / 环境未就绪）；库里没有 `users` 表或没有 root → 2，并提示先启动一次服务。命令行 `--password` 会留在 shell 历史里，所以缺省是交互输入（`getpass`，不回显，输两遍；非终端且没给 `--password` → 拒绝而不是挂起） |
| A-15 | §24.4.3 / R-A15 脚本 | `deploy/remote/41-seed-watchlist.sh`、`scripts/seed.mjs` 用 `POST /watchlist` 播种。M49 之后该路由要 `watchlist.own`，而 `service` 的权限里**去掉了** `watchlist.own`（§24.3.4）⇒ 这两个脚本在生产必然 403 | 改为 `POST /admin/featured-watchlist`（`service` 持有 `admin.featured.manage`；旧表本来就是"平台精选"）。`deploy/remote/*.sh` 与 `ops/status.sh` 统一加一个 `curl()` 包装函数自动带 `Authorization: Bearer`（取 `$API_TOKEN` 或服务的 `.env`），**绝不**把令牌发往 `http://`（脚本里 `http://` 只用来验证 301）。`capabilities`、`/cost`、`/schedules`、`/agent/probe` 的运维读取同样依赖它（对匿名是脱敏 / 401） |
| A-16 | §24.4.3 `deploy/systemd`、RV-14 | 单元只写 `TRUST_PROXY=1`、`COOKIE_SECURE=1` 还不够：`.env` 里残留一行 `AUTH_MODE=open` 会被单元之外的 `.env` 读到（环境变量优先，但单元里没写就没有优先的那一个） | 单元里另外显式写 `AUTH_MODE=enforced`；`ops/status.sh` 的 `auth:` 取自**运行中进程**的 `GET /auth/config`（不是 `.env`），`deploy.sh` 冒烟新增"`authMode == enforced`"与"匿名 `GET /admin/overview` == 401"两条硬断言（失败即 `die`） |
| A-17 | §24.4.3 `nginx` | 任务要求"核对无 `proxy_cache`" | 已逐项核对：`/api/` 里只有 `proxy_cache off;`（保持）；无 `proxy_ignore_headers/proxy_hide_header Set-Cookie`；两个 `/api` location 都 `proxy_set_header Host $host`（CSRF 的 netloc 比较依赖它）；`$proxy_add_x_forwarded_for` 把真实对端追加在最右（后端取最右）。**配置行为未改**，只在文件里加了一段注释把这四条核对结论留给后来人 |
| A-18 | §24.5.3 `POST /admin/users` | 规格只说"缺省 `password` 则生成临时密码并置 `must_change_password`"。**管理员自己填的密码**不强制改密，这意味着管理员知道该用户的密码 | 按规格实现（只有生成的才强制改密）。若要收紧——"凡管理员设置的密码都强制首次改密"——只需把 `must_change_password=temporary is not None` 改为 `True`，列在此处作为**待产品确认**的一项 |

**测试**：新增 `tests/test_admin_api.py`（114 条：概览对账 / 20k 性能 / 列表游标、通配符、索引计划、语句数 / 临时密码一次性 / 矩阵（AC-176/177）逐动作逐角色 / 服务主体收窄（AC-182）/ 重置·踢人·解锁·停用立即生效 / 删除匿名化（AC-197）/ 额度 / 邀请码 / 设置（AC-199 三模式端到端）/ 审计 / 精选 / 额度申请队列）；`tests/test_bootstrap_root.py` 追加 CLI 16 条（AC-205：旧会话全失效、强制改密、审计、策略、交互、真实 `python -m app.cli` 子进程）；`tests/test_audit.py` 追加 AC-198 端到端一条。

### 后端（M48 额度引擎 + M49 既有能力改造）

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| Q-1 | §24.3.8(b) 只有"每用户互斥锁" × AC-207 | 站点日总量检查（`site_used` 求和）跨用户：两个**不同**用户各持自己的锁，会同时读到"还差 1 个名额"而都通过，名额被超发。每用户锁挡不住这条 | `reservation()` 在 `resource='report'` 且 `site_daily_cap > 0` 时**再取一把全局 `_SITE_LOCK`**，锁序恒为 用户锁 → 站点锁（不会死锁），同样持有到调用方 commit/rollback 之后。站点上限为 0（默认关闭的部署）时不取，热路径零开销。锁均为 `RLock`（同线程重入安全） |
| Q-2 | §24.3.8(c) 预检 / (d) 退款调用点 | 预检只读不做对账：一个刚失败、退款还没被 sweep 订上的 run，会让额度恰好为 0 的用户被预检 429 挡回去，而他的额度其实已经"该还了"。读额度前对账（调用点③）只覆盖 `status()` | `precheck()` 与 `reservation()` 在**将要拒绝时**先做一次定向对账（用户级，必要时全站级）再重判；只在即将拒绝时才付这笔账，热路径不变。`reconcile_refunds()` 自带 commit（仅在确实写入时）：`reservation()` 的拒绝分支发生在调用方写任何行之前，所以不会夹带别人的半截事务 |
| Q-3 | §24.3.9(a)「`scheduler_ticker` 一行不改」× (e)「`created_by=owner_id`」 | 两句互相打架：`actor` 缺省=system 确实让 ticker 不改也能跑，但定时 run 的 `created_by` 要记创建者就必须有人把 `owner_id` 传进 `start()`。而 `tick_claim()` 返回的 dict 键集被 AC-43（`test_tick_claim_returns_plain_dicts_not_orm_rows`）逐键钉死，不能往里加 `owner_id` | `start()` 追加关键字参数 `created_by`（仅当 `actor` 不是 `user` 时生效；`user` 恒用自己的 id）；`scheduler_ticker._trigger` **一行**改：用 `item["id"]` 现查 `schedules.owner_id` 再传。`tick_claim()` 与既有断言一字未动。`scheduler.create()` 增 `owner_id` 参数，`POST /schedules` 传 `principal.user_id` |
| Q-4 | §24.3.9(g) `Capabilities` 脱敏「`engine`、`runtime` 整体置 `null`」 | `CapabilitiesResponse.engine / runtime` 声明为**非空**（`Field(default_factory=…)`），置 `null` 会在响应校验处炸。同时 `aragon` 子对象的保留名单（4 个键）漏了 `loopOk`——它与 `degradedReason == event_loop_unsupported` 同源，是"这台机器能不能生成"的事实，不是运营机密 | 两个字段改为 `X \| None`（默认值不变，所以既有构造与 SSR 兜底不受影响）；脱敏时 `aragon` 额外保留 `loopOk`（`loopReason` 机器前缀仍置空）；其余按规格。同时有**三处既有断言**因"只追加"而必然变化，§24.4.1 只点名了 `test_report_library_api`，其余未列：`test_capabilities_api::test_capabilities_shape_is_stable` 与 `test_contract_codes` 的顶层键集合（+`redacted`）、`test_dedupe::test_the_202_body_says_whether_dedupe_was_requested` 的 202 体键集合（+`quota`）。三处只加了新键，**未改任何其它断言** |
| Q-5 | §24.3.9(a)/§24.5.5 202 体 `quota:{remaining, resetAt}` vs §24.3.9 "`QuotaStatus.report` 子对象" | 两处写法不同 | 取超集：完整的 `QuotaBucket`（`limit/usedToday/bonus/remaining/resetAt/pendingRequest`），前端可直接喂给芯片；`service`/`system`（无额度）为 `null`。键始终存在 |
| Q-6 | §24.3.9(b) 与 AC-181 | §24.3.9(b) 列了 **7** 个脱敏字段（`costUsd/costUsdEst/costSource/agentProvider/agentModel/failureFingerprint/exitCode`），AC-181 写"6 个字段" | 以正文的 7 个为准（`exitCode` 属于引擎细节）；另把 `costKnown` 置 `false`（保留 `true` 会暗示"有一个你看不到的金额"）。`test_authz_routes::test_run_out_is_redacted_…` 钉住 |
| Q-7 | 附录 A `/watchlist` 写路由「仅个人表」× D-8 `AUTH_MODE=open` | `open` 模式的 service 主体没有 `user_id`、却持有全部权限（含 `watchlist.own`）。按字面"仅个人表"它没有表可写；而既有用例（单人本地模式）一直在改旧表 | 非 `user` 主体（实际只有 open 模式的 service）保持**改造前行为**：读写旧 `watchlist` 表，响应 `scope='featured'`。真实的 API_TOKEN service 本就没有 `watchlist.own`（→ 403）。`test_open_mode_keeps_editing_the_legacy_table` 钉住 |
| Q-8 | §24.3.9(d) 播种条件 | 规格同时写了"自选为空"与"旗标未置位"，`POST /watchlist` 又是个人表的另一个入口：用户第一次操作就是"加一个"，若不先播种，他的列表会是「1 个」而之后的 GET 因旗标已置不再播种，精选永远补不上 | `POST` 也走 `ensure_seeded()`（先播种再添加）；`ensure_seeded()` 以旗标插入成功为唯一裁决，插入成功后若个人表已非空则不播种（只置旗标）。`DELETE`/`PATCH reorder` 不播种（它们作用于已存在的行） |
| Q-9 | §24.3.9(c)「普通用户对自己的报告只可 `archived`，仅影响"全部"视图的归类」 | **与实现事实不符，且正是 RV-10 想堵的那类越权的小号版本**：`report_service.set_flags(archived=True)` 会同时 `is_latest=False` 并把状态置 `superseded`（"归档版不能当首页"，`report_service.py` 既有行为）。普通用户归档自己生成的**最新版**，就把它从该标的的全站首页拿掉了——这正是"改变所有访客看到的最新版本" | **按规格实现、未改既有行为**（改它会改变既有 `PATCH archived` 语义与 AC-137 系列用例），但在此如实记录为**未闭合的 P2 风险**：建议后续迭代二选一——①`archived` 对 `is_latest` 的行也要求 `report.manage_any`；②归档最新版时由服务端自动把 latest 指针交给上一版（`soft_delete()` 已有同样的"后继者"逻辑可复用）。`DELETE` 自己的最新版已经有后继者逻辑，不受影响 |
| Q-10 | §24.3.9(c) 恢复 | 规格只说"拥有者或 manage_any"，没说别人对**软删**报告 restore 返回什么：403 会泄露"这个 id 存在且已删除" | 对非拥有者：报告已软删 → `404 report_not_found`（与"回收站内容不外泄"一致）；报告未删 → `403 forbidden`；拥有者 → 原有的 200 / 409 `report_not_deleted` |
| Q-11 | §24.3.9(h) `daily_count` 增 `user_id` | `open` 模式（service，`user_id=None`）若也按"`user_id = NULL`"计数，会把历史追问与旧口径混成别的数 | 非 `user` 主体不过滤（保持"每报告每日"的旧全站口径）；`user` 主体按 `(报告, 用户)` |
| Q-12 | §24.3.8(d) 调用点 ② 与 `_finalize_failure` | 追问的两个终态写入点（`_finalize_failure` / `_finalize_cancelled`）与 run 同理，需要同样的"尽快退款"尾调用 | 两处尾部加 `_refund_soon()`（try/except，失败只记日志）。`recover_interrupted()` 之后的启动对账覆盖追问（`reconcile_refunds` 同时处理 `runs` 与 `report_asks` 两条退款流） |
| Q-13 | `run_manager.start` 的 `IntegrityError → RunConflict` 分支 | 既有行为：`mkdir` 之后、`commit` 撞 `ux_runs_active` 时工作区目录留在磁盘上（孤儿，无 run 行对应，`RUN_RETENTION_DAYS` 清不到）。额度闸把 `mkdir` 也包进了锁内，这条路径不再是"极少见" | 该分支 `rollback()` 后 `shutil.rmtree(workspace, ignore_errors=True)`；账本行随事务回滚，所以"扣了额度没开成 run"不会发生 |
| Q-14 | `tests/test_policy_table.py` | `PENDING_ROUTES` 变空（M48 删 `/me/*`、M50 删 `/admin/*`）后，`@pytest.mark.parametrize("key", PENDING_KEYS, ids=_label)` 的空参数化会把 NOTSET 哨兵交给 `_label` 而在**收集期**炸 | `_label` 对非元组入参返回 `"none"`；其余断言不动 |
| Q-15 | AC-186「`runs` 表恰 3 行」的可测性 | `ux_runs_active` 要求每标的至多一个活动 run，20 线程若共用一个标的，17 个"失败"其实是撞唯一索引而非额度 | 并发用例用 20 个不同标的；它验证的是额度而不是 409 |

**RV-9 的并发用例**（`tests/test_quota_engine.py::test_the_lock_is_held_until_the_callers_commit_not_until_the_insert`）：线程 A 在 `with reservation()` 内停住（账本行已 flush、未 commit），主线程用 `RLock.acquire(blocking=False)` 证明锁此刻**仍被持有**，再放行 A；线程 B 随后必须读到 A 已提交的扣减而被 429。全程用 `threading.Event` 同步，没有 `sleep`。若把锁改成"INSERT 之后释放"，`acquire(blocking=False)` 那条断言立刻转红。

**测试**：新增 `test_quota_engine.py`（32：默认 3 次 / 先 daily 后 bonus / 20 线程恰 3 / 锁覆盖 commit / 退款幂等与并发 / 跨零点 / 不限·禁用·豁免主体 / 站点日总量 / `start()` 接线）、`test_quota_requests.py`（20：唯一 pending 由索引兜底 / 通过同事务写赠送行 / 拒绝必须带理由 / 队列游标 / `/me/*`）、`test_authz_routes.py`（36：预检先于 409 / 202 体 / 报告管理与 RV-10 / 回收站按人 / RunOut 脱敏 / 取消与审计 / Capabilities 同形状脱敏 / 追问 404 与令牌即授权 / deep 额度 / 自选播种并发 / 定时刷新仅管理员与 owner 链路 / 端到端作者链路）。既有用例全绿；已迁移的既有断言见 Q-4（三处只加键）与 `test_report_library_api`（23→26 键 + 新键类型）。

---

---

# 25. Iteration 2 增量设计（v3）：把"能用"推到"可交付的顶级产品"（Y1–Y6）

> **定位**：§24（X1–X8）已在迭代 1 里**设计 → 评审 → 实现**完毕（M45–M52；后端 ≈2100 条、前端 597 条用例、`tsc/eslint/ruff/next build` 全绿，首屏 209.3 kB ≤ 245 kB）。
> 本节**只追加**，不改 §24 任何一行既有语义；它回答一个问题：**§24 落地之后，离"顶级产品"还差什么？**
> 方法：本节的每一条都先**在当前工作树里取证**（见 §25.0），查不到证据的"设想"一律不进来。
> 下游实现节点把 §25 当施工图，§24 当字典（错误码、权限、策略表、AC-153–208 在那边）。

| 项 | 值 |
| --- | --- |
| 本轮要做 | **Y1** 交付卫生与数据保留（提交、游离文件、会话表清理、响应头）　**Y2** 关闭迭代 1 遗留的已知风险（Q-9、A-18、F-26、F-23）　**Y3** 自助找回密码（恢复码，无邮件/短信通道下的闭环）　**Y4** 自助注销账号　**Y5** 管理效率（CSV 导出、批量赠送额度、安全事件概览）　**Y6** 体验与质量门禁（真实浏览器走查、e2e、无障碍清单） |
| 本轮明确不做 | 邮件/短信/第三方登录/TOTP（仍无外部通道；恢复码是其替代品，TOTP 扩展点沿用 §24.9）；按用户私有报告；计费支付；多 worker（仍受 E-8 约束）；改动任何既有路由的既有字段语义 |
| 交付物 | 迁移 `0008_account_recovery`；§25.4 的文件清单；`AC-209`~`AC-251`；里程碑 `M53`~`M58`；一次**包含迭代 1 + 迭代 2 全部工作的提交**（迭代 1 目前**一个字节都没提交**，见 E-17；★v4：**由本轮最后的“终审 + 提交”节点执行**，设计评审与实现节点都不提交——RV-Y15） |
| 文档版本 | **v4**（v3 = 设计节点稿；v4 = 评审节点修订稿：9 条 P1 + 8 条 P2 已就地改入，评审记录见文档顶部“评审记录（迭代 2）”；正文里的 `★v4` 标记表示该处由评审修订） |

---

## 25.0 已核实的事实与裁决（实现者先读这一节）

### A. 取证（2026-11-20，master `00db6a9` + 工作树）

| # | 事实 | 出处（可复核） | 对本轮设计的约束 |
| --- | --- | --- | --- |
| E-16 | ★**`sessions` 表只增不删**：全库只有 `audit_service.purge_expired()`（`main.py:168 _purge_audit_if_due`）与回收站 `purge_trash()` 挂在清扫循环里；`session_service` 里没有任何 `DELETE`。过期/吊销的会话行永远留在表里，`ix_sessions_user_live` 是 `WHERE revoked_at IS NULL` 的部分索引——**"过期但未吊销"的行永远算"活"**，用户登录设备多年后仍会膨胀；`list_live` 靠谓词过滤才没出错，但 `AUTH_MAX_SESSIONS_PER_USER` 的"踢最旧"逻辑与索引都在为死行付代价 | `models/user.py:78-106`；`services/session_service.py`（函数表：`create/resolve/revoke/revoke_by_token/revoke_all_for_user/get_owned/list_live`，无 purge）；`main.py:74-79,125-175` | 新增 `session_service.purge_dead()`，挂入**同一个**清扫循环（不依赖 `SCHEDULER_ENABLED`，同 V11-P1-4 的理由）；迁移 `0008` 加两个支撑索引 |
| E-17 | ★**迭代 1 的全部产出尚未提交**：`git status` 165 个变更路径（`M` 71、`??` 94，含 `api/app/security/`、`deploy/`、`web/app/{login,register,account,admin}/`、`contracts/permissions.json` 等），`HEAD` 仍是 `00db6a9`（报告库）。同时工作树里有 3 个**未被忽略的游离文件**：`api/pytest-out.txt`（一份 pytest 的 stdout 重定向）、`scripts/_patch_tmp.py`（自述"跑完即删"）、`docs/plans/report-library/probe_db.py`（探针脚本）。`web/npm-install.log`、`web/playwright-report/`、`web/test-results/` 已被 `.gitignore`（`*.log`、`playwright-report/`、`test-results/`）覆盖，无需处理 | `git status --short`（165 行）；`git status --short`（`?? api/pytest-out.txt`、`?? scripts/_patch_tmp.py`、`?? docs/plans/report-library/probe_db.py`）；`git check-ignore -v` 确认另三项已忽略 | Y1：评审节点提交前必须先清理这 3 个文件、给 `api/pytest-out*.txt` 与 `_patch_tmp.py` 补 `.gitignore`，且**提交前逐文件 Read 再决定去留**（`probe_db.py` 属于另一个 plan 目录，由评审者判断是否留档，默认移出仓库） |
| E-18 | **账号自助能力有三个空洞**：`routers/auth.py` 只有 `config/captcha/register/login/logout/me/password/profile/sessions/sessions/revoke-others/sessions/{id}`——**没有找回密码、没有注销账号**。无邮件/短信通道意味着"忘记密码"目前的唯一出路是**找管理员**（`POST /admin/users/{id}/reset-password`）或 root 走离线 CLI；对一个对外开放注册的产品，这是最高频的支持成本 | `routers/auth.py` 路由枚举（实测 11 条）；README"忘了见下一节" | Y3（恢复码）、Y4（自助注销） |
| E-19 | **管理后台不能导出**：`grep -ri csv api/app web/components/admin web/lib` 只命中与本功能无关的文件（提示词、`snapshot.py`、`config.py`）。审计日志与用户列表**只能在页面里翻**；做对账、合规留档、月度复盘都要手抄 | 上述 grep | Y5 |
| E-20 | **迭代 1 自己登记、但留着没做的 4 项**：①**Q-9** 普通用户 `archived` 自己生成的**最新版**，会让该版本退出该标的的**全站首页**（`report_service.set_flags` 既有行为），是 RV-10 同类越权的"小号版"，实现节点如实记为"未闭合的 P2 风险"；②**A-18** 管理员**手填**密码创建的用户不会被强制首登改密（管理员因此知道用户密码），列为"待产品确认"；③**F-26** "被锁定账号"概览卡无处可跳（`GET /admin/users` 没有 `locked` 过滤）；④**F-23** `api.ts` 里 20 余个 `admin*` 方法进了首屏 chunk（≈ +1.5 kB） | spec.md §"实施过程发现的方案缺陷"：Q-9 / A-18 / F-26 / F-23 | Y2：全部裁决并落地，不再"留待后续" |
| E-21 | **没有一次真实浏览器走查的证据**：迭代 1 的完成报告写的是"真实后端冒烟（curl 级）"；`web/tests/e2e/` 下只有 `ask/compare/grounding/library` 旧用例，**没有** auth 相关的 e2e；既有记录 F-REVIEW2 写明"playwright browser 1255 missing, download blocked"。而 §24 评审结论第 6 条明确要求"两种主机写法（`localhost` / `127.0.0.1`）各登录一次" | `web/tests/e2e/*`；blackboard F-REVIEW2；§24 评审结论 #6 | Y6：必须用**真实浏览器**（`ego-browser` / `playwright-cli` skill，任选其一可用者）走一遍并留截图，不是再写一份"无法运行的 e2e" |
| E-22 | **响应头**：Next 页面已有 `X-Frame-Options: DENY / Referrer-Policy / nosniff`（`web/next.config.ts:27-36`），但 **API 响应没有 `X-Content-Type-Options`**（`grep` `main.py`/`deploy` 无命中），nginx 配置也没有 HSTS。API 现在会返回带身份的 JSON 与 CSV（Y5）——`nosniff` 与 HSTS 是便宜且标准的加固 | `main.py` 中间件；`deploy/nginx/aragon-trading.conf` | Y1 |
| E-23 | **既有可复用件**（避免重复造轮子）：密码哈希/策略 `security/passwords.py`（含 KDF 并发信号量，RV-7）；限速桶 `security/ratelimit.py::RATE_LIMITS`（scope→`Limit(window,max)`，新增 scope 只加一行）；验证码 `services/captcha.py` + `auth_service.issue_challenge(purpose, …)`；审计 `audit_service.ACTIONS`（31 个动作，`test_audit` 逐个断言被真实流程触发）；注销核心 `user_service.anonymize` + `admin_user_service.delete_user`；设置 KV `settings_service.validate_field` | 各文件函数表 | 新功能全部复用，**不引入新依赖、不新增表**（只给 `users` 加 2 列、`sessions` 加 2 个索引） |

### B. 需求歧义与产品裁决（每条都可翻转；写明理由）

| # | 问题 | 裁决 | 理由 / 翻转方式 |
| --- | --- | --- | --- |
| D-9 | 没有邮件/短信，"忘记密码"怎么闭环？ | **恢复码（Recovery Key）**：注册成功时一次性展示，格式 `XXXXX-XXXXX-XXXXX-XXXXX`（Crockford Base32，20 字符 ≈ 100 bit 熵），库里只存 `sha256`（熵足够高，不需要慢哈希）；用它 + 验证码 + 新密码即可重置，**用完即作废并当场签发新码**。root **不适用**（继续走离线 CLI——root 是最高价值目标，不增加线上重置面；★v4：对 root 的自助恢复/注销一律返回**既有**码 `403 root_protected`）。管理员可在"系统设置"里整体关闭（`auth.recovery_enabled`，默认开） | 这是"无外部通道"下业界通行的做法（类 GitHub 恢复码 / 钱包助记词）；把"找管理员"降为兜底。**取舍**：恢复码泄露 = 账号被接管（与密码同级），所以只展示一次、要求用户勾选"我已保存"、轮换需要当前密码 |
| D-10 | A-18：管理员手填的密码要不要强制首登改密？ | **要**。无论管理员是否手填，`must_change_password=True` | 管理员知道用户密码本身就是一个不应存在的信任关系；强制改密只多一步，却让"管理员无法冒充用户"成为产品属性。翻转：把 `admin_user_service.create_user` 里那一行改回 `temporary is not None` |
| D-11 | Q-9：用户归档自己的最新版？ | **选 Q-9 建议的方案②**：归档最新版时由服务端把"最新"指针**自动交给上一个可用版本**（复用 `soft_delete()` 已有的后继者逻辑）；若它是该标的**唯一**可用版本 → `403 forbidden`，`detail.reason="only_version"`（需要 `report.manage_any` 才能让一个标的"没有首页"）。`pinned/setLatest` 的规则不变（RV-10） | 方案① 会让用户"归档自己的报告"变成不可用的死按钮；方案② 保住体验，同时不让任何用户改变**全站**首页 |
| D-12 | 自助注销是否立即生效？ | **立即**，需 `密码 + 键入用户名确认`；账号走既有墓碑（`username_lc="deleted:<id>"`，用户名释放），其会话全吊销、个人自选清空、待处理额度申请自动置 `rejected`（备注"账号已注销"）、账本与审计保留。root 不可注销（`403 root_protected`）；`service` 无此权限 | 与 A-4 的管理员注销同一核心，行为一致；冷静期会引入"已注销但仍能登录"的灰色状态，得不偿失 |
| D-13 | 导出的边界？ | CSV，UTF-8 **带 BOM**（Excel 直接打开不乱码）；与列表**同一套过滤条件**；单次上限 50 000 行（超出截断并带响应头 `X-Export-Truncated: 1`）；**公式注入防护**：以 `= + - @ \t \r` 开头的单元格前置 `'`；**永不导出**任何密码/令牌/恢复码/验证码相关列（由反射测试守卫）；每次导出写审计 `admin.export` | 管理员导出的是个人数据——所以导出本身要留痕、要限频（`admin.export`：6 次/分钟）、要防注入 |

---

## 25.1 Overview

迭代 1 把"用户与权限系统"从 0 做到了 1：登录/注册验证码、四种主体、默认拒绝的路由策略表、额度账本、管理后台、游客只读，并且实测过安全上的两条 P0。迭代 2 不再扩张"功能面积"，而是回答三个"上线前一定会被问到"的问题：

1. **用户忘了密码怎么办？**——今天的答案是"找管理员"。本轮给出一个**不依赖任何外部通道**的自助闭环（恢复码），并配套"自助注销"，让账号生命周期（注册 → 使用 → 找回 → 注销）在产品内**自洽**。
2. **运营一个真实站点缺什么？**——导出、批量操作、安全事件的可见性、数据保留（会话表不能无限增长）、基础响应头。这些不是新功能，是"可以放心长期运行"的条件。
3. **我们怎么知道它真的好用？**——迭代 1 没有真实浏览器走查的证据。本轮把"真实浏览器 × 两种主机写法 × 三种视口"的走查与截图，变成与单测同级的**验收门禁**，并把迭代 1 登记但没做的 4 个遗留项（Q-9/A-18/F-26/F-23）一次清零。

> **设计取舍一句话**：宁可少加功能，也要让每一项都"有证据、有 AC、可回滚"。本轮**只有一个新机制**（恢复码），其余全部是对既有机制的补全；**0 个新依赖、0 张新表**、2 个新列、2 个新索引、6 个新路由（见 §25.5）；★v4 另有 1 个新审计动作（`auth.recovery.failure`）与 1 个新用途的验证码（`recover`）。

---

## 25.2 需求分解（Y1–Y6）与验收口径

| 编号 | 需求 | 一句话验收 | 对应 AC | 里程碑 |
| --- | --- | --- | --- | --- |
| **Y1** | 交付卫生与数据保留 | 迭代 1+2 一次干净提交；游离文件清零；死会话被定期清理且不误删活会话；API 带 `nosniff`、生产带 HSTS | AC-209–AC-212、AC-242、AC-251 | M53 |
| **Y2** | 关闭遗留风险 | 用户归档自己最新版不再改变全站首页；管理员手填密码也强制改密；"被锁定"可筛选可跳转；管理 API 方法移出首屏 | AC-213–AC-216、AC-244 | M54 |
| **Y3** | 自助找回密码 | 注册时拿到恢复码；凭码+验证码可重置；用完即换新码；**所有失败对外逐字节一致**；root 不适用 | AC-217–AC-224、AC-239–241、AC-243、AC-248–250 | M55 |
| **Y4** | 自助注销 | 密码 + 键入用户名确认后立即注销；root 不可；副作用齐全 | AC-225–AC-228 | M56 |
| **Y5** | 管理效率 | 用户/审计 CSV 导出（防注入、限量、留痕）；批量赠送额度（全成全败）；概览新增 24 h 安全事件 | AC-229–AC-234、AC-245–247 | M57 |
| **Y6** | 体验与质量门禁 | 真实浏览器走查（两主机 × 三视口）有截图；auth e2e 在可运行的环境里实跑；全量门禁与首屏预算不退化 | AC-235–AC-238 | M58 |

---

## 25.3 技术设计

### 25.3.1 Y1 — 交付卫生与数据保留

**(a) 提交与清理（流程门禁，非代码）**

1. 清理：删除 `api/pytest-out.txt`、`scripts/_patch_tmp.py`；`docs/plans/report-library/probe_db.py` **先 Read 再决定**（默认移出）。（`web/npm-install.log`、`web/playwright-report/`、`web/test-results/` 已被忽略，不用动。）
2. `.gitignore` 追加：`api/pytest-out*.txt`、`**/_patch_tmp.py`。
3. 行尾：仓库 `.gitattributes` 为 `eol=lf`（R-6 已确立）；新增的 `.tsx/.ts/.py/.md` 一律 LF，提交前 `git diff --check` 无报错。
4. **一次提交**包含迭代 1 + 迭代 2 的全部变更；★v4（RV-Y15）：**提交动作只由本轮最后的“终审 + 提交”节点执行**——设计评审节点与实现节点**都不 `git commit`**（实现节点只负责让工作树处于“可提交”状态：游离文件已清、五道门实跑为绿）；提交信息沿用既有体例（`feat: 用户与权限系统（M45-M58 / X1-X8 / Y1-Y6）+ 评审修正 …`）。提交前五道门（`ruff` / `pytest` / `tsc` / `eslint` / `vitest` / `next build` + `check-bundle`）必须是**本次实跑**的绿，不得引用旧结果。

**(b) 会话表清理 `session_service.purge_dead()`**

```
cutoff = now - AUTH_SESSION_RETAIN_DAYS            # 新环境变量，默认 30
DELETE FROM sessions WHERE id IN (
  SELECT id FROM sessions WHERE revoked_at IS NOT NULL AND revoked_at < :cutoff LIMIT :batch)
DELETE FROM sessions WHERE id IN (
  SELECT id FROM sessions WHERE idle_expires_at < :cutoff LIMIT :batch)      # idle ≤ absolute 恒成立，故已覆盖绝对过期
```

* `batch = 2000`，每轮最多 5 批；循环里每批一次 commit，**不在持写锁时睡眠**；返回删除总数并记一条 INFO 日志（不含用户信息）。
* 挂在 `main._sweep_loop` 里与 `_purge_audit_if_due` 同级（`SESSION_PURGE_SECONDS = 6 * 3600`，用模块级时间戳节流，与审计清理同构）；失败只记日志、不影响清扫循环。★v4（RV-Y14）：`_sweep_loop` 是 `async` 协程，而其中既有的清扫调用都是**同步函数、直接跑在事件循环线程上**；`purge_dead` 最多一次删 10 000 行，**不得**也这样跑——入口写成 `_purge_sessions_if_due()`，内部用**自己的** `SessionLocal()`，并由循环以 `await asyncio.to_thread(_purge_sessions_if_due)` 调用（`purge_dead(db)` 本身仍是同步纯函数，便于单测直接调）。
* **安全性质**：只删"死了超过保留期"的行——活会话、刚过期/刚吊销的行（排查"我昨天怎么被踢下线"仍可用）都保留。`sessions` 无子表（审计不外键引用它），删除安全。
* 迁移 `0008` 追加两个索引：`ix_sessions_revoked`（`revoked_at`，部分索引 `WHERE revoked_at IS NOT NULL`）与 `ix_sessions_idle`（`idle_expires_at`），保证上面两条 `DELETE` 的子查询走索引，不全表扫描。

**(c) 响应头**

* API 中间件给**所有**响应加 `X-Content-Type-Options: nosniff`（既有"带身份响应 `Cache-Control: private, no-store`"的中间件旁边加一行，不动 CORS/鉴权顺序——E-2）。★v4（RV-Y7）：**500 要单独处理**——`errors.py` 的 `@app.exception_handler(Exception)` 由 Starlette 最外层 `ServerErrorMiddleware` 调用，**在用户中间件之外**，中间件加的头到不了它；所以 `_unhandled_handler` 返回的 `JSONResponse` 要**自己**带 `X-Content-Type-Options: nosniff`（`AppError`/`HTTPException`/校验失败三个处理器走的是中间件之内，已覆盖）。
* `deploy/nginx/aragon-trading.conf`（★v4：**仓库里只有一个 `server { listen 80; … }` 块，没有 443 块**——`certbot --nginx` 会把 443 监听并入这个块、另建一个只含 301 的 80 块）在**这个 server 块的 server 级**加 `add_header Strict-Transport-Security "max-age=15552000" always;`（180 天，**不带** `includeSubDomains`/`preload`，避免把根域其它子站锁进 HTTPS——写在注释里）。浏览器会忽略明文 HTTP 上的 HSTS（RFC 6797 §8.1），所以放在 server 级是安全的，certbot 之后它自然落在 443 块里。**坑**：nginx 的 `add_header` **不继承**——只要某个 `location` 自己写了 `add_header`，server 级的头就**整体失效**；本文件里 `/_next/static/` 与 `= /sw.js` 两个 location 各有 `add_header`（对静态资源无妨），**`/api/` 与 `/` 没有**，所以 API 与页面都能继承到 HSTS。不要在 `/api/` 里再加 `add_header`。

### 25.3.2 Y2 — 关闭遗留风险

**(a) Q-9：用户归档自己的最新版（D-11）**

在 `report_service.set_flags(archived=True)` 的调用路径（`routers/reports.py` 的 `PATCH`）上，对**非 `report.manage_any`** 主体：

```
if target.is_latest:
    successor = _pick_successor(session, target)      # ★v4：从 soft_delete 里原样抽出的同一个函数（见下）
    if successor is None: raise Forbidden(detail={"reason": "only_version"})   # 必须在任何字段被改之前
    target.is_latest = False; target.status = "superseded"; session.flush()    # 先降级并 flush（ux_reports_latest 只允许一个）
    successor.is_latest = True                # 再升级；status 为 superseded 时恢复 ready（同 soft_delete）
set archived
```

* ★v4（RV-Y10）：`_pick_successor(session, report)` 是**把 `soft_delete` 里的内联查询原样搬出来**——条件 = 同标的、`id != report.id`、`deleted_at IS NULL`、`archived = 0`、`status != 'provisional'`、`version` 降序取第一（**没有** `status='ready'` 这一条；v3 里的 `latest_ready_non_archived_version` 这个名字在代码里不存在）。`soft_delete` 与 `set_flags` **共用它**，AC-213/AC-244 断言两条路径对同一数据选出同一个后继者。`set_flags` 自己会 `commit`，所以 `only_version` 的判定与指针交接必须在**同一次 flush/commit 之前**完成，且整个 `PATCH` 原子失败（同 RV-10：`pinned/setLatest` 与 `archived` 同时出现时，任一不允许则全不生效）。
* 管理员/root（`manage_any`）保持既有行为（可让标的"无首页"，这是策展权）。
* 前端：`ReportLibraryRow` 的"归档"在 `only_version` 时提示"这是该标的唯一的版本，归档会让它没有首页；请联系管理员"（走 `authErrorText` 的 reason 分支，不新增错误码）。

**(b) A-18（D-10）**：`admin_user_service.create_user` 里 `must_change_password=True` 恒真；`CreateUserDialog` 的说明文案改为"该用户首次登录必须修改密码"。

**(c) F-26**：`GET /admin/users` 增查询参数 `locked=1`（仅返回 `status='active' AND locked_until > now` 的用户；走 `ix_users_list`/`status` 过滤后再比较，2 万用户下仍受 AC-195 的"≤6 条语句"约束）；概览"被锁定账号"卡变为链接 `/admin/users?locked=1`，用户页把该参数还原成顶部筛选 chip（可清除）。

**(d) F-23**：把 `api.ts` 里全部 `admin*` 方法与 admin 专用类型移到 `web/lib/api.admin.ts`（只被 `components/admin/*` 与 `app/admin/*` import）；`api.ts` 保留 `createApi` 的公共骨架并导出 `request()` 供它复用。**前置条件**：`tests/api-credentials.test.ts` 的 AC-206 扫描必须同时覆盖新文件；首屏 `check-bundle` 数字必须**不增反降**（目标 ≈ −1.5 kB），否则放弃拆分（P3 项，不得为它破坏任何门禁）。

### 25.3.3 Y3 — 自助找回密码（恢复码）

**状态模型**（`users` 加 2 列，可空、无默认、不回填——E-7 纪律）：

| 列 | 类型 | 含义 |
| --- | --- | --- |
| `recovery_hash` | `String(64)` NULL | `sha256(normalize(key))` 的十六进制；NULL = 未设置 |
| `recovery_set_at` | `Text` NULL | 最近一次签发/轮换的 ISO 时间 |

**请求模型**（★v4 RV-Y13）：`RecoverRequest.recoveryKey` 只设 `max_length=128`，**不做格式校验**——格式不对与码错同属 `401 recovery_invalid`，不能走 `422 validation_error`；`username` 沿用 `max_length=64`。

**恢复码**：`secrets.choice` 取 20 个字符（字母表 `0123456789ABCDEFGHJKMNPQRSTVWXYZ`，Crockford，去 I/L/O/U），展示成 4×5 分组 `K7QM4-XNP2R-…`。`normalize()`：去空白与 `-`、转大写、`O→0`、`I/L→1`，使用户抄写时的常见混淆不致命。比较用 `hmac.compare_digest`（字节）。**明文永不入库、不入日志、不入审计**（`audit_service` 的 `_SENSITIVE_KEY` 已含 `token|secret|answer`；再加 `recovery` 关键字一条；★v4 RV-Y12：**`logging.py` 的两处脱敏正则**〔`:44` 行内 `key=value` 与 `:55` 结构化日志键名〕同样要加 `recovery[_-]?key`——它们现在没有）。

**三条代码路径**

1. **签发（注册）**：`auth_service.register()` 成功后生成恢复码，只写 `recovery_hash/recovery_set_at`，并把**明文放进 201 响应体的 `recoveryKey`（仅此一次）**。`auth.recovery_enabled=false` 时不签发、响应里 `recoveryKey:null`。`Cache-Control: private, no-store` 已覆盖 `/auth` 前缀。★v4（RV-Y13）：`register` 的 `response_model` 改为新模型 `RegisterOut(MeOut)`（**只有它**带 `recoveryKey`）；`login/me` 仍是 `MeOut`——否则这两处会带一个恒为 `null` 的 `recoveryKey`，把“明文只出现一次”的契约搅浑。注册响应若在网络上丢失，用户可登录后到账号页“生成新的恢复码”补发（需当前密码）。
2. **轮换/首次生成（已登录）**：`POST /auth/recovery-key` `{currentPassword}`：校验当前密码（限速复用 `auth.password`，另加按用户的 `auth.recovery_rotate`：60 s / 3 次，★v4 RV-Y17）→ 生成新码 → 覆盖 `recovery_hash` → 响应 `{recoveryKey, setAt}`（一次）→ 审计 `auth.recovery.rotate`。老用户（迭代 1 期间注册、没有码）通过它补发。root → `403 root_protected`（`RootProtected(action="recovery_key")`，★v4 RV-Y2；`service` → `Forbidden(permission="account.self")`，游客 → `401`）。前端对该请求**单飞**（提交即 `disabled`、忽略重复点击）：它没有“旧码”可做 CAS，只能靠单飞 + 限速避免“屏幕上是先返回的那把、库里是后写的那把”。
3. **使用（未登录）**：`POST /auth/recover` `{username, recoveryKey, newPassword, captchaId, captchaAnswer}`。严格按以下**固定顺序**（顺序本身是安全设计）：
   1. `auth.recovery_enabled` 关 → `403 recovery_disabled`（对所有人一致）。
   2. 限速：`auth.recover`（IP，600 s / 5 次，`hit`）→ `auth.recover.global`（3600 s / 30 次，`hit`）→ `auth.recover.user`（`username_lc`，3600 s / 5 次）。超限 `429 rate_limited`。★v4（RV-Y11）：**`auth.recover.user` 在此步只 `peek`，不计数**；通过第 3 步验证码之后才 `record`（成功与失败都计；对**不存在**的用户名同样 `record` → 影子行为，同 RV-3）。理由：若在验证码之前就计数，任何人不解一张验证码就能把任意用户名的找回通道封一小时。这与 `auth_service.login` 的"`peek` 检查 + 失败后 `record`"同构。
   3. 验证码：`purpose="recover"`，**永远要求**（不像登录那样自适应）；错 → 既有 `422 captcha_invalid`（`detail` 恒 `{}`，B-6）。★v4（RV-Y1）：**这一步在 v3 里不可达**——`challenge.Purpose`、`GET /auth/captcha` 的 `purpose` 参数、`api.ts captcha()`、`useCaptcha()` 当前都只认 `register|login`。本轮必须把 `recover` 加进这四处；`captcha.verify` 的“过快 1.2 s”陷阱**只对 `register`**（`purpose == "register"`，已是现状），`recover` 不设；验证码在 `verify` 成功时即被消耗（`_drop`），所以第 4 步弱密码被拒后前端要换新验证码（与既有表单的 `refresh_captcha` 行为一致）。
   4. **新密码策略先于恢复码校验**：不合规 → `422 weak_password`。理由：策略只依赖"声称的用户名"，不含秘密，先判可避免"恢复码是否正确"成为 `weak_password` 与否的旁路；且**不消耗恢复码**。
   5. 恢复码校验：用户不存在 / 已停用 / 已注销 / 是 root / 没有设置恢复码 / 码不对——**一律** `401 recovery_invalid`，响应体逐字节相同（除 `requestId`）；对"不存在"路径照样做一次 `sha256` + `compare_digest`（对伪造哈希），抹平耗时差。★v4（RV-Y5）：每一次 5 的失败（以及 6 的 CAS 失败）都写审计 `auth.recovery.failure`——**按 `(声称的用户名, ip)` 在 60 s 内合并**（`detail.count` 递增，复用 `audit_service.record_login_failure` 的合并逻辑，把动作名参数化），**只记声称的用户名，不区分存在与否**（与登录失败一致，不构成枚举口）；它不含码、不含 `user_id`（`target.id=None`）。
   6. 成功：在**一个事务**里 `set_password(must_change=False, is_default=False)` → `revoke_all_for_user(reason="password_recover")` → 清 `failed_logins/locked_until` → **轮换恢复码** → 审计 `auth.password.recover`（actor = 该用户，`detail` 不含码）→ commit；响应 `200 {recoveryKey: <新码>, setAt}`，**不自动登录**（前端展示新码保存提示后跳登录页）。★v4（RV-Y3）：**轮换必须是比较-交换**——`UPDATE users SET recovery_hash = :new, recovery_set_at = :now WHERE id = :id AND recovery_hash = :old`（`:old` 是第 5 步刚校验过的那个哈希），`rowcount != 1`（说明另一个并发请求已用掉这把码）⇒ **整个事务回滚**（密码也不改、会话也不吊销）并按 `401 recovery_invalid` 返回（同时写一条 `auth.recovery.failure`）。否则用户双击/超时重试时两个请求都会通过校验、各生成一把新码，后写者覆盖先写者，**屏幕上保存的那把可能不是库里的那把**。前端提交即 `disabled`（单飞）作为第二道。
* KDF 沿用 `hash_password` 与其并发信号量（RV-7），所以恢复入口不会成为内存放大口。
* ★v4（RV-Y8）**`auth.recovery_enabled` 的落点**：`settings_service` 现有的 `validate_field/get_all/update` 只认 `registrationMode`（字符串枚举）与 `INT_FIELDS`（严格 int、**明确拒绝 bool**）。本轮新增 `BOOL_FIELDS = {"authRecoveryEnabled": "auth.recovery_enabled"}`：`validate_field` 要求 `isinstance(value, bool)`（`1`、`"true"` 都 `422 invalid_setting`，与既有的严格口径一致），存 `"1"/"0"`；`get_all` 返回布尔；`update` 的审计 `from/to` 用布尔；**缺省值 = `get_settings().auth_recovery_enabled`（运行期读 env，不能进静态 `DEFAULTS` 字典）**；`schemas/admin.py` 的设置读写模型追加 `authRecoveryEnabled: bool`。每次请求读库，无缓存（AC-222 / AC-199 同口径）。
* 停用账号恢复被拒是**有意的**（管理员的停用决定高于自助）；锁定账号恢复**成功并解锁**（A-5 同理：忘记密码的人此刻多半被锁）。

**前端**（复用 `AuthShell/Field/PasswordField/PasswordStrength/CaptchaField/useCaptcha`；新文案放 `copy.authPages.ts`，**不进首屏 chunk**）：

* 注册成功后**不直接进工作台**，先弹 `RecoveryKeyDialog`（`next/dynamic`）：大号等宽字体展示分组码、`复制` / `下载 .txt`（文件名 `aragon-recovery-<用户名>.txt`，内含用途说明与"勿分享"）两个动作；"我已保存"复选框勾选前，"继续"按钮 `disabled` 且 `aria-describedby` 指向原因；`Esc` 不能关闭（防误触丢码），但提供明确的"跳过（不推荐）"次级文字按钮，点击后账号页会常驻一个**可关闭**的提示条"尚未保存恢复码"。
* `/login` 表单下方加"忘记密码？"→ 新页 `/recover`（`app/recover/page.tsx`，`force-dynamic`，已登录用户访问则重定向到 `/account`）。表单：用户名、恢复码（自动大写/插入连字符的输入遮罩，粘贴整串也能分组）、新密码（实时强度清单，沿用 `lib/passwordPolicy.ts`）、验证码。失败统一展示"用户名或恢复码不正确"+"仍无法找回？联系管理员"。成功 → 展示**新**恢复码（同 `RecoveryKeyDialog`）→ 跳 `/login?recovered=1`（登录页顶部一条成功提示）。
* 账号页新增"恢复码"卡：状态（"已设置 · 2026-11-20" / "未设置"）、"生成新的恢复码"（弹**密码确认**对话框，成功后展示一次）。`recovery_enabled=false` 时整卡隐藏，`/login` 的"忘记密码？"链接也隐藏（由 `/auth/config` 追加字段 `recoveryEnabled` 驱动）。
* `Me.user` 追加 `hasRecoveryKey: boolean`（只追加，§24.5.5 精神）。

**为什么不做"重置链接/邮件"**：没有外部通道，做了也发不出；做成"管理员生成重置链接"又等于换皮的管理员重置。恢复码是唯一在**零外部依赖**下仍由用户本人持有的第二凭据。

### 25.3.4 Y4 — 自助注销

`DELETE /auth/account`，body `{password, confirm}`：`account.self`；`confirm` 必须等于当前用户名（大小写敏感，防误点）；`password` 错 → 复用 `InvalidCredentials`（与 `change_password` 一致，限速复用 `auth.password`）。核心抽成 `services/account_lifecycle.py::delete_account(db, actor: Principal, target: User, *, via: Literal["self","admin"])`，`admin_user_service.delete_user` 与新路由**共用其副作用部分**（A-4 的全部副作用：吊销会话、清个人自选、待处理额度申请置 `rejected`、墓碑化、账本与审计保留），消除两份实现的漂移风险（对应 I-106 的"收敛重复实现"教训）。

★v4（RV-Y2）**授权与副作用必须拆开**：`security/objects.assert_can_manage_user(actor, target, "delete")` 对“自己删自己”**一律** `SelfActionForbidden`（`422 self_action_forbidden`，`SELF_FORBIDDEN` 含 `delete`，AC-197 的既有行为），所以自助路径**不能**复用这道矩阵。做法：`delete_account` 只含副作用（吊销会话 / 清自选 / 拒绝待处理申请 / `anonymize` / 审计），**授权由调用方先做**——管理员路径照旧 `assert_can_manage_user`（admin 对自己仍是 422，不变）；自助路径用新增的 `objects.assert_can_self_delete(principal, user)`：`principal.kind == "service"` → `Forbidden(permission="account.self")`；`user.role == "root"` → `RootProtected(action="self_delete")`（即 `403 root_protected`，**复用既有错误码**，不另造 `forbidden{reason:"root_protected"}`——前端 `authErrors.ts` 与文案表里 `root_protected` 早已登记）。响应 `204` 并清除会话 Cookie。审计 `user.self_delete`（与管理员的 `user.delete` 区分；actor 取 `Principal` 快照里的用户名，不受随后 `anonymize` 改名影响）。

前端：账号页底部"危险区"卡（红色描边、非主色）→ `ConfirmDialog`（既有原语）要求**同时**输入密码与用户名；成功后回首页并 toast"账号已注销"。游客态/root 不渲染该卡。

### 25.3.5 Y5 — 管理效率

**(a) CSV 导出**

* `GET /api/v1/admin/users/export.csv`（`admin.users.read`）、`GET /api/v1/admin/audit/export.csv`（`admin.audit.read`）；查询参数与对应列表接口**完全相同**（复用同一个过滤函数，不复制 WHERE 逻辑）。
* `StreamingResponse`（`text/csv; charset=utf-8`，首字节输出 BOM），`Content-Disposition: attachment; filename="aragon-users-20261120.csv"`（日期取 `QUOTA_TZ`），按 `created_at`/`id` 游标分块读取而非一次性 `.all()`，内存恒定；累计达 50 000 行即停并带 `X-Export-Truncated: 1`（头需在流开始前决定，所以先 `COUNT` 一次，超过上限即预先置头；读取处另有**同值的硬上限**，两者独立成立）。★v4（RV-Y9）取数细则：
  * **块大小 500**（不是 1000）：每块要给 `quota_lite` 做一次 `IN (…)`，旧版 SQLite 的变量上限是 999；
  * 生成器**自己**开 `SessionLocal()`、**每块一个短会话**，不使用 `Depends(get_db)` 的请求作用域会话（它的关闭时序由框架决定）；
  * 用户导出的 `usedToday/bonus` 复用 `admin_user_service.quota_lite`（每块**一次** `GROUP BY`），**禁止逐行查询**；
  * **限速与 `admin.export` 审计在第一个字节之前提交**（`detail.rows` 记预估上限 `min(COUNT, 50000)`），因此导出中途失败也不会漏记。
* 列（用户）：`username, displayName, role, status, createdAt, lastLoginAt, dailyLimit, usedToday, bonus, hasRecoveryKey, invitedBy(打码)`；列（审计）：`id, at, actor, actorRole, action, targetType, targetName, ip, requestId, detail(JSON 字符串)`。**反射测试**断言表头集合与上面一致，且不含 `password|hash|token|secret|recovery_hash|captcha` 子串。
* 注入防护函数 `csv_safe(cell)`；`detail` 先经 `sanitize_detail`（已存在）。
* 限速 `admin.export`：`Limit(60, 6)`（按用户）；审计 `admin.export`（`detail={kind, rows, filters}`，`filters` 经 `sanitize_detail`）。浏览器侧用 `<a href download>`（同源生产无碍；开发期跨源见 R-A1，走查要覆盖）。

**(b) 批量赠送额度** `POST /api/v1/admin/users/bulk-quota`：body `{userIds: string[1..100], grant: int(-50..50, ≠0), note?: string<=120}`；`admin.quota.manage`。★v4（RV-Y6）逐点修订：
  * **范围 = `GRANT_MIN..GRANT_MAX` = −50..50**（`quota_service.py:63`，**复用常量，不另写数字**）；越界 `422 validation_error`。v3 的 ±100 会让 `quota_service.grant()` 抛 `ValueError` ⇒ 500。
  * **两遍制**（同 A-6）。第 1 遍：①`userIds` **去重**；②一条 `SELECT … WHERE id IN (…)` 取人，**任一未知或 `status='deleted'` ⇒ 整体 `404 not_found`，什么都不写**；③对每个目标过管理矩阵（`assert_can_manage_user(actor, target, "quota")`：admin 只能动 `role='user'`），任何一个不允许则整体 `403`，**什么都不写**。优先级固定为 `422`（形状）→ `404`（存在性）→ `403`（矩阵）。
  * 第 2 遍逐个调用既有 `set_quota` 的 `grant` 分支（同一事务，router 末尾一次 `commit`；任何异常整体回滚）。`quota_service.grant()` 对**负数**会**夹紧到该用户的 bonus 余额**，夹成 0 时不写账本、也不审计（`set_quota` 的既有语义，不改）——所以“每人恰一条账本行 + 一条 `quota.grant`”**只对实际生效的人成立**：`applied` = 实际写入账本的人数，响应追加 `skipped`（被夹成 0 的人数）。
  * `set_quota` 增关键字参数 `audit_extra: dict | None = None`，合并进 `quota.grant` 的 `detail`（`{bulk: true, batch: <batchId>}`）；`batchId` 是服务端生成的 12 位随机串，**只存在于审计 `detail`**（无新表）。
  * 响应 `{applied, skipped, batchId}`。UI：用户表增加复选列 + 吸顶工具条"已选 N 项 · 批量赠送额度"；选中上限 100，超出提示；工具条的数值输入范围 −50..50。

**(c) 概览 `security24h`**：`GET /admin/overview` 追加 `security24h: {loginFailures, loginFailureBursts, loginFailuresCapped, lockouts, recoveryFailures, recoveries, selfDeletes, exports}`，取审计动作 `auth.login.failure / auth.login.lockout / auth.recovery.failure / auth.password.recover / user.self_delete / admin.export` 过去 24 小时（滚动窗口，与 `QUOTA_TZ` 无关）的数据；复用既有 `ix_audit_action (action, at)`，不新增写路径；2 万用户下概览仍须 < 40 ms（AC-194 不退化）。★v4（RV-Y4）**失败类动作是合并写**：`auth.login.failure`（现状）与 `auth.recovery.failure`（RV-Y5）按 `(用户名, ip)` 在 60 s 内合并成**一行**、`detail.count` 递增，**行数 ≠ 尝试次数**。所以：`lockouts/recoveries/selfDeletes/exports` = 行数（`COUNT`，一行一事件）；`loginFailures` / `recoveryFailures` = **尝试次数 = `Σ detail.count`**——各自只 `SELECT detail_json … WHERE action=:a AND at>=:since LIMIT 5001`、在 Python 里求和（`detail_json` 是 `Text`，不用方言相关的 JSON 函数）；若取到 5001 行则只计前 5000 行并置 `loginFailuresCapped:true`（前端显示“5000+ 组”）；`loginFailureBursts` = 行数（“有多少组独立的尝试”）。前端 `OverviewCards` 新增一排只读的"安全事件（24 h）"，数字非零时用语义色（不使用红色以外的警示色），点击跳转 `/admin/audit?action=…`。

### 25.3.6 Y6 — 体验与质量门禁

**真实浏览器走查**（`ego-browser` 或 `playwright-cli` skill，择可用者；浏览器下载被封则用本机已装的 Chromium/Edge）。顺序固定：启动 `start-all.ps1`（`AUTH_MODE=enforced`）→ 对 **`http://localhost:3000` 与 `http://127.0.0.1:3000` 各走一遍**：

1. 游客：首页/报告库/`/{symbol}` 可读；生成入口三处（按钮、热键 `G`、命令面板）都触发登录弹窗，**不发出** `POST /reports`（查 Network 面板为 0 次）。
2. 注册（取验证码走 `AUTH_TEST_HOOKS=1` 的答案端点，仅走查期开启）→ 展示恢复码 → 勾选保存 → 进入工作台；额度芯片显示"3/3"；生成一次，芯片变"2/3"。
3. 额度耗尽对话框：文案与重置时间正确；"申请额外额度"提交成功。
4. 登出再用恢复码走 `/recover`：旧会话失效、新恢复码展示、旧码作废。
5. root 登录：强制改密 → 后台六页（概览/用户/邀请码/额度申请/审计/设置）逐页打开；CSV 导出点一次；批量赠送额度点一次。
6. 注销账号。

截图矩阵（存 `docs/plans/user-auth-permissions/walkthrough/`，文件名 `<步骤>-<主机>-<视口>.png`；**图片入库即交付证据**，不入 `.gitignore`）：登录、注册、恢复码弹窗、额度耗尽、账号页、后台概览、用户抽屉 × 视口 `1440×900 / 1024×768 / 390×844`。

**无障碍与交互清单**（评审节点逐项打勾，缺则补，不另开设计）：`autocomplete` 属性（`username` / `current-password` / `new-password`）让密码管理器可用；Enter 提交；错误文案在 `aria-live="polite"` 区；Caps Lock 提示；`prefers-reduced-motion` 下无位移动画；所有可交互元素的焦点环可见且对比度 ≥ 3:1；对话框焦点陷阱与 `Esc`（恢复码弹窗除外，见上）；360 px 宽度无横向滚动；颜色对比 ≥ 4.5:1（F-22 已修主按钮）；任何失败态都有**可执行的下一步**（重试 / 换图 / 联系管理员 / 申请额度），而不是只有一条红字。

**e2e**：新增 `web/tests/e2e/auth.spec.ts`（游客拦截 / 注册 / 额度 / 恢复 / 后台守卫），在 CI 或有浏览器的环境实跑；**不能运行的环境允许 skip，但走查证据不可 skip**（E-21 的教训：一份跑不起来的 e2e 不是证据）。

---

## 25.4 文件 / 模块变更计划

### 25.4.1 后端 `api/`

| 路径 | 动作 | 一句话意图 |
| --- | --- | --- |
| `app/db/migrations/versions/0008_account_recovery.py` | 新建 | `users` + `recovery_hash/recovery_set_at`；`sessions` + `ix_sessions_revoked`(部分) / `ix_sessions_idle`；`downgrade` 先删索引再删列 |
| `app/models/user.py` | 改 | 模型同步 2 列与 2 个索引 |
| `app/security/recovery.py` | 新建 | `generate_key()` / `normalize()` / `hash_key()` / `verify()`（纯函数，无 I/O，可单测） |
| `app/security/ratelimit.py` | 改 | `RATE_LIMITS` + `auth.recover`(600 s/5) / `auth.recover.global`(3600 s/30) / `auth.recover.user`(3600 s/5，★v4：步骤 2 只 `peek`、过验证码后才 `record`) / `auth.recovery_rotate`(60 s/3，按用户) / `admin.export`(60 s/6，按用户) |
| `app/services/challenge.py`、`app/services/captcha.py`、`app/routers/auth.py`（`GET /auth/captcha`） | 改 | ★v4（RV-Y1）：`Purpose` 加 `"recover"`；`captcha` 路由的 `purpose` Literal 同步；`verify` 的“过快”陷阱仍只对 `register` |
| `app/services/auth_service.py` | 改 | `register()` 签发恢复码；新增 `recover()`、`rotate_recovery_key()`；`public_config()` 追加 `recoveryEnabled`；`serialize_user()` 追加 `hasRecoveryKey` |
| `app/services/account_lifecycle.py` | 新建 | `delete_account()`：管理员注销与自助注销**副作用的唯一实现**（★v4：授权由调用方先做，见 §25.3.4） |
| `app/security/objects.py` | 改 | ★v4（RV-Y2）：新增 `assert_can_self_delete(principal, user)`（root → `RootProtected(action="self_delete")`；`service` → `Forbidden(permission="account.self")`）；`assert_can_manage_user` 不动 |
| `app/services/admin_user_service.py` | 改 | `delete_user` 先过矩阵、再委托 `account_lifecycle`；`create_user` 恒强制改密（D-10）；`list_users` 加 `locked` 过滤；`export_rows()`；`bulk_grant()`（两遍制，见 §25.3.5(b)）；`set_quota` 增 `audit_extra` 参数 |
| `app/services/export_service.py` | 新建 | `csv_safe()`、分块读取、表头白名单、上限与截断判定 |
| `app/services/session_service.py` | 改 | `purge_dead(db)`（同步纯函数）；★v4：`main.py` 里的 `_purge_sessions_if_due()` 自己开 `SessionLocal()`，由 `asyncio.to_thread` 调用 |
| `app/services/report_service.py` / `app/routers/reports.py` | 改 | Q-9：非 `manage_any` 归档最新版 → 后继者交接 / `only_version`；★v4：抽出 `_pick_successor()`（原样搬自 `soft_delete`），两处共用 |
| `app/services/audit_service.py` | 改 | `ACTIONS` + `auth.recovery.rotate / auth.recovery.failure / auth.password.recover / user.self_delete / admin.export`（**31→36**，★v4 比 v3 多一个 `auth.recovery.failure`）；`record_login_failure` 参数化动作名以复用 60 s 合并；`_SENSITIVE_KEY` + `recovery` |
| `app/logging.py` | 改 | ★v4（RV-Y12）：两处脱敏正则加 `recovery[_-]?key` |
| `app/services/settings_service.py` | 改 | ★v4（RV-Y8）：新增 `BOOL_FIELDS`（`authRecoveryEnabled` ↔ `auth.recovery_enabled`），`validate_field`（严格 `bool`）/`get_all`/`update` 同步；缺省取 `get_settings().auth_recovery_enabled` |
| `app/services/admin_overview.py` | 改 | `security24h`（失败类按 `Σ detail.count`，见 §25.3.5(c)） |
| `app/routers/auth.py` | 改 | `POST /auth/recover`、`POST /auth/recovery-key`、`DELETE /auth/account` |
| `app/routers/admin.py` | 改 | `GET /admin/users/export.csv`、`GET /admin/audit/export.csv`、`POST /admin/users/bulk-quota`、`GET /admin/users?locked=` |
| `app/schemas/auth.py` / `app/schemas/admin.py` | 改 | 请求/响应模型：`RecoverRequest`（`recoveryKey` 仅 `max_length=128`）、`RegisterOut(MeOut)`（唯一带 `recoveryKey`）、`RecoveryKeyOut`、`DeleteAccountRequest`、`BulkQuotaRequest/Out`、`security24h`、Settings 追加 `authRecoveryEnabled` |
| `app/security/policy.py` | 改 | 附录 A 追加 6 行（见 §25.5.4）；`test_policy_table` 的集合相等校验会强制这一步 |
| `app/errors.py` | 改 | `RecoveryInvalid(401, code="recovery_invalid")`、`RecoveryDisabled(403, code="recovery_disabled")`；★v4：`_unhandled_handler`（500）自带 `X-Content-Type-Options: nosniff` |
| `app/config.py` | 改 | `auth_session_retain_days=30`、`auth_recovery_enabled=True`（env 给出初始值，设置 KV 可覆盖） |
| `app/main.py` | 改 | 清扫循环以 `to_thread` 挂 `_purge_sessions_if_due`；中间件加 `X-Content-Type-Options` |
| `app/cli.py` | 改 | 新增 `clear-recovery <用户名>`（运维兜底：吊销某账号的恢复码；审计 `actor=system`） |
| `tests/test_recovery.py` | 新建 | Y3 全部 AC |
| `tests/test_account_delete.py` | 新建 | Y4 |
| `tests/test_admin_export.py` | 新建 | Y5（CSV/批量/概览） |
| `tests/test_session_purge.py` | 新建 | Y1(b) |
| `tests/test_migration_0008.py` | 新建 | 升降级 + 索引计划 |
| `tests/test_authz_routes.py`、`test_admin_api.py`、`test_audit.py`、`test_policy_table.py`、`test_contract_codes.py`、`test_migration_0004/0006/0007.py`、`test_cost_ledger_migration.py` | 改 | Q-9 / A-18 / `locked` / 动作词表 **31→36**（`test_audit.py:230` 的 `== 31` 与“一个场景把 31 个动作各打一遍”）/ 策略表 / head 断言 `0007`→`0008`（B-18 先例：只改 head 断言，不改其它） |
| `.gitignore`、`README.md`、`api/README.md`、`.env.example` | 改 | `.gitignore` 2 条（§25.3.1(a)）；新环境变量与恢复码/导出/注销说明 |
| `deploy/nginx/aragon-trading.conf`、`deploy/ops/status.sh` | 改 | HSTS（★v4：加在**唯一那个 server 块的 server 级**，注释写明 `add_header` 不继承、`certbot` 之后落入 443 块）；`status.sh` 输出 `recovery: on/off` |
| `api/pytest-out.txt`、`scripts/_patch_tmp.py` | **删除** | E-17 |

### 25.4.2 前端 `web/`

| 路径 | 动作 | 一句话意图 |
| --- | --- | --- |
| `app/recover/page.tsx` + `components/auth/RecoverForm.tsx` | 新建 | `/recover` 找回页 |
| `components/auth/RecoveryKeyDialog.tsx`、`RecoveryKeyInput.tsx` | 新建 | 恢复码展示（复制/下载/勾选）与输入遮罩（懒加载） |
| `components/auth/RegisterForm.tsx`、`LoginForm.tsx`、`AccountClient.tsx` | 改 | 注册后展示恢复码；登录页"忘记密码？"；账号页恢复码卡 + 危险区 |
| `components/admin/UsersTable.tsx`、`OverviewCards.tsx`、`AuditTable.tsx`、`CreateUserDialog.tsx`、`SettingsForm.tsx` | 改 | 复选与批量工具条；`locked` chip；安全事件卡；导出按钮；改密文案；`recovery_enabled` 开关 |
| `lib/api.ts` → 拆出 `lib/api.admin.ts` | 改/新建 | F-23；新方法 `recover / rotateRecoveryKey / deleteAccount / adminBulkQuota / exportUrl`；★v4（RV-Y1）：`captcha(purpose)` 的类型加 `'recover'` |
| `components/auth/useCaptcha.ts` | 改 | ★v4（RV-Y1）：`purpose` 类型加 `'recover'` |
| `lib/types.ts`、`lib/authErrors.ts`、`lib/authErrorText.ts`、`lib/copy.authPages.ts`、`lib/copy.admin.ts` | 改 | 新类型/错误码（`recovery_invalid`、`recovery_disabled`，守卫"逐码有文案"；★v4：`root_protected` 已登记，**不新增**）/ 文案；`only_version` 走 `forbidden.detail.reason` |
| `components/library/ReportLibraryRow.tsx` | 改 | Q-9 的 `only_version` 提示 |
| `tests/recover-flow.test.tsx`、`recovery-dialog.test.tsx`、`account-delete.test.tsx`、`admin-bulk.test.tsx`、`authErrors.test.ts`（改）、`api-credentials.test.ts`（改，覆盖 `api.admin.ts`） | 新建/改 | 前端 AC |
| `tests/e2e/auth.spec.ts` | 新建 | Y6 e2e |
| `web/lib/permissions.ts` / `contracts/permissions.json` | **不改** | 本轮没有新权限：恢复/注销属于既有 `account.self`，导出属于既有 `admin.users.read / admin.audit.read`，批量属于 `admin.quota.manage` |

### 25.4.3 仓库级

`.gitignore`（仅 2 条）；`README.md`（"账号与权限"章节追加"找回密码 / 注销 / 导出"）；`docs/plans/user-auth-permissions/walkthrough/*.png`（走查证据）；`scripts/` 与 `deploy/` 按 §25.4.1。

---

## 25.5 接口设计（只追加，不改既有键）

### 25.5.1 `/api/v1/auth`

| 方法 路径 | 主体 | 请求 | 成功 | 失败 |
| --- | --- | --- | --- | --- |
| `POST /auth/recover` | 公开（限速 `auth.recover`） | `{username, recoveryKey, newPassword, captchaId, captchaAnswer}` | `200 {recoveryKey, setAt}`（**新**码，一次） | `403 recovery_disabled`；`422 captcha_invalid`；`422 weak_password{rules}`；`401 recovery_invalid`（所有"码/用户"类失败逐字节一致）；`429 rate_limited` |
| `POST /auth/recovery-key` | `account.self` | `{currentPassword}` | `200 {recoveryKey, setAt}` | `401 invalid_credentials`；`403 recovery_disabled`；★v4：root → `403 root_protected{action:"recovery_key"}`；`429` |
| `DELETE /auth/account` | `account.self` | `{password, confirm}` | `204`（清 Cookie） | `401 invalid_credentials`；`422 validation_error{field:"confirm"}`；★v4：`403 root_protected{action:"self_delete"}`（**既有码**，非 `forbidden{reason}`）；`403 forbidden{permission:"account.self"}`（`service`） |

**既有接口追加字段**：`POST /auth/register` 的 201 体 + `recoveryKey: string|null`（★v4：模型是新的 `RegisterOut(MeOut)`，`login/me` 不带该键）；`GET /auth/captcha?purpose=` 的取值集合 `register|login` → `register|login|recover`；`GET /auth/config` + `recoveryEnabled: boolean`；`GET /auth/me` 的 `user` + `hasRecoveryKey: boolean`。

### 25.5.2 `/api/v1/admin`

| 方法 路径 | 主体 | 说明 |
| --- | --- | --- |
| `GET /admin/users?locked=1` | `admin.users.read` | 追加过滤；与 `role/status/q` 可组合 |
| `GET /admin/users/export.csv` | `admin.users.read` | 过滤参数同列表；限速 `admin.export`；审计 `admin.export` |
| `GET /admin/audit/export.csv` | `admin.audit.read` | 过滤参数同 `GET /admin/audit`（`action/actor/from/to`） |
| `POST /admin/users/bulk-quota` | `admin.quota.manage` | `{userIds[1..100], grant(-50..50, ≠0), note?}` → `200 {applied, skipped, batchId}`；★v4：未知/已注销目标 → 整体 `404`；任一目标越权 → 整体 `403`；优先级 `422 → 404 → 403`，失败时**什么都不写** |
| `GET /admin/overview` | 既有 | 追加 `security24h: {loginFailures, loginFailureBursts, loginFailuresCapped, lockouts, recoveryFailures, recoveries, selfDeletes, exports}` |
| `GET/PATCH /admin/settings` | 既有 | 追加键 `authRecoveryEnabled`（**严格 `bool`**；`1`/`"true"`/`null` → `invalid_setting`，A-8 同一套严格校验；实现落点见 §25.3.3 的 RV-Y8 条） |

### 25.5.3 错误码（与 `errors.py`、`web/lib/authErrors.ts` 一一对应）

| code | HTTP | 文案（面向用户，给出下一步） |
| --- | --- | --- |
| `recovery_invalid` | 401 | "用户名或恢复码不正确。请检查是否多输了空格；若恢复码已丢失，请联系管理员重置。" |
| `recovery_disabled` | 403 | "站点暂未开放自助找回密码。请联系管理员重置。" |

**不新增**其它码：`only_version` 走 `forbidden.detail.reason`；`confirm` 不符走 `validation_error`；导出越限走 `rate_limited`；★v4：对 root 的拒绝一律用**既有**码 `root_protected`（`detail.action` 区分 `self_delete / recovery_key`）；批量额度的未知目标用既有 `not_found`。

### 25.5.4 附录 A 追加行（`security/policy.py`；`test_policy_table` 强制）

```
POST   /api/v1/auth/recover             public   rate=auth.recover   note="唯一失败响应 recovery_invalid；验证码恒必填"
POST   /api/v1/auth/recovery-key        account.self                 rate=auth.password
DELETE /api/v1/auth/account             account.self  object="仅本人；root → 403 root_protected；授权用 assert_can_self_delete（不走管理矩阵）"
GET    /api/v1/admin/users/export.csv   admin.users.read             rate=admin.export
GET    /api/v1/admin/audit/export.csv   admin.audit.read             rate=admin.export
POST   /api/v1/admin/users/bulk-quota   admin.quota.manage  object="admin 仅 role=user；两遍制；先 404 后 403"
```

---

## 25.6 数据模型

**迁移 `0008_account_recovery`**（`down_revision = "0007_users_auth"`；纪律同 0006/0007：只 `add_column` / `create_index`，不 batch 重建）：

```
users    + recovery_hash    VARCHAR(64) NULL     -- 无默认、不回填
users    + recovery_set_at  TEXT        NULL
sessions + INDEX ix_sessions_revoked (revoked_at)        WHERE revoked_at IS NOT NULL   -- sqlite_where / postgresql_where 双写
sessions + INDEX ix_sessions_idle    (idle_expires_at)
downgrade: drop_index ×2 → drop_column ×2（先索引后列）
```

**设置 KV**（无迁移）：`auth.recovery_enabled`（`"1"`/`"0"`，缺省取 `AUTH_RECOVERY_ENABLED`）。**新环境变量**（`.env.example` 逐条加注释）：`AUTH_SESSION_RETAIN_DAYS=30`、`AUTH_RECOVERY_ENABLED=1`。

**不变量**：恢复码明文只存在于 ①生成它的那一个 HTTP 响应体，②用户自己的剪贴板/下载文件；服务端任何持久层与日志中**不存在**（AC-217 用"整库 + 日志 + 审计的字符串扫描"守卫）。

---

## 25.7 测试与验收

### 25.7.1 验收标准（AC-209 – AC-251；每条可自动化，Y6 的走查证据除外；★v4：AC-239–251 是评审追加，列在本节末尾的“评审追加”块，并各自标明它加固的是哪条既有 AC）

**Y1 交付卫生与数据保留（M53）**

* **AC-209** `purge_dead()`：造 100 行（活会话 / 刚吊销 / 刚过期 / 吊销 31 天 / 空闲过期 31 天 / 绝对过期 31 天），清理后只剩前三类；重复调用返回 0（幂等）；单轮上限 `batch×5`（造 12 000 死行，一次调用后剩余 > 0，再调若干次后清零），且**活会话行数与内容不变**。
* **AC-210** 迁移 `0008` 升/降/再升可往返；`EXPLAIN QUERY PLAN` 显示两条清理子查询分别使用 `ix_sessions_revoked` / `ix_sessions_idle`；`downgrade` 后 `users` 无 `recovery_*` 列；head 唯一为 `0008_account_recovery`。
* **AC-211** 提交后 `git status` 干净；`git ls-files` 不含 `pytest-out.txt`、`_patch_tmp.py`、`npm-install.log`、`playwright-report/`、`test-results/`；`git diff --check` 无输出；一次提交含迭代 1+2。★v4（RV-Y15）：**“提交后”指本轮最后的“终审 + 提交”节点执行 `git commit` 之后**；实现节点交付时的判据改为“工作树可提交”——三个游离文件已处理、`.gitignore` 两条已加、`git diff --check` 无输出、五道门实跑为绿。
* **AC-212** ★v4（RV-Y7 重写）任一 API 响应（含 401/404/422/**500**/CSV）带 `X-Content-Type-Options: nosniff`（500 用一个临时注册的、必然抛未处理异常的测试路由触发，断言来自 `_unhandled_handler` 本身）；`deploy/nginx/aragon-trading.conf` 的静态断言见 AC-242。

**Y2 遗留风险（M54）**

* **AC-213** 用户 A 归档自己的最新版：存在后继者 → 后继者成为 `is_latest`、被归档版 `archived=true`、该标的全站首页指向后继者；是唯一版本 → `403 forbidden` 且 `detail.reason=="only_version"`、数据库**无任何变化**；admin 归档同一版本 → 既有行为。★v4：同一标的上“用户归档最新版”与“用户删除最新版”选出的后继者**相同**（见 AC-244）。
* **AC-214** 管理员以**显式 `password`** 创建用户 → `must_change_password=true`；该用户首登只能访问 `/auth/me|password|logout`，其余 `403 password_change_required`。
* **AC-215** `GET /admin/users?locked=1` 仅返回处于锁定期的 `active` 用户；与 `q/role` 组合正确；2 万用户下语句数仍 ≤ 6；概览"被锁定账号"卡链接到 `/admin/users?locked=1` 且用户页显示可清除的筛选 chip。
* **AC-216** `lib/api.admin.ts` 拆分后：`check-bundle` 首屏 ≤ 拆分前（不增反降）；`api-credentials.test.ts` 的 AC-206 扫描覆盖新文件；首屏 manifest 中**无** `components/admin`、无 `api.admin`。

**Y3 恢复码（M55）**

* **AC-217** 注册成功响应含格式合法的 `recoveryKey`（正则 `^[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}$`），`users.recovery_hash` 为其 `sha256`；**明文不出现在**：整库字符串扫描、应用日志、`audit_log.detail_json`；同一用户第二次读取任何接口都不再含它。
* **AC-218** `POST /auth/recover` 成功：新密码生效且旧密码失效；该用户**全部**会话被吊销；`failed_logins/locked_until` 清零（锁定账号可恢复）；旧恢复码立即失效、响应含新码且 `hasRecoveryKey` 仍为真；审计 `auth.password.recover` 一行且 `detail` 无码。
* **AC-219** 逐字节一致：下列六种失败的响应体（除 `requestId`）与状态码完全相同——用户名不存在 / 用户存在但码错 / 用户存在但未设置码 / 已停用 / 已注销 / root；并断言**耗时差**在同一量级（不存在路径同样执行一次哈希比较）。
* **AC-220** 限速：同 IP 第 6 次 `recover`、同用户名第 6 次（★v4：指**通过验证码后**的第 6 次，见 AC-248）、全站第 31 次均 `429`；对**不存在**用户名同样按用户名计数（与存在的用户行为一致，RV-3 同构）；验证码错 → `422 captcha_invalid`，且**不**消耗恢复码。
* **AC-221** 顺序性质：弱新密码在码校验**之前**被拒（用"错误的码 + 弱密码"得到 `422 weak_password` 而非 `401`；用"正确的码 + 弱密码"得到 `422` 且**码仍然有效**）。
* **AC-222** `POST /auth/recovery-key`：错误当前密码 → `401`；正确 → 返回新码、旧码立即失效、审计 `auth.recovery.rotate`；游客 → `401`。设置 `auth.recovery_enabled=false` 后：`/auth/recover`、`/auth/recovery-key` 均 `403 recovery_disabled`，注册响应 `recoveryKey:null`，`/auth/config.recoveryEnabled=false`；改回后立即恢复（**每次请求读库，无缓存**，AC-199 同口径；设置键的严格 bool 口径见 AC-243）。★v4（RV-Y17）：`POST /auth/recovery-key` 第 4 次/60 s → `429`；前端对它与 `recover` 单飞（AC-250）。
* **AC-223** root：任何路径都无法用恢复码重置（`recover` 走统一 `401`；★v4：`recovery-key` 对 root 返回 `403 root_protected{action:"recovery_key"}`，复用既有码）；`python -m app.cli clear-recovery <用户名>` 能清除某账号恢复码并写 `actor=system` 审计。
* **AC-224** 前端（vitest）：注册成功先出现 `RecoveryKeyDialog`，"我已保存"未勾选时"继续"为 `disabled`，`Esc` 与点击遮罩**不**关闭；复制与下载动作可触发；`/recover` 表单对粘贴的整串/小写/带空格/`O`-`0` 混淆输入归一化后提交；所有 `recovery_*` 错误码都有文案（`authErrors.test.ts` 的"逐码有文案"守卫）；`recoveryEnabled=false` 时登录页无"忘记密码？"链接、账号页无恢复码卡。

**Y4 自助注销（M56）**

* **AC-225** 密码错 → `401`；`confirm` 与用户名不符 → `422 validation_error{field:"confirm"}`；二者皆对才 `204`；成功后旧 Cookie 立刻 `401`。
* **AC-226** 注销副作用（逐项断言）：全部会话吊销；个人自选清空；`pending` 额度申请 → `rejected`（备注"账号已注销"）；`username_lc` 变墓碑、原用户名可被他人重新注册；账本与审计行**保留**；审计为 `user.self_delete`（≠ `user.delete`）。
* **AC-227** ★v4（RV-Y2）root → `403 root_protected{action:"self_delete"}`（既有码）；`service` 主体 → `403 forbidden{permission:"account.self"}`；游客 → `401`；**普通 `user` 与 `admin` 自助注销 → `204`**（不被 `assert_can_manage_user` 的“对自己 `delete` ⇒ `422 self_action_forbidden`”拦住——该规则只属于管理员 API，AC-197 的既有断言不变：admin 经 `DELETE /admin/users/{自己}` 仍是 `422`）。
* **AC-228** 管理员注销（既有 AC-197）与自助注销**副作用**走**同一个** `account_lifecycle.delete_account`（反射/AST 测试：`admin_user_service.delete_user` 内无 `anonymize(` 直调；两条路径的**授权**不同，各自的函数名写进测试）；前端危险区需**同时**输入密码与用户名才启用"确认注销"。

**Y5 管理效率（M57）**

* **AC-229** 导出正确性：BOM 开头；表头与 §25.3.5(a) 完全一致；行数与对应列表接口在同一过滤下**逐条相等**；`Content-Disposition` 文件名含 `QUOTA_TZ` 当日日期；> 50 000 行时截断且 `X-Export-Truncated: 1`；内存恒定（造 20 000 行，峰值增量 < 20 MB）。
* **AC-230** 注入防护：用户名/备注/审计 `detail` 以 `=`、`+`、`-`、`@`、`\t`、`\r` 开头的值，导出后单元格均以 `'` 开头；含逗号/引号/换行的值按 RFC 4180 正确转义（用 `csv.reader` 往返断言）。
* **AC-231** 反射测试：导出表头集合不含 `password|hash|token|secret|recovery|captcha` 子串；矩阵测试：`user`/游客 → `403/401`，admin 与 root → 200，`service` 对 `users/export` 200（持有 `admin.users.read`）；`policy` 表登记且 `admin.export` 审计词表词条被真实流程触发（`test_audit` 的"词表逐条被触发"计数 **31→36**，★v4：含 `auth.recovery.failure`；`test_audit.py:230` 的 `len(ACTIONS) == 31` 同步改为 36）；同一用户第 7 次/分钟 → `429`；`service` 对 `recover`（公开路由，不看主体）与对 `recovery-key/account`（`403 forbidden{permission:"account.self"}`）的结果分别断言。
* **AC-232** `bulk-quota`（★v4 RV-Y6 改写）：100 个目标、`grant=50`（边界值）一次成功，每人恰一条账本行 + 一条 `quota.grant`（`detail.bulk=true` 且含同一个 `batch`）；101 个 → `422`；`grant=0 / 51 / -51` → `422`（**不得 500**）；其中任一目标是 admin（调用者为 admin）或 root → 整体 `403` 且**无任何账本行写入**；调用者为 `user` → `403`。去重、未知/已注销目标、收回被夹紧的口径见 AC-245。
* **AC-233** `GET /admin/overview` 含 `security24h`；人为制造 N 次登录失败（★v4：**N>1 且落在同一 `(用户名, ip)` 的 60 s 内，被合并成 1 行**）、1 次锁定、1 次恢复失败、1 次恢复、1 次导出后，`loginFailures == N`（尝试次数，不是行数）、`loginFailureBursts == 1`、其余逐项对上；超过 24 h 的行不计；2 万用户下概览 5 次取最优仍 < 40 ms（AC-194 不退化）。细则见 AC-246。
* **AC-234** 前端：用户表复选 + 工具条（选中数、批量赠送、清除选择）；选满 100 后禁用继续勾选并提示；导出按钮在无权限角色处不渲染；安全事件卡非零时可点击跳转 `/admin/audit?action=…`，且审计页能由该参数还原筛选。

**Y6 体验与质量门禁（M58）**

* **AC-235** 真实浏览器走查：§25.3.6 的 6 步在 `localhost` 与 `127.0.0.1` **各**完成一遍，全程控制台 **0 个 error**；第 1 步 Network 里游客的 `POST /reports` 为 **0**；截图矩阵 3 视口 × 7 屏齐全并入库，360 px 无横向滚动。
* **AC-236** `web/tests/e2e/auth.spec.ts` 存在且在有浏览器的环境实跑为绿（无浏览器环境下 skip 并在完成报告写明原因，**不得**以此代替 AC-235）。
* **AC-237** 全量门禁本次实跑：`ruff` 干净；`pytest` 0 失败（既有 ≈2100 条保持绿，只允许 §25.4.1 列出的断言迁移）；`tsc` + `eslint` 干净；`vitest` 全绿；`next build` 通过；`web/scripts/check-bundle.mjs` 的首屏 ≤ 245 kB 且**不高于**迭代 1 的 209.3 kB + 1.0 kB；`alembic heads` 唯一为 `0008`；`create_all` 为空（`test_boundaries` 不破）。
* **AC-238** 文档与示例同步：`README.md`（找回/注销/导出/保留期）、`api/README.md`、`.env.example`（2 个新变量）、`deploy/ops/status.sh`（`recovery:`）、本 spec 的"实施过程发现的方案缺陷"追加 §25 小节；`NEXT_PUBLIC_API_TOKEN` 的"已废弃"提示保留。

**评审追加（★v4，AC-239 – AC-251）**——每条都对应上方“评审记录（迭代 2）”里的一个 RV-Y 编号

* **AC-239**（RV-Y1，加固 AC-224）`GET /auth/captcha?purpose=recover` → `200`；`purpose=bogus` → `422`；用 `register`/`login` 签发的验证码提交给 `recover` → `422 captcha_invalid`，反之亦然（用途绑定）；`recover` 的验证码在签发后 < 1.2 s 提交**仍可通过**（“过快”陷阱只属于 `register`）；vitest：`api.captcha('recover')`、`useCaptcha('recover', true)` 类型与请求参数正确；`/recover` 页能渲染验证码图。
* **AC-240**（RV-Y3，加固 AC-218）并发/交错：两个携带**同一把有效码**的 `recover` 请求（用线程 + 在第 5 步与第 6 步之间插入屏障的测试钩子复现）→ **恰好一个 `200`、另一个 `401 recovery_invalid`**；库里的 `recovery_hash` == 赢家响应里新码的 `sha256`；账号密码 == 赢家的新密码；输家**没有**改密码、没有吊销会话（整个事务回滚）；审计恰好 1 条 `auth.password.recover` + 1 条 `auth.recovery.failure`。
* **AC-241**（RV-Y5，加固 AC-219）`auth.recovery.failure` ∈ `ACTIONS`（共 36 个）；六类“码/用户”失败与 CAS 失败都会写它；同 `(声称的用户名, ip)` 60 s 内合并为**一行**、`detail.count` 递增；`target_id` 恒为 `NULL`、`detail` 里无码、无用户是否存在的信息——对“存在的用户”与“不存在的用户名”产生的审计行，除 `target_name` 外**形状逐字段相同**；**验证码错与限速命中不写**该动作（与登录一致，避免被刷爆审计）。
* **AC-242**（RV-Y7，加固 AC-212）静态断言 `deploy/nginx/aragon-trading.conf`：server 级恰有一条 `add_header Strict-Transport-Security "max-age=15552000" always;`；值里**没有** `includeSubDomains`/`preload`；`location /api/` 与 `location /` 的块内**没有** `add_header`；文件里有一段注释说明“`add_header` 不继承”与“certbot 后落入 443 块”；`/_next/static/`、`= /sw.js` 两个 location 的既有 `add_header` **未被改动**。
* **AC-243**（RV-Y8，加固 AC-222）`PATCH /admin/settings {authRecoveryEnabled:false/true}` 生效且 `GET` 返回**布尔**；`1`、`"true"`、`null`、`0` → `422 invalid_setting` 且**整个 PATCH 不生效**（同 A-8）；库里无该行时缺省取 `AUTH_RECOVERY_ENABLED`（monkeypatch 为 `0` 与 `1` 各测一次）；`settings.update` 审计的 `from/to` 为布尔；既有 `registrationMode` 与 INT 字段的用例**不变**。
* **AC-244**（RV-Y10，加固 AC-213）夹具：同一标的 4 个版本（`ready` v4 最新、`ready` v3、`archived` v2、`deleted` v1，外加一个 `provisional` v5）：用户归档 v4 与用户删除 v4（各用一份夹具）选出**同一个**后继者 v3；整个过程**无** `ux_reports_latest` 的 `IntegrityError`（降级先 `flush`）；`PATCH {archived:true, pinned:true}`（非 `manage_any`）→ `403` 且 `archived/pinned/is_latest` **三者都未变**（原子失败）。
* **AC-245**（RV-Y6，加固 AC-232）`userIds` 含重复项 → 去重后只处理一次（`applied` 按去重后计）；含**未知** id → 整体 `404` 且无账本行；含**已注销**用户 → 整体 `404`；优先级：同时含未知 id 与越权目标 → `404`（先于 `403`）；`grant=-50` 而部分用户 bonus 不足 → 被夹紧（含夹成 0）的人 `skipped` 计数、**不写账本也不审计**，其余人正常，`applied + skipped == 去重后的人数`；`quota.grant` 审计的 `detail` 含 `bulk:true` 与同一个 `batch`；任何一步异常 → 整批回滚，账本行数不变。
* **AC-246**（RV-Y4，加固 AC-233）`loginFailures` 是 `Σ detail.count`：造三组（`count` = 50、1、7，不同 `(用户名, ip)`）→ `loginFailures == 58`、`loginFailureBursts == 3`；造 5 001 行 → `loginFailuresCapped == true` 且查询**至多取 5001 行**（用语句计数/`LIMIT` 断言）；24 h 之外的行不计；`recoveryFailures` 同口径；`detail_json` 损坏/缺 `count` 的行按 `1` 计且不抛。
* **AC-247**（RV-Y9，加固 AC-229）导出机制：块大小 ≤ 500（对 `quota_lite` 打桩计数：1 200 行 ⇒ 调用 3 次，且语句总数不随行数线性增长）；用流式客户端读到**第一个字节时**，库里**已有**对应的 `admin.export` 审计行与限速记录；生成器不使用请求作用域会话（让 `get_db` 在 `yield` 后立即关闭的夹具下导出仍完整）；`COUNT > 50000`（可用 monkeypatch 把上限降到 5）时预置 `X-Export-Truncated: 1` **且**实际输出恰好是上限行数；未超限时无该头且输出行数 == `COUNT`。
* **AC-248**（RV-Y11，加固 AC-220）同一用户名连续 10 次**验证码错误**的 `recover` → 全部 `422 captcha_invalid`，且之后一次**验证码正确**的请求**仍被处理**（`401 recovery_invalid` 或成功，而非 `429`）——证明用户名桶没有在验证码之前计数；5 次验证码正确但码错之后，第 6 次（验证码正确）→ `429`。
* **AC-249**（RV-Y12，加固 AC-217）`logging.redact("recoveryKey=K7QM4-XNP2R-ABCDE-12345")` 与结构化日志里键名 `recoveryKey` 的值都被打码；`audit_service.sanitize_detail({"recoveryKey": "…"})` 丢弃该键；端到端：在 `caplog` 开着的情况下跑完 注册 → 轮换 → 找回，全文 `grep` 三把明文码均**零命中**。
* **AC-250**（RV-Y17，加固 AC-222/224）vitest：`RecoveryKeyDialog` 触发的轮换请求与 `/recover` 表单的提交，在请求 pending 时**第二次点击不再发请求**（`fetch` 只被调用 1 次）且按钮 `aria-busy`/`disabled`；pending 结束后可再次提交。
* **AC-251**（RV-Y14，加固 AC-209）`_sweep_loop` 调用 `purge_dead` 时**不在事件循环线程上**：在测试里把 `purge_dead` 换成“记录 `threading.get_ident()` 并 `time.sleep(0.2)`”的桩，同时并行跑一个 `await asyncio.sleep(0.01)` 的心跳协程，断言 ①桩的线程 id ≠ 事件循环线程 id、②心跳在桩运行期间**至少前进 5 次**；`_purge_sessions_if_due()` 自己开关 `SessionLocal()`，异常只记日志不外抛。

### 25.7.2 里程碑与依赖

| 里程碑 | 内容 | 依赖 | 出口准则 |
| --- | --- | --- | --- |
| **M53** | Y1：清理游离文件 + `.gitignore`；迁移 0008（列 + 索引）；`purge_dead`（`to_thread`）；响应头（含 500 与 nginx HSTS） | 无 | AC-209–212、**AC-242、AC-251**；`test_migration_0008` 绿 |
| **M54** | Y2：Q-9（含 `_pick_successor` 抽取）、A-18、`locked`、`api.admin.ts` 拆分 | M53 | AC-213–216、**AC-244**；既有用例仅按 §25.4.1 迁移断言 |
| **M55** | Y3：`security/recovery.py`、`recover/rotate`（CAS）、`recover` 验证码用途、`auth.recovery.failure` 审计、注册签发（`RegisterOut`）、`/recover` 页与弹窗、账号页卡、设置开关（`BOOL_FIELDS`）、CLI、日志脱敏 | M53 | AC-217–224、**AC-239–241、AC-243、AC-248–250** |
| **M56** | Y4：`account_lifecycle` 收敛（授权/副作用拆分）+ 自助注销 + 危险区 | M55（共用账号页） | AC-225–228 |
| **M57** | Y5：导出、批量赠送、`security24h`、前端工具条 | M54 | AC-229–234、**AC-245–247** |
| **M58** | Y6：真实浏览器走查 + 截图、e2e、文档；★v4：**“工作树可提交”交付，提交本身由终审节点执行** | M53–M57 | AC-235–238；§25.8 的评审门禁 |

---

## 25.8 评审节点必须核对的点（迭代 2 的"通过条件"）

1. **顺序性质是安全设计，不是实现细节**：`recover` 的"限速 → 验证码 → 新密码策略 → 码校验"顺序，以及六类失败的逐字节一致（AC-219/221）必须有对应用例，**不得**因"实现起来麻烦"而合并步骤。
2. **恢复码明文不落地**：AC-217 的整库/日志/审计扫描必须是**真的扫描**（对 SQLite 文件字节与 `caplog` 全文 `grep` 明文），不是只断言字段为 `None`。
3. **`service` 主体的边界不被新路由撑破**：`service` 对 `recovery-key/account` 恒 `403 forbidden{permission:"account.self"}`（`_require_user`，`account.self` 在其 `minus` 里）；`recover` 是**公开**路由，不看主体（★v4 RV-Y16：v3 把它也列为 `403/401` 是误写）；导出对 `service` 开放只读（它本来就持有 `admin.users.read`）——用矩阵测试逐路由写死。
4. **`account_lifecycle` 收敛**：删除或自助删除后，两条路径的 `users/sessions/user_watchlist/quota_requests` 终态**逐表相等**（构造同一用户两次，分别走两条路径，比较快照）。
5. **一次提交的完整性**：提交前 `git status` 无 `??` 的游离产物（`pytest-out.txt` / `_patch_tmp.py` / `probe_db.py` 已处理）；已忽略项（`*.tsbuildinfo`、`*.log` 等）未被带入；提交信息按既有体例。
6. **走查证据真实**：截图必须是本次走查产生（文件修改时间晚于本轮的最后一次前端改动），且 `docs/plans/user-auth-permissions/walkthrough/` 下有一份 `README.md` 记录环境（浏览器、版本、两种主机写法各自的结果）。
7. **不得上调预算**：首屏阈值 245 kB 不得上调；如 Y3 的文案/组件把首屏推高，必须改为懒加载而不是改阈值（F-1 的先例）。
8. ★v4（RV-Y1）**`recover` 验证码用途四处同改**：`challenge.Purpose`、`GET /auth/captcha` 的 Literal、`api.ts captcha()`、`useCaptcha()`——漏任何一处，`/recover` 页都拿不到验证码（AC-239 会红）。
9. ★v4（RV-Y2）**自助注销不得走管理矩阵**：评审 `account_lifecycle` 时确认授权在调用方、副作用在 `delete_account`；root 的拒绝是**既有码** `root_protected`（不是 `forbidden{reason}`）。
10. ★v4（RV-Y3/Y5）**`recover` 的 CAS 与审计**：确认第 6 步是带旧哈希条件的 `UPDATE … WHERE recovery_hash = :old`、`rowcount != 1` 整体回滚；确认每一类失败都会写（合并的）`auth.recovery.failure`。
11. ★v4（RV-Y4）**`security24h` 的失败类求和 `detail.count`**，不是数行；确认 `loginFailuresCapped` 的 5001 行上限。
12. ★v4（RV-Y6）**批量额度的 ±50 与 404→403 优先级**；确认不存在 500 路径（`quota_service.grant` 的 `ValueError` 不可达）。
13. ★v4（RV-Y7）**HSTS 的位置**（server 级、`/api/` 与 `/` 内无 `add_header`）与 **500 的 `nosniff`**（来自 `_unhandled_handler`）。

---

## 25.9 风险与缓解

| ID | 风险 | 缓解 |
| --- | --- | --- |
| R-Y1 | 恢复码泄露 = 账号接管（与密码同级），且用户常把它和密码存在同一个地方 | 只展示一次 + 勾选确认；轮换需当前密码；`recover` 成功即作废并**吊销全部会话**；全站可关（`auth.recovery_enabled`）；root 排除；审计 + 24 h 安全事件概览让异常可见 |
| R-Y2 | `recover` 成为用户枚举口 | 六类失败逐字节一致 + 抹平耗时 + 对不存在用户名同样计数（AC-219/220）；验证码恒必填；★v4：失败审计只记“声称的用户名”、形状与存在与否无关（AC-241）；用户名桶在验证码之后才计数，防止被当作封号工具（AC-248） |
| R-Y3 | 恢复码输入遮罩的粘贴/输入法行为在不同浏览器不一致 | 遮罩只做**归一化与分组展示**，真正的规范化在服务端 `normalize()`（容忍空格/连字符/大小写/`O0/IL1`）；vitest 覆盖粘贴整串；走查时用真实浏览器粘贴一次 |
| R-Y4 | 导出放大数据泄露面（管理员账号被盗 → 一键拖库） | 限频 6/分钟、50 000 行上限、表头白名单 + 反射守卫、每次导出审计 + 概览展示；`service` 之外的导出权限仍只给 admin/root；CSV **不含**任何凭据列 |
| R-Y5 | CSV 公式注入（用户名/备注被构造成 `=HYPERLINK(...)`） | `csv_safe` 前置 `'`；`detail` 经 `sanitize_detail`；AC-230 逐前缀覆盖 |
| R-Y6 | 批量额度操作的越权或半成功状态 | 两遍制全成全败；矩阵逐目标过；每人独立账本行与审计（可逐条解释"为什么多了 2 次"） |
| R-Y7 | `purge_dead` 在大表上长时间持写锁、或阻塞事件循环，拖慢全站 | 分批（2000 行/批、最多 5 批/轮）、每批独立事务、索引支撑；每 6 小时一轮；★v4：**用 `asyncio.to_thread` 跑在线程池**（`_sweep_loop` 是协程，直接同步调用会阻塞事件循环，AC-251）；与请求线程竞争的只是 SQLite WAL 的短写 |
| R-Y8 | HSTS 配错会把站点锁死在 HTTPS（含无证书的子域） | 只设 `max-age=15552000`，**不**加 `includeSubDomains`/`preload`；写在 nginx 注释与 README；上线前用 `curl -I` 核对 |
| R-Y9 | Q-9 的"后继者交接"与既有 `soft_delete` 后继者逻辑不一致（两套选择规则） | **抽成同一个函数**并让两处共用；`test_authz_routes` 增"归档与删除选出同一个后继者"的断言 |
| R-Y10 | 迭代 1 + 2 合并成一次巨大提交，评审者无法定位问题 | 提交信息按 X/Y 分条列出；评审节点的修订写进 §25"实施过程发现的方案缺陷"并带 ID（沿用 I-/R- 体例）；**提交前**先出评审记录 |
| R-Y11 | 走查依赖本机浏览器，下载被封导致走不起来 | 允许 `ego-browser` 或本机已装 Chromium/Edge；走不起来则本节点完成报告必须写明阻塞原因并保留可复跑的脚本，**不得**伪造截图或把 skip 的 e2e 当证据 |
| R-Y13 | ★v4 恢复入口的验证码被 OCR（R-A8 已承认验证码可被机器解）后，攻击面是 5 次/10 min/IP + 30 次/小时/全站 + 100 bit 码熵 | 码熵使在线猜测不可行；限速 + 合并审计 + `security24h.recoveryFailures` 让**异常强度可见**；如需更严可由管理员整体关闭 `auth.recovery_enabled` |
| R-Y14 | ★v4 `recovery-key` 轮换与 `recover` 的“屏幕上的码 ≠ 库里的码” | `recover` 用 CAS（AC-240）；`recovery-key` 用单飞 + 3 次/分钟限速（AC-250）；码只展示一次且要求勾选“已保存”，注册后账号页常驻提示条 |
| R-Y12 | 范围偏大（6 个 Y） | 优先级：**Y1 ≥ Y3 > Y2 > Y5 > Y4 > Y6 的锦上添花项**——若时间不足，Y5(b) 批量赠送与 Y2(d) 拆包可顺延（均已标注为可独立裁撤），Y1/Y3/Y6 的走查不可裁撤 |

---

## 25.10 自检清单（设计节点交付前的自查）

* [x] 每条 Y 都有 §25.0 的证据编号（E-16–E-23）与产品裁决（D-9–D-13）。
* [x] 0 个新依赖、0 张新表；新增列均 `NULL`、无默认、不回填；迁移可回滚。
* [x] 没有新权限词条（`contracts/permissions.json` 不变）；新路由已在 §25.5.4 列出以满足"默认拒绝"的集合相等校验。
* [x] 没有改动任何既有路由的既有字段语义；新增字段全部"只追加"。
* [x] 每条 AC 可自动化（Y6 的真实浏览器走查是唯一的人工证据，并规定了产物与记录方式）。
* [x] 已为"评审节点"留出 §25.8 的核对点，并把它与 R-Y* 对应。
* [x] ★v4（评审节点复核）：§25.3 每一条对既有代码的断言都已在工作树里核实（见顶部“评审记录（迭代 2）”的“已核实”清单）；发现的 9 条 P1 / 8 条 P2 全部改入正文并各有 AC（AC-239–251）；没有新增依赖、没有新表、没有新权限词条（`contracts/permissions.json` 仍不改）；§24 一字未动。

---

## 评审结论（迭代 2：§25 / v4）

**结论：有条件通过。** §25 的方向、范围与取舍正确（“少加功能、每项有证据有 AC 有回滚”；0 新依赖、0 新表；恢复码的熵/哈希/轮换/顺序性质成立；遗留的 Q-9/A-18/F-26/F-23 全部有裁决），但 v3 初稿有 **9 条 P1** 会让 AC 不可达或让实现者必然踩坑（验证码用途不被签发口接受、自助注销被管理矩阵拦成 422、`recover` 无比较-交换、失败审计是合并写所以概览会低估、批量额度范围越界 500、HSTS/500 头对不上仓库现实、bool 设置无落点、导出取数方式未定、恢复失败不可见），另有 8 条 P2 加固项。**9 条 P1 与 8 条 P2 已全部就地改入 §25 正文并新增 AC-239–251，不存在遗留的 P0/P1**；文档版本因此由 v3 升为 **v4**。本节只改动 §25 与文档顶部的版本/评审记录，§24 一字未动，未触碰任何源码，未执行 `git commit`。

**通过的条件**（实现与终审节点必须满足，缺一不可）：

1. **AC-239 … AC-251 与 AC-209 … AC-238 同级**：它们都是自动化用例，不得以“设计里写了”代替。其中 AC-240（`recover` 并发 CAS）与 AC-239（`recover` 验证码用途）是 Y3 能否成立的前提，**先写这两条再写实现**。
2. **提交只发生一次，且只由终审节点执行**（RV-Y15）：实现节点交付“可提交”的工作树；终审节点在提交前**逐文件 Read** 三个游离文件再决定去留（`probe_db.py` 默认移出仓库），并把本评审的 RV-Y* 与实现期发现的缺陷（沿用 I-/R- 体例）写进 §25 “实施过程发现的方案缺陷”。
3. **走查证据不可替代**：AC-235 的真实浏览器走查（两种主机写法 × 三视口、截图入库、`walkthrough/README.md` 记环境）不能被 skip 的 e2e 或 curl 级冒烟代替（E-21）；浏览器确实起不来时，完成报告必须写明阻塞原因并保留可复跑脚本，**不得伪造截图**（R-Y11）。
4. **首屏预算不上调**（245 kB）；`api.admin.ts` 拆分若不能让首屏“不增反降”则放弃拆分（Y2(d) 是 P3 项，不得为它破坏任何门禁）。
5. **优先级**（R-Y12）：若时间不足，可顺延的是 Y5(b) 批量赠送与 Y2(d) 拆包；**不可裁撤**的是 Y1、Y3（含 RV-Y1/Y3/Y5 的三处）与 Y6 的走查。

**评审发现一览**（详见文档顶部“评审记录（迭代 2）”）：RV-Y1 验证码用途 `recover`（P1）· RV-Y2 自助注销授权拆分与 root 沿用既有码（P1）· RV-Y3 `recover` CAS（P1）· RV-Y4 `security24h` 失败类求和 `detail.count`（P1）· RV-Y5 `auth.recovery.failure` 审计（P1，ACTIONS 31→36）· RV-Y6 批量额度 ±50 / 404→403 / 夹紧语义（P1）· RV-Y7 HSTS 位置与 500 的 `nosniff`（P1）· RV-Y8 bool 设置的落点（P1）· RV-Y9 流式导出取数细则（P1）· RV-Y10–Y17（P2）。

### §25 主节点（M53 迁移 / M58 走查与收口）

> ID 前缀 `Y-M`。

| ID | 位置 | 发现（证据） | 采用的修正 |
| --- | --- | --- | --- |
| Y-M1 | §25.3.6 走查 / AC-235 | 开发机的 `8000` 端口被一个常驻的 `uvicorn --reload`（非本流程启动）占用；按设计"启动 `start-all.ps1`"会与之冲突，或误杀用户的服务 | 走查用 `8100/3100` 另起隔离栈 + 全新 SQLite，不碰用户的库与进程；`walkthrough.mjs` 接受端口参数；README 记录 |
| Y-M2 | AC-235 "360 px 无横向滚动" | `/admin/users` 在 360 px 下横向溢出 177 px：表头 `sr-only`（绝对定位）不被 `overflow-x-auto` 包裹层裁剪（它不是定位祖先），撑大了文档宽度。**单测与 curl 都看不到，只有真实浏览器 + 量 `scrollWidth` 才暴露** | `UsersTable` 滚动包裹层加 `relative`；`AuditTable / InvitesPanel / AccountClient` 同形包裹层当前没有 `sr-only` 子元素（实测 0 溢出），未改 |
| Y-M3 | AC-235 "全程控制台 0 error" | 游客访问标的页时偶发 `Minified React error #418`：「生成于 N 分钟前」的相对时间在 SSR 与水合之间跨分钟边界即文本不一致（既有隐患，与认证无关，但属于 AC-235 的门禁） | 相对时间文本节点加 `suppressHydrationWarning`（`ReportPanel / ReportLibraryRow / RecentReports / HistoricalBanner`）；两主机各整轮 0 error |
| Y-M4 | §25.3.1(a) / E-17 / AC-211 | `docs/plans/report-library/probe_db.py`：be-admin 评估后**保留**——已提交的 report-library spec 引用了它 5 次作为证据数字的复现脚本。它目前仍是 `??`（未跟踪） | 交给终审 + 提交节点：要么一并提交，要么先去掉 spec 里的引用再移出仓库；本节点不替终审做决定 |
| Y-M5 | §25.7 AC-237 | 后端全量 `pytest` 实跑：**2232 个结果点，10 skipped，0 failed**（`ruff` 干净）；前端 `vitest` 669 passed / 1 skipped（50 文件）、`tsc` + `eslint` 干净、`next build` 通过、首屏 JS **209.0 kB**（迭代 1 为 209.3，**不增反降**，阈值 245 未动）；`alembic heads` 唯一 `0008_account_recovery` | 见完成报告 |

