"""S1 · 默认启动参数必须不含 `--reload`（AC-113 / AC-136）。

为什么这条要靠**读源码**而不是"真起一次服务"：`scripts/dev.mjs` 会连起 uvicorn + Next
两个进程并占用 8000/3000，`start-all.ps1` 还会去开浏览器 —— 一个单测起一次服务不是断言，
是一次副作用（而且 CI 里没有浏览器）。所以这里断言的是**启动参数的构造点**：

* `scripts/dev.mjs`：构造 argv 的那段必须有 `...(reload ? ['--reload'] : [])`，且 `reload`
  只由 `DEV_RELOAD === '1'` 决定；
* `start-all.ps1`：参数块必须有 `[switch]$Reload`（V11-P2-1：没有它 `-Reload` 直接
  ParserError），且追加 `--reload` 的判据是 `if ($Reload)`。

这两条合起来才是 AC-113：「默认没有 `--reload`」+「想打开必须显式付代价」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dev_mjs_only_passes_reload_when_dev_reload_is_set() -> None:
    """AC-113 前半：`npm run dev` 的默认 argv 不含 `--reload`。"""
    source = (ROOT / "scripts" / "dev.mjs").read_text(encoding="utf-8")
    assert "DEV_RELOAD" in source, "缺少显式开关，等于把 --reload 永久打开或永久关掉"
    # 开关的判据必须逐字是 '1'：`=== '0'` 之类的写法会让任何非空值都打开热重载。
    assert re.search(r"const reload = process\.env\.DEV_RELOAD === '1'", source)
    # argv 里出现 `--reload` 的唯一方式是那段三元展开（否则默认参数又变回来了）。
    assert "...(reload ? ['--reload'] : [])" in source
    assert "--reload" in source  # 开关仍然**能**打开它，只是不再默认


def test_start_all_ps1_has_a_reload_switch_and_defaults_to_no_reload() -> None:
    """AC-136：`-Reload` 存在且默认关闭（`-NoReload` 保留为兼容别名）。"""
    source = (ROOT / "start-all.ps1").read_text(encoding="utf-8")
    param_block = source[: source.index("Set-StrictMode")] if "Set-StrictMode" in source else source[:4000]
    assert "[switch]$Reload" in param_block, "参数块没有 $Reload ⇒ `-Reload` 会 ParserError（V11-P2-1）"
    assert "[switch]$NoReload" in param_block, "-NoReload 是既有批处理的调用方式，删掉会静默破坏调用方"
    # 追加上限的判据必须是 `if ($Reload)`：`if (-not $NoReload)` 就是旧写法，默认打开。
    assert re.search(r"if \(\$Reload\)\s*\{", source)
    assert "if (-not $NoReload) { $apiArgs += '--reload' }" not in source, "旧判据又回来了"
