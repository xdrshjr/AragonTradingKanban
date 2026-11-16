"""成本账本 + 预算闸门（spec §19.3.5；AC-55 / AC-56 / AC-57 / AC-62 / AC-63）。

五条 AC 各自对应一条**不可协商**的性质，而不是"端点能返回 200"：

* **`NULL` ≠ `0`**（AC-55）：未知成本落 `usd=NULL, known=0`，汇总里单列 `unknownRuns`；
* **闸门挂在真实入口**（AC-56）：`POST /instruments/{id}/reports` 才是唯一创建入口，
  402 / `?override=1` / `runs.reason` 三条判据都打在它身上；
* **账本失败不回滚报告**（AC-57）：`record()` 自带 savepoint，缺口只进 `ledgerGap`；
* **插位与优先级**（AC-62）：409 先于 402、拒绝不留孤儿工作区、`can_start` 在
  `count_active_runs()` 之后 `mkdir` 之前；
* **`budget.state` 四态**（AC-63）：`unlimited / ok / near / exceeded`。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.errors import MonthlyBudgetExceeded
from app.models import CostLedger, Report, Run
from app.services import cost_service, report_service

#: 价格表里能解析出 `claude-sonnet` 家族的名字（日期后缀不影响，R-27）。
PRICED_MODEL = "claude-sonnet-20260210"


@pytest.fixture(autouse=True)
def _reset_gap_counter():
    """`_LEDGER_GAPS` 是**进程级**计数器：不归零的话 "+1" 断言会随测试顺序漂移。"""
    cost_service.reset_ledger_gaps()
    yield
    cost_service.reset_ledger_gaps()


# ── 写账本：NULL ≠ 0（AC-55） ────────────────────────────────────────────────


def test_unknown_cost_is_stored_as_null_and_known_zero(session) -> None:
    assert cost_service.record(session, kind="run", ref_id="run-unknown", usd=0.0, known=False) is True
    session.commit()

    row = session.execute(select(CostLedger).where(CostLedger.ref_id == "run-unknown")).scalar_one()
    assert row.usd is None, "未知成本必须是 NULL：写成 0 会被读成'这次生成免费'"
    assert row.known == 0


def test_summary_counts_unknown_runs_separately(session) -> None:
    """AC-55：4 次未知 + 2 次已知 → `unknownRuns == 4`，`spentUsd` 只累计已知。"""
    for index in range(4):
        assert cost_service.record(session, kind="run", ref_id=f"run-unknown-{index}", usd=0.0, known=False)
    assert cost_service.record(session, kind="run", ref_id="run-known-1", usd=1.0, known=True)
    assert cost_service.record(session, kind="run", ref_id="run-known-2", usd=0.25, known=True)
    session.commit()

    unknowns = session.execute(select(CostLedger).where(CostLedger.usd.is_(None))).scalars().all()
    assert len(unknowns) == 4
    assert all(row.known == 0 for row in unknowns)

    payload = cost_service.summary(session)
    assert payload["unknownRuns"] == 4
    assert payload["knownRuns"] == 2
    assert payload["spentUsd"] == 1.25, "未知次数连 0 都不是，绝不能折进 spentUsd"


def test_summary_groups_by_kind_and_by_model(session) -> None:
    cost_service.record(session, kind="run", ref_id="run-1", usd=1.5, known=True, model=PRICED_MODEL)
    cost_service.record(session, kind="ask_deep", ref_id="ask-1", usd=0.5, known=True, model="gpt-4o")
    session.commit()

    payload = cost_service.summary(session)
    assert payload["byKind"]["run"] == 1.5
    assert payload["byKind"]["ask_deep"] == 0.5
    assert payload["spentUsd"] == 2.0
    # 按 usd 降序：面板的第一行是"最贵的那一档"
    assert [row["model"] for row in payload["byModel"]] == [PRICED_MODEL, "gpt-4o"]
    assert payload["byModel"][0] == {"model": PRICED_MODEL, "usd": 1.5, "runs": 1}


# ── budget.state 四态（AC-63） ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("budget", "spend", "state", "remaining"),
    [
        (10.0, 1.0, "ok", 9.0),
        (10.0, 8.0, "near", 2.0),  # near = ≥ 80%
        (10.0, 10.5, "exceeded", 0.0),  # 超了也不会给出负数余额
    ],
)
def test_budget_state_for_a_finite_budget(session, budget, spend, state, remaining) -> None:
    cost_service.set_budget(session, budget)
    cost_service.record(session, kind="run", ref_id="run-spend", usd=spend, known=True)
    session.commit()

    block = cost_service.summary(session)["budget"]
    assert block["state"] == state
    assert block["monthlyUsd"] == budget
    assert block["remainingUsd"] == remaining
    assert block["resetAt"].endswith("T00:00:00Z"), "重置时刻是下个月 1 日 00:00Z"


def test_zero_budget_is_unlimited_without_a_fake_remaining(session) -> None:
    """`monthlyUsd = 0` 是**第四态**：`remainingUsd` / `resetAt` 都是 None，不是 0 或 ∞。"""
    cost_service.set_budget(session, 0)
    cost_service.record(session, kind="run", ref_id="run-spend", usd=99.0, known=True)
    session.commit()

    block = cost_service.summary(session)["budget"]
    assert block["state"] == "unlimited"
    assert block["monthlyUsd"] == 0.0
    assert block["remainingUsd"] is None
    assert block["resetAt"] is None


# ── 闸门：can_start 的三个分支 ──────────────────────────────────────────────


class _NoQuerySession:
    """未设预算时 `can_start` 必须**不查库**（默认路径零成本，§19.3.5）。

    `get()` 只答"Setting 里没有覆盖"，任何 `execute()` 都是违约——这正是被测性质。
    """

    def get(self, model, key):  # noqa: ANN001 - 只实现被调用的那两个方法
        return None

    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("未设预算时 can_start 不应查询账本")


def test_can_start_without_a_budget_allows_and_does_not_touch_the_ledger() -> None:
    decision = cost_service.can_start(_NoQuerySession())
    assert decision.allowed is True
    assert decision.detail == {}


def test_can_start_allows_when_spend_plus_estimate_fits(session) -> None:
    cost_service.set_budget(session, 25.0)
    cost_service.record(session, kind="run", ref_id="run-spend", usd=24.0, known=True)
    session.commit()

    decision = cost_service.can_start(session, "run", estimate_usd=0.5)
    assert decision.allowed is True
    assert decision.detail == {}


def test_can_start_rejects_with_the_402_detail(session) -> None:
    """超限时 `detail` 直接就是 402 的响应体：含 `override: true`（用户可以覆盖）。"""
    cost_service.set_budget(session, 25.0)
    cost_service.record(session, kind="run", ref_id="run-spend", usd=24.9, known=True)
    cost_service.record(session, kind="run", ref_id="run-unknown", usd=0.0, known=False)
    session.commit()

    decision = cost_service.can_start(session, "run", estimate_usd=0.2)
    assert decision.allowed is False
    detail = decision.detail
    assert detail["spentUsd"] == 24.9
    assert detail["budgetUsd"] == 25.0
    assert detail["remainingUsd"] == 0.1
    assert detail["estimateUsd"] == 0.2
    assert detail["unknownRuns"] == 1, "未知次数单独给出，绝不折算进 spentUsd"
    assert detail["override"] is True
    assert detail["resetAt"].endswith("T00:00:00Z")
    # 402 是**独立**错误类：不能复用 409 的 turns/duration 预算错误（V6-P0-2）
    assert MonthlyBudgetExceeded.code == "monthly_budget_exceeded"
    assert MonthlyBudgetExceeded.status_code == 402


# ── 账本失败不回滚报告（AC-57） ─────────────────────────────────────────────


def test_record_failure_never_raises_and_counts_a_gap(session) -> None:
    assert cost_service.record(session, kind="run", ref_id="run-dup", usd=0.4, known=True) is True
    session.commit()

    # 同一 `(kind, ref_id)` 再来一次：唯一键拒绝，但调用方不该看到异常
    assert cost_service.record(session, kind="run", ref_id="run-dup", usd=0.9, known=True) is False
    assert cost_service.ledger_gaps() == 1

    rows = session.execute(select(CostLedger).where(CostLedger.ref_id == "run-dup")).scalars().all()
    assert len(rows) == 1
    assert rows[0].usd == 0.4, "重试不能把金额改掉——账本只追加，不更新"
    # savepoint 回滚后外层事务仍健康：这里能正常提交才是"不连坐"的证据
    session.commit()


def test_ledger_failure_does_not_rollback_the_report(session, instrument, sample_report) -> None:
    """AC-57：先占住 `(run, ref_id)`，再让 `create_version` 去写同一行——报告必须照落 `ready`。"""
    # `reports.run_id` 是指向 `runs` 的外键，所以先有一条 succeeded run（非活动态，不占并发位）
    session.add(
        Run(
            id="run-collide",
            instrument_id=instrument.id,
            status="succeeded",
            pct=100,
            started_at="2026-02-09T00:00:00Z",
            cost_known=0,
            workspace_path="workspace/runs/run-collide",
        )
    )
    session.commit()
    assert cost_service.record(session, kind="run", ref_id="run-collide", usd=0.4, known=True) is True
    session.commit()

    report = report_service.create_version(
        session,
        instrument,
        sample_report,
        status="ready",
        run_id="run-collide",
        cost_usd=0.5,
        cost_known=True,
    )

    assert cost_service.ledger_gaps() == 1
    session.expire_all()
    stored = session.get(Report, report.id)
    assert stored is not None, "账本失败绝不能把报告一起回滚"
    assert stored.status == "ready"
    assert stored.cost_usd == 0.5

    rows = session.execute(select(CostLedger).where(CostLedger.ref_id == "run-collide")).scalars().all()
    assert len(rows) == 1, "冲突的那一行被拒了，不能变成两行"


# ── API 层的闸门（AC-56） ───────────────────────────────────────────────────


@pytest.fixture()
def brim_spent(session, instrument, sample_report) -> dict:
    """预算 $25、已知花费 $24.9，且**预估非空** → 闸门必然拒绝。

    预估来自"历史 token × 价格表"：只插一条 succeeded run（12 万 token）+ 一版
    `model=claude-sonnet-…` 的报告，`estimate()` 才算出 $0.72。少了这一步预估是
    `null`，$24.9 ≤ $25 直接放行，402 断言就成了噪音。
    """
    session.add(
        Run(
            id="run-history",
            instrument_id=instrument.id,
            status="succeeded",
            pct=100,
            started_at="2026-02-09T00:00:00Z",
            finished_at="2026-02-09T00:05:00Z",
            cost_known=1,
            tokens_in=100_000,
            tokens_out=20_000,
            workspace_path="workspace/runs/run-history",
        )
    )
    session.commit()
    report = report_service.create_version(
        session,
        instrument,
        sample_report,
        status="ready",
        model=PRICED_MODEL,
        run_id="run-history",
    )
    cost_service.set_budget(session, 25.0)
    assert cost_service.record(
        session, kind="run", ref_id="run-spend", usd=24.9, known=True, symbol=instrument.symbol
    )
    session.commit()

    estimate = cost_service.estimate(session, instrument.symbol, "run")
    assert estimate["estimatedUsd"] == 0.72, "预估必须先成立，否则 24.9 ≤ 25 会放行"
    return {"instrument": instrument, "report": report, "estimate": estimate}


def _create_report(client, instrument_id: str, query: str = ""):
    return client.post(f"/api/v1/instruments/{instrument_id}/reports{query}", json={})


def test_budget_gate_returns_402_with_the_override_hint(client, brim_spent) -> None:
    response = _create_report(client, brim_spent["instrument"].id)
    assert response.status_code == 402
    error = response.json()["error"]
    assert error["code"] == "monthly_budget_exceeded"
    assert error["detail"]["override"] is True
    assert error["detail"]["budgetUsd"] == 25.0
    assert error["detail"]["spentUsd"] == 24.9
    assert error["detail"]["estimateUsd"] == 0.72


def test_override_queues_the_run_and_leaves_a_trace(client, session, brim_spent) -> None:
    response = _create_report(client, brim_spent["instrument"].id, "?override=1")
    assert response.status_code == 202
    assert response.headers["X-Budget-Overridden"] == "1"

    body = response.json()
    assert body["status"] == "queued"
    assert body["estimateUsd"] == 0.72

    run = session.get(Run, body["runId"])
    assert run is not None
    assert run.reason == "budget_override", "覆盖必须留痕，否则事后无法解释这笔花费"


def test_zero_budget_never_returns_402(client, session, brim_spent) -> None:
    cost_service.set_budget(session, 0)
    response = _create_report(client, brim_spent["instrument"].id)
    assert response.status_code == 202
    assert "X-Budget-Overridden" not in response.headers, "没被拦过就不该有覆盖标记"
    assert session.get(Run, response.json()["runId"]).reason == "manual"


# ── 插位与优先级（AC-62） ───────────────────────────────────────────────────


def test_active_run_wins_over_the_budget_gate(client, session, brim_spent) -> None:
    """AC-62 ①：同标已有运行中 → 409 `run_conflict`，不是 402。

    402 说"你本月花超了"，用户会去改预算（无效动作）；409 说"等两分钟"。所以
    既有的冲突检查必须**先于**预算闸门。
    """
    session.add(
        Run(
            id="run-active",
            instrument_id=brim_spent["instrument"].id,
            status="queued",
            pct=0,
            started_at="2026-02-10T00:00:00Z",
            cost_known=0,
            workspace_path="workspace/runs/run-active",
        )
    )
    session.commit()

    response = _create_report(client, brim_spent["instrument"].id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_conflict"
    assert response.json()["error"]["detail"]["activeRunId"] == "run-active"


def test_rejected_request_leaves_no_orphan_workspace(client, session, brim_spent, monkeypatch, tmp_path) -> None:
    """AC-62 ②：402 之后磁盘上不能多出一个空工作区。

    `RUN_RETENTION_DAYS` 只清**有 run 行**的目录，所以"先 mkdir 再拒绝"等于永久垃圾。
    """
    monkeypatch.setattr(get_settings(), "workspace_root", str(tmp_path), raising=False)
    runs_dir = tmp_path / "runs"

    response = _create_report(client, brim_spent["instrument"].id)
    assert response.status_code == 402
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == [], "402 不许留孤儿工作区"

    session.rollback()  # 丢掉可能过期的快照，再确认没有多出来的 run 行
    assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 1, "只该剩下那条历史 run"


def test_gate_is_checked_after_the_conflict_checks_and_before_mkdir(
    client, brim_spent, monkeypatch, tmp_path
) -> None:
    """AC-62 ③：用探针记录调用顺序——`count_active_runs` → `can_start`，然后才 `mkdir`。"""
    monkeypatch.setattr(get_settings(), "workspace_root", str(tmp_path), raising=False)
    order: list[str] = []

    real_count = report_service.count_active_runs

    def counting(session):  # noqa: ANN001 - 探针，签名跟着被测函数走
        order.append("count_active_runs")
        return real_count(session)

    def refusing(session, kind="run", *, estimate_usd=None):  # noqa: ANN001, ANN202
        order.append("can_start")
        return cost_service.BudgetDecision(allowed=False, detail={"override": True})

    monkeypatch.setattr(report_service, "count_active_runs", counting)
    monkeypatch.setattr(cost_service, "can_start", refusing)

    response = _create_report(client, brim_spent["instrument"].id)
    assert response.status_code == 402
    assert order == ["count_active_runs", "can_start"], "闸门必须在两个既有冲突检查之后"
    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == [], "闸门必须在 mkdir 之前"


# ── 预估：`null` 是一个答案（§19.5.4） ──────────────────────────────────────


def test_estimate_endpoint_says_unknown_instead_of_zero(client) -> None:
    response = client.post("/api/v1/runs/estimate", json={"symbol": "NOPE.SH"})
    assert response.status_code == 200, "无法预估是 200，不是错误"
    body = response.json()
    assert body["estimatedUsd"] is None
    assert body["reason"] == "no_history"


def test_estimate_endpoint_uses_history_and_the_price_table(client, brim_spent) -> None:
    response = client.post("/api/v1/runs/estimate", json={"symbol": brim_spent["instrument"].symbol})
    assert response.status_code == 200
    body = response.json()
    assert body["estimatedUsd"] == 0.72
    assert body["reason"] is None
    assert body["basis"]["model"] == PRICED_MODEL
    assert body["basis"]["samples"] == 1


# ── 唯一创建入口（AC-56 的回归门 / C-13） ──────────────────────────────────


def test_post_runs_root_does_not_create_a_run(client, session) -> None:
    """C-13 的实质：`POST /api/v1/runs` 不能建出 run 行。"""
    response = client.post("/api/v1/runs", json={})
    assert response.status_code == 405
    session.rollback()
    assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 0


def test_post_runs_root_is_405(client) -> None:
    """★v6回归门（AC-56 / §19.7.3 第 8 条 / C-13）。

    为什么不是 404：`/runs` 根路径上一个方法都没有时，Starlette 回 404
    （“地址写错了”）；而这里要说的是“地址对了，但这个动作不允许”。
    `runs.py` 因此显式注册了一条不建任何东西的 405 路由，让“没事就补个
    POST /runs”这种事在第一秒就被挡住（R-31）。
    """
    response = client.post("/api/v1/runs", json={})
    assert response.status_code == 405
    body = response.json()
    assert body["error"]["code"] == "method_not_allowed"
    # 错误信息必须告知该去哪里，否则下一个人还是会自己造一个。
    assert "instruments" in body["error"]["message"]
