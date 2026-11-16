"""initial schema — every table in spec §8.1

Revision ID: 0001_init
Revises:
Create Date: 2026-02-10

Table creation order matters: `runs.report_id -> reports.id` and
`reports.run_id -> runs.id` are circular. SQLite resolves forward references at
DML time so either order works there, but Postgres (the documented alternative
`DATABASE_URL`) needs `runs` first — `reports.run_id` then points backwards at an
existing table and `runs.report_id` is added by a follow-up ALTER in the same
migration (★P1-1 note in §8.1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ── instruments ────────────────────────────────────────────────────────
    op.create_table(
        "instruments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("exchange", sa.String(16)),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("sector", sa.String(64)),
        sa.Column("tz", sa.String(48), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_instruments_market_symbol", "instruments", ["market", "symbol"])

    # ── symbol_aliases ─────────────────────────────────────────────────────
    op.create_table(
        "symbol_aliases",
        sa.Column("alias", sa.String(64), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_alias_instrument", "symbol_aliases", ["instrument_id"])

    # ── bars ───────────────────────────────────────────────────────────────
    op.create_table(
        "bars",
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("timeframe", sa.String(4), primary_key=True),
        sa.Column("ts", sa.Integer(), primary_key=True),
        sa.Column("adjust", sa.String(8), primary_key=True, server_default="qfq"),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Float()),
        sa.Column("source", sa.String(24), nullable=False),
    )
    op.create_index("ix_bars_query", "bars", ["instrument_id", "timeframe", "adjust", "ts"])

    # ── quotes ─────────────────────────────────────────────────────────────
    op.create_table(
        "quotes",
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last", sa.Float()),
        sa.Column("change_pct", sa.Float()),
        sa.Column("prev_close", sa.Float()),
        sa.Column("day_high", sa.Float()),
        sa.Column("day_low", sa.Float()),
        sa.Column("volume", sa.Float()),
        sa.Column("source", sa.String(24)),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )

    # ── runs (before reports: reports.run_id points at it) ─────────────────
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_id", sa.String(32)),  # FK added after `reports` exists
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16)),
        sa.Column("pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pid", sa.Integer()),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("error_code", sa.String(48)),
        sa.Column("error_message", sa.Text()),
        sa.Column("aragon_session_id", sa.String(64)),
        sa.Column("workspace_path", sa.Text()),
        sa.Column("log_path", sa.Text()),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("cost_known", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text()),
        sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(16)),
    )
    op.create_index("ix_runs_instrument_started", "runs", ["instrument_id", "started_at"])
    op.create_index(
        "ux_runs_active",
        "runs",
        ["instrument_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','running','cancelling')"),
        postgresql_where=sa.text("status IN ('queued','running','cancelling')"),
    )

    # ── reports ────────────────────────────────────────────────────────────
    op.create_table(
        "reports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("stance", sa.String(16)),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.Text(), nullable=False),
        sa.Column("doc_json", sa.Text(), nullable=False),
        sa.Column("doc_bytes", sa.Integer(), nullable=False),
        sa.Column("grounded_ratio", sa.Float()),
        sa.Column("quality_json", sa.Text()),
        sa.Column("provider", sa.String(32)),
        sa.Column("model", sa.String(64)),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("cost_known", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("is_latest", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.Text()),
        sa.UniqueConstraint("instrument_id", "version", name="uq_reports_instrument_id_version"),
    )
    op.create_index("ix_reports_inst_ver", "reports", ["instrument_id", "version"])
    op.create_index(
        "ux_reports_latest",
        "reports",
        ["instrument_id"],
        unique=True,
        sqlite_where=sa.text("is_latest = 1"),
        postgresql_where=sa.text("is_latest = 1"),
    )

    # close the circular reference: runs.report_id -> reports.id (SET NULL)
    if is_sqlite:
        # SQLite cannot ADD CONSTRAINT; the batch mode rebuilds the table so the
        # FK is real. Enforcement is per-connection and comes from
        # app/db/session.py (`PRAGMA foreign_keys=ON` on every connect).
        with op.batch_alter_table("runs") as batch:
            batch.create_foreign_key("fk_runs_report_id_reports", "reports", ["report_id"], ["id"], ondelete="SET NULL")
    else:
        op.create_foreign_key(
            "fk_runs_report_id_reports", "runs", "reports", ["report_id"], ["id"], ondelete="SET NULL"
        )

    # ── run_events ─────────────────────────────────────────────────────────
    op.create_table(
        "run_events",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_events_run", "run_events", ["run_id", "seq"])

    # ── watchlist / settings ───────────────────────────────────────────────
    op.create_table(
        "watchlist",
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("watchlist")
    op.drop_index("ix_events_run", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ux_reports_latest", table_name="reports")
    op.drop_index("ix_reports_inst_ver", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ux_runs_active", table_name="runs")
    op.drop_index("ix_runs_instrument_started", table_name="runs")
    op.drop_table("runs")
    op.drop_table("quotes")
    op.drop_index("ix_bars_query", table_name="bars")
    op.drop_table("bars")
    op.drop_index("ix_alias_instrument", table_name="symbol_aliases")
    op.drop_table("symbol_aliases")
    op.drop_index("ix_instruments_market_symbol", table_name="instruments")
    op.drop_table("instruments")
