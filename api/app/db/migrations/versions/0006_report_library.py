"""`reports.doc_sha1` + 三个部分索引 —— 报告库的**唯一**一次迁移（spec §23.4.1，M38）

Revision ID: 0006_report_library
Revises: 0005_engine_probes
Create Date: 2026-11-18

四条与 0003/0004/0005 同源的纪律（照抄，别自己发明）：

1. **只用 `op.add_column` + `op.create_index`**（不 batch、不重建表）。`doc_sha1` 是
   nullable 且**无默认值**，SQLite 的 `ADD COLUMN` 是 O(1)；而 `batch_alter_table` 会
   copy 整表 + 换名，`reports` 里最大的一列是 `doc_json`（本机 15 行 ≈ 200 KB，生产会更大）。
2. **`nullable=True` 且无默认值**：历史行保持 NULL，语义是"不知道指纹"。★硬约束 5
   （V11-P0-1 的延续）——**不回填**。去重只在两侧都非 NULL 时生效，所以 NULL ≠ "不同"。
3. **三个索引全部是部分索引**（带 `WHERE`）：`deleted_at IS NULL` 是几乎所有列表查询的
   公共前缀，全表索引会比它大一倍且没被用到。
4. **不建唯一索引**：同一份内容可以是两个版本（用户明确 `dedupe=false`，或两次生成之间
   `asOf` 相同但模型措辞不同）。唯一约束会把一次正常生成变成 `IntegrityError`。

★评审 P0-2 的连带修正：`ix_reports_feed` 的谓词只能是 `deleted_at IS NULL`。
`scope=latest` 与 `latest_of()` 同谓词（**不筛 `archived`**），而 `scope=all` 的谓词更强
（`deleted_at IS NULL AND archived = 0`）；SQLite/PG 都能在"查询谓词比索引谓词更强"时
使用部分索引，所以首页（latest）与库页（all）**共用这一个索引**，不需要第二个。

★评审 P2-9：`downgrade()` 必须能回滚。先 `drop_index` 三个，再 `drop_column`。
SQLite 的 `DROP COLUMN` 需要 **≥ 3.35**（本机 3.41.2 实测可用；部署机同为 Ubuntu 24.04 /
Python 3.12 自带版本）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_report_library"
down_revision: str | None = "0005_engine_probes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 三个部分索引的 (名字, 列, 谓词)。升级与降级必须完全对称。
PARTIAL_INDEXES: tuple[tuple[str, list[str], str], ...] = (
    # feed：首页（scope=latest）与库页（scope=all）共用的时间序。
    ("ix_reports_feed", ["generated_at", "id"], "deleted_at IS NULL"),
    # starred：`scope=starred`。
    ("ix_reports_starred", ["generated_at"], "pinned = 1 AND deleted_at IS NULL"),
    # trash：`scope=trash` 与 `purge_trash()` 的扫描窗口。
    ("ix_reports_trash", ["deleted_at"], "deleted_at IS NOT NULL"),
)


def upgrade() -> None:
    op.add_column("reports", sa.Column("doc_sha1", sa.String(length=40), nullable=True))
    for name, columns, predicate in PARTIAL_INDEXES:
        op.create_index(
            name,
            "reports",
            columns,
            sqlite_where=sa.text(predicate),
            postgresql_where=sa.text(predicate),
        )


def downgrade() -> None:
    for name, _columns, _predicate in reversed(PARTIAL_INDEXES):
        op.drop_index(name, table_name="reports")
    op.drop_column("reports", "doc_sha1")
