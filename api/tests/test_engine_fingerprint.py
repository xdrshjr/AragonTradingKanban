"""引擎指纹与"可比性提示"（spec §19.3.6；AC-58 / C-18 / O-10）。

三条刻意分开守的性质：

* **同输入同指纹**（AC-58）：无随机、无时钟，`engine_fingerprint` 是纯函数；
* **`quality.engine` 是四元组**（C-18）：`model` / `provider` 参与哈希但**不写进**
  `quality.engine`——它们的权威是 `reports.model|provider` 与 `capabilities.aragon.*`，
  第三份副本必然漂移（本项目为双源付过代价：I-20 / I-27 / V4-P1-10）；
* **指纹只提示、绝不判分**（O-10）：三种 `comparability` 状态下，diff 的四个结构化
  字段**逐字节相同**。`summary` 是字符串（V6-P1-2），所以"delta 一致"只能断言在
  `metricsDelta` / `blocksChanged` / `sectionsAdded` / `sectionsRemoved` 上。
"""

from __future__ import annotations

import json

import pytest

from app.agent.fingerprint import FINGERPRINT_PARTS, EngineFingerprint, engine_fingerprint
from app.agent.preflight import AragonCapability
from app.config import get_settings
from app.services import report_validator
from app.services.report_diff import compare_engine_quality, diff_docs

#: 四个结构化字段：`comparability` 不许动它们中的任何一个。
DELTA_FIELDS = ("metricsDelta", "blocksChanged", "sectionsAdded", "sectionsRemoved")


@pytest.fixture(autouse=True)
def _capability_decides_the_engine(monkeypatch):
    """让 `AragonCapability` 里的 model/provider 生效，不被本机 `.env` 覆盖（测试要可控）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aragon_model", "", raising=False)
    monkeypatch.setattr(settings, "aragon_provider", "", raising=False)


def _fingerprint(*, schema_bytes: int = 1234, **overrides) -> EngineFingerprint:  # noqa: ANN003
    """构造一次指纹计算；`overrides` 直接落到 `AragonCapability` 上。"""
    fields = {
        "available": True,
        "cli": "0.6.68",
        "provider": "anthropic",
        "model": "claude-sonnet-20260210",
        "extraAllowTools": ["read_file", "grep"],
    }
    fields.update(overrides)
    return engine_fingerprint(AragonCapability(**fields), get_settings(), schema_bytes=schema_bytes)


# ── 同输入同指纹（AC-58） ───────────────────────────────────────────────────


def test_same_input_is_the_same_fingerprint() -> None:
    first = _fingerprint()
    second = _fingerprint()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 12
    assert all(character in "0123456789abcdef" for character in first.fingerprint)
    assert first == second, "指纹对象整体相等：没有任何隐藏的可变状态"


def test_changing_the_model_changes_the_fingerprint() -> None:
    sonnet = _fingerprint(model="claude-sonnet-20260210")
    opus = _fingerprint(model="claude-opus-20260210")
    assert sonnet.fingerprint != opus.fingerprint
    assert sonnet.model == "claude-sonnet-20260210"


def test_a_date_suffix_alone_does_not_change_the_fingerprint() -> None:
    """R-27：模型名只取主版本段，否则换个日期后缀就提示"引擎已变更"，提示变噪音。"""
    first = _fingerprint(model="claude-sonnet-20260210").fingerprint
    later = _fingerprint(model="claude-sonnet-20260630").fingerprint
    assert first == later


def test_changing_the_allow_list_changes_the_fingerprint() -> None:
    base = _fingerprint(extraAllowTools=["read_file", "grep"])
    wider = _fingerprint(extraAllowTools=["read_file", "grep", "submit_result"])
    assert base.fingerprint != wider.fingerprint
    # 排序后拼接：同一组工具的先后顺序不该算作"换了引擎"
    assert base.fingerprint == _fingerprint(extraAllowTools=["grep", "read_file"]).fingerprint


def test_changing_the_schema_size_changes_the_fingerprint() -> None:
    assert _fingerprint(schema_bytes=7984).fingerprint != _fingerprint(schema_bytes=7985).fingerprint


def test_schema_bytes_comes_from_the_real_schema_file() -> None:
    """`schema_bytes` 由调用方传入（`agent/` 不得 import `services/`），这里守调用方给的值。"""
    size = report_validator.schema_bytes()
    assert size > 0
    assert _fingerprint(schema_bytes=size).schema_bytes == size
    assert _fingerprint(schema_bytes=size).fingerprint != _fingerprint(schema_bytes=size + 1).fingerprint


# ── `quality.engine` 是四元组（C-18） ──────────────────────────────────────


def test_as_quality_has_exactly_the_four_keys_and_no_model_or_provider() -> None:
    quality = _fingerprint().as_quality()
    assert set(quality) == {"fingerprint", "cliVersion", "allowTools", "schemaBytes"}
    assert "model" not in quality, "C-18：model 的权威是 `reports.model`，不是第三份副本"
    assert "provider" not in quality, "C-18：provider 的权威是 `capabilities.aragon`"
    assert isinstance(quality["allowTools"], list)


def test_as_capability_keeps_drift_and_drops_the_version_string() -> None:
    """`capabilities.engine` 同样四项，但换成 `drift`——版本字符串的权威是 `aragon.version`。"""
    capability = _fingerprint().as_capability()
    assert set(capability) == {"fingerprint", "drift", "allowTools", "schemaBytes"}
    assert "cliVersion" not in capability
    assert "version" not in capability
    assert "model" not in capability


def test_fingerprint_parts_are_the_documented_five() -> None:
    assert FINGERPRINT_PARTS == ("cliVersion", "provider", "model", "allowTools", "schemaBytes")


# ── 版本窗口（drift） ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cli", "drift"),
    [
        ("0.6.66", False),  # 下界闭
        ("0.6.68", False),
        ("0.6.99", False),
        ("0.7.0", True),  # 上界开
        ("0.6.65", True),
        ("1.0.0", True),
        (None, True),  # 拿不到版本就必须说"不可比"，而不是假装没问题
    ],
)
def test_drift_tracks_the_supported_version_window(cli, drift) -> None:
    """`>=0.6.66,<0.7` 之外一律 drift=True（与 `preflight.version_ok` 同一判定）。"""
    assert _fingerprint(cli=cli).drift is drift


# ── comparability 三态（AC-58） ────────────────────────────────────────────

ENGINE_A = {"fingerprint": "aaa111bbb222", "cliVersion": "0.6.68", "allowTools": ["read_file"], "schemaBytes": 7000}
ENGINE_B = {"fingerprint": "ccc333ddd444", "cliVersion": "0.6.68", "allowTools": ["read_file"], "schemaBytes": 7100}


def test_same_engine_reports_same_true_without_a_banner() -> None:
    result = compare_engine_quality({"engine": ENGINE_A}, {"engine": dict(ENGINE_A)})
    assert result["same"] is True
    assert result["changed"] == []
    assert result["note"] == "", "same=true 时不渲染横幅，也不该留一句噪音"
    assert result["deltaUnaffected"] is True


def test_different_engine_lists_the_changed_parts() -> None:
    result = compare_engine_quality({"engine": ENGINE_A}, {"engine": ENGINE_B})
    assert result["same"] is False
    assert result["changed"] == ["schemaBytes"]
    assert result["deltaUnaffected"] is True


def test_a_model_swap_is_reported_from_the_authoritative_columns() -> None:
    """`model` 取两版报告的 `model` 列——`quality.engine` 里根本没有这个键（V6-P1-3）。"""
    result = compare_engine_quality(
        {"engine": ENGINE_A},
        {"engine": ENGINE_B},
        model_a="claude-sonnet-20260210",
        model_b="claude-opus-20260210",
    )
    assert result["same"] is False
    assert "model" in result["changed"]
    assert "claude-sonnet-20260210" in result["note"] and "claude-opus-20260210" in result["note"]


def test_missing_engine_on_either_side_is_none_not_false() -> None:
    """老报告没有指纹：回填会伪造数据，所以"不知道"就明说不确定（三态里的 None）。"""
    for left, right in (({"engine": ENGINE_A}, None), (None, {"engine": ENGINE_A}), (None, None)):
        result = compare_engine_quality(left, right)
        assert result["same"] is None
        assert result["changed"] == []
        assert result["deltaUnaffected"] is True
        assert "指纹" in result["note"]


# ── O-10：三种状态下 delta 逐字节相同 ─────────────────────────────────────

DOC_A = {
    "_reportId": "report-a",
    "_model": "claude-sonnet-20260210",
    "rating": {"stance": "buy", "targetPrice": 1980},
    "sections": [
        {
            "id": "thesis",
            "title": "核心逻辑",
            "blocks": [{"type": "paragraph", "text": "批价企稳，直营占比提升。"}],
        },
        {
            "id": "fundamentals",
            "title": "基本面",
            "blocks": [{"type": "metrics", "items": [{"label": "毛利率", "value": "91.3%", "polarity": "bullish"}]}],
        },
        {"id": "risks", "title": "风险", "blocks": [{"type": "paragraph", "text": "需求下行。"}]},
    ],
}

DOC_B = {
    "_reportId": "report-b",
    "_model": "claude-sonnet-20260210",
    "rating": {"stance": "hold", "targetPrice": 1900},
    "sections": [
        {
            "id": "thesis",
            "title": "核心逻辑",
            "blocks": [{"type": "paragraph", "text": "批价回落，直营占比停滞。"}],
        },
        {
            "id": "fundamentals",
            "title": "基本面",
            "blocks": [{"type": "metrics", "items": [{"label": "毛利率", "value": "89.7%", "polarity": "bullish"}]}],
        },
        {"id": "catalysts", "title": "催化剂", "blocks": [{"type": "paragraph", "text": "一季报。"}]},
    ],
}


def _deltas(result: dict) -> dict[str, str]:
    """把四个结构化字段序列化——比"值相等"更严格：键顺序变了也算不一致。"""
    return {name: json.dumps(result[name], sort_keys=True, ensure_ascii=False) for name in DELTA_FIELDS}


def test_comparability_never_changes_a_single_delta() -> None:
    """O-10 的机器化：同引擎 / 换引擎 / 缺指纹，三种状态下的 delta 必须逐字节相同。"""
    quality_a = {"engine": ENGINE_A}
    states = [
        ("same", {"engine": dict(ENGINE_A)}, True),
        ("different", {"engine": ENGINE_B}, False),
        ("missing", None, None),
    ]

    results = [diff_docs(DOC_A, DOC_B, quality_a=quality_a, quality_b=quality) for _, quality, _ in states]

    baseline = _deltas(results[0])
    assert any(baseline.values()), "这份样例必须真的产生差异，否则'一致'是空话"
    for result in results[1:]:
        assert _deltas(result) == baseline, "comparability 只是提示，不许改动任何 delta"

    assert [result["comparability"]["same"] for result in results] == [expected for _, _, expected in states]
    for result in results:
        assert result["comparability"]["deltaUnaffected"] is True
        # V6-P1-2：`a` / `b` / `summary` 的既有形状不许变（变了 `/diff` 会 500）
        assert isinstance(result["a"], str) and isinstance(result["b"], str)
        assert isinstance(result["summary"], str) and result["summary"]
        assert not isinstance(result["summary"], dict)
