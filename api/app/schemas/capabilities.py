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
    mock: bool = True
    exportHtml: bool = True
    exportMd: bool = True
    pdf: bool = False
    ask: bool = False
    realtimeQuote: bool = False
    semanticSearch: bool = False


class CapabilitiesResponse(BaseModel):
    aragon: AragonAragonOut
    agentMode: Literal["real", "mock"]
    dataProviders: list[DataProviderCapability] = Field(default_factory=list)
    features: FeaturesOut = Field(default_factory=FeaturesOut)
    limits: dict[str, object] = Field(default_factory=dict)
