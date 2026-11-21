"""Typed access to the `settings` KV table (spec §24.6.3).

**M45–M47 subset.** This module only carries what the identity layer needs today:
raw string get/set (with `INSERT … ON CONFLICT DO NOTHING` / upsert semantics), the key
names and defaults of §24.6.3, and the two typed readers registration uses
(`registration_mode`, `signup_bonus`). M48 owns the full typed surface — per-field
validation (`invalid_setting`), `PATCH /admin/settings`, quota defaults — and extends
this file rather than replacing it.

Every read goes to the database on each call (AC-199: "改设置后无需重启立即生效") —
nothing here is cached.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import InvalidSetting
from ..models import Setting

KEY_REGISTRATION_MODE = "auth.registration_mode"
KEY_SESSION_SECRET = "auth.session_secret"
KEY_DEFAULT_DAILY_REPORT = "quota.default_daily.report"
KEY_DEFAULT_DAILY_DEEP_ASK = "quota.default_daily.deep_ask"
KEY_SIGNUP_BONUS = "quota.signup_bonus"
KEY_SITE_DAILY_CAP = "quota.site_daily_cap"
KEY_RECOVERY_ENABLED = "auth.recovery_enabled"
KEY_WATCHLIST_SEEDED_PREFIX = "watchlist.seeded:"
#: 研究流程图总开关（report-workflow-graph §3.6.1 / §3.9）。`"0"` 是**唯一**走旧提示词的条件。
KEY_WORKFLOW_ENABLED = "workflow.enabled"

RegistrationMode = Literal["open", "invite", "closed"]
REGISTRATION_MODES: tuple[str, ...] = ("open", "invite", "closed")

DEFAULTS: dict[str, str] = {
    KEY_REGISTRATION_MODE: "open",
    KEY_DEFAULT_DAILY_REPORT: "3",
    KEY_DEFAULT_DAILY_DEEP_ASK: "10",
    KEY_SIGNUP_BONUS: "0",
    KEY_SITE_DAILY_CAP: "60",
    # AC-W37：真实引擎对照通过之前，默认值由 M65 定稿；此处是出厂默认。
    KEY_WORKFLOW_ENABLED: "1",
}


def get_str(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.execute(select(Setting.value).where(Setting.key == key)).scalar_one_or_none()
    if row is not None:
        return str(row)
    return default if default is not None else DEFAULTS.get(key)


def set_str(db: Session, key: str, value: str) -> None:
    """Upsert (the caller commits)."""
    existing = db.get(Setting, key)
    if existing is None:
        db.add(Setting(key=key, value=value))
    else:
        existing.value = value
    db.flush()


def _dialect_insert(db: Session):  # noqa: ANN202 - returns the dialect's `insert` constructor
    """`ON CONFLICT DO NOTHING` exists on both supported dialects, under different imports."""
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert


def insert_if_absent(db: Session, key: str, value: str) -> bool:
    """`INSERT … ON CONFLICT DO NOTHING`; True only if this call inserted the row (RV-15)."""
    statement = _dialect_insert(db)(Setting).values(key=key, value=value).on_conflict_do_nothing(index_elements=["key"])
    result = db.execute(statement)
    return bool(result.rowcount)


def get_int(db: Session, key: str, default: int = 0) -> int:
    raw = get_str(db, key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def registration_mode(db: Session) -> RegistrationMode:
    raw = get_str(db, KEY_REGISTRATION_MODE) or "open"
    return raw if raw in REGISTRATION_MODES else "open"  # type: ignore[return-value]


def signup_bonus(db: Session) -> int:
    return max(0, get_int(db, KEY_SIGNUP_BONUS, 0))


# ── M48: quota defaults (typed readers; per-field validation is backend-3's `admin` layer) ──


def set_value(db: Session, key: str, value: str | int) -> None:
    """Upsert `key` (the caller commits). Validation of *admin-supplied* values lives in the
    admin layer (`invalid_setting`); this is the raw typed write."""
    set_str(db, key, str(value))


def default_daily(db: Session, resource: str) -> int:
    """`quota.default_daily.report|deep_ask` for the `user` role (admin/root default is -1)."""
    key = KEY_DEFAULT_DAILY_DEEP_ASK if resource == "deep_ask" else KEY_DEFAULT_DAILY_REPORT
    return max(0, get_int(db, key, int(DEFAULTS[key])))


def site_daily_cap(db: Session) -> int:
    """`quota.site_daily_cap`; `0` = no cap (RV-11)."""
    return max(0, get_int(db, KEY_SITE_DAILY_CAP, int(DEFAULTS[KEY_SITE_DAILY_CAP])))


# ── M50: the admin-facing, validated surface (spec §24.5.3 `GET/PATCH /admin/settings`) ──

#: Field name on the wire → (settings key, inclusive range) for the integer settings.
INT_FIELDS: dict[str, tuple[str, int, int]] = {
    "defaultDailyReport": (KEY_DEFAULT_DAILY_REPORT, 0, 100),
    "defaultDailyDeepAsk": (KEY_DEFAULT_DAILY_DEEP_ASK, 0, 200),
    "signupBonus": (KEY_SIGNUP_BONUS, 0, 20),
    "siteDailyCap": (KEY_SITE_DAILY_CAP, 0, 10000),
}
#: Boolean settings (§25 RV-Y8): wire field → settings key. Stored as `"1"` / `"0"`.
BOOL_FIELDS: dict[str, str] = {"authRecoveryEnabled": KEY_RECOVERY_ENABLED}
SETTING_FIELDS: tuple[str, ...] = ("registrationMode", *INT_FIELDS, *BOOL_FIELDS)


def recovery_enabled(db: Session) -> bool:
    """`auth.recovery_enabled`. With no row the default is the **runtime** `AUTH_RECOVERY_ENABLED`
    (not a static `DEFAULTS` entry), so flipping the env var and restarting changes it."""
    raw = db.execute(select(Setting.value).where(Setting.key == KEY_RECOVERY_ENABLED)).scalar_one_or_none()
    if raw is None:
        return bool(get_settings().auth_recovery_enabled)
    return str(raw) == "1"


def workflow_enabled(db: Session) -> bool:
    """`workflow.enabled`: read fresh on every call (changes apply to the next run, no restart)."""
    return get_str(db, KEY_WORKFLOW_ENABLED) == "1"


def validate_field(field: str, value: object) -> str:
    """Return the storage string for one admin-supplied value, or raise `422 invalid_setting`.

    Strictness is deliberate: JSON `true` is a Python `int`, `"3"` is a string, `3.0` is a
    float — none of them is silently coerced into a quota that changes who may spend money.
    """
    if field == "registrationMode":
        if not isinstance(value, str) or value not in REGISTRATION_MODES:
            raise InvalidSetting(field=field)
        return value
    if field in BOOL_FIELDS:
        if not isinstance(value, bool):  # `1`, `"true"`, `null` are all refused (A-8's strictness)
            raise InvalidSetting(field=field)
        return "1" if value else "0"
    spec = INT_FIELDS.get(field)
    if spec is None:
        raise InvalidSetting(f"未知的设置项：{field}", field=field)
    _key, low, high = spec
    if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
        raise InvalidSetting(field=field)
    return str(value)


def get_all(db: Session) -> dict[str, object]:
    """Every setting the admin page edits, read fresh from the database (AC-199)."""
    out: dict[str, object] = {"registrationMode": registration_mode(db)}
    for field, (key, _low, _high) in INT_FIELDS.items():
        out[field] = get_int(db, key, int(DEFAULTS[key]))
    out["authRecoveryEnabled"] = recovery_enabled(db)
    return out


def update(db: Session, patch: dict[str, object]) -> dict[str, dict[str, object]]:
    """Validate **every** field first, then write: a bad field rejects the whole PATCH
    (no half-applied settings). Returns `{field: {"from": old, "to": new}}` for the fields that
    actually changed (the audit detail). The caller commits.
    """
    if not patch:
        raise InvalidSetting("没有可更新的设置项", field="")
    cleaned = {field: validate_field(field, value) for field, value in patch.items()}
    before = get_all(db)
    changes: dict[str, dict[str, object]] = {}
    for field, stored in cleaned.items():
        if field == "registrationMode":
            key, new_value = KEY_REGISTRATION_MODE, stored
        elif field in BOOL_FIELDS:
            key, new_value = BOOL_FIELDS[field], stored == "1"
        else:
            key, new_value = INT_FIELDS[field][0], int(stored)
        if before[field] != new_value:
            set_str(db, key, stored)
            changes[field] = {"from": before[field], "to": new_value}
    return changes
