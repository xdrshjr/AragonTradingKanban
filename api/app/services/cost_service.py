"""成本治理的读侧与闸门（spec §19.3.5，M17）。

三层职责分离（唯一裁决）：

| 层 | 职责 | 不做什么 |
| --- | --- | --- |
| `runs.cost` / `report_asks.cost`（既有） | **权威**：一次生成/一次追问的真实花费 | 不改结构、不删字段 |
| `cost_ledger`（本模块写的表） | **投影**：按 `kind` 落成可累计的行 | 从不 UPDATE、从不 DELETE |
| `cost_service`（本模块） | **读**：窗口汇总、预估、闸门判定 | 不写权威字段；账本写入走 `record()` |

三条不可协商的裁决：

* **`NULL` ≠ `0`**：`cost.known=false` 的 run 在账本里是 `usd=NULL, known=0`，汇总里
  单列 `unknownRuns`，绝不折算成 `$0.00`（AC-55）。
* **预算默认不设限**：`BUDGET_MONTHLY_USD=0` 时 `can_start()` 恒真且**不查库**，
  默认路径零成本（§19.3.5）。
* **账本失败不回滚报告**：`record()` 自己开 savepoint，失败只记 warning 并把
  `ledgerGap` +1（AC-57，模板即 `report_service.py` 的 FTS 写法）。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agent.pricing import PRICE_VERSION, price_for
from ..config import get_settings
from ..logging import get_logger
from ..models import CostLedger, Report, ReportAsk, Run, Setting

logger = get_logger("cost_service")

#: `Setting` key for the runtime budget override (the cost panel writes it).
BUDGET_SETTING_KEY = "budget_monthly_usd"

#: Windows the panel offers. `mtd` starts on the 1st, `ytd` on Jan 1 (§19.5.1).
WINDOWS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90, "mtd": 0, "ytd": -1}

#: `near` threshold: 80% of the budget (§19.5.1).
NEAR_RATIO = 0.8

#: How many historical runs the estimator looks at (§19.3.5).
ESTIMATE_SAMPLES = 5

#: Process-scoped counter of ledger rows lost to a savepoint. Deliberately not a
#: DB column: a gap is an operational event on *this* process, not a fact about a
#: report, and persisting it would create a third source of truth for "how much
#: did I spend".
_LEDGER_GAPS = 0


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ledger_gaps() -> int:
    return _LEDGER_GAPS


def reset_ledger_gaps() -> None:
    """Test hook."""
    global _LEDGER_GAPS
    _LEDGER_GAPS = 0


# ── 写账本 ──────────────────────────────────────────────────────────────────


def record(
    session: Session,
    *,
    kind: str,
    ref_id: str,
    usd: float | None,
    known: bool,
    symbol: str | None = None,
    report_id: str | None = None,
    model: str | None = None,
) -> bool:
    """只追加一行账本。**失败绝不抛给调用方**（报告比账本重要，AC-57）。

    `session.begin_nested()` 是必须的：没有 savepoint 时，捕获异常只留下一个
    aborted transaction，随后报告自己的 `commit()` 会抛 `PendingRollbackError` —
    "账本失败不影响报告"就成了一句注释而不是性质（这条对 v4 §I-34 的推广）。
    """
    global _LEDGER_GAPS
    row = CostLedger(
        kind=kind,
        ref_id=ref_id,
        symbol=(symbol or None),
        report_id=report_id,
        model=(model or None),
        # `NULL` = 未知；`0` = 已知且免费。两者语义相反（§19.11 第 3 条）。
        usd=None if not known else usd,
        known=1 if known else 0,
        created_at=_iso(),
    )
    try:
        with session.begin_nested():
            session.add(row)
    except Exception:  # pragma: no cover - 唯一键冲突 / DB 抛错
        _LEDGER_GAPS += 1
        logger.warning("cost ledger 写入失败 kind=%s ref=%s（报告已落库，缺口计入 ledgerGap）", kind, ref_id)
        return False
    return True


def cleanup(session: Session) -> int:
    """删除超过 `COST_LEDGER_KEEP_DAYS` 的账本行（两年）。"""
    keep_days = max(1, get_settings().cost_ledger_keep_days)
    cutoff = _iso(datetime.now(UTC) - timedelta(days=keep_days))
    rows = session.execute(select(CostLedger).where(CostLedger.created_at < cutoff)).scalars().all()
    for row in rows:
        session.delete(row)
    if rows:
        session.commit()
        logger.info("清理过期账本 %s 行", len(rows))
    return len(rows)


# ── 预算 ────────────────────────────────────────────────────────────────────


def budget_monthly_usd(session: Session | None = None) -> float:
    """当前月度预算（USD）。`0` = 不设限（默认）。

    先读 `Setting`（成本面板的运行期覆盖），再退回进程配置——这样"改完即时生效"
    不需要重启，也不需要缓存一份可能过期的值。
    """
    if session is not None:
        row = session.get(Setting, BUDGET_SETTING_KEY)
        if row is not None:
            try:
                return max(0.0, float(row.value))
            except (TypeError, ValueError):  # pragma: no cover - 只有人手改坏才会发生
                logger.warning("预算设置无法解析：%r，回退到配置值", row.value)
    return max(0.0, float(get_settings().budget_monthly_usd))


def set_budget(session: Session, monthly_usd: float) -> float:
    value = max(0.0, round(float(monthly_usd), 4))
    row = session.get(Setting, BUDGET_SETTING_KEY)
    if row is None:
        session.add(Setting(key=BUDGET_SETTING_KEY, value=str(value)))
    else:
        row.value = str(value)
    session.commit()
    logger.info("月度预算已更新 usd=%s", value)
    return value


def month_reset_at(now: datetime | None = None) -> str:
    """下个月 1 日 00:00:00Z —— 预算窗口的边界，UI 用它说"何时重置"。"""
    moment = now or datetime.now(UTC)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return _iso(datetime(year, month, 1, tzinfo=UTC))


# ── 窗口汇总 ────────────────────────────────────────────────────────────────


def _window_bounds(window: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    moment = now or datetime.now(UTC)
    key = window if window in WINDOWS else "30d"
    if key == "mtd":
        return datetime(moment.year, moment.month, 1, tzinfo=UTC), moment
    if key == "ytd":
        return datetime(moment.year, 1, 1, tzinfo=UTC), moment
    return moment - timedelta(days=WINDOWS[key]), moment


def summary(session: Session, window: str = "30d") -> dict[str, Any]:
    """窗口汇总。`spentUsd` **只含已知**，未知次数单列（AC-55）。"""
    start, end = _window_bounds(window)
    rows = (
        session.execute(
            select(CostLedger).where(
                CostLedger.created_at >= _iso(start),
                CostLedger.created_at <= _iso(end),
            )
        )
        .scalars()
        .all()
    )

    spent = 0.0
    known_runs = 0
    unknown_runs = 0
    by_kind: dict[str, float] = {"run": 0.0, "ask_deep": 0.0}
    by_model: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.usd is None:
            unknown_runs += 1
            continue
        known_runs += 1
        spent += float(row.usd)
        key = row.kind or "run"
        by_kind[key] = round(by_kind.get(key, 0.0) + float(row.usd), 4)
        model = row.model or "unknown"
        bucket = by_model.setdefault(model, {"usd": 0.0, "runs": 0.0})
        bucket["usd"] += float(row.usd)
        bucket["runs"] += 1

    monthly = budget_monthly_usd(session)
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    month_spent = float(
        session.execute(
            select(func.coalesce(func.sum(CostLedger.usd), 0.0)).where(
                CostLedger.created_at >= _iso(month_start),
                CostLedger.usd.is_not(None),
            )
        ).scalar_one()
        or 0.0
    )

    if monthly <= 0:
        budget = {
            "monthlyUsd": 0.0,
            "remainingUsd": None,
            "resetAt": None,
            "state": "unlimited",
        }
    else:
        remaining = round(max(0.0, monthly - month_spent), 4)
        if month_spent > monthly:
            state = "exceeded"
        elif month_spent >= monthly * NEAR_RATIO:
            state = "near"
        else:
            state = "ok"
        budget = {
            "monthlyUsd": monthly,
            "remainingUsd": remaining,
            "resetAt": month_reset_at(now),
            "state": state,
        }

    return {
        "window": window if window in WINDOWS else "30d",
        "from": _iso(start),
        "to": _iso(end),
        "spentUsd": round(spent, 4),
        "knownRuns": known_runs,
        "unknownRuns": unknown_runs,
        "byKind": by_kind,
        "byModel": [
            {"model": model, "usd": round(bucket["usd"], 4), "runs": int(bucket["runs"])}
            for model, bucket in sorted(by_model.items(), key=lambda item: -item[1]["usd"])
        ],
        "budget": budget,
        "ledgerGap": ledger_gaps(),
    }


def entries(session: Session, limit: int = 50) -> list[CostLedger]:
    cap = max(1, min(int(limit or 50), 200))
    return list(
        session.execute(select(CostLedger).order_by(CostLedger.id.desc()).limit(cap)).scalars().all()
    )


# ── 闸门 ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class BudgetDecision:
    """`allowed=False` 时 `detail` 直接就是 402 的响应体（§19.5.3）。"""

    allowed: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


def can_start(session: Session, kind: str = "run", *, estimate_usd: float | None = None) -> BudgetDecision:
    """预算闸门。**只读账本**，不改 `RunManager` 的信号量与锁（O-9）。

    * 未设预算（`BUDGET_MONTHLY_USD=0`）→ 放行，**不查库**；
    * `spent + estimate <= budget` → 放行；
    * 超限 → `allowed=False` + 402 的 detail（含 `override: true`，因为用户可以覆盖）。
    """
    monthly = budget_monthly_usd(session)
    if monthly <= 0:
        return BudgetDecision(allowed=True)

    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    spent = float(
        session.execute(
            select(func.coalesce(func.sum(CostLedger.usd), 0.0)).where(
                CostLedger.created_at >= _iso(month_start),
                CostLedger.usd.is_not(None),
            )
        ).scalar_one()
        or 0.0
    )
    unknown = int(
        session.execute(
            select(func.count())
            .select_from(CostLedger)
            .where(CostLedger.created_at >= _iso(month_start), CostLedger.usd.is_(None))
        ).scalar_one()
    )
    pending = float(estimate_usd or 0.0)
    if spent + pending <= monthly:
        return BudgetDecision(allowed=True)

    return BudgetDecision(
        allowed=False,
        detail={
            "spentUsd": round(spent, 4),
            "budgetUsd": monthly,
            "remainingUsd": round(max(0.0, monthly - spent), 4),
            "resetAt": month_reset_at(now),
            # 未知成本单独给出次数，绝不折算进 spentUsd。
            "unknownRuns": unknown,
            "estimateUsd": None if estimate_usd is None else round(pending, 4),
            "override": True,
        },
    )


# ── 预估 ────────────────────────────────────────────────────────────────────


def estimate(session: Session, symbol: str, kind: str = "run") -> dict[str, Any]:
    """`最近 5 次同标的的 tokens 中位数 × price_for(model)`；无历史/无价格 → None。

    **永不 500、永不返回 0 冒充已知**（§19.5.4）。
    """
    if kind == "ask_deep":
        rows = (
            session.execute(
                select(ReportAsk.tokens_in, ReportAsk.tokens_out, ReportAsk.cost_usd)
                .order_by(ReportAsk.created_at.desc())
                .limit(ESTIMATE_SAMPLES * 4)
            )
            .all()
        )
        tokens = [
            (row[0] or 0) + (row[1] or 0)
            for row in rows
        ]
        model = get_settings().aragon_model or None
    else:
        from ..models import Instrument

        instrument = session.execute(select(Instrument).where(Instrument.symbol == symbol.upper())).scalar_one_or_none()
        if instrument is None:
            return {"estimatedUsd": None, "reason": "no_history"}
        rows = (
            session.execute(
                select(Run.tokens_in, Run.tokens_out)
                .where(Run.instrument_id == instrument.id, Run.status == "succeeded")
                .order_by(Run.started_at.desc())
                .limit(ESTIMATE_SAMPLES)
            )
            .all()
        )
        if not rows:
            return {"estimatedUsd": None, "reason": "no_history"}
        tokens = [(row[0] or 0) + (row[1] or 0) for row in rows]
        # `runs` 表没有 model 列（权威在 `reports.model`），所以取该标的最近一版报告的模型。
        model = (
            session.execute(
                select(Report.model)
                .where(Report.instrument_id == instrument.id, Report.model.is_not(None))
                .order_by(Report.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            or get_settings().aragon_model
            or None
        )

    history = [value for value in tokens if value > 0]
    if not history:
        return {"estimatedUsd": None, "reason": "no_history"}

    price = price_for(model)
    if price is None:
        return {"estimatedUsd": None, "reason": "no_price"}

    median_tokens = int(statistics.median(history))
    # 生成与追问的平均输入/输出配比不同；按 3:1 拆，误差由"预估"这个词本身承担。
    input_tokens = int(median_tokens * 0.75)
    output_tokens = median_tokens - input_tokens
    usd = round(input_tokens / 1000 * price.input_per_1k + output_tokens / 1000 * price.output_per_1k, 4)
    return {
        "estimatedUsd": usd,
        "reason": None,
        "basis": {
            "samples": len(history),
            "medianTokens": median_tokens,
            "model": model or "unknown",
            "priceVersion": PRICE_VERSION,
        },
    }


__all__ = [
    "BUDGET_SETTING_KEY",
    "BudgetDecision",
    "NEAR_RATIO",
    "WINDOWS",
    "budget_monthly_usd",
    "can_start",
    "cleanup",
    "entries",
    "estimate",
    "ledger_gaps",
    "month_reset_at",
    "record",
    "reset_ledger_gaps",
    "set_budget",
    "summary",
]
