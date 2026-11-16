"""Runner factory — the `AGENT_MODE` decision, made once and explained (§5.4, P1-6).

`auto` does not guess: it asks `preflight` and obeys the answer. `real` refuses
to silently degrade — if the capability is missing the caller gets a 503 with
`structured_result_unsupported` rather than a mystery. `mock` never spawns
anything, which is what makes the offline demo and the E2E suite honest.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from ..logging import get_logger
from .aragon_runner import AragonRunner
from .mock_runner import MockRunner
from .preflight import AragonCapability, check_aragon
from .runner_base import AgentError, AgentRunner

logger = get_logger("agent")


def decide_mode(settings: Settings | None = None, capability: AragonCapability | None = None) -> str:
    """Return the mode actually in force: ``real`` or ``mock``."""
    settings = settings or get_settings()
    if settings.agent_mode == "mock":
        return "mock"
    capability = capability or check_aragon()
    if settings.agent_mode == "real":
        return "real"
    # auto: real only when preflight says every gate passes (§5.4 table).
    return "real" if capability.fullyCapable else "mock"


def build_runner(settings: Settings | None = None, *, strict: bool | None = None) -> AgentRunner:
    """Build the runner for the current mode.

    ``strict=True`` (used by `run_manager` when it must honour `AGENT_MODE=real`)
    raises instead of falling back to mock.
    """
    settings = settings or get_settings()

    if settings.agent_mode == "mock":
        logger.info("agent mode=mock (forced by AGENT_MODE)")
        return MockRunner()

    # Only now is it worth paying for `aragon info --json`: `mock` must never
    # spawn anything (§5.4 / AC-15), and that is the one promise this file makes.
    capability = check_aragon()

    if settings.agent_mode == "real":
        if not capability.fullyCapable:
            raise AgentError(
                capability.degradedReason or "structured_result_unsupported",
                "AGENT_MODE=real 但生成引擎能力不完整：" + "; ".join(capability.errors[:3]),
            )
        return AragonRunner(capability)

    # auto
    if capability.fullyCapable:
        logger.info("agent mode=real (preflight passed, cli=%s)", capability.cli)
        return AragonRunner(capability)

    reason = capability.degradedReason or "unknown"
    if strict:
        raise AgentError(
            "structured_result_unsupported" if reason != "aragon_unavailable" else "aragon_unavailable",
            f"生成引擎不可用（{reason}）：" + "; ".join(capability.errors[:3]),
        )
    logger.info("agent mode=mock (preflight degraded: %s)", reason)
    return MockRunner()


def describe(settings: Settings | None = None) -> tuple[str, AragonCapability]:
    settings = settings or get_settings()
    capability = check_aragon()
    return decide_mode(settings, capability), capability
