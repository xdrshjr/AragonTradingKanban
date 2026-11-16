"""ask / search / schedule — tables, FTS5 virtual tables, backfill

Revision ID: 0002_ask_search_schedule
Revises: 0001_init
Create Date: 2026-02-11

Three rules this migration follows, all of them lessons from the v4 review:

1. **Only the FTS segment is dialect-specific.** `report_asks`, `ask_events` and
   `schedules` are created unconditionally and identically on SQLite and Postgres —
   v3 had the FTS branch raise `NotImplementedError`, which would have made
   `alembic upgrade head` impossible on Postgres and blocked the non-FTS tables
   with it (★V4-P2 note / C-11).
2. **Probe before creating.** A SQLite build without `ENABLE_FTS5` must still
   finish migrating (`fts_available=0`), otherwise the LIKE fallback R-18 promises
   can never be reached because the app refuses to boot (★V4-P1-8).
3. **Backfill through the service, not through SQL.** `body` has to be bigram
   tokenised, so every historical report goes through
   `search_index.reindex_report_payload` — the same projection the runtime uses.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_ask_search_schedule"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ── report_asks ────────────────────────────────────────────────────────
    op.create_table(
        "report_asks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "report_id", sa.String(32), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("answer_json", sa.Text()),
        sa.Column("grounded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float()),
        sa.Column("citations_dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("cost_known", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(48)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text()),
    )
    # Covers the version list *and* the 24h deep quota (V4-P2-9: no mode index).
    op.create_index("ix_asks_report_created", "report_asks", ["report_id", "created_at"])

    # ── ask_events ─────────────────────────────────────────────────────────
    op.create_table(
        "ask_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ask_id", sa.String(40), sa.ForeignKey("report_asks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ux_ask_events_seq", "ask_events", ["ask_id", "seq"], unique=True)

    # ── schedules ──────────────────────────────────────────────────────────
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cron", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("min_age_hours", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("next_run_at", sa.Text(), nullable=False),
        sa.Column("last_run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("claimed_at", sa.Text()),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_schedules_due", "schedules", ["next_run_at"])
    # Partial unique index: at most one *enabled* schedule per instrument. The column
    # is an Integer in both dialects, so `enabled = 1` is the one predicate that is
    # valid everywhere — a bare `enabled` is a type error on Postgres (42804) and
    # would abort `alembic upgrade head`. Must match `models/schedule.py`.
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX ux_schedules_inst_enabled ON schedules(instrument_id) WHERE enabled = 1"
        )
    )

    # ── FTS5 (SQLite only) ─────────────────────────────────────────────────
    from app.services import search_index

    fts_ok = search_index.detect_fts() if dialect == "sqlite" else False
    bind.execute(
        sa.text("INSERT OR REPLACE INTO settings(key, value) VALUES ('fts_available', :value)")
        if dialect == "sqlite"
        else sa.text(
            "INSERT INTO settings(key, value) VALUES ('fts_available', :value) "
            "ON CONFLICT (key) DO UPDATE SET value = :value"
        ),
        {"value": "1" if fts_ok else "0"},
    )
    if fts_ok:
        search_index.ensure_fts_schema(bind)

    # ── backfill (only meaningful when the virtual tables exist) ───────────
    if fts_ok:
        reports = bind.execute(
            sa.text("SELECT id, doc_json, title FROM reports WHERE deleted_at IS NULL")
        ).fetchall()
        for row in reports:
            search_index.reindex_report_payload(bind, str(row[0]), str(row[1] or "{}"), str(row[2] or ""))
        instruments = bind.execute(sa.text("SELECT id, symbol, name FROM instruments")).fetchall()
        for row in instruments:
            search_index.reindex_instrument_payload(bind, str(row[0]), str(row[1]), str(row[2]))


def downgrade() -> None:
    bind = op.get_bind()

    from app.services import search_index

    search_index.drop_fts_schema(bind)
    bind.execute(sa.text("DELETE FROM settings WHERE key = 'fts_available'"))

    op.drop_index("ux_schedules_inst_enabled", table_name="schedules")
    op.drop_index("ix_schedules_due", table_name="schedules")
    op.drop_table("schedules")

    op.drop_index("ux_ask_events_seq", table_name="ask_events")
    op.drop_table("ask_events")

    op.drop_index("ix_asks_report_created", table_name="report_asks")
    op.drop_table("report_asks")
