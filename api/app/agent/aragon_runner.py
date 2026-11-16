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

#: A tool_result line can be ~100KB; a small read buffer would truncate it.
STREAM_LIMIT = 4 << 20

CANCEL_GRACE_SEC = 5.0


def sanitize_session_id(value: str) -> str:
    """`--session-id` accepts `[A-Za-z0-9][A-Za-z0-9._-]*` only (≤64)."""
    cleaned = SESSION_ID_RE.sub("-", value or "")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"rsrch-{cleaned}"
    return cleaned[:48]


def build_argv(request: AgentRequest, capability: AragonCapability, binary: str) -> list[str]:
    """Assemble the exact command line described in spec §5.4."""
    settings = get_settings()
    argv: list[str] = [binary]

    # ── global flags: MUST come before `exec` ─────────────────────────────
    if settings.aragon_provider:
        argv += ["--provider", settings.aragon_provider]
    if settings.aragon_model:
        argv += ["--model", settings.aragon_model]
    argv += ["--cwd", str(request.workspace)]
    argv += ["--tool-timeout", str(settings.aragon_tool_timeout_ms)]
    argv += ["--idle-timeout", str(settings.aragon_idle_timeout_ms)]
    argv += ["--max-tokens", "auto"]
    argv += ["--no-color"]

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
    argv += ["--result-repair-turns", "2"]
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
        process = self._process
        if process is None or process.returncode is not None:
            return
        pid = process.pid
        if sys.platform == "win32":
            # `aragon.CMD` is a cmd.exe shim: proc.kill() leaves node running.
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
            logger.warning("cancel_timeout run=%s pid=%s", self._run_id, pid)

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

        argv = build_argv(request, self.capability, binary)
        self._run_id = request.run_id
        request.workspace.mkdir(parents=True, exist_ok=True)
        ndjson_path = request.workspace / "aragon.ndjson"
        stderr_path = request.workspace / "aragon.stderr.log"

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

        observed = {"result": None, "text": [], "usage": AgentUsage(), "error": None}
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
        elif result_payload:
            error_code = "result_schema_unsatisfied"
            error_message = "模型未能提交满足 JSON Schema 的结构化结果"
        else:
            error_code, error_message = self._classify_exit(exit_code, stderr_path)

        if result_payload.get("isError") and error_code is None and doc is None:
            error_code = "agent_error"
            error_message = _preview(result_payload.get("error") or "引擎返回 isError", 400)

        cost = result_payload.get("cost") or {}
        cost_known = bool(cost.get("known")) and cost.get("amount") is not None
        cost_usd = float(cost.get("amount")) if cost_known and cost.get("amount") is not None else None

        if self._cancelled and error_code is None and doc is None:
            error_code, error_message = "cancelled", "用户取消了本次生成"

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
        )
        (request.workspace / "result.json").write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "aragon finished run=%s exit=%s error=%s doc=%s", request.run_id, exit_code, error_code, doc is not None
        )
        return final

    @staticmethod
    def _classify_exit(exit_code: int | None, stderr_path: Path) -> tuple[str, str]:
        detail = ""
        try:
            if stderr_path.exists():
                detail = stderr_path.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:  # pragma: no cover
            detail = ""
        lowered = detail.lower()
        if exit_code == 0:
            return "result_missing", "引擎正常退出但没有返回 result 事件"
        if exit_code == 3:
            return "budget_exceeded", "生成超出预算（max-turns / max-duration）"
        if exit_code == 2:
            return "usage_error", f"引擎配置或参数错误：{detail[:300] or 'exit 2'}"
        if exit_code in (130, 143, 129):
            return "cancelled", "生成被终止"
        if "unknown option" in lowered:
            return "usage_error", f"引擎参数不被识别：{detail[:300]}"
        if "invalid --session-id" in lowered:
            return "usage_error", "session id 非法"
        return "agent_error", f"引擎异常退出（exit {exit_code}）：{detail[:300] or 'no stderr'}"
