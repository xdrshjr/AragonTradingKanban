"""Process configuration.

Every value has a working default, so the API boots with no `.env` at all
(spec §14). Values come from the environment first, then `<repo>/.env`.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging import get_logger

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parent

logger = get_logger("config")


def _normalize_sqlite_url(url: str) -> str:
    """Turn a repo-relative sqlite path into an absolute one.

    ``sqlite:///api/data/app.db`` must mean the same file whether uvicorn was
    started from the repo root or from ``api/``. Anything that already looks
    absolute (``sqlite:///C:/…`` or ``sqlite:////var/…``) is left untouched.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw = url[len(prefix) :]
    if not raw or raw.startswith(":memory:"):
        return url
    candidate = Path(raw)
    if candidate.is_absolute() or (len(raw) > 1 and raw[1] == ":"):
        return url
    return f"{prefix}{(REPO_DIR / raw).resolve().as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── API ──────────────────────────────────────────────────────────────
    py_api_port: int = 8000
    database_url: str = "sqlite:///api/data/app.db"
    allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    api_token: str = ""

    # ── Agent engine ─────────────────────────────────────────────────────
    agent_mode: Literal["auto", "real", "mock"] = "auto"
    force_preflight: bool = False
    aragon_required_tools: str = "read_file,glob,grep,list_dir,submit_result"
    aragon_bin: str = ""
    #: ★v7（I-61）：`aragon doctor` + `aragon models` 两项 warn-only 自检的开关。
    #: 实测这两项合计 11.1s（`models` 要问一次模型列表），而结论十分钟内不会变；
    #: 关掉它不影响任何生成能力，只少一条"这台机器自检如何"的提示。
    aragon_self_check: bool = True
    aragon_provider: str = ""
    aragon_model: str = ""
    # ── ★v9 K1：第三方 OpenAI 兼容端点（spec §21.3.2）────────────────────
    # 为什么必须进 argv：实测（E-72/E-73）`OPENAI_BASE_URL` / `OPENAI_API_KEY` 环境变量
    # **不被 CLI 采纳**——base URL 与 key 都只能走 `--base-url` / `--api-key` 全局 flag。
    # 空值 = 用 CLI 自己的 profile，此时行为与 v8 完全一致（一个字都没改）。
    aragon_base_url: str = ""
    # `SecretStr` 而不是 `str`：`repr(settings)` / `model_dump()` 永远打印 `**********`，
    # 这一条就消掉了"某天有人把 settings 打进日志"的整类事故（spec §21.3.2）。
    aragon_api_key: SecretStr = SecretStr("")
    aragon_max_turns: int = 40
    aragon_max_duration_ms: int = 900_000
    aragon_idle_timeout_ms: int = 180_000
    aragon_tool_timeout_ms: int = 60_000
    aragon_home: str = ""
    #: Extra environment variable NAMES to pass to the child process, comma
    #: separated. The base whitelist stays minimal; this is the sanctioned door.
    aragon_env_passthrough: str = ""
    max_report_cost_usd: float = 0.60

    # ── Agent robustness (H1/H2, ★v7 §20.3.2 / §20.3.3) ──────────────────
    #: `lean` → inject the six `--no-*` global flags (measured: −2551 input tokens
    #: per turn, +76%, and no more `[skills] warn` on stderr). `full` keeps the
    #: machine's own posture; it exists because an operator must be able to go back
    #: in one line (AC-68), not because it is expected.
    aragon_agent_posture: Literal["lean", "full"] = "lean"
    #: CLI default is 10 with up to 30 s backoff each; 10 attempts can eat the whole
    #: `ARAGON_MAX_DURATION_MS` and turn "the network was down" into "the model was
    #: too slow" (E-45: 6 attempts ≈ 44 s). 6 keeps the two distinguishable.
    #: ★v9（K4 / §21.3.5）：默认 6 → **0**。实测（E-69）CLI 有**第二层**重试不认
    #: `--retry-max`（`maxRetries=10`、每次退避 60 s），一次"端点不可达"花掉 2 分 03 秒
    #: 并以 `exit 3 / stopReason=timeout / isError=false` 结束——会被报成"模型不肯提交"。
    #: `--retry-max 0` 走的是另一条路（E-70）：2.0 s 返回、`exit 1`、`stream_error`，
    #: 这才可归因。重试权因此收回 runner（`AGENT_ATTEMPTS`）。配置 >0 仍允许（运维出口），
    #: 但会在 capabilities 里带一条 `retryLayersWarning` 并在启动时 warn。
    aragon_retry_max: int = 0
    #: Replaces the hard-coded `--max-tokens auto`. Measured max output 34586, so
    #: 40000 makes CLI truncation predictable instead of invisible.
    aragon_max_output_tokens: int = 40_000
    #: `--result-repair-turns`; clamped by `capability.result.maxRepairTurns` at
    #: argv-build time so a CLI that tightens its own ceiling cannot hand us exit 2.
    aragon_repair_turns: int = 2
    #: `prompt.md + system.md + schema.json` must stay under this. The gate exists
    #: to stop someone inlining a 47.6 KB bars.csv into the prompt (R-39).
    aragon_max_prompt_bytes: int = 32_768
    #: JSON price override, e.g. `{"deepseek-flash":{"in":0.00014,"out":0.00028}}`.
    #: An ops door: a price change is a restart, not a release (§20.3.5).
    #: ★v8 评审：字段名不带 `aragon_` 前缀时，pydantic-settings 认的环境变量叫
    #: `PRICE_OVERRIDE`，而 `.env.example` / `pricing.py` / spec §20.3.5 全都写
    #: `ARAGON_PRICE_OVERRIDE` —— 于是那个"运维出口"其实是个死开关：照着文档配一路
    #: 读到的都是空串，估算继续用旧价，且**不报任何错**（`extra="ignore"` 把未知键吞了）。
    #: 用 `AliasChoices` 同时接受两个名字，既有部署（若已用 `PRICE_OVERRIDE`）不受影响。
    price_override: str = Field(
        default="",
        validation_alias=AliasChoices("ARAGON_PRICE_OVERRIDE", "PRICE_OVERRIDE", "price_override"),
    )

    # ── ★v9 K2/K4：引擎自检与重试（spec §21.3.3 / §21.3.5）────────────────
    #: runner 层总尝试次数（1 = 关闭重试）。重试权从 CLI 收回之后，这里是**唯一**
    #: 会重试的地方：只重试 `provider_unavailable` / `result_missing`（RETRYABLE_BY_RUNNER）。
    agent_attempts: int = 2
    #: `AGENT_PROBE_ENABLED=0` ⇒ `POST /agent/probe` 回 503 `probe_disabled`。
    agent_probe_enabled: bool = True
    #: 已验且在 TTL 内 ⇒ 不重复 spawn（一次探针 ≈ $0.0016 / 10 秒，见 §21.3.3）。
    agent_probe_ttl_sec: int = 300
    #: `engine_probes` 的硬上限（只追加表，超过按 created_at 倒序保留最新的）。
    agent_probe_keep: int = 200

    # ── ★v12（§22.3.3 / §22.3.8）：墙钟兜底与会话卫生 ─────────────────────
    #: 硬截止 = `ARAGON_MAX_DURATION_MS` + 这个宽限（秒）。**不设"硬截止秒数"配置项**
    #: （C-45 / V11-P1-10）：那会与 CLI 的 `--max-duration` 形成第二个截止真源，而两个
    #: 真源里的那个更松的永远赢——于是"调大 max-duration"变成唯一能解释的行为。
    #: 这里只加"多等一会儿"的余量，语义是"给 CLI 收尾的时间"，不是第二个上限。
    agent_hard_deadline_grace_sec: int = 120
    #: 会话目录体积的**只读**阈值（MB）。超了只在 `/capabilities.runtime.sessionDirMb`
    #: 上报 + 界面提示"可清理"，**永不自动删**（R-61：全局 prune 实测会删别的项目的会话）。
    agent_session_dir_max_mb: int = 256

    # ── Market data ──────────────────────────────────────────────────────
    data_providers: str = "akshare,yfinance,synthetic"
    allow_synthetic: bool = True
    bar_cache_ttl_sec: int = 900
    provider_timeout_sec: float = 10.0

    # ── Storage ──────────────────────────────────────────────────────────
    workspace_root: str = ""
    run_retention_days: int = 14
    #: ★v14（U5 / §23.3.5）：回收站窗口。软删的行在 `TRASH_RETENTION_DAYS` 天之后由
    #: `purge_trash()` **硬删**（那是全项目唯一的硬删入口）。`0` ⇒ 只保留到下一次 purge，
    #: 仅供测试验证硬删路径；生产上用 30（"哎呀我删错了"够用，库也不会无限膨胀）。
    trash_retention_days: int = 30
    log_level: str = "INFO"

    # ── Ask (A1 / §18.3.2) ───────────────────────────────────────────────
    #: `cite` is free and unlimited; `deep` is the paid档 and gets a per-report
    #: daily quota. The quota is a *refusal*, never a silent downgrade (§7.3).
    ask_daily_limit: int = 30
    ask_deep_max_turns: int = 12
    ask_deep_max_duration_ms: int = 180_000
    ask_max_question_chars: int = 500
    ask_top_k: int = 5

    # ── Search / scheduler (A2, A4) ──────────────────────────────────────
    search_fts: bool = True
    #: `auto` → use pypinyin when importable; `on` → require it; `off` → skip it.
    search_pinyin: str = "auto"
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 30
    scheduler_lease_seconds: int = 120
    scheduler_batch_size: int = 5

    # ── Cost governance (G3 / §19.3.5) ───────────────────────────────────
    #: `0` means **unlimited** and is the default: a product must not lock a user
    #: out because of a default value. Read through `get_settings()` on every gate
    #: check so changing it in the cost panel takes effect immediately.
    budget_monthly_usd: float = 0.0
    #: The ledger must outlive `RUN_RETENTION_DAYS` (14) — otherwise "本月花了多少"
    #: silently shrinks on the 15th of every month (§19.3.5).
    cost_ledger_keep_days: int = 730

    # ── Web (read-only mirror, handy for capability payloads) ────────────
    next_public_api_base: str = "http://127.0.0.1:8000"
    enable_py_rewrite: bool = False

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        return _normalize_sqlite_url(value)

    # ── derived helpers ─────────────────────────────────────────────────
    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allow_origins.split(",") if item.strip()]

    @property
    def provider_chain(self) -> list[str]:
        return [item.strip().lower() for item in self.data_providers.split(",") if item.strip()]

    @property
    def required_tools(self) -> list[str]:
        return [item.strip() for item in self.aragon_required_tools.split(",") if item.strip()]

    @property
    def env_passthrough(self) -> list[str]:
        return [item.strip() for item in self.aragon_env_passthrough.split(",") if item.strip()]

    @property
    def price_overrides(self) -> dict[str, dict[str, float]]:
        """`PRICE_OVERRIDE` 解析结果。坏 JSON 只 warn 一次并当作"没有覆盖"。

        永不抛：一个配置项写坏了不该让 API 起不来（§14「每个值都有可用默认」）。
        """
        raw = (self.price_override or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("PRICE_OVERRIDE 不是合法 JSON，已忽略：%s", raw[:120])
            return {}
        if not isinstance(parsed, dict):
            logger.warning("PRICE_OVERRIDE 必须是对象，已忽略：%s", raw[:120])
            return {}
        result: dict[str, dict[str, float]] = {}
        for model, price in parsed.items():
            if not isinstance(price, dict):
                logger.warning("PRICE_OVERRIDE[%s] 不是对象，已忽略", model)
                continue
            entry: dict[str, float] = {}
            for key in ("in", "out"):
                value = price.get(key)
                if isinstance(value, (int, float)) and value >= 0:
                    entry[key] = float(value)
            if entry:
                result[str(model).strip().lower()] = entry
        return result

    @property
    def workspace_dir(self) -> Path:
        if self.workspace_root:
            path = Path(self.workspace_root)
            if not path.is_absolute():
                path = REPO_DIR / path
            return path
        return API_DIR / "workspace"

    @property
    def runs_dir(self) -> Path:
        return self.workspace_dir / "runs"

    @property
    def asks_dir(self) -> Path:
        """Ask workspaces live beside run workspaces and expire the same way."""
        return self.workspace_dir / "asks"

    @property
    def data_dir(self) -> Path:
        return API_DIR / "data"

    @property
    def prompts_dir(self) -> Path:
        return API_DIR / "app" / "agent" / "prompts"

    @property
    def schemas_dir(self) -> Path:
        return API_DIR / "app" / "agent" / "schemas"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]  # values come from env/.env
    # ★实施发现（I-92）：**空字符串的环境变量不许盖掉 `.env` 里的值**。
    # 实测于本轮真机探针：把 `ARAGON_PROVIDER=openai` / `ARAGON_MODEL=deepseek-flash` 写进
    # `.env`，进程环境里却已经有 `ARAGON_PROVIDER=''`（任何 launcher / docker-compose 的
    # `- ARAGON_PROVIDER=` 都会这样），而 pydantic-settings 的优先级是"环境变量 > .env"
    # ⇒ 用户填的值被一个空串静默盖掉。这与本轮 K1 修的"假绿"是同一类事故：
    # **配置项存在，但没生效，而且不报错**（第六条纪律）。
    settings = _restore_empty_placeholders(settings)
    # Make sure the runtime directories exist before anything writes to them.
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.asks_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


#: 这些字段是"引擎是谁"的身份项：空串 = "没填"，不是"填了空值"。
#: 只修这七个：其余的字符串设置（如 `ALLOW_ORIGINS`）里空串是有语义的（=同源）。
_EMPTY_MEANS_UNSET: tuple[str, ...] = (
    "aragon_provider",
    "aragon_model",
    "aragon_bin",
    "aragon_home",
    "aragon_base_url",
    "aragon_api_key",
    "workspace_root",
)


def _restore_empty_placeholders(settings: Settings) -> Settings:
    """环境变量给了空串、而 `.env` 里有非空值时，用 `.env` 的值（`I-92`）。

    只处理 `_EMPTY_MEANS_UNSET` 里的七个身份项；读不到 `.env` 就原样返回（永不抛）。

    读的是与 `model_config.env_file` **同一个列表、同一个顺序**（后面的文件覆盖前面的，
    与 pydantic-settings 的优先级一致）：只读仓库根那一份，会让"`api/.env` 里填了引擎"
    这个同样合法的部署在修完 I-92 之后仍然被空串吃掉。
    """
    values: dict[str, str] = {}
    for candidate in (REPO_DIR / ".env", API_DIR / ".env"):
        try:
            values.update({key: value for key, value in dotenv_values(candidate).items() if value is not None})
        except Exception:  # pragma: no cover - python-dotenv 缺失或文件不可读
            continue
    changed = False
    for name in _EMPTY_MEANS_UNSET:
        if os.environ.get(name.upper(), None) != "":
            continue
        fallback = (values.get(name.upper()) or "").strip()
        if not fallback:
            continue
        if name == "aragon_api_key":
            setattr(settings, name, SecretStr(fallback))
        else:
            setattr(settings, name, fallback)
        changed = True
    if changed:
        logger.info("检测到空的环境变量覆盖了 .env 里的引擎身份设置，已按 .env 修正（I-92）")
    return settings
