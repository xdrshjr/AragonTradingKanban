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
