"""契约夹具的双向一致（AC-104）+ `quality` 反射守卫（C-30）。

两个断言，各守一次真实事故：

1. **码表漂移**：`contracts/run_error_codes.json` 与 `failure.RUN_ERROR_CODES` 必须
   **集合相等**（18 条，不是子集）、`severity` / `retryable` 逐值相等。这套码表历史上
   已漂移两次（v7 的 V8-P1-3、`run_conflict` 静默），而漂移的表现永远是"后端发了、
   前端不认识"。
2. **响应模型漏字段**：`quality` 是字典、`ReportQuality` 是类——后者漏一个键，前者
   写得再对也到不了前端。v7 漏过 `cost`/`prompt`，v9 要加 `salvage`，所以这里加一条
   **反射**断言：`ValidationOutcome.quality` 产出的每个键都必须在 `ReportQuality` 里有
   同名字段。下一次再加键时它先红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.agent.failure import RUN_ERROR_CODES
from app.schemas.capabilities import (
    AragonAragonOut,
    CapabilitiesResponse,
    EngineCapabilityOut,
    EngineLimitsOut,
    RuntimeCapabilityOut,
)
from app.schemas.report import ReportQuality
from app.services.report_validator import SalvageReport, ValidationOutcome

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "run_error_codes.json"


def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_contract_fixture_exists_and_is_versioned() -> None:
    payload = contract()
    assert payload["version"] >= 1
    assert payload["codes"] and payload["httpCodes"] and payload["sseEvents"]


def test_run_error_codes_and_the_contract_are_the_same_set() -> None:
    """**集合相等**，不是子集。少一条 = 前端有一个码没有动作；多一条 = 契约落后。"""
    payload = contract()
    assert set(RUN_ERROR_CODES) == {row["code"] for row in payload["codes"]}
    # ★v12（§22.5.3）：18 → 21，三个追加码
    # （event_loop_unsupported / agent_timeout / output_truncated）。
    # 既有 18 条的 severity / retryable 一个字都没改（下面那条逐值断言守着）。
    assert len(payload["codes"]) == 21, "v12 的码表是 21 条（18 + 3 追加）"


def test_severity_and_retryable_match_value_by_value() -> None:
    payload = contract()
    for row in payload["codes"]:
        spec = RUN_ERROR_CODES[row["code"]]
        assert spec.severity == row["severity"], f"{row['code']} 的 severity 漂移了"
        assert bool(spec.retryable) is bool(row["retryable"]), f"{row['code']} 的 retryable 漂移了"


def test_http_codes_never_leak_into_the_run_code_set() -> None:
    """`monthly_budget_exceeded` 是 402 HTTP 码，**不在** `RUN_ERROR_CODES` 里。

    把它混进 run 码表会让"集合相等"那条断言永远红，也会让前端把"本月花超了"渲染成
    "这次生成超时"（V6-P0-2 的同一个坑）。
    """
    payload = contract()
    run_codes = set(RUN_ERROR_CODES)
    for row in payload["httpCodes"]:
        assert row["code"] not in run_codes, f"{row['code']} 不该出现在 RUN_ERROR_CODES 里"
        assert 400 <= int(row["status"]) <= 599


def test_every_code_row_carries_an_action_from_the_shared_vocabulary() -> None:
    """★v14（§23.6.4 / 评审 R-3）：`action` 是**每一行**的必填字段，不是可选的补充说明。

    两件事各守一次真实事故：

    1. **缺 `action`** ⇒ `web/lib/runErrors.ts::actionFor()` 回落到 `openLog`，界面上那个码
       就变成"看日志"——一个"看起来有动作"的空按钮（V11-P0-4 的同型事故：契约里少一个字段，
       前端不会报错，只会静默少提示一件事）。
    2. **动作不在词汇表里** ⇒ TS 侧逐行比对只会说"不一致"，而根因在这里多了一个没人认识的词。
       词汇表的真源是 `RunErrorAction` 那个联合类型（6 个取值），两边必须一模一样。

    `codes`（run 级）与 `httpCodes`（HTTP 级）都断言，因为 v14 的三条新码加在**后者**里，
    而"只有前面那一组检查 action"正是这次差点漏掉的那种不对齐。
    """
    payload = contract()
    vocabulary = {"retry", "openSettings", "checkKey", "wait", "openLog", "none"}
    # 词汇表本身也要和 TS 侧同源：少一个取值，这里就会在下一行"合法动作"断言里红。
    ts_source = (CONTRACT.parents[1] / "web" / "lib" / "runErrors.ts").read_text(encoding="utf-8")
    declared = re.search(r"export type RunErrorAction = ([^;]+);", ts_source)
    assert declared is not None, "runErrors.ts 里找不到 RunErrorAction 联合类型"
    assert set(re.findall(r"'([a-zA-Z]+)'", declared.group(1))) == vocabulary

    for group in ("codes", "httpCodes"):
        for row in payload[group]:
            assert row.get("action"), f"{group} 里的 {row['code']} 缺 action"
            assert row["action"] in vocabulary, f"{row['code']} 的 action={row['action']} 不在词汇表里"

    # ★v14（§23.5.5）：三条新码在 `httpCodes` 里、各带一个动作、且**不进** run 码表。
    by_code = {row["code"]: row for row in payload["httpCodes"]}
    for code, status in (("invalid_cursor", 422), ("report_not_deleted", 409), ("base_report_mismatch", 422)):
        assert code in by_code, f"契约里缺 v14 的 {code}"
        assert int(by_code[code]["status"]) == status
        assert by_code[code]["action"] == "retry"
    assert payload["version"] >= 3, "追加三条 httpCodes 之后契约版本必须 ≥ 3"


def test_sse_events_match_the_frontend_subscription_list() -> None:
    """服务端发出的事件名 ⊆ 前端订阅数组（`web/lib/sse.ts` 的手写清单）。"""
    payload = contract()
    sse_source = (CONTRACT.parents[1] / "web" / "lib" / "sse.ts").read_text(encoding="utf-8")
    for event in payload["sseEvents"]:
        assert f"'{event}'" in sse_source, f"sse.ts 的订阅清单里没有 {event}"


# ── C-30：`quality` 的反射守卫 ─────────────────────────────────────────────


def test_every_quality_key_has_a_response_field() -> None:
    """★v10（V10-P2-7）：`quality` 是字典，响应模型是类——后者漏一个键，前者写得再对
    也到不了前端（v7 的 `cost`/`prompt` 就这样丢了整整两个版本）。"""
    outcome = ValidationOutcome(
        doc={"sections": []},
        grounding=None,
        engine={"fingerprint": "abc"},
        cost={"source": "unknown"},
        prompt={"version": 7, "sha1": "abc", "bytes": 1},
        salvage=SalvageReport(used=True, droppedKeys=1),
    )
    produced = set(outcome.quality)
    declared = set(ReportQuality.model_fields)
    assert produced <= declared, f"quality 有键没有响应字段：{produced - declared}"


def test_the_new_capabilities_fields_reach_the_wire_model() -> None:
    """V10-P1-1：五个新字段与 `engine.limits` 的两个新数必须**在响应模型上声明**。

    `routers/capabilities.py` 传的是 `capability.as_payload()` 并带
    `# type: ignore[arg-type]` —— pydantic 会静默丢弃没声明的键。这是同一个坑的第三次。
    """
    aragon_fields = set(AragonAragonOut.model_fields)
    assert {"baseUrlHost", "keyPresent", "keySource", "verified", "verifiedAt"} <= aragon_fields
    limits_fields = set(EngineLimitsOut.model_fields)
    assert {"agentAttempts", "probeTtlSec", "retryLayersWarning"} <= limits_fields
    engine_fields = set(EngineCapabilityOut.model_fields)
    assert "lastProbe" in engine_fields
    # 顶层形状（AC-104 的既有断言在 test_capabilities_api 里守着）。
    # ★v12（S1/S7）：`runtime` 是**新增**的第七个顶层对象，既有六个一个字没改。
    assert set(CapabilitiesResponse.model_fields) == {
        "aragon",
        "agentMode",
        "dataProviders",
        "features",
        "limits",
        "engine",
        "runtime",
    }
    runtime_fields = set(RuntimeCapabilityOut.model_fields)
    assert {"eventLoop", "loopOk", "loopReason", "hardDeadlineSec", "sessionSave", "sessionDirMb"} <= runtime_fields


def test_the_three_new_codes_keep_their_hints_in_failure_py() -> None:
    """★v12（C-36 / C-39）：新码的 hint 只能写在 `FailureSpec`，且三处字符串逐字相同。

    三个同步点是 `failure.RUN_ERROR_CODES`（hint 真源）、`contracts/run_error_codes.json`
    （跨语言契约）与 `web/lib/runErrors.ts`（动作表）。hint 写在 `errors.py` 等于写进一个
    没人读的地方——前端拿到的 `hint` 会永远是 `null`（V11-P0-4）。
    """
    payload = contract()
    codes = {row["code"] for row in payload["codes"]}
    for code in ("event_loop_unsupported", "agent_timeout", "output_truncated"):
        assert code in codes and code in RUN_ERROR_CODES
        assert RUN_ERROR_CODES[code].hint, f"{code} 缺 hint"
    # errors.py 里可以出现**码**（`EventLoopUnsupported.code`，这是本仓既有的错误类形态，
    # 见 `RunConflict` / `MonthlyBudgetExceeded`），但**不许**出现 hint 文本——那会让
    # "要不要去掉 --reload" 这句话存在两个地方（V11-P0-4 / C-39 的原意）。
    errors_source = (CONTRACT.parents[1] / "api" / "app" / "errors.py").read_text(encoding="utf-8")
    for fragment in ("去掉 --reload 后重启", "调大 ARAGON_MAX_DURATION_MS", "ARAGON_MAX_OUTPUT_TOKENS 提到"):
        assert fragment not in errors_source, f"hint 的真源是 failure.FailureSpec，不是 errors.py（{fragment}）"
    assert "event_loop_unsupported" not in errors_source or "class EventLoopUnsupported" in errors_source
    # TS 侧三个码逐字存在。
    ts_source = (CONTRACT.parents[1] / "web" / "lib" / "runErrors.ts").read_text(encoding="utf-8")
    for code in ("event_loop_unsupported", "agent_timeout", "output_truncated", "stream_closed"):
        assert f"{code}:" in ts_source, f"runErrors.ts 缺 {code}"
