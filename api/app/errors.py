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
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.detail = detail or {}
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
    code = "unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "缺少或无效的凭据"


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


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        logger.info("app_error code=%s status=%s message=%s", exc.code, exc.status_code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.envelope())

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
        return JSONResponse(status_code=err.status_code, content=err.envelope())
