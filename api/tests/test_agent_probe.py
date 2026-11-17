"""K2 引擎自检：三态判定、单飞、TTL、身份变更、互斥、落库（AC-93 / AC-94 / AC-95 / AC-96）。

**全程不联网**：剧本由 `tests/fixtures/fake_aragon.py` 的 `probe_*` 三个模式驱动，
形状抄自真机的 E-86（一次工具调用 + 一次结构化提交，10 秒级、11k+215 tokens）。

这组用例守的是 §21.0.1 的 Q-2「证得了」以及 V10-P2-11 的那条互斥：探针与真实生成
必须互斥，否则单 worker 下两者各烧各的 token，而且探针的 `system` 事件会把"这个端点
生成了那份报告"的实证链搅浑。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent import probe as probe_module
from app.agent.preflight import reset_cache
from app.config import get_settings
from app.models import EngineProbe
from app.services import agent_probe


@pytest.fixture()
def fake_engine(monkeypatch, fake_aragon_bin):
    """把预检与探针都指向假引擎（`AGENT_MODE=auto` + 显式 key ⇒ real）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    monkeypatch.setattr(settings, "agent_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr("sk-test-0123456789abcdefghij"), raising=False)
    # 假引擎的剧本名必须显式放行（子进程只拿白名单里的环境变量）。
    monkeypatch.setattr(settings, "aragon_env_passthrough", "FAKE_ARAGON_MODE", raising=False)
    monkeypatch.setattr(settings, "aragon_base_url", "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(settings, "agent_probe_enabled", True, raising=False)
    monkeypatch.setattr(settings, "workspace_root", str(Path(settings.workspace_dir)), raising=False)
    agent_probe.reset_state()
    reset_cache()

    def configure(mode: str):
        monkeypatch.setenv("FAKE_ARAGON_MODE", mode)
        reset_cache()
        agent_probe.reset_state()

    yield configure
    monkeypatch.delenv("FAKE_ARAGON_MODE", raising=False)
    agent_probe.reset_state()
    reset_cache()


def test_probe_success_writes_one_row_and_turns_the_panel_green(session, fake_engine) -> None:
    """AC-94：`engine_probes` 一条 `ok=1`；`verified=true` 且 `verifiedAt` 非空（经 HTTP 断言）。"""
    fake_engine("probe_ok")
    outcome = asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))

    assert outcome.ok is True
    assert outcome.code is None
    rows = session.query(EngineProbe).all()
    assert len(rows) == 1
    assert rows[0].ok == 1
    assert rows[0].tokens_in == 11251 and rows[0].tokens_out == 215
    assert rows[0].model == "deepseek-flash"
    assert rows[0].base_url_host == "api.deepseek.com"
    assert rows[0].reason == "manual"

    ok, verified_at, stale = agent_probe.verified(session)
    assert ok is True and verified_at and stale is None


def test_probe_hits_the_http_surface(client, session, fake_engine) -> None:
    fake_engine("probe_ok")
    response = client.post("/api/v1/agent/probe", json={"force": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["tokensIn"] == 11251
    assert body["engine"]["baseUrlHost"] == "api.deepseek.com"

    listing = client.get("/api/v1/agent/probe").json()
    assert listing["verified"] is True and listing["verifiedAt"]
    assert listing["last"]["ok"] is True
    assert listing["probeTtlSec"] >= 0
    assert listing["probeCostHintUsd"] is not None

    capabilities = client.get("/api/v1/capabilities").json()
    assert capabilities["aragon"]["verified"] is True
    assert capabilities["aragon"]["verifiedAt"]
    assert capabilities["engine"]["lastProbe"]["ok"] is True


def test_probe_401_returns_provider_auth_and_is_not_retryable(session, fake_engine) -> None:
    """AC-95：401 文本 ⇒ `provider_auth`（**不是** `provider_unavailable`），且落库 `ok=0`。"""
    from app.agent.failure import is_retryable

    fake_engine("probe_auth")
    outcome = asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))

    assert outcome.ok is False
    assert outcome.code == "provider_auth"
    assert is_retryable(outcome.code) is False
    assert agent_probe.verified(session)[2] == "failed"
    # 上游回显的后 4 位可以留（E-80），但完整密钥形态一个都不许出现。
    assert "sk-" not in (outcome.message or "")


def test_probe_echo_mismatch_is_its_own_code(session, fake_engine) -> None:
    """`probe_echo_mismatch` 的含义是"端点通了但回的不是我们教它回的东西"。"""
    fake_engine("probe_bad_echo")
    outcome = asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))
    assert outcome.ok is False
    assert outcome.code == "probe_echo_mismatch"


def test_probe_never_touches_the_runs_directory(session, fake_engine) -> None:
    """O-21：探针工作区与 `runs/` 分开 —— 报告路径里不会出现探针。

    **真的去数一次 `runs/`**，而不是断言 `probes_dir()` 的字符串里含 "probes" ——
    后者无论探针写到哪里都成立，属于"写完就再也不会红"的那种断言。
    """
    runs_dir = get_settings().runs_dir
    before = sorted(path.name for path in runs_dir.iterdir()) if runs_dir.exists() else []

    fake_engine("probe_ok")
    asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))

    assert agent_probe.probes_dir().exists()
    assert agent_probe.probes_dir().parent == runs_dir.parent, "探针与 runs/ 是兄弟目录"
    after = sorted(path.name for path in runs_dir.iterdir()) if runs_dir.exists() else []
    assert after == before, "探针不许往 runs/ 里写任何东西"


def test_ttl_prevents_a_second_spawn(session, monkeypatch, fake_engine) -> None:
    """AC-93 的前半：已验证且未过期 ⇒ 不重复 spawn（第二次不新增行）。"""
    monkeypatch.setattr(get_settings(), "agent_probe_ttl_sec", 300, raising=False)
    fake_engine("probe_ok")
    asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))
    first = session.query(EngineProbe).count()

    asyncio.run(agent_probe.probe_now(session, reason="manual"))
    assert session.query(EngineProbe).count() == first, "TTL 内不许再花一次 token"

    asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))
    assert session.query(EngineProbe).count() == first + 1, "force=True 必须真的重跑"


def test_expired_probe_is_no_longer_verified(session, monkeypatch, fake_engine) -> None:

    fake_engine("probe_ok")
    asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))
    ok, _verified_at, stale = agent_probe.verified(session, ttl=0)
    assert ok is False and stale == "expired"


def test_identity_change_invalidates_the_previous_green(session, monkeypatch, fake_engine) -> None:
    """AC-93：改 `ARAGON_MODEL` ⇒ `staleReason == "identity_changed"` 且 `verified == false`。

    只看"上次 ok"是不够的：用户改完模型之后，上一条 ok 的记录会撒谎。
    """
    fake_engine("probe_ok")
    asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))
    assert agent_probe.verified(session)[0] is True

    monkeypatch.setattr(get_settings(), "aragon_model", "another-model", raising=False)
    monkeypatch.setattr(
        "app.services.agent_probe.check_aragon",
        lambda *a, **k: _capability_with_model("another-model"),
    )
    ok, _verified_at, stale = agent_probe.verified(session)
    assert ok is False
    assert stale == "identity_changed"


def _capability_with_model(model: str):
    from app.agent.preflight import AragonCapability

    return AragonCapability(
        available=True,
        cli="0.6.68",
        versionOk=True,
        provider="openai",
        model=model,
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=["read_file", "submit_result"],
        result={"maxRepairTurns": 2},
        resolvedBinary="aragon",
    )


def test_two_concurrent_probes_never_spawn_twice(session, fake_engine) -> None:
    """AC-96：单飞 —— 第二个并发请求得 `probe_in_progress`，且只落一行。"""
    fake_engine("probe_ok")

    async def go():
        first = asyncio.create_task(agent_probe.probe_now(session, reason="manual", force=True))
        await asyncio.sleep(0)
        second = await agent_probe.probe_now(session, reason="manual", force=True)
        return await first, second

    first, second = asyncio.run(go())
    assert first.ok is True
    assert second.ok is False
    assert second.code == "probe_in_progress"
    assert session.query(EngineProbe).count() == 1


def test_a_live_generation_blocks_the_probe(client, fake_engine, monkeypatch) -> None:
    """C-29 / V10-P2-11：有活动 run 时**不 spawn**，`POST /agent/probe` 回 409 `probe_busy`。"""
    fake_engine("probe_ok")
    from app.services.run_manager import get_manager

    manager = get_manager()
    monkeypatch.setattr(manager, "has_active_runs", lambda: True, raising=False)

    response = client.post("/api/v1/agent/probe", json={"force": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "probe_busy"


def test_mock_mode_refuses_the_probe(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "agent_mode", "mock", raising=False)
    response = client.post("/api/v1/agent/probe", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_mock_mode"


def test_disabled_switch_is_a_different_code(client, monkeypatch, fake_engine) -> None:
    fake_engine("probe_ok")
    monkeypatch.setattr(get_settings(), "agent_probe_enabled", False, raising=False)
    response = client.post("/api/v1/agent/probe", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "probe_disabled"


def test_probe_has_no_second_spawn_point() -> None:
    """C-29 的机器判据：探针文件里**不许**出现 `create_subprocess_exec`。"""
    source = Path(probe_module.__file__).read_text(encoding="utf-8")
    assert "create_subprocess_exec" not in source
    # 它走的是既有 runner（argv 的唯一 choke point）。
    assert "AragonRunner(" in source


def test_probe_request_uses_the_three_nullable_overrides(tmp_path: Path) -> None:
    """V10-P1-9：探针的参数表通过 `AgentRequest` 落地，不改 `settings`。

    ★实施发现（I-99）：这里必须用 `tmp_path` —— `build_probe_request()` 会**写三份小文件**
    （`probe.txt` / `system.md` / `schema.json`），传 `Path(".")` 会往仓库里写字。
    """
    request = probe_module.build_probe_request(tmp_path / "probe")
    assert request.max_turns == probe_module.PROBE_MAX_TURNS == 3
    assert request.max_duration_ms == probe_module.PROBE_MAX_DURATION_MS == 90_000
    assert request.max_output_tokens == probe_module.PROBE_MAX_OUTPUT_TOKENS == 1_500
    assert request.retry_max == 0
    assert request.repair_turns == 0
    assert request.schema_source_path is None, "探针的 schema 不是报告 schema ⇒ 不做身份断言"
    assert (tmp_path / "probe" / probe_module.PROBE_FILENAME).is_file()


def test_probe_schema_is_tiny_and_closed() -> None:
    assert probe_module.PROBE_SCHEMA["additionalProperties"] is False
    assert probe_module.PROBE_SCHEMA["required"] == ["ok", "echo"]


def test_probe_filename_and_echo_are_separate_constants() -> None:
    """V10-P2-13：文件名与"应读到的内容"分成两个常量。

    v9 用一个常量同时表示两者，而文件名是**字面写进提示词**的——改名只改一处，
    探针就恒报 `probe_echo_mismatch`，而那个码的含义是"网关在改写 JSON"，会把人
    带去查网关。
    """
    assert probe_module.PROBE_FILENAME == "probe.txt"
    assert probe_module.PROBE_ECHO == "PROBE-ECHO-OK"
    assert probe_module.PROBE_FILENAME != probe_module.PROBE_ECHO
    assert probe_module.PROBE_FILENAME in probe_module.PROBE_PROMPT


def test_probe_retention_is_capped(session, fake_engine, monkeypatch) -> None:
    """R-50：`engine_probes` 只追加，但硬上限裁剪（默认 200 行）。"""
    monkeypatch.setattr(get_settings(), "agent_probe_keep", 2, raising=False)
    fake_engine("probe_ok")
    for _ in range(4):
        asyncio.run(agent_probe.probe_now(session, reason="manual", force=True))
    assert session.query(EngineProbe).count() == 2
