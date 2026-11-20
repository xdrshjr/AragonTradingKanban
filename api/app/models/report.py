"""Report — one immutable version of a research document (spec §8.1)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex
    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ready|degraded|provisional|superseded
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    stance: Mapped[str | None] = mapped_column(String(16))  # buy|hold|sell|watch
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)
    doc_json: Mapped[str] = mapped_column(Text, nullable=False)
    doc_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ★v14（U4 / §23.4.1）：内容指纹 `sha1(canonical_json(doc))`。
    #:
    #: 三件事写死在这里，因为它们是纪律而不是实现细节：
    #: 1. `nullable=True` 且**无默认值**：历史行是 NULL，语义是"不知道指纹"（硬约束 5，
    #:    不回填）。`NULL` **不是**"不同"——去重只在两侧都非 NULL 时生效。
    #: 2. `index=True` **不要**加：索引由 `0006_report_library` 显式建（带 `WHERE`），
    #:    这里再加会多出一个没有谓词的重复索引。
    #: 3. 它是一个**派生**列：真源永远是 `doc_json`，写入时与 `doc_json` 分开算（一个给人、
    #:    一个给比较），绝不在模型返回的 doc 里加字段（schema 顶层
    #:    `additionalProperties: false`）。
    doc_sha1: Mapped[str | None] = mapped_column(String(40))
    grounded_ratio: Mapped[float | None] = mapped_column(Float)
    quality_json: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("runs.id", ondelete="SET NULL"))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_known: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    is_latest: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pinned: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    deleted_at: Mapped[str | None] = mapped_column(Text)
    #: ★v15（0007_users_auth / §24.6.2）：生成者。`NULL` = 历史/系统数据，**仅管理员可管**。
    #: 派生自 `run.created_by`（`create_version()` 沿用 `run_id` 链路写入）。
    created_by: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("instrument_id", "version", name="uq_reports_instrument_id_version"),
        # "exactly one latest per instrument" is enforced by the database, not by
        # hope: the writer clears the flag and sets it again in one transaction.
        Index(
            "ux_reports_latest",
            "instrument_id",
            unique=True,
            sqlite_where=text("is_latest = 1"),
            postgresql_where=text("is_latest = 1"),
        ),
        Index("ix_reports_inst_ver", "instrument_id", "version"),
        # 部分索引，与 0006 同纪律。★实施发现（见 spec「实施过程发现的方案缺陷」）：设计稿写的
        # 谓词是 `deleted_at IS NULL`，它与 `ix_reports_feed` **谓词相同**，SQLite 对"无 ORDER BY
        # 的 all/starred 扫描"会改选这个更窄的索引，`test_each_scope_uses_its_partial_index`
        # （O-44）随即变红。加上 `created_by IS NOT NULL`：`created_by = ?` 的查询照样蕴含它，
        # 而 feed/starred 的扫描不再蕴含（NULL 的历史行也不需要这个索引）。
        Index(
            "ix_reports_created_by",
            "created_by",
            sqlite_where=text("created_by IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("created_by IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    @property
    def doc(self) -> dict[str, Any]:
        return json.loads(self.doc_json)

    @property
    def quality(self) -> dict[str, Any]:
        return json.loads(self.quality_json) if self.quality_json else {}

    @property
    def provisional(self) -> bool:
        return self.status == "provisional"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Report {self.id} v{self.version} {self.status}>"
