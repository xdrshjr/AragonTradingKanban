"""`/capabilities` — what the front end needs to decide on a degraded banner (P1-6).

Every feature flag is a **measurement** (§7.1 / V4-P2-4): the ask entry point, the
search box and the schedule dialog appear or disappear based on what actually works
here, not on what the design hoped worked.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..agent import decide_mode
from ..agent.preflight import check_aragon
from ..config import get_settings
from ..db.session import get_db
from ..schemas.capabilities import CapabilitiesResponse, DataProviderCapability, FeaturesOut
from ..services import ask_service, search_index
from ..services.market_data.registry import get_registry
from ..services.run_manager import MAX_CONCURRENT_RUNS

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities(db: Session = Depends(get_db)) -> CapabilitiesResponse:
    settings = get_settings()
    capability = check_aragon()
    mode = decide_mode(settings, capability)
    registry = get_registry()
    providers = [DataProviderCapability(**item) for item in registry.available_providers()]

    pinyin_on = search_index.pinyin_available() and settings.search_pinyin != "off"
    return CapabilitiesResponse(
        aragon=capability.as_payload(),  # type: ignore[arg-type]
        agentMode=mode,  # type: ignore[arg-type]
        dataProviders=providers,
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
        ),
        limits={
            "maxConcurrentRuns": MAX_CONCURRENT_RUNS,
            "runRetentionDays": settings.run_retention_days,
            "maxReportCostUsd": settings.max_report_cost_usd,
            "requiredTools": settings.required_tools,
            "askDailyLimit": settings.ask_daily_limit,
            "askMaxQuestionChars": settings.ask_max_question_chars,
            "schedulerTickSeconds": settings.scheduler_tick_seconds,
        },
    )
