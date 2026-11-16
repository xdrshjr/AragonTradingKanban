"""引擎指纹（spec §19.3.6，G4 / M18）。

**为什么要有指纹**：投研产品最危险的误读之一是「模型升级」被读成「观点变化」。
两版报告的 diff 差异可能完全来自换了模型，而这一点在 v4 的界面里是不可见的。

指纹 = `sha1(version|provider|model|sorted(allow_tools)|schema_bytes)[:12]`。

三条刻意的限制：

* **`model` / `provider` 参与哈希，但不写进 `quality.engine`**（★V6-P1-3）。
  这两个事实的权威是 `reports.model|provider` 列与 `capabilities.aragon.*`；
  第三份副本必然漂移（本项目为双源付过的代价见 I-20 / I-27 / V4-P1-10）。
* **模型名只取主版本段**（`claude-sonnet-20260210` → `claude-sonnet`），否则每次
  换个日期后缀都会提示"引擎已变更"，提示变成噪音（R-27）。
* **指纹只做提示，绝不判分**（O-10）：它不改变 diff 的任何 `delta`。

`schema_bytes` 由**调用方**传入：`agent/` 不得 import `services/`
（`test_boundaries` 规则 4），所以读取 schema 文件的动作留在 `report_validator`。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from .preflight import MAX_CLI_EXCLUSIVE, MIN_CLI_VERSION, AragonCapability, parse_version

#: 指纹的组成项，也是 `comparability.changed` 的取值域（顺序固定，便于断言）。
FINGERPRINT_PARTS = ("cliVersion", "provider", "model", "allowTools", "schemaBytes")


def _family(model: str | None) -> str:
    """`claude-sonnet-20260210` → `claude-sonnet`；空值 → 空串。"""
    if not model:
        return ""
    lowered = model.strip().lower()
    parts = lowered.split("-")
    if len(parts) >= 3 and parts[-1].isdigit():
        parts = parts[:-1]
    return "-".join(parts)


@dataclass(slots=True, frozen=True)
class EngineFingerprint:
    fingerprint: str
    cli_version: str
    #: 参与哈希；**不**写进 `quality.engine`。
    model: str = ""
    provider: str = ""
    allow_tools: tuple[str, ...] = ()
    schema_bytes: int = 0
    #: 版本是否落在 `>=0.6.66,<0.7` 之外（`capabilities.engine.drift`）。
    drift: bool = False

    def as_quality(self) -> dict[str, Any]:
        """`quality.engine` 的**四元组**。C-18 用键集合守住这条。"""
        return {
            "fingerprint": self.fingerprint,
            "cliVersion": self.cli_version,
            "allowTools": list(self.allow_tools),
            "schemaBytes": self.schema_bytes,
        }

    def as_capability(self) -> dict[str, Any]:
        """`capabilities.engine` —— 同样四项，`model`/`provider` 留在 `aragon`。"""
        return {
            "fingerprint": self.fingerprint,
            "drift": self.drift,
            "schemaBytes": self.schema_bytes,
            "allowTools": list(self.allow_tools),
        }


def _version_drift(version: str | None) -> bool:
    parsed = parse_version(version)
    if parsed is None:
        return True
    return not (MIN_CLI_VERSION <= parsed < MAX_CLI_EXCLUSIVE)


def engine_fingerprint(
    capability: AragonCapability,
    settings: Settings,
    *,
    schema_bytes: int,
) -> EngineFingerprint:
    """算一次指纹。同输入必须同输出（AC-58），无随机、无时钟。"""
    model = settings.aragon_model or capability.model or ""
    provider = settings.aragon_provider or capability.provider or ""
    allow_tools = tuple(sorted(capability.extraAllowTools or settings.required_tools))
    version = capability.cli or ""

    payload = "|".join(
        [
            version,
            provider,
            _family(model),
            ",".join(allow_tools),
            str(int(schema_bytes)),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return EngineFingerprint(
        fingerprint=digest,
        cli_version=version,
        model=model,
        provider=provider,
        allow_tools=allow_tools,
        schema_bytes=int(schema_bytes),
        drift=_version_drift(version),
    )


def current_fingerprint(*, schema_bytes: int) -> EngineFingerprint | None:
    """便捷入口：从缓存的能力预检算指纹；拿不到 → **None 而不是伪造一份**。

    `preflight.check_aragon()` 是 60 秒缓存，所以这里不会额外 spawn 进程。
    """
    from ..config import get_settings
    from .preflight import check_aragon

    capability = check_aragon()
    if not capability.available:
        return None
    return engine_fingerprint(capability, get_settings(), schema_bytes=schema_bytes)


__all__ = [
    "FINGERPRINT_PARTS",
    "EngineFingerprint",
    "current_fingerprint",
    "engine_fingerprint",
]
