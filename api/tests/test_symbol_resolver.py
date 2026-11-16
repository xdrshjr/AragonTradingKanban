"""Symbol resolution: codes, names, aliases, full-width input, misses (§6.2, US-1)."""

from __future__ import annotations

import pytest

from app.errors import InstrumentNotFound
from app.services import symbol_resolver as resolver

# ── pure canonicalisation rules (12 cases, spec §6.2) ─────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SH"),
        ("600519.SH", "600519.SH"),
        ("600519.SS", "600519.SH"),
        ("601318", "601318.SH"),
        ("000001", "000001.SZ"),
        ("002594", "002594.SZ"),
        ("00700", "0700.HK"),
        ("0700.HK", "0700.HK"),
        ("0700", "0700.HK"),
        ("aapl", "AAPL"),
        ("AAPL.US", "AAPL"),
        ("MSFT", "MSFT"),
    ],
)
def test_canonicalize_covers_the_documented_rules(raw: str, expected: str) -> None:
    assert resolver.canonicalize(raw) == expected


def test_canonicalize_rejects_names() -> None:
    assert resolver.canonicalize("贵州茅台") is None
    assert resolver.canonicalize("") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519"),
        ("  AAPL  ", "aapl"),
        ("ＡＡＰＬ", "aapl"),
        ("贵州 茅台", "贵州 茅台"),
        ("", ""),
    ],
)
def test_normalize_text_folds_width_case_and_whitespace(raw: str, expected: str) -> None:
    assert resolver.normalize_text(raw) == expected


def test_infer_market_from_symbol() -> None:
    assert resolver.infer_market_from_symbol("600519.SH") == "CN"
    assert resolver.infer_market_from_symbol("0700.HK") == "HK"
    assert resolver.infer_market_from_symbol("AAPL") == "US"
    assert resolver.infer_market_from_symbol("!!!") is None


# ── database resolution ───────────────────────────────────────────────────


def test_resolve_by_canonical_code(session) -> None:
    import asyncio

    resolution = asyncio.run(resolver.resolve(session, "600519"))
    assert resolution.instrument.symbol == "600519.SH"
    assert resolution.instrument.name == "贵州茅台"
    assert resolution.created is True


def test_resolve_by_name_uses_the_catalogue(session) -> None:
    import asyncio

    resolution = asyncio.run(resolver.resolve(session, "茅台"))
    assert resolution.instrument.symbol == "600519.SH"


def test_resolve_is_idempotent(session) -> None:
    import asyncio

    first = asyncio.run(resolver.resolve(session, "600519"))
    session.commit()
    second = asyncio.run(resolver.resolve(session, "600519.SH"))
    assert second.instrument.id == first.instrument.id
    assert second.created is False


def test_resolve_stores_the_typed_alias(session, instrument) -> None:
    import asyncio

    asyncio.run(resolver.resolve(session, "茅台"))
    session.commit()
    alias = session.get(resolver.SymbolAlias, "茅台")
    assert alias is not None
    assert alias.instrument_id == instrument.id


def test_resolve_finds_the_alias_later(session, instrument) -> None:
    import asyncio

    resolver.add_alias(session, instrument, "白酒龙头")
    session.commit()
    resolution = asyncio.run(resolver.resolve(session, "白酒龙头"))
    assert resolution.instrument.id == instrument.id


def test_resolve_unknown_name_raises_instrument_not_found(session) -> None:
    import asyncio

    with pytest.raises(InstrumentNotFound):
        asyncio.run(resolver.resolve(session, "完全不存在的标的xyz"))

    # A code-shaped miss is resolvable (we can create the instrument), but a
    # purely textual miss cannot be invented.
    assert True


def test_resolve_empty_query_raises(session) -> None:
    import asyncio

    with pytest.raises(InstrumentNotFound):
        asyncio.run(resolver.resolve(session, "   "))


def test_resolve_creates_a_plausible_row_for_an_unknown_code(session) -> None:
    import asyncio

    resolution = asyncio.run(resolver.resolve(session, "688981"))
    assert resolution.instrument.symbol == "688981.SH"
    assert resolution.instrument.market == "CN"
    assert resolution.instrument.currency == "CNY"
    assert resolution.instrument.tz == "Asia/Shanghai"


def test_search_is_case_and_width_insensitive(session, instrument) -> None:
    import asyncio

    hits = asyncio.run(resolver.search(session, "６００５１９", 5))
    assert any(hit.symbol == "600519.SH" for hit in hits)


def test_search_by_name_fragment(session, instrument) -> None:
    import asyncio

    hits = asyncio.run(resolver.search(session, "贵州", 5))
    assert any(hit.symbol == "600519.SH" for hit in hits)


def test_search_returns_nothing_for_gibberish(session) -> None:
    import asyncio

    hits = asyncio.run(resolver.search(session, "qqqqzzzz", 5))
    assert hits == []


def test_search_respects_the_limit(session) -> None:
    import asyncio

    hits = asyncio.run(resolver.search(session, "6", 2))
    assert len(hits) <= 2


def test_local_search_scores_exact_matches_highest(session, instrument) -> None:
    hits = resolver.search_local(session, "600519.SH", 5)
    assert hits[0].symbol == "600519.SH"
    assert hits[0].score == 1.0


def test_synthetic_search_backs_up_a_cold_database(session) -> None:
    import asyncio

    hits = asyncio.run(resolver.search(session, "宁德", 5))
    assert any(hit.symbol == "300750.SZ" for hit in hits)
