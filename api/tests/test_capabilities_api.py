"""`GET /capabilities` under the three degraded CLI shapes (★P1-6).

The endpoint-level counterpart of `test_preflight.py`: the front end decides
whether to show the "示例模式" banner from exactly this payload, so its shape and
its `agentMode` verdict are part of the contract.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.agent.preflight import reset_cache
from app.config import get_settings

FAKE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_aragon.py"


@pytest.fixture()
def capability_client(client, monkeypatch, fake_aragon_bin):
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    monkeypatch.setattr(settings, "agent_mode", "auto", raising=False)
    monkeypatch.setattr(
        settings,
        "aragon_env_passthrough",
        "FAKE_ARAGON_INFO",
        raising=False,
    )

    def configure(info: str | None = None):
        if info:
            monkeypatch.setenv("FAKE_ARAGON_INFO", info)
        else:
            monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
        reset_cache()
        return client.get("/api/v1/capabilities").json()

    yield configure
    monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
    reset_cache()


def test_capable_cli_reports_real_mode(capability_client) -> None:
    body = capability_client()
    assert body["agentMode"] == "real"
    assert body["aragon"]["available"] is True
    assert body["aragon"]["hasSubmitResult"] is True
    assert body["aragon"]["canStructured"] is True
    assert body["aragon"]["degradedReason"] is None


def test_missing_submit_result_switches_to_mock_with_a_reason(capability_client) -> None:
    """The P0-1 shape: a CLI that cannot submit must never be trusted."""
    body = capability_client("no_submit")
    assert body["agentMode"] == "mock"
    assert body["aragon"]["hasSubmitResult"] is False
    assert body["aragon"]["degradedReason"] == "submit_result_missing"
    assert body["aragon"]["errors"], "the reason must be explainable, not just a boolean"


def test_missing_api_key_switches_to_mock(capability_client, monkeypatch) -> None:
    """★v9（V10-P0-1 / I-12 的同型陷阱）：**必须显式清空** `aragon_api_key`。

    开发机的 `.env` 里恰好有 `ARAGON_API_KEY` 时，这条断言会**误红**（keySource == "env"
    ⇒ real），而它想证明的是"CLI 说没钥匙、我们也没有"这一种情形。不清空的断言
    证明不了任何事。
    """
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "aragon_api_key", SecretStr(""), raising=False)
    body = capability_client("no_key")
    assert body["agentMode"] == "mock"
    assert body["aragon"]["degradedReason"] == "no_api_key"
    assert body["aragon"]["keySource"] == "none"
    assert body["aragon"]["keyPresent"] is False


def test_missing_cli_switches_to_mock(capability_client) -> None:
    body = capability_client("missing")
    assert body["agentMode"] == "mock"
    assert body["aragon"]["available"] is False
    assert body["aragon"]["degradedReason"] == "aragon_unavailable"


def test_capabilities_shape_is_stable(capability_client) -> None:
    body = capability_client()
    # ★v6：新增顶层 `engine` 对象（V6-P1-3：它是**新增**的，不是给既有对象加字段）。
    assert set(body) == {"aragon", "agentMode", "dataProviders", "features", "limits", "engine"}
    assert {"available", "version", "versionOk", "provider", "model", "ajv"} <= set(body["aragon"])
    # ★v7（V8-P1-2）：`baseUrl` 归 `aragon`（"引擎是谁"），不归 `engine`（"指纹与预算"）。
    assert "baseUrl" in body["aragon"]
    # ★v7：`engine` = v6 的四项 + 六项新事实，且**仍然**没有 model/provider（H-19）。
    assert set(body["engine"]) == {
        "fingerprint",
        "drift",
        "schemaBytes",
        "allowTools",
        "posture",
        "schemaSha1",
        "schema",
        "limits",
        "priceVersion",
        "lastFailure",
        # ★v9（K2）：最近一次自检的摘要（`{ok, ms, at, code}`）。model/provider 仍然
        # 不进 `engine` —— 唯一真源在 `aragon`（H-19）。
        "lastProbe",
    }
    assert "model" not in body["engine"] and "provider" not in body["engine"]
    assert body["engine"]["posture"] in ("lean", "full")
    assert isinstance(body["engine"]["drift"], bool), "drift 是布尔：三态由 comparability.same 表达"
    # AC-70：深度余量 = 1 这件事必须能在 /capabilities 上读到。
    assert body["engine"]["schema"]["depth"] == 11
    assert body["engine"]["schema"]["headroom"] == 1
    # ★v9（K4）：CLI 层重试默认 6 → **0**（实测 E-69：第二层重试不认 `--retry-max`，
    # 一次坏端点吃掉 2m03s）。重试权收回 runner（`agentAttempts`）。
    assert body["engine"]["limits"]["retryMax"] == 0
    assert body["engine"]["limits"]["agentAttempts"] >= 1
    # >0 时才会出现这条告警（此刻是 0 ⇒ 干净）。
    assert body["engine"]["limits"]["retryLayersWarning"] is None
    assert body["engine"]["limits"]["repairTurns"] == 2
    assert body["engine"]["priceVersion"]
    assert body["engine"]["lastFailure"] is None, "这个库里还没有失败记录"
    # ★V4-P2-4: `ask` is no longer a 501 placeholder, so the flags are the v4 set —
    # and every one of them is a measurement, which is what the next test pins down.
    assert body["features"] == {
        "mock": True,
        "exportHtml": True,
        "exportMd": True,
        "pdf": False,
        "ask": True,
        "askDeep": True,
        "fullTextSearch": True,
        "pinyinSearch": False,
        # The suite runs with SCHEDULER_ENABLED=0 (the ticker is driven explicitly),
        # so this flag proves the payload follows the setting rather than a constant.
        "schedule": False,
        "offline": True,
        "realtimeQuote": False,
        "semanticSearch": False,
        # ★v6 五个新布尔（G1–G4），全部由实测推导，默认 False。
        "grounding": True,
        "rangeQuery": True,
        "readingMode": True,
        "costLedger": True,
        "engineFingerprint": True,
    }
    assert body["limits"]["askDailyLimit"] == 30
    # ★v6：面板与徽标需要的四个数（`maxBlockRangeDays` 只用于 UI 文案）。
    assert body["limits"]["minFactClaims"] == 5
    assert body["limits"]["maxBlockRangeYears"] == 5
    assert body["limits"]["monthlyBudgetUsd"] == 0, "默认不设限，产品不能因默认值把用户锁在门外"


def test_feature_flags_follow_measurements(capability_client, monkeypatch) -> None:
    """The flags must be measured, not assumed: no FTS5 ⇒ `fullTextSearch=false`."""
    from app.services import search_index

    monkeypatch.setattr(search_index, "fts_ready", lambda *_args, **_kwargs: False)
    body = capability_client()
    assert body["features"]["fullTextSearch"] is False
    # `cite` still works — it never touches the index or the CLI.
    assert body["features"]["ask"] is True


def test_data_providers_are_listed_with_availability(capability_client) -> None:
    body = capability_client()
    providers = {entry["id"]: entry["available"] for entry in body["dataProviders"]}
    assert "synthetic" in providers
    assert providers["synthetic"] is True


def test_fake_cli_info_contract() -> None:
    """The fake must stay shaped like the real 0.6.68 payload it stands in for."""
    completed = subprocess.run(
        [sys.executable, str(FAKE_SCRIPT), "info", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    import json

    info = json.loads(completed.stdout)
    assert {"cli", "core", "provider", "model", "hasApiKey", "tools", "features", "result"} <= set(info)
    assert {"ajv", "schemaBytes", "schemaDepth", "schemaRefs", "validationNodes", "maxRepairTurns"} <= set(
        info["result"]
    )

def test_limits_expose_the_prompt_budget(capability_client) -> None:
    """★v8 评审：§20.5.1 要求顶层 `limits` 增 `maxPromptBytes`，而此前没有任何断言。

    它不是一个装饰字段：界面用它解释 `prompt_over_budget`（"上限是多少、我超了多少"），
    漏了它那条 hint 就只能说"超了"。
    """
    body = capability_client()
    assert body["limits"]["maxPromptBytes"] == get_settings().aragon_max_prompt_bytes
    assert body["limits"]["maxPromptBytes"] > 0

