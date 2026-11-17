"""成本三态（spec §20.3.5 / M21；AC-77 / AC-78 / AC-89）。

反例优先（§20.7.3 第 3 条）：先写"reported 存在时**不许**走估算"，再写"reported 缺失时
必须估算"。最容易犯的错是反过来——估算覆盖了引擎回执，账本从此不可信。

三个不可协商的裁决：

* **`estimated` 与 `reported` 永不同义**（H-6）：分列、分措辞、分计数；
* **`cost_known` 的语义一字不改**（AC-55）：当且仅当 `source == "reported"`；
* **既有 6 条价格逐值不可变**（AC-89 / H-20）：改价必须换 `PRICE_VERSION`，
  否则历史估算被静默改写，版本号失去追溯意义。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.cost_model import COST_SOURCES, resolve_cost
from app.agent.pricing import (
    BASELINE_PRICES_2026_02,
    PRICE_VERSION,
    PRICES,
    cost_for_tokens,
    model_family,
    price_for,
)
from app.agent.runner_base import AgentResult, AgentUsage
from app.config import get_settings
from app.models import CostLedger
from app.services import cost_service


def result(
    *,
    known: bool = False,
    cost: float | None = None,
    model: str | None = "deepseek-flash",
    tokens_in: int | None = 72_810,
    tokens_out: int | None = 24_955,
) -> AgentResult:
    return AgentResult(
        usage=AgentUsage(input_tokens=tokens_in, output_tokens=tokens_out),
        cost_usd=cost,
        cost_known=known,
        model=model,
    )


# ── AC-77：三态优先级 ──────────────────────────────────────────────────────


def test_reported_wins_over_the_estimate() -> None:
    """反例优先：**有回执时不许估**。否则估算会盖掉唯一可信的那个数。"""
    resolution = resolve_cost(result(known=True, cost=0.42))
    assert resolution.source == "reported"
    assert resolution.usd == 0.42
    assert resolution.reported_usd == 0.42
    assert resolution.estimated_usd is None, "reported 行不带估算值：两列互斥"
    assert resolution.price_version is None
    assert resolution.known is True


def test_estimated_when_the_engine_says_unknown() -> None:
    """实测的常态：`cost.known=false`。手上有精确 token 数，就不该说"不知道"。"""
    resolution = resolve_cost(result(known=False))
    assert resolution.source == "estimated"
    assert resolution.reported_usd is None
    assert resolution.usd == resolution.estimated_usd
    assert resolution.estimated_usd == pytest.approx(0.0172, abs=1e-6)
    assert resolution.price_version == PRICE_VERSION
    assert resolution.known is False, "估算**绝不**被标成已知（AC-55 的语义不变）"


def test_unknown_when_either_the_model_or_the_tokens_are_missing() -> None:
    """`unknown` 是三元里的一员，不是错误：没有价格或没有 token 就诚实地说不知道。"""
    assert resolve_cost(result(model="mystery-model")).source == "unknown"
    assert resolve_cost(result(tokens_in=None, tokens_out=None)).source == "unknown"
    empty = resolve_cost(result(tokens_in=0, tokens_out=0))
    assert empty.source == "unknown"
    assert empty.usd is None, "未知不是 0：0 会被读成'这次生成免费'"


def test_cost_sources_are_exactly_three() -> None:
    assert COST_SOURCES == ("reported", "estimated", "unknown")


def test_payload_shape_is_what_the_ui_and_quality_record_need() -> None:
    payload = resolve_cost(result()).as_payload()
    assert set(payload) == {"source", "reportedUsd", "estimatedUsd", "usd", "priceVersion"}
    assert payload["source"] == "estimated"
    assert payload["reportedUsd"] is None


# ── AC-89：既有价格逐值不可变，新模型能命中 ─────────────────────────────────


def test_existing_prices_are_immutable() -> None:
    """AC-89：既有 6 条与 2026-02 版**逐值相等**；改价必须换 `PRICE_VERSION`。"""
    assert PRICE_VERSION != "2026-02", "本轮加了两条数据，版本号必须推进（H-20）"
    for model, (expected_in, expected_out) in BASELINE_PRICES_2026_02.items():
        price = PRICES[model]
        assert (price.input_per_1k, price.output_per_1k) == (expected_in, expected_out), (
            f"{model} 的价格被就地改过——历史估算会被静默改写"
        )
    assert len(BASELINE_PRICES_2026_02) == 6


def test_new_deepseek_entries_resolve_without_crossing_wires() -> None:
    """`deepseek-flash` 命中新条目，`deepseek-chat` 仍命中旧条目（不串价）。"""
    flash = price_for("deepseek-flash")
    chat = price_for("deepseek-chat")
    assert flash is not None and chat is not None
    assert (flash.input_per_1k, flash.output_per_1k) == (0.00014, 0.00028)
    assert (chat.input_per_1k, chat.output_per_1k) == (0.0003, 0.0012), "flash 的存在不许影响 chat 的定价"
    assert model_family("deepseek-flash") == "deepseek-flash"
    assert price_for("deepseek-reasoner") is not None
    assert price_for("nope-model") is None


def test_price_override_is_an_ops_door(monkeypatch) -> None:
    """`ARAGON_PRICE_OVERRIDE` 让"价格变了"是一次重启，而不是一次发版。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "price_override", '{"deepseek-flash": {"in": 0.001, "out": 0.002}}', raising=False)
    override = price_for("deepseek-flash")
    assert override is not None
    assert (override.input_per_1k, override.output_per_1k) == (0.001, 0.002)

    monkeypatch.setattr(settings, "price_override", "{not json", raising=False)
    fallback = price_for("deepseek-flash")
    assert fallback is not None and fallback.input_per_1k == 0.00014, "坏 JSON 只 warn，不改行为"


def test_cost_for_tokens_is_linear_and_refuses_to_guess() -> None:
    assert cost_for_tokens("deepseek-flash", 1000, 0) == pytest.approx(0.0001, abs=1e-9)
    assert cost_for_tokens("mystery", 1000, 1000) is None
    assert cost_for_tokens("deepseek-flash", None, None) is None


# ── AC-78 / AC-86：三态的列分工与账本互斥 ──────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_gap_counter():
    cost_service.reset_ledger_gaps()
    yield
    cost_service.reset_ledger_gaps()


def test_estimated_run_keeps_reported_columns_null(session) -> None:
    """AC-78：`source='estimated'` 时 `usd` 仍为 NULL、`usd_est>0`（run 与账本两侧）。"""
    assert (
        cost_service.record(
            session,
            kind="run",
            ref_id="run-est",
            usd=None,
            known=False,
            source="estimated",
            usd_est=0.0172,
            model="deepseek-flash",
        )
        is True
    )
    session.commit()

    row = session.execute(select(CostLedger).where(CostLedger.ref_id == "run-est")).scalar_one()
    assert row.usd is None, "AC-55 一字不改：估算不写进'已报告'那一列"
    assert row.known == 0
    assert row.source == "estimated"
    assert row.usd_est == pytest.approx(0.0172)


def test_ledger_source_tristate_is_exclusive(session) -> None:
    """AC-86：每一行都满足 `source ⇔ (usd, usd_est)` 的三态互斥。"""
    cost_service.record(
        session, kind="run", ref_id="r-reported", usd=0.5, known=True, source="reported", usd_est=None
    )
    cost_service.record(
        session, kind="ask_deep", ref_id="a-estimated", usd=None, known=False, source="estimated", usd_est=0.02
    )
    cost_service.record(
        session, kind="run", ref_id="r-unknown", usd=None, known=False, source="unknown", usd_est=None
    )
    session.commit()

    rows = {row.ref_id: row for row in session.execute(select(CostLedger)).scalars().all()}
    assert rows["r-reported"].usd is not None and rows["r-reported"].usd_est is None
    assert rows["a-estimated"].usd is None and rows["a-estimated"].usd_est is not None
    assert rows["r-unknown"].usd is None and rows["r-unknown"].usd_est is None


def test_reported_without_an_amount_becomes_unknown(session) -> None:
    """自称"已报告"却没有金额：事实不成立，降级成 unknown 比写一个假 0 诚实。"""
    assert cost_service.record(session, kind="run", ref_id="r-liar", usd=None, known=True, source="reported") is True
    session.commit()
    row = session.execute(select(CostLedger).where(CostLedger.ref_id == "r-liar")).scalar_one()
    assert row.source == "unknown"
    assert row.usd is None


def test_an_illegal_source_is_a_gap_not_a_row(session) -> None:
    """AC-86 后半：非法 `source` 由 `record()` 记 `ledgerGap`，不写入。"""
    assert cost_service.record(session, kind="run", ref_id="r-bad", usd=1.0, known=True, source="guess") is False
    assert cost_service.ledger_gaps() == 1
    assert session.execute(select(CostLedger).where(CostLedger.ref_id == "r-bad")).scalar_one_or_none() is None


def test_source_is_a_required_keyword(session) -> None:
    """R-42：`source` 必填——缺参在测试期就炸，而不是被列的默认值静默错标成 unknown。"""
    with pytest.raises(TypeError):
        cost_service.record(session, kind="run", ref_id="r-no-source", usd=1.0, known=True)  # type: ignore[call-arg]


# ── AC-79 / AC-80：汇总双数与含估算的闸门 ──────────────────────────────────


def test_ledger_keeps_two_year_rows_and_null_usd(tmp_path: Path, session) -> None:
    """`usd IS NULL` 的老行迁移后仍是 NULL、`source` 是 `unknown`（AC-87 的数据侧）。"""
    session.add(
        CostLedger(
            kind="run",
            ref_id="r-legacy",
            usd=None,
            known=0,
            source="unknown",
            created_at="2026-01-05T00:00:00Z",
        )
    )
    session.commit()
    row = session.execute(select(CostLedger).where(CostLedger.ref_id == "r-legacy")).scalar_one()
    assert row.usd is None and row.usd_est is None and row.source == "unknown"

# ── AC-86 的落库侧：`apply_cost` 是 `runs` 三态唯一的写入口（★v8 评审补测）─────


def test_apply_cost_writes_mutually_exclusive_columns() -> None:
    """三态在 `runs` 行上互斥：reported ⇔ cost_usd 有值、estimated ⇔ 只有 cost_usd_est。

    ★v8 评审为什么必须补：`run_diagnostics.apply_cost()` 是 `runs.cost_source` /
    `runs.cost_usd_est` 的**唯一**写入口，而在补这条之前它一行测试都没有——
    把 `run_manager` 里那次调用删掉，全套用例依然全绿（AC-78 的 runs 半边形同虚设）。
    """
    from app.models import Run
    from app.services import run_diagnostics

    def fresh() -> Run:
        return Run(id="r-1", instrument_id="i-1", status="succeeded")

    reported = fresh()
    run_diagnostics.apply_cost(
        reported, resolve_cost(result(known=True, cost=1.25))
    )
    assert (reported.cost_source, reported.cost_usd, reported.cost_known, reported.cost_usd_est) == (
        "reported",
        1.25,
        True,
        None,
    )

    estimated = fresh()
    run_diagnostics.apply_cost(estimated, resolve_cost(result(known=False, cost=None)))
    assert estimated.cost_source == "estimated"
    assert estimated.cost_usd is None, "AC-78：估算绝不写进引擎回执那一列"
    assert estimated.cost_known is False
    assert estimated.cost_usd_est and estimated.cost_usd_est > 0

    unknown = fresh()
    unknown.cost_usd = 9.99  # 上一次的残留，必须被清掉
    run_diagnostics.apply_cost(unknown, resolve_cost(result(model="no-such-model")))
    assert (unknown.cost_source, unknown.cost_usd, unknown.cost_known, unknown.cost_usd_est) == (
        "unknown",
        None,
        False,
        None,
    )


def test_create_version_never_silently_marks_a_known_cost_as_unknown(session, instrument) -> None:
    """R-42 的第二个入口：`create_version()` 漏传 `cost_source` 不该变成"未知成本"。

    过去 `cost_source` 的默认值是 `"unknown"`，而 `record()` 为了维持三态互斥会把
    `usd` 丢弃——于是"传了金额却忘了来源"的结果是一行**静默错标**的账本，
    而 `usd` 永远消失。现在来源由金额推导。
    """
    from app.services import report_service

    report = report_service.create_version(
        session,
        instrument,
        {"schema": "aragon.research.report/v1", "asOf": "2026-03-01"},
        status="ready",
        cost_usd=0.87,
        cost_known=True,
    )
    row = session.execute(select(CostLedger).where(CostLedger.report_id == report.id)).scalar_one()
    assert row.source == "reported"
    assert row.usd == 0.87
    assert row.usd_est is None


def test_cost_ledger_has_no_check_constraint() -> None:
    """V8-P1-7：约束**只在** `cost_service.record()` 的白名单里，ORM 里一个都不许有。

    生产表由 Alembic 建、没有 CHECK；把同一个白名单写成 ORM `CheckConstraint` 的结果是
    "测试库有约束、生产库没有"，正好把"schema 只有一个真源：Alembic"反过来用。
    """
    assert list(CostLedger.__table__.constraints) == [CostLedger.__table__.primary_key]
    for column in CostLedger.__table__.columns:
        assert not list(column.constraints or ())

def test_price_override_reads_the_documented_env_var(monkeypatch) -> None:
    """§20.3.5 的运维出口必须真的能被环境变量打开。

    ★v8 评审：字段叫 `price_override` 时环境变量是 `PRICE_OVERRIDE`，而 `.env.example`、
    `pricing.py` 的注释与设计文档全都写 `ARAGON_PRICE_OVERRIDE`——照着文档配置的结果是
    一个**静默失效**的开关（`extra="ignore"` 把未知键吞掉，估算继续用旧价）。
    所以这条用例从环境变量出发，而不是 monkeypatch `settings.price_override`。
    """
    from app.config import Settings

    monkeypatch.setenv("ARAGON_PRICE_OVERRIDE", '{"deepseek-flash":{"in":9,"out":9}}')
    settings = Settings()
    assert settings.price_overrides["deepseek-flash"] == {"in": 9.0, "out": 9.0}

    # 覆盖表参与 `price_for()`（同一份解析结果，不另存一份）。
    monkeypatch.setattr(get_settings(), "price_override", '{"deepseek-flash":{"in":9,"out":9}}', raising=False)
    assert price_for("deepseek-flash").input_per_1k == 9.0


def test_bad_price_override_is_ignored_not_fatal() -> None:
    """写坏的 JSON 只 warn 不抛——一个配置项写错不该让 API 起不来（§14）。"""
    from app.config import Settings

    assert Settings(price_override="{not json").price_overrides == {}
    assert Settings(price_override='{"x": 1}').price_overrides == {}

