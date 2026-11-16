"""Capability preflight — the guard against P0-1 and its future recurrences (§5.4, R-12).

`aragon info --json` is the only source of truth here. The five checks below
decide, deterministically, whether a real structured generation can possibly
succeed. If any of them fails, `AGENT_MODE=auto` falls back to mock and
`/capabilities` says exactly why — we never "try and see", because the failure
mode (exit 3 with `structured: null` after two wasted minutes) is silent.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging import get_logger

logger = get_logger("preflight")

#: The `--result-schema` + `submit_result` combination landed in 0.6.66.
MIN_CLI_VERSION = (0, 6, 66)
MAX_CLI_EXCLUSIVE = (0, 7, 0)

CACHE_TTL_SEC = 60.0


@dataclass(slots=True)
class AragonCapability:
    available: bool = False
    cli: str | None = None
    core: str | None = None
    versionOk: bool = False
    provider: str | None = None
    model: str | None = None
    hasApiKey: bool = False
    ajv: bool = False
    canStructured: bool = False
    hasSubmitResult: bool = False
    extraAllowTools: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    resolvedBinary: str | None = None
    errors: list[str] = field(default_factory=list)
    checkedAt: float = 0.0

    @property
    def fullyCapable(self) -> bool:
        """Every gate the spec requires before we dare spawn a real generation."""
        return self.available and self.versionOk and self.canStructured and self.hasSubmitResult and self.hasApiKey

    @property
    def degradedReason(self) -> str | None:
        if self.fullyCapable:
            return None
        if not self.available:
            return "aragon_unavailable"
        if not self.canStructured:
            return "structured_result_unsupported"
        if not self.hasSubmitResult:
            return "submit_result_missing"
        if not self.hasApiKey:
            return "no_api_key"
        if not self.versionOk:
            return "version_out_of_range"
        return "unknown"

    def as_payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "version": self.cli,
            "core": self.core,
            "versionOk": self.versionOk,
            "provider": self.provider,
            "model": self.model,
            "hasApiKey": self.hasApiKey,
            "ajv": self.ajv,
            "canStructured": self.canStructured,
            "hasSubmitResult": self.hasSubmitResult,
            "extraAllowTools": self.extraAllowTools,
            "resolvedBinary": self.resolvedBinary,
            "degradedReason": self.degradedReason,
            "errors": self.errors,
        }


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """`0.6.68` → `(0, 6, 68)`. Only the leading digit run of each part counts,
    so a suffix can never invent a version: `0.6.65-hotfix1` stays `(0, 6, 65)`
    and is correctly rejected as older than the 0.6.66 floor."""
    if not value:
        return None
    numbers: list[int] = []
    for part in value.strip().lstrip("v").split(".")[:3]:
        match = re.match(r"\d+", part)
        numbers.append(int(match.group(0)) if match else 0)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def version_ok(value: str | None) -> bool:
    parsed = parse_version(value)
    if parsed is None:
        return False
    return MIN_CLI_VERSION <= parsed < MAX_CLI_EXCLUSIVE


def resolve_binary() -> str | None:
    settings = get_settings()
    if settings.aragon_bin:
        candidate = Path(settings.aragon_bin)
        if candidate.is_file():
            return str(candidate)
        logger.warning("ARAGON_BIN points at a missing file: %s", settings.aragon_bin)
        return None

    return shutil.which("aragon")


def _run_info(binary: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        completed = subprocess.run(  # noqa: S603 - binary comes from config/which
            [binary, "info", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"spawn failed: {exc}"
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return None, f"empty stdout (rc={completed.returncode}) {completed.stderr[:200]}"
    start = stdout.find("{")
    if start < 0:
        return None, f"no JSON in stdout: {stdout[:200]}"
    try:
        return json.loads(stdout[start:]), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def check_aragon(force: bool = False) -> AragonCapability:
    """Run the five preflight gates against `aragon info --json` (60s cache)."""
    settings = get_settings()
    cached = _cache[0]
    if (
        cached is not None
        and not force
        and not settings.force_preflight
        and time.monotonic() - cached.checkedAt < CACHE_TTL_SEC
    ):
        return cached

    capability = AragonCapability(checkedAt=time.monotonic())
    binary = resolve_binary()
    capability.resolvedBinary = binary
    if not binary:
        capability.errors.append("aragon not found on PATH (set ARAGON_BIN or run mock mode)")
        _cache[0] = capability
        return capability

    info, error = _run_info(binary)
    if info is None:
        capability.errors.append(error or "aragon info --json failed")
        _cache[0] = capability
        return capability

    capability.available = True
    capability.cli = info.get("cli")
    capability.core = info.get("core")
    capability.provider = info.get("provider")
    capability.model = info.get("model")
    capability.hasApiKey = bool(info.get("hasApiKey"))
    capability.tools = list(info.get("tools") or [])
    capability.permissions = list(info.get("permissionModes") or [])
    result = info.get("result") or {}
    capability.result = result
    capability.ajv = result.get("ajv") is True

    features = info.get("features") or []
    capability.canStructured = "structured_result" in features and capability.ajv
    # ★P0-1: without submit_result the model has NO channel to satisfy
    # --result-schema. Treat its absence as a hard capability failure.
    capability.hasSubmitResult = "submit_result" in capability.tools
    capability.versionOk = version_ok(capability.cli)

    required = settings.required_tools
    available_tools = set(capability.tools)
    capability.extraAllowTools = [tool for tool in required if tool in available_tools]
    missing = [tool for tool in required if tool not in available_tools]
    if missing:
        capability.errors.append(f"missing required tools: {', '.join(missing)}")
        if "submit_result" in missing:
            capability.hasSubmitResult = False

    if not capability.versionOk:
        capability.errors.append(f"CLI {capability.cli} outside the supported range >=0.6.66,<0.7")
    if not capability.canStructured:
        capability.errors.append("structured_result feature or ajv validation unavailable")
    if not capability.hasApiKey:
        capability.errors.append("no model API key configured for the CLI")

    logger.info(
        "preflight available=%s cli=%s capable=%s reason=%s",
        capability.available,
        capability.cli,
        capability.fullyCapable,
        capability.degradedReason,
    )
    _cache[0] = capability
    return capability


_cache: list[AragonCapability | None] = [None]


def reset_cache() -> None:
    """Test hook — forces the next `check_aragon` to spawn `aragon info` again."""
    _cache[0] = None
