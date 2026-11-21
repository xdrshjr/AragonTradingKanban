"""迁移 `0010_workflow_graph`（report-workflow-graph §6.1，M60；AC-W4）。

升降级往返无损、老 `runs` 行不变（新列恒为 NULL，不回填）、单头，以及**ORM 模型与迁移逐列一致**——
尤其是偏唯一索引 `ux_workflow_default`：它必须同时声明在模型里（`conftest` 的 `create_all` 库才会强制它），
否则"默认至多一个"的测试在测试库里是空转。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import get_settings
from app.db.base import Base

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
NEW_TABLES = {"workflow_templates", "workflow_versions", "workflow_chat_messages"}
RUN_COLUMNS = {"workflow_version_id", "workflow_label", "workflow_adherence"}


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):  # noqa: ANN001
    url = f"sqlite:///{(tmp_path / 'workflow.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", url, raising=False)
    engine = sa.create_engine(url)
    try:
        yield _config_for(url), engine
    finally:
        engine.dispose()


def _columns(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in sa.inspect(engine).get_columns(table)}


def _index_sql(engine: sa.Engine, name: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(sa.text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name}).scalar_one()
        )


def test_upgrade_creates_three_tables_and_three_nullable_run_columns(migrated) -> None:  # noqa: ANN001
    config, engine = migrated
    command.upgrade(config, "head")
    assert set(sa.inspect(engine).get_table_names()) >= NEW_TABLES
    columns = _columns(engine, "runs")
    for name in RUN_COLUMNS:
        assert columns[name]["nullable"] is True and columns[name]["default"] is None
    assert "ix_runs_workflow" in {index["name"] for index in sa.inspect(engine).get_indexes("runs")}


def test_the_default_index_is_a_partial_unique_index(migrated) -> None:  # noqa: ANN001
    config, engine = migrated
    command.upgrade(config, "head")
    sql = _index_sql(engine, "ux_workflow_default").upper()
    assert "UNIQUE" in sql and "WHERE" in sql and "IS_DEFAULT = 1" in sql.replace("  ", " ")


def test_existing_runs_are_not_backfilled(migrated) -> None:  # noqa: ANN001
    config, engine = migrated
    command.upgrade(config, "0009_ask_context")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO instruments (id, symbol, market, name, currency, created_at, updated_at) "
                "VALUES ('i1', '600519', 'CN', 'x', 'CNY', 't', 't')"
            )
        )
        connection.execute(
            sa.text("INSERT INTO runs (id, instrument_id, status, started_at) VALUES ('r1', 'i1', 'succeeded', 't')")
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT status, workflow_version_id, workflow_label, workflow_adherence FROM runs WHERE id='r1'")
        ).one()
    assert tuple(row) == ("succeeded", None, None, None)


def test_upgrade_downgrade_upgrade_leaves_no_trace(migrated) -> None:  # noqa: ANN001
    config, engine = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "0009_ask_context")
    assert not (NEW_TABLES & set(sa.inspect(engine).get_table_names()))
    assert not (RUN_COLUMNS & set(_columns(engine, "runs")))
    command.upgrade(config, "head")
    assert set(sa.inspect(engine).get_table_names()) >= NEW_TABLES
    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}


def test_the_revision_chain_has_one_head_with_the_real_id() -> None:
    script = ScriptDirectory.from_config(_config_for("sqlite://"))
    assert script.get_heads() == ["0010_workflow_graph"]
    assert script.get_revision("0010_workflow_graph").down_revision == "0009_ask_context"


def test_orm_models_and_the_migration_agree_column_by_column(migrated) -> None:  # noqa: ANN001
    """`Base.metadata` vs the reflected migrated tables: names, nullability, and every index
    (name, uniqueness, columns) — including the partial unique index's WHERE clause."""
    config, engine = migrated
    command.upgrade(config, "head")
    inspector = sa.inspect(engine)
    for table in sorted(NEW_TABLES | {"runs"}):
        model = Base.metadata.tables[table]
        reflected = {column["name"]: column for column in inspector.get_columns(table)}
        assert set(model.columns.keys()) == set(reflected), table
        for name, column in model.columns.items():
            assert column.nullable == reflected[name]["nullable"] or column.primary_key, (table, name)
        model_indexes = {(ix.name, bool(ix.unique), tuple(c.name for c in ix.columns)) for ix in model.indexes}
        real_indexes = {
            (ix["name"], bool(ix["unique"]), tuple(ix["column_names"])) for ix in inspector.get_indexes(table)
        }
        assert model_indexes <= real_indexes, (table, model_indexes - real_indexes)
    where = Base.metadata.tables["workflow_templates"].indexes
    default_index = next(ix for ix in where if ix.name == "ux_workflow_default")
    assert default_index.unique and "is_default = 1" in str(default_index.dialect_options["sqlite"]["where"])


def test_the_test_database_enforces_a_single_default(engine) -> None:  # noqa: ANN001
    """The `create_all` schema carries the partial unique index too (otherwise AC-W5 is vacuous)."""
    insert = sa.text(
        "INSERT INTO workflow_templates (id, name, draft_doc_json, is_default, created_at, updated_at) "
        "VALUES (:id, 'n', '{}', :d, 't', 't')"
    )
    with engine.begin() as connection:
        connection.execute(insert, {"id": "a", "d": 1})
        connection.execute(insert, {"id": "b", "d": 0})
        connection.execute(insert, {"id": "c", "d": 0})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(insert, {"id": "d", "d": 1})
