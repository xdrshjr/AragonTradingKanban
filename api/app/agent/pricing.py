"""Model price table — the only place a token count becomes dollars (§19.3.5).

`price_for()` returns **None** for an unknown model rather than a guess: the cost
panel must be able to say「无法预估」instead of inventing a number. Prices are per
1K tokens and are deliberately kept as data, not logic — a model rename is a data
edit, never a code change.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ★v7（H-20 / AC-89）：价格变了就要换版本号。既有 6 条价格**逐值不可变**——历史报告的
#: 估算要用当时的价解释，就地改价等于静默改写历史。
PRICE_VERSION = "2026-03"


@dataclass(slots=True, frozen=True)
class ModelPrice:
    input_per_1k: float
    output_per_1k: float


#: `main segment` → price. Lookups fall back to the longest matching prefix, so
#: `claude-sonnet-20260210` resolves through `claude-sonnet` (R-27: a date suffix
#: must not turn into "unknown model").
#:
#: ★v7：**只新增两条 deepseek 数据**（既有 6 条一字不动，AC-89 逐值断言）。
#: 来源：DeepSeek 公开价目表（api-docs.deepseek.com/quick_start/pricing，2026-03 查询，
#: 按 USD / 1K tokens 折算：deepseek-chat 输入 $0.27/M、输出 $1.10/M；
#: deepseek-reasoner 输入 $0.55/M、输出 $2.19/M；flash 档 $0.14/M、$0.28/M）。
#: 这三行是**数据，不是结论**：价格变了就改这张表 + 换 `PRICE_VERSION`，
#: 或者用 `ARAGON_PRICE_OVERRIDE` 在重启时改（§20.3.5）。
PRICES: dict[str, ModelPrice] = {
    "claude-sonnet": ModelPrice(input_per_1k=0.003, output_per_1k=0.015),
    "claude-opus": ModelPrice(input_per_1k=0.015, output_per_1k=0.075),
    "claude-haiku": ModelPrice(input_per_1k=0.0008, output_per_1k=0.004),
    "gpt-4o": ModelPrice(input_per_1k=0.0025, output_per_1k=0.01),
    "gpt-4o-mini": ModelPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    "deepseek-chat": ModelPrice(input_per_1k=0.0003, output_per_1k=0.0012),
    "deepseek-reasoner": ModelPrice(input_per_1k=0.00055, output_per_1k=0.00219),
    "deepseek-flash": ModelPrice(input_per_1k=0.00014, output_per_1k=0.00028),
}

#: 既有 6 条（AC-89 的基线）：改价必须换 `PRICE_VERSION` 并在注释里写生效日期。
BASELINE_PRICES_2026_02: dict[str, tuple[float, float]] = {
    "claude-sonnet": (0.003, 0.015),
    "claude-opus": (0.015, 0.075),
    "claude-haiku": (0.0008, 0.004),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "deepseek-chat": (0.0003, 0.0012),
}


def _override_table() -> dict[str, ModelPrice]:
    """`ARAGON_PRICE_OVERRIDE` 解析结果 → 价格条目。

    运维出口：`{"deepseek-flash":{"in":0.00014,"out":0.00028}}`。
    读 `get_settings()` 的派生属性（内部已做坏 JSON 兜底），这里只负责形状。
    """
    from ..config import get_settings

    try:
        raw = get_settings().price_overrides
    except Exception:  # pragma: no cover - 配置读不出来时不该让估算炸掉
        return {}
    table: dict[str, ModelPrice] = {}
    for model, price in raw.items():
        base = PRICES.get(model)
        table[model] = ModelPrice(
            input_per_1k=float(price.get("in", base.input_per_1k if base else 0.0)),
            output_per_1k=float(price.get("out", base.output_per_1k if base else 0.0)),
        )
    return table


def model_family(model: str | None) -> str | None:
    """`claude-sonnet-20260210` → `claude-sonnet`（★R-27：截到主版本段）。"""
    if not model:
        return None
    lowered = model.strip().lower()
    if not lowered:
        return None
    parts = lowered.split("-")
    if len(parts) >= 2 and parts[-1].isdigit():
        parts = parts[:-1]
    # `claude-sonnet` 是两段；再短就只剩厂商名，认不出型号。
    return "-".join(parts[:2]) if len(parts) >= 2 else parts[0]


def price_for(model: str | None) -> ModelPrice | None:
    """Known model → price; unknown → `None`（**永不**拿一个默认价冒充）。

    ★v7：先看 `ARAGON_PRICE_OVERRIDE`（运维出口，重启即生效），再查静态表。
    """
    if not model:
        return None
    lowered = model.strip().lower()
    if not lowered:
        return None
    overrides = _override_table()
    if lowered in overrides:
        return overrides[lowered]
    if lowered in PRICES:
        return PRICES[lowered]
    family = model_family(lowered)
    if family and family in overrides:
        return overrides[family]
    if family and family in PRICES:
        return PRICES[family]
    # 前缀匹配取最长者，避免 `gpt-4o-mini` 命中 `gpt-4o`。覆盖表与静态表同权。
    table = {**PRICES, **overrides}
    candidates = [key for key in table if lowered.startswith(key)]
    if candidates:
        return table[max(candidates, key=len)]
    return None


def cost_for_tokens(model: str | None, tokens_in: int | None, tokens_out: int | None) -> float | None:
    """`(model, in, out)` → USD；价格未知或 token 缺失 → None（未知就是未知）。"""
    price = price_for(model)
    if price is None:
        return None
    known_in = tokens_in or 0
    known_out = tokens_out or 0
    if known_in <= 0 and known_out <= 0:
        return None
    return round(known_in / 1000 * price.input_per_1k + known_out / 1000 * price.output_per_1k, 4)


__all__ = [
    "BASELINE_PRICES_2026_02",
    "PRICE_VERSION",
    "PRICES",
    "ModelPrice",
    "cost_for_tokens",
    "model_family",
    "price_for",
]
