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

import asyncio
import contextlib
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
    agent,
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
from .services import ask_service, report_service, search_index
from .services.run_manager import get_manager
from .services.scheduler_ticker import get_ticker

logger = get_logger("main")

#: ★v12（§22.3.3 第 2 层）：独立于 `SCHEDULER_ENABLED` 的活性清扫周期。
#: 用 60 秒而不是 ticker 的 30 秒：这是**对账**，不是调度，没有必要跟调度同频
#: （V11-P2-9 把两处的数字统一成"30 秒 tick，截止 = max_duration + grace"；
#: 清扫周期单列，因为它与 tick 是两件事）。
STALE_SWEEP_SECONDS = 60

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


async def _sweep_loop() -> None:
    """★v12（S2 第 2 层）：每 `STALE_SWEEP_SECONDS` 秒对账一次"还在 running 但没人管"的 run。

    为什么是常驻任务而不是挂在 ticker 上（V11-P1-4）：`SCHEDULER_ENABLED=0` 时 ticker
    根本不启动（`lifespan` 里那两行就是全部真相），而"关掉定时刷新"恰恰是最需要自愈的
    配置。这也是为什么这里**不**用 `asyncio.create_task` 再包一层：`sweep_stale_runs()`
    是同步函数（只碰 DB），直接调、异常直接落日志，而不是丢进一个没人 await 的 Task。

    ★v14（U5 / ★P1-14）：`purge_trash()` 也挂**这里**，理由与 V11-P1-4 同构——
    `SCHEDULER_ENABLED=0` 的部署不会启动 ticker，挂在 ticker 上等于"回收站永不清理、
    `.db` 只增不减"。它同样只碰 DB，异常只记日志（不变量 3：清理失败不改任何响应结论）。
    """
    while True:
        try:
            await asyncio.sleep(STALE_SWEEP_SECONDS)
            swept = get_manager().sweep_stale_runs()
            if swept:
                logger.warning("活性清扫：%s 个卡在 running 的 run 已标记 failed(agent_timeout)", swept)
            _purge_trash_once()
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:  # pragma: no cover - 一次对账失败不该杀死清扫
            logger.exception("活性清扫失败")


def _purge_trash_once() -> None:
    """回收站硬清理（唯一入口调用的地方之一，另一个是 lifespan）。

    ★P1-14：**不能**挂 scheduler tick（ticker 只在 `SCHEDULER_ENABLED=1` 时启动）。
    永不抛：清理是后台事务，失败只记日志。
    """
    session = SessionLocal()
    try:
        purged = report_service.purge_trash(session)
        if purged["reports"]:
            logger.info("回收站清理：%s 份报告 / %s 条追问已永久删除", purged["reports"], purged["asks"])
    except Exception:  # pragma: no cover - 清理失败不影响服务
        logger.exception("回收站清理失败（不影响任何读路径）")
        session.rollback()
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    assert_single_worker()

    # ★v12（S1 / §22.3.2(b)）：启动即自证"这个进程能不能 spawn 子进程"。
    # **不 raise**（S-2）：一个起不来的服务比一个"能看不能生成"的服务更糟——既有报告
    # 仍然可读可检索，只是新的生成会被 `RunManager.start()` 第 0 步拒绝并说清原因。
    from .agent.preflight import check_aragon, running_loop_name, spawn_capability

    # ★评审修正（E-114）：**先把"本进程在哪个 loop 上跑"记下来**，再判能力。
    # `lifespan` 是 async 的，这里的 `get_running_loop()` 就是 serve loop 的真身；
    # 之后同步路由（`/capabilities` 在 threadpool 里跑，自己没有 running loop）问
    # `spawn_capability()` 时才有东西可查。顺序不能反：反过来那一次判定仍会落到
    # "看 asyncio 策略"的分支上，而 uvicorn 换的是工厂、不是策略（E-114）。
    loop_name = running_loop_name()
    loop_ok, loop_reason = spawn_capability()
    if not loop_ok:
        # 不是 warning 级别的问题：这个进程**不可能**完成任何一次生成。
        logger.error("startup: 子进程不可用 (%s)；生成将被拒绝，请去掉 --reload", loop_reason)

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
        # ★v12（S2 第 2 层 / V11-P1-4）：**先同步清扫一次**。`recover_interrupted()` 把
        # 所有 ACTIVE 行都收了，它盖得住的只有"重启前那个进程已死"这一种；真正需要
        # 清扫的是"进程还活着、但某个 run 的协程已经不在了"（`create_version()` 撞
        # SQLITE_BUSY 之后停在 running 就是这种）。两件事分开做，而不是指望一个函数。
        swept = get_manager().sweep_stale_runs()
        if swept:
            logger.warning("启动清扫：%s 个卡在 running 的 run 已标记 failed(agent_timeout)", swept)
        get_manager().cleanup_workspaces(session)
        # ★v14（U5 / §23.3.5）：启动就跑一次回收站硬清理（与 `cleanup_workspaces()` 并列）。
        # 它只碰 DB，异常只记日志——清理失败不该拒绝启动（S-2 的同一条纪律）。
        try:
            purged = report_service.purge_trash(session)
            if purged["reports"]:
                logger.info("启动清理：%s 份过期报告 / %s 条追问已永久删除", purged["reports"], purged["asks"])
        except Exception:  # pragma: no cover - 清理失败不影响启动
            logger.exception("启动清理回收站失败（不影响启动）")
            session.rollback()
        # ★V4-P1-5: an ask left `queued`/`running` by a killed process would otherwise
        # keep its SSE stream polling forever and make cancel a silent no-op.
        stale_asks = ask_service.recover_interrupted(session)
        if stale_asks:
            logger.warning("启动恢复：%s 个中断的追问已标记 failed(interrupted)", stale_asks)
        # Ask rows and ask workspaces expire with the same retention window (V4-P2-9).
        ask_service.cleanup_asks(session)
    finally:
        session.close()

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

    # ★v12（S2 第 2 层 / V11-P1-4）：活性清扫**不看 `SCHEDULER_ENABLED`**。
    # 过去把它挂在 `scheduler_ticker` 上，而该开关为 0 时 ticker 根本不启动
    # （`main.py` 的那两行就是全部真相）⇒ 最需要自愈的部署恰恰整层消失。
    # 这里挂一个独立的 60 秒常驻任务：同一个死协程，另一种来源。
    sweeper = asyncio.create_task(_sweep_loop(), name="stale-sweeper")

    logger.info(
        "startup complete version=%s agent_mode=%s aragon=%s capable=%s loop=%s loopOk=%s",
        __version__,
        settings.agent_mode,
        capability.cli or "unavailable",
        capability.fullyCapable,
        # ★评审修正（E-114）：`loop` 打**循环类名**（诊断要看的就是这个），`loopOk` 单独一位
        # ——过去把 `loopReason or "ok"` 塞进 `loop=`，读起来像"循环没事"，而"这个进程不能
        # spawn"这条事实反而没有位置。
        loop_name or "unknown",
        capability.loopOk,
    )
    yield

    # Shutdown order matters: stop the trigger source first, then kill children, so
    # no new run is created while we are sweeping (§18.9 M10 exit criterion).
    sweeper.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await sweeper
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
    # ★v9（K2）：引擎自检（`POST/GET /agent/probe`）。
    app.include_router(agent.router, prefix=api_prefix)

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
