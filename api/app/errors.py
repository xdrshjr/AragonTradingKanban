"""Error taxonomy and the single error envelope shape (spec §7.1, §7.3).

Every failure leaves the API as::

    {"error": {"code": "...", "message": "...", "requestId": "...", "detail": {...}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import get_logger, request_id_var

logger = get_logger("errors")


class AppError(Exception):
    """Base class for every error the client is allowed to see."""

    code: str = "internal"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "服务器内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.detail = detail or {}
        #: Extra response headers (`Retry-After` for 429/503). Empty for almost every error.
        self.headers: dict[str, str] = headers or {}
        super().__init__(self.message)

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "requestId": request_id_var.get(),
                "detail": self.detail,
            }
        }


class BadRequest(AppError):
    code = "bad_request"
    status_code = status.HTTP_400_BAD_REQUEST
    message = "请求参数不合法"


class InvalidQuery(AppError):
    code = "invalid_query"
    status_code = 422
    message = "查询条件不合法"


class InvalidAskContext(AppError):
    code = "invalid_ask_context"
    status_code = 422
    message = "历史上下文已不可用，请开始新话题"


class NotFound(AppError):
    code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "资源不存在"


class InstrumentNotFound(NotFound):
    code = "instrument_not_found"
    message = "无法解析该投资标的"


class ReportNotFound(NotFound):
    code = "report_not_found"
    message = "报告不存在"


class RunNotFound(NotFound):
    code = "run_not_found"
    message = "运行记录不存在"


class Conflict(AppError):
    code = "conflict"
    status_code = status.HTTP_409_CONFLICT
    message = "状态冲突"


class RunConflict(Conflict):
    code = "run_conflict"
    message = "该标的已有进行中的生成任务"


class LatestConflict(Conflict):
    code = "latest_conflict"
    message = "最新版本指针冲突，请重试"


class RunNotActive(Conflict):
    code = "run_not_active"
    message = "该运行已结束，无法取消"


class ProbeInProgress(Conflict):
    """★v9（K2 / V10-P2-6）：探针单飞 —— **另一次自检**在跑。

    三个新 HTTP 码都走 `AppError` 子类，**不裸抛 `HTTPException`**：后者会被
    `_http_handler` 压成 `conflict` / `service_unavailable`，前端分不出来
    （而"再等等"与"关掉开关"是两个完全不同的动作）。
    """

    code = "probe_in_progress"
    message = "另一次引擎自检正在运行，请稍后"


class ProbeBusy(Conflict):
    """★v9（V10-P2-11）：**有真实生成在跑**，探针不 spawn。

    与 `probe_in_progress` 分开：前者是"别人在忙"（稍后即可），后者是"另一次自检在跑"。
    前端把按钮置灰而不是弹错。
    """

    code = "probe_busy"
    message = "有生成任务正在运行，稍后再点自检"


class BudgetExceeded(Conflict):
    code = "budget_exceeded"
    message = "生成超出预算（turns / duration）"


class TooManyRuns(AppError):
    code = "too_many_runs"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "当前排队任务过多，请稍后重试"


class MonthlyBudgetExceeded(AppError):
    """★v6（V6-P0-2）：月度预算闸门的**独立**错误类。

    不要复用 `BudgetExceeded`：它是 409、语义是"生成超出 turns / duration 预算"，
    而 `web/lib/types.ts` 的 `RunStatus` 里已经有一个同名终态字面量在用它。复用会把
    "本月花超了"伪装成"这次生成超时"，改它的 status 又会静默改写 v1 就存在的行为。

    402 只在**用户显式设了预算**时出现；`BUDGET_MONTHLY_USD=0` 恒放行。
    """

    code = "monthly_budget_exceeded"
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    message = "本月预算已用完"


class InvalidRange(AppError):
    """`/reports/{id}/blocks` 的区间参数非法（`from > to` 或不可解析）。"""

    code = "invalid_range"
    status_code = 422
    message = "区间不合法（from 必须不晚于 to，且为 YYYY-MM-DD）"


class RangeTooWide(AppError):
    """跨度超过 5 个日历年——挡住"拿 1900-01-01 做全量扫描"这类用法。"""

    code = "range_too_wide"
    status_code = 422
    message = "区间跨度过大（上限 5 个日历年）"


class Unavailable(AppError):
    code = "data_provider_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "数据源暂不可用"


class AragonUnavailable(Unavailable):
    code = "aragon_unavailable"
    message = "生成引擎（aragon）不可用"


class StructuredResultUnsupported(Unavailable):
    code = "structured_result_unsupported"
    message = "生成引擎缺少结构化结果能力（submit_result / ajv）"


class EventLoopUnsupported(Unavailable):
    """★v12（S1 / §22.5.3）：本进程的事件循环 spawn 不了子进程（Windows 上的 `--reload`）。

    503 而不是 500：这是"这台机器现在做不了这件事"，不是"我们写错了"。同步路径
    （`RunManager.start()` 第 0 步）直接抛它，用户点下去立刻看到原因，而不是等 10 秒
    拿到一个 `internal`（E-89）。

    ★v12（C-39）：**这里不写 hint**。hint 的唯一真源是
    `agent/failure.py::FailureSpec.hint`（`hint_for()` 是读取入口）；写在这里的字符串
    没有任何读取者，前端拿到的 `hint` 会一直是 `null`。
    """

    code = "event_loop_unsupported"
    message = "当前进程的事件循环不支持启动子进程"


class ProbeDisabled(Unavailable):
    """`AGENT_PROBE_ENABLED=0`。与 `agent_mock_mode` 分开：原因不同，动作也不同。"""

    code = "probe_disabled"
    message = "引擎自检已被配置关闭（AGENT_PROBE_ENABLED=0）"


class AgentMockMode(Unavailable):
    """`AGENT_MODE=mock` 时探针没有意义（它量的是真实端点）。"""

    code = "agent_mock_mode"
    message = "当前是示例模式（AGENT_MODE=mock），引擎自检不可用"


class Unauthorized(AppError):
    """The one 401 code (spec E-9). `detail.reason` says why: `no_session | expired |
    revoked | disabled` — the front end treats "never had a session" (no_session) very
    differently from "your session just died"."""

    code = "unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "缺少或无效的凭据"

    def __init__(
        self,
        message: str | None = None,
        *,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        merged = dict(detail or {})
        if reason is not None:
            merged["reason"] = reason
        super().__init__(message, detail=merged or None, **kwargs)


class InvalidQuestion(AppError):
    """§7.3: empty / too long / control characters — never a 500."""

    code = "invalid_question"
    status_code = 422
    message = "问题不合法（1–500 字符，且不能包含控制字符）"


class InvalidCron(AppError):
    code = "invalid_cron"
    status_code = 422
    message = "cron 表达式不合法（需 5 个字段，且各字段在取值范围内）"


class DiffSameReport(AppError):
    code = "diff_same_report"
    status_code = 422
    message = "同两个版本不能比较"


class ReportNotReady(Conflict):
    """`deep` needs a grounded baseline; provisional/degraded has none (§7.3)."""

    code = "report_not_ready"
    message = "这份报告是临时版或降级版，请先重新生成再深度追问"


class AskNotActive(Conflict):
    code = "ask_not_active"
    message = "该追问已结束，无法取消"


class DuplicateSchedule(Conflict):
    code = "duplicate_schedule"
    message = "该标的已有启用中的定时刷新"


class TooManyAsks(AppError):
    code = "too_many_asks"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "今日深度追问额度已用完"


class AskDeepUnavailable(Conflict):
    """★ 实施补充：`features.askDeep=false` 时的显式拒绝，不做静默降级。

    The design expresses this state as a capability flag; §7.3's table has no code
    for it. A 409 with an explicit code beats a `cite` answer pretending to be a
    `deep` one (see `## 实施过程发现的方案缺陷` in the spec).
    """

    code = "ask_deep_unavailable"
    message = "当前生成引擎不可用，深度追问已关闭（引用式检索仍可用）"


class NotImplementedFeature(AppError):
    code = "not_implemented"
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    message = "本期未实现"


class ExportUnsupported(NotImplementedFeature):
    code = "export_unsupported"
    message = "该导出格式暂不支持"


class SchemaUnsatisfied(AppError):
    code = "result_schema_unsatisfied"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "生成结果不满足结构化契约"


#: ★v14（§23.5.5）：三条新码**全是 HTTP 码**，不进 `RUN_ERROR_CODES`
#: （`tests/test_contract_codes.py` 会替我们盯着"run 码与 HTTP 码不相交"）。
#: 三条的语义都是"换个参数 / 刷新一下就能继续"，与既有 `schema_drift` 的 `retry` 同类。
class InvalidCursor(AppError):
    """`before` 不是 `"{iso}|{32 hex}"`。

    非法游标必须 **422**，不能静默忽略：静默忽略会让"翻页翻不动"变成一个没人能诊断的
    幽灵 bug（★P1-3）。也**不做**任何时间解析/归一化——一次
    `datetime.fromisoformat(...).isoformat()` 往返就会把 `Z` 变成 `+00:00`，于是字符串比较
    `generated_at < ?` **整段漏行**且不报错（R-76）。
    """

    code = "invalid_cursor"
    status_code = 422
    message = "游标不合法"


class ReportNotDeleted(Conflict):
    """对一份**没被删除**的报告调 restore ⇒ 409。

    不是 200：那会让前端以为"状态变了"而实际上什么都没发生。
    """

    code = "report_not_deleted"
    status_code = status.HTTP_409_CONFLICT
    message = "这份报告没有被删除，无需恢复"


class BaseReportMismatch(AppError):
    """`baseReportId` 不属于该标的（或已被硬删）⇒ 422。

    ★评审 P1-15：**取不到结构**（provisional / corrupt）**不是** 422 —— 那种情况按"无 base"
    处理，不报错、不崩。只有"不属于这个标的"才是参数错误。
    """

    code = "base_report_mismatch"
    status_code = 422
    message = "所选的基准版本不属于该标的"


# ── ★v15（§24.5.4）：用户与权限系统的错误码。全部走同一个信封；前端 `authErrors.ts` 逐码有文案。
class Forbidden(AppError):
    """已登录但权限不足。`detail.permission` 是缺的那条权限串。"""

    code = "forbidden"
    status_code = status.HTTP_403_FORBIDDEN
    message = "没有权限执行该操作"

    def __init__(self, message: str | None = None, *, permission: str | None = None, **kwargs: Any) -> None:
        detail = dict(kwargs.pop("detail", None) or {})
        if permission is not None:
            detail["permission"] = permission
        super().__init__(message, detail=detail or None, **kwargs)


class InvalidCredentials(AppError):
    """用户名或密码不对——**不区分**不存在 / 密码错（AC-170）。"""

    code = "invalid_credentials"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "用户名或密码不正确"


class AccountDisabled(AppError):
    """密码正确、但账号被停用。密码不对时仍是 `invalid_credentials`（不泄露哪些名字存在）。"""

    code = "account_disabled"
    status_code = status.HTTP_403_FORBIDDEN
    message = "该账号已被停用，请联系管理员"


class AccountLocked(AppError):
    code = "account_locked"
    status_code = 423
    message = "登录失败次数过多，账号已被临时锁定"

    def __init__(self, message: str | None = None, *, retry_after_ms: int = 0, **kwargs: Any) -> None:
        super().__init__(
            message,
            detail={"retryAfterMs": int(retry_after_ms)},
            headers={"Retry-After": str(max(1, (int(retry_after_ms) + 999) // 1000))},
            **kwargs,
        )


class PasswordChangeRequired(AppError):
    code = "password_change_required"
    status_code = status.HTTP_403_FORBIDDEN
    message = "请先修改初始密码"


class CsrfRejected(AppError):
    code = "csrf_rejected"
    status_code = status.HTTP_403_FORBIDDEN
    message = "请求来源不受信任"

    def __init__(self, message: str | None = None, *, origin: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, detail={"origin": origin or ""}, **kwargs)


class RateLimited(AppError):
    code = "rate_limited"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "操作过于频繁，请稍后再试"

    def __init__(
        self, message: str | None = None, *, scope: str = "", retry_after_ms: int = 1000, **kwargs: Any
    ) -> None:
        super().__init__(
            message,
            detail={"scope": scope, "retryAfterMs": int(retry_after_ms)},
            headers={"Retry-After": str(max(1, (int(retry_after_ms) + 999) // 1000))},
            **kwargs,
        )


class QuotaExceeded(AppError):
    """额度不足（预检或预留），或全站日总量已满（`detail.scope='site'`，`canRequest=false`）。

    M48 的额度引擎抛它；类在 M45 先建好，让码表与前端 `authErrors.ts` 从第一天起对得上。
    """

    code = "quota_exceeded"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "今日生成额度已用完"


class CaptchaRequired(AppError):
    code = "captcha_required"
    status_code = 422
    message = "请输入验证码"


class CaptchaInvalid(AppError):
    """验证码错 / 过期 / 已用 / 蜜罐 / 过快——**一律同一个响应**（AC-168 要求字节级相同）。"""

    code = "captcha_invalid"
    status_code = 422
    message = "验证码不正确或已过期"


class CaptchaUnavailable(AppError):
    code = "captcha_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "验证码服务暂不可用，暂时无法注册"


class RegistrationClosed(AppError):
    code = "registration_closed"
    status_code = status.HTTP_403_FORBIDDEN
    message = "注册已关闭，请联系管理员"


class InviteRequired(AppError):
    code = "invite_required"
    status_code = 422
    message = "当前仅支持邀请码注册"


class InviteInvalid(AppError):
    code = "invite_invalid"
    status_code = 422
    message = "邀请码无效、已过期或已用完"


class InvalidUsername(AppError):
    code = "invalid_username"
    status_code = 422
    message = "用户名需为 3–20 位字母、数字或下划线，且不能使用保留词"


class UsernameTaken(AppError):
    code = "username_taken"
    status_code = status.HTTP_409_CONFLICT
    message = "该用户名已被占用"


class WeakPassword(AppError):
    """`detail.rules` 是**未满足**的规则 id（前端据此点亮强度条下的清单）。"""

    code = "weak_password"
    status_code = 422
    message = "密码强度不足"

    def __init__(self, message: str | None = None, *, rules: list[str] | None = None, **kwargs: Any) -> None:
        super().__init__(message, detail={"rules": list(rules or [])}, **kwargs)


class RecoveryInvalid(AppError):
    """恢复码类失败的**唯一**出口：用户不存在 / 已停用 / 是 root / 没设码 / 码不对 / 并发输家，
    状态码与响应体逐字节相同（AC-219）。"""

    code = "recovery_invalid"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "用户名或恢复码不正确。请检查是否多输了空格；若恢复码已丢失，请联系管理员重置。"


class RecoveryDisabled(AppError):
    code = "recovery_disabled"
    status_code = status.HTTP_403_FORBIDDEN
    message = "站点暂未开放自助找回密码。请联系管理员重置。"


class RootProtected(AppError):
    code = "root_protected"
    status_code = status.HTTP_403_FORBIDDEN
    message = "根管理员账号受保护，不可修改"

    def __init__(self, message: str | None = None, *, action: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, detail={"action": action or ""}, **kwargs)


class SelfActionForbidden(AppError):
    code = "self_action_forbidden"
    status_code = 422
    message = "不能对自己执行该操作"

    def __init__(self, message: str | None = None, *, action: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, detail={"action": action or ""}, **kwargs)


class AdminProtected(AppError):
    code = "admin_protected"
    status_code = status.HTTP_403_FORBIDDEN
    message = "只有根管理员可以对管理员执行该操作"

    def __init__(self, message: str | None = None, *, action: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, detail={"action": action or ""}, **kwargs)


class QuotaRequestPending(Conflict):
    code = "quota_request_pending"
    message = "已有一条待处理的额度申请"

    def __init__(self, message: str | None = None, *, request_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, detail={"requestId": request_id or ""}, **kwargs)


class InvalidSetting(AppError):
    code = "invalid_setting"
    status_code = 422
    message = "设置值不合法"

    def __init__(self, message: str | None = None, *, field: str = "", **kwargs: Any) -> None:
        super().__init__(message, detail={"field": field}, **kwargs)


class PolicyMissing(AppError):
    """路由没有登记进策略表。启动期集合校验使它**理论上不可达**；到了这里宁可 500 也不放行。"""

    code = "policy_missing"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "服务器内部错误"

    def __init__(self, message: str | None = None, *, route: str = "", **kwargs: Any) -> None:
        super().__init__(message, detail={"route": route}, **kwargs)


class Busy(AppError):
    """KDF 槽位排队超时（RV-7）。不区分是登录还是注册，也不泄露任何账号信息。"""

    code = "busy"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "服务繁忙，请稍后重试"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, headers={"Retry-After": "2"}, **kwargs)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        logger.info("app_error code=%s status=%s message=%s", exc.code, exc.status_code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.envelope(), headers=exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": ".".join(str(part) for part in item.get("loc", [])),
                "msg": item.get("msg", ""),
                "type": item.get("type", ""),
            }
            for item in exc.errors()[:12]
        ]
        # `invalid_query` is the symbol-search contract (§7.3); a bad body on any
        # other route is a plain `validation_error` and must not claim to be one.
        err: AppError = (
            InvalidQuery(detail={"errors": errors})
            if request.url.path.startswith("/api/v1/symbols")
            else AppError(
                code="validation_error",
                message="请求参数不合法",
                status_code=422,
                detail={"errors": errors},
            )
        )
        return JSONResponse(status_code=err.status_code, content=err.envelope())

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Every status a client can legitimately trigger needs its own code, or
        # the front end cannot branch and a 405 looks like a server crash.
        code = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            413: "payload_too_large",
            415: "unsupported_media_type",
            422: "validation_error",
            429: "too_many_requests",
            501: "not_implemented",
            502: "upstream_error",
            503: "service_unavailable",
            504: "upstream_timeout",
        }.get(exc.status_code, "internal")
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        err = AppError(code=code, message=message, status_code=exc.status_code)
        return JSONResponse(status_code=exc.status_code, content=err.envelope())

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error path=%s", request.url.path)
        err = AppError(message="服务器内部错误")
        # ★v16（RV-Y7）：本处理器由最外层 ServerErrorMiddleware 调用，用户中间件加的头到不了它。
        return JSONResponse(
            status_code=err.status_code,
            content=err.envelope(),
            headers={"X-Content-Type-Options": "nosniff"},
        )
