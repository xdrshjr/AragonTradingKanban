"""用户与权限系统：7 张新表 + 5 个 `add_column` + 部分唯一索引（spec §24.6，M45）

Revision ID: 0007_users_auth
Revises: 0006_report_library
Create Date: 2026-11-20

这是 §24 **唯一**的一次迁移。纪律与 0003–0006 一致（照抄，别自己发明）：

1. 既有表只 `op.add_column` + `op.create_index`，**不 batch、不重建表**。五个新列全部
   `nullable=True` 且**无默认值**、**不回填**：旧行的 `created_by` / `user_id` / `owner_id`
   是 NULL，语义是"系统 / 历史数据"，不是"某个人"（E-7）。
2. 这五列**不挂外键**（SQLite 的 `ADD COLUMN … REFERENCES` 只在默认值为 NULL 时才允许，
   而 Alembic 对命名约束的支持要走 `batch_alter_table`，会整表重建 `reports`——其中
   `doc_json` 是最大的列）。用户永远是**匿名化**而不是物理删除，所以悬空引用不会出现；
   完整性由 `user_service` 保证并由 AC-184 守卫。
3. **root 的种子行不在迁移里写**：它要读环境变量、要哈希、要幂等，见
   `services/bootstrap.py::ensure_root_admin`。
4. `downgrade()` 逆序：先 `drop_index`，再 `drop_column`（SQLite ≥ 3.35 才支持，本机 3.41.2
   实测可用），最后 `drop_table`（`user_watchlist` → … → `users`）。

★ 实施发现：`quota_ledger.ref_id` 用 `String(40)` 而不是 spec DDL 里的 `VARCHAR(32)`——
追问 id 是 `"ask_" + uuid4 hex`（36 字符）。SQLite 不在乎，Postgres 会拒绝/截断。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_users_auth"
down_revision: str | None = "0006_report_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 五个既有表的新列：(表, 列名, 类型)。
ADDED_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("runs", "created_by", 32),
    ("reports", "created_by", 32),
    ("report_asks", "user_id", 32),
    ("schedules", "owner_id", 32),
    ("cost_ledger", "user_id", 32),
)


def _partial(predicate: str) -> dict[str, sa.TextClause]:
    return {"sqlite_where": sa.text(predicate), "postgresql_where": sa.text(predicate)}


def upgrade() -> None:
    # ── 7 张新表 ───────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(20), nullable=False),
        sa.Column("username_lc", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(40)),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("disabled_reason", sa.String(200)),
        sa.Column("disabled_at", sa.Text),
        sa.Column("must_change_password", sa.Integer, nullable=False, server_default="0"),
        sa.Column("password_is_default", sa.Integer, nullable=False, server_default="0"),
        sa.Column("password_changed_at", sa.Text),
        sa.Column("failed_logins", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.Text),
        sa.Column("last_login_at", sa.Text),
        sa.Column("last_login_ip", sa.String(48)),
        sa.Column("quota_daily_limit", sa.Integer),
        sa.Column("quota_deep_daily_limit", sa.Integer),
        sa.Column("invite_id", sa.String(32)),
        sa.Column("created_by", sa.String(32)),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.CheckConstraint("role IN ('root','admin','user')", name="role"),
        sa.CheckConstraint("status IN ('active','disabled','deleted')", name="status"),
    )
    op.create_index("ux_users_username_lc", "users", ["username_lc"], unique=True)
    op.create_index("ux_users_root", "users", ["role"], unique=True, **_partial("role = 'root'"))
    op.create_index("ix_users_list", "users", ["created_at", "id"])
    op.create_index("ix_users_status_role", "users", ["status", "role"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remember", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("last_seen_at", sa.Text, nullable=False),
        sa.Column("idle_expires_at", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("ip", sa.String(48)),
        sa.Column("user_agent", sa.String(200)),
        sa.Column("revoked_at", sa.Text),
        sa.Column("revoked_reason", sa.String(24)),
    )
    op.create_index("ux_sessions_token", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_live", "sessions", ["user_id", "created_at"], **_partial("revoked_at IS NULL"))

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("note", sa.String(80)),
        sa.Column("max_uses", sa.Integer, nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bonus_credits", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.Text),
        sa.Column("disabled_at", sa.Text),
        sa.Column("created_by", sa.String(32)),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_index("ux_invite_code", "invite_codes", ["code"], unique=True)

    op.create_table(
        "quota_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("resource", sa.String(10), nullable=False),
        sa.Column("delta", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("source", sa.String(6), nullable=False),
        sa.Column("day_key", sa.String(10), nullable=False),
        sa.Column("ref_id", sa.String(40)),
        sa.Column("actor_id", sa.String(32)),
        sa.Column("note", sa.String(120)),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.CheckConstraint("resource IN ('report','deep_ask')", name="resource"),
        sa.CheckConstraint("source IN ('daily','bonus')", name="source"),
    )
    op.create_index("ix_quota_user_daily", "quota_ledger", ["user_id", "resource", "source", "day_key"])
    op.create_index(
        "ux_quota_ref_reason",
        "quota_ledger",
        ["ref_id", "reason"],
        unique=True,
        **_partial("reason IN ('run','run_refund','ask','ask_refund')"),
    )

    op.create_table(
        "quota_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("resource", sa.String(10), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("granted", sa.Integer),
        sa.Column("resolved_by", sa.String(32)),
        sa.Column("resolved_at", sa.Text),
        sa.Column("resolution_note", sa.String(200)),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="status"),
    )
    op.create_index(
        "ux_quota_req_pending", "quota_requests", ["user_id"], unique=True, **_partial("status = 'pending'")
    )
    op.create_index("ix_quota_req_queue", "quota_requests", ["status", "created_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", sa.Text, nullable=False),
        sa.Column("actor_id", sa.String(32)),
        sa.Column("actor_name", sa.String(40)),
        sa.Column("actor_role", sa.String(8)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(16)),
        sa.Column("target_id", sa.String(32)),
        sa.Column("target_name", sa.String(40)),
        sa.Column("ip", sa.String(48)),
        sa.Column("request_id", sa.String(16)),
        sa.Column("detail_json", sa.Text),
    )
    op.create_index("ix_audit_at", "audit_log", ["at", "id"])
    op.create_index("ix_audit_action", "audit_log", ["action", "at"])
    op.create_index("ix_audit_actor", "audit_log", ["actor_id", "at"])

    op.create_table(
        "user_watchlist",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "instrument_id", sa.String(32), sa.ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pinned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("added_at", sa.Text, nullable=False),
    )

    # ── 5 个 add_column（可空、无默认值、不回填、不挂 FK） ──────────────────────
    for table, column, length in ADDED_COLUMNS:
        op.add_column(table, sa.Column(column, sa.String(length=length), nullable=True))
    op.create_index("ix_runs_created_by", "runs", ["created_by", "started_at"])
    # ★实施发现：谓词比设计稿多一个 `created_by IS NOT NULL`——否则它与 `ix_reports_feed`
    # 谓词相同，SQLite 会在 all/starred 扫描里改选它（O-44 守卫变红）。
    op.create_index(
        "ix_reports_created_by",
        "reports",
        ["created_by"],
        **_partial("created_by IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index("ix_asks_user", "report_asks", ["user_id", "report_id"])
    op.create_index("ix_cost_user", "cost_ledger", ["user_id", "created_at"])


def downgrade() -> None:
    # 逆序：先索引、再列、最后表（子表在前，`users` 最后）。
    op.drop_index("ix_cost_user", table_name="cost_ledger")
    op.drop_index("ix_asks_user", table_name="report_asks")
    op.drop_index("ix_reports_created_by", table_name="reports")
    op.drop_index("ix_runs_created_by", table_name="runs")
    for table, column, _length in reversed(ADDED_COLUMNS):
        op.drop_column(table, column)

    op.drop_table("user_watchlist")
    op.drop_index("ix_audit_actor", table_name="audit_log")
    op.drop_index("ix_audit_action", table_name="audit_log")
    op.drop_index("ix_audit_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_quota_req_queue", table_name="quota_requests")
    op.drop_index("ux_quota_req_pending", table_name="quota_requests")
    op.drop_table("quota_requests")
    op.drop_index("ux_quota_ref_reason", table_name="quota_ledger")
    op.drop_index("ix_quota_user_daily", table_name="quota_ledger")
    op.drop_table("quota_ledger")
    op.drop_index("ux_invite_code", table_name="invite_codes")
    op.drop_table("invite_codes")
    op.drop_index("ix_sessions_user_live", table_name="sessions")
    op.drop_index("ux_sessions_token", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_users_status_role", table_name="users")
    op.drop_index("ix_users_list", table_name="users")
    op.drop_index("ux_users_root", table_name="users")
    op.drop_index("ux_users_username_lc", table_name="users")
    op.drop_table("users")
