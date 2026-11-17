"""密钥零落盘（spec §20.0.2 红线 7 / AC-84）。

本轮拿到了一次真实凭证，最危险的不是"用错它"，而是"它出现在某个审计文件里"：
`argv.json`、`run_events` 的日志行、`result.json`、报告 JSON、`/capabilities` 的响应体，
都是会被导出、被贴进工单、被留在磁盘上两周的东西。

所以这条测试扫**产物**而不扫源码：把一次（假 CLI 驱动的）生成跑完，然后遍历整个
workspace 与 capabilities 响应，断言没有任何 `sk-` 前缀的字符串。

判据刻意写得像攻击者：`sk-` 后面跟 20 个以上字符，或任何形如 `Bearer xxx` 的长 token。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agent.aragon_runner import ENV_ALLOWLIST
from app.agent.preflight import reset_cache
from app.config import get_settings

#: `sk-…`（OpenAI / DeepSeek 的形态）、`Bearer …`、以及 40 位以上的裸十六进制。
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\b[a-f0-9]{40,}\b"),
)

#: 这些键名一旦非空，值就不许出现在产物里。
SECRET_ENV_NAMES = ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")

FAKE_SCRIPT = Path(__file__).parent / "fixtures" / "fake_aragon.py"


def _scan(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - 目录/权限
        return []
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            hits.append(f"{path.name}: {match.group(0)[:12]}…")
    return hits


def test_the_allow_list_is_the_only_door_for_keys() -> None:
    """红线 7 的另一半：只有白名单里的环境变量会交给子进程（不是 `os.environ`）。"""
    assert "OPENAI_API_KEY" in ENV_ALLOWLIST
    assert "DEEPSEEK_API_KEY" in ENV_ALLOWLIST
    assert "ARAGON_HOME" in ENV_ALLOWLIST
    # 不该顺手把整台机器的秘密交给一个会被报告生成读取的子进程。
    for forbidden in ("AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "SUPABASE_SERVICE_KEY"):
        assert forbidden not in ENV_ALLOWLIST


def test_no_secret_in_run_artifacts(tmp_path: Path, monkeypatch, fake_aragon_bin) -> None:
    """AC-84：一次完整生成的每份审计材料里都不许出现 `sk-` 字符串。"""
    import asyncio

    from app.agent.aragon_runner import AragonRunner
    from app.agent.preflight import AragonCapability
    from app.agent.runner_base import AgentRequest

    workspace = tmp_path / "run"
    workspace.mkdir()
    schema = workspace / "schema.json"
    real_schema = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas" / "report_doc.schema.json"
    schema.write_bytes(real_schema.read_bytes())
    prompt = workspace / "prompt.md"
    prompt.write_text("# prompt", encoding="utf-8")
    system = workspace / "system.md"
    system.write_text("# system", encoding="utf-8")

    # 一个假 Key 放在子进程能看见的位置：它**可以**进环境变量，但绝不许进任何文件。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0123456789abcdefghij")
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "aragon_env_passthrough", "", raising=False)
    monkeypatch.setenv("FAKE_ARAGON_MODE", "ok")
    reset_cache()

    try:
        import os

        previous = os.environ.get("FAKE_ARAGON_MODE")
        os.environ["FAKE_ARAGON_MODE"] = "ok"
        capability = AragonCapability(
            available=True,
            cli="0.6.68",
            versionOk=True,
            provider="openai",
            model="deepseek-flash",
            hasApiKey=True,
            ajv=True,
            canStructured=True,
            hasSubmitResult=True,
            extraAllowTools=["read_file", "glob", "grep", "list_dir", "submit_result"],
            tools=["read_file", "glob", "grep", "list_dir", "submit_result"],
            result={"schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 5000, "maxRepairTurns": 2},
            resolvedBinary=str(fake_aragon_bin),
        )
        request = AgentRequest(
            run_id="secret-scan",
            workspace=workspace,
            prompt_path=prompt,
            system_prompt_path=system,
            schema_path=schema,
            schema_source_path=schema,
            instrument_symbol="600519.SH",
            instrument_name="贵州茅台",
        )
        result = asyncio.run(AragonRunner(capability).run(request))
    finally:
        if previous is None:
            os.environ.pop("FAKE_ARAGON_MODE", None)
        else:
            os.environ["FAKE_ARAGON_MODE"] = previous
        reset_cache()

    assert result.doc is not None, "这条测试的前提是这一跑真的成功"

    artifacts = [path for path in workspace.rglob("*") if path.is_file()]
    assert artifacts, "工作区里必须有审计材料（argv.json / ndjson / stderr / result.json）"
    names = {path.name for path in artifacts}
    assert {"argv.json", "aragon.ndjson", "aragon.stderr.log", "result.json"} <= names

    hits: list[str] = []
    for path in artifacts:
        hits.extend(_scan(path))
    assert hits == [], f"审计材料里出现了密钥形态的字符串：{hits}"


def test_capabilities_payload_carries_no_key(client, monkeypatch, fake_aragon_bin) -> None:
    """`/capabilities` 也是审计材料：它会被前端缓存、被贴进工单。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-abcdefghijklmnopqrstuvwxyz")
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_bin", str(fake_aragon_bin), raising=False)
    monkeypatch.setattr(settings, "force_preflight", True, raising=False)
    reset_cache()
    try:
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200
        blob = json.dumps(response.json(), ensure_ascii=False)
        for pattern in SECRET_PATTERNS:
            assert pattern.search(blob) is None, "capabilities 响应里出现了密钥形态的字符串"
        assert "sk-" not in blob
    finally:
        reset_cache()


@pytest.mark.parametrize("name", SECRET_ENV_NAMES)
def test_env_names_are_documented_not_stored(name: str) -> None:
    """`.env.example` 只能出现**名字**，不能出现值（红线 7 的文档侧）。"""
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        assert pattern.search(example) is None, f".env.example 里有密钥形态的字符串（{name} 相关）"
