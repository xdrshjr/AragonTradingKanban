"""`cost_ledger` — an append-only projection of what things actually cost (§19.6.1).

Why a separate table instead of `SUM`ming `runs`: `runs` rows live for
`RUN_RETENTION_DAYS=14`, so any month-to-date total computed over them silently
shrinks on the 15th. The ledger outlives the run records (default 730 days) and
is written in the **same transaction** as the thing it prices.

Two invariants this table carries:

* **`usd IS NULL` means "unknown"**, `0` means "known and free". They are opposite
  facts; collapsing them into 0 is exactly the v4 defect (AC-55).
* `ux_cost_ledger_ref` on `(kind, ref_id)` is the idempotency key: a retried
  insert is rejected by the constraint and counted as `ledgerGap`, never double
  counted.
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

LEDGER_KINDS = ("run", "ask_deep")


class CostLedger(Base):
    __tablename__ = "cost_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: `run` | `ask_deep` — one row per paid operation, never updated.
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: `runs.id` or `report_asks.id`.
    ref_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(24))
    report_id: Mapped[str | None] = mapped_column(String(36))
    model: Mapped[str | None] = mapped_column(String(64))
    #: `NULL` = unknown. Never 0 (see the module docstring).
    usd: Mapped[float | None] = mapped_column(Float)
    #: `Integer`, not `Boolean`: every other boolean column in this schema uses
    #: `Integer` + `server_default="0"` (see `0001_init`), and a PG `BOOLEAN
    #: DEFAULT 1` is a hard DDL error. One convention, one double-branch migration.
    known: Mapped[bool] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # ── ★v7（0004_agent_diagnostics）：估算值与来源三态。
    # `usd_est` 只在 `source='estimated'` 时有值；`reported` 行必须为 NULL
    # （AC-78/AC-86 断言这条互斥性，`can_start` 用 `COALESCE(usd, usd_est)` 求和）。
    usd_est: Mapped[float | None] = mapped_column(Float)
    #: `reported | estimated | unknown`。**字符串三态，不是布尔**（H-11）：
    #: 布尔表达不了三种来源，且 `BOOLEAN DEFAULT 1` 在 PG 上是硬 DDL 错误（I-55）。
    #: 也**不加 ORM `CheckConstraint`**：本项目禁止在应用层建表（`test_boundaries`
    #: 的两条建表守卫），生产表由 Alembic 建、没有 CHECK ⇒ 那样写的结果是"测试库有
    #: 约束、生产库没有"。白名单判定在 `cost_service.record()` 里（非法值记 `ledgerGap`）。
    source: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown", server_default="unknown")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_cost_ledger_created", "created_at"),
        Index("ix_cost_ledger_kind", "kind", "created_at"),
        Index("ix_cost_ledger_symbol", "symbol", "created_at"),
        Index("ix_cost_ledger_source", "source", "created_at"),
        # Retry-safe: the same run can never be counted twice.
        Index("ux_cost_ledger_ref", "kind", "ref_id", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CostLedger {self.kind}:{self.ref_id} usd={self.usd} known={self.known}>"
