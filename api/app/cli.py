"""Offline recovery CLI (spec §24.3.13, AC-205).

    python -m app.cli reset-root-password [--password PW]
    python -m app.cli create-admin <username> [--password PW]
    python -m app.cli unlock <username>
    python -m app.cli clear-recovery <username>

**The authorisation model is "you can reach the database file".** There is no e-mail
recovery and no back-door route: a forgotten root password is fixed by someone who can run a
command on the machine that holds `DATABASE_URL`'s database — exactly the people who could
also edit that file by hand. That is why these commands exist at all, and why none of them is
reachable over HTTP.

What every command does, in one transaction:

* writes an **audit row** (`root.password.cli_reset` / `user.create` / `user.unlock`, actor
  `system`, `detail.via = "cli"`, never the password);
* `reset-root-password` additionally **revokes every session** of root (reason `cli`) and sets
  `must_change_password`, so the password typed into a terminal is only ever good for getting
  as far as the change-password page;
* refuses a password the normal policy refuses. (The *bootstrap* default is exempt from the
  policy because it is public by requirement — that exemption does not extend to a password an
  operator chooses now.)

Exit codes: `0` done, `1` refused (bad input / policy / unknown user), `2` the environment is
not ready (database unreachable or not migrated).

Safe to run while the service is up: SQLite WAL lets the CLI write, and a revoked session is
rejected on the service's very next request (sessions are validated against the table every
time, see `session_service.resolve`).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from .config import get_settings
from .db import session as db_session
from .errors import AppError
from .security import clock
from .security.passwords import MAX_LENGTH, hash_password, password_rule_failures
from .security.principal import SYSTEM
from .services import admin_user_service, audit_service, session_service, user_service
from .services.audit_service import AuditTarget

EXIT_OK, EXIT_REFUSED, EXIT_ENVIRONMENT = 0, 1, 2

#: Human text for the password rule ids (`security/passwords.py::ALL_RULES`).
RULE_TEXT = {
    "min_length": "至少 8 个字符",
    "max_length": f"不超过 {MAX_LENGTH} 个字符",
    "contains_username": "不能包含用户名",
    "char_classes": "至少包含两类字符（大写 / 小写 / 数字 / 符号）",
    "common_password": "不能是常见弱口令",
    "repeated_chars": "不能是同一个字符的重复",
    "sequential_chars": "不能是连续序列（如 abcd、1234）",
}


class CliError(Exception):
    """A refusal the operator should read; `code` becomes the process exit status."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.code = code


def _redacted_database() -> str:
    try:
        return make_url(get_settings().database_url).render_as_string(hide_password=True)
    except Exception:  # pragma: no cover - a malformed URL fails later, with a better message
        return "<DATABASE_URL>"


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - no passwd entry in some containers
        return "unknown"


def _open() -> Session:
    """A session on the configured database, after checking the schema is there at all."""
    try:
        session = db_session.SessionLocal()
        if not inspect(session.get_bind()).has_table("users"):
            session.close()
            raise CliError(
                f"数据库 {_redacted_database()} 里还没有 users 表："
                "请先启动一次服务（它会自动迁移到最新版本），再运行本命令。",
                EXIT_ENVIRONMENT,
            )
        return session
    except CliError:
        raise
    except (OperationalError, SQLAlchemyError) as exc:
        raise CliError(f"无法连接数据库 {_redacted_database()}：{exc.__class__.__name__}", EXIT_ENVIRONMENT) from exc


def _read_password(provided: str | None) -> str:
    """`--password` wins (scripts); otherwise ask twice on the terminal without echo."""
    if provided is not None:
        return provided
    if not sys.stdin.isatty():
        raise CliError("没有终端可以交互输入密码：请用 --password 传入，或在终端里运行。")
    first = getpass.getpass("新密码：")
    if first != getpass.getpass("再输入一次："):
        raise CliError("两次输入不一致。")
    return first


def _check_policy(password: str, username: str) -> None:
    failures = password_rule_failures(password, username)
    if failures:
        raise CliError("密码不符合策略：" + "；".join(RULE_TEXT.get(rule, rule) for rule in failures) + "。")


def _lookup(db: Session, username: str):  # noqa: ANN202 - the ORM `User`
    user = user_service.get_by_username(db, username)
    if user is None or user.status == "deleted":
        raise CliError(f"没有名为 {username!r} 的用户。")
    return user


# ── commands ─────────────────────────────────────────────────────────────────


def reset_root_password(password: str | None = None) -> str:
    """Set root's password, revoke its sessions, force a change at the next login."""
    db = _open()
    try:
        root = user_service.get_root(db)
        if root is None:
            raise CliError(
                "库里还没有 root 账号：请先启动一次服务让它自动引导（见 README「根管理员」）。", EXIT_ENVIRONMENT
            )
        new_password = _read_password(password)
        _check_policy(new_password, root.username)
        moment = clock.now()
        user_service.set_password(db, root, hash_password(new_password), must_change=True, is_default=False, now=moment)
        user_service.unlock(db, root, moment)
        revoked = session_service.revoke_all_for_user(db, root.id, reason="cli", now=moment)
        audit_service.record(
            db,
            SYSTEM,
            "root.password.cli_reset",
            AuditTarget("user", root.id, root.username),
            {"via": "cli", "osUser": _os_user(), "revokedSessions": revoked},
            now=moment,
        )
        db.commit()
        return f"root（{root.username}）的密码已重置；{revoked} 个会话已吊销；下次登录必须先修改密码。"
    finally:
        db.close()


def create_admin(username: str, password: str | None = None) -> str:
    """Create an `admin` account (a stranded install has no other way to get a second one)."""
    db = _open()
    try:
        temporary = None
        if password is None and sys.stdin.isatty():
            password = _read_password(None)
        elif password is None:
            temporary = admin_user_service.generate_temp_password(username)
            password = temporary
        _check_policy(password, username)
        moment = clock.now()
        try:
            user = user_service.create_user(
                db,
                username=username,
                password_hash=hash_password(password),
                role="admin",
                must_change_password=True,
                now=moment,
            )
        except AppError as exc:
            db.rollback()
            raise CliError(f"无法创建：{exc.message}") from exc
        audit_service.record(
            db,
            SYSTEM,
            "user.create",
            AuditTarget("user", user.id, user.username),
            {"role": "admin", "via": "cli", "osUser": _os_user()},
            now=moment,
        )
        db.commit()
        text = f"管理员 {user.username} 已创建；首次登录必须先修改密码。"
        if temporary:
            text += f"\n临时密码（只显示这一次）：{temporary}"
        return text
    finally:
        db.close()


def unlock(username: str) -> str:
    db = _open()
    try:
        user = _lookup(db, username)
        moment = clock.now()
        was_locked = user_service.is_locked(user, moment)
        user_service.unlock(db, user, moment)
        audit_service.record(
            db,
            SYSTEM,
            "user.unlock",
            AuditTarget("user", user.id, user.username),
            {"via": "cli", "osUser": _os_user(), "wasLocked": was_locked},
            now=moment,
        )
        db.commit()
        return f"{user.username} 已解锁。"
    finally:
        db.close()


def clear_recovery(username: str) -> str:
    """Forget an account's recovery key (spec 25.3.3, AC-223). The ops fallback for "this key
    leaked": the owner can mint a new one from the account page after signing in. Audited as
    `auth.recovery.rotate` with `detail.cleared`, actor `system`."""
    db = _open()
    try:
        user = _lookup(db, username)
        moment = clock.now()
        had_key = bool(user.recovery_hash)
        user.recovery_hash = None
        user.recovery_set_at = None
        audit_service.record(
            db,
            SYSTEM,
            "auth.recovery.rotate",
            AuditTarget("user", user.id, user.username),
            {"via": "cli", "osUser": _os_user(), "cleared": True, "hadKey": had_key},
            now=moment,
        )
        db.commit()
        return f"{user.username} 的恢复码已清除。"
    finally:
        db.close()


# ── entry point ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="离线恢复：需要能访问数据库文件的机器（这就是它的授权模型）。",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    reset = commands.add_parser("reset-root-password", help="重置 root 密码（吊销其全部会话并强制下次登录改密）")
    reset.add_argument("--password", help="新密码；缺省时在终端里交互输入（不回显）。命令行参数会留在 shell 历史里")

    create = commands.add_parser("create-admin", help="创建一个管理员账号")
    create.add_argument("username")
    create.add_argument("--password", help="初始密码；缺省且无终端时生成临时密码并打印一次")

    lock = commands.add_parser("unlock", help="解除某用户的登录锁定")
    lock.add_argument("username")

    clear = commands.add_parser("clear-recovery", help="清除某账号的恢复码（密钥泄露时的运维兜底）")
    clear.add_argument("username")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "reset-root-password":
            message = reset_root_password(args.password)
        elif args.command == "create-admin":
            message = create_admin(args.username, args.password)
        elif args.command == "clear-recovery":
            message = clear_recovery(args.username)
        else:
            message = unlock(args.username)
    except CliError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.code
    print(message)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
