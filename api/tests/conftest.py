"""Test fixtures: a throwaway SQLite database, a fake runner, and a fixed clock.

★P1-2 / R-14: `create_all` is allowed **only** here — the application itself is
migrated by Alembic and never creates tables.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

# Environment must be set before `app.config` is imported anywhere.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="aragon-test-"))
_DB_PATH = _TMP_DIR / "test.db"
_WORKSPACE = _TMP_DIR / "workspace"

# Assigned, not `setdefault`: the ambient shell may already export
# `AGENT_MODE=auto` (the `.env` default), and `setdefault` would then let the suite
# spawn a *paid* `aragon exec`. Tests must be deterministic and offline (AC-15);
# the tests that need a real/fake CLI build their own runner explicitly.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"
os.environ["WORKSPACE_ROOT"] = str(_WORKSPACE)
os.environ["AGENT_MODE"] = "mock"
os.environ["DATA_PROVIDERS"] = "synthetic"
os.environ["ALLOW_SYNTHETIC"] = "1"
os.environ["API_TOKEN"] = ""
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["SCHEDULER_ENABLED"] = "0"  # the ticker is driven explicitly in tests
# ★v7（I-61）：`aragon doctor` + `aragon models` 实测合计 11.1s。整套测试每次 preflight
# 都付这笔账毫无意义（结论十分钟内不会变），所以默认关掉；需要断言这两项的测试自己打开。
os.environ["ARAGON_SELF_CHECK"] = "0"
# ★v9（K4）：runner 层重试在测试里默认关掉（`AGENT_ATTEMPTS=1`）。
# 两个理由：①一次重试带 1.5s+ 的退避，会把整套测试拖慢；②既有 AC-74 断言的是
# "CLI 的 8 条 retry 事件一条不丢"，而 runner 重试会让它变成 16 条 —— 那条用例
# 测的是 CLI 词汇的转发，不该被本服务自己的重试改写。需要重试语义的用例
# （AC-100 / AC-101）自己把它 monkeypatch 回 2。
os.environ["AGENT_ATTEMPTS"] = "1"
# 引擎自检在测试里**永不联网**：需要探针的用例显式注入假引擎（tests/test_agent_probe.py）。
os.environ["AGENT_PROBE_ENABLED"] = "1"

from app.db.base import Base  # noqa: E402
from app.db.session import build_engine  # noqa: E402
from app.models import Instrument  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fake_aragon_bin() -> Path:
    """An executable launcher for `fake_aragon.py` on this platform.

    Windows refuses to exec a `.py` directly, so tests get a `.cmd` shim; POSIX
    gets a chmod-ed shell script. `ARAGON_BIN` points at whichever applies.
    """
    script = FIXTURES_DIR / "fake_aragon.py"
    python = sys.executable
    if os.name == "nt":
        shim = _TMP_DIR / "fake_aragon.cmd"
        shim.write_text(f'@echo off\r\n"{python}" "{script}" %*\r\n', encoding="ascii")
    else:  # pragma: no cover - exercised on CI runners
        shim = _TMP_DIR / "fake_aragon.sh"
        shim.write_text(f'#!/bin/sh\nexec "{python}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return shim


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def sample_report() -> dict:
    return json.loads((FIXTURES_DIR / "sample_report.json").read_text(encoding="utf-8"))


@pytest.fixture()
def engine():
    """A fresh schema per test — `create_all` is a test-only convenience.

    ★V4-P1-7: `create_all` cannot build an FTS5 virtual table, so the shared
    `ensure_fts_schema()` runs right after it. Without this the whole search test
    layer would be silently indexing into nothing (AC-33 would be permanently false).
    """
    from app.services.search_index import drop_fts_schema, ensure_fts_schema

    test_engine = build_engine(f"sqlite:///{_DB_PATH.as_posix()}")
    Base.metadata.drop_all(test_engine)
    with test_engine.begin() as connection:
        drop_fts_schema(connection)  # virtual tables are invisible to drop_all
        Base.metadata.create_all(connection)
        ensure_fts_schema(connection)
    yield test_engine
    with test_engine.begin() as connection:
        drop_fts_schema(connection)
    Base.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture()
def session(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(engine, monkeypatch) -> Iterator[TestClient]:
    """TestClient wired to the per-test engine."""
    from app.db import session as session_module

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "engine", engine)

    import app.main as main_module

    monkeypatch.setattr(main_module, "SessionLocal", factory, raising=False)
    # Tests own the schema: create_all above already built it, so the startup
    # migration would collide. Alembic stays the production path (R-14).
    monkeypatch.setattr(main_module, "run_migrations", lambda: None, raising=False)

    from app.services import run_manager as run_manager_module

    run_manager_module.reset_manager()
    # The capability cache is deliberately NOT reset here: tests that assert on a
    # specific verdict reset it themselves (test_capabilities_api.py), and doing it
    # per test made the suite spawn `aragon info --json` on every request.

    with TestClient(main_module.app) as test_client:
        yield test_client
    run_manager_module.reset_manager()


@pytest.fixture()
def instrument(session: Session) -> Instrument:
    row = Instrument(
        id="CN:600519.SH",
        symbol="600519.SH",
        name="贵州茅台",
        market="CN",
        exchange="SSE",
        currency="CNY",
        sector="食品饮料",
        tz="Asia/Shanghai",
        created_at="2026-02-10T00:00:00Z",
        updated_at="2026-02-10T00:00:00Z",
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture()
def fixed_now() -> datetime:
    return datetime(2026, 2, 10, 1, 0, 0, tzinfo=UTC)


@pytest.fixture()
def snapshot_payload() -> dict:
    """A compact but complete §8.4 snapshot, matching what `build_snapshot` emits."""
    return {
        "instrument": {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "market": "CN",
            "currency": "CNY",
            "sector": "食品饮料",
            "tz": "Asia/Shanghai",
        },
        "asOf": "2026-02-10T01:00:00Z",
        "quote": {"last": 1672.0, "changePct": 1.24, "prevClose": 1651.5, "volume": 3120000},
        "performance": {
            "ret1w": 2.1,
            "ret1m": -3.4,
            "ret3m": 6.8,
            "ret1y": 12.3,
            "maxDrawdown1y": -18.2,
            "volAnnualized": 24.6,
        },
        "valuation": {"peTtm": 24.1, "pePercentile3y": 31.0, "pb": 7.2, "dividendYield": 2.1, "marketCap": 2.1e12},
        "financials": [
            {
                "period": "2025Q3",
                "revenue": 123456000000.0,
                "revenueYoY": 15.2,
                "netProfit": 60800000000.0,
                "netProfitYoY": 16.1,
                "ocf": 58200000000.0,
                "grossMargin": 91.3,
            }
        ],
        "technicals": {"ma5": 1650.2, "ma20": 1622.7, "ma60": 1598.4, "rsi14": 56.2, "macd": 12.4},
        "peers": [{"symbol": "000858.SZ", "name": "五粮液", "peTtm": 18.2, "pb": 4.1, "revenueYoY": 8.1}],
        "sources": [
            {"id": "S1", "title": "行情与估值快照", "publisher": "akshare", "publishedAt": "2026-02-10"},
            {"id": "S2", "title": "2025 年三季报", "publisher": "上交所", "publishedAt": "2025-10-28"},
        ],
        "dataQuality": {
            "source": "synthetic",
            "barsCount": 730,
            "lastBarTs": 1770681600,
            "stale": False,
            "warnings": [],
        },
    }
