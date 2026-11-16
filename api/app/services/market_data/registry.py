"""Provider registry — ordered fallback with per-provider timeout (spec §9).

``DATA_PROVIDERS=akshare,yfinance,synthetic`` is tried left to right. Every
attempt is time-boxed, every failure is classified, and the winning source is
reported back on the result so the UI can say where the numbers came from.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from ...config import get_settings
from ...logging import get_logger
from .base import BarRow, BarsResult, FundamentalPeriod, ProviderError, ProviderErrorKind, QuoteRow, SymbolHit

logger = get_logger("market_data")

T = TypeVar("T")


@dataclass(slots=True)
class ChainResult(Generic[T]):
    value: T
    source: str
    warnings: list[str] = field(default_factory=list)


def _provider_classes() -> dict[str, Callable[[], Any]]:
    from .akshare_provider import AkshareProvider
    from .synthetic_provider import SyntheticProvider
    from .yfinance_provider import YFinanceProvider

    return {
        "akshare": AkshareProvider,
        "yfinance": YFinanceProvider,
        "synthetic": SyntheticProvider,
    }


class ProviderRegistry:
    def __init__(self, timeout: float | None = None) -> None:
        settings = get_settings()
        self.chain = settings.provider_chain or ["synthetic"]
        self.allow_synthetic = settings.allow_synthetic
        self.timeout = timeout or settings.provider_timeout_sec
        classes = _provider_classes()
        self._instances: dict[str, Any] = {}
        for name in self.chain:
            factory = classes.get(name)
            if factory is None:
                logger.warning("unknown provider in DATA_PROVIDERS: %s", name)
                continue
            self._instances[name] = factory(timeout=self.timeout) if name != "synthetic" else factory()

    # ── introspection ────────────────────────────────────────────────────
    def available_providers(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, instance in self._instances.items():
            try:
                ok = bool(instance.available())
            except Exception:  # pragma: no cover - defensive
                ok = False
            out.append({"id": name, "available": ok})
        return out

    # ── core fallback loop ───────────────────────────────────────────────
    async def _try_chain(
        self,
        method: str,
        *args: Any,
        symbol_hint: str = "",
        **kwargs: Any,
    ) -> ChainResult[Any]:
        warnings: list[str] = []
        for name, provider in self._instances.items():
            if name == "synthetic" and not self.allow_synthetic:
                warnings.append("synthetic 已被 ALLOW_SYNTHETIC=0 禁用")
                continue
            try:
                ok = await asyncio.wait_for(asyncio.to_thread(provider.available), timeout=2.0)
            except (TimeoutError, Exception):
                ok = False
            if not ok:
                warnings.append(f"{name}: 依赖不可用")
                continue
            try:
                call = getattr(provider, method)
                value = await asyncio.wait_for(asyncio.to_thread(call, *args, **kwargs), timeout=self.timeout)
            except TimeoutError:
                warnings.append(f"{name}: 超时（>{self.timeout:.0f}s）")
                logger.warning("provider %s %s timed out for %s", name, method, symbol_hint)
                continue
            except ProviderError as exc:
                warnings.append(f"{name}: {exc.kind.value} {str(exc)[:120]}")
                if exc.kind is ProviderErrorKind.notfound and name != "synthetic":
                    # A definitive "this symbol is not here" is not worth retrying
                    # with the same question elsewhere, but another provider may
                    # still cover the market — keep walking the chain.
                    logger.info("provider %s has no data for %s", name, symbol_hint)
                continue
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"{name}: {type(exc).__name__} {str(exc)[:120]}")
                logger.warning("provider %s %s failed: %s", name, method, exc)
                continue
            if value is None or (isinstance(value, list) and len(value) == 0):
                warnings.append(f"{name}: 空结果")
                continue
            return ChainResult(value=value, source=name, warnings=warnings)
        raise ProviderError(
            ProviderErrorKind.unavailable,
            "所有数据源都失败了: " + "; ".join(warnings) if warnings else "数据源链为空",
        )

    # ── public API ───────────────────────────────────────────────────────
    async def search(self, query: str, limit: int = 8) -> ChainResult[list[SymbolHit]]:
        return await self._try_chain("search", query, limit, symbol_hint=query)

    async def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> ChainResult[BarsResult]:
        result = await self._try_chain(
            "bars", symbol, timeframe=timeframe, start_ts=start_ts, adjust=adjust, symbol_hint=symbol
        )
        rows: list[BarRow] = result.value
        rows.sort(key=lambda row: row.ts)
        return ChainResult(
            value=BarsResult(bars=rows, source=result.source, warnings=result.warnings),
            source=result.source,
            warnings=result.warnings,
        )

    async def quote(self, symbol: str) -> ChainResult[QuoteRow]:
        return await self._try_chain("quote", symbol, symbol_hint=symbol)

    async def fundamentals(self, symbol: str, periods: int = 8) -> ChainResult[list[FundamentalPeriod]]:
        return await self._try_chain("fundamentals", symbol, periods, symbol_hint=symbol)


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_registry() -> None:
    """Test hook: forces the next call to rebuild the chain from settings."""
    global _registry
    _registry = None
