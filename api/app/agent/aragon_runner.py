"""`aragon exec` driver — argv assembly, NDJSON parsing, cancellation (§5.4).

Three things in this file are load-bearing and are asserted by tests:

1. **argv order** (★P2-04): global flags go *before* the `exec` subcommand,
   subcommand flags after it. Reversed order is `error: unknown option`.
2. **`submit_result` in the allow list** (★P0-1): under
   `--permission-mode strict` the allow list is the only registration channel,
   and `submit_result` is a tool like any other. Omit it and `--result-schema`
   can never be satisfied — the model has nowhere to submit.
3. **`--append-system-prompt-file`** (★P1-11): without it the grounding rules
   in `research_system.md` are just a document nobody reads.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging import get_logger
from . import schema_budget
from .failure import (
    CLI_ERROR_CODE_MAP,
    EXIT_MAP,
    RETRYABLE_BY_RUNNER,
    STDERR_REFINE,
    STOP_REASON_MAP,
    hint_for,
    looks_like_auth,
    mask_secrets,
)
from .preflight import (
    AragonCapability,
    base_url_problem,
    check_aragon,
    host_of,
    normalize_base_url,
    provider_problem,
)
from .runner_base import AgentError, AgentEvent, AgentEventType, AgentRequest, AgentResult, AgentUsage

logger = get_logger("aragon_runner")

#: Without this the CLI cannot receive a structured result at all. It is not a
#: tunable — it is the contract (★P0-1).
SUBMIT_TOOL = "submit_result"

#: Denied explicitly so the model never sees them and stops planning around
#: them. Deny beats allow in the CLI.
DENY_TOOLS = ("bash", "write_file", "edit_file", "task", "skill_install", "skill_create", "skill")

#: Env vars worth passing to the child. Deliberately not `os.environ`.
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "ARAGON_HOME",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "NODE_EXTRA_CA_CERTS",
)

SESSION_ID_RE = re.compile(r"[^A-Za-z0-9._-]")

#: ★v7 §20.3.2 / H1：`lean` 体位注入的 6 个**全局** flag（必须在 `exec` 之前）。
#: 实测（E-43）：默认体位比 lean 多 2551 input tokens（3352 → 5903，+76%），
#: 并且 stderr 被打上一条与生成无关的 `[skills] warn`，被 `_read_stderr` 变成
#: 逐条 SSE warn。六个 flag 全在 `aragon --help` 的全局段（`--no-skills` 放在
#: `exec` 之后是 `error: unknown option`，exit 2）。
LEAN_FLAGS: tuple[str, ...] = (
    "--no-update",
    "--no-skills",
    "--no-gene",
    "--no-memory",
    "--no-todo",
    "--no-team",
)

#: A tool_result line can be ~100KB; a small read buffer would truncate it.
STREAM_LIMIT = 4 << 20

CANCEL_GRACE_SEC = 5.0

#: `--idle-timeout` 必须比 `--tool-timeout` 多出的余量（§20.5.3 的显式保证）。
IDLE_TOOL_MARGIN_MS = 30_000


def sanitize_session_id(value: str) -> str:
    """`--session-id` accepts `[A-Za-z0-9][A-Za-z0-9._-]*` only (≤64)."""
    cleaned = SESSION_ID_RE.sub("-", value or "")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"rsrch-{cleaned}"
    return cleaned[:48]


def clamp_repair_turns(configured: int, capability: AragonCapability) -> int:
    """`min(ARAGON_REPAIR_TURNS, capability.result.maxRepairTurns)`（§20.3.2）。

    CLI 的 `maxRepairTurns` 是硬上限（实测 2，E-39）。超越它是 exit 2，而不是
    "多修一轮"——所以这个夹取不是保守，是唯一正确的写法。
    """
    cli_max = (capability.result or {}).get("maxRepairTurns")
    configured = max(0, int(configured))
    if isinstance(cli_max, int) and cli_max >= 0:
        return min(configured, cli_max)
    return configured


#: ★v9（K1 / §21.3.2"打码是安全半条命"）：`--api-key` 的下一位与非 flag 位置的兜底都走
#: `failure.mask_secrets()` —— 打码只有**一份**正则（`failure._SECRET_RE`），本文件不再
#: 复制第二份：两份词表必然漂移，而这一份守的是"密钥不落盘"。
_REDACTED = "***"


def redact_argv(argv: Sequence[str]) -> list[str]:
    """`--api-key` 的下一位一律换成 `***`；其余位置再做一次正则兜底。

    **只产出显示用副本**。三个允许的落盘点（V10-P0-2 / 裁决 K-16）：
    `argv.json` 的 `write_text` 之前、`run.log` 的启动行、任何 SSE / `run_events` 载荷。
    绝不允许在 `build_argv()` 的 `return` 前调用它——那个返回值**就是**子进程命令行，
    打码等于把 `***` 当密钥交给引擎（E-76：每一次生成与深答都会以 401 结束，
    而 AC-90/AC-91 照样全绿）。
    """
    masked = list(argv)
    for index, item in enumerate(masked[:-1]):
        if item in ("--api-key", "--apikey"):
            masked[index + 1] = _REDACTED
    return [mask_secrets(item) for item in masked]


def build_argv(request: AgentRequest, capability: AragonCapability, binary: str) -> list[str]:
    """Assemble the exact command line described in spec §5.4 / §20.5.3.

    这是**唯一**的 choke point（裁决 H-2）：生成与 deep ask 都经过它
    （`ask_runner.build_ask_argv` 只是转发），所以 schema / 提示词 / 身份三道闸门
    放在这里天然覆盖两条路径，不需要第二份判断。

    纯函数：只读文件、不写盘、不 spawn。度量结果由 `stream()` 落盘
    （★V8-P2-5：闸门失败的那一次最需要审计材料）。
    """
    settings = get_settings()
    argv: list[str] = [binary]

    # ── 进门前闸门 0/3：K1 的两条**静态**配置检查（spawn 之前，不联网）─────────
    # 为什么在这里拦（§21.3.2）：E-68 实测 `--provider deepseek` 的结局是 2 秒后
    # `error.code="config"` + exit 2，而该码不在映射表里 ⇒ 会被报成 `agent_error`
    # （"引擎返回了一个我们没见过的错误形态"），其实只是一句"你填了个不存在的 provider"。
    problem = provider_problem(settings.aragon_provider)
    if problem:
        raise AgentError("usage_error", problem)
    problem = base_url_problem(settings.aragon_base_url)
    if problem:
        raise AgentError("usage_error", problem)

    # ── global flags: MUST come before `exec` ─────────────────────────────
    if settings.aragon_provider:
        argv += ["--provider", settings.aragon_provider.strip().lower()]
    if settings.aragon_model:
        argv += ["--model", settings.aragon_model]
    # ★v9（K1 / AC-90）：顺序写死 provider → model → base-url → api-key，全在 `exec` 之前。
    # 位置错了的表现是 `error: unknown option`（exit 2），§20.5.3 已经踩过一次。
    # **空值不传**（AC-92）：`--api-key ""` 会让 CLI 认为"给了一个空密钥"，比"不给"更难诊断。
    base_url = normalize_base_url(settings.aragon_base_url)
    if base_url:
        argv += ["--base-url", base_url]
    api_key = settings.aragon_api_key.get_secret_value().strip()
    if api_key:
        argv += ["--api-key", api_key]
    argv += ["--cwd", str(request.workspace)]
    argv += ["--tool-timeout", str(settings.aragon_tool_timeout_ms)]
    # ★v8 评审（§20.5.3 的"显式保证"）：`--idle-timeout` 必须比 `--tool-timeout` 至少多
    # 30s，否则一次慢工具会把整轮生成按"空闲"掐掉，而 CLI 那边只是慢。CLI 自己会抬，
    # 但那是它的事；我们**显式保证**——配置写错时抬到安全值并留一条 warn，
    # 而不是让生成以一条无法归因的 `cancelled` 结束。
    tool_timeout = max(1, int(settings.aragon_tool_timeout_ms))
    idle_timeout = max(int(settings.aragon_idle_timeout_ms), tool_timeout + IDLE_TOOL_MARGIN_MS)
    if idle_timeout != settings.aragon_idle_timeout_ms:
        logger.warning(
            "ARAGON_IDLE_TIMEOUT_MS=%s 小于 tool-timeout+%sms，已抬到 %s",
            settings.aragon_idle_timeout_ms,
            IDLE_TOOL_MARGIN_MS,
            idle_timeout,
        )
    argv += ["--idle-timeout", str(idle_timeout)]
    # ★v7 H1：`auto` 的含义是"用模型上限"，于是"截断"变得不可预测；
    # 实测 5 次真实成功生成的 output tokens 最大 34586，给 40000 让截断可解释。
    # ★v9（V10-P1-9）：`request` 上的三个可空覆盖让探针（K2）能设自己的预算。
    # `None` ⇒ 跟 `settings`，所以生成路径的行为一个字都没变。
    max_tokens = settings.aragon_max_output_tokens if request.max_output_tokens is None else request.max_output_tokens
    argv += ["--max-tokens", str(max(1, int(max_tokens)))]
    # ★v7 H1：CLI 默认 10 次重试 + 每次退避上限 30s，最坏会撞上我们自己的
    # `--max-duration`，把一个网络问题记成"模型太慢"（E-45：6 次已吃 44s）。
    # ★v9（K4）：默认改为 0（重试权收回 runner）；探针（K2）也用它关掉 CLI 重试。
    retry_max = settings.aragon_retry_max if request.retry_max is None else request.retry_max
    argv += ["--retry-max", str(max(0, min(20, int(retry_max))))]
    if settings.aragon_agent_posture == "lean":
        # ★v7 H1：六个 `--no-*` 全在全局段。放在 `exec` 之后是 exit 2。
        argv += list(LEAN_FLAGS)
    argv += ["--no-color"]

    # ── 进门前闸门 1/3：资料预算（prompt + system + schema） ───────────────
    schema_budget.assert_prompt_budget(request, settings.aragon_max_prompt_bytes)
    # ── 进门前闸门 2/3：schema 预算（深度/体积/节点），CLI 硬上限来自 preflight ──
    budget = schema_budget.measure(request.schema_path, schema_budget.limits_from_capability(capability))
    if not budget.ok:
        raise AgentError("schema_over_budget", f"报告 Schema 超出引擎预算：{budget.reason()}")
    # ── 进门前闸门 3/3：交给 CLI 的那份必须与真源字节一致（漂移 = 白烧一轮） ──
    if request.schema_source_path is not None:
        schema_budget.assert_schema_identity(request.schema_path, request.schema_source_path)

    # ── subcommand ────────────────────────────────────────────────────────
    argv += ["exec"]
    argv += ["--output-format", "stream-json"]
    argv += ["--partial-messages"]
    argv += ["--no-save-session"]

    # ── permissions: strict baseline, allow list is the only registration ──
    allow = list(capability.extraAllowTools)
    # Trust the preflight verdict, not a re-derivation from `tools`: exactly one
    # place decides whether structured submission is possible (see preflight.py).
    if capability.hasSubmitResult and SUBMIT_TOOL not in allow:
        allow.append(SUBMIT_TOOL)
    if not capability.hasSubmitResult or SUBMIT_TOOL not in allow:
        # Better to blow up here than to burn 90s and return structured:null.
        raise AgentError(
            "structured_result_unsupported",
            "aragon 未提供 submit_result 工具，无法提交结构化报告",
        )
    argv += ["--permission-mode", "strict"]
    argv += ["--allow-tool", ",".join(allow)]
    argv += ["--deny-tool", ",".join(DENY_TOOLS)]

    argv += ["--append-system-prompt-file", str(request.system_prompt_path)]
    argv += ["--result-schema", str(request.schema_path)]
    # ★v7 H1：配置化但默认不变；并且被 CLI 自己的 `maxRepairTurns` 夹取。
    # ★v9（K2）：探针要的是"一次就成"，不是"修几次能成" ⇒ `repair_turns=0`。
    repair_turns = settings.aragon_repair_turns if request.repair_turns is None else request.repair_turns
    argv += ["--result-repair-turns", str(clamp_repair_turns(repair_turns, capability))]
    argv += ["--on-result-unsatisfied", "error"]

    argv += ["--max-turns", str(request.max_turns)]
    argv += ["--max-duration", str(request.max_duration_ms)]
    argv += ["--prompt-file", str(request.prompt_path)]
    return argv


def child_env(workspace: Path) -> dict[str, str]:
    settings = get_settings()
    # Base whitelist plus whatever the operator explicitly named in
    # ARAGON_ENV_PASSTHROUGH — never the whole of os.environ (§11.3).
    allowed = (*ENV_ALLOWLIST, *settings.env_passthrough)
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    if settings.aragon_home:
        env["ARAGON_HOME"] = settings.aragon_home
    # Keep any child caches inside the run workspace instead of the home dir.
    # POSIX reads TMPDIR, Windows reads TEMP/TMP — set all three, otherwise the
    # passthrough copy of TEMP/TMP silently wins on Windows.
    workspace_tmp = str(workspace)
    env["TMPDIR"] = workspace_tmp
    env["TEMP"] = workspace_tmp
    env["TMP"] = workspace_tmp
    return env


def _preview(value: Any, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ⏎ ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _salvageable(result_payload: dict[str, Any]) -> Any | None:
    """★v9（K5）：`result.result` 里的原始 JSON（未清洗）。解析不了就是 `None`。

    实测成功侧的形状是 `"result": "{\\"ok\\":true,…}"`（字符串，不是对象），所以这条路
    不是猜的。**只解析，不判定**——是否抢救由 `report_validator.salvage_doc()` 决定。
    """
    raw = result_payload.get("result")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_engine_json(
    workspace: Path,
    engine: dict[str, Any] | None,
    capability: AragonCapability,
) -> None:
    """★v9（K7 / AC-105）：`engine.json` —— **这次真的用了什么**。

    为什么另立一个文件：`capabilities.aragon` 是**静态**预检（装没装、版本对不对），
    而这里记的是本次 `system` 事件里的实况。两者不一致（同一 `quality.engine.fingerprint`
    却不同 `baseUrlHost`）正是 G4 可比性最怕的情形——同时也说明"有人换了端点但没换模型名"。

    `system` 事件缺失时写 `{"missing": true}` 而**不是不写**：一个"文件不存在"与
    "这次没有 system 事件"是两件事，前者会让排查的人去查磁盘。
    """
    base_url = (engine or {}).get("baseUrl") or (get_settings().aragon_base_url or None)
    payload: dict[str, Any] = {
        "provider": (engine or {}).get("provider") or capability.provider,
        "model": (engine or {}).get("model") or capability.model,
        "baseUrlHost": host_of(base_url),
        "permissionMode": "strict",
        "tools": list(capability.extraAllowTools),
        "capabilities": {
            "cli": capability.cli,
            "core": capability.core,
            "versionOk": capability.versionOk,
            "canStructured": capability.canStructured,
            "hasSubmitResult": capability.hasSubmitResult,
            "keySource": capability.keySource,
        },
        "cli": capability.cli,
    }
    if not engine:
        payload["missing"] = True
    try:
        (workspace / "engine.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:  # pragma: no cover - 工作区被删
        logger.debug("engine.json 写入失败（工作区不可写）")


def _with_attempt(request: AgentRequest, attempt: int, attempt_dir: Path) -> AgentRequest:
    """★v9（K4）：把一次 attempt 的视图做出来（不改调用方传进来的那个对象）。

    单次尝试时 `attempt_dir is request.workspace`，返回的就是**同一个对象**
    （零行为变化：既有测试断言的 argv.json / ndjson 位置一个字都没动）。
    """
    if attempt_dir == request.workspace:
        return request
    return replace(request, workspace=attempt_dir, attempt=attempt, attempt_dir=attempt_dir)


#: 每次 attempt 的审计文件（重试时整批归档到 `attempt-<n>/`）。
ATTEMPT_ARTIFACTS: tuple[str, ...] = (
    "argv.json",
    "schema_budget.json",
    "engine.json",
    "aragon.ndjson",
    "aragon.stderr.log",
    "result.json",
)


def _archive_attempt(workspace: Path, attempt: int) -> Path | None:
    """把根目录下第一次尝试的产物挪进 `attempt-<n>/`（重试前调用）。

    为什么这样做而不是"一开始就把每次 attempt 放进子目录"：单次尝试（绝大多数情况）的
    产物路径必须与 v8 完全一致 —— 那是 `run_diagnostics`、审计脚本、既有的 12 条测试
    共同依赖的布局。只有真的发生重试时才付出"多两层目录"的代价（AC-100）。
    """
    target = workspace / f"attempt-{attempt}"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover
        return None
    for name in ATTEMPT_ARTIFACTS:
        source = workspace / name
        if not source.exists():
            continue
        try:
            source.replace(target / name)
        except OSError:  # pragma: no cover - 文件被占用
            logger.debug("归档 %s 失败（跳过）", name)
    return target


def _accumulate_usage(total: AgentUsage, usage: AgentUsage) -> None:
    """★v9（K4 / R-48）：usage 跨 attempt 累加，成本不丢（`run.done.costUsd` 是总和）。"""
    for field_name in ("input_tokens", "output_tokens", "total_tokens", "turns", "tool_calls"):
        incoming = getattr(usage, field_name, None)
        if incoming is None:
            continue
        current = getattr(total, field_name, None) or 0
        setattr(total, field_name, int(current) + int(incoming))


def _merge_usage(total: AgentUsage, last: AgentUsage) -> AgentUsage:
    """总和 ≥ 最后一次：只有当累加器什么都没收到时才退回最后一次的数。"""
    if total.input_tokens is None and total.output_tokens is None and total.total_tokens is None:
        return last
    merged = AgentUsage(
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        total_tokens=total.total_tokens,
        turns=total.turns if total.turns is not None else last.turns,
        tool_calls=total.tool_calls if total.tool_calls is not None else last.tool_calls,
    )
    return merged


async def kill_process_tree(process: asyncio.subprocess.Process | None, *, label: str = "-") -> None:
    """★P1-09, reusable (★V4-P1-5): kill the tree, not just the shim.

    `aragon.CMD` on Windows is a cmd.exe shim, so `proc.kill()` leaves the node
    child running and the run never finishes cancelling. `AskRegistry` needs the
    very same semantics for `deep` asks, so this lives at module level instead of
    inside `AragonRunner.cancel()`.
    """
    if process is None or process.returncode is not None:
        return
    pid = process.pid
    if sys.platform == "win32":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/T",
                "/F",
                "/PID",
                str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except OSError as exc:  # pragma: no cover - non-Windows CI
            logger.warning("taskkill failed (%s), falling back to kill()", exc)
            process.kill()
    else:
        try:
            os.killpg(os.getpgid(pid), 15)
        except (OSError, ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=CANCEL_GRACE_SEC)
    except TimeoutError:
        logger.warning("cancel_timeout %s pid=%s", label, pid)


class AragonRunner:
    """Spawns `aragon exec`, forwards NDJSON as `AgentEvent`s, never hangs."""

    name = "aragon"
    source = "aragon"

    def __init__(self, capability: AragonCapability | None = None, *, max_attempts: int | None = None) -> None:
        self.capability = capability or check_aragon()
        self._process: asyncio.subprocess.Process | None = None
        self._cancelled = False
        self._run_id = "-"
        #: ★v9（K4）：`None` ⇒ 跟 `settings.agent_attempts`。探针（K2）传 1 ——
        #: 一次自检不值得再花一次 token，也不该把"通不通"这件事拖成两倍时间。
        self._max_attempts = max_attempts

    # ── public API ───────────────────────────────────────────────────────
    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AgentResult:
        result: AgentResult | None = None
        async for event in self.stream(request, on_event):
            if event.type == AgentEventType.log and (candidate := event.payload.get("__result")):
                result = candidate  # type: ignore[assignment]
        return result or AgentResult(
            error_code="internal", error_message="runner produced no result", source=self.source
        )

    async def cancel(self) -> None:
        """★P1-09: kill the process *tree*, not just the shim."""
        self._cancelled = True
        await kill_process_tree(self._process, label=f"run={self._run_id}")

    # ── streaming core ───────────────────────────────────────────────────
    async def stream(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """★v9（K4 / §21.3.5）：**重试权收回 runner**。

        为什么是这里而不是 CLI：实测（E-69）`--retry-max` 管不住 CLI 的第二层重试
        （`maxRetries=10`、每次退避 60 s），一次"端点不可达"花掉 2 分 03 秒并撞上时长
        预算，最终以 `exit 3 / stopReason=timeout / isError=false` 结束——会被报成
        "模型不肯提交"。关掉 CLI 重试后（E-70）同样的问题 **2.0 秒**返回且可归因。

        三条纪律：
        1. 只重试 `RETRYABLE_BY_RUNNER`（`provider_unavailable` / `result_missing`）；
        2. 每次 attempt 用 `attempt-<n>/` 留档（argv / ndjson / stderr / engine.json），
           事后能证明"第一次网络断、第二次成功"；单次尝试时**不建子目录**（行为不变）；
        3. usage 跨 attempt 累加，`run.done.costUsd` 是全部 attempt 之和（成本不丢）。
        """
        settings = get_settings()
        total = max(1, int(self._max_attempts if self._max_attempts is not None else settings.agent_attempts))
        started = time.monotonic()
        usage_total = AgentUsage()
        result: AgentResult | None = None
        attempts_made = 0

        for attempt in range(1, total + 1):
            if self._cancelled:
                break
            if attempt > 1:
                # 预算守卫（§21.3.5 第 3 条）：不满足就**不重试**，把第一次的错误交出去
                # —— 比"重试到超时"诚实。
                expected_ms = max(1_000, int(request.max_duration_ms) // 3)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms + expected_ms >= int(request.max_duration_ms):
                    logger.info(
                        "runner retry skipped run=%s attempt=%s elapsed=%sms budget=%sms",
                        request.run_id,
                        attempt,
                        elapsed_ms,
                        request.max_duration_ms,
                    )
                    break
                backoff_ms = min(10_000, max(1_500, 1_500 * (attempt - 1) + 1_000))
                logger.info(
                    "runner retry run=%s attempt=%s/%s code=%s",
                    request.run_id,
                    attempt,
                    total,
                    result.error_code,
                )
                yield AgentEvent(
                    AgentEventType.retry,
                    {
                        # ★v9（K4）：`layer` 是新增字段（v7 的载荷一字不改）。
                        # 前端按它显示"引擎重试 x/10" vs "本服务重试 1/2"。
                        "layer": "runner",
                        "attempt": attempt,
                        "of": total,
                        "code": (result.error_code if result else None),
                        "backoffMs": backoff_ms,
                    },
                )
                await asyncio.sleep(backoff_ms / 1000)

            attempt_dir = request.workspace if attempt == 1 else request.workspace / f"attempt-{attempt}"
            if attempt == 2:
                # ★v9（K4）：第一次尝试的文件留在**根目录**（与 v8 完全一致：单次尝试时
                # 布局一个字都没动），只在真的发生重试时才归档到 `attempt-1/`，
                # 于是 AC-100 要的"两次的 attempt-<n>/ 都存在"与既有产物路径同时成立。
                _archive_attempt(request.workspace, 1)
            attempt_request = _with_attempt(request, attempt, attempt_dir)
            attempts_made = attempt
            result = None
            async for event in self._stream_once(attempt_request, on_event):
                if event.type == AgentEventType.log and (candidate := event.payload.get("__result")):
                    result = candidate  # type: ignore[assignment]
                    continue
                yield event
            if result is None:  # pragma: no cover - _stream_once always yields one
                result = AgentResult(
                    error_code="internal", error_message="runner produced no result", source=self.source
                )
            _accumulate_usage(usage_total, result.usage)

            if self._cancelled or result.ok:
                break
            if result.error_code not in RETRYABLE_BY_RUNNER:
                break

        if result is None:  # pragma: no cover - defensive
            result = AgentResult(error_code="internal", error_message="runner produced no result", source=self.source)
        result.usage = _merge_usage(usage_total, result.usage)
        result.attempts_made = max(1, attempts_made)
        yield AgentEvent(AgentEventType.log, {"__result": result, "level": "info", "message": "生成引擎已退出"})

    async def _stream_once(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        binary = self.capability.resolvedBinary
        if not binary:
            raise AgentError("aragon_unavailable", "找不到 aragon 可执行文件")
        if not self.capability.hasSubmitResult:
            raise AgentError(
                "structured_result_unsupported",
                "aragon 缺少 submit_result 工具，结构化生成不可用（P0-1 防护）",
            )
        if not self.capability.canStructured:
            raise AgentError("structured_result_unsupported", "aragon 不支持结构化结果（ajv / structured_result）")

        self._run_id = request.run_id
        request.workspace.mkdir(parents=True, exist_ok=True)
        ndjson_path = request.workspace / "aragon.ndjson"
        stderr_path = request.workspace / "aragon.stderr.log"

        # ★v8（V8-P2-5）：度量**先落盘**，再 `build_argv()`。理由有两条：
        # ①`build_argv` 是纯函数，黄金 argv 断言不该被"顺手写了一个文件"干扰；
        # ②闸门**失败**的那一次，恰恰是最想知道"当时 schema 有多深"的一次——
        #    把写盘放在函数尾部就等于在最需要审计材料的时候不写。
        # ★v8 评审（V8-P2-5 的同型问题）：资料预算的**实测值**也要落盘，而且要在
        # 断言**之前**算——`prompt_over_budget` 是三道闸门里唯一以"数字"为判据的一条，
        # 失败的那一次过去在 `schema_budget.json` 里一个字节都看不到。
        settings = get_settings()
        measurement: dict[str, Any] = {
            "posture": settings.aragon_agent_posture,
            "retryMax": settings.aragon_retry_max,
            "maxOutputTokens": settings.aragon_max_output_tokens,
            "maxPromptBytes": settings.aragon_max_prompt_bytes,
        }
        try:
            measurement["promptBytes"] = schema_budget.prompt_total_bytes(request)
            measurement.update(
                schema_budget.measure(
                    request.schema_path, schema_budget.limits_from_capability(self.capability)
                ).as_payload()
            )
        finally:
            # 读文件这一层就可能抛（schema 不在 / 工作区被清）；抛也要留下现场。
            (request.workspace / "schema_budget.json").write_text(
                json.dumps(measurement, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        argv = build_argv(request, self.capability, binary)

        # ★v9（V10-P0-2 / 裁决 K-16）：**打码只发生在落盘点**。
        # `build_argv()` 的返回值就是下面 `create_subprocess_exec` 的实参，
        # 所以在它的 `return` 前打码等于把 `***` 当密钥交给引擎（E-76：每一次生成
        # 与深答都会以 401 结束，而 AC-90/AC-91 照样全绿）。这里落的是**副本**。
        argv_path = request.workspace / "argv.json"
        argv_path.write_text(json.dumps(redact_argv(argv), indent=2, ensure_ascii=False), encoding="utf-8")

        self._emit(
            on_event,
            AgentEvent(
                AgentEventType.log,
                {
                    "level": "info",
                    # ★v9（V10-P2-12）：过去是 `argv[:3]`，安全**只是碰巧**（密钥排在第 7 位），
                    # 而 v7 已经调过一次 flag 顺序。改成先打码再看前 5 项。
                    "message": f"启动生成引擎：{' '.join(redact_argv(argv)[:5])} …",
                },
            ),
        )

        emit_event, event_queue = self._make_queue(on_event)

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(request.workspace),
                env=child_env(request.workspace),
                limit=STREAM_LIMIT,
                **(dict(start_new_session=True) if sys.platform != "win32" else {}),
            )
        except (OSError, ValueError) as exc:
            raise AgentError("aragon_unavailable", f"无法启动 aragon：{exc}") from exc

        self._process = process
        logger.info("spawned aragon pid=%s run=%s", process.pid, request.run_id)

        observed = {
            "result": None,
            "text": [],
            "usage": AgentUsage(),
            "error": None,
            "retries": 0,
            "engine": None,
            "last_retry": None,
            # ★v9（K3）：`stopReason` 是 CLI 的原话，过去**从未被消费**
            # （`grep -rn stopReason api/app` 只有注释）。它是 R-2b 系列规则的唯一输入。
            "stop_reason": None,
        }
        readers = [
            asyncio.create_task(
                self._read_stdout(process, ndjson_path, observed, emit_event),
                name=f"stdout-{request.run_id}",
            ),
            asyncio.create_task(
                self._read_stderr(process, stderr_path, emit_event),
                name=f"stderr-{request.run_id}",
            ),
        ]
        waiter = asyncio.create_task(process.wait(), name=f"wait-{request.run_id}")

        # ★P1-09: converge on *either* the final `result` line or process exit.
        # Windows only emits the final result on Ctrl-C, so waiting on stdout
        # alone would hang forever.
        try:
            while True:
                if waiter.done() and event_queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.2)
                except TimeoutError:
                    if waiter.done():
                        break
                    continue
                if event is None:
                    break
                yield event
            await asyncio.gather(*readers, return_exceptions=True)
            # Drain anything the readers enqueued while we were exiting.
            while not event_queue.empty():
                event = event_queue.get_nowait()
                if event is not None:
                    yield event
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            await self.cancel()
            raise
        finally:
            for task in [*readers, waiter]:
                if not task.done():
                    task.cancel()
            if process.returncode is None:
                # Either we were cancelled, or the consumer abandoned the
                # generator (`aclose`/`GeneratorExit`). Both mean nobody will read
                # this child's output again, so it must not be left running.
                await self.cancel()

        exit_code = process.returncode
        result = self._finalize(request, observed, exit_code, ndjson_path, stderr_path)
        if result.text and not result.doc:
            # Surface the model's last words even on failure (★P1-10 input).
            emit_event(AgentEvent(AgentEventType.text, {"delta": result.text[:4000], "final": True}))
        # ★v9（K4）：最终结果由 `stream()` 统一 yield（它要跨 attempt 累加 usage 与
        # `attempts_made`）。这里只把结果交给它 —— 两次 yield 会让 `run()` 拿到两份。
        yield AgentEvent(AgentEventType.log, {"__result": result, "level": "info", "message": "本次尝试已退出"})

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _emit(on_event: Callable[[AgentEvent], None] | None, event: AgentEvent) -> None:
        if on_event is not None:
            try:
                on_event(event)
            except Exception:  # pragma: no cover - never let a callback kill a run
                logger.exception("agent event callback failed")

    @staticmethod
    def _make_queue(
        on_event: Callable[[AgentEvent], None] | None,
    ) -> tuple[Callable[[AgentEvent], None], asyncio.Queue[AgentEvent | None]]:
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def emit(event: AgentEvent) -> None:
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:  # pragma: no cover
                    logger.exception("agent event callback failed")
            loop.call_soon_threadsafe(queue.put_nowait, event)

        return emit, queue

    async def _read_stdout(
        self,
        process: asyncio.subprocess.Process,
        ndjson_path: Path,
        observed: dict[str, Any],
        emit: Callable[[AgentEvent], None],
    ) -> None:
        assert process.stdout is not None
        counter = {"toolCalls": 0}
        with ndjson_path.open("w", encoding="utf-8") as sink:
            while True:
                try:
                    raw = await process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    # A line longer than the buffer: skip it rather than die (§5.4 point 7).
                    emit(AgentEvent(AgentEventType.log, {"level": "warn", "message": "跳过超长输出行"}))
                    continue
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.strip():
                    continue
                sink.write(line + "\n")
                sink.flush()
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    emit(
                        AgentEvent(AgentEventType.log, {"level": "warn", "message": f"非法 JSON 行已跳过：{line[:80]}"})
                    )
                    continue
                if not isinstance(payload, dict):
                    continue
                self._handle_line(payload, observed, emit, counter)

    @staticmethod
    async def _read_stderr(
        process: asyncio.subprocess.Process,
        stderr_path: Path,
        emit: Callable[[AgentEvent], None],
    ) -> None:
        assert process.stderr is not None
        with stderr_path.open("w", encoding="utf-8") as sink:
            while True:
                raw = await process.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    continue
                sink.write(line + "\n")
                sink.flush()
                emit(AgentEvent(AgentEventType.log, {"level": "warn", "message": f"stderr: {line[:300]}"}))

    def _handle_line(
        self,
        payload: dict[str, Any],
        observed: dict[str, Any],
        emit: Callable[[AgentEvent], None],
        counter: dict[str, int],
    ) -> None:
        kind = str(payload.get("type") or "")
        # Unknown types are ignored on purpose — forward compatibility (§5.4).
        if kind == "system":
            # ★v7 §20.3.7：`baseUrl` 只出现在 exec 的 system 事件里（不在
            # `aragon info --json`，E-39）。有就记下，没有就留空 —— 不猜。
            model_block = payload.get("model")
            if isinstance(model_block, dict):
                info = {
                    "provider": model_block.get("provider"),
                    "model": model_block.get("id") or model_block.get("model"),
                    "baseUrl": model_block.get("baseUrl"),
                }
                observed["engine"] = {key: value for key, value in info.items() if value}
            return

        if kind == "retry":
            # ★v7 §20.3.4 / P-4：实测 8 条 retry 事件过去被完全忽略。
            # **累计用本地自增，绝不用 `attempt`**：实测同一进程里 attempt 会从
            # 1 重新开始（两次独立请求各自从 1 数），拿它当累计会少报一半重试。
            observed["retries"] = int(observed.get("retries") or 0) + 1
            retry_payload = {
                "attempt": payload.get("attempt"),
                "maxRetries": payload.get("maxRetries"),
                "errorType": payload.get("errorType"),
                "delayMs": payload.get("delayMs"),
                "tier": payload.get("tier"),
                "tierCount": payload.get("tierCount"),
                "totalAttempts": payload.get("totalAttempts"),
                "retries": observed["retries"],
            }
            observed["last_retry"] = retry_payload
            emit(AgentEvent(AgentEventType.retry, retry_payload))
            return

        if kind == "result":
            observed["result"] = payload
            # ★v9（K3 / §21.3.4）：`stopReason` 是"怎么停的"，`error.code` 是"为什么"。
            # 前者优先级**低于**后者（裁决 K-7），但它是 R-2b 系列规则唯一能看见的事实。
            if payload.get("stopReason") is not None:
                observed["stop_reason"] = str(payload.get("stopReason") or "") or None
            usage = payload.get("usage") or {}
            observed["usage"] = AgentUsage(
                input_tokens=usage.get("inputTokens") or usage.get("input_tokens"),
                output_tokens=usage.get("outputTokens") or usage.get("output_tokens"),
                total_tokens=usage.get("totalTokens") or usage.get("total_tokens"),
                turns=payload.get("turns") or usage.get("turns"),
                tool_calls=counter["toolCalls"],
            )
            if isinstance(payload.get("text"), str) and payload["text"].strip():
                observed["text"].append(payload["text"])
            return

        if kind in ("tool_call", "tool_result"):
            if kind == "tool_call":
                counter["toolCalls"] += 1
                emit(
                    AgentEvent(
                        AgentEventType.tool,
                        {
                            "name": payload.get("name") or payload.get("tool") or "tool",
                            "input": _preview(payload.get("input") or {}, 300),
                            "isError": False,
                            "phase": "call",
                        },
                    )
                )
            else:
                emit(
                    AgentEvent(
                        AgentEventType.tool,
                        {
                            "name": payload.get("name") or payload.get("tool") or "tool",
                            "input": _preview(payload.get("input") or {}, 300),
                            "isError": bool(payload.get("isError")),
                            "durationMs": payload.get("durationMs"),
                            "outputPreview": _preview(payload.get("output") or "", 400),
                            "phase": "result",
                        },
                    )
                )
            return

        if kind in ("assistant", "text", "text_delta"):
            text = payload.get("text") or payload.get("delta") or ""
            if isinstance(text, str) and text.strip():
                if kind != "text_delta":
                    observed["text"].append(text)
                emit(AgentEvent(AgentEventType.text, {"delta": text[:2000]}))
            return

        if kind == "execution_progress":
            emit(
                AgentEvent(
                    AgentEventType.progress,
                    {
                        "turns": payload.get("turns"),
                        "toolCalls": counter["toolCalls"],
                        "elapsedMs": payload.get("elapsedMs"),
                    },
                )
            )
            return

        if kind == "error":
            observed["error"] = payload.get("error") or payload
            # ★v7（I-63）：`{"type":"error","fatal":true,"code":"stream_error",…}` 是 CLI
            # 在**没有 result 行**时唯一的分类依据（实测：坏 base URL 就是先发它、再发
            # 一行 isError 的 result；连接真断时可能只发它）。只留 code，不留整条 payload。
            if payload.get("code"):
                observed["fatal_code"] = str(payload.get("code"))
            emit(
                AgentEvent(
                    AgentEventType.log,
                    {"level": "warn", "message": f"引擎错误：{_preview(observed['error'], 300)}"},
                )
            )
            return

    def _finalize(
        self,
        request: AgentRequest,
        observed: dict[str, Any],
        exit_code: int | None,
        ndjson_path: Path,
        stderr_path: Path,
    ) -> AgentResult:
        result_payload: dict[str, Any] = observed["result"] or {}
        usage: AgentUsage = observed["usage"]
        text = "\n".join(observed["text"]).strip()

        structured = result_payload.get("structured")
        doc: dict[str, Any] | None = None
        error_code: str | None = None
        error_message: str | None = None

        # ★P0-1 / §5.4 point 3: judge by `structured`, never by exitCode.
        if isinstance(structured, dict) and isinstance(structured.get("value"), dict):
            doc = structured["value"]
        else:
            # ★v8（V8-P1-1）：分类入口是 `classify_failure(observed, …)`，不是
            # `_classify_exit`。`_finalize` 过去写成 `elif result_payload:` →
            # `result_schema_unsatisfied`，把"有 result 行 + error.code=network_error"
            # 也归成"模型没提交"——而那正是 AC-73 ② 要守的路径。
            error_code, error_message = classify_failure(observed, exit_code, stderr_path)

        if result_payload.get("isError") and error_code is None and doc is None:
            error_code = "agent_error"
            error_message = _preview(result_payload.get("error") or "引擎返回 isError", 400)

        cost = result_payload.get("cost") or {}
        cost_known = bool(cost.get("known")) and cost.get("amount") is not None
        cost_usd = float(cost.get("amount")) if cost_known and cost.get("amount") is not None else None

        if self._cancelled and error_code is None and doc is None:
            error_code, error_message = "cancelled", "用户取消了本次生成"

        attempts = structured.get("attempts") if isinstance(structured, dict) else None
        final = AgentResult(
            doc=doc,
            text=text,
            usage=usage,
            cost_usd=cost_usd,
            cost_known=cost_known,
            exit_code=exit_code,
            # ★v9（K1 的安全半条命）：`error_message` 会进 `runs.error_message`、SSE 与
            # 失败卡片。上游原文里可能带密钥形态（E-71 的 401 原文），出门前统一打码。
            error_code=error_code,
            error_message=mask_secrets(error_message) if error_message else error_message,
            provider=self.capability.provider,
            model=self.capability.model,
            source=self.source,
            ndjson_path=str(ndjson_path),
            engine_info=observed.get("engine"),
            retries=int(observed.get("retries") or 0),
            structured_attempts=attempts if isinstance(attempts, int) else None,
            # ★v9（K3）：CLI 原样；映射表在 `failure.STOP_REASON_MAP`，这里是事实。
            stop_reason=observed.get("stop_reason"),
            # ★v9（K5）：`result.result` 是 CLI 把提交内容放在那个字符串字段里的地方
            # （成功侧形状实测：`{"ok":true,"echo":"…"}`）。解析失败就是 `None`。
            salvageable_raw=_salvageable(result_payload),
        )
        (request.workspace / "result.json").write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_engine_json(request.workspace, observed.get("engine"), self.capability)
        logger.info(
            "aragon finished run=%s exit=%s error=%s doc=%s retries=%s stop=%s",
            request.run_id,
            exit_code,
            error_code,
            doc is not None,
            final.retries,
            final.stop_reason,
        )
        return final

    def _classify_failure(
        self,
        observed: dict[str, Any],
        exit_code: int | None,
        stderr_path: Path,
    ) -> tuple[str, str]:
        """★v9（V10-P1-9）：**一行委托**。规则表与实现都在模块级 `classify_failure()`。

        保留这个方法名是因为既有代码（`_finalize`）与 v8 的注释都指向它；但判定只有
        一份实现——探针（K2）与 AC-73/97/98 的夹具调**同一个函数**。两套判定必然漂移，
        而漂移的方向永远是"面板说好、生成说坏"（裁决 K-5）。
        """
        return classify_failure(observed, exit_code, stderr_path)


#: ★v9（K3 / §21.3.4）：**有序**规则表（代码里的顺序即优先级）。这是失败的
#: **唯一**判定实现：`AragonRunner._classify_failure` 是一行委托，探针（K2）与
#: AC-97/98/99/108 的夹具调的都是这个函数（裁决 K-5：两套判定必然漂移）。
#:
#: | 序 | 条件（全部可观测） | 结果码 |
#: | --- | --- | --- |
#: | R-1 | `structured.value` 是 dict | —（成功，在 `_finalize` 里判定） |
#: | R-2a-1 | 供应商封套码（stream/network/fetch_error）且 message 命中 `AUTH_MARKERS` | `provider_auth` |
#: | R-2a-2 | 同上但未命中 `AUTH_MARKERS` | `provider_unavailable` |
#: | R-2a-3 | `error.code` ∈ 映射表（其余） | 映射值 |
#: | R-2a-4 | `error.code` 存在但不在表里 | `agent_error` |
#: | R-2b-1 | 无 `error.code`，`stopReason=timeout` | `provider_unavailable` |
#: | R-2b-2 | 无 `error.code`，`stopReason=max_turns` | `budget_exceeded` |
#: | R-2b-3 | 无 `error.code`，`stopReason` 是结论且不在表里（`error`/`aborted`） | 映射值 |
#: | R-2b-4 | 无 `error.code`，`stopReason` 缺失/`end_turn`，且 `retries > 0` | `provider_unavailable` |
#: | R-2c | 无 `error.code`，`stopReason` 空/缺失/`end_turn`，`retries == 0` | `result_schema_unsatisfied` |
#: | R-3 | 没有 `result` 行 | 现状（fatal_code → auth 提示 → `EXIT_MAP`） |
#:
#: **这张表必须穷尽**（V10-P1-2 / AC-108）：对 `{code ∈ map, code ∉ map, 无 code}` ×
#: `{timeout, max_turns, end_turn, aborted, 缺失}` × `{retries 0, 3}` 的 **30 种组合**，
#: 本函数必须返回且只返回一个合法码，永不返回 `None`、永不抛。v9 草案在
#: `end_turn + retries==0`（正是 AC-99 的夹具）与 `timeout + retries==0` 两处留了洞。
_PROVIDER_ENVELOPE_CODES = frozenset({"stream_error", "network_error", "fetch_error"})


def classify_failure(
    observed: dict[str, Any],
    exit_code: int | None,
    stderr_path: Path,
) -> tuple[str, str]:
    """失败的**唯一**判定入口（规则表见上方注释）。返回 `(code, message)`。"""
    result_payload = observed.get("result") or {}
    retries = int(observed.get("retries") or 0)
    last_retry = observed.get("last_retry") or {}
    stop_reason = str(observed.get("stop_reason") or "").strip()
    stop_mapped = STOP_REASON_MAP.get(stop_reason, "")

    if result_payload:
        error_block = result_payload.get("error")
        cli_code = None
        cli_message = ""
        if isinstance(error_block, dict):
            cli_code = error_block.get("code")
            cli_message = str(error_block.get("message") or "")
        elif isinstance(error_block, str) and error_block:
            cli_message = error_block
        if cli_code:
            code_text = str(cli_code)
            mapped = CLI_ERROR_CODE_MAP.get(code_text)
            # R-2a-1 / R-2a-2：`stream_error` 同时表示"网络断了"与"密钥错了"（E-70 vs E-71，
            # 同一个码一字不差）。判据是 message 的**语义**，而且要求**两个条件同时成立**
            # （V10-P1-4）：`error.code ∈ 供应商封套码` 且 message 命中 `AUTH_MARKERS`。
            if code_text in _PROVIDER_ENVELOPE_CODES:
                if looks_like_auth(cli_message):
                    return "provider_auth", mask_secrets(
                        f"模型凭证被拒绝或额度耗尽：{cli_message or '（上游未给出原文）'}", 400
                    )
                return "provider_unavailable", mask_secrets(
                    "模型服务不可用（供应商网络错误）：" + (cli_message or "（无详情）"), 400
                )
            if mapped is not None:
                # R-2a-3
                return mapped, mask_secrets(cli_message or hint_for(mapped), 400)
            # R-2a-4
            return "agent_error", mask_secrets(f"引擎报错 {code_text}：{cli_message or '（无详情）'}", 400)

        # R-2b 系列：没有 `error.code`，只有 `stopReason` 与重试证据。
        if stop_mapped and stop_mapped not in ("agent_error", ""):
            # `timeout` → provider_unavailable / `max_turns` → budget_exceeded /
            # `aborted` → cancelled（R-2b-1 / R-2b-2 / R-2b-3）。
            if stop_mapped == "provider_unavailable":
                return "provider_unavailable", (
                    f"引擎重试 {retries} 次后停止（stopReason={stop_reason or 'timeout'}）"
                    f"，最后一次 errorType={last_retry.get('errorType') or 'unknown'}"
                )
            return stop_mapped, hint_for(stop_mapped)
        # ★实施发现（I-90）：`stopReason == "error"` **不**单独推导成 `agent_error`。
        # 既有 AC-9 的夹具（`stopReason:"error"` + `structured:null` + exit 1，E-42 的成功侧
        # 之外那一条）必须继续得到 `result_schema_unsatisfied`；"引擎异常"这条语义由
        # `_finalize` 里 `isError` 的兜底与 `EXIT_MAP` 承担，不在这里重复推断一次。
        if retries > 0:
            # R-2b-4
            return "provider_unavailable", (
                "模型服务连续不可用："
                f"lastErrorType={last_retry.get('errorType') or 'unknown'}，"
                f"maxRetries={last_retry.get('maxRetries') if last_retry.get('maxRetries') is not None else '?'}，"
                f"attempts={retries}（本地累计，CLI 的 attempt 会重置），"
                f"lastDelayMs={last_retry.get('delayMs') or 0}"
            )
        # R-2c：真机成功侧的 `stopReason` 恒为 `end_turn`（E-78/E-86），所以这一条
        # 必须把它当"不是结论"来处理，不能当成"未知错误"。
        return "result_schema_unsatisfied", cli_message or "模型未能提交满足 JSON Schema 的结构化结果"

    if retries > 0:
        return "provider_unavailable", (
            "模型服务连续不可用："
            f"lastErrorType={last_retry.get('errorType') or 'unknown'}，"
            f"maxRetries={last_retry.get('maxRetries') if last_retry.get('maxRetries') is not None else '?'}，"
            f"attempts={retries}（本地累计，CLI 的 attempt 会重置），"
            f"lastDelayMs={last_retry.get('delayMs') or 0}"
        )

    # ★v7（I-63）：没有 result 行时，CLI 的致命 `error` 行仍然是第一手证据。
    fatal_code = observed.get("fatal_code")
    if fatal_code and str(fatal_code) in CLI_ERROR_CODE_MAP:
        mapped = CLI_ERROR_CODE_MAP[str(fatal_code)]
        return mapped, f"引擎报告 {fatal_code}（未产出 result 行）"

    detail = _read_stderr(stderr_path)
    # ★v8 评审（B-2）：凭证扫描**只在退出码不是"确定结论"时才做**。
    # 一次用户取消（130/143）或撞预算（3）的 stderr 完全可能带一个端口号 `4013` 或一句
    # "quota will reset at…"。那两件事的结论是**已知的**（cancelled / budget_exceeded），
    # 不该被一次匹配改写——改写的结果是用户点了取消却看到"凭证被拒"。
    definitive = exit_code in _EXIT_CODES_WITH_CERTAINTY
    # ★v9（V10-P1-4）：判据是**分词正则**（`looks_like_auth`），不是子串 —— 旧写法
    # `hint in lowered` 在 `AUTH_HINTS` 变成正则之后恒不命中（也正因为如此，
    # 裸 `401` 那类"把已知结论改写掉"的 B-2 事故从源头消失）。
    if not definitive and looks_like_auth(detail):
        return "provider_auth", mask_secrets(f"模型凭证被拒绝或额度耗尽：{detail[:300]}", 400)

    return _classify_exit(exit_code, stderr_path, detail=detail)


def _read_stderr(stderr_path: Path) -> str:
    """读引擎 stderr 的前 600 字符（分类用）。读不到就是空串，绝不抛。"""
    try:
        if stderr_path.exists():
            return stderr_path.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:  # pragma: no cover
        return ""
    return ""


#: 这些退出码本身就已经是**结论**，不该再被 stderr 里的子串改写（★v8 评审 B-2）。
#: `budget_exceeded` 是引擎自己的判定、`cancelled` 是用户的判定——两者都比一句
#: "stderr 里出现了 401" 更权威。
_EXIT_CODES_WITH_CERTAINTY = frozenset({3, 129, 130, 143})


def _classify_exit(exit_code: int | None, stderr_path: Path, *, detail: str | None = None) -> tuple[str, str]:
    """`_classify_failure` 的第 3c 步（★v8：它**不再是**入口）。

    表驱动：`EXIT_MAP` 查 exit code，`STDERR_REFINE` 精化 exit 2——把"参数错误"里的
    `result_schema_too_deep` 认成 `schema_over_budget`（历史 run `06456fcb` 就是死在
    那一条上，过去却被报成"参数错误"，对实现者没有任何指向性）。

    ★v8 评审（B-1 / B-3）两处修正：
    * 精化**只对 exit 2 生效**（`STDERR_REFINE` 的语义是"exit 2 的已知精化"）：过去它
      在任意退出码上都跑，于是一次 exit 0 的 `result_missing`、一次 exit 143 的
      `cancelled` 只要 stderr 里出现 `unknown option` 就会被改写成 `usage_error`。
    * 精化命中后，**message 也必须跟着换**：过去返回的是 `EXIT_MAP[2]` 的
      "引擎配置或参数错误"，于是码说"schema 太深"、句子说"参数错误"——正是
      §20.3.4 要消灭的那种自相矛盾。
    """
    text = _read_stderr(stderr_path) if detail is None else detail
    lowered = text.lower()
    code, message = EXIT_MAP.get(
        exit_code if exit_code is not None else -1,
        ("agent_error", f"引擎异常退出（exit {exit_code}）"),
    )
    if exit_code == 2:
        for needle, refined in STDERR_REFINE:
            if needle in lowered:
                # 码与句子一起换：`hint_for` 给出"下一步做什么"，原文留 300 字符做证据。
                return refined, f"{hint_for(refined)}｜引擎原文：{text[:300].strip() or '（无输出）'}"
    if code == "usage_error":
        return code, f"{message}：{text[:300] or f'exit {exit_code}'}"
    if exit_code is not None and exit_code not in EXIT_MAP:
        return code, f"{message}：{text[:300] or 'no output'}"
    return code, message
