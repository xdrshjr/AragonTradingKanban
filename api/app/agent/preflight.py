"""Capability preflight — the guard against P0-1 and its future recurrences (§5.4, R-12).

`aragon info --json` is the only source of truth here. The five checks below
decide, deterministically, whether a real structured generation can possibly
succeed. If any of them fails, `AGENT_MODE=auto` falls back to mock and
`/capabilities` says exactly why — we never "try and see", because the failure
mode (exit 3 with `structured: null` after two wasted minutes) is silent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import get_settings
from ..logging import get_logger

logger = get_logger("preflight")

#: The `--result-schema` + `submit_result` combination landed in 0.6.66.
MIN_CLI_VERSION = (0, 6, 66)
MAX_CLI_EXCLUSIVE = (0, 7, 0)

#: ★v7（§20.3.7）：成功缓存 60s，**失败缓存 10s**。过去失败也缓存 60s，用户点生成时
#: 若正好落在那 60 秒里，会看到一个"引擎不可用"的假象（一次 `aragon info` 最坏 30s）。
CACHE_TTL_SEC = 60.0
FAILURE_CACHE_TTL_SEC = 10.0

#: 两项**只 warn 不降级**的自检。实测 `deepseek-flash` 不在 CLI 的内置模型表里
#: （`pricing` 与 `models` 的 registry 都不认识它），但它确实跑通了本库 5 次真实生成；
#: `aragon doctor` 也常因与本项目无关的项非 0。把它当降级条件，等于"本机装了个 skill
#: 就不许生成"——那是把误杀当稳健（H-8）。
DOCTOR_TIMEOUT_SEC = 15
MODELS_TIMEOUT_SEC = 30

#: ★实施发现（I-61）：这两项**很贵**，不能跟着 `info` 的 60s 缓存走。
#: 本机实测：`info --json` 3.4s、`doctor` 3.3s、`models` 7.8s（后者要问一次模型列表）。
#: 加进每次 preflight 等于把启动/探测从 3.4s 拖到 14.5s；而它们的结论（"这台机器能不能
#: 自检通过""这个模型在不在名录里"）几分钟内不会变。所以单独缓存 10 分钟，
#: 并给一个 `ARAGON_SELF_CHECK=0` 的关闭开关。
SELF_CHECK_TTL_SEC = 600.0

#: ★v9（K1 / §21.3.2）：`aragon --help` 的全局段字面枚举。空串 = 用 CLI 自己的配置。
#: **spawn 之前**拦住不在表里的 provider：实测（E-68）`--provider deepseek` 2 秒后
#: `result.error.code="config"` + exit 2，而 `config` 不在映射表里 ⇒ 会被报成
#: `agent_error`（"引擎返回了一个我们没见过的错误形态"），而它其实是一句清清楚楚的
#: "你填了一个不存在的 provider"。
PROVIDER_WHITELIST: tuple[str, ...] = ("", "anthropic", "openai", "google")

#: `ARAGON_BASE_URL` 的长度上限（防呆，不是安全边界）。
BASE_URL_MAX = 200

#: `keySource` 的三个取值域（唯一契约，§21.5.1）。**不设配置项**（V10-P1-6）。
KEY_SOURCES: tuple[str, ...] = ("env", "cli_profile", "none")


def effective_key_source(cli_has_key: bool | None = None) -> str:
    """→ `"env" | "cli_profile" | "none"`。**唯一**的"到底有没有钥匙"判定。

    真源只有两个：`settings.aragon_api_key`（K1 的 env 入口）与 `aragon info --json`
    的 `hasApiKey`（CLI 自己的 profile）。`fullyCapable` / `degradedReason` /
    `capabilities.keyPresent|keySource` 全部由它派生（V10-P0-1 / 裁决 K-15）。

    为什么 env 优先：**"我们的 `.env` 里有钥匙"是比"CLI 的 profile 里有钥匙"更强的事实**
    —— 前者正是本轮开始时用户交到手上的那一份，而且它会被真正送进 argv。
    """
    settings = get_settings()
    if (settings.aragon_api_key.get_secret_value() or "").strip():
        return "env"
    if cli_has_key:
        return "cli_profile"
    return "none"


def normalize_base_url(raw: str) -> str:
    """去空、去尾斜杠。**不改协议、不补 path**（补 `/v1` 猜错一次就换来一轮 401）。"""
    return (raw or "").strip().rstrip("/")[:BASE_URL_MAX]


def provider_problem(raw: str) -> str | None:
    """`None` = 合法。否则是一句能直接给用户看的话（列出合法值）。"""
    value = (raw or "").strip().lower()
    if value in PROVIDER_WHITELIST:
        return None
    return (
        f"--provider 只能是 {' / '.join(item or '（空=用 CLI 配置）' for item in PROVIDER_WHITELIST)}，"
        f"收到 {value!r}"
    )


def base_url_problem(raw: str) -> str | None:
    """`None` = 形状合法。**这里不做网络 I/O**（可达性交给 K2 探针）。"""
    value = (raw or "").strip()
    if not value:
        return None
    if not value.lower().startswith(("http://", "https://")):
        return "ARAGON_BASE_URL 必须以 http:// 或 https:// 开头"
    if len(value) > BASE_URL_MAX:
        return f"ARAGON_BASE_URL 过长（上限 {BASE_URL_MAX} 字符）"
    tail = value.rstrip("/").lower()
    if tail.endswith("/chat/completions") or tail.endswith("/completions"):
        return "ARAGON_BASE_URL 应填到 /v1 或根地址，不要带上 /chat/completions"
    return None


def host_of(raw: str | None) -> str | None:
    """`https://api.deepseek.com/v1` → `api.deepseek.com`。

    三条约束（§21.5.1）：① 只保留 `host[:port]`；② **永不**回显 path（"把 token 拼进
    path"的网关部署存在）；③ 空配置时返回 `None` 而不是空串。
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parts = urlsplit(value if "//" in value else f"//{value}")
        host = parts.hostname or ""
        if not host:
            return None
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}"[:128]
    except ValueError:  # 坏端口之类
        return None



#: 会话目录体积上报的缓存 TTL（秒）。测体积要 walk 上千个文件，`/capabilities`
#: 会被前端反复轮询，所以测一次用一分钟——它是个易变值，不是档案。
SESSION_DIR_TTL_SEC = 60.0


def _proactor_loop_class() -> type | None:
    """`asyncio.windows_events.ProactorEventLoop`，非 Windows 返回 `None`。

    ★v12 修订（§22.3.2(a) 的落地差异，见 I-107）：§22.3.2 的示例代码在**模块顶层**写
    `import asyncio.windows_events`。那个模块**只存在于 Windows**——顶层导入会让
    `preflight` 在 Linux 上 import 失败，而本项目的生产环境是 Ubuntu。所以这里改成
    **惰性导入**，且只在 `sys.platform == "win32"` 时执行。语义与设计完全一致
    （"不能靠间接导入拿类"），只是把那条约束限制在它成立的分支里。
    """
    if sys.platform != "win32":  # pragma: no cover - 非 Windows 分支
        return None
    try:  # pragma: no cover - Windows 专属
        import asyncio.windows_events as windows_events

        return windows_events.ProactorEventLoop
    except (ImportError, AttributeError):  # pragma: no cover - 冻结构建
        return None


def is_proactor_loop(loop: Any) -> bool:
    """这个 loop 能不能 `create_subprocess_exec`（Windows 上只有 Proactor 能）。"""
    proactor = _proactor_loop_class()
    if proactor is not None and isinstance(loop, proactor):
        return True
    return type(loop).__name__ == "ProactorEventLoop"


#: 当前运行中的事件循环类名（诊断用，`capabilities.runtime.eventLoop`）。
#: ★v12（AC-112 的同一条纪律）：它与 `spawn_capability()` 读的是**同一个** loop 对象，
#: 不另做一次判定。
_LOOP_NAME: dict[str, str | None] = {"value": None}

#: 启动时记下的 loop 实体（进程级"我在哪个循环上跑"的唯一真源）。`main.py::lifespan`
#: 在 startup 里调用 `running_loop_name()` 一次就把真身记进来。
_SERVING_LOOP: dict[str, Any] = {"value": None}


def _serving_loop() -> Any | None:
    """本进程**实际在跑**的那个 loop：当前线程的（若有），否则启动时记下的那个。

    ★评审修正（E-114）：`uvicorn` 的循环是**工厂**换的，不是**策略**换的。
    `uvicorn/loops/asyncio.py::asyncio_loop_factory(use_subprocess)` 在 `--reload` /
    `--workers>1` 时直接 `return asyncio.SelectorEventLoop`，全程**没有**
    `set_event_loop_policy()` —— 于是同步路由（`/capabilities` 跑在 threadpool 里，
    没有 running loop）走"看策略"的分支时，会在一个**注定 spawn 不了子进程**的进程上
    报 `loopOk=True`。那是比"报错"更糟的一种假绿：界面说一切正常，而每一次生成都会被
    `RunManager.start()` 第 0 步拒绝（R-54 的缓解手段因此失效）。

    所以启动时（那里一定有 running loop）记下真身，之后任何线程问"这个进程能不能
    spawn"都拿它回答。

    ★评审修正（E-114 的第二半）：记下的那个 loop **已经关掉**时返回 `None`。
    一个关闭的循环不可能再是"本进程在跑的那个"，拿它回答只会让判据对着一个死对象
    说话；而这个函数是**能力判据**的唯一输入，宁可不回答，也不能回答一个过时的事实
    （用例之间也会因此串味：`TestClient` 每次生命周期换一个 loop）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = _SERVING_LOOP["value"]
        if loop is None:
            return None
        try:
            if loop.is_closed():
                return None
        except AttributeError:  # pragma: no cover - 假 loop（用例里）
            return loop
        return loop
    return loop


def running_loop_name() -> str | None:
    """`ProactorEventLoop` / `_WindowsSelectorEventLoop` / `None`（没有运行中的 loop）。

    顺带把 loop 实体记下来 —— `main.py::lifespan` 在启动时调用一次，从此
    `spawn_capability()` 在同步路由里也能回答"**这个进程**能不能 spawn"。
    每次在真有 running loop 的上下文里被调用都会刷新（一个进程只有一个 serve loop；
    测试里每个 `TestClient` 各有一个，所以"最新的那次"才是真的）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _LOOP_NAME["value"]
    _SERVING_LOOP["value"] = loop
    _LOOP_NAME["value"] = type(loop).__name__
    return _LOOP_NAME["value"]


def spawn_capability() -> tuple[bool, str | None]:
    """`(这台机器现在能不能 spawn 子进程, 不能时的机器可读原因)`。**唯一判据**（S1）。

    为什么必须是**同步纯函数**（★v12 修订 V11-P1-1 / C-38）：`RunManager.start()` 是同步
    方法，而闸门要前移到它的第 0 步（`mkdir` 之前，V11-P0-2）。异步函数在那里根本没法调。

    为什么这件事必须问"当前 loop"而不是"平台"：实测（E-88/E-113）`uvicorn --reload` 会把
    loop factory 换成 `asyncio.SelectorEventLoop`，而 `create_subprocess_exec` 在它上面
    直接抛 `NotImplementedError('')` —— 于是**默认启动参数下每一次生成都在 research 阶段
    以 `internal` 结束**（`runs.7aa5aeaa`）。这不是"可能出问题"，是"必坏"。

    两个前缀是**故意不同**的：`event_loop_selector:` 是"运行中的循环不对"（去关 `--reload`），
    `event_loop_policy:` 是"策略不对"（**从未**观测到运行中的 loop 时的静态判断）——
    可操作方向不同，所以不能合并成一句话。

    ★评审修正（E-114）：中间那档"没有 running loop"**先看本进程真正在跑的 loop**
    （启动时由 `main.py::lifespan` 记下），只有连那个都没有时才退到策略。原因见
    `_serving_loop()`：`uvicorn --reload` 换的是工厂不是策略，只看策略会在一个注定
    spawn 不了的进程上报 `True`。
    """
    if sys.platform != "win32":
        # R-55：非 Windows / uvloop 下 `isinstance(loop, ProactorEventLoop)` 恒为假，
        # 不做平台短路就会把一台好好的 Linux 机器判成"不能 spawn"。
        return True, None
    loop = _serving_loop()
    if loop is not None:
        if is_proactor_loop(loop):
            return True, None
        return False, f"event_loop_selector:{type(loop).__name__}"
    # 连"进程在哪个 loop 上跑"都不知道（纯 CLI 入口 / 单测 / 从未启动过服务）：只能看策略。
    policy = type(asyncio.get_event_loop_policy()).__name__
    if policy == "WindowsProactorEventLoopPolicy":
        return True, None
    return False, f"event_loop_policy:{policy}"


def session_dir() -> Path:
    """CLI 自己的会话目录（`~/.aragon-agent/sessions`，`ARAGON_HOME` 可改根）。

    只读用途：`capabilities.runtime.sessionDirMb` 要告诉用户"这里已经 1.5 GB 了"。
    **绝不在这里删任何东西**（R-61）。
    """
    settings = get_settings()
    root = Path(settings.aragon_home) if settings.aragon_home else Path.home() / ".aragon-agent"
    return root / "sessions"


def session_dir_size_mb() -> float | None:
    """会话目录体积（MB，两位小数）。读不到 → `None`（不猜 0：0 与"没测到"是两件事）。

    结果按 `SESSION_DIR_TTL_SEC` 缓存：它是易变值，而 `/capabilities` 会被反复轮询。
    """
    cached = _session_size[0]
    if cached is not None and time.monotonic() - cached[0] < SESSION_DIR_TTL_SEC:
        return cached[1]
    total = 0
    try:
        for root, _dirs, files in os.walk(session_dir()):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:  # pragma: no cover - 文件在遍历时被删
                    continue
    except OSError:  # pragma: no cover - 目录不存在
        _session_size[0] = (time.monotonic(), None)
        return None
    megabytes = round(total / (1024 * 1024), 2)
    _session_size[0] = (time.monotonic(), megabytes)
    return megabytes


#: `sessionDirMb` 的缓存：单元素列表是模块级可变状态的**唯一**载体（与 `_cache` 同型）。
_session_size: list[tuple[float, float | None] | None] = [None]


@dataclass(slots=True)
class AragonCapability:
    available: bool = False
    cli: str | None = None
    core: str | None = None
    versionOk: bool = False
    provider: str | None = None
    model: str | None = None
    hasApiKey: bool = False
    ajv: bool = False
    canStructured: bool = False
    hasSubmitResult: bool = False
    extraAllowTools: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    resolvedBinary: str | None = None
    errors: list[str] = field(default_factory=list)
    checkedAt: float = 0.0
    #: ★v7：这次探测**本身**成不成功（`info --json` 拿到并解析了）。
    #: 缓存据此决定存活多久：成功 60s / 失败 10s。
    probe_ok: bool = False
    #: ★v7：`aragon doctor` 的摘要与 `aragon models` 的模型是否可见。**只 warn 不降级**。
    doctor: str | None = None
    modelKnown: bool | None = None
    #: ★v7：子进程 `system` 事件的 `model.baseUrl` 回填（过去只在 exec 事件里，
    #: 由 `run_manager` 在生成时写回本进程的能力快照）。没有就留 `None`。
    baseUrl: str | None = None
    #: ★v9（K1 / §21.3.2 末）：**CLI 自己的 profile 里有没有 key**（`aragon info` 的
    #: `hasApiKey`）。它与 `hasApiKey` 分开记，因为后者的语义在 v9 变成了"合并事实"：
    #: `.env` 的 key 也算（这正是 Q-1 的第二道门，V10-P0-1）。
    cliHasApiKey: bool = False
    #: ★v9（K2）：探针结论 —— 由 `services.agent_probe` 回填，本类只承载。
    verified: bool = False
    verifiedAt: str | None = None
    #: ★v12（S1 / §22.3.2(c)）：**本进程能不能 spawn 子进程**。进程级事实，不落库。
    #: 唯一供给者是 `spawn_capability()`，`fullyCapable` / `degradedReason` /
    #: `capabilities.runtime.loopOk` 三处都读它（C-33：一个事实一份判定）。
    loopOk: bool = True
    loopReason: str | None = None

    @property
    def keySource(self) -> str:
        """`env | cli_profile | none` —— **唯一**的"到底有没有钥匙"判定（V10-P0-1/K-15）。

        只有一个真源：`effective_key_source()`。`fullyCapable`、`degradedReason`、
        `capabilities.keyPresent/keySource` 全部由它派生，**不设配置项**——做成
        `ARAGON_API_KEY_SOURCE` 只会多出第三个真源（V10-P1-6）。
        """
        return effective_key_source(self.hasApiKey)

    @property
    def keyPresent(self) -> bool:
        return self.keySource != "none"

    @property
    def baseUrlHost(self) -> str | None:
        """只留 `host[:port]`，**永不**回显 path（spec §21.5.1 的三条约束）。

        优先用本次实测的 `system.baseUrl`；没有就用配置里的 `ARAGON_BASE_URL`。
        """
        return host_of(self.baseUrl or get_settings().aragon_base_url)

    @property
    def fullyCapable(self) -> bool:
        """Every gate the spec requires before we dare spawn a real generation.

        ★v9（V10-P0-1）：钥匙这一道门走 `keySource`，不再直接看 CLI 的 `hasApiKey`。
        过去只填 `.env` 的机器上这里恒 `False` ⇒ `auto` 静默退回 mock ⇒ 用户拿到一份
        示例报告而界面上没有任何红字（这是四轮里最坏的失败形态：不是红，是绿）。

        ★v12（S1 / §22.3.2(c)）：第三道闸 `loopOk`。前五轮的"稳健"全部建立在
        "子进程能起来"这个前提上，而默认启动参数下它不成立（E-88/E-89）。这一项不是
        警告，是**必要条件**——不能 spawn 的进程一次生成都做不成。
        """
        return (
            self.available
            and self.versionOk
            and self.canStructured
            and self.hasSubmitResult
            and self.keySource != "none"
            and self.loopOk
        )

    @property
    def degradedReason(self) -> str | None:
        if self.fullyCapable:
            return None
        # ★v12（S1）：排在最前。它不是"引擎配得不对"，而是"这台机器根本起不来"——
        # 用户能照做的动作与后面五条完全不同（去掉 --reload，而不是改配置/升级 CLI）。
        if not self.loopOk:
            return "event_loop_unsupported"
        if not self.available:
            return "aragon_unavailable"
        if not self.canStructured:
            return "structured_result_unsupported"
        if not self.hasSubmitResult:
            return "submit_result_missing"
        if self.keySource == "none":
            return "no_api_key"
        if not self.versionOk:
            return "version_out_of_range"
        return "unknown"

    def as_payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "version": self.cli,
            "core": self.core,
            "versionOk": self.versionOk,
            "provider": self.provider,
            "model": self.model,
            "hasApiKey": self.hasApiKey,
            "ajv": self.ajv,
            "canStructured": self.canStructured,
            "hasSubmitResult": self.hasSubmitResult,
            "extraAllowTools": self.extraAllowTools,
            "resolvedBinary": self.resolvedBinary,
            "degradedReason": self.degradedReason,
            # ★v7（V8-P1-2）：`baseUrl` 住在这里，因为它是"引擎是谁"，不是"指纹与预算"。
            # 它**不在** `aragon info --json` 里（E-39），只出现在 `exec` 的 system 事件；
            # 没有 system 事件时留空，不猜。
            "baseUrl": self.baseUrl,
            # ★v9（K1/K2 / V10-P1-1）：四个新字段。它们**必须**同时在
            # `schemas/capabilities.py::AragonAragonOut` 里声明，否则 pydantic 会静默丢弃
            # ——"响应模型是第二道门"（这是同一个坑的第三次：v6 `engine`、v7
            # `quality.cost/prompt`、v9 `aragon.verified`）。
            "baseUrlHost": self.baseUrlHost,
            "keyPresent": self.keyPresent,
            "keySource": self.keySource,
            "verified": self.verified,
            "verifiedAt": self.verifiedAt,
            "doctor": self.doctor,
            "modelKnown": self.modelKnown,
            "errors": self.errors,
            # ★v12（S1 / AC-112）：这两个键是**新增**的，既有键一个字没改。
            # 只加到 dataclass 而不加到 `as_payload()`，pydantic 的响应模型会静默丢弃
            # ——这是同一个坑的第四次（v6 engine、v7 quality.cost、v9 aragon.verified）。
            "loopOk": self.loopOk,
            "loopReason": self.loopReason,
        }


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """`0.6.68` → `(0, 6, 68)`. Only the leading digit run of each part counts,
    so a suffix can never invent a version: `0.6.65-hotfix1` stays `(0, 6, 65)`
    and is correctly rejected as older than the 0.6.66 floor."""
    if not value:
        return None
    numbers: list[int] = []
    for part in value.strip().lstrip("v").split(".")[:3]:
        match = re.match(r"\d+", part)
        numbers.append(int(match.group(0)) if match else 0)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def version_ok(value: str | None) -> bool:
    parsed = parse_version(value)
    if parsed is None:
        return False
    return MIN_CLI_VERSION <= parsed < MAX_CLI_EXCLUSIVE


def resolve_binary() -> str | None:
    settings = get_settings()
    if settings.aragon_bin:
        candidate = Path(settings.aragon_bin)
        if candidate.is_file():
            return str(candidate)
        logger.warning("ARAGON_BIN points at a missing file: %s", settings.aragon_bin)
        return None

    return shutil.which("aragon")


def _run_info(binary: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        completed = subprocess.run(  # noqa: S603 - binary comes from config/which
            [binary, "info", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"spawn failed: {exc}"
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return None, f"empty stdout (rc={completed.returncode}) {completed.stderr[:200]}"
    start = stdout.find("{")
    if start < 0:
        return None, f"no JSON in stdout: {stdout[:200]}"
    try:
        return json.loads(stdout[start:]), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def _run_text(binary: str, args: list[str], timeout: int) -> tuple[int, str]:
    """跑一个只读子命令，返回 `(exit_code, stdout+stderr)`。**永不抛**。"""
    try:
        completed = subprocess.run(  # noqa: S603 - binary comes from config/which
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - spawn 失败
        return -1, f"spawn failed: {exc}"
    return completed.returncode, f"{completed.stdout or ''}{completed.stderr or ''}"


def _first_problem_line(text: str) -> str:
    """`doctor` 输出的第一条非 PASS 行（没有就取首行非空文本）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "[PASS]" in stripped:
            continue
        return stripped[:200]
    return (text.strip().splitlines() or [""])[0][:200]


def _warn_only_checks(capability: AragonCapability, binary: str) -> None:
    """★v7 §20.3.7 / H-8：两项只 warn 不降级的自检。

    * `aragon doctor` —— exit 0 = 全绿。非 0 只把摘要放进 `errors`，**不降级**；
    * `aragon models` —— 目标 model 是否出现在输出里。不出现只 warn，**不降级**。

    为什么不降级：实测 `deepseek-flash` 不在 CLI 的**内置**模型表里（`doctor` 会打印
    `[WARN] model … is not in the builtin registry`），但它确实跑通了本库那 5 次真实
    生成；`doctor` 也会因与本项目无关的项（skills 目录的 SKILL.md 变更）非 0。
    以名录或 doctor 为依据降级，就是"本机装了个 skill 就不许生成"。

    ★实施发现（I-61）：结果按 `(binary, model)` 单独缓存 10 分钟——这两条加起来实测
    11.1s，跟着 `info` 的 60s 缓存走会把每次强制探测从 3.4s 拖成 14.5s。
    """
    settings = get_settings()
    if not settings.aragon_self_check:
        capability.doctor = None
        capability.modelKnown = None
        return

    target = (capability.model or settings.aragon_model or "").strip().lower()
    key = f"{binary}|{target}"
    cached = _self_check.get(key)
    if cached is not None and time.monotonic() - cached[0] < SELF_CHECK_TTL_SEC:
        doctor, model_known = cached[1], cached[2]
    else:
        rc, text = _run_text(binary, ["doctor"], DOCTOR_TIMEOUT_SEC)
        doctor = "ok" if rc == 0 else _first_problem_line(text)
        model_known = None
        if target:
            rc_models, models_text = _run_text(binary, ["models"], MODELS_TIMEOUT_SEC)
            if rc_models == 0:
                model_known = target in models_text.lower()
        _self_check[key] = (time.monotonic(), doctor, model_known)

    capability.doctor = doctor
    capability.modelKnown = model_known
    if doctor not in (None, "ok"):
        logger.warning("aragon doctor 非全绿（不降级）：%s", doctor)
        capability.errors.append(f"doctor: {doctor}")
    if model_known is False:
        logger.warning("模型 %s 不在 `aragon models` 的名单里（不降级）", target)
        capability.errors.append(f"model_unknown: {target}")


def check_aragon(force: bool = False) -> AragonCapability:
    """Run the five preflight gates against `aragon info --json`.

    ★v7 §20.3.7：缓存**成功 60s / 失败 10s**。失败也缓存 60s 时，用户点生成若正好落在
    那个窗口里，会看到一个"引擎不可用"的假象。
    """
    settings = get_settings()
    cached = _cache[0]
    if cached is not None and not force and not settings.force_preflight:
        ttl = CACHE_TTL_SEC if cached.probe_ok else FAILURE_CACHE_TTL_SEC
        if time.monotonic() - cached.checkedAt < ttl:
            return cached

    capability = AragonCapability()
    binary = resolve_binary()
    capability.resolvedBinary = binary
    # ★v12（S1 / §22.3.2(c)）：把"本进程能不能 spawn"记进能力快照。**放在最前面**，
    # 包括下面每条早退路径——`aragon` 根本没装的时候，`degradedReason` 仍应是
    # `aragon_unavailable`（更具体），但 `runtime.loopOk` 仍然必须正确。
    capability.loopOk, capability.loopReason = spawn_capability()
    if not binary:
        capability.errors.append("aragon not found on PATH (set ARAGON_BIN or run mock mode)")
        return _remember(capability)

    info, error = _run_info(binary)
    if info is None:
        capability.errors.append(error or "aragon info --json failed")
        return _remember(capability)

    capability.available = True
    capability.probe_ok = True
    capability.cli = info.get("cli")
    capability.core = info.get("core")
    capability.provider = info.get("provider")
    capability.model = info.get("model")
    # ★v9（V10-P0-1）：`hasApiKey` 从"CLI 的 profile 有钥匙吗"变成"**到底有没有钥匙**"。
    # 两件事实分开记：`cliHasApiKey` 是 CLI 的原话，`hasApiKey` 是合并事实。
    # 不合并的后果：只填 `.env` 的机器上 `fullyCapable` 恒 False ⇒ `auto` 静默退回 mock，
    # 于是 K1 把密钥送进 argv 这件事**从来没发生过**，而界面上一行红字都没有。
    capability.cliHasApiKey = bool(info.get("hasApiKey"))
    # 只问一次 `effective_key_source()`：它已经是"两处合起来到底有没有钥匙"的唯一判据，
    # 这里再手写一遍 `cliHasApiKey or …` 等于把同一个事实写成第二个表达式。
    capability.hasApiKey = effective_key_source(capability.cliHasApiKey) != "none"
    capability.tools = list(info.get("tools") or [])
    capability.permissions = list(info.get("permissionModes") or [])
    result = info.get("result") or {}
    capability.result = result
    capability.ajv = result.get("ajv") is True

    features = info.get("features") or []
    capability.canStructured = "structured_result" in features and capability.ajv
    # ★P0-1: without submit_result the model has NO channel to satisfy
    # --result-schema. Treat its absence as a hard capability failure.
    capability.hasSubmitResult = "submit_result" in capability.tools
    capability.versionOk = version_ok(capability.cli)

    required = settings.required_tools
    available_tools = set(capability.tools)
    capability.extraAllowTools = [tool for tool in required if tool in available_tools]
    missing = [tool for tool in required if tool not in available_tools]
    if missing:
        capability.errors.append(f"missing required tools: {', '.join(missing)}")
        if "submit_result" in missing:
            capability.hasSubmitResult = False

    if not capability.versionOk:
        capability.errors.append(f"CLI {capability.cli} outside the supported range >=0.6.66,<0.7")
    if not capability.canStructured:
        capability.errors.append("structured_result feature or ajv validation unavailable")
    if not capability.hasApiKey:
        capability.errors.append("no model API key configured for the CLI")
    # ★v12（S1）：这一条不是"配置问题"，而是"本进程做不成任何一次生成"。
    # 它与 `fullyCapable` / `capabilities.runtime` 读的是**同一个** `spawn_capability()`
    # 结果（C-33），所以三条消费点不会互相矛盾。
    if not capability.loopOk:
        capability.errors.append(
            f"子进程不可用: {capability.loopReason}（去掉 --reload 后重启）"
        )

    # ★v9（K1）：配置形状的两条**静态**检查（不 spawn、不做网络 I/O）。
    # 这里只 warn：真正的拦截在 `build_argv()`（`usage_error`，spawn 之前）。
    problem = provider_problem(settings.aragon_provider)
    if problem:
        capability.errors.append(f"provider: {problem}")
    problem = base_url_problem(settings.aragon_base_url)
    if problem:
        capability.errors.append(f"baseUrl: {problem}")

    # ★v7 §20.3.7：两项 warn-only 自检（只在本机真有二进制时才跑）。
    _warn_only_checks(capability, binary)

    logger.info(
        "preflight available=%s cli=%s capable=%s reason=%s",
        capability.available,
        capability.cli,
        capability.fullyCapable,
        capability.degradedReason,
    )
    return _remember(capability)


def _remember(capability: AragonCapability) -> AragonCapability:
    """缓存一次探测结果，并且**在探测结束之后**才盖时间戳。

    ★v8 评审（B-3）：过去 `checkedAt` 在探测**开始**时就盖，而 `_run_info` 的超时是 30s。
    一旦 `aragon info` 卡到超时，缓存里那份对象的"年龄"一落地就已经是 30 秒，于是
    "失败只缓存 10s"直接失效——每一次请求都会再起一个 30 秒的子进程，把一次供应商抖动
    变成一台被自己拖垮的机器。时间戳必须标在**结论产生的时刻**。
    """
    capability.checkedAt = time.monotonic()
    _cache[0] = capability
    return capability


_cache: list[AragonCapability | None] = [None]

#: ★v7（I-61）：`doctor` / `models` 的独立缓存：`key → (monotonic, doctor, modelKnown)`。
_self_check: dict[str, tuple[float, str | None, bool | None]] = {}


def reset_cache() -> None:
    """Test hook — forces the next `check_aragon` to spawn `aragon info` again."""
    _cache[0] = None
    _self_check.clear()
    _session_size[0] = None
    _LOOP_NAME["value"] = None
    # ★评审修正（E-114）：把"本进程在哪个 loop 上跑"也清掉。用例之间必须互不影响：
    # 一个用例记下的 TestClient loop 若留给下一个用例，`spawn_capability()` 就会对着
    # 一个已经关掉的 loop 回答，而它恰恰是用来断言"这个进程能不能 spawn"的判据。
    _SERVING_LOOP["value"] = None


def note_base_url(base_url: str | None) -> None:
    """把 `exec` 的 `system` 事件里看到的 `model.baseUrl` 记回进程级能力快照。

    ★v8 评审（§20.3.7 的覆盖缺口）：`baseUrl` **不在** `aragon info --json` 里（E-39），
    只出现在 `exec` 的 `system` 事件。设计里写明它由 `run_manager` 在生成时写回快照，
    但此前只有 `AgentResult.engine_info` 记了它，`/capabilities.aragon.baseUrl`
    永远是 `null` —— 一个"文档说会填、代码从不填"的字段，比没有这个字段更糟
    （排查"打的是哪个 endpoint"时会以为它真的为空）。

    只写不降级：拿不到就保持 `None`，绝不猜测，也绝不因此判定能力不足。
    """
    if not base_url or _cache[0] is None:
        return
    _cache[0].baseUrl = base_url


def note_verified(verified: bool, verified_at: str | None) -> None:
    """★v9（K2）：把探针结论写回进程级能力快照，`/capabilities` 才能读出它。

    只写不降级（与 `note_base_url` 同一条纪律）：探针失败不会让生成不可用，
    它只让面板变红（硬约束 2「降级永不上抛」）。
    """
    if _cache[0] is None:
        return
    _cache[0].verified = bool(verified)
    _cache[0].verifiedAt = verified_at


def current_snapshot() -> AragonCapability | None:
    """进程级能力快照（可能为 `None` —— 还没人调过 `check_aragon`）。

    K2 的 `identity()` 要用它读"本次配的是谁"，但**不允许**它在这里触发一次 spawn。
    """
    return _cache[0]
