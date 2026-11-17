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
    #: ★v7（V8-P1-2）：引擎"是谁"的第四个事实。来自 `exec` 的 `system` 事件
    #: `model.baseUrl`（不在 `aragon info --json` 里），没有就为空。
    baseUrl: str | None = None
    #: ★v7：两项只 warn 不降级的自检结果（`doctor` 摘要 / 模型是否在名录里）。
    doctor: str | None = None
    modelKnown: bool | None = None
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


class EngineSchemaOut(BaseModel):
    """★v7（AC-70）：当前 schema 的实测预算与**深度余量**。

    `headroom = maxDepth - depth`，当前 = 1。这个数是本轮最该被看见的一个数字：
    任何一次"给块再加一层嵌套"都会把它变成 0，而 CLI 的下一次生成会以 exit 2
    `result_schema_too_deep` 结束（历史 run `06456fcb`）。
    """

    bytes: int = 0
    depth: int = 0
    nodes: int = 0
    maxBytes: int = 0
    maxDepth: int = 0
    maxNodes: int = 0
    headroom: int = 0
    ok: bool = True


class EngineLimitsOut(BaseModel):
    """★v7：这次 spawn 的四个显式预算 + CLI 的值上限。**只增不改**。"""

    retryMax: int = 0
    repairTurns: int = 0
    maxOutputTokens: int = 0
    valueBytes: int = 0
    schemaBytes: int = 0


class EngineLastFailureOut(BaseModel):
    """★v7（H3.5）：最近一次失败。**只读、不据此降级**（H-15）。"""

    code: str
    hint: str = ""
    at: str | None = None
    #: 同一指纹在同一标的最近 N 条 run 里出现的次数（`>= 3` 才换成更强措辞）。
    count: int = 0
    #: ★v8：服务端判定好的"该换更强措辞了吗"——前端不再自己拍一个阈值。
    alert: bool = False


class EngineCapabilityOut(BaseModel):
    """★v6（V6-P1-3）：`capabilities.engine` 是**新增对象**，只带四项。

    `model` / `provider` 留在 `aragon` 子对象（唯一真源），这里不复制（H-19）。

    ★v7 增 5 项：`posture / schemaSha1 / schema / limits / priceVersion / lastFailure`。
    `drift` 保持 **`bool`**（`web/lib/types.ts` 与这里都是 boolean）：v7 草案样例里的
    `"drift": null` 会让实现者去改两个类型定义、或直接 500；"指纹缺失/两侧不可比"
    这件事由 `comparability.same` 表达，不是 `drift`（V8-P2-1）。
    """

    fingerprint: str | None = None
    #: 版本落在 `>=0.6.66,<0.7` 之外 → 结构化能力未在本机回归，UI 给一行提示。
    drift: bool = False
    schemaBytes: int = 0
    allowTools: list[str] = Field(default_factory=list)
    #: ★v7：当次体位（`lean` / `full`）。出问题时能追到"是不是体位导致的"（R-32）。
    posture: str = "lean"
    #: ★v7：**当次交给 CLI 的那份** schema 的 sha1（`workspace/schema.json`）。
    schemaSha1: str | None = None
    #: ★v7：当前 schema 的字节/深度/节点/余量。wire 名是 `schema`（字段名带下划线
    #: 只是为了避免遮蔽 `BaseModel.schema`，序列化按 alias 输出，形状与 §20.5.1 一致）。
    schema_: EngineSchemaOut | None = Field(default=None, alias="schema")
    limits: EngineLimitsOut = Field(default_factory=EngineLimitsOut)
    #: ★v7：估算用的价格表版本（`estimated` 才有意义）。
    priceVersion: str | None = None
    #: ★v7：最近一次失败；从未失败 → `null`。
    lastFailure: EngineLastFailureOut | None = None

    model_config = {"populate_by_name": True}


class CapabilitiesResponse(BaseModel):
    aragon: AragonAragonOut
    agentMode: Literal["real", "mock"]
    dataProviders: list[DataProviderCapability] = Field(default_factory=list)
    features: FeaturesOut = Field(default_factory=FeaturesOut)
    limits: dict[str, object] = Field(default_factory=dict)
    engine: EngineCapabilityOut = Field(default_factory=EngineCapabilityOut)
