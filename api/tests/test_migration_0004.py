"""迁移 `0004_agent_diagnostics`：诊断列 + 成本三态（spec §20.6.1 / M20-M21；AC-87）。

★v8（V8-P0-2）这条 AC 的核心不是"列存在"，而是**链路可达**：`revision` / `down_revision`
写成 `"0003"` 会让 `alembic upgrade head` 第一步就报 `Can't locate revision identified
by '0003'`——一个只在真正部署时才炸的错误。所以这里从**空库**一次跑到 head，再
`downgrade -1` + `upgrade head` 往返一次。

四条与 0003 同源的纪律在这里被断言/说明：

1. `batch_alter_table`（SQLite 与 PG 走同一条路径）；
2. 布尔列一律 `Integer` + `server_default`（本轮**不新增布尔列**，`source` 是字符串三态）；
3. **不在 ALTER 里加 CHECK**，也**不写进 ORM**——本项目禁止 `create_all`，生产表由
   Alembic 建、没有 CHECK，写在 ORM 里只会造成"测试库有约束、生产库没有"；
4. `server_default` 必须给，老行的 `source='unknown'` 是**正确事实**，不是占位符。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
PG_URL = os.environ.get("TEST_POSTGRES_URL", "")

RUN_COLUMNS = {
    "retry_count",
    "structured_attempts",
    "agent_provider",
    "agent_model",
    "failure_fingerprint",
    "cost_usd_est",
    "cost_source",
}
LEDGER_COLUMNS = {"usd_est", "source"}


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    settings = get_settings()
    url = f"sqlite:///{(tmp_path / 'agent.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url, raising=False)
    engine = sa.create_engine(url)
    try:
        yield _config_for(url), engine
    finally:
        engine.dispose()


def _columns(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in sa.inspect(engine).get_columns(table)}


def _revision(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


# ── AC-87 主线：空库一次跑到 head ───────────────────────────────────────────


def test_upgrade_head_reaches_0004_from_an_empty_database(migrated) -> None:
    """AC-87：整条链在空库上一次跑通（`down_revision` 指向**真实的** revision id）。"""
    config, engine = migrated
    command.upgrade(config, "head")
    assert _revision(engine) == "0006_report_library"

    run_columns = _columns(engine, "runs")
    assert set(run_columns) >= RUN_COLUMNS
    assert run_columns["retry_count"]["nullable"] is False, "server_default='0'，老行不用回填"
    assert run_columns["structured_attempts"]["nullable"] is True
    assert run_columns["failure_fingerprint"]["nullable"] is True
    assert run_columns["agent_model"]["nullable"] is True

    ledger_columns = _columns(engine, "cost_ledger")
    assert set(ledger_columns) >= LEDGER_COLUMNS
    assert ledger_columns["source"]["nullable"] is False, "source 非空，靠 server_default 落地"
    assert ledger_columns["usd_est"]["nullable"] is True

    indexes = {index["name"] for index in sa.inspect(engine).get_indexes("cost_ledger")}
    assert "ix_cost_ledger_source" in indexes
    # 0003 的四个索引一个都不能少
    assert {"ix_cost_ledger_created", "ix_cost_ledger_kind", "ix_cost_ledger_symbol", "ux_cost_ledger_ref"} <= indexes


def test_downgrade_and_upgrade_round_trip(migrated) -> None:
    """AC-87：`downgrade -1` 再 `upgrade head` 往返成功；唯一键不被破坏。"""
    config, engine = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "0003_cost_ledger")
    assert _revision(engine) == "0003_cost_ledger"
    assert not (RUN_COLUMNS & set(_columns(engine, "runs"))), "降级必须把 7 个列全部删掉"
    assert not (LEDGER_COLUMNS & set(_columns(engine, "cost_ledger")))

    command.upgrade(config, "head")
    assert _revision(engine) == "0006_report_library"
    assert set(_columns(engine, "runs")) >= RUN_COLUMNS
    indexes = {index["name"] for index in sa.inspect(engine).get_indexes("cost_ledger")}
    assert "ux_cost_ledger_ref" in indexes, "往返之后幂等键仍在"

    # 再从 head 一路降回 base：整条链没有断点
    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}


def test_old_rows_keep_usd_null_and_get_source_unknown(migrated) -> None:
    """老行的 `source='unknown'` 是**正确事实**（当时确实不知道），不是占位符。"""
    config, engine = migrated
    command.upgrade(config, "0003_cost_ledger")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO cost_ledger (kind, ref_id, symbol, report_id, model, usd, known, created_at) "
                "VALUES ('run', 'legacy-1', NULL, NULL, NULL, NULL, 0, '2026-01-01T00:00:00Z')"
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT usd, usd_est, source, known FROM cost_ledger WHERE ref_id = 'legacy-1'")
        ).one()
    assert row.usd is None, "旧行不回填：回填会伪造事实"
    assert row.usd_est is None
    assert row.source == "unknown"
    assert row.known == 0


def test_revision_ids_are_the_real_ones_not_abbreviations() -> None:
    """★v8（V8-P0-2）：写 `"0003"` 会让 `upgrade head` 第一步就炸，不必能起数据库也能发现。"""
    script = ScriptDirectory.from_config(_config_for("sqlite://"))
    assert script.get_current_head() == "0006_report_library"
    revision = script.get_revision("0004_agent_diagnostics")
    assert revision is not None
    assert revision.down_revision == "0003_cost_ledger"
    # 目标必须真实存在（这条断言就是 V8-P0-2 的机器化版本）
    assert script.get_revision(str(revision.down_revision)) is not None


def test_the_migration_says_why_there_is_no_check_constraint() -> None:
    """C-20：迁移文件里必须有"为什么不加 CHECK"的注释，否则下一个人会顺手补上。

    补上之后，测试库（用建表助手建的）有约束、生产库（Alembic 建的）没有——正好把
    "schema 只有一个真源"反过来用。★实施发现（I-62）：这段注释本身**不能写出那个
    建表助手的名字**——`test_boundaries::test_no_raw_create_all_token_anywhere_in_app`
    对 `api/app/**` 做的是字面扫描，连注释里的名字也会被拦（它守的是"这个词不该出现在
    应用代码里"，而迁移文件属于应用代码）。
    """
    source = (MIGRATIONS_DIR / "versions" / "0004_agent_diagnostics.py").read_text(encoding="utf-8")
    assert "CheckConstraint" in source
    assert "禁止在应用层建表" in source
    assert "ledgerGap" in source
    assert ("create" + "_all") not in source


# ── Postgres 分支 ───────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not PG_URL,
    reason="没有 TEST_POSTGRES_URL：Postgres 分支需要一台真实实例与一个空库（测试默认离线）",
)
def test_postgres_round_trip() -> None:  # pragma: no cover - 需要真实 PG
    config = _config_for(PG_URL)
    engine = sa.create_engine(PG_URL)
    try:
        command.upgrade(config, "head")
        columns = _columns(engine, "cost_ledger")
        assert columns["source"]["nullable"] is False
        assert columns["usd_est"]["nullable"] is True
        command.downgrade(config, "0003_cost_ledger")
        assert "source" not in _columns(engine, "cost_ledger")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
