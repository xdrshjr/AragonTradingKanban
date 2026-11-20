"""根管理员引导（spec §24.3.13、D-1/D-2；AC-153、AC-169(root)、AC-176 的唯一性半条）。

离线恢复 CLI（`reset-root-password` / `create-admin` / `unlock`，AC-205）在文件末尾。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import cli
from app.config import get_settings
from app.models import AuditLog, User, UserSession
from app.security.passwords import verify_password
from app.services import bootstrap, user_service
from app.services.bootstrap import RootBootstrapError, ensure_root_admin

URL = "/api/v1/auth"
DEFAULT_PASSWORD = "13519126390"


@pytest.fixture()
def db(session):  # noqa: ANN001, ANN201
    return session


def roots(db) -> list[User]:  # noqa: ANN001
    return list(db.execute(select(User).where(User.role == "root")).scalars())


# ── 引导本身 ────────────────────────────────────────────────────────────────


def test_an_empty_database_gets_exactly_one_root(db) -> None:
    assert ensure_root_admin(db) is True
    (root,) = roots(db)
    assert root.username == "jdqqjr" and root.username_lc == "jdqqjr"
    assert root.status == "active" and root.created_by is None
    assert verify_password(DEFAULT_PASSWORD, root.password_hash)
    # 默认口令：标记为默认，并强制首次登录改密（D-2）。
    assert bool(root.password_is_default) is True
    assert bool(root.must_change_password) is True


def test_bootstrap_is_audited_without_the_password(db) -> None:
    ensure_root_admin(db)
    (row,) = db.execute(select(AuditLog).where(AuditLog.action == "root.bootstrap")).scalars().all()
    assert row.actor_role == "system" and row.target_name == "jdqqjr"
    assert DEFAULT_PASSWORD not in (row.detail_json or "")


def test_a_second_call_does_nothing(db) -> None:
    ensure_root_admin(db)
    assert ensure_root_admin(db) is False
    assert len(roots(db)) == 1


def test_an_existing_root_is_never_overwritten_by_a_changed_environment(db, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量只管"第一次"：否则运维改个 env 就能悄悄重置 root 密码，是后门。"""
    ensure_root_admin(db)
    (root,) = roots(db)
    original_hash = root.password_hash

    settings = get_settings()
    monkeypatch.setattr(settings, "root_admin_username", "someone_else", raising=False)
    monkeypatch.setattr(
        settings, "root_admin_password", type(settings.root_admin_password)("Brand-New-Pass-1"), raising=False
    )
    assert ensure_root_admin(db) is False

    (after,) = roots(db)
    assert after.username == "jdqqjr" and after.password_hash == original_hash
    assert user_service.get_by_username(db, "someone_else") is None


def test_the_root_username_and_password_come_from_the_environment_once(db, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "root_admin_username", "13519126390", raising=False)  # D-1：11 位数字也是合法用户名
    monkeypatch.setattr(
        settings, "root_admin_password", type(settings.root_admin_password)("Custom-Root-Pass-9"), raising=False
    )
    ensure_root_admin(db)
    (root,) = roots(db)
    assert root.username == "13519126390"
    assert verify_password("Custom-Root-Pass-9", root.password_hash)
    assert bool(root.password_is_default) is False
    assert bool(root.must_change_password) is False, "非默认口令不强制改密"


def test_force_change_can_be_switched_off_for_the_default_password(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_force_root_password_change", False, raising=False)
    ensure_root_admin(db)
    (root,) = roots(db)
    assert bool(root.password_is_default) is True
    assert bool(root.must_change_password) is False


def test_a_non_root_account_squatting_the_root_username_stops_startup(db) -> None:
    user_service.create_user(db, username="jdqqjr", password_hash="x", role="user", allow_reserved=True)
    db.commit()
    with pytest.raises(RootBootstrapError, match="jdqqjr"):
        ensure_root_admin(db)
    assert roots(db) == [], "不静默接管别人的账号"


def test_the_squatter_case_is_case_insensitive(db) -> None:
    user_service.create_user(db, username="JDQQJR", password_hash="x", role="admin", allow_reserved=True)
    db.commit()
    with pytest.raises(RootBootstrapError):
        ensure_root_admin(db)


def test_the_database_itself_forbids_a_second_root(db) -> None:
    ensure_root_admin(db)
    with pytest.raises(IntegrityError):
        user_service.create_user(db, username="another_root", password_hash="x", role="root", allow_reserved=True)
        db.commit()


def test_the_log_line_never_contains_the_password(db, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        ensure_root_admin(db)
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "root admin bootstrapped" in text
    assert DEFAULT_PASSWORD not in text


# ── lifespan 里的引导（经真实应用）─────────────────────────────────────────


def test_enforced_startup_bootstraps_root(auth_client, engine) -> None:
    with sessionmaker(bind=engine)() as db:
        assert [row.username for row in roots(db)] == ["jdqqjr"]


def test_open_mode_startup_does_not_bootstrap(client, engine) -> None:
    """`open` 没有人需要登录；切回 `enforced` 重启时才会引导（见 `main.lifespan`）。"""
    with sessionmaker(bind=engine)() as db:
        assert roots(db) == []


def test_the_documented_first_login_flow(auth_client) -> None:
    """AC-153：`jdqqjr/13519126390` 能登录，但只到改密页；改密后恢复正常。"""
    login = auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": DEFAULT_PASSWORD})
    assert login.status_code == 200
    me = login.json()
    assert me["user"]["role"] == "root"
    assert me["user"]["mustChangePassword"] is True and me["user"]["passwordIsDefault"] is True

    # 除 /auth/me|password|logout 外，其余全部 403 password_change_required。
    for path in ("/api/v1/reports", "/api/v1/watchlist", "/api/v1/cost/summary", f"{URL}/sessions"):
        blocked = auth_client.get(path)
        assert blocked.status_code == 403, path
        assert blocked.json()["error"]["code"] == "password_change_required"
    assert auth_client.get(f"{URL}/me").status_code == 200

    changed = auth_client.post(
        f"{URL}/password", json={"currentPassword": DEFAULT_PASSWORD, "newPassword": "A-Brand-New-Root-Pass-7"}
    )
    assert changed.status_code == 204
    me = auth_client.get(f"{URL}/me").json()
    assert me["user"]["mustChangePassword"] is False and me["user"]["passwordIsDefault"] is False
    assert auth_client.get("/api/v1/reports").status_code == 200
    assert auth_client.get("/api/v1/cost/summary").status_code == 200

    # 旧的公开口令从此不能登录。
    auth_client.post(f"{URL}/logout")
    assert (
        auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": DEFAULT_PASSWORD}).status_code == 401
    )
    assert (
        auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": "A-Brand-New-Root-Pass-7"}).status_code
        == 200
    )


def test_with_force_off_root_logs_in_straight_to_work(auth_client, monkeypatch: pytest.MonkeyPatch, engine) -> None:
    # 引导已经按默认（强制）发生过；模拟"部署者本来就设了 =0"：直接清标志位。
    with sessionmaker(bind=engine)() as db:
        root = user_service.get_root(db)
        root.must_change_password = 0
        db.commit()
    assert (
        auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": DEFAULT_PASSWORD}).status_code == 200
    )
    assert auth_client.get("/api/v1/reports").status_code == 200
    assert auth_client.get(f"{URL}/me").json()["user"]["passwordIsDefault"] is True, (
        "不强制，但标志仍在（界面据此显示红色横幅）"
    )


def test_auth_config_reports_the_forced_change_policy(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    assert auth_client.get(f"{URL}/config").json()["forceRootPasswordChange"] is True
    monkeypatch.setattr(get_settings(), "auth_force_root_password_change", False, raising=False)
    assert auth_client.get(f"{URL}/config").json()["forceRootPasswordChange"] is False


def test_bootstrap_module_exposes_the_documented_defaults() -> None:
    assert bootstrap.DEFAULT_ROOT_USERNAME == "jdqqjr"
    assert bootstrap.DEFAULT_ROOT_PASSWORD == DEFAULT_PASSWORD


# ── 离线恢复 CLI（AC-205）─────────────────────────────────────────────────

CLI_PASSWORD = "Recovered-Root-Pass-5k"


def run_cli(*argv: str) -> int:
    return cli.main(list(argv))


def audit_rows(engine, action: str) -> list[AuditLog]:  # noqa: ANN001
    with sessionmaker(bind=engine)() as db:
        return list(db.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).scalars())


def test_reset_root_password_revokes_sessions_forces_change_and_is_audited(
    auth_client,
    engine,
    capsys: pytest.CaptureFixture[str],  # noqa: ANN001
) -> None:
    """AC-205：重置后旧会话全失效、需强制改密、写审计。"""
    with sessionmaker(bind=engine)() as db:  # 模拟"root 早已改过密码、正在线上用着"
        root = user_service.get_root(db)
        root.must_change_password = 0
        root.password_is_default = 0
        db.commit()
    other = auth_client.__class__(auth_client.app)
    for client in (auth_client, other):
        assert client.post(f"{URL}/login", json={"username": "jdqqjr", "password": DEFAULT_PASSWORD}).status_code == 200
    assert auth_client.get("/api/v1/cost/summary").status_code == 200
    with sessionmaker(bind=engine)() as db:  # 有人在撞 root 的口令：失败计数不为零
        user_service.get_root(db).failed_logins = 4
        db.commit()

    assert run_cli("reset-root-password", "--password", CLI_PASSWORD) == 0

    # 旧会话立即失效（下一次请求即 guest），旧口令不能再登录，新口令能但要先改密
    assert auth_client.get(f"{URL}/me").json()["authenticated"] is False
    assert other.get(f"{URL}/me").json()["authenticated"] is False
    old = auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": DEFAULT_PASSWORD})
    assert old.status_code == 401
    login = auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": CLI_PASSWORD})
    assert login.status_code == 200 and login.json()["user"]["mustChangePassword"] is True
    blocked = auth_client.get("/api/v1/cost/summary")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "password_change_required"

    with sessionmaker(bind=engine)() as db:
        reasons = {row.revoked_reason for row in db.execute(select(UserSession)).scalars() if row.revoked_at}
        assert "cli" in reasons
        assert user_service.get_root(db).failed_logins == 0  # 锁定也一并解除
    (row,) = audit_rows(engine, "root.password.cli_reset")
    assert row.actor_name == "system" and row.target_name == "jdqqjr"
    assert '"via":"cli"' in row.detail_json and CLI_PASSWORD not in (row.detail_json or "")
    out = capsys.readouterr()
    assert CLI_PASSWORD not in out.out + out.err


def test_reset_root_password_enforces_the_password_policy(auth_client, engine, capsys) -> None:  # noqa: ANN001
    assert run_cli("reset-root-password", "--password", "12345678") == 1
    err = capsys.readouterr().err
    assert "密码不符合策略" in err and "12345678" not in err
    assert audit_rows(engine, "root.password.cli_reset") == []
    # root 的口令没有被改动
    assert (
        auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": DEFAULT_PASSWORD}).status_code == 200
    )


def test_reset_root_password_needs_a_root(engine, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    """空库（服务还没启动过）：提示先启动一次，退出码 2，什么都不写。"""
    from app.db import session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    assert run_cli("reset-root-password", "--password", CLI_PASSWORD) == 2
    assert "root" in capsys.readouterr().err
    with sessionmaker(bind=engine)() as db:
        assert roots(db) == []


def test_the_interactive_prompt_asks_twice_and_never_takes_a_mismatch(
    auth_client,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    import getpass

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    answers = iter([CLI_PASSWORD, CLI_PASSWORD + "x"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert run_cli("reset-root-password") == 1
    assert "不一致" in capsys.readouterr().err

    answers = iter([CLI_PASSWORD, CLI_PASSWORD])
    assert run_cli("reset-root-password") == 0
    assert auth_client.post(f"{URL}/login", json={"username": "jdqqjr", "password": CLI_PASSWORD}).status_code == 200


def test_without_a_terminal_or_a_password_the_cli_refuses_instead_of_hanging(
    auth_client,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert run_cli("reset-root-password") == 1
    assert "--password" in capsys.readouterr().err


def test_create_admin_with_a_password(auth_client, engine, capsys) -> None:  # noqa: ANN001
    assert run_cli("create-admin", "rescuer", "--password", CLI_PASSWORD) == 0
    with sessionmaker(bind=engine)() as db:
        row = user_service.get_by_username(db, "rescuer")
        assert row.role == "admin" and row.must_change_password == 1 and row.created_by is None
        assert verify_password(CLI_PASSWORD, row.password_hash)
    (audit,) = audit_rows(engine, "user.create")
    assert audit.target_name == "rescuer" and '"via":"cli"' in audit.detail_json
    assert CLI_PASSWORD not in capsys.readouterr().out
    # 用它登录：可以，且被卡在改密页
    login = auth_client.post(f"{URL}/login", json={"username": "rescuer", "password": CLI_PASSWORD})
    assert login.status_code == 200 and login.json()["user"]["role"] == "admin"


def test_create_admin_without_a_password_prints_a_temporary_one_exactly_once(
    auth_client,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert run_cli("create-admin", "tempadmin") == 0
    out = capsys.readouterr().out
    temporary = out.split("：")[-1].strip()
    assert len(temporary) == 12
    assert auth_client.post(f"{URL}/login", json={"username": "tempadmin", "password": temporary}).status_code == 200
    (audit,) = audit_rows(engine, "user.create")
    assert temporary not in (audit.detail_json or "")


@pytest.mark.parametrize("username", ["ab", "bad name", "admin", "jdqqjr"])
def test_create_admin_refuses_bad_or_reserved_names(auth_client, username: str, capsys) -> None:  # noqa: ANN001
    assert run_cli("create-admin", username, "--password", CLI_PASSWORD) == 1
    assert "无法创建" in capsys.readouterr().err


def test_create_admin_refuses_a_taken_name_and_a_weak_password(auth_client, make_user, capsys) -> None:  # noqa: ANN001
    make_user("already_here")
    assert run_cli("create-admin", "already_here", "--password", CLI_PASSWORD) == 1
    assert run_cli("create-admin", "fresh_admin", "--password", "password") == 1


def test_unlock_clears_a_lockout_and_is_audited(auth_client, make_user, engine, capsys) -> None:  # noqa: ANN001
    from datetime import timedelta

    from app.db.base import iso
    from app.security import clock

    victim = make_user("locked_one", failed_logins=5, locked_until=iso(clock.now() + timedelta(minutes=15)))
    locked = auth_client.post(f"{URL}/login", json={"username": "locked_one", "password": victim.password})
    assert locked.status_code == 423
    assert run_cli("unlock", "LOCKED_ONE") == 0  # 用户名不区分大小写
    ok = auth_client.post(f"{URL}/login", json={"username": "locked_one", "password": victim.password})
    assert ok.status_code == 200
    (audit,) = audit_rows(engine, "user.unlock")
    assert audit.actor_name == "system" and '"wasLocked":true' in audit.detail_json


def test_unlock_of_an_unknown_or_deleted_user_is_refused(auth_client, make_user, engine, capsys) -> None:  # noqa: ANN001
    assert run_cli("unlock", "nobody_here") == 1
    gone = make_user("tombstoned")
    with sessionmaker(bind=engine)() as db:
        user_service.anonymize(db, user_service.get_by_id(db, gone.id))
        db.commit()
    assert run_cli("unlock", "tombstoned") == 1
    assert audit_rows(engine, "user.unlock") == []


def test_the_module_entry_point_reports_an_unmigrated_database_with_exit_code_two(tmp_path: Path) -> None:
    """`python -m app.cli`：真正的入口，真正的子进程。空库文件 → 退出码 2 + 可读提示，且不泄露密码。"""
    api_dir = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DATABASE_URL": f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"}
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "reset-root-password", "--password", CLI_PASSWORD],
        cwd=api_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "users 表" in result.stderr
    assert CLI_PASSWORD not in result.stdout + result.stderr


def test_the_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main([])
    assert raised.value.code == 2
