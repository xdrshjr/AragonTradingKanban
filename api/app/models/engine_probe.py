"""`engine_probes` — 引擎自检的**只追加**记录（★v9 K2 / §21.6.1）。

为什么单独一张表而不是给 `runs` 加列：探针**不是**一次 run（不进报告列表、不占并发位、
不写账本）。把它的字段塞进 `runs` 会让"runs 的每一行都是一份报告尝试"这条不变式破掉，
而这条不变式被 `run_manager` 的冲突判定与 `runs` 列表接口共同依赖。

为什么只追加：探针的历史本身就是"这个端点什么时候好过"的时间序列，覆盖写会毁掉唯一
有价值的信号。保留期与 `RUN_RETENTION_DAYS` 同窗口，另有硬上限（见 `services.agent_probe`）。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

#: `reason` 的取值域（谁触发的这次自检）。
PROBE_REASONS = ("manual", "pre_run", "panel")


class EngineProbe(Base):
    __tablename__ = "engine_probes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    #: `Integer` + `server_default`，不是 `Boolean`（0004 纪律 2：PG 上的
    #: `BOOLEAN DEFAULT 1` 是硬 DDL 错误；这套 schema 里每一个布尔列都是 Integer）。
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: 失败时的 run 级码；成功为 `NULL`。
    code: Mapped[str | None] = mapped_column(String(32))
    ms: Mapped[int | None] = mapped_column(Integer)
    turns: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    structure_attempts: Mapped[int | None] = mapped_column(Integer)
    cli: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    #: 只存 host（**永不** path/query）：`baseUrl` 里可能被网关拼进 token。
    base_url_host: Mapped[str | None] = mapped_column(String(128))
    #: 探针 schema 的 12 位 sha1：它变了，上一条验证就不再代表现在。
    schema_sha1: Mapped[str | None] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(String(16))
    #: 已打码的 message（≤1000 字符）。
    detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_engine_probes_created_at", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EngineProbe {self.id} ok={self.ok} code={self.code} ms={self.ms}>"
