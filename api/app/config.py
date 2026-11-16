"""Process configuration.

Every value has a working default, so the API boots with no `.env` at all
(spec §14). Values come from the environment first, then `<repo>/.env`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parent


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
    aragon_provider: str = ""
    aragon_model: str = ""
    aragon_max_turns: int = 40
    aragon_max_duration_ms: int = 900_000
    aragon_idle_timeout_ms: int = 180_000
    aragon_tool_timeout_ms: int = 60_000
    aragon_home: str = ""
    #: Extra environment variable NAMES to pass to the child process, comma
    #: separated. The base whitelist stays minimal; this is the sanctioned door.
    aragon_env_passthrough: str = ""
    max_report_cost_usd: float = 0.60

    # ── Market data ──────────────────────────────────────────────────────
    data_providers: str = "akshare,yfinance,synthetic"
    allow_synthetic: bool = True
    bar_cache_ttl_sec: int = 900
    provider_timeout_sec: float = 10.0

    # ── Storage ──────────────────────────────────────────────────────────
    workspace_root: str = ""
    run_retention_days: int = 14
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
    # Make sure the runtime directories exist before anything writes to them.
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.asks_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
