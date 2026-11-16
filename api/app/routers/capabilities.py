"""`/capabilities` — what the front end needs to decide on a degraded banner (P1-6)."""

from __future__ import annotations

from fastapi import APIRouter

from ..agent import decide_mode
from ..agent.preflight import check_aragon
from ..config import get_settings
from ..schemas.capabilities import CapabilitiesResponse, DataProviderCapability, FeaturesOut
from ..services.market_data.registry import get_registry

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities() -> CapabilitiesResponse:
    settings = get_settings()
    capability = check_aragon()
    mode = decide_mode(settings, capability)
    registry = get_registry()
    providers = [DataProviderCapability(**item) for item in registry.available_providers()]
    return CapabilitiesResponse(
        aragon=capability.as_payload(),  # type: ignore[arg-type]
        agentMode=mode,  # type: ignore[arg-type]
        dataProviders=providers,
        features=FeaturesOut(
            mock=True,
            exportHtml=True,
            exportMd=True,
            pdf=False,
            ask=False,
            realtimeQuote=False,
            semanticSearch=False,
        ),
        limits={
            "maxConcurrentRuns": 2,
            "runRetentionDays": settings.run_retention_days,
            "maxReportCostUsd": settings.max_report_cost_usd,
            "requiredTools": settings.required_tools,
        },
    )
