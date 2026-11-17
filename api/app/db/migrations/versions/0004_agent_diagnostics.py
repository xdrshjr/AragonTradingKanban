"""agent 诊断与成本三态 —— 9 个新列 + 1 个索引（spec §20.6.1，M20/M21）

Revision ID: 0004_agent_diagnostics
Revises: 0003_cost_ledger
Create Date: 2026-02-20

四条与 0003 同源的纪律（照抄，别自己发明）：

1. **`batch_alter_table`**：SQLite 的 `ADD COLUMN` 只能一次一列且不能改约束，
   `batch` 让它与 PostgreSQL 走同一条路径。
2. **布尔列一律 `Integer` + `server_default="0"`**：`Boolean` + `DEFAULT 1` 在 PG 上是
   硬 DDL 错误（I-55 的教训）。本轮**不新增布尔列**——`cost_ledger.source` 用字符串
   三态，正是为了避开这个坑（H-11）。
3. **不在 `ALTER` 里加 CHECK 约束**：SQLite 不支持给已有表加 CHECK。
   ★v8（V8-P1-7）**也不要把这条约束写进 `models/cost.py` 的 `CheckConstraint`**：
   v7 原文说"建表时生效，测试库走建表助手也会生效"，但本项目 `api/app` 里
   **禁止在应用层建表**（`test_boundaries.py` 的两条建表守卫，连那个助手名字都不许
   出现），生产表由 0003 建、**没有** CHECK ⇒ 那样写的结果是"测试库里约束存在、生产库
   里不存在"，正好把 README 的"schema 只有一个真源：Alembic"反过来用。做法改为：
   `COST_SOURCES` 白名单在 `cost_service.record()` 写入前判定，非法值记 `ledgerGap`
   （AC-86 覆盖）。**下一个人不要"顺手补上"，那会在 PG 上炸。**
4. **`server_default` 必须给**：`cost_ledger.source` 非空，老行靠默认值 `'unknown'`
   落地——**老行的 `source='unknown'` 是正确事实**（当时确实不知道），不是占位符。

★v8（V8-P0-2）：`revision` / `down_revision` 必须与既有文件**同形**，且指向**真实的**
revision id。0003 里写的是 `revision = "0003_cost_ledger"` / `down_revision =
"0002_ask_search_schedule"`；写成 `"0003"` 会让 `alembic upgrade head` 第一步就报
`Can't locate revision identified by '0003'`（AC-87 就是守这条）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_agent_diagnostics"
down_revision: str | None = "0003_cost_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 升级时要加、降级时要删的列（顺序无关，但两个方向必须完全对称）。
RUN_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("structured_attempts", sa.Integer()),
    sa.Column("agent_provider", sa.String(32)),
    sa.Column("agent_model", sa.String(64)),
    sa.Column("failure_fingerprint", sa.String(12)),
    sa.Column("cost_usd_est", sa.Float()),
    sa.Column("cost_source", sa.String(12)),
)

LEDGER_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("usd_est", sa.Float()),
    sa.Column("source", sa.String(12), nullable=False, server_default="unknown"),
)


def upgrade() -> None:
    # runs：诊断与成本三态。全部可空或有默认值 —— 老行不需要回填（不回填是纪律）。
    with op.batch_alter_table("runs") as batch:
        for column in RUN_COLUMNS:
            batch.add_column(column)
    # cost_ledger：估算值与来源。`usd` / `known` 语义不变（AC-55 一字不改）。
    with op.batch_alter_table("cost_ledger") as batch:
        for column in LEDGER_COLUMNS:
            batch.add_column(column)
    op.create_index("ix_cost_ledger_source", "cost_ledger", ["source", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_cost_ledger_source", table_name="cost_ledger")
    with op.batch_alter_table("cost_ledger") as batch:
        for column in reversed(LEDGER_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("runs") as batch:
        for column in reversed(RUN_COLUMNS):
            batch.drop_column(column.name)
