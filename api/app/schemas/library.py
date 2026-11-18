"""★v14（U1 / §23.4.3）：报告库的响应模型（**不是**表）。

为什么不把字段平铺进 `ReportSummary`：那会让它的 23 个键在库里出现第二份定义
（`schemas/report.py` 与 `schemas/library.py`），这是本项目已经罚过的漂移模式
（`test_the_new_capabilities_fields_reach_the_wire_model` 就是为它写的）。嵌套的代价只是
前端多一层 `item.report.version`。

★硬约束 2：`ReportSummary` **一个字段都不改名、不加键**。本轮要追加的东西
（`docSha1` / `integrity` / `deletedAt`）全部落在**新模型**这一层——新增模型允许追加。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .report import ReportSummary

#: 五个 scope。`latest` **不是**库页的 tab（库页 4 个 tab，默认 `all`）——它服务首页、
#: 命令面板与"工作台默认打开哪一版"。
LibraryScope = Literal["latest", "all", "starred", "archived", "trash"]


class LibraryInstrument(BaseModel):
    """一次 join 拿齐标的，前端不再逐行查 `/instruments/{id}`。"""

    id: str
    symbol: str
    name: str
    market: Literal["CN", "HK", "US"]
    currency: str


class ReportLibraryItem(BaseModel):
    """列表的一行 = 一份不可变版本 + 它的标的 + 两个只读事实。"""

    #: 复用既有模型：一列都不改名、不加键（硬约束 2）。
    report: ReportSummary
    instrument: LibraryInstrument
    #: `None` = 早期版本（`doc_sha1` 不回填，硬约束 5）。**不是**"内容不同"。
    docSha1: str | None = None
    #: ★评审 P1-2：由 SQL `json_valid()` 算出来，不在列表里解析 doc 深层结构。
    #: 语义与详情的 `ReportDetail.integrity` **相同**，成本不同。
    integrity: Literal["ok", "corrupt"] = "ok"
    #: ★评审 P1-13：回收站要显示"删除于 X / 还剩 N 天"。非删除行恒 `None`；
    #: `scope=trash` 里非空。它**不能**放进 `ReportSummary`（硬约束 2），所以在这里。
    deletedAt: str | None = None


class ActiveRunItem(BaseModel):
    """「正在生成」那一行。★评审 P2-8：逐字段对齐既有列，别让人猜。"""

    runId: str
    instrumentId: str
    #: `instruments.symbol` / `instruments.name`（join 来的，不是 runs 表的列）。
    symbol: str
    name: str
    #: `runs.stage`（可空：刚入队时是 `None`）。
    stage: str | None = None
    #: `runs.pct`（非空默认 0）。
    pct: int = 0
    #: `runs.started_at`（Text，ISO `Z`）。
    startedAt: str


class LibraryResponse(BaseModel):
    """`GET /api/v1/reports` 的 200 体。

    三个量分得清清楚楚，别混（§23.3.2）：

    * `counts[scope]` —— **不含** `q` / `instrumentId` 的全库计数（tab 徽章）；
    * `total` —— **当前过滤条件**命中数（"共 N 份"）；`withCounts=0` 时它**仍然准确**；
    * `nextCursor` —— 只在有下一页时非 `null`（keyset，不许 OFFSET）。
    """

    items: list[ReportLibraryItem] = Field(default_factory=list)
    nextCursor: str | None = None
    total: int = 0
    #: 键 = 5 个 scope 名。**不含中文标签**：文案在前端 `copy.ts`，服务端不下发 UI 文案。
    counts: dict[str, int] = Field(default_factory=dict)
    active: list[ActiveRunItem] = Field(default_factory=list)
