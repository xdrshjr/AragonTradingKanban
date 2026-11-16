"""Model price table — the only place a token count becomes dollars (§19.3.5).

`price_for()` returns **None** for an unknown model rather than a guess: the cost
panel must be able to say「无法预估」instead of inventing a number. Prices are per
1K tokens and are deliberately kept as data, not logic — a model rename is a data
edit, never a code change.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICE_VERSION = "2026-02"


@dataclass(slots=True, frozen=True)
class ModelPrice:
    input_per_1k: float
    output_per_1k: float


#: `main segment` → price. Lookups fall back to the longest matching prefix, so
#: `claude-sonnet-20260210` resolves through `claude-sonnet` (R-27: a date suffix
#: must not turn into "unknown model").
PRICES: dict[str, ModelPrice] = {
    "claude-sonnet": ModelPrice(input_per_1k=0.003, output_per_1k=0.015),
    "claude-opus": ModelPrice(input_per_1k=0.015, output_per_1k=0.075),
    "claude-haiku": ModelPrice(input_per_1k=0.0008, output_per_1k=0.004),
    "gpt-4o": ModelPrice(input_per_1k=0.0025, output_per_1k=0.01),
    "gpt-4o-mini": ModelPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    "deepseek-chat": ModelPrice(input_per_1k=0.0003, output_per_1k=0.0012),
}


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
    """Known model → price; unknown → `None`（**永不**拿一个默认价冒充）。"""
    if not model:
        return None
    lowered = model.strip().lower()
    if not lowered:
        return None
    if lowered in PRICES:
        return PRICES[lowered]
    family = model_family(lowered)
    if family and family in PRICES:
        return PRICES[family]
    # 前缀匹配取最长者，避免 `gpt-4o-mini` 命中 `gpt-4o`。
    candidates = [key for key in PRICES if lowered.startswith(key)]
    if candidates:
        return PRICES[max(candidates, key=len)]
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


__all__ = ["PRICE_VERSION", "PRICES", "ModelPrice", "cost_for_tokens", "model_family", "price_for"]
