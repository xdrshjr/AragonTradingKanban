#!/usr/bin/env python
"""A controllable stand-in for the `aragon` CLI (spec §12.1).

It exists so the runner's argv contract and every failure mode can be tested
without a model, a network or a key. Crucially it does **not** "just work": the
`info` payload is driven by `FAKE_ARAGON_INFO`, so the preflight tests can prove
that a CLI without `submit_result` is refused rather than silently failing.

Modes (`FAKE_ARAGON_MODE`):

  ok                     structured result, exit 0               (the happy path)
  unsatisfied            structured=null, no error, exit **1**    (measured: E-42)
  exit1_unsatisfied      result line with error.code=result_schema_unsatisfied, exit 1
  schema_too_deep        exit 2 + `result_schema_too_deep` on stderr (history run 06456fcb)
  no_result_line         exit 0 and never a result line             (result_missing)
  retry_storm            8 retry events (attempt resets), no result line (E-45)
  attempts_two_then_ok   two rejected submissions, then a valid result with attempts=2
  budget                 no result at all, exit 3                  (budget exhausted)
  usage                  exit 2, empty stdout                      (bad arguments)
  huge                   one 200KB tool_result line, then a valid result (line buffer)
  garbage                non-JSON lines interleaved, then a valid result (parser tolerance)
  hang                   never exits                               (timeout / cancel)
  error                  exit 1 with an error line on stderr
  ask_ok                 a schema-valid ask answer citing a real source (v3 §18.3.2)
  ask_denied             the same shape with `denied: true`  (「报告没写」must be sayable)
  ask_badcite            cites a source id that does not exist (R-16 must drop it)
  probe_ok               K2 self-check: structured `{"ok":true,"echo":"PROBE-ECHO-OK"}` (2 turns)
  probe_bad_echo         the same shape but `echo` is rewritten (a gateway mangling JSON)
  probe_auth             E-71's 401 text, `error.code=stream_error`, exit 1 (K2 failure side)

★v7（§20.4 / P-8）：五个新剧本的退出码以**实测**为准，不是从 `--help` 猜的——
`unsatisfied` 过去写 exit 3，真实 CLI 用 exit **1**（E-42），于是"exit 1 该落哪个码"
这件事在测试里从来没有被真正覆盖过。AC-82 用表驱动逐行比对 `EXIT_MAP`。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

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


ASK_MODES = {"ask_ok", "ask_denied", "ask_badcite"}

#: ★v9（K2）：探针剧本。形状抄自真机（E-86）：一次工具调用 + 一次结构化提交。
PROBE_MODES = {"probe_ok", "probe_bad_echo", "probe_auth"}


def handle_probe(mode: str) -> int:
    emit({"type": "system", "subtype": "init", "text": "自检开始"})
    emit({"type": "tool_call", "name": "read_file", "input": {"path": "probe.txt"}})
    echo = "PROBE-ECHO-OK" if mode != "probe_bad_echo" else "PROBE-ECH0-Rewritten-By-Gateway"
    emit(
        {
            "type": "tool_result",
            "name": "read_file",
            "input": {"path": "probe.txt"},
            "output": echo,
            "isError": False,
            "durationMs": 3,
        }
    )
    if mode == "probe_auth":
        # E-71 的逐字形状：`error.code` 是 `stream_error`，真正的病在 message 里。
        emit(
            {
                "type": "error",
                "fatal": True,
                "code": "stream_error",
                "message": "Authentication failed - check your API key.",
            }
        )
        emit(
            {
                "type": "result",
                "isError": True,
                "error": {
                    "code": "stream_error",
                    "message": (
                        "Authentication failed - check your API key. (openai API error 401: "
                        '{"error":{"message":"Authentication Fails, Your api key: ****O5p6 is invalid"}})'
                    ),
                },
                "stopReason": "error",
                "exitCode": 1,
                "text": "",
                "structured": None,
                "usage": {"inputTokens": 4100, "outputTokens": 12, "totalTokens": 4112},
                "cost": {"amount": 0, "currency": "USD", "known": False},
            }
        )
        return 1

    payload = {"ok": True, "echo": echo}
    emit(
        {
            "type": "result",
            "isError": False,
            "error": None,
            "stopReason": "end_turn",
            "exitCode": 0,
            "text": "自检完成。",
            "structured": {
                "value": payload,
                "schemaName": "schema.json",
                "schemaBytes": 153,
                "attempts": 1,
                "acceptedAt": "2026-02-21T09:12:03Z",
            },
            "usage": {"inputTokens": 11251, "outputTokens": 215, "totalTokens": 11466},
            "cost": {"amount": 0, "currency": "USD", "known": False},
        }
    )
    return 0


def ask_payload(mode: str) -> dict:
    """Three ask answers: grounded, explicitly denied, and citing a fake source."""
    if mode == "ask_denied":
        return {
            "answer": "这份报告没有讨论卫星发射计划，它只覆盖了白酒主业与渠道结构。",
            "citations": [],
            "confidence": "low",
            "denied": True,
        }
    if mode == "ask_badcite":
        return {
            "answer": "毛利率下滑主要来自渠道结构变化。",
            "citations": [
                {"sourceId": "S99", "claim": "渠道结构变化导致毛利率下降"},
                {"sourceId": "S1", "claim": "毛利率 62.1%"},
            ],
            "confidence": "medium",
            "denied": False,
        }
    return {
        "answer": "毛利率下滑主要来自渠道结构与产品结构变化，公司给出的口径是同比 -3.4pct。",
        "citations": [{"sourceId": "S1", "claim": "毛利率 62.1%，同比 -3.4pct"}],
        "confidence": "high",
        "denied": False,
    }


def _system_event() -> dict:
    """★v7 §20.3.7：实测的 `system` 首行形状——`baseUrl` 只在这里出现（E-39 的 info 没有）。"""
    return {
        "type": "system",
        "subtype": "init",
        "sessionId": "fake-session",
        "model": {"provider": "openai", "id": "deepseek-flash", "baseUrl": "https://api.deepseek.com"},
    }


#: report-workflow-graph §3.6.8：四种遵循度。读取 cwd 下的 `workflow.json`（工作区）按 items 发进度标记。
WORKFLOW_MODES = {"ok_workflow", "ok_workflow_partial", "ok_workflow_split", "ok_workflow_none"}


def emit_workflow_markers(mode: str) -> str:
    """Emit marker `text_delta`s for the workspace's plan and return the final `result.text`
    (which carries the same markers, so the "strip markers from `result.text`" path is exercised)."""
    if mode == "ok_workflow_none":
        return "报告已完成。"
    try:
        items = json.loads(Path("workflow.json").read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError):
        return "报告已完成。"
    keys = [item["key"] for item in items]
    if mode == "ok_workflow_partial":
        keys = keys[: max(1, len(keys) // 2)]
    lines: list[str] = []
    for index, key in enumerate(keys):
        for state in ("start", "done"):
            marker = f"[[wf:{key}:{state}]]"
            lines.append(marker)
            if mode == "ok_workflow_split" and index == 0 and state == "done":
                cut = len(marker) // 2
                emit({"type": "text_delta", "delta": marker[:cut]})
                emit({"type": "text_delta", "delta": marker[cut:] + "\n"})
            else:
                emit({"type": "text_delta", "delta": marker + "\n"})
    return "报告已完成。\n" + "\n".join(lines)


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

    if mode == "schema_too_deep":
        # ★v7：实测 exit 2 + 这句话（历史 run 06456fcb）。过去被分类成"参数错误"。
        emit({"type": "system", "subtype": "init", "text": "校验 schema"})
        sys.stderr.write("error: result_schema_too_deep: nests 13 levels deep; the limit is 12\n")
        return 2

    if mode == "no_result_line":
        # ★v7：正常退出但一行 result 都没有（`_classify_exit(0)` 的唯一真实来源）。
        emit({"type": "system", "subtype": "init", "text": "研究开始"})
        emit({"type": "execution_progress", "turns": 2, "elapsedMs": 1200})
        return 0

    if mode == "retry_storm":
        # ★v7 / E-45 的逐字形状：8 条 retry，`attempt` 序列出现两次 1→6（**会重置**）。
        emit(_system_event())
        for attempt, delay in ((1, 568), (2, 1753), (1, 977), (2, 1998), (3, 3743), (4, 6030), (5, 15208), (6, 15925)):
            emit(
                {
                    "type": "retry",
                    "sessionId": "fake-retry",
                    "attempt": attempt,
                    "maxRetries": 10,
                    "errorType": "network_error",
                    "delayMs": delay,
                    "tier": 1,
                    "tierCount": 2,
                    "totalAttempts": attempt,
                }
            )
        sys.stderr.write("aragon: provider request failed after retries\n")
        return 1

    if mode in ASK_MODES:
        emit({"type": "system", "text": "读报告"})
        emit({"type": "tool_call", "name": "read_file", "input": {"path": "report.json"}})
        emit(
            {
                "type": "tool_result",
                "name": "read_file",
                "input": {"path": "report.json"},
                "output": '{"headline":"…"}',
                "isError": False,
                "durationMs": 8,
            }
        )
        emit({"type": "text_delta", "delta": "毛利率"})
        payload = ask_payload(mode)
        emit(
            {
                "type": "result",
                "isError": False,
                "error": None,
                "stopReason": "end_turn",
                "exitCode": 0,
                "text": payload["answer"],
                "structured": {
                    "value": payload,
                    "schemaName": "ask.schema.json",
                    "schemaBytes": 913,
                    "attempts": 1,
                    "acceptedAt": "2026-02-10T01:04:00Z",
                },
                "usage": {"inputTokens": 1200, "outputTokens": 240, "totalTokens": 1440},
                "cost": {"amount": 0.004, "currency": "USD", "known": True},
            }
        )
        return 0

    if mode in PROBE_MODES:
        return handle_probe(mode)

    if mode == "budget":
        emit({"type": "system", "text": "starting"})
        emit({"type": "execution_progress", "turns": 40, "elapsedMs": 900000})
        return 3

    if mode == "error":
        # ★v7：真实 CLI 在供应商失败时给出**带 error.code 的 result 行**（探针 C / E-42
        # 的形状），exit 1。这正是"exit code 相同、原因不同"那条规则要区分的情形。
        emit(_system_event())
        sys.stderr.write("aragon: provider request failed (rate limited)\n")
        emit(
            {
                "type": "result",
                "isError": True,
                "error": {"code": "rate_limited", "message": "provider request failed: rate limited"},
                "stopReason": "error",
                "exitCode": 1,
                "text": "模型服务拒绝了这次请求。",
                "structured": None,
                "usage": {"inputTokens": 1200, "outputTokens": 0, "totalTokens": 1200},
                "cost": {"amount": 0, "currency": "USD", "known": False},
            }
        )
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

    emit({"type": "system", "subtype": "init", "text": "研究开始"})
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
        # ★v7：真实 CLI 用 exit **1**（E-42），不是 3。没有 `error` 对象 →
        # 分类落 `result_schema_unsatisfied`（§20.3.4 第 2c 条）。
        emit(
            {
                "type": "result",
                "isError": False,
                "error": None,
                "stopReason": "error",
                "exitCode": 1,
                "text": "我尝试提交报告，但结构化提交工具不可用。",
                "structured": None,
                "cost": {"amount": 0, "currency": "USD", "known": False},
            }
        )
        return 1

    if mode == "exit1_unsatisfied":
        # ★v7：探针 C 的逐字形状（E-42）——`error.code` 在，`isError: true`。
        emit(
            {
                "type": "result",
                "isError": True,
                "error": {
                    "code": "result_schema_unsatisfied",
                    "message": "No result matching schema.json was submitted.",
                },
                "stopReason": "error",
                "exitCode": 1,
                "text": "两次提交都被拒了。",
                "structured": None,
                "usage": {"inputTokens": 17864, "outputTokens": 1066, "totalTokens": 18930},
                "cost": {"amount": 0, "currency": "USD", "known": False},
            }
        )
        return 1

    if mode == "attempts_two_then_ok":
        # ★v7：两次被拒 + 第三次成功，`structured.attempts` 必须是 2（不是 1）。
        for index in (1, 2):
            emit(
                {
                    "type": "tool_result",
                    "name": "submit_result",
                    "input": {"attempt": index},
                    "output": "rejected: result: required (nothing was submitted)",
                    "isError": True,
                    "durationMs": 5,
                }
            )

    doc = load_sample()
    emit(_system_event())
    result_text = emit_workflow_markers(mode) if mode in WORKFLOW_MODES else "报告已完成。"
    emit(
        {
            "type": "result",
            "isError": False,
            "error": None,
            "stopReason": "end_turn",
            "exitCode": 0,
            "text": result_text,
            "result": json.dumps(doc, ensure_ascii=False),
            "structured": {
                "value": doc,
                "schemaName": "report_doc.schema.json",
                "schemaBytes": 19136,
                "attempts": 2 if mode == "attempts_two_then_ok" else 1,
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
