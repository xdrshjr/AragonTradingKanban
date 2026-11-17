"""`deep` ask runner — one spawn, the same argv contract as generation (A1 / §18.3.2).

There is exactly **one** argv contract in this project (`aragon_runner.build_argv`),
and `deep` asks use it verbatim: same strict permission mode, same `submit_result`
requirement (the P0-1 lesson), same budget flags, same stream-json parsing and the
same process-tree cancellation. What differs is only *what* is handed in — the ask
schema, the ask system prompt, a rebuilt workspace and a 12-turn budget.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import get_settings
from ..logging import get_logger
from .aragon_runner import SUBMIT_TOOL, AragonRunner, build_argv
from .preflight import AragonCapability, check_aragon
from .runner_base import AgentEvent, AgentRequest, AgentResult

logger = get_logger("ask_runner")

ASK_SCHEMA_FILE = "ask.schema.json"
ASK_SYSTEM_FILE = "ask_system.md"

#: Which workspace files the prompt may advertise. Only the ones that actually
#: exist are listed — a template that promises `snapshot.json` and then has it
#: missing teaches the model to invent (§18.3.2, V4-P1-6).
ASK_FILE_DESCRIPTIONS: dict[str, str] = {
    "report.json": "这份报告的结构化原文（章节 / 块 / 来源），答案的唯一证据来源",
    "sources.md": "S1..Sn 来源清单（标题 / 发布方 / 时间 / 链接）",
    "snapshot.json": "生成这份报告时的行情与财务快照，仅用于核对数字口径",
    "fundamentals.md": "近 8 期营收 / 利润 / 现金流，仅用于核对数字口径",
}


def build_ask_argv(request: AgentRequest, capability: AragonCapability, binary: str) -> list[str]:
    """The generation argv contract, pointed at the ask schema/prompt.

    Kept as its own name because AC-28 asserts on it directly: the allow list must
    contain `submit_result`, otherwise the model has no channel to submit the
    structured answer and the schema is never satisfied.
    """
    argv = build_argv(request, capability, binary)
    allow_index = argv.index("--allow-tool") + 1 if "--allow-tool" in argv else -1
    allow_list = argv[allow_index].split(",") if allow_index > 0 else []
    if SUBMIT_TOOL not in allow_list:  # pragma: no cover - build_argv already guards
        raise ValueError("ask argv 的 --allow-tool 必须包含 submit_result（P0-1 教训）")
    return argv


def render_ask_prompt(
    path: Path,
    workspace: Path,
    *,
    question: str,
    report_row: Any,
    doc: dict[str, Any],
    available_files: list[str],
) -> Path:
    """Write `ask-<id>.md` + the system prompt, and return the prompt path."""
    settings = get_settings()
    environment = Environment(
        loader=FileSystemLoader(str(settings.prompts_dir)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("ask_task.md.j2")
    sources = [source for source in doc.get("sources") or [] if isinstance(source, dict)]
    prompt = template.render(
        question=question,
        report={
            "title": getattr(report_row, "title", ""),
            "symbol": ((doc.get("instrument") or {}).get("symbol") or ""),
            "instrument_name": ((doc.get("instrument") or {}).get("name") or ""),
            "version": getattr(report_row, "version", 0),
            "as_of": getattr(report_row, "as_of", ""),
            "status": getattr(report_row, "status", ""),
        },
        available_files=available_files,
        descriptions=ASK_FILE_DESCRIPTIONS,
        sections=[
            {"id": section.get("id"), "title": section.get("title")}
            for section in doc.get("sections") or []
            if isinstance(section, dict)
        ],
        source_ids=[str(source.get("id")) for source in sources],
        key_metrics=[
            {"label": item.get("label"), "value": item.get("value")}
            for item in doc.get("keyMetrics") or []
            if isinstance(item, dict)
        ][:8],
    )
    path.write_text(prompt, encoding="utf-8")
    system_target = workspace / ASK_SYSTEM_FILE
    # 与 I-58 同一条纪律（★v8 评审）：提示词也**按字节**复制。`read_text`+`write_text`
    # 在 Windows 上把 `\n` 变成 `\r\n`，两份"文本上相同"的文件因此在 sha1 / 字节数上不同。
    system_target.write_bytes((settings.prompts_dir / "ask_system.md").read_bytes())
    schema_target = workspace / ASK_SCHEMA_FILE
    # ★v7（I-58 的同型修正）：**按字节**复制，不做文本读写。`read_text`+`write_text`
    # 在 Windows 上会把 `\n` 变成 `\r\n`，于是"文本相同"的副本在 sha1 上是另一份
    # schema —— `build_argv()` 的 `schema_drift` 闸门会当场拦下每一次深度追问。
    schema_target.write_bytes((settings.schemas_dir / ASK_SCHEMA_FILE).read_bytes())
    return path


def build_ask_request(
    *,
    workspace: Path,
    prompt_path: Path,
    instrument_symbol: str,
    instrument_name: str,
) -> AgentRequest:
    settings = get_settings()
    return AgentRequest(
        run_id=f"ask-{workspace.name}",
        workspace=workspace,
        prompt_path=prompt_path,
        system_prompt_path=workspace / ASK_SYSTEM_FILE,
        schema_path=workspace / ASK_SCHEMA_FILE,
        # ★实施发现（I-60）：ask 路径的真源是 `ask.schema.json`，不是报告 schema。
        schema_source_path=settings.schemas_dir / ASK_SCHEMA_FILE,
        instrument_symbol=instrument_symbol,
        instrument_name=instrument_name,
        max_turns=settings.ask_deep_max_turns,
        max_duration_ms=settings.ask_deep_max_duration_ms,
    )


def make_runner(capability: AragonCapability | None = None) -> AragonRunner:
    """A real runner bound to the current capability verdict.

    `deep` asks are only offered when the engine can actually submit structured
    results; the router refuses earlier (409 `ask_deep_unavailable`) so reaching
    this line means the capability is genuinely present.
    """
    return AragonRunner(capability or check_aragon())


async def run_deep_ask(
    request: AgentRequest,
    *,
    capability: AragonCapability | None = None,
    on_event: Callable[[AgentEvent], None] | None = None,
    runner: AragonRunner | None = None,
) -> AgentResult:
    """Run one ask to completion and return whatever it produced."""
    active = runner or make_runner(capability)
    return await active.run(request, on_event)


__all__ = [
    "ASK_SCHEMA_FILE",
    "ASK_SYSTEM_FILE",
    "build_ask_argv",
    "build_ask_request",
    "make_runner",
    "render_ask_prompt",
    "run_deep_ask",
]
