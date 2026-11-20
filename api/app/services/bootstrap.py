"""Root-admin bootstrap (spec §24.3.13, D-1/D-2).

`ensure_root_admin()` runs in `lifespan` right after the migrations and before run
recovery. The rules, each with a test:

* **No root in the database → create one** from `ROOT_ADMIN_USERNAME` / `ROOT_ADMIN_PASSWORD`
  (defaults `jdqqjr` / `13519126390`). `password_is_default` records whether the password
  is the built-in default; with `AUTH_FORCE_ROOT_PASSWORD_CHANGE=1` (the default) that
  also sets `must_change_password`, so the public password only ever gets you as far as
  the change-password page.
* **A root already exists → do nothing.** The environment is read exactly once: otherwise
  anyone who can edit the service's env file could silently reset root's password — a
  back door.
* **A non-root account already owns the root username → refuse to start** and say why. The
  reserved-word list should make that impossible, but a hand-edited database is exactly
  where "silently adopting" a stranger's account as root would hurt.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from ..logging import get_logger
from ..security import clock
from ..security.passwords import hash_password
from ..security.principal import SYSTEM
from . import audit_service, user_service

logger = get_logger("bootstrap")

#: The password written in the requirements. Compared (not just defaulted) so that
#: `password_is_default` is true even when someone sets the env var to the same string.
DEFAULT_ROOT_PASSWORD = "13519126390"
DEFAULT_ROOT_USERNAME = "jdqqjr"


class RootBootstrapError(RuntimeError):
    """A non-root user already holds the root username."""


def ensure_root_admin(db: Session) -> bool:
    """Create the root admin if the database has none. Returns True when it created one."""
    existing = user_service.get_root(db)
    if existing is not None:
        return False

    settings = get_settings()
    username = (settings.root_admin_username or DEFAULT_ROOT_USERNAME).strip()
    password = settings.root_admin_password.get_secret_value() or DEFAULT_ROOT_PASSWORD

    clash = user_service.get_by_username(db, username)
    if clash is not None:
        raise RootBootstrapError(
            f"无法引导根管理员：用户名 {username!r} 已被一个非 root 账号（role={clash.role}）占用。"
            "请检查数据库，或用 ROOT_ADMIN_USERNAME 指定另一个用户名后重启。"
        )

    is_default = password == DEFAULT_ROOT_PASSWORD
    must_change = bool(settings.auth_force_root_password_change and is_default)
    # The bootstrap password is exempt from `password_rule_failures` (it is public by
    # requirement); the forced change on first login is what covers it.
    root = user_service.create_user(
        db,
        username=username,
        password_hash=hash_password(password),
        role="root",
        must_change_password=must_change,
        password_is_default=is_default,
        allow_reserved=True,
        now=clock.now(),
    )
    audit_service.record(
        db,
        SYSTEM,
        "root.bootstrap",
        audit_service.AuditTarget("user", root.id, root.username),
        {"passwordIsDefault": is_default, "mustChangePassword": must_change},
    )
    db.commit()
    logger.warning(
        "root admin bootstrapped username=%s default_password=%s must_change_password=%s",
        username,
        is_default,
        must_change,
    )
    return True
