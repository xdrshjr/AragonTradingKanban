#!/usr/bin/env python
"""A controllable stand-in for the `aragon` CLI (spec §12.1).

It exists so the runner's argv contract and every failure mode can be tested
without a model, a network or a key. Crucially it does **not** "just work": the
`info` payload is driven by `FAKE_ARAGON_INFO`, so the preflight tests can prove
that a CLI without `submit_result` is refused rather than silently failing.

Modes (`FAKE_ARAGON_MODE`):

  ok           structured result, exit 0                      (the happy path)
  unsatisfied  structured=null, isError=false, exit 3         (schema never met)
  budget       no result at all, exit 3                       (budget exhausted)
  usage        exit 2, empty stdout                           (bad arguments)
  huge         one 200KB tool_result line, then a valid result (line buffer)
  garbage      non-JSON lines interleaved, then a valid result (parser tolerance)
  hang         never exits                                    (timeout / cancel)
  error        exit 1 with an error line on stderr
"""

from __future__ import annotations

import json
import os
import sys
import time

FIXTURES = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(FIXTURES, "sample_report.json")

DEFAULT_INFO = {
    "cli": "0.6.68",
    "core": "0.2.80",
    "node": "v22.18.0",
    "schemaVersion": 1,
    "provider": "openai",
    "model": "deepseek-flash",
    "hasApiKey": True,
    "tools": [
        "read_file",
        "glob",
        "grep",
        "list_dir",
        "submit_result",
        "bash",
        "write_file",
        "edit_file",
        "skill",
        "task",
    ],
    "permissionModes": ["auto", "plan", "strict", "ask"],
    "features": ["exec", "sessions", "permissions", "budgets", "structured_result"],
    "result": {
        "schemaBytes": 65536,
        "schemaDepth": 12,
        "schemaRefs": 64,
        "validationNodes": 5000,
        "maxViolations": 12,
        "valueBytes": 262144,
        "defaultRepairTurns": 1,
        "maxRepairTurns": 2,
        "ajv": True,
    },
}


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_sample() -> dict:
    with open(SAMPLE, encoding="utf-8") as handle:
        return json.load(handle)


def argv_log(argv: list[str]) -> None:
    """Record what we were called with so tests can assert on it (AC-18/AC-19)."""
    target = os.environ.get("FAKE_ARAGON_ARGV_LOG")
    if not target:
        return
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(argv, handle, ensure_ascii=False, indent=2)


def handle_info() -> int:
    override = os.environ.get("FAKE_ARAGON_INFO")
    if override == "missing":
        # A CLI that is not installed at all.
        sys.stderr.write("aragon: command not found\n")
        return 1
    if override == "no_submit":
        info = json.loads(json.dumps(DEFAULT_INFO))
        info["tools"] = [tool for tool in info["tools"] if tool != "submit_result"]
        emit(info)
        return 0
    if override == "no_key":
        info = json.loads(json.dumps(DEFAULT_INFO))
        info["hasApiKey"] = False
        emit(info)
        return 0
    if override == "old":
        info = json.loads(json.dumps(DEFAULT_INFO))
        info["cli"] = "0.5.20"
        emit(info)
        return 0
    if override == "no_ajv":
        info = json.loads(json.dumps(DEFAULT_INFO))
        info["result"]["ajv"] = False
        emit(info)
        return 0
    if override == "no_structured_feature":
        info = json.loads(json.dumps(DEFAULT_INFO))
        info["features"] = [f for f in info["features"] if f != "structured_result"]
        emit(info)
        return 0
    emit(DEFAULT_INFO)
    return 0


def handle_exec(argv: list[str]) -> int:
    argv_log(argv)
    mode = os.environ.get("FAKE_ARAGON_MODE", "ok")

    if mode == "hang":
        # Stay alive until killed — exercises the cancel and timeout paths.
        while True:
            time.sleep(0.2)

    if mode == "usage":
        sys.stderr.write("error: unknown option '--bogus'\n")
        return 2

    if mode == "budget":
        emit({"type": "system", "text": "starting"})
        emit({"type": "execution_progress", "turns": 40, "elapsedMs": 900000})
        return 3

    if mode == "error":
        sys.stderr.write("aragon: provider request failed (rate limited)\n")
        return 1

    if mode == "garbage":
        sys.stdout.write("not json at all\n")
        sys.stdout.write('{"broken": \n')
        sys.stdout.flush()
        emit({"type": "unknown_future_type", "payload": {"whatever": True}})

    if mode == "huge":
        emit(
            {
                "type": "tool_result",
                "name": "read_file",
                "input": {"path": "bars.csv"},
                "output": "x" * 200_000,
                "isError": False,
            }
        )

    emit({"type": "system", "text": "研究开始"})
    emit({"type": "tool_call", "name": "read_file", "input": {"path": "snapshot.json"}})
    emit(
        {
            "type": "tool_result",
            "name": "read_file",
            "input": {"path": "snapshot.json"},
            "output": '{"symbol":"600519.SH"}',
            "isError": False,
            "durationMs": 12,
        }
    )
    emit({"type": "assistant", "text": "先看估值分位。"})
    emit({"type": "text_delta", "delta": "然后"})
    emit({"type": "execution_progress", "turns": 3, "elapsedMs": 4200})

    if mode == "unsatisfied":
        emit(
            {
                "type": "result",
                "isError": False,
                "error": None,
                "stopReason": "max_turns",
                "exitCode": 3,
                "text": "我尝试提交报告，但结构化提交工具不可用。",
                "structured": None,
                "cost": {"amount": 0, "currency": "USD", "known": False},
            }
        )
        return 3

    doc = load_sample()
    emit(
        {
            "type": "result",
            "isError": False,
            "error": None,
            "stopReason": "end_turn",
            "exitCode": 0,
            "text": "报告已完成。",
            "result": json.dumps(doc, ensure_ascii=False),
            "structured": {
                "value": doc,
                "schemaName": "report_doc.schema.json",
                "schemaBytes": 19136,
                "attempts": 1,
                "acceptedAt": "2026-02-10T01:04:00Z",
            },
            "usage": {"inputTokens": 68974, "outputTokens": 20676, "totalTokens": 89650},
            "cost": {"amount": 0.0, "currency": "USD", "known": False},
        }
    )
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write("usage: fake_aragon [info|exec] …\n")
        return 2
    if argv[0] == "info":
        return handle_info()
    if argv[0] == "exec" or (argv[0].startswith("--") and "exec" in argv):
        return handle_exec(argv)
    if argv[0] == "--version":
        sys.stdout.write("0.6.68\n")
        return 0
    sys.stderr.write(f"fake_aragon: unknown command {argv[0]}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
