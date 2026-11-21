"""研究流程服务（report-workflow-graph §3.7，M60）：模板/草稿/版本/默认的语义与并发纪律。

并发用例（AC-W38）是本文件的重点：路由是同步 `def`、在线程池里真并发，SQLite 又忽略
`SELECT … FOR UPDATE`——所以正确性只来自 CAS、DB 唯一约束与短事务。每个线程各持独立 `Session`，
用 `threading.Barrier` 对齐起跑线，断言"恰一个成功、另一个 409、无 500、无丢失更新、默认恰一个"。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from app.errors import (
    AppError,
    WorkflowArchived,
    WorkflowBuiltinReadonly,
    WorkflowInvalid,
    WorkflowInvalidOps,
    WorkflowIsDefault,
    WorkflowLimitReached,
    WorkflowNotFound,
    WorkflowNotPublished,
    WorkflowRevisionConflict,
    WorkflowUnchanged,
)
from app.models import AuditLog, WorkflowTemplate, WorkflowVersion
from app.security.principal import Principal
from app.services import settings_service, workflow_views
from app.services import workflow_contract as C
from app.services import workflow_service as svc
from app.services.workflow_builtin import builtin_doc

ADMIN = Principal(kind="user", user_id="u_admin", username="boss", role="admin", permissions=frozenset())


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db(maker):  # noqa: ANN001, ANN201
    with maker() as session:
        svc.ensure_builtin(session)
        yield session


def _clone(db, name: str = "副本") -> str:  # noqa: ANN001
    return svc.create(db, ADMIN, name, "", "builtin").meta.id


def _first_step_id() -> str:
    return next(n.id for n in builtin_doc().nodes if n.kind == "step")


def _rename(db, template_id: str, title: str, *, revision: int, expect_rev: int = 1) -> dict[str, Any]:  # noqa: ANN001
    op = {"op": "update_node", "id": _first_step_id(), "expectRev": expect_rev, "patch": {"title": title}}
    return svc.apply_ops(db, ADMIN, template_id, revision, [op])


# ── built-in template ────────────────────────────────────────────────────────


def test_ensure_builtin_creates_a_default_and_is_idempotent(db) -> None:  # noqa: ANN001
    row = db.get(WorkflowTemplate, C.BUILTIN_TEMPLATE_ID)
    assert row is not None and row.is_builtin and row.is_default and row.current_version_id
    svc.ensure_builtin(db)
    svc.ensure_builtin(db)
    versions = db.execute(select(func.count()).select_from(WorkflowVersion)).scalar_one()
    assert versions == 1


def test_ensure_builtin_never_steals_an_existing_default(maker) -> None:  # noqa: ANN001
    """RW-11：已有别的默认时，内置行只插入、不抢指针（否则撞 `ux_workflow_default` 使启动失败）。"""
    with maker() as session:
        session.add(
            WorkflowTemplate(
                id="wf_other", name="别人的", draft_doc_json="{}", is_default=1, created_at="t", updated_at="t"
            )
        )
        session.commit()
        svc.ensure_builtin(session)
        builtin = session.get(WorkflowTemplate, C.BUILTIN_TEMPLATE_ID)
        assert builtin is not None and not builtin.is_default
        assert session.get(WorkflowTemplate, "wf_other").is_default


def test_a_changed_builtin_document_appends_a_version_and_keeps_the_pointer(db, monkeypatch) -> None:  # noqa: ANN001
    doc = builtin_doc()
    doc.nodes[1].title = "代码升级后的标题"
    monkeypatch.setattr(svc, "builtin_doc", lambda: doc)
    svc.ensure_builtin(db)
    versions = db.execute(select(WorkflowVersion).order_by(WorkflowVersion.version)).scalars().all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].change_note == C.BUILTIN_VERSION_NOTE
    assert db.get(WorkflowTemplate, C.BUILTIN_TEMPLATE_ID).is_default


def test_the_builtin_template_is_read_only(db) -> None:  # noqa: ANN001
    with pytest.raises(WorkflowBuiltinReadonly):
        svc.apply_ops(db, ADMIN, C.BUILTIN_TEMPLATE_ID, 1, [])
    with pytest.raises(WorkflowBuiltinReadonly):
        svc.publish(db, ADMIN, C.BUILTIN_TEMPLATE_ID, 1, "", False)
    with pytest.raises(WorkflowBuiltinReadonly):
        svc.archive(db, ADMIN, C.BUILTIN_TEMPLATE_ID)
    with pytest.raises(WorkflowBuiltinReadonly):
        svc.patch_meta(db, ADMIN, C.BUILTIN_TEMPLATE_ID, "改名", None)


# ── create / drafts ──────────────────────────────────────────────────────────


def test_create_blank_builtin_and_clone_start_unpublished_and_not_default(db) -> None:  # noqa: ANN001
    blank = svc.create(db, ADMIN, "空白", "", "blank")
    clone = svc.create(db, ADMIN, "克隆", "", {"templateId": C.BUILTIN_TEMPLATE_ID})
    for made in (blank, clone):
        assert made.draft.revision == 1 and not made.meta.isDefault and not made.meta.isBuiltin
        assert made.meta.latestVersion is None and made.versions == []
    assert blank.draft.validation.blank
    assert clone.meta.stepCount == len([n for n in builtin_doc().nodes if n.kind == "step"])


def test_active_template_limit_is_a_409(db, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(C, "MAX_ACTIVE_TEMPLATES", 2)
    svc.create(db, ADMIN, "一", "", "blank")  # builtin + 一 = 2
    with pytest.raises(WorkflowLimitReached):
        svc.create(db, ADMIN, "二", "", "blank")


def test_structural_ops_raise_the_revision_and_moves_do_not(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    renamed = _rename(db, template_id, "新标题", revision=1)
    assert renamed["revision"] == 2 and renamed["changed"] is True
    move = {"op": "move_node", "id": _first_step_id(), "position": {"x": 11, "y": 22}}
    moved = svc.apply_ops(db, ADMIN, template_id, 1, [move])  # 过期的 baseRevision 对移动放宽
    assert moved["revision"] == 2
    detail = svc.detail(db, template_id)
    node = next(n for n in detail.draft.doc.nodes if n.id == _first_step_id())
    assert (node.position.x, node.position.y) == (11, 22) and node.title == "新标题" and node.rev == 2


def test_a_stale_base_revision_is_a_409_carrying_the_authoritative_draft(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    _rename(db, template_id, "甲", revision=1)
    with pytest.raises(WorkflowRevisionConflict) as caught:
        _rename(db, template_id, "乙", revision=1, expect_rev=2)
    detail = caught.value.detail
    assert detail["currentRevision"] == 2 and detail["doc"]["schemaVersion"] == 1


def test_an_entity_rev_conflict_lists_the_entity(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    _rename(db, template_id, "甲", revision=1)
    with pytest.raises(WorkflowRevisionConflict) as caught:
        _rename(db, template_id, "乙", revision=2, expect_rev=1)
    assert caught.value.detail["conflicts"][0]["entityId"] == _first_step_id()


def test_an_invalid_op_reports_its_index_and_changes_nothing(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    ops = [
        {"op": "update_node", "id": _first_step_id(), "expectRev": 1, "patch": {"title": "好"}},
        {"op": "delete_node", "id": "n_nonexistent", "expectRev": 1},
    ]
    with pytest.raises(WorkflowInvalidOps) as caught:
        svc.apply_ops(db, ADMIN, template_id, 1, ops)
    assert caught.value.detail["opIndex"] == 1
    assert svc.detail(db, template_id).draft.revision == 1


def test_a_noop_batch_does_not_write(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    node = next(n for n in builtin_doc().nodes if n.id == _first_step_id())
    same = {"op": "move_node", "id": node.id, "position": node.position.model_dump()}
    result = svc.apply_ops(db, ADMIN, template_id, 1, [same])
    assert result["changed"] is False and result["revision"] == 1


def test_an_archived_template_refuses_edits(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    svc.archive(db, ADMIN, template_id)
    with pytest.raises(WorkflowArchived):
        _rename(db, template_id, "x", revision=1)
    assert svc.archive(db, ADMIN, template_id) is None  # 幂等


def test_unknown_template_is_not_found(db) -> None:  # noqa: ANN001
    with pytest.raises(WorkflowNotFound):
        svc.detail(db, "nope")


# ── publish / versions ───────────────────────────────────────────────────────


def test_a_blank_template_cannot_be_published(db) -> None:  # noqa: ANN001
    blank = svc.create(db, ADMIN, "空白", "", "blank")
    with pytest.raises(WorkflowInvalid) as caught:
        svc.publish(db, ADMIN, blank.meta.id, 1, "", False)
    assert caught.value.detail["report"]["errorCount"] > 0 or caught.value.detail["report"]["blank"]


def test_publish_appends_versions_and_refuses_an_unchanged_draft(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    first = svc.publish(db, ADMIN, template_id, 1, "首发", False)
    assert first["version"] == 1 and first["isDefault"] is False and first["changeNote"] == "首发"
    with pytest.raises(WorkflowUnchanged):
        svc.publish(db, ADMIN, template_id, 1, "", False)
    _rename(db, template_id, "新", revision=1)
    second = svc.publish(db, ADMIN, template_id, 2, "二版", False)
    assert second["version"] == 2 and second["hash"] != first["hash"]
    detail = svc.detail(db, template_id)
    assert detail.meta.latestVersion == 2 and detail.meta.dirty is False
    assert [v.version for v in detail.versions] == [2, 1]


def test_moving_a_node_does_not_light_up_unpublished_changes(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    svc.publish(db, ADMIN, template_id, 1, "", False)
    move = {"op": "move_node", "id": _first_step_id(), "position": {"x": 1, "y": 2}}
    svc.apply_ops(db, ADMIN, template_id, 1, [move])
    assert svc.detail(db, template_id).meta.dirty is False


def test_publish_with_a_stale_base_revision_is_a_409(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    _rename(db, template_id, "新", revision=1)
    with pytest.raises(WorkflowRevisionConflict):
        svc.publish(db, ADMIN, template_id, 1, "", False)


def test_publish_can_set_the_default_in_the_same_transaction(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    out = svc.publish(db, ADMIN, template_id, 1, "", True)
    assert out["isDefault"] is True
    defaults = db.execute(select(WorkflowTemplate.id).where(WorkflowTemplate.is_default == 1)).scalars().all()
    assert defaults == [template_id]
    audit = db.execute(select(AuditLog).where(AuditLog.action == "workflow.publish")).scalar_one()
    assert '"previousDefault":"wf_builtin_standard"' in (audit.detail_json or "")


def test_set_default_needs_a_published_active_template(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    with pytest.raises(WorkflowNotPublished):
        svc.set_default(db, ADMIN, template_id)
    svc.publish(db, ADMIN, template_id, 1, "", False)
    assert svc.set_default(db, ADMIN, template_id) == {"defaultTemplateId": template_id, "version": 1}
    assert svc.default_summary(db)["templateId"] == template_id


def test_the_default_cannot_be_archived(db) -> None:  # noqa: ANN001
    with pytest.raises(WorkflowBuiltinReadonly):
        svc.archive(db, ADMIN, C.BUILTIN_TEMPLATE_ID)
    template_id = _clone(db)
    svc.publish(db, ADMIN, template_id, 1, "", True)
    with pytest.raises(WorkflowIsDefault):
        svc.archive(db, ADMIN, template_id)


def test_rollback_publishes_a_new_version_and_resets_the_draft(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    v1 = svc.publish(db, ADMIN, template_id, 1, "", False)
    _rename(db, template_id, "改了", revision=1)
    svc.publish(db, ADMIN, template_id, 2, "", False)
    out = svc.rollback(db, ADMIN, template_id, 1, "")
    assert out.version == 3 and out.hash == v1["hash"] and out.changeNote == "回滚到 v1"
    detail = svc.detail(db, template_id)
    assert detail.meta.dirty is False
    node = next(n for n in detail.draft.doc.nodes if n.id == _first_step_id())
    assert node.title != "改了"


def test_restore_to_draft_does_not_publish(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    svc.publish(db, ADMIN, template_id, 1, "", False)
    _rename(db, template_id, "改了", revision=1)
    out = svc.restore_to_draft(db, ADMIN, template_id, 1)
    assert out["revision"] == 3
    assert svc.detail(db, template_id).meta.latestVersion == 1


def test_the_version_limit_is_a_409(db, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(C, "MAX_VERSIONS_PER_TEMPLATE", 1)
    template_id = _clone(db)
    svc.publish(db, ADMIN, template_id, 1, "", False)
    _rename(db, template_id, "改了", revision=1)
    with pytest.raises(WorkflowLimitReached):
        svc.publish(db, ADMIN, template_id, 2, "", False)


def test_versions_are_append_only(engine, db) -> None:  # noqa: ANN001
    """整个服务层不得对 `workflow_versions` 发出 UPDATE / DELETE（SQL 事件监听断言）。"""
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _spy(_conn, _cursor, statement, *_rest):  # noqa: ANN001, ANN202
        statements.append(statement)

    template_id = _clone(db)
    svc.publish(db, ADMIN, template_id, 1, "", True)
    _rename(db, template_id, "改了", revision=1)
    svc.publish(db, ADMIN, template_id, 2, "", False)
    svc.rollback(db, ADMIN, template_id, 1, "")
    svc.restore_to_draft(db, ADMIN, template_id, 2)
    svc.archive(db, ADMIN, _clone(db, "另一个"))
    svc.ensure_builtin(db)
    event.remove(engine, "before_cursor_execute", _spy)
    touching = [
        s for s in statements if "workflow_versions" in s and s.lstrip().upper().startswith(("UPDATE", "DELETE"))
    ]
    assert touching == []


# ── audit ────────────────────────────────────────────────────────────────────


def test_audit_rows_exist_for_the_five_actions_and_not_for_draft_edits(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    _rename(db, template_id, "x", revision=1)
    svc.publish(db, ADMIN, template_id, 2, "", False)
    svc.set_default(db, ADMIN, template_id)
    _rename(db, template_id, "y", revision=2, expect_rev=2)
    svc.publish(db, ADMIN, template_id, 3, "", False)
    svc.rollback(db, ADMIN, template_id, 1, "")
    other = _clone(db, "另一个")
    svc.archive(db, ADMIN, other)
    actions = [row.action for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()]
    assert {a for a in actions if a.startswith("workflow.")} == {
        "workflow.create",
        "workflow.publish",
        "workflow.default.set",
        "workflow.rollback",
        "workflow.archive",
    }
    assert actions.count("workflow.publish") == 2 and actions.count("workflow.rollback") == 1


def test_set_enabled_is_audited_as_a_settings_update(db) -> None:  # noqa: ANN001
    assert svc.set_enabled(db, ADMIN, False) is False
    assert settings_service.workflow_enabled(db) is False
    row = db.execute(select(AuditLog).where(AuditLog.action == "settings.update")).scalar_one()
    assert '"workflow.enabled"' in (row.detail_json or "")
    svc.set_enabled(db, ADMIN, False)  # 未变化 -> 不再记
    assert (
        db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.action == "settings.update")).scalar_one()
        == 1
    )


# ── views ────────────────────────────────────────────────────────────────────


def test_version_diff_lists_changed_nodes_and_edges(db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    svc.publish(db, ADMIN, template_id, 1, "", False)
    _rename(db, template_id, "改名", revision=1)
    svc.publish(db, ADMIN, template_id, 2, "", False)
    payload = workflow_views.version_with_diff(db, svc.get_version(db, template_id, 2))
    assert [n["id"] for n in payload["diffVsPrevious"]["changedNodes"]] == [_first_step_id()]
    first = workflow_views.version_with_diff(db, svc.get_version(db, template_id, 1))
    assert first["diffVsPrevious"]["addedNodes"] and not first["diffVsPrevious"]["removedNodes"]


def test_usage_aggregates_runs_by_version(db, instrument) -> None:  # noqa: ANN001
    from app.db.base import iso
    from app.models import Run, RunEvent

    template_id = _clone(db)
    version = svc.publish(db, ADMIN, template_id, 1, "", False)
    for index, (status, adherence) in enumerate([("succeeded", "full"), ("succeeded", "partial"), ("failed", None)]):
        db.add(
            Run(
                id=f"r{index}",
                instrument_id=instrument.id,
                status=status,
                started_at=iso(),
                workflow_version_id=version["id"],
                workflow_adherence=adherence,
            )
        )
    db.commit()
    stat = svc.workflow_views.usage(db, template_id, 30)["byVersion"][0]
    assert (stat["runs"], stat["succeeded"], stat["failed"]) == (3, 2, 1)
    assert stat["adherence"] == {"full": 1, "partial": 1, "none": 0}
    assert stat["fallbacks"] == 0
    db.add(
        RunEvent(
            run_id="r1",
            type="run.log",
            payload_json='{"level": "warn", "code": "workflow_fallback", "reason": "doc_corrupt"}',
            created_at=iso(),
        )
    )
    db.commit()
    assert svc.workflow_views.usage(db, template_id, 30)["byVersion"][0]["fallbacks"] == 1
    assert svc.detail(db, template_id).meta.runs30d == 3


def test_chat_history_keeps_only_the_latest_hundred_per_admin(db, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(C, "CHAT_KEPT", 5)
    template_id = _clone(db)
    for index in range(8):
        svc.add_chat_message(db, template_id, "u1", "user", f"m{index}")
    svc.add_chat_message(db, template_id, "u2", "user", "别人的")
    db.commit()
    mine = svc.list_chat(db, template_id, "u1")
    assert [m.content for m in mine] == ["m3", "m4", "m5", "m6", "m7"]
    svc.clear_chat(db, template_id, "u1")
    assert svc.list_chat(db, template_id, "u1") == []
    assert len(svc.list_chat(db, template_id, "u2")) == 1


def test_preview_compiles_the_sample_instrument(db) -> None:  # noqa: ANN001
    out = svc.preview(db, C.BUILTIN_TEMPLATE_ID)
    assert out["compilable"] and "S1" in out["planMd"] and out["bytes"] > 0
    assert out["budget"]["promptTotal"] < out["budget"]["limit"]


# ── AC-W38: two threads, independent sessions ────────────────────────────────


def _race(maker, work: list[Callable[[Any], Any]]) -> list[Any]:  # noqa: ANN001
    """Run each callable in its own thread + `Session`, released together by a `Barrier`.
    Returns `result | exception` per callable."""
    barrier = threading.Barrier(len(work))
    out: list[Any] = [None] * len(work)

    def runner(index: int) -> None:
        with maker() as session:
            barrier.wait(timeout=10)
            try:
                out[index] = work[index](session)
            except Exception as exc:  # noqa: BLE001 - the test classifies them
                out[index] = exc

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(len(work))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return out


def _is_conflict(result: Any) -> bool:
    return isinstance(result, (WorkflowRevisionConflict, WorkflowUnchanged))


def test_two_concurrent_apply_ops_on_one_base_revision_one_wins(maker, db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    out = _race(
        maker,
        [
            lambda s: _rename(s, template_id, "甲", revision=1),
            lambda s: _rename(s, template_id, "乙", revision=1),
        ],
    )
    winners = [r for r in out if isinstance(r, dict)]
    losers = [r for r in out if isinstance(r, WorkflowRevisionConflict)]
    assert len(winners) == 1 and len(losers) == 1, out
    detail = svc.detail(db, template_id)
    assert detail.draft.revision == 2
    node = next(n for n in detail.draft.doc.nodes if n.id == _first_step_id())
    assert node.title in ("甲", "乙") and node.rev == 2  # 无丢失更新：只应用了一个


def test_two_concurrent_publishes_of_one_draft_make_exactly_one_version(maker, db) -> None:  # noqa: ANN001
    template_id = _clone(db)
    out = _race(
        maker,
        [
            lambda s: svc.publish(s, ADMIN, template_id, 1, "a", False),
            lambda s: svc.publish(s, ADMIN, template_id, 1, "b", False),
        ],
    )
    created = [r for r in out if isinstance(r, dict)]
    assert len(created) == 1 and all(_is_conflict(r) for r in out if not isinstance(r, dict)), out
    numbers = (
        db.execute(
            select(WorkflowVersion.version)
            .where(WorkflowVersion.template_id == template_id)
            .order_by(WorkflowVersion.version)
        )
        .scalars()
        .all()
    )
    assert numbers == [1]


def test_concurrent_set_default_of_two_templates_leaves_exactly_one_default(maker, db) -> None:  # noqa: ANN001
    first, second = _clone(db, "甲"), _clone(db, "乙")
    for template_id in (first, second):
        svc.publish(db, ADMIN, template_id, 1, "", False)
    out = _race(maker, [lambda s: svc.set_default(s, ADMIN, first), lambda s: svc.set_default(s, ADMIN, second)])
    assert not [r for r in out if isinstance(r, Exception) and not isinstance(r, AppError)], out
    db.expire_all()
    defaults = db.execute(select(WorkflowTemplate.id).where(WorkflowTemplate.is_default == 1)).scalars().all()
    assert len(defaults) == 1 and defaults[0] in (first, second)
