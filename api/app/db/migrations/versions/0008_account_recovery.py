"""账号恢复码 + 会话清理索引（spec §25.6，M53/M55）

Revision ID: 0008_account_recovery
Revises: 0007_users_auth
Create Date: 2026-11-20

纪律同 0006/0007：既有表只 `op.add_column` + `op.create_index`，不 batch、不重建表；
新列可空、无默认、不回填（NULL = 未设置恢复码）。部分索引双写 sqlite_where / postgresql_where。
`downgrade()` 先删索引、后删列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_account_recovery"
down_revision: str | None = "0007_users_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("recovery_hash", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("recovery_set_at", sa.Text(), nullable=True))
    predicate = "revoked_at IS NOT NULL"
    op.create_index(
        "ix_sessions_revoked",
        "sessions",
        ["revoked_at"],
        sqlite_where=sa.text(predicate),
        postgresql_where=sa.text(predicate),
    )
    op.create_index("ix_sessions_idle", "sessions", ["idle_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sessions_idle", table_name="sessions")
    op.drop_index("ix_sessions_revoked", table_name="sessions")
    op.drop_column("users", "recovery_set_at")
    op.drop_column("users", "recovery_hash")
