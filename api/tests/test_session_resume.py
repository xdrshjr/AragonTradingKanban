"""S7 · 重试不重付与会话卫生（AC-126 / AC-127 / AC-133 / C-40 / R-61）。

E-100/E-101 的两条实测：单次生成的 input token 是 **68,974–181,421**，而
`sanitize_session_id()` 是**死代码**（`build_argv()` 从不发 `--session-id`，`:234` 永远加
`--no-save-session`）⇒ attempt 2 是**从零重跑**，重试一次就是双倍钱。

三条判据：

* **AC-126**：`AGENT_ATTEMPTS=2` 且 attempt 1 产生过 pid ⇒ attempt 2 的 argv 含 `--resume`、
  **不含** `--prompt-file`；无会话时回落并记 `resumed: false`；
* **AC-127**：清理命令必须是 `aragon sessions rm <id> --yes`（缺 `--yes` 时 CLI 回
  exit 2 `Refusing to delete without --yes.`），**不存在**任何全局 `prune`；
* **AC-133**：`runtime.sessionSave` 由 `agent_attempts` **派生**，设置里没有
  `agent_session_save` 这个键。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from app.agent import aragon_runner, preflight
from app.agent.runner_base import AgentRequest
from app.config import get_settings


def _capability() -> preflight.AragonCapability:
    return preflight.AragonCapability(
        available=True,
        versionOk=True,
        canStructured=True,
        hasSubmitResult=True,
        hasApiKey=True,
        resolvedBinary="fake-aragon",
    )


def _request(tmp_path: Path) -> AgentRequest:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("研究茅台", encoding="utf-8")
    system_prompt = tmp_path / "system.md"
    system_prompt.write_text("你是研究员。", encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    return AgentRequest(
        run_id="r" + "0" * 31,
        workspace=tmp_path,
        prompt_path=prompt,
        system_prompt_path=system_prompt,
        schema_path=schema,
        schema_source_path=schema,
        instrument_symbol="600519.SH",
        instrument_name="贵州茅台",
    )


# ── AC-126：resume 的 argv 形态 ─────────────────────────────────────────────


def test_two_attempts_save_the_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AGENT_ATTEMPTS > 1` **且调用点认领清理** ⇒ attempt 1 带 `--session-id`、不带
    `--no-save-session`。"""
    monkeypatch.setattr(get_settings(), "agent_attempts", 2, raising=False)
    request = _request(tmp_path)
    request.keep_session = True
    argv = aragon_runner.build_argv(request, _capability(), "fake-aragon")
    assert "--session-id" in argv
    assert "--no-save-session" not in argv, "要重试就必须保存会话，否则第二次从零重跑"
    session_id = argv[argv.index("--session-id") + 1]
    assert session_id == aragon_runner.sanitize_session_id("r" + "0" * 31)


def test_one_attempt_behaves_exactly_like_v8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AGENT_ATTEMPTS=1` ⇒ 行为与 v8 一个字都没改（`--no-save-session`，无 `--session-id`）。"""
    monkeypatch.setattr(get_settings(), "agent_attempts", 1, raising=False)
    request = _request(tmp_path)
    request.keep_session = True
    argv = aragon_runner.build_argv(request, _capability(), "fake-aragon")
    assert "--no-save-session" in argv
    assert "--session-id" not in argv


def test_only_the_call_site_that_cleans_up_may_save_a_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★评审修正（评审节点 I-131）：会话落了盘就必须有人删。

    `AGENT_ATTEMPTS=2`（仓库默认）时，上一版对**所有**调用点都发 `--session-id`——
    包括 `ask_runner`（报告内追问）与 `probe`（引擎自检）。那两条路径**不在**
    `run_manager._cleanup_session()` 的 finally 里，于是每问一次就留一个永不回收的文件，
    而会话目录今天已经 1545.71 MB（E-101）——正是 S7 要治的病（R-61）。

    判据：只有 `keep_session=True`（= 会清理的那个调用点）才保存；默认 `False` 时
    argv 与 v11 逐字相同。
    """
    monkeypatch.setattr(get_settings(), "agent_attempts", 2, raising=False)
    ask_like = _request(tmp_path)  # `keep_session` 默认 False，等价于 ask / probe
    assert ask_like.keep_session is False
    argv = aragon_runner.build_argv(ask_like, _capability(), "fake-aragon")
    assert "--no-save-session" in argv, "不负责清理的调用点不许让 CLI 落盘会话"
    assert "--session-id" not in argv

    # 而 run_manager 的那一份（`keep_session=True`）仍然会保存——上面两条用例守着。
    source = Path(aragon_runner.__file__).resolve().parents[1] / "services" / "run_manager.py"
    assert "keep_session=True" in source.read_text(encoding="utf-8")


def test_resume_drops_the_prompt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-126 的核心：`--resume` 时**不再喂一遍 prompt**（那等于把上下文重付一次）。"""
    monkeypatch.setattr(get_settings(), "agent_attempts", 2, raising=False)
    request = _request(tmp_path)
    request.resume_session = aragon_runner.sanitize_session_id(request.run_id)
    argv = aragon_runner.build_argv(request, _capability(), "fake-aragon")
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == request.resume_session
    assert "--prompt-file" not in argv
    assert "--session-id" not in argv, "resume 与 session-id 是互斥的两种起步方式"


def test_session_save_is_derived_not_configured() -> None:
    """AC-133 / C-45：`sessionSave` 由 `agent_attempts` **派生**，不是配置项。"""
    from app.routers.capabilities import _runtime_payload

    settings = get_settings()
    original = settings.agent_attempts
    try:
        settings.agent_attempts = 2
        assert _runtime_payload(settings).sessionSave == "retry_only"
        settings.agent_attempts = 1
        assert _runtime_payload(settings).sessionSave == "off"
    finally:
        settings.agent_attempts = original
    assert "agent_session_save" not in set(settings.model_fields), (
        "第二个会话真源（V10-P1-6 的 keySource 教训）"
    )


# ── AC-127：会话清理 ────────────────────────────────────────────────────────


def _record_cli(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, ...]],
    *,
    returncode: int = 0,
    stderr: bytes = b"",
):
    async def _fake(*args, **kwargs):
        calls.append(tuple(str(arg) for arg in args))

        class _Proc:
            returncode = None

            async def communicate(self):
                self.returncode = returncode
                return b"", stderr

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


def test_cleanup_uses_rm_with_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-127：命令是 `sessions rm <id> --yes`（实测缺 `--yes` 时 CLI 回 exit 2）。"""
    calls: list[tuple[str, ...]] = []
    _record_cli(monkeypatch, calls)
    ok, detail = asyncio.run(aragon_runner.cleanup_session("r" + "0" * 31, binary="fake-aragon"))
    assert ok is True and detail == "removed"
    assert calls and calls[0][1:3] == ("sessions", "rm")
    assert "--yes" in calls[0], "缺 --yes 会恒失败（E-108）"
    assert calls[0][-1] == "--yes"


def test_cleanup_never_prunes_globally(monkeypatch: pytest.MonkeyPatch) -> None:
    """C-40 / R-61：**不存在**任何全局 `prune` 调用。

    实测 `aragon sessions prune --older-than 7 --dry-run` 会列出
    `smoke-threads-tenant-a/b`、`webuse-17d194d2-…` 这些**别的项目**的会话 9 条，
    而目录今天已经 1545.71 MB 早过阈值 ⇒ 按 v11 的写法第一次 tick 就会删别人的东西。
    """
    calls: list[tuple[str, ...]] = []
    _record_cli(monkeypatch, calls)
    asyncio.run(aragon_runner.cleanup_session("r" + "0" * 31, binary="fake-aragon"))
    for call in calls:
        assert "prune" not in call, f"出现了全局 prune：{call}"
    source = Path(aragon_runner.__file__).read_text(encoding="utf-8")
    # 代码里不许出现可执行的 prune 子命令（注释里解释"为什么不用"是允许的）。
    # ★评审修正（评审节点 I-129）：这里原来还有一句 `assert ... or True` —— 恒真，
    # 删掉。真判据是下面这个逐行扫描。
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", '"""', "*", "|", ">")) or not stripped:
            continue
        assert '"prune"' not in stripped and "'prune'" not in stripped, line


def test_cleanup_failure_is_visible_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """静默失败正是这一节要消灭的东西（S-12）：失败必须能被落成一条 warn。"""
    calls: list[tuple[str, ...]] = []
    _record_cli(monkeypatch, calls, returncode=2, stderr=b"Refusing to delete without --yes.")
    ok, detail = asyncio.run(aragon_runner.cleanup_session("r" + "0" * 31, binary="fake-aragon"))
    assert ok is False
    assert "Refusing" in detail or "exit 2" in detail


def test_no_binary_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 模式下没有 CLI，也就没有会话——那不是失败，不该落 warn。"""
    # `cleanup_session` 用的是 `aragon_runner.check_aragon` 这个名字（不是 preflight.）。
    monkeypatch.setattr(aragon_runner, "check_aragon", lambda *a, **k: preflight.AragonCapability())
    ok, detail = asyncio.run(aragon_runner.cleanup_session("r" + "0" * 31))
    assert (ok, detail) == (False, "no_binary")


def test_a_missing_session_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """★实施发现（I-117）：CLI 对"会话不存在"的回答是 `session_not_found`。

    单次尝试（`AGENT_ATTEMPTS=1`，走 `--no-save-session`）与 mock 模式都没有会话文件。
    把它当失败会在**每一次正常运行**的末尾落一条 `session_cleanup_failed` 警告——
    一条永远为真的警告等于没有警告（S-12 的另一半）。
    """
    calls: list[tuple[str, ...]] = []
    _record_cli(
        monkeypatch,
        calls,
        returncode=1,
        stderr=b'session_not_found: no session "r0000".',
    )
    ok, detail = asyncio.run(aragon_runner.cleanup_session("r" + "0" * 31, binary="fake-aragon"))
    assert ok is True
    assert detail == "absent"


def test_cleanup_is_wired_into_the_run_finally_block() -> None:
    """run 终止后一定要清理**本 run 自己的**会话，且失败要落 `session_cleanup_failed`。"""
    from app.services import run_manager as run_manager_module

    source = Path(run_manager_module.__file__).read_text(encoding="utf-8")
    assert "_cleanup_session(run_id)" in source
    assert '"session_cleanup_failed"' in source


# ── `resumed` 必须进证据链 ─────────────────────────────────────────────────


def test_retry_frame_carries_resumed_and_session_id() -> None:
    """§22.5.2：`run.retry` 新增 `resumed` / `sessionId`。

    没有它，"重试一次多花一倍钱"这件事在证据链里看不见——而这正是本轮要消掉的浪费。
    """
    source = Path(aragon_runner.__file__).read_text(encoding="utf-8")
    assert '"resumed":' in source
    assert '"sessionId":' in source


def _stub_engine() -> preflight.AragonCapability:
    """一份"引擎完全正常"的能力快照：本文件只测会话事实，不测预检。"""
    return preflight.AragonCapability(
        available=True,
        versionOk=True,
        canStructured=True,
        hasSubmitResult=True,
        hasApiKey=True,
        resolvedBinary="fake-aragon",
    )


def _fake_stream(seen: list[AgentRequest], outcomes: list[aragon_runner.AgentResult]):
    """把 `_stream_once` 换成"每次尝试按剧本返回一个结果"，并记下每次的 `AgentRequest`。

    为什么不跑真引擎（评审节点 I-126）：本用例要断言的正是**重试前**那次判定
    （`can_resume`）与 `engine.json` 里的事实，而真实 `_stream_once` 会 spawn 子进程
    —— 那既慢，又会把"会话文件在不在"变成环境问题。
    """

    async def _stream_once(attempt_request: AgentRequest, on_event) -> object:  # noqa: ANN001
        seen.append(attempt_request)
        yield aragon_runner.AgentEvent(
            aragon_runner.AgentEventType.log, {"__result": outcomes[len(seen) - 1]}
        )

    return _stream_once


def _drive(monkeypatch: pytest.MonkeyPatch, workspace: Path, outcomes, *, attempts: int):
    """跑一次 `stream()`，返回 `(frames, seen, engine.json 的 session 段)`。"""
    workspace.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_attempts", attempts, raising=False)
    monkeypatch.setattr(aragon_runner, "check_aragon", lambda *a, **k: _stub_engine())

    request = _request(workspace)
    runner = aragon_runner.AragonRunner()
    # 等价于"第一次真的起过进程"：真实路径里这两个事实由 `_stream_once()` 在 spawn 之后写，
    # 这里换了 `_stream_once`，所以必须自己放上（否则 `can_resume` 永远为假）。
    runner._attempt1_spawned = True
    runner._session_id = aragon_runner.sanitize_session_id(request.session_id or request.run_id)

    seen: list[AgentRequest] = []
    monkeypatch.setattr(runner, "_stream_once", _fake_stream(seen, list(outcomes)))

    async def go():
        return [event async for event in runner.stream(request)]

    frames = asyncio.run(go())
    engine = json.loads((workspace / "engine.json").read_text(encoding="utf-8"))
    return frames, seen, engine["session"]


def _result(code: str, stop_reason: str | None = None) -> aragon_runner.AgentResult:
    return aragon_runner.AgentResult(error_code=code, error_message=code, stop_reason=stop_reason)


def _retry_frames(frames) -> list[dict]:
    return [event.payload for event in frames if event.type == aragon_runner.AgentEventType.retry]


def test_resumed_truth_has_exactly_one_decision_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """★评审修正（评审节点 I-126）：`resumed` 只有**一个**判定点，帧与 `engine.json` 共用。

    上一版在 `run.retry` 帧里与 `engine.json` 里各算了一遍"是否 resume"，两处都会说假话：

    * `AGENT_ATTEMPTS=3` 的**第三次**尝试：帧里 `resumed: true`，而准入条件只有
      `attempt == 2` 成立 ⇒ 那一次其实是从零重跑，于是"重试不重付"被记账成"已经做到"；
    * attempt 2 真的 `--resume` 了、但最终 `stop_reason=length` 收场：`engine.json`
      记成 `resumed: false / reason: truncated`，把"我们接过上下文"这件事抹掉。

    三条路径一起钉住：真的 resume / 第 3 次不 resume / 截断时不 resume。
    """
    session_id = aragon_runner.sanitize_session_id("r" + "0" * 31)

    # ① 第 2 次真的 resume 了，而且最终就是截断——事实必须是"接过上下文"。
    frames, seen, session = _drive(
        monkeypatch,
        tmp_path / "a",
        [_result("result_missing"), _result("output_truncated", "length")],
        attempts=2,
    )
    assert seen[0].resume_session is None, "第一次尝试不能用 --resume"
    assert seen[1].resume_session == session_id
    retries = _retry_frames(frames)
    assert retries[0]["resumed"] is True
    assert retries[0]["sessionId"] == session_id
    assert session == {"id": session_id, "resumed": True}

    # ② 第三次尝试没有会话可接（只有第 2 次会 resume）：帧里必须是 false，而且
    #    `engine.json` 不能说 `no_session`（会话当时是在的）。
    frames, seen, session = _drive(
        monkeypatch,
        tmp_path / "b",
        [_result("result_missing"), _result("result_missing"), _result("result_missing")],
        attempts=3,
    )
    retries = _retry_frames(frames)
    assert [frame["resumed"] for frame in retries] == [True, False]
    assert seen[2].resume_session is None
    assert retries[1]["sessionId"] is None
    assert session == {"id": session_id, "resumed": True}

    # ③ 第一次就被 max-token 截断：按 R-60 **不** resume（要新的输出预算），
    #    而 `engine.json` 必须给出原因，不许静默。
    #    错误码仍取 `result_missing`（runner 层的重试白名单只有那两条码），
    #    这里要测的是 `stop_reason=length` 对 resume 准入的影响，不是重试权。
    frames, seen, session = _drive(
        monkeypatch,
        tmp_path / "c",
        [_result("result_missing", "length"), _result("result_missing", "length")],
        attempts=2,
    )
    assert seen[1].resume_session is None
    retries = _retry_frames(frames)
    assert retries[0]["resumed"] is False
    assert retries[0]["sessionId"] is None
    assert session == {"id": session_id, "resumed": False, "reason": "truncated"}


def test_engine_json_records_the_session_fact(tmp_path: Path) -> None:
    """`engine.json.session = {id, resumed, reason?}`；`resumed: false` 必须带 `reason`。"""
    aragon_runner.note_session(tmp_path, session_id="abc", resumed=False, reason="no_session")
    payload = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert payload["session"] == {"id": "abc", "resumed": False, "reason": "no_session"}
    aragon_runner.note_session(tmp_path, session_id="abc", resumed=True)
    payload = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    assert payload["session"]["resumed"] is True
    assert "reason" not in payload["session"]


@pytest.mark.skipif(sys.platform != "win32", reason="sanitize_session_id 的行为是跨平台的，这里只做一次覆盖")
def test_session_id_is_sanitized() -> None:
    """`sanitize_session_id()` 不是死代码：它现在真的被 argv 用到（AC-126 的前提）。"""
    assert aragon_runner.sanitize_session_id("a/b\\c d") == "a-b-c-d"
    source = Path(aragon_runner.__file__).read_text(encoding="utf-8")
    assert 'argv += ["--session-id", session_id]' in source
