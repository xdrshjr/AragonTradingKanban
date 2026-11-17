"""K3 失败归因 v2：9 行有序规则表 + **穷尽性**（AC-97 / AC-98 / AC-99 / **AC-108**）。

三条真实夹具（E-68 / E-70 / E-71）逐字抄自真机输出——不是编的：

* E-71 错误密钥：`error.code == "stream_error"`，message 是
  `"Authentication failed - check your API key. (openai API error 401: … is invalid …)"`；
* E-70 网络断：`error.code` **同为 `stream_error`**，message 是
  `"Network error reaching the provider. (openai network error: fetch failed (ECONNRESET: …))"`；
* E-68 坏 provider：`error.code == "config"` + exit 2（现在映射成 `usage_error`）。

同一个 code 下面是两种病 ⇒ 判据必须下沉到 message 的**语义**，而且要求两个条件同时成立
（V10-P1-4）；裸子串 `401` 会把 v8 的 B-2 修过的东西请回来，而 `provider_auth`
**不在** `RETRYABLE_BY_RUNNER` 里，误判一次就少一次重试。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from app.agent.aragon_runner import classify_failure
from app.agent.failure import (
    CLI_ERROR_CODE_MAP,
    RETRYABLE_BY_RUNNER,
    RUN_ERROR_CODES,
    STOP_REASON_MAP,
    looks_like_auth,
    mask_secrets,
)

#: E-71 原文（错误密钥）。
E71_AUTH = (
    "Authentication failed - check your API key. (openai API error 401: "
    '{"error":{"message":"Authentication Fails, Your api key: ****O5p6 is invalid"}})'
)

#: E-70 原文（坏端点/网络断）。
E70_NETWORK = (
    "Network error reaching the provider. (openai network error: "
    "fetch failed (ECONNRESET: Client network socket disconnected before secure TLS connection was established))"
)


def observed(
    *,
    code: str | None = None,
    message: str = "",
    stop_reason: str | None = "end_turn",
    retries: int = 0,
    with_result: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "result": None,
        "text": [],
        "usage": None,
        "retries": retries,
        "engine": None,
        "last_retry": {"errorType": "network_error", "maxRetries": 10, "delayMs": 60000} if retries else None,
        "stop_reason": stop_reason,
    }
    if with_result:
        result: dict[str, object] = {
            "isError": bool(code),
            "error": {"code": code, "message": message} if code else None,
            "stopReason": stop_reason,
            "structured": None,
        }
        payload["result"] = result
    return payload


# ── AC-97：同一个码，两条路 ─────────────────────────────────────────────────


def test_stream_error_with_auth_text_is_provider_auth(tmp_path: Path) -> None:
    code, message = classify_failure(observed(code="stream_error", message=E71_AUTH), 1, tmp_path / "e.log")
    assert code == "provider_auth"
    assert RETRYABLE_BY_RUNNER.isdisjoint({code}), "凭证错重试没有意义，且不在重试白名单里"
    assert RUN_ERROR_CODES[code].retryable is False


def test_stream_error_with_network_text_is_provider_unavailable(tmp_path: Path) -> None:
    code, message = classify_failure(observed(code="stream_error", message=E70_NETWORK), 1, tmp_path / "e.log")
    assert code == "provider_unavailable"
    assert "network" in message.lower()


@pytest.mark.parametrize(
    "text",
    [
        E70_NETWORK,
        # 反面夹具 ①：网络原文里的 `4013` 之类数字不得被当成 401。
        "fetch failed: connect ECONNREFUSED 127.0.0.1:4013 (gateway port 4013)",
        # 反面夹具 ②：人造的"max tokens 401 is not a limit"不得判 auth。
        "max tokens 401 is not a limit",
    ],
)
def test_these_texts_must_never_be_judged_as_auth(text: str) -> None:
    assert looks_like_auth(text) is False


# ── E-68：坏 provider 的码要在映射表里 ──────────────────────────────────────


def test_config_error_code_maps_to_usage_error(tmp_path: Path) -> None:
    code, _ = classify_failure(
        observed(code="config", message='Provider "deepseek" has no adapter'), 2, tmp_path / "e.log"
    )
    assert code == "usage_error"
    assert CLI_ERROR_CODE_MAP["config"] == "usage_error"


# ── AC-98 / O-19：E-68 形状（isError:false + stopReason:timeout + exit 3）────


def test_timeout_without_an_error_object_is_never_a_schema_problem(tmp_path: Path) -> None:
    """真机形状：`isError:false` + `stopReason:"timeout"` + `exitCode:3` + `retries=3` + **无 error 对象**。

    旧代码会返回 `result_schema_unsatisfied`（"模型没能提交合规报告"），而真实原因是
    **网络不通 + CLI 自己重试了 3 次 + 撞上 2 分钟时长预算**。两者的用户动作完全相反。
    """
    code, message = classify_failure(
        observed(code=None, stop_reason="timeout", retries=3), 3, tmp_path / "e.log"
    )
    assert code == "provider_unavailable"
    assert "3" in message and "timeout" in message.lower()


# ── AC-99：不回归（end_turn + retries==0）──────────────────────────────────


def test_end_turn_without_retries_stays_result_schema_unsatisfied(tmp_path: Path) -> None:
    """`end_turn` 正是**真机成功侧**的取值（E-78 / E-86）——所以它绝不能被当成"未知错误"。"""
    code, _ = classify_failure(observed(code=None, stop_reason="end_turn", retries=0), 1, tmp_path / "e.log")
    assert code == "result_schema_unsatisfied"


# ── AC-108：规则表必须穷尽（30 种组合）──────────────────────────────────────


def test_the_rule_table_is_exhaustive_over_thirty_combinations(tmp_path: Path) -> None:
    """★v10（V10-P1-2）：v9 的表在 `end_turn + retries==0`（正是 AC-99 的夹具）与
    `timeout + retries==0` 两处留了洞。这条断言比"再补一条规则"更值钱——
    下一个 `stopReason` 值出现时它先红。
    """
    codes: list[str | None] = ["stream_error", "result_schema_unsatisfied", "totally_unknown_code"]
    stop_reasons: list[str | None] = ["timeout", "max_turns", "end_turn", "aborted", None]
    retries_values = [0, 3]
    seen = 0
    for cli_code, stop_reason, retries in itertools.product(codes, stop_reasons, retries_values):
        payload = observed(
            code=cli_code,
            message=E70_NETWORK if cli_code == "stream_error" else "",
            stop_reason=stop_reason,
            retries=retries,
        )
        code, message = classify_failure(payload, 1, tmp_path / "e.log")
        assert isinstance(code, str) and code, f"组合 {cli_code}/{stop_reason}/{retries} 返回了空码"
        assert code in RUN_ERROR_CODES, f"{code} 不在 RUN_ERROR_CODES 里（前端无处查 hint）"
        assert isinstance(message, str)
        seen += 1
    assert seen == 3 * 5 * 2 == 30


def test_no_result_line_still_classifies(tmp_path: Path) -> None:
    payload = observed(with_result=False, retries=0, stop_reason=None)
    payload["retries"] = 3
    code, _ = classify_failure(payload, 1, tmp_path / "e.log")
    assert code == "provider_unavailable"


def test_stop_reason_map_is_data_not_branches() -> None:
    assert STOP_REASON_MAP == {
        "timeout": "provider_unavailable",
        "error": "agent_error",
        "max_turns": "budget_exceeded",
        "aborted": "cancelled",
        "end_turn": "",
    }


# ── 打码 ────────────────────────────────────────────────────────────────────


def test_mask_secrets_masks_whole_keys_but_keeps_the_four_digit_tail() -> None:
    """E-80：上游回显的是密钥**后 4 位**（`****O5p6`），4 位不足以冒用 ⇒ 放过它。

    去追那 4 位会把 request-id 之类的正常文本一起打掉。
    """
    text = f"Your api key: {SECRET_FAKE} is invalid; ref ****O5p6"
    masked = mask_secrets(text)
    assert SECRET_FAKE not in masked
    assert "****O5p6" in masked


SECRET_FAKE = "sk-d0bf07896171422e9a896f5b8ac96d0f"


def test_auth_text_in_the_message_is_masked_before_it_leaves(tmp_path: Path) -> None:
    code, message = classify_failure(
        observed(code="stream_error", message=f"Authentication failed for {SECRET_FAKE}"), 1, tmp_path / "e.log"
    )
    assert code == "provider_auth"
    assert SECRET_FAKE not in message
