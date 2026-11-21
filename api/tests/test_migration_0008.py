"""迁移 `0008_account_recovery`（spec §25.6，M53/M55；AC-210）。

只做三件事，纪律同 0006/0007：`users` 加两个可空无默认的列、`sessions` 加两个支撑 `purge_dead` 的
索引（其一是部分索引）、`downgrade` 先删索引再删列。升 → 降 → 再升无痕。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
NEW_COLUMNS = {"recovery_hash", "recovery_set_at"}
NEW_INDEXES = {"ix_sessions_revoked", "ix_sessions_idle"}


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):  # noqa: ANN001
    settings = get_settings()
    url = f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url, raising=False)
    engine = sa.create_engine(url)
    try:
        yield _config_for(url), engine
    finally:
        engine.dispose()


def _revision(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _columns(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in sa.inspect(engine).get_columns(table)}


def _indexes(engine: sa.Engine, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(engine).get_indexes(table)}


def test_upgrade_adds_two_nullable_columns_and_two_indexes(migrated) -> None:  # noqa: ANN001
    config, engine = migrated
    command.upgrade(config, "head")
    assert _revision(engine) == "0010_workflow_graph"
    columns = _columns(engine, "users")
    for name in NEW_COLUMNS:
        assert name in columns
        assert columns[name]["nullable"] is True
        assert columns[name]["default"] is None
    assert _indexes(engine, "sessions") >= NEW_INDEXES


def test_existing_users_are_not_backfilled(migrated) -> None:  # noqa: ANN001
    """旧用户的 `recovery_hash` 是 NULL = "未设置"——不回填（否则等于凭空发了一把码）。"""
    config, engine = migrated
    command.upgrade(config, "0007_users_auth")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (id, username, username_lc, password_hash, role, status, created_at, updated_at) "
                "VALUES ('u1', 'old', 'old', 'x', 'user', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT recovery_hash, recovery_set_at FROM users WHERE id='u1'")).one()
    assert tuple(row) == (None, None)


def test_downgrade_drops_indexes_then_columns_and_the_round_trip_replays(migrated) -> None:  # noqa: ANN001
    config, engine = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "0007_users_auth")
    assert _revision(engine) == "0007_users_auth"
    assert not (NEW_COLUMNS & set(_columns(engine, "users")))
    assert not (NEW_INDEXES & _indexes(engine, "sessions"))
    # 0007 自己的东西不能被连带删掉。
    assert {"ux_sessions_token", "ix_sessions_user_live"} <= _indexes(engine, "sessions")
    command.upgrade(config, "head")
    assert _revision(engine) == "0010_workflow_graph"
    assert set(_columns(engine, "users")) >= NEW_COLUMNS
    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}


def test_the_revision_chain_has_one_head() -> None:
    script = ScriptDirectory.from_config(_config_for("sqlite://"))
    assert script.get_heads() == ["0010_workflow_graph"]
    revision = script.get_revision("0008_account_recovery")
    assert revision is not None
    assert revision.down_revision == "0007_users_auth"


def test_the_migration_obeys_the_discipline() -> None:
    source = (MIGRATIONS_DIR / "versions" / "0008_account_recovery.py").read_text(encoding="utf-8")
    assert "batch_alter_table(" not in source
    assert source.count("op.add_column(") == 2
    # 部分索引双写方言条件（SQLite / PostgreSQL 各一份）。
    assert "sqlite_where" in source and "postgresql_where" in source
    # downgrade：先删索引、后删列。
    downgrade = source[source.index("def downgrade") :]
    assert downgrade.index("drop_index") < downgrade.index("drop_column")


def test_the_purge_subqueries_use_the_new_indexes(migrated) -> None:  # noqa: ANN001
    """AC-210：两条清理子查询分别走 `ix_sessions_revoked` / `ix_sessions_idle`，不全表扫描。"""
    config, engine = migrated
    command.upgrade(config, "head")
    revoked_plan = (
        "EXPLAIN QUERY PLAN SELECT id FROM sessions WHERE revoked_at IS NOT NULL AND revoked_at < :c LIMIT 10"
    )
    idle_plan = "EXPLAIN QUERY PLAN SELECT id FROM sessions WHERE idle_expires_at < :c LIMIT 10"
    with engine.connect() as connection:
        revoked = " ".join(str(r[3]) for r in connection.execute(sa.text(revoked_plan), {"c": "2026-01-01"}))
        idle = " ".join(str(r[3]) for r in connection.execute(sa.text(idle_plan), {"c": "2026-01-01"}))
    assert "ix_sessions_revoked" in revoked, revoked
    assert "ix_sessions_idle" in idle, idle


def test_orm_models_and_the_migration_agree() -> None:
    """模型文件里新增的列/索引与迁移一致：测试库（`create_all`）与生产库不能是两个形状。"""
    import app.models  # noqa: F401
    from app.db.base import Base

    users = Base.metadata.tables["users"]
    assert {column.name for column in users.columns} >= NEW_COLUMNS
    assert all(users.columns[name].nullable for name in NEW_COLUMNS)
    sessions = Base.metadata.tables["sessions"]
    assert {index.name for index in sessions.indexes} >= NEW_INDEXES
