"""Per-user watchlist over the legacy "featured" table (spec §24.3.9(d), RV-15).

Two tables, two meanings:

* `watchlist` (legacy, one list for the whole site) is now the **platform featured list** —
  read-only for everybody here, maintained by admins through `/admin/featured-watchlist`.
* `user_watchlist` is a person's own list, primary key `(user_id, instrument_id)`.

A guest sees the featured list (read-only). A signed-in user sees their own; the **first**
time they ask and have nothing, the featured list is copied into theirs once, so a new
account does not open to an empty left rail. Seeding is a write inside a `GET`, so it must be
idempotent under concurrency (RV-15): the `settings` flag is inserted with
`INSERT … ON CONFLICT DO NOTHING` first and **only the request whose insert succeeded** copies
— two simultaneous first `GET`s cannot double-seed. The copy itself is `ON CONFLICT DO NOTHING`
as well.

Non-`user` principals that reach the write routes (only `AUTH_MODE=open`, whose service
principal holds the whole vocabulary) keep the pre-§24 behaviour: they edit the legacy table.
That preserves the single-user "local-first" mode byte for byte.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import iso
from ..models import Instrument, UserWatchlistItem, WatchlistItem
from ..security.principal import Principal
from . import settings_service

Scope = Literal["personal", "featured"]

#: Row shape shared by both tables: `(sort_order, pinned, added_at, instrument)`.
Row = tuple[int, bool, str, Instrument]


def _insert_ignore(db: Session):  # noqa: ANN202 - the dialect's `insert` constructor
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert


def personal_user_id(principal: Principal) -> str | None:
    return principal.user_id if principal.kind == "user" else None


def featured_rows(db: Session) -> list[Row]:
    rows = db.execute(
        select(WatchlistItem, Instrument)
        .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
        .order_by(WatchlistItem.pinned.desc(), WatchlistItem.sort_order.asc(), WatchlistItem.added_at.asc())
    ).all()
    return [(item.sort_order, bool(item.pinned), item.added_at, instrument) for item, instrument in rows]


def personal_rows(db: Session, user_id: str) -> list[Row]:
    rows = db.execute(
        select(UserWatchlistItem, Instrument)
        .join(Instrument, Instrument.id == UserWatchlistItem.instrument_id)
        .where(UserWatchlistItem.user_id == user_id)
        .order_by(
            UserWatchlistItem.pinned.desc(), UserWatchlistItem.sort_order.asc(), UserWatchlistItem.added_at.asc()
        )
    ).all()
    return [(item.sort_order, bool(item.pinned), item.added_at, instrument) for item, instrument in rows]


def ensure_seeded(db: Session, user_id: str) -> bool:
    """Copy the featured list into an empty personal list **once**; True iff this call did.

    The flag row is the arbiter: only the caller whose `INSERT … ON CONFLICT DO NOTHING`
    inserted it may copy. A user who already has entries when the flag is first set (they
    added something before ever listing) is not seeded — the spec seeds an *empty* list."""
    flag = f"{settings_service.KEY_WATCHLIST_SEEDED_PREFIX}{user_id}"
    if not settings_service.insert_if_absent(db, flag, "1"):
        db.rollback()  # nothing was written; do not hold the (read) transaction open
        return False
    already = db.execute(
        select(func.count()).select_from(UserWatchlistItem).where(UserWatchlistItem.user_id == user_id)
    ).scalar_one()
    if already:
        db.commit()
        return False
    stamp = iso()
    insert = _insert_ignore(db)
    for order, (item,) in enumerate(
        db.execute(
            select(WatchlistItem).order_by(
                WatchlistItem.pinned.desc(), WatchlistItem.sort_order.asc(), WatchlistItem.added_at.asc()
            )
        ).all()
    ):
        db.execute(
            insert(UserWatchlistItem)
            .values(
                user_id=user_id,
                instrument_id=item.instrument_id,
                sort_order=order,
                pinned=1 if item.pinned else 0,
                added_at=stamp,
            )
            .on_conflict_do_nothing()
        )
    db.commit()
    return True


def list_for(db: Session, principal: Principal) -> tuple[Scope, list[Row]]:
    user_id = personal_user_id(principal)
    if user_id is None:
        return "featured", featured_rows(db)
    ensure_seeded(db, user_id)
    return "personal", personal_rows(db, user_id)


def add(db: Session, principal: Principal, instrument: Instrument) -> None:
    user_id = personal_user_id(principal)
    if user_id is None:
        if db.get(WatchlistItem, instrument.id) is None:
            orders = db.execute(select(WatchlistItem.sort_order)).scalars().all()
            db.add(
                WatchlistItem(
                    instrument_id=instrument.id,
                    sort_order=(max(orders) + 1) if orders else 0,
                    pinned=0,
                    added_at=iso(),
                )
            )
            db.commit()
        return
    ensure_seeded(db, user_id)
    if db.get(UserWatchlistItem, (user_id, instrument.id)) is None:
        orders = db.execute(
            select(UserWatchlistItem.sort_order).where(UserWatchlistItem.user_id == user_id)
        ).scalars().all()
        statement = (
            _insert_ignore(db)(UserWatchlistItem)
            .values(
                user_id=user_id,
                instrument_id=instrument.id,
                sort_order=(max(orders) + 1) if orders else 0,
                pinned=0,
                added_at=iso(),
            )
            .on_conflict_do_nothing()
        )
        db.execute(statement)
        db.commit()


def remove(db: Session, principal: Principal, instrument_id: str) -> bool:
    """True if a row was removed."""
    user_id = personal_user_id(principal)
    item = db.get(WatchlistItem, instrument_id) if user_id is None else db.get(
        UserWatchlistItem, (user_id, instrument_id)
    )
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def reorder(db: Session, principal: Principal, ids: list[str]) -> str | None:
    """Apply `ids` as the new order; returns the first id that is not in the list (nothing is
    changed in that case), else `None`."""
    user_id = personal_user_id(principal)
    items = []
    for instrument_id in ids:
        item = db.get(WatchlistItem, instrument_id) if user_id is None else db.get(
            UserWatchlistItem, (user_id, instrument_id)
        )
        if item is None:
            return instrument_id
        items.append(item)
    for order, item in enumerate(items):
        item.sort_order = order
    db.commit()
    return None
