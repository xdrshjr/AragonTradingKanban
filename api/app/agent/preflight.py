"""Capability preflight — the guard against P0-1 and its future recurrences (§5.4, R-12).

`aragon info --json` is the only source of truth here. The five checks below
decide, deterministically, whether a real structured generation can possibly
succeed. If any of them fails, `AGENT_MODE=auto` falls back to mock and
`/capabilities` says exactly why — we never "try and see", because the failure
mode (exit 3 with `structured: null` after two wasted minutes) is silent.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
        """
        return (
            self.available
            and self.versionOk
            and self.canStructured
            and self.hasSubmitResult
            and self.keySource != "none"
        )

    @property
    def degradedReason(self) -> str | None:
        if self.fullyCapable:
            return None
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
