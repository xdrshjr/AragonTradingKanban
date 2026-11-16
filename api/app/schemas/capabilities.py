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

    ★v6 的五个新布尔**默认 `False`、由实测推导**（V6-P2-4）：照抄 `True` 默认值
    就是"入口出现了但点了没用"的老毛病。
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
    #: G1：`numeric_claims` 可导入且分类表可跑（数字溯源徽标是否可渲染）。
    grounding: bool = False
    #: G2a：报告 doc 能解析出 `asOf`（区间反查的前提）。
    rangeQuery: bool = False
    #: G2b：全文视图可用（前端 bundle 生效）。
    readingMode: bool = False
    #: G3：0003 已迁移、`cost_ledger` 可查。
    costLedger: bool = False
    #: G4：preflight 拿到了版本，能算出指纹。
    engineFingerprint: bool = False


class EngineCapabilityOut(BaseModel):
    """★v6（V6-P1-3）：`capabilities.engine` 是**新增对象**，只带四项。

    `model` / `provider` 留在 `aragon` 子对象（唯一真源），这里不复制。
    """

    fingerprint: str | None = None
    #: 版本落在 `>=0.6.66,<0.7` 之外 → 结构化能力未在本机回归，UI 给一行提示。
    drift: bool = False
    schemaBytes: int = 0
    allowTools: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    aragon: AragonAragonOut
    agentMode: Literal["real", "mock"]
    dataProviders: list[DataProviderCapability] = Field(default_factory=list)
    features: FeaturesOut = Field(default_factory=FeaturesOut)
    limits: dict[str, object] = Field(default_factory=dict)
    engine: EngineCapabilityOut = Field(default_factory=EngineCapabilityOut)
