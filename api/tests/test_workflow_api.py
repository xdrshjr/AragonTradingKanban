"""`/api/v1/admin/workflows*` over HTTP（report-workflow-graph §5.1 / §5.4，M60）。

世界是一个 `enforced` 应用 + 一个 admin 客户端。服务语义（CAS、版本只追加、并发）在
`test_workflow_service.py`；这里钉的是**路由形状**：状态码、错误码、载荷键、字面量路径先于 `/{id}`、
乐观并发的 409 载荷、以及每个写操作确实落审计。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog
from app.services.workflow_builtin import builtin_doc

BASE = "/api/v1/admin/workflows"
BUILTIN = "wf_builtin_standard"


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def admin(auth_client, make_user, login_as) -> TestClient:  # noqa: ANN001
    client = TestClient(auth_client.app)
    login_as(client, make_user("wf_boss", role="admin"))
    return client


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def _step_id() -> str:
    return next(n.id for n in builtin_doc().nodes if n.kind == "step")


def _clone(admin: TestClient, name: str = "副本") -> dict[str, Any]:
    response = admin.post(BASE, json={"name": name, "from": "builtin"})
    assert response.status_code == 201, response.text
    return response.json()


def _rename(admin: TestClient, template_id: str, title: str, revision: int, expect_rev: int = 1):  # noqa: ANN202
    op = {"op": "update_node", "id": _step_id(), "expectRev": expect_rev, "patch": {"title": title}}
    return admin.post(f"{BASE}/{template_id}/ops", json={"baseRevision": revision, "ops": [op]})


def test_the_list_starts_with_the_builtin_default(admin) -> None:  # noqa: ANN001
    body = admin.get(BASE).json()
    assert body["enabled"] is True and body["defaultTemplateId"] == BUILTIN
    [item] = body["items"]
    assert item["isBuiltin"] and item["isDefault"] and item["latestVersion"] == 1 and item["dirty"] is False


def test_literal_paths_win_over_the_id_family(admin) -> None:  # noqa: ANN001
    summary = admin.get(f"{BASE}/default-summary")
    assert summary.status_code == 200 and summary.json()["templateId"] == BUILTIN
    assert set(summary.json()) == {"templateId", "name", "version", "hash", "enabled"}
    assert admin.put(f"{BASE}/enabled", json={"enabled": False}).json() == {"enabled": False}
    assert admin.get(BASE).json()["enabled"] is False


def test_create_returns_the_detail_shape(admin) -> None:  # noqa: ANN001
    detail = _clone(admin)
    assert set(detail) == {"meta", "draft", "versions"}
    assert set(detail["draft"]) == {"doc", "revision", "validation", "dirty"}
    assert detail["draft"]["revision"] == 1 and detail["meta"]["isDefault"] is False
    assert admin.get(f"{BASE}/{detail['meta']['id']}").json()["meta"]["name"] == "副本"


def test_create_rejects_a_bad_source_and_a_missing_template(admin) -> None:  # noqa: ANN001
    assert admin.post(BASE, json={"name": "x", "from": "nonsense"}).status_code == 422
    missing = admin.post(BASE, json={"name": "x", "from": {"templateId": "nope"}})
    assert missing.status_code == 404 and _code(missing) == "workflow_not_found"


def test_ops_round_trip_and_the_409_payload_is_authoritative(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    ok = _rename(admin, template_id, "新标题", 1)
    assert ok.status_code == 200 and ok.json()["revision"] == 2 and ok.json()["changed"] is True
    assert set(ok.json()["validation"]) >= {"issues", "errorCount", "publishable"}
    stale = _rename(admin, template_id, "又改", 1, expect_rev=2)
    assert stale.status_code == 409 and _code(stale) == "workflow_revision_conflict"
    detail = stale.json()["error"]["detail"]
    assert detail["currentRevision"] == 2 and detail["doc"]["nodes"]


def test_invalid_ops_are_a_422_with_the_op_index(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    response = admin.post(
        f"{BASE}/{template_id}/ops",
        json={"baseRevision": 1, "ops": [{"op": "delete_node", "id": "n_ghost", "expectRev": 1}]},
    )
    assert response.status_code == 422 and _code(response) == "workflow_invalid_ops"
    assert response.json()["error"]["detail"]["opIndex"] == 0


def test_the_builtin_is_read_only_over_http(admin) -> None:  # noqa: ANN001
    ops = admin.post(f"{BASE}/{BUILTIN}/ops", json={"baseRevision": 1, "ops": []})
    assert ops.status_code == 409 and _code(ops) == "workflow_builtin_readonly"
    assert _code(admin.delete(f"{BASE}/{BUILTIN}")) == "workflow_builtin_readonly"
    assert _code(admin.patch(f"{BASE}/{BUILTIN}", json={"name": "x"})) == "workflow_builtin_readonly"
    assert _code(admin.post(f"{BASE}/{BUILTIN}/publish", json={"baseRevision": 1})) == "workflow_builtin_readonly"


def test_publish_set_default_versions_diff_and_usage(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    published = admin.post(
        f"{BASE}/{template_id}/publish", json={"baseRevision": 1, "changeNote": "首发", "setDefault": True}
    )
    assert published.status_code == 201
    body = published.json()
    assert body["version"] == 1 and body["isDefault"] is True and body["changeNote"] == "首发"
    assert admin.get(BASE).json()["defaultTemplateId"] == template_id
    again = admin.post(f"{BASE}/{template_id}/publish", json={"baseRevision": 1})
    assert again.status_code == 409 and _code(again) == "workflow_unchanged"

    _rename(admin, template_id, "改名", 1)
    assert admin.post(f"{BASE}/{template_id}/publish", json={"baseRevision": 2}).status_code == 201
    listing = admin.get(f"{BASE}/{template_id}/versions").json()
    assert [v["version"] for v in listing["items"]] == [2, 1] and listing["next"] is None
    one = admin.get(f"{BASE}/{template_id}/versions/2").json()
    assert set(one) == {"summary", "doc", "diffVsPrevious"}
    assert [n["id"] for n in one["diffVsPrevious"]["changedNodes"]] == [_step_id()]
    assert admin.get(f"{BASE}/{template_id}/versions/9").status_code == 404
    usage = admin.get(f"{BASE}/{template_id}/usage").json()
    assert [row["version"] for row in usage["byVersion"]] == [1, 2]
    assert set(usage["byVersion"][0]) == {"version", "runs", "succeeded", "failed", "adherence", "fallbacks"}


def test_publishing_an_invalid_graph_is_a_422_with_the_report(admin) -> None:  # noqa: ANN001
    blank = admin.post(BASE, json={"name": "空白", "from": "blank"}).json()
    response = admin.post(f"{BASE}/{blank['meta']['id']}/publish", json={"baseRevision": 1})
    assert response.status_code == 422 and _code(response) == "workflow_invalid"
    assert "issues" in response.json()["error"]["detail"]["report"]


def test_set_default_before_publishing_is_a_409(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    response = admin.post(f"{BASE}/{template_id}/set-default")
    assert response.status_code == 409 and _code(response) == "workflow_not_published"


def test_rollback_restore_and_archive(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    admin.post(f"{BASE}/{template_id}/publish", json={"baseRevision": 1})
    _rename(admin, template_id, "改名", 1)
    restored = admin.post(f"{BASE}/{template_id}/versions/1/restore")
    assert restored.status_code == 200 and restored.json() == {"revision": 3}
    rolled = admin.post(f"{BASE}/{template_id}/versions/1/rollback", json={})
    assert rolled.status_code in (201, 409)  # 草稿即 v1 内容 -> unchanged 也是合法语义
    assert admin.delete(f"{BASE}/{template_id}").json() == {"status": "archived"}
    assert [i["id"] for i in admin.get(f"{BASE}?status=archived").json()["items"]] == [template_id]
    assert _code(_rename(admin, template_id, "x", 3, expect_rev=1)) == "workflow_archived"


def test_the_default_cannot_be_archived_over_http(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    admin.post(f"{BASE}/{template_id}/publish", json={"baseRevision": 1, "setDefault": True})
    response = admin.delete(f"{BASE}/{template_id}")
    assert response.status_code == 409 and _code(response) == "workflow_is_default"


def test_put_draft_resets_and_checks_the_revision(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    doc = builtin_doc().model_dump(mode="json")
    stale = admin.put(f"{BASE}/{template_id}/draft", json={"baseRevision": 5, "doc": doc})
    assert stale.status_code == 409
    ok = admin.put(f"{BASE}/{template_id}/draft", json={"baseRevision": 1, "doc": doc})
    assert ok.status_code == 200 and ok.json()["revision"] == 2
    bad = admin.put(f"{BASE}/{template_id}/draft", json={"baseRevision": 2, "doc": {"nodes": "x"}})
    assert bad.status_code == 422


def test_validate_and_preview(admin) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    report = admin.post(f"{BASE}/{template_id}/validate").json()
    assert report["publishable"] is True
    inline = admin.post(f"{BASE}/{template_id}/validate", json={"doc": {"nodes": [], "edges": []}}).json()
    assert inline["publishable"] is False
    preview = admin.get(f"{BASE}/{template_id}/preview").json()
    assert preview["compilable"] and preview["planMd"] and preview["budget"]["limit"] > 0
    assert admin.get(f"{BASE}/nope/preview").status_code == 404


def test_chat_is_per_admin_and_clearable(admin, maker) -> None:  # noqa: ANN001
    from app.services import workflow_service

    template_id = _clone(admin)["meta"]["id"]
    with maker() as db:
        user_id = admin.get("/api/v1/auth/me").json()["user"]["id"]
        workflow_service.add_chat_message(db, template_id, user_id, "user", "你好")
        workflow_service.add_chat_message(db, template_id, "someone-else", "user", "别人")
        db.commit()
    items = admin.get(f"{BASE}/{template_id}/chat").json()["items"]
    assert [m["content"] for m in items] == ["你好"] and set(items[0]) >= {"id", "role", "content", "createdAt"}
    assert admin.delete(f"{BASE}/{template_id}/chat").status_code == 204
    assert admin.get(f"{BASE}/{template_id}/chat").json()["items"] == []


def test_every_write_leaves_the_right_audit_row(admin, maker) -> None:  # noqa: ANN001
    template_id = _clone(admin)["meta"]["id"]
    _rename(admin, template_id, "改名", 1)  # 草稿编辑不入审计
    admin.post(f"{BASE}/{template_id}/publish", json={"baseRevision": 2, "setDefault": True})
    admin.put(f"{BASE}/enabled", json={"enabled": False})
    with maker() as db:
        actions = [row.action for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()]
    assert actions.count("workflow.create") == 1 and actions.count("workflow.publish") == 1
    assert "settings.update" in actions and not [a for a in actions if "ops" in a]
