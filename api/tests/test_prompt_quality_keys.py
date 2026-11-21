"""`quality.prompt` 的五个键**既在响应模型上、也有生产者**（spec §23.3.4b；AC-146 / R-77）。

这是"声明了但没有生产者"的第三次守卫（前两次是 `quality.cost` 与 `quality.prompt` 本身，
它们整整两个版本没到过前端——V10-P2-7）。

只加响应字段 = 加两个永远为 `None` 的空键，而 `test_every_quality_key_has_a_response_field`
盯的是**反方向**（quality 有键、模型没有字段），救不了这一侧。所以这里**真的跑一次**
`prompt_quality()`，断言键集合与值。
"""

from __future__ import annotations

import hashlib

from app.config import get_settings
from app.schemas.report import PromptQuality
from app.services import report_validator

EXPECTED_KEYS = {"version", "sha1", "bytes", "baseReportId", "baseVersion"}


def _research_prompt():
    return get_settings().prompts_dir / "research_system.md"


def test_both_new_keys_exist_on_the_response_model() -> None:
    assert set(PromptQuality.model_fields) >= EXPECTED_KEYS


def test_the_producer_returns_all_five_keys() -> None:
    payload = report_validator.prompt_quality(_research_prompt())
    assert payload is not None
    assert set(payload) == EXPECTED_KEYS


def test_the_producer_carries_the_base_pair_through() -> None:
    payload = report_validator.prompt_quality(
        _research_prompt(), base_report_id="a" * 32, base_version=12
    )
    assert payload is not None
    assert payload["baseReportId"] == "a" * 32
    assert payload["baseVersion"] == 12

    baseline = report_validator.prompt_quality(_research_prompt())
    assert baseline is not None
    assert baseline["baseReportId"] is None and baseline["baseVersion"] is None, (
        "独立生成 ⇒ 两个键仍**在**、值为 None（缺键会让前端分不清'没更新'与'旧后端'）"
    )


def test_the_payload_survives_the_response_model() -> None:
    """生产者给的字典必须能被响应模型收下（多一个键就 `extra=forbid`… 这里是默认 ignore）。"""
    payload = report_validator.prompt_quality(_research_prompt(), base_version=3)
    model = PromptQuality.model_validate(payload)
    assert model.bytes > 0
    assert model.version == 9
    assert model.baseVersion == 3


def test_bytes_and_sha1_still_describe_the_system_prompt() -> None:
    """P0-3：`quality.prompt.bytes` 量的是 **system** 提示词，不是 task 模板。

    `base_digest` 进的是任务提示词（`research_task.md.j2`），所以它**不改变**这个数字——
    这条断言就是"别拿 prompt.bytes 去证明 base_digest 的预算"的机器化版本。
    """
    path = _research_prompt()
    raw = path.read_bytes()
    payload = report_validator.prompt_quality(path)
    assert payload is not None
    assert payload["bytes"] == len(raw)
    assert payload["sha1"] == hashlib.sha1(raw).hexdigest()[:12]


def test_the_task_template_is_not_a_version_marker_host() -> None:
    """★P0-3：标记的唯一宿主是两个 system 提示词；task 模板不许有标记。"""
    task = get_settings().prompts_dir / "research_task.md.j2"
    assert report_validator.PROMPT_VERSION_RE.search(task.read_text(encoding="utf-8")) is None
