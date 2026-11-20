"""多轮上下文的授权、固定字节预算与运行快照。"""

from __future__ import annotations

import json

import pytest

from app.models import ReportAsk
from app.security.principal import service_principal, user_principal
from app.services import ask_context, report_service


@pytest.fixture()
def context_report(session, instrument, sample_report):
    return report_service.create_version(
        session, instrument, sample_report, status="ready"
    )


def _actor(user_id="u1", role="user"):
    return user_principal(
        user_id=user_id,
        username=user_id,
        role=role,
        session_id=None,
        must_change_password=False,
    )


def _turn(session, report, ask_id="a1", **changes):
    fields = dict(
        id=ask_id,
        report_id=report.id,
        user_id="u1",
        question="主要风险？",
        mode="deep",
        status="done",
        grounded=1,
        created_at="2026-11-20T00:00:00Z",
        answer_json=json.dumps(
            {
                "answerText": "报告依据[S1]",
                "citations": [{"sourceId": "S1"}, {"sourceId": "S99"}],
            }
        ),
    )
    fields.update(changes)
    row = ReportAsk(**fields)
    session.add(row)
    session.commit()
    return row


def test_context_orders_and_filters_to_current_source_whitelist(
    session, context_report
):
    _turn(session, context_report, "a2")
    _turn(session, context_report, "a1")
    result = ask_context.load_context(
        session, context_report, ["a2", "a1"], _actor()
    )
    assert result.ids == ["a1", "a2"]
    payload = json.loads(result.content)
    assert payload["reportId"] == context_report.id
    assert payload["turns"][0]["sourceIds"] == ["S1"]
    assert len(result.content) <= 16384


@pytest.mark.parametrize("role", ["user", "admin", "root"])
def test_context_cannot_read_another_owner_even_as_admin(
    session, context_report, role
):
    from app.errors import NotFound

    _turn(session, context_report, user_id="u2")
    with pytest.raises(NotFound):
        ask_context.load_context(
            session, context_report, ["a1"], _actor(role=role)
        )


def test_authorization_precedes_readiness_for_entire_batch(
    session, context_report
):
    from app.errors import NotFound

    _turn(session, context_report, "own", status="running")
    _turn(session, context_report, "other", user_id="u2")
    with pytest.raises(NotFound):
        ask_context.load_context(
            session, context_report, ["own", "other"], _actor()
        )


@pytest.mark.parametrize(
    "changes", [{"status": "running"}, {"grounded": 0}, {"answer_json": "{}"}]
)
def test_context_rejects_unready_or_unusable_turns(
    session, context_report, changes
):
    from app.errors import InvalidAskContext

    _turn(session, context_report, **changes)
    with pytest.raises(InvalidAskContext) as error:
        ask_context.load_context(session, context_report, ["a1"], _actor())
    assert error.value.detail["reason"] == "not_ready"


def test_open_context_only_reads_null_owner(session, context_report):
    from app.errors import NotFound

    _turn(session, context_report, "null", user_id=None)
    _turn(session, context_report, "owned")
    actor = service_principal(open_mode=True)
    assert ask_context.load_context(
        session, context_report, ["null"], actor
    ).ids == ["null"]
    with pytest.raises(NotFound):
        ask_context.load_context(session, context_report, ["owned"], actor)


def test_budget_prunes_oldest_whole_turn_and_strict_mode_refuses(
    session, context_report
):
    from app.errors import InvalidAskContext

    for index in range(6):
        _turn(
            session,
            context_report,
            f"a{index}",
            question="问" * 500,
            answer_json=json.dumps({"answerText": "答" * 1200}),
        )
    ids = [f"a{i}" for i in range(6)]
    result = ask_context.load_context(session, context_report, ids, _actor())
    assert result.ids[-1] == "a5"
    assert len(result.ids) < 6
    assert len(result.content) <= 16384
    assert [
        turn["askId"] for turn in json.loads(result.content)["turns"]
    ] == result.ids
    with pytest.raises(InvalidAskContext):
        ask_context.load_context(
            session, context_report, ids, _actor(), strict=True
        )


def test_even_last_turn_must_fit_complete_outer_budget(session, context_report):
    from app.errors import InvalidAskContext

    _turn(session, context_report, question="问" * 6000)
    with pytest.raises(InvalidAskContext) as error:
        ask_context.load_context(session, context_report, ["a1"], _actor())
    assert error.value.detail["reason"] == "too_large"


def test_context_rejects_missing_and_cross_version(
    session, context_report, instrument, sample_report
):
    from app.errors import NotFound

    _turn(session, context_report)
    newer = report_service.create_version(
        session, instrument, sample_report, status="ready"
    )
    for report, ids in [(context_report, ["missing"]), (newer, ["a1"])]:
        with pytest.raises(NotFound):
            ask_context.load_context(session, report, ids, _actor())


def test_cite_context_limits_hits_and_filters_source_ids(
    session, context_report
):
    hits = [
        {
            "excerpt": "文" * 900,
            "citations": ["S1", "S99"],
            "blockId": "thesis#0",
        }
    ] * 5
    _turn(
        session,
        context_report,
        mode="cite",
        answer_json=json.dumps({"hits": hits}),
    )
    result = ask_context.load_context(session, context_report, ["a1"], _actor())
    turn = json.loads(result.content)["turns"][0]
    assert len(turn["hits"]) == 3
    assert all(len(hit["excerpt"]) == 400 for hit in turn["hits"])
    assert all(hit["citations"] == ["S1"] for hit in turn["hits"])


def test_runtime_rejects_corrupt_persisted_ids_and_writes_exact_bytes(
    session, context_report, tmp_path
):
    from app.errors import InvalidAskContext

    _turn(session, context_report)
    queued = _turn(
        session, context_report, "new", context_ask_ids_json='["a1"]'
    )
    assert (
        ask_context.write_context(session, context_report, queued, tmp_path)
        is True
    )
    content = (tmp_path / "conversation-context.json").read_bytes()
    assert b"\r\n" not in content
    assert [turn["askId"] for turn in json.loads(content)["turns"]] == ["a1"]
    queued.context_ask_ids_json = "{bad"
    with pytest.raises(InvalidAskContext):
        ask_context.write_context(session, context_report, queued, tmp_path)


@pytest.mark.parametrize("raw", [None, "[]", "{bad", "null", "[1]", '["a","a"]'])
def test_historical_read_property_is_tolerant_of_legacy_or_corrupt_context(raw):
    assert ReportAsk(id="history", context_ask_ids_json=raw).context_ask_ids == []
