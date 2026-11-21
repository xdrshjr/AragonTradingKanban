"""Research workflow templates, immutable versions and the admin assistant's chat (spec §6.1).

`workflow_versions` is **append-only**: the service layer has no UPDATE/DELETE path for it
(`test_workflow_service.py::test_versions_are_append_only`). The partial unique index
`ux_workflow_default` is declared here as well as in the migration so the test-schema builder (conftest)
database enforces "at most one default" too — otherwise the uniqueness tests would be vacuous.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    is_builtin: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_default: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: `active | archived`
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", server_default="active")
    draft_doc_json: Mapped[str] = mapped_column(Text, nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    #: The latest published version; `NULL` = never published.
    current_version_id: Mapped[str | None] = mapped_column(String(32))
    #: Reserved for a later "route by market / instrument type" iteration; always `NULL` for now.
    tags_json: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index(
            "ux_workflow_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default = 1"),
        ),
        Index("ix_workflow_templates_status", "status", "updated_at"),
    )


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_templates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Already stripped of `viewport`.
    doc_json: Mapped[str] = mapped_column(Text, nullable=False)
    doc_sha1: Mapped[str] = mapped_column(String(12), nullable=False)
    change_note: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    step_count: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("template_id", "version", name="template_version"),)


class WorkflowChatMessage(Base):
    __tablename__ = "workflow_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_templates.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: `user | assistant`
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: assistant only: `{ops, summary, baseRevision, revision, applied}`
    applied_json: Mapped[str | None] = mapped_column(Text)
    assist_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_workflow_chat", "template_id", "user_id", "id"),)
