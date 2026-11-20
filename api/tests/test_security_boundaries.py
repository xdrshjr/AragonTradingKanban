"""§24 的依赖方向守卫（spec E-13；`test_boundaries.py` 的续篇，不改它）。

规则：

1. `services/` **可以** import `security/`；`security/` **不可以** import `services/` 或 `routers/`——
   唯一例外是 `security/deps.py`，它是 HTTP 适配层（Cookie → 会话查询），与路由同侧。
2. `services/` 不 import `security.deps`（否则 `deps → services → deps` 成环，且服务层就知道 HTTP 了）。
3. `agent/` 不 import `security/`（引擎驱动层不该认识"人"）。
4. 只有路由与 `main.py` 可以 import `security.deps`。

同样按 AST 判定、必须有正控（扫到足够多的文件、构造样本能触发检测器）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
SECURITY = APP_ROOT / "security"
SERVICES = APP_ROOT / "services"
ROUTERS = APP_ROOT / "routers"
AGENT = APP_ROOT / "agent"


def python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def absolute_imports(path: Path) -> set[str]:
    """Resolved dotted module names a file imports (relative imports resolved against `app`)."""
    package = list(path.relative_to(APP_ROOT.parent).with_suffix("").parts[:-1])  # e.g. ['app', 'security']
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                target = node.module or ""
            modules.add(target)
            # `from . import x` / `from ..services import y` import submodules by name.
            modules.update(f"{target}.{alias.name}" for alias in node.names)
    return modules


def imports_package(path: Path, package: str) -> list[str]:
    return sorted(m for m in absolute_imports(path) if m == package or m.startswith(package + "."))


def test_the_detector_has_teeth(tmp_path: Path) -> None:
    probe = APP_ROOT / "security" / "_probe_tmp.py"
    probe.write_text("from ..services import session_service\nimport app.routers.auth\n", encoding="utf-8")
    try:
        assert imports_package(probe, "app.services") == ["app.services", "app.services.session_service"]
        assert imports_package(probe, "app.routers") == ["app.routers.auth"]
    finally:
        probe.unlink()
    assert tmp_path is not None


def test_the_walk_sees_enough_files() -> None:
    assert len(python_files(SECURITY)) >= 10
    assert len(python_files(SERVICES)) >= 20
    assert (SECURITY / "deps.py").is_file()


def test_security_never_imports_services_or_routers_except_deps() -> None:
    offenders = []
    for path in python_files(SECURITY):
        if path.name == "deps.py":
            continue
        for package in ("app.services", "app.routers"):
            offenders.extend(f"{path.name}: {module}" for module in imports_package(path, package))
    assert offenders == [], f"security/ 反向依赖了 services/routers：{offenders}"


def test_deps_is_the_one_security_module_allowed_to_reach_services() -> None:
    deps_services = imports_package(SECURITY / "deps.py", "app.services")
    assert any(module.endswith("session_service") for module in deps_services), "deps 应当经 session_service 解析会话"
    assert imports_package(SECURITY / "deps.py", "app.routers") == []


def test_services_may_import_security_but_never_its_http_adapter() -> None:
    uses_security = False
    offenders = []
    for path in python_files(SERVICES):
        if imports_package(path, "app.security"):
            uses_security = True
        offenders.extend(f"{path.name}: {module}" for module in imports_package(path, "app.security.deps"))
    assert uses_security, "services 里没有任何文件用 security/：方向假设已失效"
    assert offenders == [], f"services 依赖了 HTTP 适配层 security.deps：{offenders}"


def test_agent_does_not_know_about_people() -> None:
    offenders = [
        f"{path.name}: {module}" for path in python_files(AGENT) for module in imports_package(path, "app.security")
    ]
    assert offenders == []


def test_only_routers_and_main_import_the_http_adapter() -> None:
    importers = {
        path.relative_to(APP_ROOT).as_posix()
        for path in python_files(APP_ROOT)
        if imports_package(path, "app.security.deps") and path.name != "deps.py"
    }
    allowed_prefixes = ("routers/", "main.py")
    stray = sorted(name for name in importers if not name.startswith(allowed_prefixes))
    assert stray == [], f"这些文件不该 import security.deps：{stray}"
    assert "routers/auth.py" in importers and "main.py" in importers


@pytest.mark.parametrize(
    "module",
    [
        "principal",
        "permissions",
        "passwords",
        "weak_passwords",
        "ratelimit",
        "csrf",
        "objects",
        "policy",
        "clock",
        "ipaddr",
    ],
)
def test_pure_security_modules_do_not_import_fastapi_request_machinery(module: str) -> None:
    """`Principal` 等必须能被 `services/` 在没有 HTTP 的场景下使用。`csrf` 只接受头的映射。"""
    modules = absolute_imports(SECURITY / f"{module}.py")
    assert not {m for m in modules if m.startswith(("fastapi", "starlette"))}, module
