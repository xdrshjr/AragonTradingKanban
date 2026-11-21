"""研究流程图：三张新表 + 偏唯一索引 + runs 三列（spec report-workflow-graph §6.1，M60）

Revision ID: 0010_workflow_graph
Revises: 0009_ask_context

纪律与 0003–0009 一致：既有表只 `op.add_column` + `op.create_index`，不 batch、不重建表；三个新列
全部可空、无默认、**不回填**（老 run 的 `workflow_*` 是 NULL = "未使用流程"）；`runs.workflow_version_id`
不挂外键（版本只追加、不物理删除，同 `created_by` 的理由）。
`ux_workflow_default` 是**偏唯一索引**：全站至多一个默认模板由数据库保证，而不是服务层的愿望。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_workflow_graph"
down_revision: str | None = "0009_ask_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("is_builtin", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_default", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("draft_doc_json", sa.Text, nullable=False),
        sa.Column("draft_revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("current_version_id", sa.String(32)),
        sa.Column("tags_json", sa.Text),
        sa.Column("created_by", sa.String(32)),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_by", sa.String(32)),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_index(
        "ux_workflow_default",
        "workflow_templates",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
        postgresql_where=sa.text("is_default = 1"),
    )
    op.create_index("ix_workflow_templates_status", "workflow_templates", ["status", "updated_at"])

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(32),
            sa.ForeignKey(
                "workflow_templates.id",
                ondelete="CASCADE",
                name="fk_workflow_versions_template_id_workflow_templates",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("doc_json", sa.Text, nullable=False),
        sa.Column("doc_sha1", sa.String(12), nullable=False),
        sa.Column("change_note", sa.String(200), nullable=False, server_default=""),
        sa.Column("step_count", sa.Integer, nullable=False),
        sa.Column("plan_bytes", sa.Integer, nullable=False),
        sa.Column("published_by", sa.String(32)),
        sa.Column("published_at", sa.Text, nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_workflow_versions_template_version"),
    )

    op.create_table(
        "workflow_chat_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "template_id",
            sa.String(32),
            sa.ForeignKey(
                "workflow_templates.id",
                ondelete="CASCADE",
                name="fk_workflow_chat_messages_template_id_workflow_templates",
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("applied_json", sa.Text),
        sa.Column("assist_id", sa.String(32)),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_index("ix_workflow_chat", "workflow_chat_messages", ["template_id", "user_id", "id"])

    op.add_column("runs", sa.Column("workflow_version_id", sa.String(32), nullable=True))
    op.add_column("runs", sa.Column("workflow_label", sa.String(80), nullable=True))
    op.add_column("runs", sa.Column("workflow_adherence", sa.String(10), nullable=True))
    op.create_index("ix_runs_workflow", "runs", ["workflow_version_id", "started_at"])

    # `cost_ledger.kind` 是 VARCHAR(12)，而新的 kind `workflow_assist` 有 15 个字符。SQLite 不检查
    # 长度（也就不必为此重建表）；Postgres 会拒绝，所以只在非 SQLite 方言上放宽。
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column(
            "cost_ledger", "kind", existing_type=sa.String(12), type_=sa.String(24), existing_nullable=False
        )


def downgrade() -> None:
    op.drop_index("ix_runs_workflow", table_name="runs")
    op.drop_column("runs", "workflow_adherence")
    op.drop_column("runs", "workflow_label")
    op.drop_column("runs", "workflow_version_id")
    op.drop_index("ix_workflow_chat", table_name="workflow_chat_messages")
    op.drop_table("workflow_chat_messages")
    op.drop_table("workflow_versions")
    op.drop_index("ix_workflow_templates_status", table_name="workflow_templates")
    op.drop_index("ux_workflow_default", table_name="workflow_templates")
    op.drop_table("workflow_templates")
