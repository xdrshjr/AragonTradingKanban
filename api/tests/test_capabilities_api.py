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
    # ★v12（S1/S7 / §22.5.1）：新增顶层 `runtime`（进程级事实：能不能 spawn / 硬截止 /
    # 会话策略 / 会话目录体积）。放在顶层而不是塞进 `aragon`：`aragon` 回答"引擎是谁、
    # 装没装对"，`runtime` 回答"**这个进程**现在能做什么"，两者可以互相矛盾
    # （引擎完全正常，但 `--reload` 起的这个进程 spawn 不了子进程）。
    assert set(body) == {
        "aragon",
        "agentMode",
        "dataProviders",
        "features",
        "limits",
        "engine",
        "runtime",
        # ★v15（§24.3.9(g)）：顶层追加 `redacted`——只加不改，其余七个键一个字没动。
        "redacted",
    }
    # ★v12（AC-112）：`loopOk` 必须真的在 wire 上——pydantic 会静默丢弃没声明的键，
    # 这是同一个坑的第四次（v6 engine、v7 quality.cost、v9 aragon.verified）。
    assert set(body["runtime"]) == {
        "eventLoop",
        "loopOk",
        "loopReason",
        "hardDeadlineSec",
        "sessionSave",
        "sessionDirMb",
    }
    assert body["runtime"]["loopOk"] is True
    assert body["runtime"]["sessionSave"] in ("retry_only", "off")
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
        # ★v14（U1 / §23.5.4）：**实测**判据（`0006_report_library` 已应用 + `doc_sha1` 可查）。
        # 这条断言是**精确相等**的 18 键字典，所以下一次再加开关时必须同步这里——
        # 那正是"响应模型是第二道门"的机器化版本（V10-P1-1 的同型守卫）。
        "reportLibrary": True,
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


def test_schema_bytes_and_sha1_come_from_the_real_file(capability_client) -> None:
    """AC-129（★v12 / V11-P2-6）：`/capabilities` 里任何声称 schema 字节的地方都必须是**真源**。

    为什么要盯这么一个数字：上一轮的 spec 把 `19,295 B` 写进了记录，而那是
    `read_text` + `write_text` 的 **CRLF 副本**；真源是 **18,392 B / sha1 `7a93509904b4`**。
    一个字节数漂移意味着「预算闸门在量一份不存在的文件」，而闸门又是
    「这份报告能不能被生成」的判据。

    三条断言，一条比一条强：
    1. 两个字段存在（它们不在的话，界面上那一行就会是空的）；
    2. `schemaSha1` 与 `report_validator.schema_sha1()` **相等**（同一份实现，不是两份副本）；
    3. 数值就是今天的真源：**18,392 B**、sha1 前缀 `7a93509904b4`。schema 真的变了而
       这条没跟着改，它就会红——那正是我们要的：“口径变了必须说出来”。
    """
    from app.services import report_validator

    body = capability_client()
    engine = body["engine"]
    schema = engine["schema"]
    assert schema is not None, "schema 度量不应降级为 null（真源文件在仓库里）"
    assert engine["schemaSha1"] == report_validator.schema_sha1()

    source = (get_settings().schemas_dir / "report_doc.schema.json").read_bytes()
    assert schema["bytes"] == len(source), f"应报 {len(source)} B，实报 {schema['bytes']} B"
    # 真源的今天（E-118）：不是「一个大致合理的数」，而是一份可重算的快照。
    assert len(source) == 18_392
    assert report_validator.schema_sha1() == "7a93509904b4"


def test_aragon_payload_carries_the_loop_facts(capability_client) -> None:
    """★评审修正（E-114）：`aragon.loopOk` / `loopReason` 必须真的到 wire 上。

    这是同一型事故的**第五次**（v6 的 `engine`、v7 的 `quality.cost`、v9 的
    `aragon.verified`、v11 的 `loopOk` 半次）：`preflight.as_payload()` 里加了键，
    但 `schemas/capabilities.py::AragonAragonOut` 没声明 ⇒ pydantic **静默丢弃**。

    实测（本机 `/capabilities`，修之前）：

        {"available": true, "degradedReason": null, "keySource": "env",
         "cli": null, "verified": false, "loopOk": null, "loopReason": null}

    `loopOk` 根本不在响应里。后果不是"难看"：`EndpointBadge` 的第四态
    （`loop_blocked`）判据是 `aragon.loopOk === false`，所以在一个
    `--reload` 起的进程上，徽标会显示"正常"而每一次生成都被拒（R-54 的缓解失效）。
    """
    body = capability_client()
    aragon = body["aragon"]
    assert "loopOk" in aragon, "aragon.loopOk 被响应模型丢掉了（as_payload 里有、schema 里没有）"
    assert "loopReason" in aragon
    assert aragon["loopOk"] is True  # 这条用例跑在 Proactor 上（Windows）或非 win32 恒真
    assert aragon["loopReason"] is None


def test_the_sync_route_judges_the_serving_loop_not_the_policy(monkeypatch) -> None:
    """★评审修正（E-114）：同步路由问"能不能 spawn"时，答案来自**本进程的 serve loop**。

    为什么这条必须有：`uvicorn` 换的是 **loop factory**，不是 asyncio 策略。
    `uvicorn/loops/asyncio.py::asyncio_loop_factory(use_subprocess=True)` 直接返回
    `asyncio.SelectorEventLoop`，全程没有 `set_event_loop_policy()`。而 `/capabilities`
    是同步路由（跑在 threadpool 里，**没有** running loop），旧实现此时"看策略"⇒
    默认策略是 `WindowsProactorEventLoopPolicy` ⇒ 报 `loopOk=True`。

    于是在一个**注定每一次生成都失败**的进程上，`runtime.loopOk` 与
    `aragon.degradedReason` 双双说"没事"（AC-112 的等价关系成立，但两个都错）。
    修法：`lifespan` 启动时用 `running_loop_name()` 把 serve loop 记下来，
    `spawn_capability()` 在没有 running loop 时先看它。

    这条用例用**假 loop** 断言那个优先级：记下一个"不能 spawn"的 loop 之后，
    即使 asyncio 策略是 Proactor，判据也必须说 `False`。
    """
    from app.agent import preflight

    reset_cache()
    monkeypatch.setattr(preflight.sys, "platform", "win32")

    # ① 先记下一个 Selector 形态的 serve loop（等价于 `--reload` 起的那个进程）。
    class _WindowsSelectorEventLoop:
        pass

    monkeypatch.setattr(preflight.asyncio, "get_running_loop", lambda: _WindowsSelectorEventLoop())
    monkeypatch.setattr(preflight, "is_proactor_loop", lambda loop: type(loop).__name__ == "ProactorEventLoop")
    assert preflight.running_loop_name() == "_WindowsSelectorEventLoop"

    # ② 现在站在同步路由的位置上：没有 running loop，而策略"看起来"是好的。
    def _no_loop() -> object:
        raise RuntimeError("no running event loop")

    class WindowsProactorEventLoopPolicy:
        pass

    monkeypatch.setattr(preflight.asyncio, "get_running_loop", _no_loop)
    monkeypatch.setattr(preflight.asyncio, "get_event_loop_policy", lambda: WindowsProactorEventLoopPolicy())

    capable, reason = preflight.spawn_capability()
    assert capable is False, "同步路由不能只看策略——uvicorn 换的是工厂，不是策略"
    assert reason == "event_loop_selector:_WindowsSelectorEventLoop"

    # ③ 真值仍是 Proactor 时，同一个调用点必须说"可以"（不能矫枉过正）。
    reset_cache()

    class ProactorEventLoop:
        pass

    monkeypatch.setattr(preflight.asyncio, "get_running_loop", lambda: ProactorEventLoop())
    assert preflight.running_loop_name() == "ProactorEventLoop"
    monkeypatch.setattr(preflight.asyncio, "get_running_loop", _no_loop)
    assert preflight.spawn_capability() == (True, None)
    reset_cache()

