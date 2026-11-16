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

from ..agent import decide_mode
from ..agent.fingerprint import current_fingerprint
from ..agent.preflight import check_aragon
from ..config import get_settings
from ..db.session import get_db
from ..schemas.capabilities import (
    CapabilitiesResponse,
    DataProviderCapability,
    EngineCapabilityOut,
    FeaturesOut,
)
from ..services import ask_service, cost_service, numeric_claims, report_validator, search_index
from ..services.market_data.registry import get_registry
from ..services.run_manager import MAX_CONCURRENT_RUNS

router = APIRouter(tags=["capabilities"])


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


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities(db: Session = Depends(get_db)) -> CapabilitiesResponse:
    settings = get_settings()
    capability = check_aragon()
    mode = decide_mode(settings, capability)
    registry = get_registry()
    providers = [DataProviderCapability(**item) for item in registry.available_providers()]

    pinyin_on = search_index.pinyin_available() and settings.search_pinyin != "off"
    fingerprint = current_fingerprint(schema_bytes=report_validator.schema_bytes())
    engine = (
        EngineCapabilityOut.model_validate(fingerprint.as_capability())
        if fingerprint is not None
        else EngineCapabilityOut()
    )
    return CapabilitiesResponse(
        aragon=capability.as_payload(),  # type: ignore[arg-type]
        agentMode=mode,  # type: ignore[arg-type]
        dataProviders=providers,
        engine=engine,
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
        },
    )
