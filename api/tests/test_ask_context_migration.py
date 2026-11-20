"""真实 SQLite 0008 ↔ 0009，保留追问、答案、所有权及事件。"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import ask_context

MIGRATIONS = Path(__file__).resolve().parents[1] / "app/db/migrations"


def _config(url):
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _seed_old_rows(engine):
    # Use ordinary connections, as the existing migration fixtures do.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO report_asks "
                "(id, report_id, question, mode, status, answer_json, "
                "created_at, user_id) VALUES "
                "('old', 'r', '历史问题', 'deep', 'done', :answer, "
                "'2026', 'u1')"
            ),
            {"answer": '{"answerText":"保留"}'},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ask_events "
                "(ask_id, seq, type, payload_json, created_at) VALUES "
                "('old', 1, 'ask.done', :payload, '2026')"
            ),
            {"payload": '{"askId":"old","grounded":true}'},
        )


def _assert_old_rows_unchanged(engine):
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT question, answer_json, user_id FROM report_asks "
                "WHERE id='old'"
            )
        ).one()
        assert tuple(row) == ("历史问题", '{"answerText":"保留"}', "u1")
        event = conn.execute(
            sa.text(
                "SELECT ask_id, seq, type, payload_json, created_at "
                "FROM ask_events"
            )
        ).one()
        assert tuple(event) == (
            "old", 1, "ask.done", '{"askId":"old","grounded":true}', "2026"
        )


def _context_value(engine):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT context_ask_ids_json FROM report_asks WHERE id='old'"
            )
        ).scalar_one()


def test_real_old_database_roundtrip_retains_answers(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'context.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = _config(url)
    engine = sa.create_engine(url)
    try:
        command.upgrade(config, "0008_account_recovery")
        with Session(engine) as db:
            assert ask_context.available(db) is False
        _seed_old_rows(engine)
        command.upgrade(config, "head")
        with Session(engine) as db:
            assert ask_context.available(db) is True
        column = next(
            c
            for c in sa.inspect(engine).get_columns("report_asks")
            if c["name"] == "context_ask_ids_json"
        )
        assert column["nullable"] and column["default"] is None
        _assert_old_rows_unchanged(engine)
        assert _context_value(engine) is None
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE report_asks SET context_ask_ids_json=:ids "
                    "WHERE id='old'"
                ),
                {"ids": '["previous"]'},
            )
        assert _context_value(engine) == '["previous"]'
        command.downgrade(config, "0008_account_recovery")
        assert "context_ask_ids_json" not in {
            c["name"] for c in sa.inspect(engine).get_columns("report_asks")
        }
        _assert_old_rows_unchanged(engine)
        command.upgrade(config, "head")
        _assert_old_rows_unchanged(engine)
        assert _context_value(engine) is None
    finally:
        engine.dispose()


def test_context_migration_has_single_head_and_only_nullable_column():
    script = ScriptDirectory.from_config(_config("sqlite://"))
    assert script.get_heads() == ["0009_ask_context"]
    assert (
        script.get_revision("0009_ask_context").down_revision
        == "0008_account_recovery"
    )
    source = (MIGRATIONS / "versions/0009_ask_context.py").read_text(
        encoding="utf-8"
    )
    assert source.count("op.add_column(") == 1
    assert "batch_alter_table" not in source
    assert "op.drop_column(" in source
