"""失败分类学（spec §20.3.4 / §20.5.4 / M20；AC-73 / AC-74 / AC-76 / AC-82）。

这个文件是本轮最重要的守卫，因为它守的是**最贵的一类缺陷：什么都不报**。历史事实：

* 有 `result` 行时，`_finalize` 过去无条件写 `result_schema_unsatisfied`（`aragon_runner.py`
  旧 `:509-511`），于是"`--retry-max 0` + 坏 base URL"这种**有 result 行、`error.code`
  是 `network_error`、`retries=0`** 的失败被报成"模型不肯提交报告"；
* 没有 `result` 行时，8 条 `retry` 事件被完全忽略，网络问题被记成 `agent_error`
  （"引擎异常退出"）；
* exit 2 + `result_schema_too_deep` 被报成 `usage_error`（"参数错误"）——历史 run
  `06456fcb` 就是这么死的，而提示语对实现者没有任何指向性。

**分类的第一真源是 CLI 的 `error.code`，不是 exit code**（V8-P1-1）。所以这里先写反例：
"exit 1 不许落 `agent_error`"、"有 result 行的 `network_error` 不许落
`result_schema_unsatisfied`"，再写正例（§20.7.3）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent.aragon_runner import AragonRunner
from app.agent.failure import (
    CLI_ERROR_CODE_MAP,
    EXIT_MAP,
    RUN_ERROR_CODES,
    STDERR_REFINE,
    failure_fingerprint,
    hint_for,
    is_retryable,
)
from app.agent.preflight import AragonCapability

FIXTURES = Path(__file__).parent / "fixtures"
FAKE = FIXTURES / "fake_aragon.py"
ALL_TOOLS = ["read_file", "glob", "grep", "list_dir", "submit_result"]


def capability() -> AragonCapability:
    return AragonCapability(
        available=True,
        cli="0.6.68",
        versionOk=True,
        provider="openai",
        model="deepseek-flash",
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=list(ALL_TOOLS),
        tools=list(ALL_TOOLS),
        resolvedBinary=str(FAKE),
        result={"schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 5000, "maxRepairTurns": 2},
    )


def classify(observed: dict, exit_code: int | None, stderr: str = "", tmp_path: Path | None = None) -> tuple[str, str]:
    """跑一次分类（`_classify_failure` 是唯一入口，O-14）。"""
    root = tmp_path or Path(__file__).parent
    stderr_path = root / "classify.stderr.log"
    if stderr:
        stderr_path.write_text(stderr, encoding="utf-8")
    else:
        stderr_path.write_text("", encoding="utf-8")
    return AragonRunner(capability())._classify_failure(observed, exit_code, stderr_path)


def observed(
    *, result: dict | None = None, retries: int = 0, last_retry: dict | None = None, fatal_code: str | None = None
) -> dict:
    return {
        "result": result,
        "retries": retries,
        "last_retry": last_retry,
        "text": [],
        "usage": None,
        "error": None,
        "fatal_code": fatal_code,
    }


# ── AC-73：分类优先级逐条可判（表驱动） ─────────────────────────────────────


@pytest.mark.parametrize(
    ("case", "observed_payload", "exit_code", "stderr", "expected"),
    [
        # ① CLI 自己给的码永远优先
        (
            "cli_says_unsatisfied",
            observed(result={"isError": True, "error": {"code": "result_schema_unsatisfied", "message": "x"}}),
            1,
            "",
            "result_schema_unsatisfied",
        ),
        # ② **头号守卫**：有 result 行 + error.code=network_error + retries=0
        #    过去这里落 exit 1 → `result_schema_unsatisfied`，把网络问题报成"模型没提交"。
        (
            "cli_says_network_error",
            observed(result={"isError": True, "error": {"code": "network_error", "message": "socket hang up"}}),
            1,
            "",
            "provider_unavailable",
        ),
        (
            "cli_says_rate_limited",
            observed(result={"isError": True, "error": {"code": "rate_limited", "message": "429"}}),
            1,
            "",
            "provider_unavailable",
        ),
        (
            "cli_says_auth_error",
            observed(result={"isError": True, "error": {"code": "auth_error", "message": "401"}}),
            1,
            "",
            "provider_auth",
        ),
        # ③ exit 2 + `result_schema_too_deep` → `schema_over_budget`（不是"参数错误"）
        (
            "exit2_too_deep",
            observed(),
            2,
            "error: result_schema_too_deep: nests 13 levels deep\n",
            "schema_over_budget",
        ),
        ("exit2_unknown_option", observed(), 2, "error: unknown option '--no-skills'\n", "usage_error"),
        # ③b ★实施发现（I-63）：真实 CLI 对供应商网络失败给的 `error.code` 是 `stream_error`。
        #     AC-83 的真实失败探针就是它——只照 `errorType` 的词汇写映射会落到 `agent_error`。
        (
            "cli_says_stream_error",
            observed(
                result={
                    "isError": True,
                    "error": {
                        "code": "stream_error",
                        "message": "Network error reaching the provider. (openai network error: fetch failed)",
                    },
                }
            ),
            1,
            "",
            "provider_unavailable",
        ),
        # ③c 没有 result 行、只有一行致命的 `error`：同样以 CLI 的码为准。
        ("fatal_error_line_only", observed(fatal_code="stream_error"), 1, "", "provider_unavailable"),
        # ④ exit 0 + 有 result 行、无 structured、无 error → `result_schema_unsatisfied`
        (
            "exit0_no_structured",
            observed(result={"isError": False, "error": None, "structured": None}),
            0,
            "",
            "result_schema_unsatisfied",
        ),
        # ⑤ exit 3（无 result 行）→ `budget_exceeded`
        ("exit3_budget", observed(), 3, "", "budget_exceeded"),
        # 别的 exit code：不允许"什么都算 agent_error"，但未知码确实只能落这里
        ("exit1_no_result", observed(), 1, "", "result_schema_unsatisfied"),
        ("exit130_cancelled", observed(), 130, "", "cancelled"),
        ("exit143_cancelled", observed(), 143, "", "cancelled"),
        ("exit129_cancelled", observed(), 129, "", "cancelled"),
        ("exit0_no_result", observed(), 0, "", "result_missing"),
        ("exit7_unknown", observed(), 7, "", "agent_error"),
        # 未映射的 CLI 码 → `agent_error`，但原码必须留在 message 里
        (
            "cli_unknown_code",
            observed(result={"isError": True, "error": {"code": "brand_new_code", "message": "???"}}),
            1,
            "",
            "agent_error",
        ),
    ],
)
def test_classification_table(case, observed_payload, exit_code, stderr, expected, tmp_path: Path) -> None:
    """AC-73：一个参数一张表。第 ② 条是本轮的回归重点（C-21）。"""
    code, _message = classify(observed_payload, exit_code, stderr, tmp_path)
    assert code == expected, case


def test_network_error_with_a_result_line_never_falls_into_result_schema_unsatisfied(tmp_path: Path) -> None:
    """C-21 的**两种形状**都断言过：有 result 行与无 result 行。"""
    code_with_result, _ = classify(
        observed(result={"isError": True, "error": {"code": "network_error", "message": "getaddrinfo ENOTFOUND"}}),
        1,
        "",
        tmp_path,
    )
    code_without_result, _ = classify(observed(retries=0), 1, "network_error: ENOTFOUND\n", tmp_path)
    assert code_with_result == "provider_unavailable"
    # 无 result 行、无 retry、stderr 也没有凭证特征 → exit 1 的默认落点是"没提交"，
    # 这**不是**网络问题的分支（它靠 error.code / retry 观测区分）。
    assert code_without_result == "result_schema_unsatisfied"


def test_auth_hint_from_the_engine_log(tmp_path: Path) -> None:
    code, message = classify(observed(), 1, "error: 401 Unauthorized: invalid api key\n", tmp_path)
    assert code == "provider_auth"
    assert "凭证" in message


# ── AC-74：抖动风暴 → provider_unavailable，且 message 说清是什么 ───────────


def test_retry_storm_is_provider_unavailable(tmp_path: Path) -> None:
    """AC-74：无 `result` 行 + ≥1 条 `retry` → `provider_unavailable`，
    message 含 `lastErrorType` / `maxRetries` / `attempts` 三个事实。"""
    last = {
        "attempt": 6,
        "maxRetries": 10,
        "errorType": "network_error",
        "delayMs": 15925,
        "tier": 1,
        "retryCount": 8,
        "retries": 8,
    }
    code, message = classify(observed(retries=8, last_retry=last), 1, "", tmp_path)
    assert code == "provider_unavailable"
    assert "network_error" in message
    assert "10" in message, "maxRetries 必须出现在人话里，否则用户不知道为什么等这么久"
    assert "8" in message, "累计重试次数必须是本地计数（attempt 会重置）"


# ── AC-76：每个码都有非空 hint，且不含英文原始报错 ─────────────────────────


def test_every_code_has_a_human_hint() -> None:
    """AC-76：遍历全部 run 级码，hint 非空、且不出现 `exit code` / `stderr` 字样。"""
    assert len(RUN_ERROR_CODES) >= 15, "§20.5.4 的 15 条一条都不能少"
    for code, spec in RUN_ERROR_CODES.items():
        assert spec.code == code
        assert spec.hint.strip(), f"{code} 没有 hint：用户会看到一个光秃秃的错误码"
        lowered = spec.hint.lower()
        assert "exit code" not in lowered, f"{code} 的 hint 里混进了英文原始报错"
        assert "stderr" not in lowered, f"{code} 的 hint 里混进了英文原始报错"
        assert spec.severity in ("config", "transient", "model", "user", "internal", "engine")

    required = {
        "schema_over_budget",
        "schema_drift",
        "prompt_over_budget",
        "provider_unavailable",
        "provider_auth",
        "result_missing",
        "result_schema_unsatisfied",
    }
    assert required <= set(RUN_ERROR_CODES), "本轮新增的四个码必须进表"


def test_retryable_comes_from_the_single_table() -> None:
    """`run_manager` 的内联元组已删除：retryable 只有一处真源。"""
    assert is_retryable("provider_unavailable") is True
    assert is_retryable("provider_auth") is False
    assert is_retryable("schema_over_budget") is False
    assert is_retryable("cancelled") is False
    assert is_retryable("no_such_code") is False
    assert hint_for("no_such_code"), "未知码也要给一句诚实的兜底，而不是空字符串"


# ── 失败指纹（H3.5 / R-38） ────────────────────────────────────────────────


def test_fingerprint_is_stable_across_variable_parts() -> None:
    """R-38：消息里的数字与绝对路径必须先被抹掉，否则"同因不同表现"算成不同指纹。"""
    first = failure_fingerprint("provider_unavailable", "failed after 6 retries in 44.3s", 1)
    second = failure_fingerprint("provider_unavailable", "failed after 9 retries in 61.7s", 1)
    assert first == second
    assert len(first) == 12

    windows = failure_fingerprint("schema_drift", "C:\\runs\\a1\\schema.json differs", 2)
    posix = failure_fingerprint("schema_drift", "/tmp/runs/b2/schema.json differs", 2)
    assert windows == posix

    # 不同码 / 不同 exit code 必须分开
    assert first != failure_fingerprint("provider_auth", "failed after 6 retries in 44.3s", 1)
    assert first != failure_fingerprint("provider_unavailable", "failed after 6 retries in 44.3s", 2)


# ── AC-82：fake CLI 的 exit code 与实测契约逐行一致 ────────────────────────


#: `FAKE_ARAGON_MODE` → 期望 exit code。表本身就是契约（§20.5.4）。
FAKE_EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "unsatisfied": 1,  # ★v7：实测 exit 1（E-42），不是 3
    "exit1_unsatisfied": 1,
    "attempts_two_then_ok": 0,
    "no_result_line": 0,
    "retry_storm": 1,
    "schema_too_deep": 2,
    "budget": 3,
    "usage": 2,
    "error": 1,
    "ask_ok": 0,
    "ask_denied": 0,
    "ask_badcite": 0,
}


@pytest.mark.parametrize(("mode", "expected"), sorted(FAKE_EXIT_CODES.items()))
def test_fake_cli_exit_codes_match_measured_contract(mode: str, expected: int, tmp_path: Path) -> None:
    """AC-82：剧本的退出码必须跟实测一致，否则"分类测试全绿"是假的。"""
    env = {**os.environ, "FAKE_ARAGON_MODE": mode}
    completed = subprocess.run(  # noqa: S603 - 本地 fixture，参数全常量
        [sys.executable, str(FAKE), "exec"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
        env=env,
        check=False,
    )
    assert completed.returncode == expected, f"{mode} 退出码与实测契约不一致：{completed.stderr[:200]}"


def test_unsatisfied_maps_to_the_same_code_as_the_measured_exit_1(tmp_path: Path) -> None:
    """exit 1 的落点由 `EXIT_MAP` 决定：`result_schema_unsatisfied`（H-4）。"""
    assert EXIT_MAP[1] == ("result_schema_unsatisfied", "模型多次提交仍未满足 JSON Schema")
    assert "result_schema_too_deep" in {needle for needle, _ in STDERR_REFINE}
    assert CLI_ERROR_CODE_MAP["network_error"] == "provider_unavailable"

# ── ★v8 评审：三处分类缺陷的回归（B-1 / B-2 / B-3）──────────────────────────


def test_refined_code_comes_with_its_own_sentence(tmp_path: Path) -> None:
    """B-1：精化后的码必须配精化后的句子。

    实测的历史失败（run `06456fcb`）是 exit 2 + `result_schema_too_deep`。过去码改成
    `schema_over_budget`，句子却还是 `EXIT_MAP[2]` 的"引擎配置或参数错误"——
    于是界面上一句话说"schema 太深"、下一句话说"参数错误"，而 CLI 的原文（几层嵌套、
    上限多少）被整条丢掉。这条断言三件事同时成立：码对、句子来自该码的 hint、原文可查。
    """
    code, message = classify(
        {},
        2,
        "error: result_schema_too_deep: nests 13 levels deep; the limit is 12\n",
        tmp_path,
    )
    assert code == "schema_over_budget"
    assert message != EXIT_MAP[2][1]
    assert message.startswith(hint_for("schema_over_budget"))
    assert "nests 13 levels deep" in message, "引擎原文必须留下，它是唯一能定位到那层嵌套的证据"


def test_stderr_refine_only_applies_to_exit_2(tmp_path: Path) -> None:
    """B-3：`STDERR_REFINE` 只在 exit 2 上生效（§20.3.4 的原话是"exit 2 的已知精化"）。"""
    # exit 0 + 一句无关的 `unknown option` 不许把 `result_missing` 改写成 `usage_error`。
    code, _ = classify({}, 0, "warn: discarding unknown option from cached config\n", tmp_path)
    assert code == "result_missing"

    # exit 143（被 SIGTERM 结束）同理：结论是"被终止"，不是"参数错误"。
    code, _ = classify({}, 143, "unknown option\n", tmp_path)
    assert code == "cancelled"


def test_a_bare_401_substring_never_overrides_a_certain_exit_code(tmp_path: Path) -> None:
    """B-2：`AUTH_HINTS` 里是裸子串（`401` / `403` / `quota`），不许改写已知结论。

    场景很具体：本机一个上游端口是 `4013`，或者供应商的 stderr 里带一句
    "quota will reset at …"。用户点了取消（130/143），看到"模型凭证被拒绝"——
    一个已经确定的结论被一次子串匹配推翻，用户会去查一个不存在的 Key 问题。
    """
    code, _ = classify({}, 143, "fetch failed: connect ECONNREFUSED 127.0.0.1:4013\n", tmp_path)
    assert code == "cancelled"

    code, _ = classify({}, 3, "budget: max-duration reached; quota will reset later\n", tmp_path)
    assert code == "budget_exceeded"

    # 反过来，真的没有结论时（无 result 行、非确定退出码）它仍然要能认出来。
    code, _ = classify({}, 1, "401 Unauthorized: invalid api key\n", tmp_path)
    assert code == "provider_auth"

