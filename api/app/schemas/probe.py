"""`/agent/probe` 的响应模型（★v9 K2 / §21.5.1）。

两个都是**只增不改**：`ProbeOut` 的键名与 spec §21.5.1 的样例逐字一致，
`ProbeListOut` 是 `GET` 侧的 `{last, verified, verifiedAt, staleReason}`。

★评审修订（I-100）：这里原本还有第三个模型 `CapabilitiesAragonEndpointOut`（"密钥抽屉
要的四个事实"），但它**没有任何生产者也没有任何消费者** —— 抽屉读的是
`capabilities.aragon` 的 `baseUrlHost / keySource / verified`（同一份真源）。留着一个
没人用的模型，下一个人会以为有第二个端点，或者改字段时改错地方。删掉，语义由
`AragonAragonOut` 单独承担（§21.4 的那一行随之过期，见 spec 的 I-100）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProbeEngineOut(BaseModel):
    """`{provider, model, baseUrlHost}` —— 只用观测到的事实（不猜）。"""

    provider: str | None = None
    model: str | None = None
    baseUrlHost: str | None = None


class ProbeOut(BaseModel):
    ok: bool
    #: 失败时的 run 级码（与 K3 同一套码表）+ `probe_echo_mismatch` / `busy` /
    #: `probe_in_progress`；成功为 `null`。
    code: str | None = None
    #: 已打码（`failure.mask_secrets()`）。
    message: str | None = None
    ms: int = 0
    turns: int | None = None
    tokensIn: int | None = None
    tokensOut: int | None = None
    structureAttempts: int | None = None
    engine: ProbeEngineOut = Field(default_factory=ProbeEngineOut)
    cli: str | None = None
    probedAt: str | None = None


class ProbeListOut(BaseModel):
    last: ProbeOut | None = None
    verified: bool = False
    verifiedAt: str | None = None
    #: `never | expired | identity_changed | failed`；已验证时为 `null`。
    staleReason: Literal["never", "expired", "identity_changed", "failed"] | None = None
    #: ★v10（V10-P2-2）：这条提示数优先用**上一条探针实测的 tokens**，没有历史探针时
    #: 才回退常量（≈$0.0011）。"点一次自检多贵"是用户敢不敢点它的唯一依据。
    probeCostHintUsd: float | None = None
    probeTtlSec: int = 0


class ProbeRequest(BaseModel):
    """`POST` body：`force=true` 跳过 TTL 缓存。"""

    force: bool = False
