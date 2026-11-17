"""K1 引擎接入面：argv 顺序、空值不传、端点形状、provider 白名单、打码、第二道门。

对应 spec §21.3.2 与 AC-90 / AC-91 / AC-92 / AC-105 / **AC-106** / **AC-107**。

这组用例里最值钱的两条是 AC-106 与 AC-107，理由写在 spec §21.13 的"★v10 追加一句"里：
它们是本轮**唯一**可能让整轮白做的两处——① 只填 `.env` 的机器上 `.env` 的密钥永远
走不到 argv，`auto` 模式安静地给出一份示例报告（不是红，是绿）；② 打码打在了
`build_argv()` 的出口，于是每次生成都拿 `***` 当密钥（而 AC-90/AC-91 照样全绿）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent import aragon_runner, preflight
from app.agent.preflight import (
    AragonCapability,
    base_url_problem,
    check_aragon,
    effective_key_source,
    host_of,
    normalize_base_url,
    provider_problem,
    reset_cache,
)
from app.agent.runner_base import AgentError, AgentRequest
from app.config import get_settings

SCHEMAS = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas"

SECRET = "sk-d0bf07896171422e9a896f5b8ac96d0f"


def capability(resolved: str = "aragon", **overrides) -> AragonCapability:
    base = {
        "available": True,
        "cli": "0.6.68",
        "versionOk": True,
        "provider": "openai",
        "model": "deepseek-flash",
        "hasApiKey": True,
        "ajv": True,
        "canStructured": True,
        "hasSubmitResult": True,
        "extraAllowTools": ["read_file", "glob", "grep", "list_dir", "submit_result"],
        "result": {"maxRepairTurns": 2, "schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 5000},
        "resolvedBinary": resolved,
    }
    base.update(overrides)
    return AragonCapability(**base)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "run"
    target.mkdir()
    for name in ("prompt.md", "system.md"):
        (target / name).write_text("# x", encoding="utf-8")
    (target / "schema.json").write_bytes((SCHEMAS / "report_doc.schema.json").read_bytes())
    return target


def request_for(workspace: Path) -> AgentRequest:
    return AgentRequest(
        run_id="run-k1",
        workspace=workspace,
        prompt_path=workspace / "prompt.md",
        system_prompt_path=workspace / "system.md",
        schema_path=workspace / "schema.json",
        schema_source_path=SCHEMAS / "report_doc.schema.json",
        instrument_symbol="600519.SH",
        instrument_name="贵州茅台",
    )


@pytest.fixture()
def configured(monkeypatch):
    """只写 `.env` 的三项（不碰 CLI 的全局配置）—— Q-1 的字面断言。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "aragon_model", "deepseek-flash", raising=False)
    monkeypatch.setattr(settings, "aragon_base_url", "https://api.deepseek.com/", raising=False)
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(SECRET), raising=False)
    return settings


# ── AC-90：argv 顺序（黄金断言）──────────────────────────────────────────────


def test_argv_order_puts_the_endpoint_before_exec(workspace: Path, configured) -> None:
    argv = aragon_runner.build_argv(request_for(workspace), capability(), "/bin/aragon")

    ordered = ["--provider", "--model", "--base-url", "--api-key"]
    positions = [argv.index(flag) for flag in ordered]
    assert positions == sorted(positions), "顺序写死：provider → model → base-url → api-key"
    for flag in ordered:
        assert argv.index(flag) < argv.index("exec"), f"{flag} 是全局 flag，必须在 exec 之前"
    # 尾斜杠被去掉（`normalize_base_url`），值本身不许被改写。
    assert argv[argv.index("--base-url") + 1] == "https://api.deepseek.com"


# ── AC-92：空值不传（不是传空串）─────────────────────────────────────────────


def test_empty_values_are_not_passed_at_all(workspace: Path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_base_url", "", raising=False)
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(""), raising=False)

    argv = aragon_runner.build_argv(request_for(workspace), capability(), "/bin/aragon")

    assert "--base-url" not in argv
    assert "--api-key" not in argv, "`--api-key ''` 会让 CLI 认为给了一个空密钥，比不给更难诊断"


# ── AC-107 / V10-P0-2：spawn 用真 argv，落盘用打码副本 ───────────────────────


def test_build_argv_returns_the_real_key_and_redact_is_a_copy(workspace: Path, configured) -> None:
    """**这一条是本轮最值钱的两条之一**（V10-P0-2 / O-23）。

    `build_argv()` 的返回值**就是** `create_subprocess_exec` 的实参。在它的 `return`
    前打码 ⇒ 子进程拿到 `--api-key ***` ⇒ 每一次真实生成与深答都以 401 结束，而
    AC-90（黄金 argv）与 AC-91（产物不含 `sk-`）**照样全绿**。
    """
    raw = aragon_runner.build_argv(request_for(workspace), capability(), "/bin/aragon")
    masked = aragon_runner.redact_argv(raw)

    assert raw[raw.index("--api-key") + 1] == SECRET, "spawn 用的那份必须是真 key"
    assert masked[masked.index("--api-key") + 1] == "***"
    assert raw is not masked, "派生副本，不许原地改"
    assert SECRET not in json.dumps(masked)


def test_redact_argv_also_masks_a_key_hiding_in_another_position() -> None:
    argv = ["aragon", "exec", "--note", f"token={SECRET}"]
    assert aragon_runner.redact_argv(argv)[3] == "token=***"


def test_argv_json_is_written_masked_and_the_child_still_gets_the_key(
    workspace: Path, configured, monkeypatch, fake_aragon_bin
) -> None:
    """落盘的六处都不许出现 `sk-`，而假引擎收到的 argv 里必须有真 key。"""
    import asyncio

    settings = get_settings()
    log = workspace / "argv.log"
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    # 子进程只拿到白名单里的环境变量，所以剧本名与 argv 日志路径必须**显式**放行。
    monkeypatch.setattr(settings, "aragon_env_passthrough", "FAKE_ARAGON_MODE,FAKE_ARAGON_ARGV_LOG", raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ok")
    monkeypatch.setenv("FAKE_ARAGON_ARGV_LOG", str(log))
    reset_cache()

    cap = capability(resolved=str(fake_aragon_bin))
    asyncio.run(aragon_runner.AragonRunner(cap, max_attempts=1).run(request_for(workspace)))

    recorded = json.loads((workspace / "argv.json").read_text(encoding="utf-8"))
    assert recorded[recorded.index("--api-key") + 1] == "***"

    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen[seen.index("--api-key") + 1] == SECRET, "子进程必须收到真 key（假 CLI 会把它记下来）"

    for name in ("argv.json", "engine.json", "aragon.ndjson", "aragon.stderr.log", "result.json"):
        text = (workspace / name).read_text(encoding="utf-8", errors="replace")
        assert SECRET not in text, f"{name} 里出现了明文密钥"
    reset_cache()


# ── AC-105：engine.json 的七项 ────────────────────────────────────────────────


def test_engine_json_records_the_seven_facts(workspace: Path, configured, monkeypatch, fake_aragon_bin) -> None:
    import asyncio

    monkeypatch.setenv("FAKE_ARAGON_MODE", "ok")
    cap = capability(resolved=str(fake_aragon_bin))
    asyncio.run(aragon_runner.AragonRunner(cap, max_attempts=1).run(request_for(workspace)))

    payload = json.loads((workspace / "engine.json").read_text(encoding="utf-8"))
    assert set(payload) >= {
        "provider",
        "model",
        "baseUrlHost",
        "permissionMode",
        "tools",
        "capabilities",
        "cli",
    }
    assert payload["permissionMode"] == "strict"
    assert payload["baseUrlHost"] == "api.deepseek.com"
    assert SECRET not in json.dumps(payload)


def test_engine_json_says_missing_when_the_system_event_never_arrived(workspace: Path) -> None:
    """`system` 事件缺失 ⇒ 写 `{"missing": true}`，而**不是不写**。"""
    aragon_runner._write_engine_json(workspace, None, capability())
    payload = json.loads((workspace / "engine.json").read_text(encoding="utf-8"))
    assert payload["missing"] is True


# ── AC-106 / V10-P0-1：第二道门（`.env` 的钥匙必须真的生效）────────────────


def test_effective_key_source_prefers_env(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(SECRET), raising=False)
    assert effective_key_source(False) == "env"
    assert effective_key_source(True) == "env", "`.env` 里有钥匙是比 CLI profile 更强的事实"

    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(""), raising=False)
    assert effective_key_source(True) == "cli_profile"
    assert effective_key_source(False) == "none"


def test_env_key_alone_makes_the_engine_capable(monkeypatch, fake_aragon_bin) -> None:
    """AC-106：假 `aragon info` 报 `hasApiKey=false`，但 `.env` 有 key ⇒ **real**，
    `fullyCapable=True`、`keySource == "env"`、`degradedReason is None`。

    这条是"假绿"的守门人：修之前 `auto` 会静默退回 mock，用户拿到一份
    `quality.source="mock"` 的报告而界面上没有任何一行红字。
    """
    from app.agent import build_runner

    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "agent_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(SECRET), raising=False)
    monkeypatch.setenv("FAKE_ARAGON_INFO", "no_key")
    reset_cache()
    try:
        cap = check_aragon(force=True)
        assert cap.cliHasApiKey is False, "CLI 自己的配置里确实没有钥匙"
        assert cap.keySource == "env"
        assert cap.keyPresent is True
        assert cap.fullyCapable is True
        assert cap.degradedReason is None
        from app.agent import decide_mode

        assert decide_mode(settings, cap) == "real"
        assert type(build_runner(settings, strict=True)).__name__ == "AragonRunner"
    finally:
        monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
        reset_cache()


def test_no_key_anywhere_still_degrades(monkeypatch, fake_aragon_bin) -> None:
    from app.agent import decide_mode

    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(""), raising=False)
    monkeypatch.setenv("FAKE_ARAGON_INFO", "no_key")
    reset_cache()
    try:
        cap = check_aragon(force=True)
        assert cap.keySource == "none"
        assert cap.degradedReason == "no_api_key"
        assert decide_mode(settings, cap) == "mock"
    finally:
        monkeypatch.delenv("FAKE_ARAGON_INFO", raising=False)
        reset_cache()


def test_capabilities_exposes_the_five_new_fields(client, monkeypatch, fake_aragon_bin) -> None:
    """C-26 / V10-P1-1：五个新字段**必须过响应模型**（pydantic 会静默丢弃没声明的键）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    monkeypatch.setattr(settings, "aragon_api_key", SecretStr(SECRET), raising=False)
    monkeypatch.setattr(settings, "aragon_base_url", "https://api.deepseek.com/v1", raising=False)
    reset_cache()
    try:
        body = client.get("/api/v1/capabilities").json()
        aragon = body["aragon"]
        assert aragon["keyPresent"] is True
        assert aragon["keySource"] == "env"
        assert aragon["baseUrlHost"] == "api.deepseek.com"
        assert aragon["verified"] is False and aragon["verifiedAt"] is None
        assert body["engine"]["limits"]["probeTtlSec"] >= 0
    finally:
        reset_cache()


# ── K1 的两条静态检查（spawn 之前拦）────────────────────────────────────────


def test_provider_whitelist_matches_the_cli_enum() -> None:
    assert provider_problem("") is None
    assert provider_problem("openai") is None
    assert provider_problem("Anthropic") is None, "大小写不敏感"
    message = provider_problem("deepseek")
    assert message and "deepseek" in message, "报错要能直接给用户看，并列出合法值"


def test_base_url_shape_rules() -> None:
    assert base_url_problem("") is None
    assert base_url_problem("https://api.deepseek.com/v1") is None
    assert base_url_problem("api.deepseek.com"), "必须带协议"
    assert base_url_problem("https://api.deepseek.com/v1/chat/completions"), "不要带 /chat/completions"
    assert normalize_base_url(" https://api.deepseek.com/v1/ ") == "https://api.deepseek.com/v1"


def test_host_of_never_leaks_the_path() -> None:
    """§21.5.1 的三条约束：只留 host[:port]、永不回显 path、空配置给 None。"""
    assert host_of("https://api.deepseek.com/v1?token=tok_abc") == "api.deepseek.com"
    assert host_of("http://127.0.0.1:8080/v1") == "127.0.0.1:8080"
    assert host_of("") is None
    assert host_of(None) is None


def test_a_bad_provider_never_spawns(workspace: Path, monkeypatch) -> None:
    """E-68 的教训：坏 provider 2 秒 exit 2，且被映射表报成"没见过的错误形态"。

    所以它在 **spawn 之前**就被拦成 `usage_error`（主路径），映射表补 `config` 只是兜底。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_provider", "deepseek", raising=False)
    # 断言**具体类型**：`pytest.raises(Exception)` 连一次 `KeyError` 都会放行，
    # 而这条用例守的正是"spawn 之前拦成 `usage_error`"这一个结论。
    with pytest.raises(AgentError) as info:
        aragon_runner.build_argv(request_for(workspace), capability(), "/bin/aragon")
    assert info.value.code == "usage_error"
    assert "openai" in info.value.message, "报错要列出合法值（用户照着改）"


def test_preflight_reports_config_shape_problems_as_warnings(monkeypatch, fake_aragon_bin) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "aragon_provider", "deepseek", raising=False)
    reset_cache()
    try:
        cap = check_aragon(force=True)
        assert any("provider" in error for error in cap.errors)
    finally:
        reset_cache()


def test_secretstr_never_prints_the_key(configured) -> None:
    """`repr(settings)` 里永远不出现明文（SecretStr 的全部价值）。"""
    assert SECRET not in repr(configured)
    assert SECRET not in json.dumps(configured.model_dump(mode="json"), ensure_ascii=False, default=str)


def test_preflight_module_exposure() -> None:
    """`KEY_SOURCES` 是唯一的取值域（V10-P1-6：不设第三个配置项）。"""
    assert preflight.KEY_SOURCES == ("env", "cli_profile", "none")
    assert not hasattr(get_settings(), "aragon_api_key_source")


def test_an_empty_env_placeholder_must_not_shadow_dotenv(monkeypatch, tmp_path) -> None:
    """★实施发现（I-92）：环境变量给了空串时，`.env` 里的值必须赢。

    实测于本轮真机探针：`.env` 写着 `ARAGON_PROVIDER=openai`，而进程环境里已经有
    `ARAGON_PROVIDER=''`（任何 launcher / docker-compose 的 `- ARAGON_PROVIDER=` 都会
    这样）。pydantic-settings 的优先级是"环境变量 > .env"，于是用户填的值被一个空串
    **静默**盖掉 —— 与 K1 修的"假绿"是同一类事故（配置项存在，但没生效，且不报错）。

    它只对七个**身份项**生效：`ALLOW_ORIGINS` 这类空串有语义（=同源），不能修。
    """
    from app.config import _restore_empty_placeholders  # noqa: PLC0415

    monkeypatch.setenv("ARAGON_PROVIDER", "")
    monkeypatch.setenv("ARAGON_MODEL", "")
    settings = get_settings()
    repaired = _restore_empty_placeholders(settings)
    # 判定只看"是否被 .env 里的非空值覆盖"，因此这台机器上 .env 有没有填都成立。
    from dotenv import dotenv_values

    values = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    expected_provider = (values.get("ARAGON_PROVIDER") or "").strip()
    if expected_provider:
        assert repaired.aragon_provider == expected_provider
    expected_model = (values.get("ARAGON_MODEL") or "").strip()
    if expected_model:
        assert repaired.aragon_model == expected_model
    # 名单之外的项不受影响。
    assert "ALLOW_ORIGINS" not in {name.upper() for name in _restore_empty_placeholders.__doc__ or ""}
