"""导入无环守卫（AC-66 / V6-P0-3）。

`numeric_claims` 与 `block_text` 的依赖方向是**单向**且**语义化**的：
`numeric_claims → block_text`。反过来（或把两者的正则塞进一个文件）会立刻成环，
而循环 import 的表现是"第一次 import 成功、换个入口就 ImportError"——只在 CI 的某种
执行顺序下才炸。所以这里用**两种导入顺序**各跑一次真实子进程。

同样的道理，`report_validator` 曾经自己写了 `NUMBER_RE` 与 `CITATION_RE` 两份
（I-33），这里用 AST 扫描把"再写一遍"变成可判定的失败。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"

#: 正则是**有主**的：名字 → 唯一允许定义它的模块（§19.3.1 规则 1）。
RE_OWNERS = {
    "CITATION_RE": "block_text.py",
    "NUMBER_RE": "numeric_claims.py",
    "IDENTIFIER_RE": "numeric_claims.py",
    "DATE_LIKE_RE": "numeric_claims.py",
}


def _run(module_expr: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # 子进程只验证"能不能 import"，不连数据库、不 spawn CLI。
    env.setdefault("PYTHONPATH", str(API_ROOT))
    return subprocess.run(  # noqa: S603 - 固定 argv，无外部输入
        [sys.executable, "-c", module_expr],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        env=env,
    )


def test_numeric_claims_then_block_text_imports_cleanly() -> None:
    result = _run("import app.services.numeric_claims, app.services.block_text")
    assert result.returncode == 0, result.stderr[-2000:]


def test_block_text_then_numeric_claims_imports_cleanly() -> None:
    """反序也必须成功：这是"无环"唯一可信的证明方式。"""
    result = _run("import app.services.block_text, app.services.numeric_claims")
    assert result.returncode == 0, result.stderr[-2000:]


def test_report_validator_and_both_owners_import_in_any_order() -> None:
    for expression in (
        "import app.services.report_validator, app.services.numeric_claims",
        "import app.services.numeric_claims, app.services.report_validator",
        "import app.services.block_dates, app.services.numeric_claims",
        "import app.services.numeric_claims, app.services.block_dates",
    ):
        result = _run(expression)
        assert result.returncode == 0, f"{expression} -> {result.stderr[-1500:]}"


def _module_level_re_compiles(path: Path) -> dict[str, int]:
    """模块级（含 `ast.Assign` / `ast.AnnAssign`）的 `NAME = re.compile(...)`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, int] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if value is None or not isinstance(value, ast.Call):
            continue
        func = value.func
        is_re_compile = (
            isinstance(func, ast.Attribute)
            and func.attr == "compile"
            and isinstance(func.value, ast.Name)
            and func.value.id == "re"
        )
        if not is_re_compile:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.lineno
    return found


def test_regexes_have_exactly_one_owner() -> None:
    """AC-66：`NUMBER_RE`/`IDENTIFIER_RE`/`DATE_LIKE_RE` 只在 `numeric_claims`，
    `CITATION_RE` 只在 `block_text`。别人写了同名赋值就是第二份真源。"""
    offenders: list[str] = []
    checked = 0
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        checked += 1
        for name, line in _module_level_re_compiles(path).items():
            owner = RE_OWNERS.get(name)
            if owner is None:
                continue
            if path.name != owner:
                offenders.append(f"{path.relative_to(API_ROOT).as_posix()}:{line} 重新定义了 {name}")
    assert checked > 20, f"只扫到 {checked} 个模块，判据失效"
    assert offenders == [], f"正则有第二份定义：{offenders}"


def test_owners_really_define_their_regexes() -> None:
    """反控：所有者必须真的持有它们，否则上一条会因为"没人定义"而假绿。"""
    numeric = _module_level_re_compiles(APP_ROOT / "services" / "numeric_claims.py")
    block_text = _module_level_re_compiles(APP_ROOT / "services" / "block_text.py")
    assert {"NUMBER_RE", "IDENTIFIER_RE", "DATE_LIKE_RE"} <= set(numeric)
    assert "CITATION_RE" in block_text


def test_block_text_does_not_depend_on_semantics() -> None:
    """`block_text` 只负责"块 → 文本 / 块 id / 引用"；日期与数字分类属于语义层。"""
    source = (APP_ROOT / "services" / "block_text.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    forbidden = {"numeric_claims", "block_dates", "report_validator", "report_diff"}
    assert not any(item.rsplit(".", 1)[-1] in forbidden for item in imported), imported


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            names.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return names


def test_block_dates_reuses_the_one_date_shape_table() -> None:
    """★V6-P0-3 规则 2：`block_dates → numeric_claims` 允许（形状表一处定义两处用）。"""
    imported = _imported_modules(APP_ROOT / "services" / "block_dates.py")
    assert "numeric_claims" in imported


def test_numeric_claims_never_depends_on_block_dates() -> None:
    """反向不允许——注释里提到名字不算依赖，只看 AST 里的 import。"""
    imported = _imported_modules(APP_ROOT / "services" / "numeric_claims.py")
    assert "block_dates" not in imported, f"numeric_claims 反向依赖了：{sorted(imported)}"
