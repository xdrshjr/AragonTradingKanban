"""★v14（U1 / §23.3.2）：报告库的**读路径唯一实现**。

它替代的是两件既有事实，而不是再叠一层：

1. **首页的 N+1**（E-126：`1 × /watchlist + 1 × /instruments + 最多 12 × latest`，最坏
   14 次串行 RSC fetch）。现在首页只要一次 `GET /reports?scope=latest&limit=6`。
2. **前端的筛选与排序**：5 个 scope 的谓词、keyset 游标、计数全在服务端算一次，
   前端只渲染。

三条**必须**写死在这里的纪律：

* **排序的唯一规则**是 `ORDER BY generated_at DESC, id DESC`（**不做** pinned-first：
  一条置顶不该把 4 小时前的新报告压到屏幕外。工作台的版本切换器保持
  `pinned desc, version desc` 不变——那是另一个阅读场景）。
* **`counts` 与列表同源**：5 个数字与列表用**同一个**谓词构造器算出来，不允许列表一套
  where、计数另一套（这是本项目历史上最贵的一类漂移）。但"同源"不等于"同一个数"：
  `counts[scope]` **不含** `q`/`instrumentId`（tab 徽章必须恒定，否则用户一输入就看见
  "全部 1 / 星标 0"），`total` **含**过滤条件（"共 N 份"必须联动）。
* **游标是"原样透传的列文本"，不是时间对象**：`generated_at` 是 `Text` 列、由
  `report_service._iso()` 写成 `2026-11-18T03:23:57Z` 这种固定形态、秒级、`Z` 结尾。
  服务端**只做正则校验、不做任何时间解析/归一化**——一次
  `datetime.fromisoformat(x).isoformat()` 往返会把 `Z` 变成 `+00:00`，于是
  `generated_at < '...T10:10:22+00:00'` 在整个 `T`–`+` 之间比较出错误结果，**整段漏行**
  且不报错（★P1-3 / R-76）。秒级精度还意味着"同一秒的两份报告"必然并列，所以 `id` 的
  tie-break 是正确性的一部分，不是可选项。

`scope=latest` 与 `report_service.latest_of()` **逐字同形**（★P0-2）：
`deleted_at IS NULL`，**不筛 `archived`**。`set_flags(archived=True)` 会把 `is_latest` 清掉，
所以带 `archived = 0` 的 CTE 会退到"次新且未归档"，而 `latest_of()` 退到"最新但已归档"
——同一个标的两个不同 id。`archived` 只留在 `all` / `archived` 两个 scope 里（R-75：
后来的人不要为了"看起来一致"把它们对齐）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Select, and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from ..errors import InvalidCursor
from ..logging import get_logger
from ..models import Instrument, Report, Run
from ..models.run import ACTIVE_STATUSES
from ..schemas.library import ActiveRunItem, LibraryInstrument, LibraryResponse, ReportLibraryItem
from . import report_service

logger = get_logger("report_library")

#: 五个 scope 的名字就是 `counts` 的键（`schemas/library.py::LibraryScope` 同形）。
LIBRARY_SCOPES: tuple[str, ...] = ("latest", "all", "starred", "archived", "trash")

#: ★评审 P1-3：允许的游标形态**只有一个**。三段式（年-月-日 T 时:分:秒 Z）+ 32 位小写 hex。
CURSOR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\|([0-9a-f]{32})$")

#: `q` 的长度上限（超长 → 422 `validation_error`，由路由层的 `Query` 约束负责）。
MAX_Q_CHARS = 80

#: 「正在生成」最多 3 条：再多就不是"正在生成"而是"排队事故"。
MAX_ACTIVE = 3


@dataclass(frozen=True)
class Cursor:
    """keyset 游标。`at` 是 `generated_at` 的**原样列文本**，`id` 是 tie-break。"""

    at: str
    id: str


def encode_cursor(at: str, report_id: str) -> str:
    """`"{generated_at}|{id}"`。前端负责 `encodeURIComponent`（`|` 必须被编码）。"""
    return f"{at}|{report_id}"


def decode_cursor(raw: str | None) -> Cursor | None:
    """校验并拆开游标；`None` / 空串 ⇒ 无游标（第一页）。

    形态不符 → **422 `invalid_cursor`**（`detail={"before": raw}`），**不是**静默忽略。
    这里**不做**任何时间解析（P1-3 的第二条）。
    """
    if raw is None or raw == "":
        return None
    match = CURSOR_RE.match(raw)
    if match is None:
        raise InvalidCursor(detail={"before": raw})
    return Cursor(at=match.group(1), id=match.group(2))


def _latest_ids() -> Select[tuple[str]]:
    """每个标的中"UI 默认会打开的那一版"的 id 集合（批量版，一次算完）。

    窗口函数而不是 N 个 related subquery：20 个标的的首页只需要一条 SQL。
    `order by is_latest desc, version desc` 与 `latest_of()` 的两段式查询**同序**：
    有 `is_latest` 就给 `is_latest`，没有就退回版本最大的那一行（临时版永远不会抢
    `is_latest`，所以"只有失败尝试"的标的也能在首页看到那张临时卡片）。
    """
    ranked = (
        select(
            Report.id.label("id"),
            func.row_number()
            .over(
                partition_by=Report.instrument_id,
                order_by=(Report.is_latest.desc(), Report.version.desc()),
            )
            .label("rn"),
        )
        # ★P0-2：不筛 archived —— 与 `latest_of()` 同谓词。
        .where(Report.deleted_at.is_(None))
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.rn == 1)


def _scope_conditions(scope: str) -> list[Any]:
    """五个 scope 的谓词（**唯一**真源：列表、计数、`counts` 三处共用这一个函数）。"""
    if scope == "latest":
        return [Report.id.in_(_latest_ids())]
    if scope == "all":
        return [Report.deleted_at.is_(None), Report.archived == 0]
    if scope == "starred":
        return [Report.deleted_at.is_(None), Report.pinned == 1]
    if scope == "archived":
        return [Report.deleted_at.is_(None), Report.archived == 1]
    if scope == "trash":
        return [Report.deleted_at.is_not(None)]
    raise ValueError(f"unknown scope: {scope}")  # pragma: no cover - 由 Literal 拦住


def _escaped_like(term: str) -> str:
    """把 `%` / `_` / `\\` 转义成字面量，再包成 `%term%`。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _headline_expr(session: Session) -> Any:
    """`doc_json -> $.headline`（只有 SQLite 有 `json_extract`）。

    `headline` 是列表里唯一"有信息量"的文本，所以 `q` 必须能搜到它；但它是 doc 的字段，
    没有独立列。用 `json_extract` 在 SQL 里取值，避免为了一个搜索把 20 份 16 KB 的 doc
    全解析一遍。其他方言（PG）退化成"只搜 title / symbol / name"，并在日志里说清——
    静默少搜一个字段比报错更糟。

    ★v14 评审（R-1）：**必须**用 `json_valid()` 把它包起来。`json_extract()` 在语法坏掉的
    行上不是返回 NULL，而是抛 `OperationalError: malformed JSON`（本机 SQLite 3.41.2 实测），
    异常直接冒到路由层 ⇒ 一条坏数据让**整个搜索**变成 500，外加 `total`（同一个谓词）也炸。
    这与 AC-147 是同一条不变量（"坏一行不得让别人看不了"），差别只是多了一个 `q`：不带 `q`
    时这条表达式根本不会被求值，所以那个用例是绿的。`CASE` 是短路求值的（同一实测），
    坏行的 headline 一律当作空串——它仍然出现在列表里，只是搜不到（`integrity=corrupt`
    已经由 `_integrity_expr()` 标出来了）。
    """
    if session.get_bind().dialect.name == "sqlite":
        return case(
            (func.json_valid(Report.doc_json) == 1, func.json_extract(Report.doc_json, "$.headline")),
            else_=literal(""),
        )
    return literal("")


def _query_conditions(session: Session, *, scope: str, q: str, instrument_id: str | None) -> list[Any]:
    """scope + `q` + `instrumentId` 的完整谓词（**列表与 total 共用**）。"""
    conditions = _scope_conditions(scope)
    if instrument_id:
        conditions.append(Report.instrument_id == instrument_id)
    term = (q or "").strip()
    if term:
        pattern = _escaped_like(term)
        conditions.append(
            or_(
                Report.title.like(pattern, escape="\\"),
                _headline_expr(session).like(pattern, escape="\\"),
                Instrument.symbol.like(pattern, escape="\\"),
                Instrument.name.like(pattern, escape="\\"),
            )
        )
    return conditions


def _integrity_expr(session: Session) -> Any:
    """★评审 P1-2：`integrity` 由 **SQL** 算，不在列表里 `json.loads` 20 份 doc。

    `json_valid()` 只做语法检查（本机 3.41.2 实测 `select json_valid('{') → 0`），对
    20 行 × 16 KB 是最便宜的做法；Python 侧只在**详情**路径调 `load_doc_safe()` 取完整壳。
    两者的判定语义相同、成本不同——这正是 P1-2 要的"同语义、不同实现成本"。
    """
    if session.get_bind().dialect.name == "sqlite":
        return case((func.json_valid(Report.doc_json) == 1, "ok"), else_="corrupt")
    # 非 SQLite 没有 `json_valid`：宁可老实说"不知道"（全部 ok），也不在列表里解析正文。
    return literal("ok")


def _base_statement(session: Session, *, scope: str, q: str, instrument_id: str | None) -> Select[Any]:
    conditions = _query_conditions(session, scope=scope, q=q, instrument_id=instrument_id)
    return (
        select(Report, _integrity_expr(session).label("integrity"), Instrument)
        .join(Instrument, Instrument.id == Report.instrument_id)
        .where(*conditions)
    )


def counts(session: Session) -> dict[str, int]:
    """5 个 scope 的全库计数（**不含** `q` / `instrumentId`）。

    谁读它：库页 4 个 tab 的徽章（`latest` 不是 tab，但首页要用它）、首页「查看全部 N 份」
    取 `counts["all"]`。它恒定，`total` 联动——过滤器不是数据变更。
    """
    result: dict[str, int] = {}
    for scope in LIBRARY_SCOPES:
        # 五个 scope 共用 `_scope_conditions()`（列表用的也是它）——这是"同源"的实现。
        conditions = _scope_conditions(scope)
        result[scope] = int(
            session.execute(select(func.count()).select_from(Report).where(*conditions)).scalar_one()
        )
    return result


def total_for(session: Session, *, scope: str, q: str = "", instrument_id: str | None = None) -> int:
    """当前过滤条件命中的总行数（"共 N 份" / 空态判断用；**不参与分页**）。"""
    conditions = _query_conditions(session, scope=scope, q=q, instrument_id=instrument_id)
    return int(
        session.execute(
            select(func.count())
            .select_from(Report)
            .join(Instrument, Instrument.id == Report.instrument_id)
            .where(*conditions)
        ).scalar_one()
    )


def list_page(
    session: Session,
    *,
    scope: str = "latest",
    q: str = "",
    instrument_id: str | None = None,
    limit: int = 20,
    before: str | None = None,
    with_counts: bool = True,
    include_active: bool = False,
) -> LibraryResponse:
    """一页列表 + 计数 + （可选）进行中的 run。

    keyset 分页（**不许 OFFSET**）：两段式比较
    `generated_at < at OR (generated_at = at AND id < id)`，不依赖 SQLite 的 row-value
    比较（Postgres 上也一样）。多取一条判断 `has_more`；`nextCursor` 只在有下一页时非 `null`。
    """
    cursor = decode_cursor(before)
    capped = max(1, min(int(limit), 50))
    stmt = _base_statement(session, scope=scope, q=q, instrument_id=instrument_id)
    if cursor is not None:
        stmt = stmt.where(
            or_(
                Report.generated_at < cursor.at,
                and_(Report.generated_at == cursor.at, Report.id < cursor.id),
            )
        )
    stmt = stmt.order_by(Report.generated_at.desc(), Report.id.desc()).limit(capped + 1)
    rows = session.execute(stmt).all()

    has_more = len(rows) > capped
    visible = rows[:capped]
    items = [
        ReportLibraryItem(
            report=report_service.summarize(report),
            instrument=LibraryInstrument(
                id=instrument.id,
                symbol=instrument.symbol,
                name=instrument.name,
                market=instrument.market,  # type: ignore[arg-type]
                currency=instrument.currency,
            ),
            docSha1=report.doc_sha1,
            integrity=integrity or "ok",  # type: ignore[arg-type]
            # ★评审 P1-13：回收站要显示"删除于 X / 还剩 N 天"，而硬约束 2 不许往
            # `ReportSummary` 加键 —— 所以它落在**新模型**的这一层。
            deletedAt=report.deleted_at,
        )
        for report, integrity, instrument in visible
    ]
    next_cursor = (
        encode_cursor(str(visible[-1][0].generated_at), visible[-1][0].id) if has_more and visible else None
    )
    return LibraryResponse(
        items=items,
        nextCursor=next_cursor,
        total=total_for(session, scope=scope, q=q, instrument_id=instrument_id),
        counts=counts(session) if with_counts else {},
        active=active_runs(session) if include_active else [],
    )


def active_runs(session: Session, limit: int = MAX_ACTIVE) -> list[ActiveRunItem]:
    """进行中的 run（`status in ACTIVE_STATUSES`），最多 `limit` 条。

    ★评审 P2-8：逐字段对齐既有列，别让人猜——`stage` = `runs.stage`（可空）、
    `pct` = `runs.pct`（非空默认 0）、`startedAt` = `runs.started_at`（Text, ISO `Z`）；
    活跃三态用既有常量 `ACTIVE_STATUSES`（与 `report_service.count_active_runs()`
    同一份定义，不新写字面量元组）。`symbol` / `name` 来自 `instruments` 的 join。
    """
    rows = session.execute(
        select(Run, Instrument)
        .join(Instrument, Instrument.id == Run.instrument_id)
        .where(Run.status.in_(ACTIVE_STATUSES))
        .order_by(Run.started_at.desc())
        .limit(max(1, min(limit, MAX_ACTIVE)))
    ).all()
    return [
        ActiveRunItem(
            runId=run.id,
            instrumentId=run.instrument_id,
            symbol=instrument.symbol,
            name=instrument.name,
            stage=run.stage,
            pct=int(run.pct or 0),
            startedAt=str(run.started_at),
        )
        for run, instrument in rows
    ]


def available(session: Session) -> bool:
    """`features.reportLibrary` 的**实测**判据（★§23.5.4）。

    `0006` 已应用（`reports.doc_sha1` 列存在）+ 一次 `select ... limit 1` 不抛异常。
    禁止硬编码 `True`：与既有 `_cost_ledger_available` / `_grounding_available` 同一形状，
    即"每一个开关都来自一次测量"。
    """
    try:
        session.execute(select(Report.doc_sha1).where(Report.doc_sha1.is_not(None)).limit(1)).first()
        return True
    except Exception:  # pragma: no cover - 只有 0006 未应用才会走到
        logger.warning("报告库不可用：reports.doc_sha1 不存在（0006_report_library 未应用？）")
        return False


ScopeLiteral = Literal["latest", "all", "starred", "archived", "trash"]
