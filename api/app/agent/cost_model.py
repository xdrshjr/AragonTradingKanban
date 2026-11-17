"""成本三态 —— `reported / estimated / unknown` 的**唯一**判定入口（spec §20.3.5，H4 / M21）。

问题重述（E-44 / E-49）：deepseek 这条路上 `cost.known=false` 是**常态**，不是例外。
于是 `runs.cost_usd` 恒为 NULL、`cost_ledger` 每行 `usd=NULL, known=0`、
`/cost/summary` 的 `spentUsd` 恒为 0，而预算闸门对着一个空账本**永远放行**——
它不是"没超"，是"永远不知道"。

与此同时我们手上有 55.3 万 input token / 13.5 万 output token 的**精确计数**，以及一份
公开的单价比。**不知道价格**与**装作不知道**是两件事：前者用一个可替换的数据表就能
解决，后者是设计上的放弃。所以这里引入第三种来源 `estimated`。

三条刻意的限制：

* **`estimated` 与 `reported` 永不同义**（H-6）：账本分列（`usd` / `usd_est`）、
  界面分措辞（"已确认成本" / "估算成本"）、汇总分两个数（`spentUsd` / `spentWithEst`）。
  把估算说成回执，账本就再也不可信。
* **唯一的判定函数**（O-13）：`runs` / `reports` / `cost_ledger` 三处都由
  `resolve_cost()` 派生。任何一处自己再算一遍都是一个新的漂移面。
* **`agent/` 不得 import `services/`**：所以换算住在这里（只依赖 `agent/pricing.py`
  与 `agent/runner_base.py`），`services/cost_service.py` 反过来 import 它。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .pricing import PRICE_VERSION, cost_for_tokens
from .runner_base import AgentResult

CostSource = Literal["reported", "estimated", "unknown"]

#: 三态取值域。`cost_ledger.source` 的白名单用它（`cost_service.record()` 判定）。
COST_SOURCES: tuple[str, ...] = ("reported", "estimated", "unknown")


@dataclass(frozen=True, slots=True)
class CostResolution:
    """一次调用的成本归属。`source` 是唯一真相，两个金额列**互斥**。"""

    source: CostSource
    #: `reported` 或 `estimated` 的最终值；`unknown` → `None`（不是 0）。
    usd: float | None
    reported_usd: float | None
    estimated_usd: float | None
    model: str | None
    #: 只有 `estimated` 才有意义（价格表版本，用来解释历史估算）。
    price_version: str | None

    @property
    def known(self) -> bool:
        """`cost_known` 列的取值：**只有** `reported` 算已知（AC-55 一字不改）。"""
        return self.source == "reported"

    def as_payload(self) -> dict[str, Any]:
        """`quality.cost` 与 `run.done` / `run.error` 的载荷形状。"""
        return {
            "source": self.source,
            "reportedUsd": self.reported_usd,
            "estimatedUsd": self.estimated_usd,
            "usd": self.usd,
            "priceVersion": self.price_version,
        }


def resolve_cost(result: AgentResult) -> CostResolution:
    """唯一判定入口（O-13）。优先级：**CLI 报的 > 自己算的 > 不知道**。

    绝不把 estimated 说成 reported。也绝不把"估算失败"退化成 0:
    `cost_for_tokens()` 在价格未知或 token 缺失时返回 `None`，于是落到 `unknown`。
    """
    if result.cost_known and result.cost_usd is not None:
        return CostResolution(
            source="reported",
            usd=result.cost_usd,
            reported_usd=result.cost_usd,
            estimated_usd=None,
            model=result.model,
            price_version=None,
        )
    estimated = cost_for_tokens(result.model, result.usage.input_tokens, result.usage.output_tokens)
    if estimated is not None:
        return CostResolution(
            source="estimated",
            usd=estimated,
            reported_usd=None,
            estimated_usd=estimated,
            model=result.model,
            price_version=PRICE_VERSION,
        )
    return CostResolution(
        source="unknown",
        usd=None,
        reported_usd=None,
        estimated_usd=None,
        model=result.model,
        price_version=None,
    )


__all__ = ["COST_SOURCES", "CostResolution", "CostSource", "resolve_cost"]
