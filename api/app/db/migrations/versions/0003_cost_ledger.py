"""cost_ledger — the append-only cost projection (spec §19.6.1)

Revision ID: 0003_cost_ledger
Revises: 0002_ask_search_schedule
Create Date: 2026-02-12

Three properties this migration has to preserve, each a review lesson:

1. **No FTS involvement.** unlike 0002 there is no virtual table here, so the
   upgrade is dialect-neutral; only the autoincrement spelling differs
   (`INTEGER PRIMARY KEY AUTOINCREMENT` on SQLite, `BIGSERIAL` on Postgres).
2. **`usd` is nullable and stays nullable.** `NULL` = unknown, `0` = known and
   free. A `NOT NULL DEFAULT 0` here would recreate the v4 defect AC-55 guards.
3. **`ux_cost_ledger_ref` is the idempotency key**, so a retried insert is
   rejected by the database instead of double-counting a run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_cost_ledger"
down_revision: str | None = "0002_ask_search_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        id_column: sa.Column = sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True)
    else:
        id_column = sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)

    op.create_table(
        "cost_ledger",
        id_column,
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("ref_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(24)),
        sa.Column("report_id", sa.String(36)),
        sa.Column("model", sa.String(64)),
        sa.Column("usd", sa.Float()),
        # `Integer`, not `Boolean`: every other boolean column in this schema is an
        # `Integer` (`0001_init` :124/:129/:166-172), and PostgreSQL rejects
        # `BOOLEAN NOT NULL DEFAULT 1` outright — the "SQLite/Postgres 双分支"
        # promise would hold on SQLite and die on PG.
        sa.Column("known", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_cost_ledger_created", "cost_ledger", ["created_at"])
    op.create_index("ix_cost_ledger_kind", "cost_ledger", ["kind", "created_at"])
    op.create_index("ix_cost_ledger_symbol", "cost_ledger", ["symbol", "created_at"])
    op.create_index("ux_cost_ledger_ref", "cost_ledger", ["kind", "ref_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_cost_ledger_ref", table_name="cost_ledger")
    op.drop_index("ix_cost_ledger_symbol", table_name="cost_ledger")
    op.drop_index("ix_cost_ledger_kind", table_name="cost_ledger")
    op.drop_index("ix_cost_ledger_created", table_name="cost_ledger")
    op.drop_table("cost_ledger")
