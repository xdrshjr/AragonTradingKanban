"""`GET /reports/{reportId}/blocks` 的响应形状（spec §19.5.2，M15）。

字段名是前端 `web/lib/types.ts::BlockRangeResponse` 的镜像，两侧逐字一致
（`web/tests/contract.test.ts` 的镜像断言守着）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RangeWindow(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True)


class BlockHit(BaseModel):
    """一个"谈论了这段时间"的块。"""

    blockId: str
    sectionId: str
    sectionTitle: str = ""
    #: 块的类型（`paragraph` / `table` / …），供 UI 决定图标。
    kind: str = "paragraph"
    #: 与请求区间的**自然日**重叠天数（闭区间）。
    overlapDays: int = 0
    #: 命中的区间精度：`day` / `month` / `quarter` / `half` / `year` / `relative`。
    matchKind: str = "relative"
    #: ≤ 160 字摘要（直接来自 `block_text.block_to_text` 的切片）。
    text: str = ""


class BlockRangeResponse(BaseModel):
    reportId: str
    range: RangeWindow
    items: list[BlockHit] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False
    #: 报告自己的时点（相对期间的锚点），也是 AC-53 "永久可复现"的凭据。
    asOf: str = ""
    #: 明确说明"为什么是空的"，而不是让用户以为坏了（`provisional_no_dates`）。
    reason: str | None = None
