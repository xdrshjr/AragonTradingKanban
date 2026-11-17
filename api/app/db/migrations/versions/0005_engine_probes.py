"""`engine_probes` —— 一张**只追加**的表（spec §21.6.1，K2/M26）

Revision ID: 0005_engine_probes
Revises: 0004_agent_diagnostics
Create Date: 2026-02-21

三条与 0003/0004 同源的纪律（照抄，别自己发明）：

1. **布尔列一律 `Integer` + `server_default="0"`**：`Boolean` + `DEFAULT 1` 在 PG 上是
   硬 DDL 错误（I-55 的教训）。
2. **`down_revision` 必须指向真实的 revision id**（V8-P0-2）：0004 写的是
   `revision = "0004_agent_diagnostics"`，写成 `"0004"` 会让 `alembic upgrade head`
   第一步就报 `Can't locate revision identified by '0004'`。
3. **新表用 `op.create_table`**（没有需要 `batch_alter_table` 的既有表）。

为什么是独立表而不是给 `runs` 加列：探针不是一次 run（不进报告列表、不占并发位、
不写账本），把它塞进 `runs` 会破掉"runs 的每一行都是一份报告尝试"这条不变式。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_engine_probes"
down_revision: str | None = "0004_agent_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_probes",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        # 布尔一律 Integer + server_default（0004 纪律 2）。
        sa.Column("ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("code", sa.String(32)),
        sa.Column("ms", sa.Integer()),
        sa.Column("turns", sa.Integer()),
        sa.Column("tokens_in", sa.Integer()),
        sa.Column("tokens_out", sa.Integer()),
        sa.Column("structure_attempts", sa.Integer()),
        sa.Column("cli", sa.String(32)),
        sa.Column("provider", sa.String(32)),
        sa.Column("model", sa.String(64)),
        # 只存 host：`baseUrl` 里可能被网关拼进 token（§21.5.1 的第二条约束）。
        sa.Column("base_url_host", sa.String(128)),
        sa.Column("schema_sha1", sa.String(12)),
        sa.Column("reason", sa.String(16)),
        # 已打码的 message（≤1000 字符）。
        sa.Column("detail", sa.Text()),
    )
    op.create_index("ix_engine_probes_created_at", "engine_probes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_engine_probes_created_at", table_name="engine_probes")
    op.drop_table("engine_probes")
