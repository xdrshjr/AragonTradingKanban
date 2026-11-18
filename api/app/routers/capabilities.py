"""`/capabilities` — what the front end needs to decide on a degraded banner (P1-6).

Every feature flag is a **measurement** (§7.1 / V4-P2-4): the ask entry point, the
search box and the schedule dialog appear or disappear based on what actually works
here, not on what the design hoped worked.

★v6 adds five booleans (G1–G4) and one new top-level object. Each boolean is
derived from a real probe in this process, and each defaults to `False` — "the
entry appeared but did nothing" is the exact defect V4-P2-4 fixed by measurement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..agent import decide_mode, schema_budget
from ..agent.aragon_runner import clamp_repair_turns
from ..agent.cost_model import COST_SOURCES  # noqa: F401 - 文档性引用，说明三态来源
from ..agent.fingerprint import current_fingerprint
from ..agent.preflight import check_aragon, running_loop_name, session_dir_size_mb, spawn_capability
from ..agent.pricing import PRICE_VERSION
from ..agent.runner_base import AgentError
from ..config import get_settings
from ..db.session import get_db
from ..logging import get_logger
from ..schemas.capabilities import (
    CapabilitiesResponse,
    DataProviderCapability,
    EngineCapabilityOut,
    EngineLastFailureOut,
    EngineLimitsOut,
    EngineSchemaOut,
    FeaturesOut,
    RuntimeCapabilityOut,
)
from ..services import (
    agent_probe,
    ask_service,
    cost_service,
    numeric_claims,
    report_validator,
    run_diagnostics,
    search_index,
)
from ..services.market_data.registry import get_registry
from ..services.run_manager import MAX_CONCURRENT_RUNS

router = APIRouter(tags=["capabilities"])

logger = get_logger("capabilities")


def _grounding_available() -> bool:
    """`numeric_claims` 能导入、分类表能跑（不是"文件在"）。"""
    try:
        doc = {
            "asOf": "2026-02-10T00:00:00Z",
            "keyMetrics": [{"label": "最新价", "value": "1,672.00"}],
            "sections": [
                {"id": "thesis", "title": "核心逻辑", "blocks": [{"type": "paragraph", "text": "营收 12.3 亿"}]}
            ],
        }
        report = numeric_claims.grounding_report(doc, {"quote": {"last": 1672.0}})
        return report.fact_claims == 2
    except Exception:  # pragma: no cover - 只有模块损坏才会走到
        return False


def _range_query_available() -> bool:
    """报告 doc 能解析出 `asOf` → 区间反查能锚定相对期间。"""
    from datetime import date

    from ..services import block_dates

    try:
        return block_dates.parse_as_of("2026-02-10T01:00:00Z") == date(2026, 2, 10)
    except Exception:  # pragma: no cover
        return False


def _cost_ledger_available(db: Session) -> bool:
    """0003 已迁移：表真的存在（`inspect`，不是 try/except 一次查询）。"""
    try:
        return "cost_ledger" in inspect(db.get_bind()).get_table_names()
    except Exception:  # pragma: no cover
        return False


def _engine_payload(db: Session, settings, capability, fingerprint) -> EngineCapabilityOut:
    """★v7 §20.3.7：`capabilities.engine` = 指纹四项 + 六项新事实。

    **`model` / `provider` 永不进来**（H-19 / V6-P1-3）：它们的权威是
    `aragon.*` 与 `reports.model|provider`，第三份副本必然漂移。
    """
    base: dict[str, object] = dict(fingerprint.as_capability()) if fingerprint is not None else {}
    # ★v8 评审（B-5）：`measure()` 读不到文件时会抛 `schema_over_budget`（这是闸门该有的
    # 行为）。但 `/capabilities` 是**诊断**端点：一个坏掉的 schema 不该让它 500，
    # 那会把"为什么生成不了"的唯一入口也一起关掉。所以这里降级成 `schema: null`。
    try:
        budget = schema_budget.measure(
            settings.schemas_dir / "report_doc.schema.json",
            schema_budget.limits_from_capability(capability),
        )
    except AgentError as exc:
        logger.warning("capabilities: schema 度量失败（降级为 null）：%s", exc.message)
        budget = None
    cli_result = capability.result or {}
    base["posture"] = settings.aragon_agent_posture
    base["schemaSha1"] = report_validator.schema_sha1()
    base["schema"] = EngineSchemaOut.model_validate(budget.as_payload()) if budget is not None else None
    base["limits"] = EngineLimitsOut(
        retryMax=max(0, min(20, int(settings.aragon_retry_max))),
        repairTurns=clamp_repair_turns(settings.aragon_repair_turns, capability),
        maxOutputTokens=max(1, int(settings.aragon_max_output_tokens)),
        valueBytes=int(cli_result.get("valueBytes") or 262_144),
        schemaBytes=int(cli_result.get("schemaBytes") or schema_budget.FALLBACK_LIMITS["schemaBytes"]),
        # ★v9（K4/K2）：两个新数 + 一条只在 CLI 重试被打开时才出现的告警。
        agentAttempts=max(1, int(settings.agent_attempts)),
        probeTtlSec=max(0, int(settings.agent_probe_ttl_sec)),
        retryLayersWarning=(
            "cli_second_layer_uncontrolled" if int(settings.aragon_retry_max) > 0 else None
        ),
    )
    base["priceVersion"] = PRICE_VERSION
    failure = run_diagnostics.recent_failure(db)
    base["lastFailure"] = EngineLastFailureOut.model_validate(failure) if failure else None
    # ★v9（K2）：最近一次自检摘要（`engine.lastProbe`）。H-19 不变：model/provider 仍不进来。
    try:
        last_probe = agent_probe.last_probe(db)
    except Exception:  # pragma: no cover - 0005 未迁移时不能把诊断端点也弄挂
        last_probe = None
        logger.warning("capabilities: engine_probes 不可读（0005 未迁移？）")
    base["lastProbe"] = (
        {"ok": last_probe.ok, "ms": last_probe.ms, "at": last_probe.probed_at, "code": last_probe.code}
        if last_probe is not None
        else None
    )
    return EngineCapabilityOut.model_validate(base)


def _aragon_payload(db: Session, capability) -> dict[str, object]:
    """★v9（K2）：`aragon` 子对象的 wire 载荷。

    `verified/verifiedAt` 的**唯一**真源是 `engine_probes`（K2 落库的那张表），所以这里
    把 `agent_probe.verified()` 的结论写进载荷 —— `preflight.note_verified()` 只更新
    进程内快照，重启之后那份就没了，而表还在。
    """
    payload: dict[str, object] = dict(capability.as_payload())
    try:
        ok, verified_at, _stale = agent_probe.verified(db)
        payload["verified"] = ok
        payload["verifiedAt"] = verified_at
    except Exception:  # pragma: no cover - 0005 未迁移
        logger.warning("capabilities: 探针结论不可读，verified 保持进程内快照值")
    return payload


def _runtime_payload(settings) -> RuntimeCapabilityOut:
    """★v12（S1 / S7 / §22.5.1）：组装 `capabilities.runtime`。

    三个纪律：

    1. **不重新判定 `loopOk`**（C-33）。`spawn_capability()` 是唯一判据，`fullyCapable`、
       `degradedReason`、这里三处读的是同一个函数——所以 `runtime.loopOk == false`
       必然等价于 `aragon.degradedReason == "event_loop_unsupported"`（AC-112 断言的就是
       这个等价关系，而不是一个根本不在 wire 上的 `fullyCapable`）。
    2. `sessionSave` 由 `agent_attempts` **派生**，不开配置项（C-45）。
    3. `sessionDirMb` 是**只读测量**，超阈值只提示不删除（R-61）。
    """
    loop_ok, _loop_reason = spawn_capability()
    return RuntimeCapabilityOut(
        eventLoop=running_loop_name(),
        loopOk=loop_ok,
        loopReason=_loop_reason,
        hardDeadlineSec=int(
            float(settings.aragon_max_duration_ms) / 1000.0
            + max(0, int(settings.agent_hard_deadline_grace_sec))
        ),
        sessionSave="retry_only" if max(1, int(settings.agent_attempts)) > 1 else "off",
        sessionDirMb=session_dir_size_mb(),
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities(db: Session = Depends(get_db)) -> CapabilitiesResponse:
    settings = get_settings()
    capability = check_aragon()
    mode = decide_mode(settings, capability)
    registry = get_registry()
    providers = [DataProviderCapability(**item) for item in registry.available_providers()]

    pinyin_on = search_index.pinyin_available() and settings.search_pinyin != "off"
    fingerprint = current_fingerprint(schema_bytes=report_validator.schema_bytes())
    engine = _engine_payload(db, settings, capability, fingerprint)
    return CapabilitiesResponse(
        # ★v9（K2 / V10-P1-1）：`verified/verifiedAt` 由 `engine_probes` 派生；
        # 其余四个字段（`baseUrlHost/keyPresent/keySource`）来自 `as_payload()`。
        aragon=_aragon_payload(db, capability),  # type: ignore[arg-type]
        agentMode=mode,  # type: ignore[arg-type]
        dataProviders=providers,
        engine=engine,
        # ★v12（§22.5.1）：进程级事实（能不能 spawn / 硬截止 / 会话保存策略 / 会话目录体积）。
        runtime=_runtime_payload(settings),
        features=FeaturesOut(
            mock=True,
            exportHtml=True,
            exportMd=True,
            pdf=False,
            # `cite` never spawns a process, so it is available in every mode.
            ask=True,
            askDeep=ask_service.ask_deep_available(),
            fullTextSearch=bool(settings.search_fts) and search_index.fts_ready(db),
            pinyinSearch=pinyin_on,
            schedule=bool(settings.scheduler_enabled),
            offline=True,
            realtimeQuote=False,
            semanticSearch=False,
            grounding=_grounding_available(),
            rangeQuery=_range_query_available(),
            # 全文视图是纯前端容器，后端只需要确认渲染器可用（RSC 已在跑）。
            readingMode=True,
            costLedger=_cost_ledger_available(db),
            engineFingerprint=fingerprint is not None,
        ),
        limits={
            "maxConcurrentRuns": MAX_CONCURRENT_RUNS,
            "runRetentionDays": settings.run_retention_days,
            "maxReportCostUsd": settings.max_report_cost_usd,
            "requiredTools": settings.required_tools,
            "askDailyLimit": settings.ask_daily_limit,
            "askMaxQuestionChars": settings.ask_max_question_chars,
            "schedulerTickSeconds": settings.scheduler_tick_seconds,
            # ★v6 G3/G4：面板与徽标需要的四个数。
            "monthlyBudgetUsd": cost_service.budget_monthly_usd(db),
            "minFactClaims": numeric_claims.MIN_FACT_CLAIMS,
            "maxBlockRangeYears": 5,
            "maxBlockRangeDays": 1825,
            # ★v7（§20.5.1）：资料预算上限，界面用它解释 `prompt_over_budget`。
            "maxPromptBytes": settings.aragon_max_prompt_bytes,
        },
    )
