"""Append-only audit log (spec §24.3.10).

`record()` adds one row **to the caller's transaction** (it `flush`es, it does not
`commit`): the audit entry and the thing it describes then commit or roll back together,
which is the only way "who did what" can be trusted. Callers that audit a *refusal*
(failed login, lockout) commit explicitly because there is nothing else to be atomic with.

Safety properties, each with a test:

* `detail_json` never carries secrets: any key that looks like a password / token /
  secret / cookie / captcha answer is dropped, recursively (AC-163, AC-198).
* `actor_name / actor_role / target_name` are **snapshots**, so a later rename or
  anonymisation does not rewrite history.
* Repeated failed logins for the same `(username, ip)` inside 60 s are **merged** into one
  row with a `count` (the audit table must not become the thing a brute-forcer floods).
* There is no update/delete in the HTTP surface; `purge_expired()` is the retention sweep.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso
from ..errors import InvalidCursor
from ..logging import request_id_var
from ..models import AuditLog
from ..security import clock
from ..security.principal import Principal

#: The fixed action vocabulary (spec §24.3.10). The admin UI's filter dropdown is built
#: from it and `tests/test_audit.py` asserts every entry has a write site.
ACTIONS: tuple[str, ...] = (
    "auth.login.success",
    "auth.login.failure",
    "auth.login.lockout",
    "auth.logout",
    "auth.register",
    "auth.password.change",
    "auth.password.reset",
    "auth.session.revoke",
    "user.create",
    "user.role.change",
    "user.disable",
    "user.enable",
    "user.delete",
    "user.unlock",
    "quota.grant",
    "quota.limit.set",
    "quota.request.create",
    "quota.request.approve",
    "quota.request.reject",
    "invite.create",
    "invite.revoke",
    "settings.update",
    "featured.add",
    "featured.remove",
    "schedule.create",
    "schedule.update",
    "schedule.delete",
    "report.manage_other",
    "run.cancel_other",
    "root.bootstrap",
    "root.password.cli_reset",
    # §25 迭代 2（31 → 36）
    "auth.recovery.rotate",
    "auth.recovery.failure",
    "auth.password.recover",
    "user.self_delete",
    "admin.export",
    # 研究流程图（36 → 41）：草稿编辑与 AI 对话高频低价值，不入审计；发布/回滚/默认/归档/创建入审计。
    "workflow.create",
    "workflow.publish",
    "workflow.rollback",
    "workflow.default.set",
    "workflow.archive",
)

FAILURE_MERGE_WINDOW = timedelta(seconds=60)
REQUEST_ID_COLUMN = 16
_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)|token|secret|cookie|authorization|captcha|answer|api_?key|recovery[_-]?(key|hash)", re.I
)
_MAX_VALUE_CHARS = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class AuditTarget(NamedTuple):
    """What the action was done *to*: `(type, id, name)`, e.g. `("user", uid, "alice")`."""

    type: str
    id: str | None = None
    name: str | None = None


def sanitize_detail(detail: Any) -> Any:
    """Drop secret-looking keys (recursively) and clip long strings."""
    if isinstance(detail, dict):
        return {
            str(key): sanitize_detail(value) for key, value in detail.items() if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(detail, (list, tuple)):
        return [sanitize_detail(item) for item in detail][:50]
    if isinstance(detail, str):
        return detail[:_MAX_VALUE_CHARS]
    return detail


def _clean_name(value: str | None, limit: int = 40) -> str | None:
    if value is None:
        return None
    return _CONTROL_CHARS.sub("", value)[:limit] or None


def _actor_name(principal: Principal) -> str | None:
    if principal.username:
        return _clean_name(principal.username)
    return principal.kind if principal.kind in ("service", "system") else None


def record(
    db: Session,
    principal: Principal,
    action: str,
    target: AuditTarget | tuple[str, str | None, str | None] | None = None,
    detail: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> AuditLog:
    """Add one audit row to the caller's transaction (flush, no commit)."""
    target_type, target_id, target_name = (tuple(target) + (None, None))[:3] if target else (None, None, None)
    request_id = request_id_var.get()
    row = AuditLog(
        at=iso(now or clock.now()),
        actor_id=principal.user_id,
        actor_name=_actor_name(principal),
        actor_role=principal.role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=_clean_name(target_name),
        ip=principal.ip or None,
        request_id=None if request_id in (None, "", "-") else str(request_id)[:REQUEST_ID_COLUMN],
        detail_json=json.dumps(sanitize_detail(detail), ensure_ascii=False, separators=(",", ":")) if detail else None,
    )
    db.add(row)
    db.flush()
    return row


def record_login_failure(
    db: Session,
    principal: Principal,
    username: str,
    *,
    user_id: str | None = None,
    now: datetime | None = None,
    action: str = "auth.login.failure",
) -> AuditLog:
    """`auth.login.failure` (or `auth.recovery.failure`, via `action`), merged per
    `(username, ip)` within 60 s (`detail.count`).

    `username` is attacker-controlled text: control characters are stripped and it is
    clipped to the 40-char snapshot column.
    """
    moment = now or clock.now()
    name = _clean_name(username)
    horizon = iso(moment - FAILURE_MERGE_WINDOW)
    existing = db.execute(
        select(AuditLog)
        .where(
            AuditLog.action == action,
            AuditLog.target_name == name,
            AuditLog.ip == (principal.ip or None),
            AuditLog.at >= horizon,
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        try:
            count = int(json.loads(existing.detail_json or "{}").get("count", 1))
        except (ValueError, TypeError):
            count = 1
        existing.detail_json = json.dumps({"count": count + 1}, separators=(",", ":"))
        existing.at = iso(moment)
        db.flush()
        return existing
    return record(
        db,
        principal,
        action,
        AuditTarget("user", user_id, name),
        {"count": 1},
        now=moment,
    )


# ── reading ──────────────────────────────────────────────────────────────────

_CURSOR = re.compile(r"^(?P<at>\d{4}-\d{2}-\d{2}T[^|]+Z)\|(?P<id>\d+)$")


def parse_detail(detail_json: str | None) -> dict[str, Any] | None:
    """`detail_json` → dict for the API; anything that is not a JSON object reads as `None`."""
    if not detail_json:
        return None
    try:
        parsed = json.loads(detail_json)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def encode_cursor(row: AuditLog) -> str:
    return f"{row.at}|{row.id}"


def filtered(
    *,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_id: int | None = None,
):  # noqa: ANN201 - a SQLAlchemy `Select`
    """The one WHERE-clause builder: the list endpoint and the CSV export (§25.3.5a) share it."""
    statement = select(AuditLog)
    if action:
        statement = statement.where(AuditLog.action == action)
    if actor:
        statement = statement.where(or_(AuditLog.actor_id == actor, AuditLog.actor_name == actor))
    if target:
        statement = statement.where(or_(AuditLog.target_id == target, AuditLog.target_name == target))
    if since:
        statement = statement.where(AuditLog.at >= since)
    if until:
        statement = statement.where(AuditLog.at <= until)
    if max_id is not None:
        # A snapshot bound: the CSV export must not include the row it writes about itself.
        statement = statement.where(AuditLog.id <= max_id)
    return statement


def query(
    db: Session,
    *,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    before: str | None = None,
    max_limit: int = 200,
    max_id: int | None = None,
) -> tuple[list[AuditLog], str | None]:
    """Newest first, keyset-paginated on `(at, id)`; returns `(rows, next_cursor)`."""
    limit = max(1, min(int(limit), max_limit))
    statement = filtered(action=action, actor=actor, target=target, since=since, until=until, max_id=max_id)
    if before:
        match = _CURSOR.match(before)
        if match is None:
            raise InvalidCursor()
        at, row_id = match.group("at"), int(match.group("id"))
        statement = statement.where(or_(AuditLog.at < at, and_(AuditLog.at == at, AuditLog.id < row_id)))
    rows = list(db.execute(statement.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit + 1)).scalars())
    next_cursor = encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    return rows[:limit], next_cursor


def purge_expired(db: Session, now: datetime | None = None) -> int:
    """Delete rows older than `AUDIT_RETENTION_DAYS`. Returns the number removed (caller commits)."""
    days = int(get_settings().audit_retention_days)
    if days <= 0:
        return 0
    horizon = iso((now or clock.now()) - timedelta(days=days))
    result = db.execute(AuditLog.__table__.delete().where(AuditLog.at < horizon))
    return int(result.rowcount or 0)
