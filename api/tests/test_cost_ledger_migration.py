"""迁移 `0003_cost_ledger` 的升级/回滚（spec §19.6.1；AC-55 / AC-57 的数据库侧）。

**用了哪一种做法，为什么用这一种**：本仓库此前没有任何测试驱动 alembic
（`grep alembic api/tests` 命中 0 条），所以先实测"能不能从 pytest 里跑"，结论是**能**：
`alembic.config.Config()` + `command.upgrade/downgrade` 对一个临时 sqlite 文件可以完整跑通
`base → head → 0003 → 0002 → base`。原因是 `app/db/migrations/env.py` 的 URL 只取自
`get_settings().database_url`，测试里 monkeypatch 这一个字段就够了——不需要 `alembic.ini`、
不需要改 cwd、不需要子进程。所以这里走**真实迁移**（而不是退化成"断言 op 名被调用"），
因为真实迁移能顺带证明 `usd` 可空与唯一键真的落到了库里。

Postgres 分支需要一台真实实例：拿不到 `TEST_POSTGRES_URL` 就跳过，理由写明。最后一条用例
额外守 revision 链——迁移文件被改名或接错父节点时，不必能起 alembic 也能发现。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"

LEDGER_INDEXES = {
    "ix_cost_ledger_created",
    "ix_cost_ledger_kind",
    "ix_cost_ledger_symbol",
    "ux_cost_ledger_ref",
}

PG_URL = os.environ.get("TEST_POSTGRES_URL", "")


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    """一个指向临时 sqlite 文件的 alembic 环境（用完 dispose，Windows 才删得掉文件）。"""
    settings = get_settings()
    url = f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url, raising=False)

    engine = sa.create_engine(url)
    try:
        yield _config_for(url), engine
    finally:
        engine.dispose()


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _revision(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


# ── 升级 ────────────────────────────────────────────────────────────────────


def test_upgrade_creates_the_ledger_with_a_nullable_usd(migrated) -> None:
    """`usd` **可空**是这条迁移的核心：`NULL` = 未知，`0` = 已知且免费（AC-55）。"""
    config, engine = migrated
    # ★v7：升到**本条迁移**为止（`head` 现在是 0004，新列由 `test_migration_0004`
    # 守着），这样这一条断言永远只谈 0003 建的形状。
    command.upgrade(config, "0003_cost_ledger")

    inspector = sa.inspect(engine)
    assert "cost_ledger" in inspector.get_table_names()

    columns = {column["name"]: column for column in inspector.get_columns("cost_ledger")}
    assert columns["usd"]["nullable"] is True, "NOT NULL DEFAULT 0 会复活 v4 的'未知记成免费'缺陷"
    assert columns["kind"]["nullable"] is False
    assert columns["ref_id"]["nullable"] is False
    assert columns["known"]["nullable"] is False
    assert columns["created_at"]["nullable"] is False

    indexes = {index["name"]: index for index in inspector.get_indexes("cost_ledger")}
    assert set(indexes) == LEDGER_INDEXES
    assert bool(indexes["ux_cost_ledger_ref"]["unique"]) is True, "幂等键必须是唯一索引"
    assert indexes["ux_cost_ledger_ref"]["column_names"] == ["kind", "ref_id"]
    assert _revision(engine) == "0003_cost_ledger"


def test_the_idempotency_key_is_enforced_by_the_database(migrated) -> None:
    """AC-57 的数据库侧：重试插入被唯一键拒绝，而不是把同一次生成记两遍。"""
    config, engine = migrated
    command.upgrade(config, "0003_cost_ledger")
    insert = sa.text(
        "INSERT INTO cost_ledger (kind, ref_id, symbol, report_id, model, usd, known, created_at) "
        "VALUES (:kind, :ref, NULL, NULL, NULL, :usd, :known, :created)"
    )
    with engine.begin() as connection:
        connection.execute(
            insert,
            {"kind": "run", "ref": "run-1", "usd": None, "known": 0, "created": "2026-02-12T00:00:00Z"},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert,
            {"kind": "run", "ref": "run-1", "usd": 0.5, "known": 1, "created": "2026-02-12T00:00:01Z"},
        )

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM cost_ledger")).scalar_one() == 1


# ── 回滚 ────────────────────────────────────────────────────────────────────


def test_downgrade_drops_only_the_ledger(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "0002_ask_search_schedule")

    tables = _tables(engine)
    assert "cost_ledger" not in tables
    assert "report_asks" in tables, "回滚 0003 不该碰 0002 建的表"
    assert "schedules" in tables
    assert _revision(engine) == "0002_ask_search_schedule"

    # 再往下是 0001 → base：整条链没有断点（悬空的外键/顺序问题会在这里炸）
    command.downgrade(config, "base")
    assert _tables(engine) == {"alembic_version"}


# ── revision 链（不依赖能否起 alembic） ────────────────────────────────────


def test_revision_chain_points_at_0002() -> None:
    script = ScriptDirectory.from_config(_config_for("sqlite://"))

    # ★v7：head 已经是 0004；这条断言守的是**整条链**没有断点（0003 的父节点仍是 0002）。
    assert script.get_current_head() == "0010_workflow_graph"
    revision = script.get_revision("0003_cost_ledger")
    assert revision is not None
    assert revision.down_revision == "0002_ask_search_schedule"
    assert [rev.revision for rev in script.walk_revisions()][:9] == [
        "0010_workflow_graph",
        "0009_ask_context",
        "0008_account_recovery",
        "0007_users_auth",
        "0006_report_library",
        "0005_engine_probes",
        "0004_agent_diagnostics",
        "0003_cost_ledger",
        "0002_ask_search_schedule",
    ]


# ── Postgres 分支 ───────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not PG_URL,
    reason="没有 TEST_POSTGRES_URL：Postgres 分支需要一台真实实例与一个空库（本仓库测试默认离线、无网络）",
)
def test_postgres_uses_bigserial_for_the_ledger_id() -> None:
    """SQLite 与 Postgres 的唯一差异就是自增拼法；其余列按同一张表定义建出来。"""
    config = _config_for(PG_URL)
    engine = sa.create_engine(PG_URL)
    try:
        command.upgrade(config, "head")
        columns = {column["name"]: column for column in sa.inspect(engine).get_columns("cost_ledger")}
        assert columns["id"]["type"].__class__.__name__ == "BIGINT"
        assert columns["usd"]["nullable"] is True
        indexes = {index["name"] for index in sa.inspect(engine).get_indexes("cost_ledger")}
        assert "ux_cost_ledger_ref" in indexes

        command.downgrade(config, "0002_ask_search_schedule")
        assert "cost_ledger" not in _tables(engine)
    finally:
        engine.dispose()
