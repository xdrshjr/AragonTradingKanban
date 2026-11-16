"""FastAPI application factory (spec §6.2).

Lifespan does four things, in this order, and refuses to start if any of the
first three is violated:

1. **Single-worker assertion** (★P1-5 / AC-23). Run orchestration is in-process;
   a second worker would put the task in process A and the SSE connection in
   process B, and the progress stream would simply spin forever.
2. **Migrations** (★P1-2). Alembic is the only schema source — the ORM's
   create-all helper lives exclusively in `tests/conftest.py` (R-14).
3. **Recovery** (★P1-5). Anything left `queued`/`running`/`cancelling` is dead
   the moment this process starts, so it becomes `failed(interrupted)`.
4. Housekeeping: expired run workspaces are removed.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .db.session import SessionLocal, engine, fts_available
from .errors import Unauthorized, register_exception_handlers
from .logging import configure_logging, get_logger, request_id_var
from .routers import (
    asks,
    capabilities,
    cost,
    health,
    instruments,
    reports,
    runs,
    schedules,
    search,
    symbols,
    watchlist,
)
from .services import ask_service, search_index
from .services.run_manager import get_manager
from .services.scheduler_ticker import get_ticker

logger = get_logger("main")

#: Paths that must stay reachable without a bearer token. SSE uses `?token=`
#: instead of a header because `EventSource` cannot set one (★P1-3).
PUBLIC_PATHS = ("/api/v1/health", "/docs", "/openapi.json", "/redoc")
SSE_SUFFIX = "/events"


def assert_single_worker() -> None:
    """Refuse to boot with more than one worker (R-13)."""
    workers_raw = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS") or "1"
    try:
        workers = int(workers_raw)
    except ValueError:
        workers = 1
    if workers > 1:
        raise RuntimeError(
            "Aragon Research Terminal 必须以单 worker 运行（生成编排是进程内任务，"
            "多 worker 会导致 SSE 连到没有任务的进程）。请使用 --workers 1。"
        )
    if "uvicorn" in sys.modules:
        argv = " ".join(sys.argv)
        if "--workers" in argv:
            try:
                value = int(argv.split("--workers", 1)[1].split()[0])
            except (IndexError, ValueError):
                value = 1
            if value > 1:
                raise RuntimeError("检测到 --workers > 1，请改为 --workers 1（见 spec R-13）。")


def run_migrations() -> None:
    """`alembic upgrade head` at startup — the only schema writer (★P1-2)."""
    from alembic import command
    from alembic.config import Config

    settings = get_settings()
    api_dir = settings.data_dir.parent
    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("script_location", str(api_dir / "app" / "db" / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    assert_single_worker()

    try:
        run_migrations()
    except Exception:  # pragma: no cover - misconfigured deploy
        logger.exception("数据库迁移失败")
        raise

    session = SessionLocal()
    try:
        recovered = get_manager().recover_interrupted(session)
        if recovered:
            logger.warning("启动恢复：%s 个中断的 run 已标记 failed(interrupted)", recovered)
        get_manager().cleanup_workspaces(session)
        # ★V4-P1-5: an ask left `queued`/`running` by a killed process would otherwise
        # keep its SSE stream polling forever and make cancel a silent no-op.
        stale_asks = ask_service.recover_interrupted(session)
        if stale_asks:
            logger.warning("启动恢复：%s 个中断的追问已标记 failed(interrupted)", stale_asks)
        # Ask rows and ask workspaces expire with the same retention window (V4-P2-9).
        ask_service.cleanup_asks(session)
    finally:
        session.close()

    from .agent.preflight import check_aragon

    capability = check_aragon()
    # ★V4-P1-8: say out loud whether this build can index at all, so a missing
    # ENABLE_FTS5 shows up in the log instead of as "search finds nothing".
    with engine.connect() as probe:
        tables_ready = search_index.fts_ready(probe)
    logger.info(
        "full-text search: compile_options=%s tables=%s",
        "fts5" if fts_available() else "no-fts5",
        "ready" if tables_ready else "missing",
    )
    if settings.scheduler_enabled:
        get_ticker().start()
    else:
        logger.info("scheduler disabled (SCHEDULER_ENABLED=0)")

    logger.info(
        "startup complete version=%s agent_mode=%s aragon=%s capable=%s",
        __version__,
        settings.agent_mode,
        capability.cli or "unavailable",
        capability.fullyCapable,
    )
    yield

    # Shutdown order matters: stop the trigger source first, then kill children, so
    # no new run is created while we are sweeping (§18.9 M10 exit criterion).
    await get_ticker().stop()
    manager = get_manager()
    for run_id in list(manager._tasks):  # noqa: SLF001 - deliberate shutdown sweep
        manager.cancel_now(run_id)
    # Same sweep for `deep` asks: they live in their own registry (V4-P1-5), so the
    # run sweep above cannot see them and their child processes would be orphaned.
    from .services.ask_registry import get_ask_registry

    for ask_id in get_ask_registry().active_ids():
        get_ask_registry().cancel(ask_id)
    engine.dispose()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Aragon Research Terminal API",
        version=__version__,
        description="投研报告工作台后端：标的解析、行情缓存、报告版本化与 AI 生成编排。",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def _observability_and_auth(request: Request, call_next):  # noqa: ANN001, ANN202
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token_handle = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            if settings.api_token and _requires_auth(request):
                header = request.headers.get("authorization", "")
                expected = f"Bearer {settings.api_token}"
                if header != expected:
                    raise Unauthorized("缺少或无效的 Authorization: Bearer <API_TOKEN>")
            response = await call_next(request)
        except Unauthorized as exc:
            response = JSONResponse(status_code=exc.status_code, content=exc.envelope())
        finally:
            request_id_var.reset(token_handle)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-Id"] = request_id
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        return response

    # CORS is added *after* the auth middleware on purpose: Starlette prepends
    # each `add_middleware` call, so the last one is the outermost. If auth were
    # outermost, every 401 would leave the browser as an opaque CORS failure.
    if not settings.cors_origins:
        # Never fail open: an empty allow-list means "same origin only", which is
        # the right reading of an empty ALLOW_ORIGINS — not "allow everything".
        logger.warning("ALLOW_ORIGINS 为空：仅允许同源访问（不会放开为 *）")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Request-Id"],
    )

    register_exception_handlers(app)

    api_prefix = "/api/v1"
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(capabilities.router, prefix=api_prefix)
    app.include_router(symbols.router, prefix=api_prefix)
    app.include_router(instruments.router, prefix=api_prefix)
    app.include_router(reports.router, prefix=api_prefix)
    app.include_router(runs.router, prefix=api_prefix)
    app.include_router(asks.router, prefix=api_prefix)
    app.include_router(search.router, prefix=api_prefix)
    app.include_router(schedules.router, prefix=api_prefix)
    # ★v6 G3：成本面板的读侧（`/cost/summary` / `/cost/entries` / `/cost/budget`）。
    app.include_router(cost.router, prefix=api_prefix)
    app.include_router(watchlist.router, prefix=api_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"name": "Aragon Research Terminal API", "version": __version__, "docs": "/docs"}

    return app


def _requires_auth(request: Request) -> bool:
    path = request.url.path
    if any(path.startswith(public) for public in PUBLIC_PATHS):
        return False
    # SSE carries its own short-lived `?token=` signature (★P1-3).
    if path.endswith(SSE_SUFFIX) and request.query_params.get("token"):
        return False
    return request.method != "OPTIONS"


app = create_app()
