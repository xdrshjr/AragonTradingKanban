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


class BudgetExceeded(Conflict):
    code = "budget_exceeded"
    message = "生成超出预算（turns / duration）"


class TooManyRuns(AppError):
    code = "too_many_runs"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "当前排队任务过多，请稍后重试"


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


class Unauthorized(AppError):
    code = "unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "缺少或无效的凭据"


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
