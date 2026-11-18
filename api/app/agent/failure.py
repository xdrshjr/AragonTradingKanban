"""Run 级失败分类学 —— 一张表、一个 hint、一个指纹（spec §20.3.4 / §20.5.4，H3 / M20）。

这个模块只放**表与纯函数**：`RUN_ERROR_CODES`（★v12 起 21 条）、`EXIT_MAP`、`STDERR_REFINE`、
`CLI_ERROR_CODE_MAP`、`failure_fingerprint()`。它**不 import 任何 runner**——真正的
分类入口是 `aragon_runner._classify_failure(observed, exit_code, stderr_path)`，因为它
需要看 runner 的 `observed`（`result` 行与 `retry` 观测都在那里）。

三条不可协商的裁决：

* **分类的第一真源是 CLI 的 `error.code`，不是 exit code**（V8-P1-1）。实测（E-42）
  探针 C 的 result 行是 `{"isError": true, "stopReason": "error", "error": {"code":
  "result_schema_unsatisfied"}}`，exit **1**；而 `--retry-max 0` + 坏 base URL 是
  `error.code: network_error`，exit 也是 1。**同一个 exit code 对应两种完全不同的
  失败**，只查 exit code 必然把网络问题报成"模型不肯提交报告"。
* **每个码都有一句人话 hint**（P-2 / AC-76），且由**服务端**下发：只有服务端知道
  exit code 与引擎日志。前端不再拼字符串（`web/lib/copy.ts` 只放标签）。
* **`retryable` 只有一份**。v6 的 `run_manager.py` 里有一个内联元组，这里搬过来
  消灭"这张表在两个地方各写一份"的漂移面（v6 I-33 的同型问题）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

#: §20.5.4 的 severity 取值域。★实施发现（I-59）：§20.6.2 的 `Literal` 只列了五项
#: （`config / transient / model / user / internal`），而 §20.5.4 的分类表里
#: `result_missing` / `budget_exceeded` / `agent_error` 三条写的是 **`engine`**。
#: 两处文档不一致时选**信息更多**的那个：`engine` 是有意义的区分（"引擎自己决定停下"
#: 与"我们内部炸了"不是一回事），把它压成 `internal` 会丢掉这个事实。
Severity = Literal["config", "transient", "model", "user", "internal", "engine"]


@dataclass(frozen=True, slots=True)
class FailureSpec:
    """一条 run 级失败的全部事实。`http_status` 只有同步路径会用到。"""

    code: str
    retryable: bool
    severity: Severity
    hint: str
    http_status: int | None = None


#: 15 条 run 级码（§20.5.4 的完整表）。`hint` 是**中文的下一步动作**，
#: 刻意不提 `exit code` / `stderr` 这类英文原始报错（AC-76 可机器判定）。
RUN_ERROR_CODES: dict[str, FailureSpec] = {
    "aragon_unavailable": FailureSpec(
        code="aragon_unavailable",
        retryable=False,
        severity="config",
        hint="找不到 aragon 可执行文件，或它无法启动。检查 ARAGON_BIN 与 PATH，或先用 AGENT_MODE=mock 演示。",
        http_status=503,
    ),
    "structured_result_unsupported": FailureSpec(
        code="structured_result_unsupported",
        retryable=False,
        severity="config",
        hint="当前 aragon 版本不支持结构化结果（缺 submit_result 或 ajv）。升级 CLI 到 0.6.66 以上，或改用 mock 模式。",
        http_status=503,
    ),
    "schema_over_budget": FailureSpec(
        code="schema_over_budget",
        retryable=False,
        severity="config",
        hint="报告 Schema 超出引擎上限（深度/体积/节点数），这是代码问题：按提示缩小 Schema 后重试，不要再加一层嵌套。",
    ),
    "schema_drift": FailureSpec(
        code="schema_drift",
        retryable=False,
        severity="config",
        hint=(
            "工作区里的 Schema 与真源字节不一致（生成中途被换过文件）。"
            "重新生成一次即可；若复现，检查是否有第二个写入方。"
        ),
    ),
    "prompt_over_budget": FailureSpec(
        code="prompt_over_budget",
        retryable=False,
        severity="config",
        hint="提示词与 Schema 合计体积超出上限。把大块资料（如整份 K 线）移出提示词，或调大 ARAGON_MAX_PROMPT_BYTES。",
    ),
    "result_missing": FailureSpec(
        code="result_missing",
        retryable=True,
        severity="engine",
        hint="引擎正常退出却没有交付任何结果（异常路径）。先看本次工作区里 NDJSON 日志的末尾，再重试一次。",
    ),
    "result_schema_unsatisfied": FailureSpec(
        code="result_schema_unsatisfied",
        retryable=True,
        severity="model",
        hint="模型没能提交合规报告。看本次工作区里 NDJSON 日志中最后一次提交的拒绝原因；也可以换一个更强的模型再试。",
        # ★v8 评审：§20.5.4 第 7 行写的是「500（仅同步）」。同步路径（`build_runner`
        # 之后的首次校验）会用它，run 级路径不看这个字段。漏了它这张表就与文档不符。
        http_status=500,
    ),
    "provider_unavailable": FailureSpec(
        code="provider_unavailable",
        retryable=True,
        severity="transient",
        hint=(
            "模型服务连续不可用（网络错误或对方 429/5xx）。稍等 1–2 分钟再试；"
            "若每次都这样，检查网络/代理与 ARAGON_BASE_URL（第三方端点请填到 /v1 或根地址），"
            "并在面板上点一次「引擎自检」确认端点此刻是通的。"
        ),
    ),
    "provider_auth": FailureSpec(
        code="provider_auth",
        retryable=False,
        severity="config",
        hint=(
            "模型凭证被拒绝或额度耗尽。检查 .env 里的 ARAGON_API_KEY（或 CLI 自己 profile 里的密钥）"
            "是否有效、余额是否充足；改完重启服务，再点一次「引擎自检」。"
        ),
    ),
    "budget_exceeded": FailureSpec(
        code="budget_exceeded",
        retryable=True,
        severity="engine",
        hint="本次生成撞上轮次/时长预算。调大 ARAGON_MAX_TURNS / ARAGON_MAX_DURATION_MS，或换一个更快的模型。",
    ),
    "cancelled": FailureSpec(
        code="cancelled",
        retryable=False,
        severity="user",
        hint="这次生成被你中止了。随时可以点「再次生成」重来，账本会如实记录已经花掉的部分。",
    ),
    "usage_error": FailureSpec(
        code="usage_error",
        retryable=False,
        severity="config",
        hint="命令行参数或本机配置不被引擎接受。看本次工作区里的 argv.json，以及引擎的告警输出。",
    ),
    "agent_error": FailureSpec(
        code="agent_error",
        retryable=True,
        severity="engine",
        hint="引擎返回了一个我们没见过的错误形态。看本次工作区的 NDJSON 日志尾部；重试一次通常能换一条路径。",
    ),
    "internal": FailureSpec(
        code="internal",
        retryable=True,
        severity="internal",
        hint="服务端在编排过程中出错（不是模型的问题）。稍后重试；连续出现请把日志里的 run id 一起反馈。",
        http_status=500,
    ),
    "interrupted": FailureSpec(
        code="interrupted",
        retryable=True,
        severity="internal",
        hint="服务重启导致本次生成中断。重新生成一次即可。",
    ),
    # ── ★v12（§22.5.3）：三个追加码。语义**只追加**，既有 18 条一个字都没改。 ────
    # ★v12 修订（V11-P0-4）：hint 的**唯一真源是这里**（`FailureSpec.hint`，读取入口
    # `hint_for()`）。写进 `errors.py` 等于写进一个没人读的地方——前端拿到的 `hint`
    # 会一直是 `null`，而界面会留一个空洞（C-39：`grep event_loop_unsupported api/app/errors.py` 必须为空）。
    "event_loop_unsupported": FailureSpec(
        code="event_loop_unsupported",
        # 重试没有意义：同一进程里循环类型不会自己变。
        retryable=False,
        severity="config",
        hint=(
            "当前进程的事件循环不支持启动子进程（Windows 上通常是 --reload 造成的）："
            "去掉 --reload 后重启 API，或用 npm run dev 的默认参数重来。"
        ),
        # 同步路径（`RunManager.start()` 第 0 步）会直接抛它 —— 503 是"这台机器现在
        # 做不了这件事"，不是 500"我们写错了"。
        http_status=503,
    ),
    "agent_timeout": FailureSpec(
        code="agent_timeout",
        retryable=True,
        severity="engine",
        hint=(
            "引擎超过硬截止仍未结束。把 ARAGON_MAX_DURATION_MS 调大，"
            "或点『查看日志』看这次到底做到哪一步。"
        ),
    ),
    "output_truncated": FailureSpec(
        code="output_truncated",
        retryable=True,
        severity="engine",
        hint=(
            "这次输出被 max-token 上限截断：把 ARAGON_MAX_OUTPUT_TOKENS 提到 60000，"
            "或把资料里 K 线的行数减半。"
        ),
    ),
}

#: 非 agent 的 run 级码（数据层）。同样需要 hint，否则用户只能看到代码。
#: ★v8 评审：`retryable` 必须与 v6 的内联元组**逐值相同**（§20.5.4 的"保持原样"）。
#: v6 的元组里只有 `internal / budget_exceeded / result_schema_unsatisfied / agent_error /
#: interrupted`，所以这两个都不是 retryable——把它们翻成 `True` 会让一次"数据源全挂"
#: 在界面上多出一个重试按钮，而重试只会再失败一次。
RUN_ERROR_CODES.update(
    {
        "instrument_not_found": FailureSpec(
            code="instrument_not_found",
            retryable=False,
            severity="user",
            hint="这个标的已经被删除了。回搜索重新选一个标的。",
        ),
        "data_provider_unavailable": FailureSpec(
            code="data_provider_unavailable",
            retryable=False,
            severity="transient",
            hint="行情/财务数据源都不可用。稍后重试；离线演示可以把 DATA_PROVIDERS 设成 synthetic。",
        ),
        # ★v8 评审（AC-76 的漏洞）：`mock_runner` 会抛 `snapshot_missing`，它就经
        # `run_manager._finalize_failure(exc.code)` 变成一条**真实的** run 级码，过去却
        # 没有表项 ⇒ 用户看到的是通用兜底句，`retryable` 被静默判成 False。
        # 这是我们自己的 bug（快照没组装起来），所以归 `internal`。
        "snapshot_missing": FailureSpec(
            code="snapshot_missing",
            retryable=True,
            severity="internal",
            hint=(
                "本次生成的资料快照没有组装完整（服务端问题，不是模型的问题）。"
                "重新生成一次即可；连续出现请把 run id 一起反馈。"
            ),
        ),
    }
)

#: exit code → `(code, message)`。★v8 / E-42：**exit 1 = 结构化未提交**（实测），
#: 不是 `agent_error`。这条是 H-4 裁决的落地。
EXIT_MAP: dict[int, tuple[str, str]] = {
    0: ("result_missing", "引擎正常退出但没有返回 result 事件"),
    1: ("result_schema_unsatisfied", "模型多次提交仍未满足 JSON Schema"),
    2: ("usage_error", "引擎配置或参数错误"),
    3: ("budget_exceeded", "生成超出预算（max-turns / max-duration）"),
    129: ("cancelled", "生成被终止"),
    130: ("cancelled", "生成被终止"),
    143: ("cancelled", "生成被终止"),
}

#: exit 2 的已知精化（按顺序匹配，先命中先返回）。`result_schema_too_*` 三条把
#: "参数错误"变成"schema 超预算"——历史 run `06456fcb` 死于其中第一条。
STDERR_REFINE: tuple[tuple[str, str], ...] = (
    ("result_schema_too_deep", "schema_over_budget"),
    ("result_schema_too_large", "schema_over_budget"),
    ("result_schema_too_many", "schema_over_budget"),
    ("unknown option", "usage_error"),
    ("invalid --session-id", "usage_error"),
)

#: 没有 result 行时，从引擎日志里认凭证类失败。
#: ★v9（V10-P1-4）：v9 草案里的裸子串 `"401"` / `"403"` 会把 v8 的 B-2 修过的东西请回来
#: （`aragon_runner.py`：裸 `401` / `quota` 曾把"用户取消"改写成"凭证被拒"）。这次的
#: 后果更重——`provider_auth` **不在** `RETRYABLE_BY_RUNNER` 里，误判一次就少一次重试。
#: 所以判据是**分词正则**，不是子串；`AUTH_HINTS` 从此是同一份词表的别名（消灭第二份）。
AUTH_MARKERS: tuple[re.Pattern[str], ...] = (
    # 成词才认：`14013` / `port 4013` 不命中（`\b` 在数字串里不成立）。
    re.compile(r"\b401\b|\b403\b", re.I),
    # 上游 401 的原文里出现两次（E-71 / E-80）：`check your API key`、`Your api key: …`
    re.compile(r"api[ _-]?key", re.I),
    re.compile(r"\bunauthori[sz]ed\b|\bforbidden\b", re.I),
    re.compile(r"\bauthenticat", re.I),
    re.compile(r"\binsufficient\b|\bquota\b|\bbalance\b|\bbilling\b", re.I),
)

#: v8 的 `AUTH_HINTS`（用于 stderr 的无 result 行路径）与上面的 message 判据是**同一件事**，
#: 收敛成一份（V10-P1-4）：保留旧名字，消灭第二份可漂移的词表。
AUTH_HINTS: tuple[str, ...] = tuple(pattern.pattern for pattern in AUTH_MARKERS)

#: `AUTH_MARKERS` 的**反面**判据（V10-P1-4 的第二道保险）。
#: 一条人造的 `max tokens 401 is not a limit` 不是凭证问题；把 `--max-tokens 401`
#: 这类"预算数字"读成 HTTP 401，会让用户跑去改一把完全正常的密钥。
AUTH_ANTI_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:max|token|tokens|limit)[\s:=_\-]{0,3}\b(?:401|403)\b", re.I),
    re.compile(r"\b(?:401|403)\b.{0,16}\b(?:tokens?|not a limit|rate limit)\b", re.I),
)


def looks_like_auth(text: str | None) -> bool:
    """message 是否像"凭证被拒/额度耗尽"。**只做判定，不做推导。**

    正面词表命中 **且** 反面词表不命中。两条都必要：`401` 在"max tokens 401"里是预算
    数字，不是 HTTP 状态码，而 `provider_auth` 不参与 runner 重试（误判一次就少一次重试）。
    """
    value = text or ""
    if any(pattern.search(value) for pattern in AUTH_ANTI_MARKERS):
        return False
    return any(pattern.search(value) for pattern in AUTH_MARKERS)


#: ★v9（K3 / §21.3.4）：`stopReason` → run 级码。**实测出现的三个值 + 两个保守预留**。
#: 空串 = "这不是结论，往下走"（R-2c）。未知值**不推导**（R-46：CLI 的词表随版本变化），
#: 落到 EXIT_MAP 兜底并记一条 info —— 这张表是数据，不是分支。
STOP_REASON_MAP: dict[str, str] = {
    "timeout": "provider_unavailable",
    "error": "agent_error",
    "max_turns": "budget_exceeded",
    "aborted": "cancelled",
    "end_turn": "",
    # ★v12（§22.5.3 / AC-120）：**输出被 max-token 截断**是另一件事，不是"轮次烧完"。
    # `--max-tokens 40000` 是我们自己定的预算（`config.aragon_max_output_tokens`），一次真实
    # 生成平均要 20k–34k 输出 token，报告越长越接近——所以这个码在真机上一定会出现。
    # 把它并进 `budget_exceeded`（hint 指向 `ARAGON_MAX_TURNS`）会让用户去调一个不是
    # 瓶颈的数字（S-5：截断必须能指向那个预算）。
    "length": "output_truncated",
    "max_tokens": "output_truncated",
}

#: ★v9（K3 / §21.3.5）：runner 层**唯一**允许重试的码。明确不重试的理由：
#: `provider_auth`（改密钥才有用）、`cancelled`（用户意图）、`budget_exceeded`（要改配置）、
#: `result_schema_unsatisfied`（重试同一模型多半同样结局，且成本翻倍 —— 留给用户显式再生成）。
RETRYABLE_BY_RUNNER: frozenset[str] = frozenset({"provider_unavailable", "result_missing"})

#: ★v9（K3）：密钥打码的兜底正则（与 `aragon_runner._SECRET_RE` 同一份形态）。
#: 只打**完整**的 `sk-…` / `ar-…`；上游回显的 `****O5p6`（后 4 位）**放过**（E-80）。
_SECRET_RE = re.compile(r"(?:sk|ar|ak)-[A-Za-z0-9_\-]{8,}")


def mask_secrets(text: str | None, limit: int | None = None) -> str:
    """把 `result.error.message` 里的密钥形态打掉再带出去（K1 的安全半条命）。

    `run.error_message` / NDJSON 回显 / SSE 载荷上凡是把上游原文带出去的地方都过它。
    """
    value = _SECRET_RE.sub("***", text or "")
    return value[:limit] if limit is not None else value

#: CLI 自己写进 `result.error.code` 的码 → 我们的 run 级码。
#: 实测：探针 C 的 result 行是 `{"isError": true, "stopReason": "error",
#: "error": {"code": "result_schema_unsatisfied", "message": "No result matching
#: schema.json was submitted."}}`。
#:
#: ★实施发现（I-63）：真实 CLI 对"供应商网络失败"给的码是 **`stream_error`**，不是
#: `network_error`——后者是 `retry` 事件里 `errorType` 的词汇（E-45），两套词表不是同一个。
#: 实测（AC-83 的真实失败探针）：坏 base URL 的 result 行是
#: `{"isError": true, "error": {"code": "stream_error", "message": "Network error reaching
#: the provider. (openai network error: fetch failed (Error: bad port))"}}`，exit 1。
#: 只按设计里那 8 个 `errorType` 写映射，这条失败就会落到 `agent_error`——"引擎返回了
#: 一个我们没见过的错误形态"，而它其实是一次清清楚楚的网络问题。
CLI_ERROR_CODE_MAP: dict[str, str] = {
    "result_schema_unsatisfied": "result_schema_unsatisfied",
    "network_error": "provider_unavailable",
    "stream_error": "provider_unavailable",
    "fetch_error": "provider_unavailable",
    "upstream_error": "provider_unavailable",
    "rate_limited": "provider_unavailable",
    "timeout": "provider_unavailable",
    "auth_error": "provider_auth",
    "invalid_api_key": "provider_auth",
    "context_overflow": "usage_error",
    "invalid_request": "usage_error",
    #: ★v9（K3 / E-68）：`--provider deepseek` 的 result 行是
    #: `error.code="config"`（"Provider \"deepseek\" has no adapter…"）+ exit 2。
    #: 这个码过去不在表里 ⇒ 落成 `agent_error`（"没见过的错误形态"），
    #: 而它其实是一句清清楚楚的"你填了一个不存在的 provider"。
    "config": "usage_error",
}


def spec_for(code: str | None) -> FailureSpec | None:
    """码 → `FailureSpec`；未知码返回 `None`（调用方决定怎么兜底，不这里编）。"""
    if not code:
        return None
    return RUN_ERROR_CODES.get(code)


def hint_for(code: str | None) -> str:
    """码 → hint。未知码给一句诚实的兜底，而不是空字符串（界面会留一个空洞）。"""
    spec = spec_for(code)
    if spec is not None:
        return spec.hint
    return "这次失败还没有对应的说明。看本次工作区的 NDJSON 日志尾部，或把 run id 一起反馈。"


def is_retryable(code: str | None) -> bool:
    """`runs.error_code` 的唯一 retryable 判定（消灭 v6 的内联元组）。"""
    spec = spec_for(code)
    return bool(spec and spec.retryable)


#: 归一化时抹掉的绝对路径（Windows `C:\…` / POSIX `/…/`）。
_PATH_RE = re.compile(r"(?:[a-zA-Z]:[\\/][^\s\"']*|(?:/[\w.\-]+){2,})")
_DIGITS_RE = re.compile(r"\d+")


def normalize_message(message: str | None, limit: int = 200) -> str:
    """小写 + 数字→`#` + 去绝对路径 + 截断（R-38：同因不同表现必须同指纹）。"""
    text = (message or "").lower()
    text = _PATH_RE.sub("#path", text)
    text = _DIGITS_RE.sub("#", text)
    return text[:limit]


def failure_fingerprint(code: str, message: str | None, exit_code: int | None) -> str:
    """12 位十六进制指纹：同一根因连出三次说明是配置问题，不是运气问题。

    稳定性由 `normalize_message()` 保证——消息里的可变数字与绝对路径先被抹掉，
    否则"同因不同表现"会被算成不同指纹，三次告警永远不触发。
    """
    normalized = normalize_message(message)
    payload = f"{code}|{normalized}|{exit_code}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "AUTH_HINTS",
    "AUTH_MARKERS",
    "CLI_ERROR_CODE_MAP",
    "EXIT_MAP",
    "FailureSpec",
    "RETRYABLE_BY_RUNNER",
    "RUN_ERROR_CODES",
    "STDERR_REFINE",
    "STOP_REASON_MAP",
    "Severity",
    "failure_fingerprint",
    "hint_for",
    "is_retryable",
    "looks_like_auth",
    "mask_secrets",
    "normalize_message",
    "spec_for",
]
