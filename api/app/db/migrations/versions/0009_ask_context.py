"""报告追问上下文快照定位信息，不复制历史正文。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_ask_context"
down_revision = "0008_account_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_asks",
        sa.Column("context_ask_ids_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_asks", "context_ask_ids_json")
