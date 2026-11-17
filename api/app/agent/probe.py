"""Engine self-check probe — "这个端点此刻是通的吗" 的一次最小真实往返（★v9 K2 / §21.3.3）。

**它不是一个新的 spawn 点。** 这是本节最容易被写错的地方（V10-P1-9）：

* 探针 = **一份 `AgentRequest` + `AragonRunner.run()`**。argv 的唯一 choke point 仍是
  `build_argv()`（裁决 H-2），所以 `argv.json`（已打码）、`schema_budget.json`（三道闸门）、
  `engine.json`、`aragon.ndjson` **全部白拿**。
* 三个"探针自己的参数"（`--max-tokens 1500` / `--retry-max 0` / `--result-repair-turns 0`）
  通过 `AgentRequest` 的三个可空覆盖传下去 —— 那些值过去只从 `settings` 读，
  `AgentRequest` 没有覆盖入口，于是 v9 草案只能自己再写一个 spawn 点（违反硬约束 1）。

依赖方向（`tests/test_boundaries.py` 已机器化）：本文件住在 `agent/`，**不 import `services/`**。
单飞、TTL 与落库都在 `services/agent_probe.py`。

成本（实测 E-86，argv 路径 = 本文件走的路径）：`10.23s / 11251 in + 215 out ≈ $0.0016`，
比一次真实报告（3 万 token 级）低两个数量级 —— 所以"点一次自检"这个交互站得住。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging import get_logger
from .aragon_runner import AragonRunner
from .preflight import AragonCapability, check_aragon, host_of
from .runner_base import AgentError, AgentRequest, AgentResult

logger = get_logger("agent_probe")

#: 最小 schema：深 2、~153 字节（实测 CLI 接受；E-86 用同一份形状跑通）。
PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ok", "echo"],
    "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string", "maxLength": 64}},
    "additionalProperties": False,
}

#: ★v10 修订（V10-P2-13）：文件名与**内容**必须是两个常量。
#: v9 用一个 `PROBE_FILE = "PROBE-ECHO-OK"` 同时表示"要读的文件名"与"应读到的内容"，
#: 而文件名又是**字面写进提示词**的 —— 改一处名字，探针就恒报 `probe_echo_mismatch`，
#: 而那个码的含义是"网关在改写 JSON"，会把人带去查网关。
PROBE_FILENAME = "probe.txt"
PROBE_ECHO = "PROBE-ECHO-OK"

PROBE_PROMPT = (
    f"只做一次工具调用：用 read_file 读取 ./{PROBE_FILENAME} 的前 64 个字符，"
    '然后把 {"ok": true, "echo": "<读到的内容>"} 作为结构化结果提交。不要做别的。'
)

PROBE_SYSTEM = (
    "你在做一次引擎连通性自检。只做被要求的那一次工具调用，然后立刻用 submit_result "
    "提交结构化结果；不要解释、不要额外调用工具。"
)

#: 内置参数表（§21.3.3 的五行，每行都有理由）：
#: * `--max-turns 3`   —— 实测最小成功用 2 turns（E-67），留一次纠错余量；
#: * `--max-duration 90s` —— 8~10s 的成功侧留 10× 余量；坏端点侧 2~5s 就返回（E-70）；
#: * `--max-tokens 1500` —— 实测成功侧 output 159~215 tokens；
#: * `--retry-max 0`   —— 探针不允许把 2 分钟花在重试上（E-69 的教训）；
#: * `--result-repair-turns 0` —— 探针要的是"一次就成"，不是"修几次能成"。
PROBE_MAX_TURNS = 3
PROBE_MAX_DURATION_MS = 90_000
PROBE_MAX_OUTPUT_TOKENS = 1_500


@dataclass(slots=True)
class ProbeOutcome:
    """一次自检的全部事实。`message` 已经过 `mask_secrets()`。"""

    ok: bool
    code: str | None = None
    message: str | None = None
    ms: int = 0
    turns: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    structure_attempts: int | None = None
    engine: dict[str, Any] = field(default_factory=dict)
    cli: str | None = None
    schema_sha1: str | None = None
    probed_at: str = field(default_factory=lambda: _iso())

    def as_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "ms": self.ms,
            "turns": self.turns,
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "structureAttempts": self.structure_attempts,
            "engine": self.engine,
            "cli": self.cli,
            "probedAt": self.probed_at,
        }


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def probe_identity(capability: AragonCapability | None = None) -> dict[str, Any]:
    """`{provider, model, baseUrlHost, cli, schemaSha1}` —— 五元组任一变化即视为失效。

    只看"上次 ok"是不够的：用户改完 `ARAGON_MODEL` 之后，上一条 ok 的记录会撒谎。
    """
    settings = get_settings()
    capability = capability or check_aragon()
    return {
        "provider": (capability.provider or settings.aragon_provider or "").strip(),
        "model": (capability.model or settings.aragon_model or "").strip(),
        "baseUrlHost": capability.baseUrlHost or "",
        "cli": capability.cli or "",
        "schemaSha1": _schema_sha1() or "",
    }


def _schema_sha1() -> str | None:
    """探针 schema 的 12 位 sha1（形状变了 ⇒ 上次验证不再代表现在）。"""
    return hashlib.sha1(json.dumps(PROBE_SCHEMA, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _write_probe_workspace(workspace: Path) -> tuple[Path, Path, Path]:
    """写三份小文件：`probe.txt`（要读的目标）/ `system.md` / `schema.json`。"""
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / PROBE_FILENAME
    target.write_text(PROBE_ECHO, encoding="utf-8")
    prompt_path = workspace / "prompt.md"
    prompt_path.write_text(PROBE_PROMPT, encoding="utf-8")
    system_path = workspace / "system.md"
    system_path.write_text(PROBE_SYSTEM, encoding="utf-8")
    schema_path = workspace / "schema.json"
    schema_path.write_text(json.dumps(PROBE_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")
    return prompt_path, system_path, schema_path


def build_probe_request(workspace: Path) -> AgentRequest:
    """探针的全部参数：一份 `AgentRequest`。**没有第二个 spawn 点。**

    不需要 `capability`：探针用的是**最小 schema**（153 B，天然过三道闸门），不做身份
    断言（`schema_source_path=None`），也不看工具清单——那些都由 `build_argv()` 从
    `settings` 与本进程的能力快照里取。多收一个不用的参数只会让人以为它有用。
    """
    prompt_path, system_path, schema_path = _write_probe_workspace(workspace)
    return AgentRequest(
        run_id=f"probe-{uuid.uuid4().hex[:12]}",
        workspace=workspace,
        prompt_path=prompt_path,
        system_prompt_path=system_path,
        schema_path=schema_path,
        # 探针的 schema 不是报告 schema ⇒ 不做身份断言（`None`）。
        schema_source_path=None,
        instrument_symbol="-",
        instrument_name="probe",
        max_turns=PROBE_MAX_TURNS,
        max_duration_ms=PROBE_MAX_DURATION_MS,
        # 三个覆盖：K2 与 K4 的唯一差别就写在这里。
        max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
        retry_max=0,
        repair_turns=0,
    )


def _judge(result: AgentResult) -> ProbeOutcome:
    """把 `AgentResult` 翻译成 `ProbeOutcome`（三态判定，不给模糊地带）。

    1. `structured.value.ok is True` 且 `echo.strip() == PROBE_ECHO` → ok；
    2. 有 `structured.value` 但 echo 不对 → `probe_echo_mismatch`
       （端点通了，但回的不是我们教它回的东西 —— 通常意味着模型/网关在改写 JSON）；
    3. 没有结构化结果 → 码原样来自 **K3 的同一个** `classify_failure()`
       （`provider_auth` / `provider_unavailable` / `usage_error` / …）。

    为什么第 3 条不自己写一套判定：两套判定必然漂移，而漂移的方向永远是"面板说好、生成说坏"。
    """
    value = result.doc if isinstance(result.doc, dict) else None
    usage = result.usage
    base: dict[str, Any] = {
        "engine": _engine_identity(result),
        "tokens_in": usage.input_tokens,
        "tokens_out": usage.output_tokens,
        "turns": usage.turns,
        "structure_attempts": result.structured_attempts,
    }
    if value is not None:
        echo = str(value.get("echo") or "").strip()
        if value.get("ok") is True and echo == PROBE_ECHO:
            return ProbeOutcome(ok=True, code=None, message=None, **base)
        return ProbeOutcome(
            ok=False,
            code="probe_echo_mismatch",
            message=f"端点通了但回的不是约定内容（echo={echo[:32]!r}）",
            **base,
        )
    return ProbeOutcome(
        ok=False,
        code=result.error_code or "internal",
        message=result.error_message,
        **base,
    )


def _engine_identity(result: AgentResult) -> dict[str, Any]:
    """`{provider, model, baseUrlHost}` —— 只用**观测到**的事实，不猜。"""
    info = result.engine_info or {}
    return {
        "provider": info.get("provider") or result.provider,
        "model": info.get("model") or result.model,
        "baseUrlHost": host_of(info.get("baseUrl") or get_settings().aragon_base_url),
    }


async def run_probe(
    workspace: Path,
    *,
    capability: AragonCapability | None = None,
) -> ProbeOutcome:
    """跑一次最小真实生成。**不抛**：任何异常都变成 `ok=False` 的一条记录（硬约束 2）。

    降级永不上抛：探针失败**不阻断**生成，只让 `capabilities` 变红。
    """
    capability = capability or check_aragon()
    started = time.monotonic()
    try:
        request = build_probe_request(workspace)
        # 零新增 spawn 点：走既有 runner（`max_attempts=1` —— 一次自检不值得再花一次 token）。
        result = await AragonRunner(capability, max_attempts=1).run(request)
        outcome = _judge(result)
    except AgentError as exc:
        outcome = ProbeOutcome(ok=False, code=exc.code, message=exc.message)
    except Exception as exc:  # pragma: no cover - 只在 runner 真崩时走到
        logger.exception("probe crashed")
        outcome = ProbeOutcome(ok=False, code="internal", message=f"{type(exc).__name__}: {exc}"[:300])
    outcome.ms = int((time.monotonic() - started) * 1000)
    outcome.cli = capability.cli
    outcome.schema_sha1 = _schema_sha1()
    if not outcome.engine:
        outcome.engine = {
            "provider": capability.provider or get_settings().aragon_provider or None,
            "model": capability.model or get_settings().aragon_model or None,
            "baseUrlHost": capability.baseUrlHost,
        }
    logger.info("probe done ok=%s code=%s ms=%s", outcome.ok, outcome.code, outcome.ms)
    return outcome


__all__ = [
    "PROBE_ECHO",
    "PROBE_FILENAME",
    "PROBE_MAX_DURATION_MS",
    "PROBE_MAX_OUTPUT_TOKENS",
    "PROBE_MAX_TURNS",
    "PROBE_PROMPT",
    "PROBE_SCHEMA",
    "ProbeOutcome",
    "build_probe_request",
    "probe_identity",
    "run_probe",
]
