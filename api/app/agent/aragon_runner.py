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
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging import get_logger
from . import schema_budget
from .failure import (
    AUTH_HINTS,
    CLI_ERROR_CODE_MAP,
    EXIT_MAP,
    STDERR_REFINE,
    hint_for,
)
from .preflight import AragonCapability, check_aragon
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

    # ── global flags: MUST come before `exec` ─────────────────────────────
    if settings.aragon_provider:
        argv += ["--provider", settings.aragon_provider]
    if settings.aragon_model:
        argv += ["--model", settings.aragon_model]
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
    argv += ["--max-tokens", str(max(1, int(settings.aragon_max_output_tokens)))]
    # ★v7 H1：CLI 默认 10 次重试 + 每次退避上限 30s，最坏会撞上我们自己的
    # `--max-duration`，把一个网络问题记成"模型太慢"（E-45：6 次已吃 44s）。
    argv += ["--retry-max", str(max(0, min(20, int(settings.aragon_retry_max))))]
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
    argv += ["--result-repair-turns", str(clamp_repair_turns(settings.aragon_repair_turns, capability))]
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

    def __init__(self, capability: AragonCapability | None = None) -> None:
        self.capability = capability or check_aragon()
        self._process: asyncio.subprocess.Process | None = None
        self._cancelled = False
        self._run_id = "-"

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

        argv_path = request.workspace / "argv.json"
        argv_path.write_text(json.dumps(argv, indent=2, ensure_ascii=False), encoding="utf-8")

        self._emit(
            on_event,
            AgentEvent(AgentEventType.log, {"level": "info", "message": f"启动生成引擎：{' '.join(argv[:3])} …"}),
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
        yield AgentEvent(AgentEventType.log, {"__result": result, "level": "info", "message": "生成引擎已退出"})

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
            # ★v8（V8-P1-1）：分类入口是 `_classify_failure(observed, …)`，不是
            # `_classify_exit`。`_finalize` 过去写成 `elif result_payload:` →
            # `result_schema_unsatisfied`，把"有 result 行 + error.code=network_error"
            # 也归成"模型没提交"——而那正是 AC-73 ② 要守的路径。
            error_code, error_message = self._classify_failure(observed, exit_code, stderr_path)

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
            error_code=error_code,
            error_message=error_message,
            provider=self.capability.provider,
            model=self.capability.model,
            source=self.source,
            ndjson_path=str(ndjson_path),
            engine_info=observed.get("engine"),
            retries=int(observed.get("retries") or 0),
            structured_attempts=attempts if isinstance(attempts, int) else None,
        )
        (request.workspace / "result.json").write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "aragon finished run=%s exit=%s error=%s doc=%s retries=%s",
            request.run_id,
            exit_code,
            error_code,
            doc is not None,
            final.retries,
        )
        return final

    def _classify_failure(
        self,
        observed: dict[str, Any],
        exit_code: int | None,
        stderr_path: Path,
    ) -> tuple[str, str]:
        """★v8（V8-P1-1 / O-14）：失败的**唯一**判定入口。

        四条规则按顺序生效（§20.3.4）：

        1. 有 `result` 行 + `structured.value` 是 dict → 成功（在 `_finalize` 里判定）。
        2. 有 `result` 行但没提交结构化结果：
           a. **先看 `result.error.code`** —— CLI 是唯一知道"为什么没提交"的一方，
              它给的码永远优先于我们对 exit code 的猜测；
           b. 有码但不在映射表里 → `agent_error`，把原码与原文放进去；
           c. 完全没有 `error` 对象 → `result_schema_unsatisfied`（exit 1 的正常形态）。
        3. 没有 `result` 行：
           a. 观察到 `retry`（`retries > 0`）→ `provider_unavailable`（P-4）；
           b. 引擎日志里有凭证类特征 → `provider_auth`；
           c. 否则按 `EXIT_MAP` + `STDERR_REFINE`。
        4. 以上都不成立 → `agent_error`（保留 `_finalize` 里 `isError` 那条的老行为）。

        **为什么必须写第 2a 条**：`--retry-max 0` + 一个坏 base URL 会给出
        "有 result 行、`error.code=network_error`、`retries=0`"。若只在"无 result 行"
        的分支里认 `provider_*`，它就会落到 exit 1 → `result_schema_unsatisfied`，
        把网络问题报成"模型不肯提交报告"。
        """
        result_payload = observed.get("result") or {}
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
                mapped = CLI_ERROR_CODE_MAP.get(str(cli_code))
                if mapped is not None:
                    return mapped, (cli_message or hint_for(mapped))
                return "agent_error", f"引擎报错 {cli_code}：{cli_message or '（无详情）'}"[:400]
            return "result_schema_unsatisfied", cli_message or "模型未能提交满足 JSON Schema 的结构化结果"

        retries = int(observed.get("retries") or 0)
        last_retry = observed.get("last_retry") or {}
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
        lowered = detail.lower()
        # ★v8 评审（B-2）：凭证扫描**只在退出码不是"确定结论"时才做**。
        # `AUTH_HINTS` 里是 `401` / `403` / `quota` 这类裸子串，而一次用户取消
        # （130/143）或撞预算（3）的 stderr 完全可能带一个端口号 `4013` 或一句
        # "quota will reset at…"。那两件事的结论是**已知的**（cancelled / budget_exceeded），
        # 不该被一次子串匹配改写——改写的结果是用户点了取消却看到"凭证被拒"。
        definitive = exit_code in _EXIT_CODES_WITH_CERTAINTY
        if not definitive and any(hint in lowered for hint in AUTH_HINTS):
            return "provider_auth", f"模型凭证被拒绝或额度耗尽：{detail[:300]}"

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
