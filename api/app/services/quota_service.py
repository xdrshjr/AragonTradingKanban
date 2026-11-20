"""Quota engine (spec §24.3.8, X4).

**The ledger is the truth, not a counter.** Every spend, refund and grant is an immutable
`quota_ledger` row; balances are `SUM`s. That is what makes a refund idempotent (partial
unique index `ux_quota_ref_reason`), "why do I only have 1 left?" answerable row by row, and
admin adjustments auditable.

Public surface

* `status(db, user)` / `snapshot(db, principal)` → `QuotaStatus` `{report, deepAsk, tz}`.
* `precheck(db, principal, resource)` — read-only, raises `QuotaExceeded` (error priority, §24.3.8(c)).
* `reservation(db, principal, resource, ref_id)` — context manager. **The per-user lock is
  held until the caller has committed or rolled back** (RV-9): releasing it right after the
  INSERT lets the next thread read a balance that does not yet contain the uncommitted
  deduction, i.e. over-issue. Inside the block the caller may do one local `mkdir` and the
  SQL of its own transaction — never network, subprocess or sleep.
* `reconcile_refunds(db, user_id=None)` — idempotent refund sweep; the four call sites are
  `main._sweep_loop`, start-up, `status()`/`precheck()` (targeted) and
  `run_manager._finalize_failure` (a pure accelerator).
* `grant(...)`, `set_limit(...)` — admin adjustments (the *caller* audits).

Who is subject to quota: only a `user` principal (it has a `user_id`). `service` and `system`
(scheduler) are never charged and never write ledger rows; a `limit == -1` user is the same
("unlimited quota writes no ledger rows", §24.3.8(b)1).

Concurrency model: the locks are process-local on purpose — the single-worker invariant
(`assert_single_worker`, E-8) is what makes that sufficient. The balance itself lives in the
database, so a restart loses nothing.
"""

from __future__ import annotations

import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso
from ..errors import InvalidCursor, QuotaExceeded
from ..logging import get_logger
from ..models import QuotaLedger, QuotaRequest, ReportAsk, Run, User
from ..models.quota import QUOTA_RESOURCES
from ..security import clock
from ..security.principal import Principal
from . import settings_service

logger = get_logger("quota")

#: `resource → (deduction reason, refund reason)`
REASONS: dict[str, tuple[str, str]] = {"report": ("run", "run_refund"), "deep_ask": ("ask", "ask_refund")}
#: run / ask terminal statuses that give the charge back (spec §24.3.8(d)). `succeeded`
#: (including `deduped`) is deliberately absent: the AI call was paid for.
REFUNDABLE_RUN_STATUSES = ("failed", "cancelled", "timeout", "budget_exceeded")
REFUNDABLE_ASK_STATUSES = ("failed", "cancelled")

GRANT_MIN, GRANT_MAX = -50, 50

_STRIPES = 64
_USER_LOCKS = [threading.RLock() for _ in range(_STRIPES)]
#: Serialises the *site-wide* check across users. Lock order is always user → site.
_SITE_LOCK = threading.RLock()


def _stripe(user_id: str) -> threading.RLock:
    return _USER_LOCKS[zlib.crc32(user_id.encode("utf-8")) % _STRIPES]


# ── resolution of who/what is limited ────────────────────────────────────────


def _check_resource(resource: str) -> None:
    if resource not in QUOTA_RESOURCES:
        raise ValueError(f"unknown quota resource: {resource!r}")


def constrained_user_id(principal: Principal) -> str | None:
    """The `user_id` quota applies to, or `None` for guest / service / system."""
    if principal.kind == "user" and principal.user_id:
        return principal.user_id
    return None


def effective_limit(db: Session, user_id: str, resource: str) -> int:
    """`users.quota_*_limit ?? role default` — `-1` unlimited, `0` disabled, `N` per day."""
    _check_resource(resource)
    row = db.execute(
        select(User.role, User.quota_daily_limit, User.quota_deep_daily_limit).where(User.id == user_id)
    ).one_or_none()
    role, report_override, deep_override = row if row is not None else ("user", None, None)
    override = deep_override if resource == "deep_ask" else report_override
    if override is not None:
        return int(override)
    if role in ("admin", "root"):
        return -1
    return settings_service.default_daily(db, resource)


def _tz() -> str:
    return get_settings().quota_tz


def _today(moment: datetime) -> str:
    return clock.local_day_key(moment, _tz())


# ── balances ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Balance:
    used_today: int
    bonus: int


def _balance(db: Session, user_id: str, resource: str, day_key: str) -> _Balance:
    daily = func.coalesce(
        func.sum(
            case((QuotaLedger.source == "daily", case((QuotaLedger.day_key == day_key, QuotaLedger.delta), else_=0)))
        ),
        0,
    )
    bonus = func.coalesce(func.sum(case((QuotaLedger.source == "bonus", QuotaLedger.delta), else_=0)), 0)
    used, bonus_total = db.execute(
        select(daily, bonus).where(QuotaLedger.user_id == user_id, QuotaLedger.resource == resource)
    ).one()
    return _Balance(used_today=max(0, -int(used or 0)), bonus=int(bonus_total or 0))


def site_used(db: Session, day_key: str) -> int:
    """Net reports generated today by quota-bound users (AC-207). Refunds give names back."""
    total = db.execute(
        select(func.coalesce(func.sum(QuotaLedger.delta), 0)).where(
            QuotaLedger.resource == "report",
            QuotaLedger.reason.in_(("run", "run_refund")),
            QuotaLedger.day_key == day_key,
        )
    ).scalar_one()
    return max(0, -int(total or 0))


def _remaining(limit: int, balance: _Balance) -> int | None:
    if limit == -1:
        return None
    return max(0, limit - balance.used_today) + max(0, balance.bonus)


def _reset_at(moment: datetime) -> str:
    return iso(clock.next_local_midnight(moment, _tz()))


def _pending_request(db: Session, user_id: str) -> QuotaRequest | None:
    return db.execute(
        select(QuotaRequest).where(QuotaRequest.user_id == user_id, QuotaRequest.status == "pending")
    ).scalar_one_or_none()


def _bucket(
    db: Session, user_id: str, resource: str, moment: datetime, pending: QuotaRequest | None
) -> dict[str, Any]:
    limit = effective_limit(db, user_id, resource)
    balance = _balance(db, user_id, resource, _today(moment))
    remaining = _remaining(limit, balance)
    return {
        "limit": None if limit == -1 else limit,
        "usedToday": balance.used_today,
        "bonus": max(0, balance.bonus),
        "remaining": remaining,
        "resetAt": _reset_at(moment),
        "pendingRequest": (
            {"id": pending.id, "createdAt": pending.created_at}
            if pending is not None and pending.resource == resource
            else None
        ),
    }


def status(db: Session, user: User, *, now: datetime | None = None) -> dict[str, Any]:
    """`QuotaStatus`. Reconciles this user's refunds first, so a reload after a failed run
    already shows the refunded balance (§24.3.8(d) call site ③)."""
    moment = now or clock.now()
    reconcile_refunds(db, user_id=user.id, now=moment)
    pending = _pending_request(db, user.id)
    return {
        "report": _bucket(db, user.id, "report", moment, pending),
        "deepAsk": _bucket(db, user.id, "deep_ask", moment, pending),
        "tz": _tz(),
    }


def snapshot(db: Session, principal: Principal, *, now: datetime | None = None) -> dict[str, Any] | None:
    user_id = constrained_user_id(principal)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None:
        return None
    return status(db, user, now=now)


def _exceeded(
    resource: str,
    *,
    scope: str,
    limit: int,
    balance: _Balance,
    moment: datetime,
) -> QuotaExceeded:
    detail = {
        "resource": resource,
        "scope": scope,
        "limit": limit,
        "usedToday": balance.used_today,
        "bonus": max(0, balance.bonus),
        "resetAt": _reset_at(moment),
        # A site-wide cap cannot be lifted by one more grant for this person.
        "canRequest": scope == "user",
    }
    if scope == "site":
        return QuotaExceeded("今日全站生成名额已用完，明日 00:00 重置", detail=detail)
    if resource == "deep_ask":
        return QuotaExceeded("今日深度追问额度已用完", detail=detail)
    return QuotaExceeded(detail=detail)


# ── precheck / reservation ───────────────────────────────────────────────────


def _blocked(
    db: Session, user_id: str, resource: str, limit: int, moment: datetime
) -> QuotaExceeded | None:
    """The exception this user would get right now, or `None` when there is room."""
    day = _today(moment)
    balance = _balance(db, user_id, resource, day)
    remaining = _remaining(limit, balance)
    if remaining is not None and remaining <= 0:
        return _exceeded(resource, scope="user", limit=limit, balance=balance, moment=moment)
    if resource == "report":
        cap = settings_service.site_daily_cap(db)
        if cap > 0 and site_used(db, day) >= cap:
            return _exceeded(resource, scope="site", limit=limit, balance=balance, moment=moment)
    return None


def precheck(db: Session, principal: Principal, resource: str = "report", *, now: datetime | None = None) -> None:
    """Read-only. Raises `QuotaExceeded`; passing does **not** promise the reservation succeeds."""
    _check_resource(resource)
    user_id = constrained_user_id(principal)
    if user_id is None:
        return
    limit = effective_limit(db, user_id, resource)
    if limit == -1:
        return
    moment = now or clock.now()
    blocked = _blocked(db, user_id, resource, limit, moment)
    if blocked is None:
        return
    # A failed run whose refund the sweeper has not booked yet must not turn the user away.
    if reconcile_refunds(db, user_id=user_id, now=moment) or reconcile_refunds(db, now=moment):
        blocked = _blocked(db, user_id, resource, limit, moment)
        if blocked is None:
            return
    raise blocked


@contextmanager
def reservation(
    db: Session, principal: Principal, resource: str, ref_id: str, *, now: datetime | None = None
) -> Iterator[QuotaLedger | None]:
    """Take the user's lock, re-check, `flush` the deduction row; release after the caller's
    commit/rollback (RV-9). Yields the ledger row, or `None` for an exempt principal."""
    _check_resource(resource)
    user_id = constrained_user_id(principal)
    if user_id is None:
        yield None
        return
    limit = effective_limit(db, user_id, resource)
    if limit == -1:
        yield None
        return

    moment = now or clock.now()
    stripe = _stripe(user_id)
    site_locked = False
    stripe.acquire()
    try:
        if resource == "report" and settings_service.site_daily_cap(db) > 0:
            _SITE_LOCK.acquire()
            site_locked = True
        blocked = _blocked(db, user_id, resource, limit, moment)
        if blocked is not None and (
            reconcile_refunds(db, user_id=user_id, now=moment) or reconcile_refunds(db, now=moment)
        ):
            blocked = _blocked(db, user_id, resource, limit, moment)
        if blocked is not None:
            raise blocked

        day = _today(moment)
        balance = _balance(db, user_id, resource, day)
        source = "daily" if balance.used_today < limit else "bonus"
        row = QuotaLedger(
            user_id=user_id,
            resource=resource,
            delta=-1,
            reason=REASONS[resource][0],
            source=source,
            day_key=day,
            ref_id=ref_id,
            actor_id=None,
            note=None,
            created_at=iso(moment),
        )
        db.add(row)
        db.flush()
        yield row
    finally:
        if site_locked:
            _SITE_LOCK.release()
        stripe.release()


# ── refunds ──────────────────────────────────────────────────────────────────


def _insert_ignore(db: Session):  # noqa: ANN202 - the dialect's `insert` constructor
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert


def reconcile_refunds(db: Session, *, user_id: str | None = None, now: datetime | None = None) -> int:
    """Book the missing `*_refund` rows; returns how many it inserted (and commits iff > 0).

    Idempotent and safe to call concurrently: the refund copies the *original* row's
    `source` and `day_key` (the charge goes back to the day and pool it came from, not to
    today), and `ux_quota_ref_reason` plus `ON CONFLICT DO NOTHING` turn a second booking into
    a no-op. A `succeeded` run is never refunded.
    """
    moment = now or clock.now()
    created = iso(moment)
    inserted = 0
    for resource, status_column, statuses, model in (
        ("report", Run.status, REFUNDABLE_RUN_STATUSES, Run),
        ("deep_ask", ReportAsk.status, REFUNDABLE_ASK_STATUSES, ReportAsk),
    ):
        charge_reason, refund_reason = REASONS[resource]
        refund = QuotaLedger.__table__.alias("refund")
        query = (
            select(QuotaLedger)
            .join(model, model.id == QuotaLedger.ref_id)
            .where(
                QuotaLedger.reason == charge_reason,
                QuotaLedger.resource == resource,
                status_column.in_(statuses),
                ~select(refund.c.id)
                .where(refund.c.ref_id == QuotaLedger.ref_id, refund.c.reason == refund_reason)
                .exists(),
            )
        )
        if user_id is not None:
            query = query.where(QuotaLedger.user_id == user_id)
        for charge in db.execute(query.limit(500)).scalars().all():
            statement = (
                _insert_ignore(db)(QuotaLedger)
                .values(
                    user_id=charge.user_id,
                    resource=charge.resource,
                    delta=1,
                    reason=refund_reason,
                    source=charge.source,
                    day_key=charge.day_key,
                    ref_id=charge.ref_id,
                    actor_id=None,
                    note=None,
                    created_at=created,
                )
                .on_conflict_do_nothing()
            )
            if db.execute(statement).rowcount:
                inserted += 1
    if inserted:
        db.commit()
        logger.info("quota refunds booked: %d%s", inserted, f" (user {user_id})" if user_id else "")
    return inserted


# ── admin adjustments ────────────────────────────────────────────────────────


def bonus_balance(db: Session, user_id: str, resource: str) -> int:
    return _balance(db, user_id, resource, "").bonus


def grant(
    db: Session,
    *,
    actor: Principal,
    target_user: User,
    resource: str,
    amount: int,
    note: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> QuotaLedger | None:
    """Add (or, if negative, take back) bonus credits. Flush only — the caller audits.

    A take-back is clamped to the current bonus balance (it can never push the balance
    negative); a clamp to zero writes nothing and returns `None`.
    """
    _check_resource(resource)
    if amount == 0 or not (GRANT_MIN <= amount <= GRANT_MAX):
        raise ValueError(f"grant amount must be non-zero within [{GRANT_MIN}, {GRANT_MAX}]")
    moment = now or clock.now()
    stripe = _stripe(target_user.id)
    with stripe:
        delta = amount
        if amount < 0:
            delta = -min(-amount, max(0, bonus_balance(db, target_user.id, resource)))
            if delta == 0:
                return None
        row = QuotaLedger(
            user_id=target_user.id,
            resource=resource,
            delta=delta,
            reason=reason or ("admin_grant" if delta > 0 else "admin_revoke"),
            source="bonus",
            day_key=_today(moment),
            ref_id=None,
            actor_id=actor.user_id,
            note=note[:120] if note else None,
            created_at=iso(moment),
        )
        db.add(row)
        db.flush()
        return row


def set_limit(db: Session, *, actor: Principal, target_user: User, resource: str, limit: int | None) -> None:  # noqa: ARG001
    """`None` = follow the default, `-1` = unlimited, `0` = disabled, `N` = per day."""
    _check_resource(resource)
    if limit is not None and limit < -1:
        raise ValueError("limit must be None, -1 or >= 0")
    if resource == "deep_ask":
        target_user.quota_deep_daily_limit = limit
    else:
        target_user.quota_daily_limit = limit
    db.flush()


# ── ledger listing ───────────────────────────────────────────────────────────


def ledger_item(row: QuotaLedger) -> dict[str, Any]:
    return {
        "id": row.id,
        "at": row.created_at,
        "resource": row.resource,
        "delta": row.delta,
        "reason": row.reason,
        "source": row.source,
        "refId": row.ref_id,
        "note": row.note,
    }


def list_ledger(
    db: Session, user_id: str, *, limit: int = 50, before: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Newest first. The cursor is the last row's integer id (a stable, gap-tolerant keyset)."""
    limit = max(1, min(int(limit), 200))
    query = select(QuotaLedger).where(QuotaLedger.user_id == user_id)
    if before:
        try:
            query = query.where(QuotaLedger.id < int(before))
        except ValueError as exc:
            raise InvalidCursor() from exc
    rows = db.execute(query.order_by(QuotaLedger.id.desc()).limit(limit + 1)).scalars().all()
    page = rows[:limit]
    next_cursor = str(page[-1].id) if len(rows) > limit and page else None
    return [ledger_item(row) for row in page], next_cursor
