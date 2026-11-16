"""依赖方向守卫（AC-38 / R-14，§18.4 的四条机器化规则）。

AC-38 把"架构约定"降级成"可以 grep 的断言"，因为这个项目最容易腐化的不是算法，
而是层次：今天有人从 `services/` import `routers/` 拿一个响应模型，明天就有人把
HTTP 语义灌进服务层。四条规则（§18.4）：

1. `api/app/**` 不出现 `create_all`（R-14：schema 只归 Alembic，`create_all` 仅限
   `tests/conftest.py`）；
2. `api/app/**` 不 import `react` / `next/`（后端不许长前端依赖）；
3. `web/lib`、`web/components`、`web/app` 不 import `aragon` / `anthropic` / `openai`
   / `akshare` / `yfinance`（浏览器里不许有生成引擎或数据源的 SDK，也不许藏 key）；
4. `services/` 不 import `routers/`，`agent/` 不 import `services/`（单向依赖）。

实现上有两点刻意的选择：

* **按 AST / import 语句判定，不按裸字符串**。注释和文档字符串里提到一个名词是
  说明，不是依赖；把说明也判成违规，守卫就只会被加 `# noqa` 消掉。
* **必须有正控**。一个"扫到 0 个文件所以全部通过"的守卫比没有守卫更糟，
  所以这里断言 `> 50` 个文件被读到，并用构造样本验证检测器本身有效。
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

#: AC-38 的字面判据：这个 token 只允许出现在 tests/conftest.py。
BUILD_HELPER = "create" + "_all"
REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
APP_ROOT = API_ROOT / "app"
WEB_ROOT = REPO_ROOT / "web"

#: 目录名级跳过：构建产物与虚拟环境里既有几万个文件，也有别人的 import。
SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        ".next",
        "playwright-report",
        "test-results",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".css",
        ".scss",
        ".json",
        ".md",
        ".j2",
        ".mako",
        ".html",
        ".txt",
        ".yml",
        ".yaml",
        ".toml",
    }
)

#: AC-38 第三条：浏览器侧不许出现的模块名。
FORBIDDEN_WEB_MODULES = ("aragon", "anthropic", "openai", "akshare", "yfinance")


# ── 扫描与解析的脚手架 ──────────────────────────────────────────────────────


def _walk(root: Path) -> list[Path]:
    """递归收集文本文件，目录名级跳过构建产物。"""
    if not root.exists():  # pragma: no cover - 仓库结构变了才会走到
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in TEXT_SUFFIXES
        and not any(part in SKIP_DIR_NAMES for part in path.parts)
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _python_imports(source: str) -> list[str]:
    """`ast` 解析出的模块名：`import a.b` / `from .x import y` 都覆盖。

    解析失败（比如语法只有未来版本才支持）时退回逐行正则，宁可少扫也不误报。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - app 代码始终可解析
        return [
            match.group(1)
            for match in re.finditer(r"^\s*(?:from|import)\s+([\w.]+)", source, re.MULTILINE)
        ]
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * (node.level or 0)
            modules.append(f"{prefix}{node.module or ''}")
    return modules


#: `import x from "y"` / `require("y")` / `import("y")` / `import "y"`。
_JS_IMPORT_PATTERN = re.compile(
    r"""(?:\bfrom|\brequire\(|\bimport\()\s*['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _js_imports(source: str) -> list[str]:
    """前端侧的 import 说明符（含 `@/lib/api` 这类别名与 `next/font`）。"""
    return [a or b for a, b in _JS_IMPORT_PATTERN.findall(source)]


def _module_matches(module: str, needles: tuple[str, ...]) -> bool:
    """模块名匹配：`aragon` 命中 `aragon`、`aragon/client`、`@aragon/ui`、`aragon.x`，
    不命中形近词 `aragonite` / `reactive`。

    按 `/` 与 `.` 切成段再比，这样 `akshare.stock`、`app.routers.schedules`、
    `from ..routers` 三种写法都能命中同一个规则。
    """
    lowered = module.lower().lstrip("./")
    segments = {part for part in re.split(r"[/.]", lowered) if part}
    for needle in needles:
        target = needle.lower()
        if target.endswith("/"):  # `next/`
            if lowered == target[:-1] or lowered.startswith(target):
                return True
            continue
        if target in segments or f"@{target}" in segments:
            return True
        # `@scope/aragon` 这类作用域名不算，但 `x/aragon/y` 这种路径段要算。
        if f"/{target}/" in f"/{lowered}/":
            return True
    return False


def _code_only_lines(source: str) -> set[int]:
    """非注释、非文档字符串的行号——用来区分"说明"与"依赖"。"""
    prose: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                prose.update(range(token.start[0], token.end[0] + 1))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - 只可能是坏文件
        return set()
    total = set(range(1, source.count("\n") + 2))
    return total - prose


def _create_all_calls(source: str) -> list[int]:
    """真正的 `*.create_all(...)` 调用行号：`Base.metadata.create_all(bind)` 之类。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_all"
        ):
            lines.append(node.lineno)
    return lines


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app_files() -> list[Path]:
    return [path for path in _walk(APP_ROOT) if "__pycache__" not in path.parts]


@pytest.fixture(scope="module")
def web_files() -> list[Path]:
    return _walk(WEB_ROOT)


# ── 正控：守卫必须先证明自己看得见东西 ──────────────────────────────────────


def test_the_walk_is_not_empty(app_files: list[Path], web_files: list[Path]) -> None:
    """AC-38 正控：`> 50` 个文件被真读到，且两棵树都在。

    没有这条，"扫不到文件所以零违规"会让整个 test_boundaries 变成绿色摆设。
    """
    assert len(app_files) + len(web_files) > 50, (
        f"扫描到的文件太少（app={len(app_files)}, web={len(web_files)}），"
        "守卫可能在空走上通过"
    )
    assert len(app_files) > 20
    assert len(web_files) > 20
    # 关键哨兵必须被扫到（路径写错会立刻暴露）。
    names = {path.name for path in app_files}
    assert "main.py" in names
    assert "scheduler.py" in names
    assert any(path.suffix == ".tsx" for path in web_files)
    # 跳过目录确实生效。
    assert not any("__pycache__" in path.parts for path in app_files)
    assert not any("node_modules" in path.parts for path in web_files)


def test_the_detectors_have_teeth() -> None:
    """AC-38 正控：检测器对构造样本必须报警，否则"零违规"没有意义。"""
    assert _create_all_calls("Base.metadata.create_all(engine)\n") == [1]
    assert _module_matches("react", ("react", "next/"))
    assert _module_matches("next/font/google", ("react", "next/"))
    assert _module_matches("akshare.stock", FORBIDDEN_WEB_MODULES)
    assert _module_matches("@aragon/ui", FORBIDDEN_WEB_MODULES)
    assert any(_module_matches(m, ("routers", "../routers")) for m in _python_imports("from ..routers import x\n"))
    assert any(_module_matches(m, ("services",)) for m in _python_imports("from ..services import report_service\n"))
    assert _js_imports("import x from 'aragon'\n") == ["aragon"]
    assert _js_imports("const y = require('openai')\n") == ["openai"]
    assert _js_imports("await import('yfinance')\n") == ["yfinance"]
    # 反控：形近词不算违规。
    assert not _module_matches("aragonite", FORBIDDEN_WEB_MODULES)
    assert not _module_matches("reactive", ("react",))


# ── 规则 1：api/app 不许 create_all（R-14） ─────────────────────────────────


def test_app_never_calls_create_all(app_files: list[Path]) -> None:
    """AC-38 / R-14：schema 的唯一真源是 Alembic，应用代码不得自己建表。"""
    offenders = []
    for path in app_files:
        if path.suffix != ".py":
            continue
        for line in _create_all_calls(_read(path)):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}")
    assert offenders == [], f"api/app 里出现了 create_all() 调用：{offenders}"


def test_create_all_mentions_are_prose_only(app_files: list[Path]) -> None:
    """AC-38：`create_all` 只允许作为**说明文字**出现（代码行上不许有）。"""
    offenders = []
    for path in app_files:
        if path.suffix != ".py":
            continue
        source = _read(path)
        if "create_all" not in source:
            continue
        code_lines = _code_only_lines(source)
        for number, line in enumerate(source.splitlines(), 1):
            if "create_all" in line and number in code_lines:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert offenders == [], f"create_all 出现在可执行代码里：{offenders}"


def test_no_raw_create_all_token_anywhere_in_app(app_files: list[Path]) -> None:
    """AC-38 字面版：`api/app/**` 全文不出现建表辅助函数的 token。"""
    offenders = [str(path.relative_to(REPO_ROOT)) for path in app_files if BUILD_HELPER in _read(path)]
    assert offenders == [], f"api/app 里出现了建表辅助函数的名字：{offenders}"


# ── 规则 2：api/app 不许 import react / next ────────────────────────────────


def test_app_never_imports_react_or_next(app_files: list[Path]) -> None:
    """AC-38：后端不 import 前端框架（这里按 import 语句判，不按散文）。"""
    offenders = []
    for path in app_files:
        if path.suffix != ".py":
            continue
        for module in _python_imports(_read(path)):
            if _module_matches(module, ("react", "react-dom", "next", "next/")):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")
    assert offenders == [], f"api/app 出现了前端框架 import：{offenders}"


# ── 规则 3：web 侧不许 import 生成引擎 / 数据源 / 后端 SDK ──────────────────


def test_web_never_imports_vendor_or_engine_sdks(web_files: list[Path]) -> None:
    """AC-38：`web/lib`、`web/components`、`web/app` 不许 import 生成引擎或数据源。

    浏览器里没有 key，也不该有 SDK：那些东西只能在服务端进程里（§7.1 的能力探测
    与 `DATA_PROVIDERS` 配置都在后端一处）。
    """
    watched = {
        (WEB_ROOT / "lib").resolve(),
        (WEB_ROOT / "components").resolve(),
        (WEB_ROOT / "app").resolve(),
    }
    offenders = []
    scanned = 0
    for path in web_files:
        resolved = path.resolve()
        if not any(root == resolved or root in resolved.parents for root in watched):
            continue
        scanned += 1
        for specifier in _js_imports(_read(path)):
            if _module_matches(specifier, FORBIDDEN_WEB_MODULES):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {specifier}")
    assert scanned > 20, f"web/lib+components+app 只扫到 {scanned} 个文件，判据失效"
    assert offenders == [], f"前端引用了禁止的模块：{offenders}"


# ── 规则 4：services 不依赖 routers；agent 不依赖 services ──────────────────


def test_services_never_import_routers(app_files: list[Path]) -> None:
    """AC-38：服务层不知道 HTTP 存在——`routers` 只能单向依赖 `services`。"""
    services_dir = (APP_ROOT / "services").resolve()
    offenders = []
    for path in app_files:
        if path.suffix != ".py" or services_dir not in path.resolve().parents:
            continue
        source = _read(path)
        for module in _python_imports(source):
            if _module_matches(module, ("routers",)):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")
        # 兜底：`importlib.import_module("app.routers…")` 这类字符串导入。
        for match in re.finditer(r"[\"'](?:app\.)?\.?routers[\w.]*[\"']", source):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: 字符串导入 {match.group(0)}")
    assert offenders == [], f"services 反向依赖了 routers：{offenders}"


def test_agent_never_imports_services(app_files: list[Path]) -> None:
    """AC-38：`agent/` 是生成引擎的薄驱动层，不得依赖 `services/`（否则循环依赖）。"""
    agent_dir = (APP_ROOT / "agent").resolve()
    offenders = []
    for path in app_files:
        if path.suffix != ".py" or agent_dir not in path.resolve().parents:
            continue
        source = _read(path)
        for module in _python_imports(source):
            if _module_matches(module, ("services",)):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")
        for match in re.finditer(r"[\"'](?:app\.)?\.?services[\w.]*[\"']", source):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: 字符串导入 {match.group(0)}")
    assert offenders == [], f"agent 反向依赖了 services：{offenders}"


def test_routers_may_depend_on_services() -> None:
    """AC-38 反控：允许的方向必须真的存在，否则上面几条会因为"没文件"而假绿。"""
    routers_dir = APP_ROOT / "routers"
    assert routers_dir.is_dir(), "找不到 routers/ 目录，守卫的路径假设已过期"
    allowed = []
    for path in sorted(routers_dir.glob("*.py")):
        if any(_module_matches(module, ("services",)) for module in _python_imports(_read(path))):
            allowed.append(path.name)
    assert allowed, "没有任何 router 导入 services，说明解析器可能失效了"
