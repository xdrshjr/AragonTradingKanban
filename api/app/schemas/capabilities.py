"""Capability payloads — mirrors §7.1 `GET /capabilities`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AragonAragonOut(BaseModel):
    available: bool = False
    version: str | None = None
    core: str | None = None
    versionOk: bool = False
    provider: str | None = None
    model: str | None = None
    hasApiKey: bool = False
    ajv: bool = False
    canStructured: bool = False
    hasSubmitResult: bool = False
    extraAllowTools: list[str] = Field(default_factory=list)
    resolvedBinary: str | None = None
    degradedReason: str | None = None
    errors: list[str] = Field(default_factory=list)


class DataProviderCapability(BaseModel):
    id: str
    available: bool


class FeaturesOut(BaseModel):
    """★V4-P2-4: an out-of-date example here is how the ask entry point never shipped.

    Every switch is filled from a *measurement*, never from a default:

    * `ask` — the `cite`档 never touches the CLI, so it is always available;
    * `askDeep` — needs `submit_result` + ajv, i.e. a genuinely capable engine;
    * `fullTextSearch` — FTS5 detected **and** the virtual tables built;
    * `pinyinSearch` — `pypinyin` importable;
    * `schedule` — `SCHEDULER_ENABLED`;
    * `offline` — the front end ships a service worker (production builds only).
    """

    mock: bool = True
    exportHtml: bool = True
    exportMd: bool = True
    pdf: bool = False
    ask: bool = True
    askDeep: bool = False
    fullTextSearch: bool = False
    pinyinSearch: bool = False
    schedule: bool = True
    offline: bool = True
    realtimeQuote: bool = False
    semanticSearch: bool = False


class CapabilitiesResponse(BaseModel):
    aragon: AragonAragonOut
    agentMode: Literal["real", "mock"]
    dataProviders: list[DataProviderCapability] = Field(default_factory=list)
    features: FeaturesOut = Field(default_factory=FeaturesOut)
    limits: dict[str, object] = Field(default_factory=dict)
