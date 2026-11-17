"""Cost ledger payloads — mirrors §19.5.1 (`GET /cost/summary`, `/cost/entries`).

`budget.state` has **four** members, not three (★V6-P2-5): the v5 table listed
`ok | near | exceeded` and then discussed `unlimited` in the same paragraph, so a
`monthlyUsd === 0` deployment had no legal value to report. `web/lib/types.ts`
mirrors the union verbatim and `web/tests/contract.test.ts` asserts it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BudgetState = Literal["ok", "near", "exceeded", "unlimited"]
CostWindow = Literal["7d", "30d", "90d", "mtd", "ytd"]
CostKind = Literal["run", "ask_deep"]


class CostBudgetOut(BaseModel):
    monthlyUsd: float = 0.0
    #: `null` when unlimited — never a fake infinity, never 0.
    remainingUsd: float | None = None
    resetAt: str | None = None
    state: BudgetState = "unlimited"


class CostModelRow(BaseModel):
    model: str
    usd: float = 0.0
    runs: int = 0


class CostSummary(BaseModel):
    window: CostWindow = "30d"
    from_: str = Field(alias="from")
    to: str
    #: Known spend only. Unknown runs are counted in `unknownRuns`, never as $0.00.
    spentUsd: float = 0.0
    #: ★v7（H-4）：按 token × 单价估算的部分，**单独一列**，绝不并进 `spentUsd`。
    estimatedUsd: float = 0.0
    #: ★v7：两数相加的合计，界面上的"含估算"（R-31：永远并排显示，不合并成一个数）。
    spentIncludingEstimatedUsd: float = 0.0
    knownRuns: int = 0
    #: ★v7：三态各计一次，互斥（`reportedRuns + estimatedRuns + unknownRuns == 行数`）。
    reportedRuns: int = 0
    estimatedRuns: int = 0
    unknownRuns: int = 0
    byKind: dict[str, float] = Field(default_factory=dict)
    byModel: list[CostModelRow] = Field(default_factory=list)
    budget: CostBudgetOut = Field(default_factory=CostBudgetOut)
    #: ★v7：估算用的价格表版本（错了改数据、不改代码）。
    priceVersion: str | None = None
    #: Ledger rows lost to a savepoint (the report still landed) — R-26.
    ledgerGap: int = 0

    model_config = {"populate_by_name": True}


class CostEntry(BaseModel):
    id: int
    kind: str
    refId: str
    symbol: str | None = None
    reportId: str | None = None
    model: str | None = None
    usd: float | None = None
    known: bool = True
    #: ★v7：估算值与来源三态（`usd` / `usd_est` 互斥，AC-86）。
    usdEst: float | None = None
    source: str = "unknown"
    createdAt: str


class CostEntriesResponse(BaseModel):
    items: list[CostEntry] = Field(default_factory=list)
    total: int = 0


class BudgetUpdate(BaseModel):
    """`POST /cost/budget` — `0` means unlimited (the default)."""

    monthlyUsd: float = Field(default=0.0, ge=0, le=10_000)


class EstimateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    kind: CostKind = "run"


class EstimateBasis(BaseModel):
    samples: int = 0
    medianTokens: int = 0
    model: str = ""
    priceVersion: str | None = None


class EstimateResponse(BaseModel):
    """`estimatedUsd: null` is a legitimate 200 — "无法预估" is an answer."""

    estimatedUsd: float | None = None
    reason: Literal["no_history", "no_price"] | None = None
    basis: EstimateBasis | None = None
