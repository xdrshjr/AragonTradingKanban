"""迁移 `0007_users_auth`（spec §24.6.5，M45；AC-175/176、AC-205 的回滚半条）。

四条断言来自 §24.6.5，外加 0003–0006 一路传下来的纪律：

1. `ux_users_root` 阻止第二个 root；
2. `ux_quota_ref_reason` 阻止重复扣/退（但**不**限制没进谓词的 reason，例如反复 `admin_grant`）；
3. 五个新列确为可空、无默认值、不回填；
4. `alembic heads` 只有一个；升 → 降 → 再升三连无痕；不 batch、不重建表。
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

NEW_TABLES = {"users", "sessions", "invite_codes", "quota_ledger", "quota_requests", "audit_log", "user_watchlist"}
ADDED_COLUMNS = {
    "runs": "created_by",
    "reports": "created_by",
    "report_asks": "user_id",
    "schedules": "owner_id",
    "cost_ledger": "user_id",
}
NEW_INDEXES = {
    "users": {"ux_users_username_lc", "ux_users_root", "ix_users_list", "ix_users_status_role"},
    "sessions": {"ux_sessions_token", "ix_sessions_user_live"},
    "invite_codes": {"ux_invite_code"},
    "quota_ledger": {"ix_quota_user_daily", "ux_quota_ref_reason"},
    "quota_requests": {"ux_quota_req_pending", "ix_quota_req_queue"},
    "audit_log": {"ix_audit_at", "ix_audit_action", "ix_audit_actor"},
    "runs": {"ix_runs_created_by"},
    "reports": {"ix_reports_created_by"},
    "report_asks": {"ix_asks_user"},
    "cost_ledger": {"ix_cost_user"},
}


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    settings = get_settings()
    url = f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"
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


def _index_sql(engine: sa.Engine, name: str) -> str:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:name"), {"name": name}
        ).one_or_none()
    return "" if row is None or row.sql is None else str(row.sql)


def _fk_on(connection) -> None:  # noqa: ANN001
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


NOW = "2026-11-20T00:00:00Z"


def _insert_user(connection, user_id: str, name: str, role: str = "user") -> None:  # noqa: ANN001
    connection.execute(
        sa.text(
            "INSERT INTO users (id, username, username_lc, password_hash, role, created_at, updated_at) "
            "VALUES (:id, :name, :lc, 'x', :role, :now, :now)"
        ),
        {"id": user_id, "name": name, "lc": name.lower(), "role": role, "now": NOW},
    )


def _insert_ledger(connection, reason: str, ref: str | None, delta: int = -1) -> None:  # noqa: ANN001
    connection.execute(
        sa.text(
            "INSERT INTO quota_ledger (user_id, resource, delta, reason, source, day_key, ref_id, created_at) "
            "VALUES ('u1', 'report', :delta, :reason, 'daily', '2026-11-20', :ref, :now)"
        ),
        {"delta": delta, "reason": reason, "ref": ref, "now": NOW},
    )


# ── 升级 ────────────────────────────────────────────────────────────────────


def test_upgrade_creates_seven_tables_five_columns_and_every_index(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    assert _revision(engine) == "0010_workflow_graph"
    assert set(sa.inspect(engine).get_table_names()) >= NEW_TABLES
    for table, names in NEW_INDEXES.items():
        assert names <= _indexes(engine, table), (table, names - _indexes(engine, table))


def test_the_five_added_columns_are_nullable_with_no_default(migrated) -> None:
    """E-7：只 `add_column`、可空、无默认值、不回填——旧行语义是"系统/历史"。"""
    config, engine = migrated
    command.upgrade(config, "0006_report_library")
    with engine.begin() as connection:  # 旧行：升级前就存在
        connection.execute(
            sa.text(
                "INSERT INTO instruments (id, symbol, name, market, exchange, currency, tz, created_at, updated_at) "
                "VALUES ('CN:1', '1', 'x', 'CN', 'SSE', 'CNY', 'Asia/Shanghai', :n, :n)"
            ),
            {"n": NOW},
        )
        connection.execute(
            sa.text("INSERT INTO runs (id, instrument_id, status, started_at) VALUES ('r1', 'CN:1', 'succeeded', :n)"),
            {"n": NOW},
        )
    command.upgrade(config, "head")
    for table, column in ADDED_COLUMNS.items():
        info = _columns(engine, table)[column]
        assert info["nullable"] is True, (table, column)
        assert info["default"] is None, (table, column)
        assert info["type"].length == 32
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT created_by FROM runs WHERE id='r1'")).scalar_one() is None, "不回填"


def test_the_root_index_is_partial_and_blocks_a_second_root(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    assert "WHERE role = 'root'" in _index_sql(engine, "ux_users_root")
    with engine.begin() as connection:
        _insert_user(connection, "u1", "root_one", "root")
        _insert_user(connection, "u2", "admin_one", "admin")
        _insert_user(connection, "u3", "admin_two", "admin")  # 多个 admin / user 都可以
        _insert_user(connection, "u4", "plain", "user")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        _insert_user(connection, "u5", "root_two", "root")


def test_usernames_are_unique_case_insensitively_through_username_lc(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _insert_user(connection, "u1", "Alice")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        _insert_user(connection, "u2", "ALICE")


def test_role_and_status_check_constraints(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        _insert_user(connection, "u1", "wizard", "wizard")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (id, username, username_lc, password_hash, role, status, created_at, updated_at) "
                "VALUES ('u9', 'x', 'x', 'x', 'user', 'frozen', :now, :now)"
            ),
            {"now": NOW},
        )


def test_the_ledger_index_blocks_a_double_charge_and_a_double_refund_only(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    assert "reason IN ('run','run_refund','ask','ask_refund')" in _index_sql(engine, "ux_quota_ref_reason")
    with engine.begin() as connection:
        _insert_ledger(connection, "run", "run1")
        _insert_ledger(connection, "run_refund", "run1", delta=1)
        _insert_ledger(connection, "run", "run2")
    for reason, delta in (("run", -1), ("run_refund", 1)):
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            _insert_ledger(connection, reason, "run1", delta)
    # 不在谓词里的 reason 不受限：同一个 ref 可以反复 grant（管理员多次赠送）。
    with engine.begin() as connection:
        _insert_ledger(connection, "admin_grant", "req1", 5)
        _insert_ledger(connection, "admin_grant", "req1", 5)
        _insert_ledger(connection, "signup_bonus", None, 2)
        _insert_ledger(connection, "signup_bonus", None, 2)


def test_ask_ids_fit_the_ledger_ref_column(migrated) -> None:
    """spec 缺陷修正：追问 id 是 `"ask_" + 32 hex`（36 字符），`ref_id` 必须 ≥ 36。"""
    config, engine = migrated
    command.upgrade(config, "head")
    assert _columns(engine, "quota_ledger")["ref_id"]["type"].length >= 36
    with engine.begin() as connection:
        _insert_ledger(connection, "ask", "ask_" + "a" * 32)


def test_only_one_pending_quota_request_per_user(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")

    def add(request_id: str, status: str) -> None:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO quota_requests (id, user_id, resource, amount, reason, status, created_at) "
                    "VALUES (:id, 'u1', 'report', 3, 'why', :status, :now)"
                ),
                {"id": request_id, "status": status, "now": NOW},
            )

    add("q1", "pending")
    add("q2", "approved")
    add("q3", "rejected")
    with pytest.raises(sa.exc.IntegrityError):
        add("q4", "pending")


def test_sessions_cascade_with_their_user_and_tokens_are_unique(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _fk_on(connection)
        _insert_user(connection, "u1", "alice")
        for sid, token in (("s1", "t" * 64), ("s2", "u" * 64)):
            connection.execute(
                sa.text(
                    "INSERT INTO sessions "
                    "(id, token_hash, user_id, created_at, last_seen_at, idle_expires_at, expires_at) "
                    "VALUES (:id, :token, 'u1', :n, :n, :n, :n)"
                ),
                {"id": sid, "token": token, "n": NOW},
            )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO sessions "
                "(id, token_hash, user_id, created_at, last_seen_at, idle_expires_at, expires_at) "
                "VALUES ('s3', :token, 'u1', :n, :n, :n, :n)"
            ),
            {"token": "t" * 64, "n": NOW},
        )
    with engine.begin() as connection:
        _fk_on(connection)
        connection.execute(sa.text("DELETE FROM users WHERE id='u1'"))
        assert connection.execute(sa.text("SELECT COUNT(*) FROM sessions")).scalar_one() == 0


# ── 降级与往返 ──────────────────────────────────────────────────────────────


def test_downgrade_removes_everything_and_the_round_trip_replays(migrated) -> None:
    """AC-205 的回滚半条：升 → 降 → 再升三连，降级后既有的 0006 状态原样。"""
    config, engine = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "0006_report_library")
    assert _revision(engine) == "0006_report_library"
    remaining = set(sa.inspect(engine).get_table_names())
    assert not (NEW_TABLES & remaining)
    for table, column in ADDED_COLUMNS.items():
        assert column not in _columns(engine, table), (table, column)
    for table, names in NEW_INDEXES.items():
        if table in remaining:
            assert not (names & _indexes(engine, table)), table
    # 0006 的东西不能被连带删掉。
    assert {"ix_reports_feed", "ix_reports_starred", "ix_reports_trash"} <= _indexes(engine, "reports")
    assert "doc_sha1" in _columns(engine, "reports")

    command.upgrade(config, "head")
    assert _revision(engine) == "0010_workflow_graph"
    assert set(sa.inspect(engine).get_table_names()) >= NEW_TABLES

    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}


def test_downgrade_keeps_the_rows_of_the_tables_it_only_altered(migrated) -> None:
    config, engine = migrated
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO instruments (id, symbol, name, market, exchange, currency, tz, created_at, updated_at) "
                "VALUES ('CN:1', '1', 'x', 'CN', 'SSE', 'CNY', 'Asia/Shanghai', :n, :n)"
            ),
            {"n": NOW},
        )
        connection.execute(
            sa.text(
                "INSERT INTO runs (id, instrument_id, status, started_at, created_by) "
                "VALUES ('r1', 'CN:1', 'succeeded', :n, 'someone')"
            ),
            {"n": NOW},
        )
    command.downgrade(config, "0006_report_library")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT id FROM runs")).scalars().all() == ["r1"]


# ── revision 链与纪律 ───────────────────────────────────────────────────────


def test_there_is_exactly_one_head_and_the_ids_are_the_real_ones() -> None:
    script = ScriptDirectory.from_config(_config_for("sqlite://"))
    assert script.get_heads() == ["0010_workflow_graph"]
    revision = script.get_revision("0007_users_auth")
    assert revision is not None
    assert revision.down_revision == "0006_report_library"
    assert script.get_revision(str(revision.down_revision)) is not None


def test_the_migration_obeys_the_discipline() -> None:
    source = (MIGRATIONS_DIR / "versions" / "0007_users_auth.py").read_text(encoding="utf-8")
    # 调用形态（文件头的说明不算）：不 batch、不重建既有表。
    assert "batch_alter_table(" not in source
    assert source.count("op.add_column(") >= 1
    assert 'op.drop_table("reports"' not in source
    # 种子行不在迁移里写（root 的引导要读环境变量并哈希，且要幂等）。
    assert "INSERT INTO users" not in source and "op.bulk_insert" not in source


def test_orm_models_and_the_migration_agree_on_tables_and_indexes(migrated) -> None:
    """模型文件里的表/索引名与迁移建出来的一致：测试库（`create_all`）与生产库不能是两个形状。"""
    import app.models  # noqa: F401
    from app.db.base import Base

    config, engine = migrated
    command.upgrade(config, "head")
    inspector = sa.inspect(engine)
    for table_name in NEW_TABLES:
        orm_table = Base.metadata.tables[table_name]
        orm_columns = {column.name for column in orm_table.columns}
        assert orm_columns == set(_columns(engine, table_name)), table_name
        orm_indexes = {index.name for index in orm_table.indexes}
        assert orm_indexes <= {index["name"] for index in inspector.get_indexes(table_name)}, table_name
    for table_name, column in ADDED_COLUMNS.items():
        assert column in {c.name for c in Base.metadata.tables[table_name].columns}


@pytest.mark.skipif(not PG_URL, reason="没有 TEST_POSTGRES_URL：PG 分支需要真实实例（测试默认离线）")
def test_postgres_round_trip() -> None:  # pragma: no cover - 需要真实 PG
    config = _config_for(PG_URL)
    engine = sa.create_engine(PG_URL)
    try:
        command.upgrade(config, "head")
        assert set(sa.inspect(engine).get_table_names()) >= NEW_TABLES
        command.downgrade(config, "0006_report_library")
        assert not (NEW_TABLES & set(sa.inspect(engine).get_table_names()))
        command.upgrade(config, "head")
    finally:
        engine.dispose()
