"""迁移 `0006_report_library`：`doc_sha1` + 三个部分索引（spec §23.4.1，M38；AC-147/148）。

三条纪律在这里被机器化，而不是写在注释里：

1. **`revision` / `down_revision` 必须指向真实的 revision id**（V8-P0-2）：写 `"0005"` 会
   让 `alembic upgrade head` 第一步就报 `Can't locate revision identified by '0005'`。
2. **只 `add_column` + `create_index`**：不 batch、不重建表（SQLite 的
   `batch_alter_table` 会 copy 整表 + 换名，而 `reports` 里最大的一列是 `doc_json`）。
3. **`downgrade()` 必须能回滚**（P2-9）：真的跑一次 upgrade → downgrade → upgrade，而不是
   只断言列存在。
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

FEED, STARRED, TRASH = "ix_reports_feed", "ix_reports_starred", "ix_reports_trash"


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    settings = get_settings()
    url = f"sqlite:///{(tmp_path / 'library.db').as_posix()}"
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


def _index_names(engine: sa.Engine, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(engine).get_indexes(table)}


def _index_sql(engine: sa.Engine, name: str) -> str:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:name"), {"name": name}
        ).one_or_none()
    return "" if row is None or row.sql is None else str(row.sql)


def test_upgrade_adds_the_column_and_the_three_partial_indexes(migrated) -> None:
    """三件事各自可断言：列存在且可空、三个索引存在、每个索引的谓词**写进了 DDL**。"""
    config, engine = migrated
    command.upgrade(config, "head")
    assert _revision(engine) == "0006_report_library"

    columns = _columns(engine, "reports")
    assert "doc_sha1" in columns
    assert columns["doc_sha1"]["nullable"] is True, "历史行保持 NULL（硬约束 5：不回填）"
    assert isinstance(columns["doc_sha1"]["type"], sa.String)
    assert columns["doc_sha1"]["type"].length == 40

    indexes = _index_names(engine, "reports")
    assert {FEED, STARRED, TRASH} <= indexes
    # 部分索引：谓词必须在 DDL 里，否则它就是一个更大而没被用到的全表索引。
    assert "WHERE deleted_at IS NULL" in _index_sql(engine, FEED)
    assert "pinned = 1 AND deleted_at IS NULL" in _index_sql(engine, STARRED)
    assert "deleted_at IS NOT NULL" in _index_sql(engine, TRASH)


def test_downgrade_removes_everything_and_upgrade_replays(migrated) -> None:
    """P2-9：`downgrade()` 必须能回滚（SQLite 的 DROP COLUMN 需要 ≥ 3.35，本机满足）。"""
    config, engine = migrated
    command.upgrade(config, "head")
    command.downgrade(config, "0005_engine_probes")
    assert _revision(engine) == "0005_engine_probes"
    assert "doc_sha1" not in _columns(engine, "reports")
    assert not ({FEED, STARRED, TRASH} & _index_names(engine, "reports"))

    command.upgrade(config, "head")
    assert _revision(engine) == "0006_report_library"
    assert "doc_sha1" in _columns(engine, "reports")
    assert {FEED, STARRED, TRASH} <= _index_names(engine, "reports")

    # 再从 head 一路降回 base：整条链没有断点。
    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}


def test_revision_ids_are_the_real_ones_not_abbreviations() -> None:
    script = ScriptDirectory.from_config(_config_for("sqlite://"))
    assert script.get_current_head() == "0006_report_library"
    revision = script.get_revision("0006_report_library")
    assert revision is not None
    assert revision.down_revision == "0005_engine_probes"
    assert script.get_revision(str(revision.down_revision)) is not None


def test_the_migration_never_rebuilds_the_table() -> None:
    """`reports` 里最大的一列是 `doc_json`，重建表会锁库（R-65）。

    这条断言守的是一个**诱惑**：加索引时顺手 `batch_alter_table` 一次。
    """
    source = (MIGRATIONS_DIR / "versions" / "0006_report_library.py").read_text(encoding="utf-8")
    # 断言的是**调用形态**（`(`），不是词：文件头会解释"为什么不用 batch"，那是文档。
    assert "batch_alter_table(" not in source
    assert "drop_table(" not in source
    assert "op.add_column(" in source


@pytest.mark.skipif(not PG_URL, reason="没有 TEST_POSTGRES_URL：PG 分支需要真实实例（测试默认离线）")
def test_postgres_round_trip() -> None:  # pragma: no cover - 需要真实 PG
    config = _config_for(PG_URL)
    engine = sa.create_engine(PG_URL)
    try:
        command.upgrade(config, "head")
        assert "doc_sha1" in _columns(engine, "reports")
        command.downgrade(config, "0005_engine_probes")
        assert "doc_sha1" not in _columns(engine, "reports")
        command.upgrade(config, "head")
    finally:
        engine.dispose()


# ── O-44：三个 scope 的 `EXPLAIN QUERY PLAN` 断言（确定性，不是时序）─────────────
#
# 为什么挂在这里而不是 `test_report_library_service.py`：测试库是 `conftest.engine` 用
# 建表助手建的，而三个部分索引只存在于**迁移**里。索引断言必须对着"真的有索引"的库跑，
# 否则它测的是"这个索引不存在"——一条永远红或者永远无意义的断言。


@pytest.fixture()
def library_session(migrated):
    """一个跑完 `0006` 的库 + 几行真实数据（`reports` 的行内约束一个都不能少）。"""
    from sqlalchemy.orm import Session as OrmSession

    from app.models import Instrument, Report

    config, engine = migrated
    command.upgrade(config, "head")
    session = OrmSession(bind=engine)
    session.add(
        Instrument(
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
    )
    for version, (deleted_at, pinned, archived) in enumerate(
        [(None, 0, 0), (None, 1, 0), ("2026-11-01T00:00:00Z", 0, 0)], start=1
    ):
        payload = '{"headline":"白酒动销回暖","asOf":"2026-11-17"}'
        session.add(
            Report(
                id=f"{version:032x}",
                instrument_id="CN:600519.SH",
                version=version,
                status="ready" if deleted_at is None else "superseded",
                title="贵州茅台 · 投研报告",
                as_of="2026-11-17",
                generated_at=f"2026-11-1{version}T00:00:00Z",
                doc_json=payload,
                doc_bytes=len(payload.encode("utf-8")),
                is_latest=1 if version == 3 else 0,
                pinned=pinned,
                archived=archived,
                deleted_at=deleted_at,
            )
        )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _query_plan(session, scope: str) -> str:
    from app.services import report_library

    statement = report_library._base_statement(scope=scope, q="", instrument_id=None, session=session)
    sql = str(statement.compile(session.get_bind(), compile_kwargs={"literal_binds": True}))
    rows = session.execute(sa.text(f"EXPLAIN QUERY PLAN {sql}")).all()
    return " | ".join(str(row[-1]) for row in rows)


@pytest.mark.parametrize(
    ("scope", "index_name"),
    [("all", FEED), ("starred", STARRED), ("trash", TRASH)],
)
def test_each_scope_uses_its_partial_index(library_session, scope: str, index_name: str) -> None:
    """`all/starred/trash` 必须分别用上自己的部分索引。

    部分索引只有在"查询谓词蕴含索引谓词"时才会被选中 ⇒ 这条断言同时证明了
    `ix_reports_feed` 的谓词是 `deleted_at IS NULL`（而不是更窄的
    `deleted_at IS NULL AND archived = 0`，后者会让"更弱的谓词"用不上它——P0-2 的连带修正）。
    """
    plan = _query_plan(library_session, scope)
    assert index_name in plan, f"{scope} 没有用上 {index_name}：{plan}"


def test_latest_scope_is_indexed_but_through_the_version_index(library_session) -> None:
    """`scope=latest` 的窗口函数**不会**用 `ix_reports_feed`，用的是 `ix_reports_inst_ver`。

    ★实施发现（见 spec 的「实施过程发现的方案缺陷」）：设计稿 §23.5.1 断言
    `scope=latest` 的 CTE 必须显示 `SEARCH reports USING INDEX ix_reports_feed`，实测在
    SQLite 3.4x 上**用的是 `ix_reports_inst_ver (instrument_id, version)`**——因为那条语句
    是 `row_number() over (partition by instrument_id order by is_latest desc, version desc)`，
    优化器优先选能消掉 `partition by` 排序的那个索引。这是**更好**的结果（省一次排序），
    而不是缺陷，所以断言改成"必须走索引，且不是全表扫描"：

    * 断言 `"USING INDEX ix_reports"` 出现 ⇒ 它不是 `SCAN reports`（无索引全表扫描）；
    * 断言 `"USE TEMP B-TREE FOR RIGHT PART OF ORDER BY"` 出现 ⇒ 只有右半段排序留在内存里，
      `partition by` 的排序已被索引消掉。

    换句话说：**"用了哪个索引"这件事不能靠设计稿断言，只能靠 EXPLAIN 读**。
    """
    plan = _query_plan(library_session, "latest")
    assert "ix_reports_inst_ver" in plan, f"latest 的窗口子查询应该走版本索引：{plan}"
    assert "SCAN reports USING INDEX" in plan, plan
    assert "SCAN reports\n" not in plan and plan.strip() != "SCAN reports", plan
