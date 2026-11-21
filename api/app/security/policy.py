"""The default-deny policy table: one row per route (spec §24.3.3, Appendix A).

Every `/api/v1` route must be listed here. `verify_against_app()` runs at the end of
`create_app()` and **refuses to start** on any difference between this table and the
application's real routes (both directions), and `tests/test_policy_table.py` asserts the
same again so the check cannot be deleted quietly. Forgetting a route is the classic
authorisation bug; this turns it into a boot failure.

Three details the spec pins down (RV-4):

* **Keys carry the `/api/v1` prefix.** Appendix A omits it for readability; `_api()` adds
  it, and `verify_against_app()` compares the full `path_format`.
* **The exemption rule is "not under `/api/v1`"**, not a hand-written list. The five root
  routes (`/`, `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, `/redoc`) are exempt
  because of where they are, so a sixth FastAPI documentation route can never brick boot.
  One further prefix is exempt by design: `/api/v1/auth/_test/` (the e2e captcha hook,
  mounted only with `AUTH_TEST_HOOKS=1` and covered by its own "404 by default" test).
* **Path-parameter names are the routes' own** (`{report_id}`, `{ask_id}` …), not
  Appendix A's generic `{id}`; the check is what forces them to agree.

`PENDING_ROUTES` is the one temporary escape hatch: Appendix A lists `/me/*` (M48) and
`/admin/*` (M50) routes that do not exist yet. They are registered in the table so the
permission vocabulary and the 4-principal matrix are already pinned, and this set tells
`verify_against_app()` not to demand a matching real route. **Whoever implements a route
deletes its entry here** — leaving a stale entry for a route that now exists is itself a
startup error, so the list can only shrink.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .permissions import API_PREFIX

#: Routes under this prefix are not subject to the table (see module docstring).
EXEMPT_PREFIXES: tuple[str, ...] = (f"{API_PREFIX}/auth/_test/",)

#: Methods Starlette adds on its own; never part of the table.
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})

RouteKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Policy:
    """`permission` is a vocabulary string from `contracts/permissions.json`, or `"public"`.

    `rate` names a limiter scope applied by `enforce()` per client IP (guest-only for the
    `guest.*` scopes). `object_rule` / `public_note` are documentation for the human
    reading the table; the object rules themselves are enforced by the route through
    `security/objects.py`.
    """

    permission: str
    object_rule: str | None = None
    rate: str | None = None
    public_note: str | None = None


POLICIES: dict[RouteKey, Policy] = {}


def _api(method: str, tail: str, permission: str = "public", **kwargs: Any) -> None:
    key = (method.upper(), f"{API_PREFIX}{tail}")
    if key in POLICIES:  # pragma: no cover - a typo in this file
        raise RuntimeError(f"policy table: duplicate route {key}")
    POLICIES[key] = Policy(permission, **kwargs)


# ── ops / meta ───────────────────────────────────────────────────────────────
_api("GET", "/health", public_note="运维探针")
_api("GET", "/capabilities", public_note="对非 engine.probe 主体脱敏（同形状）")

# ── market data (public reads) ───────────────────────────────────────────────
_api("GET", "/symbols/search")
_api("POST", "/symbols/resolve", rate="guest.resolve", public_note="公开但贵：会触发外部数据源（user 及以上不限）")
_api("GET", "/instruments")
_api("GET", "/instruments/{instrument_id}/bars")
_api("GET", "/instruments/{instrument_id}/quote")
_api("GET", "/instruments/{instrument_id}/reports", public_note="软删对象不返回")
_api("GET", "/instruments/{instrument_id}/reports/latest", public_note="软删对象不返回")
_api("GET", "/instruments/{instrument_id}/runs", public_note="RunOut 对非拥有者非管理员脱敏")

# ── reports ──────────────────────────────────────────────────────────────────
_api("GET", "/reports", public_note="scope=trash 需登录且仅“我的”（admin 全部）；starred 公开")
_api("GET", "/reports/{report_id}", public_note="软删对象对非拥有者/非管理员 404")
_api("GET", "/reports/{report_id}/blocks", public_note="软删对象对非拥有者/非管理员 404")
_api("GET", "/reports/{report_id}/diff/{other_id}", public_note="软删对象对非拥有者/非管理员 404")
_api("GET", "/reports/{report_id}/export", "export.read", rate="guest.export")
_api(
    "POST",
    "/instruments/{instrument_id}/reports",
    "report.generate",
    public_note="预检额度 → start(actor) 预留；202 追加 quota",
)
_api(
    "PATCH",
    "/reports/{report_id}",
    "report.manage_own",
    object_rule=(
        "拥有者 或 report.manage_any；created_by IS NULL 仅管理员；"
        "body 含 pinned/setLatest 需 report.manage_any（RV-10）"
    ),
)
_api(
    "DELETE",
    "/reports/{report_id}",
    "report.manage_own",
    object_rule="同 PATCH；管理员改他人的 → 审计 report.manage_other",
)
_api("POST", "/reports/{report_id}/restore", "report.manage_own", object_rule="同 PATCH")

# ── runs ─────────────────────────────────────────────────────────────────────
_api("POST", "/runs", public_note="恒 405（既有占位路由，不泄露存在性差异）")
_api("POST", "/runs/estimate", "report.generate", public_note="暴露成本估算")
_api("GET", "/runs/{run_id}", public_note="RunOut 对非拥有者非管理员脱敏")
_api("POST", "/runs/{run_id}/events-token")
_api("GET", "/runs/{run_id}/events", public_note="自带 ?token= 真签名校验")
_api(
    "POST",
    "/runs/{run_id}/cancel",
    "report.manage_own",
    object_rule="run.created_by == me 或 manage_any；他人 → 审计 run.cancel_other",
)

# ── asks ─────────────────────────────────────────────────────────────────────
_api(
    "POST",
    "/reports/{report_id}/ask",
    "ask.cite",
    rate="guest.ask_cite",
    public_note="mode=cite 公开；mode=deep 在路由内二次判定 ask.deep + 额度 deep_ask（guest → 401）",
)
_api("GET", "/reports/{report_id}/asks", public_note="返回“我的”（admin 全部，guest 空）")
_api("GET", "/asks/{ask_id}", "ask.cite", object_rule="拥有者或管理员；否则 404")
_api(
    "POST",
    "/asks/{ask_id}/events-token",
    "ask.cite",
    object_rule="拥有者或管理员；否则 404（对象级校验唯一发生在这里，RV-5）",
)
_api("POST", "/asks/{ask_id}/cancel", "ask.cite", object_rule="拥有者或管理员；否则 404")
_api("GET", "/asks/{ask_id}/events", "ask.cite", public_note="只验 ?token=（绑定 ask_id，60 s）；令牌即授权（RV-5）")

# ── search / watchlist ───────────────────────────────────────────────────────
_api("GET", "/search", "search.read")
_api("GET", "/watchlist", public_note="登录→个人（首次播种）；guest→精选；追加 scope")
_api("POST", "/watchlist", "watchlist.own")
_api("PATCH", "/watchlist/reorder", "watchlist.own")
_api("DELETE", "/watchlist/{instrument_id}", "watchlist.own")

# ── operations (admin / root) ────────────────────────────────────────────────
_api("GET", "/schedules", "schedule.manage")
_api("POST", "/schedules", "schedule.manage", public_note="审计 schedule.create")
_api("PATCH", "/schedules/{schedule_id}", "schedule.manage", public_note="审计 schedule.update")
_api("DELETE", "/schedules/{schedule_id}", "schedule.manage", public_note="审计 schedule.delete")
_api("GET", "/cost/summary", "cost.read")
_api("GET", "/cost/entries", "cost.read")
_api("POST", "/cost/budget", "cost.manage", public_note="仅 root")
_api("GET", "/agent/probe", "engine.probe")
_api("POST", "/agent/probe", "engine.probe")

# ── /auth (M47) ──────────────────────────────────────────────────────────────
_api("GET", "/auth/config")
_api("GET", "/auth/captcha", rate="auth.captcha")
_api("POST", "/auth/register", rate="auth.register", public_note="另有全站 60/小时与邀请码试错限速（服务内）")
_api("POST", "/auth/login", rate="auth.login", public_note="另有同用户名 10/min、全站 120/min（服务内）")
_api("POST", "/auth/logout", public_note="幂等：无会话也 204")
_api("GET", "/auth/me", public_note="永不 401")
_api("POST", "/auth/password", "account.self", public_note="强制改密态唯一可用的写接口")
_api("POST", "/auth/recover", rate="auth.recover", public_note="唯一失败响应 recovery_invalid；验证码恒必填")
_api("POST", "/auth/recovery-key", "account.self", rate="auth.password")
_api(
    "DELETE",
    "/auth/account",
    "account.self",
    object_rule="仅本人；root → 403 root_protected；授权用 assert_can_self_delete（不走管理矩阵）",
)
_api("GET", "/auth/sessions", "account.self")
_api("DELETE", "/auth/sessions/{session_id}", "account.self", object_rule="只能删自己的")
_api("POST", "/auth/sessions/revoke-others", "account.self")
_api("PATCH", "/auth/profile", "account.self")

# ── /me (M48 — not implemented yet, see PENDING_ROUTES) ──────────────────────
_api("GET", "/me/quota", "account.self")
_api("GET", "/me/quota-ledger", "account.self")
_api("POST", "/me/quota-requests", "quota.request")
_api("GET", "/me/quota-requests", "account.self")

# ── /admin (M50) ─────────────────────────────────────────────────────────────
_api("GET", "/admin/overview", "admin.overview.read")
_api("GET", "/admin/users", "admin.users.read")
_api("POST", "/admin/users", "admin.users.manage", object_rule="role=admin 仅 root 可指定（assert_can_create_user）")
_api(
    "GET",
    "/admin/users/export.csv",
    "admin.users.read",
    object_rule="限速 admin.export（按用户，服务内；不用 policy.rate——那是按 IP）+ 审计 admin.export（§25.3.5a）",
)
_api(
    "POST",
    "/admin/users/bulk-quota",
    "admin.quota.manage",
    object_rule="admin 仅 role=user；两遍制；先 404 后 403（§25.3.5b）",
)
_api("GET", "/admin/users/{user_id}", "admin.users.read")
_api(
    "PATCH",
    "/admin/users/{user_id}",
    "admin.users.manage",
    object_rule="矩阵见 §24.3.14（assert_can_manage_user）；role 字段需 admin.admins.manage",
)
_api("DELETE", "/admin/users/{user_id}", "admin.users.manage", object_rule="矩阵见 §24.3.14；需 confirm=<username>")
_api("POST", "/admin/users/{user_id}/reset-password", "admin.users.manage", object_rule="矩阵见 §24.3.14")
_api("POST", "/admin/users/{user_id}/revoke-sessions", "admin.users.manage", object_rule="矩阵见 §24.3.14")
_api("POST", "/admin/users/{user_id}/unlock", "admin.users.manage", object_rule="矩阵见 §24.3.14")
_api("POST", "/admin/users/{user_id}/quota", "admin.quota.manage", object_rule="矩阵见 §24.3.14")
_api(
    "DELETE",
    "/admin/users/{user_id}/sessions/{session_id}",
    "admin.users.manage",
    object_rule="矩阵见 §24.3.14；会话必须属于该用户否则 404；审计 auth.session.revoke（B-19，§24.3.15d 的逐条吊销）",
)
_api("GET", "/admin/invites", "admin.invites.manage")
_api("POST", "/admin/invites", "admin.invites.manage")
_api("DELETE", "/admin/invites/{invite_id}", "admin.invites.manage")
_api("GET", "/admin/quota-requests", "admin.quota.manage")
_api("POST", "/admin/quota-requests/{request_id}/resolve", "admin.quota.manage")
_api("GET", "/admin/audit", "admin.audit.read", public_note="只读")
_api(
    "GET",
    "/admin/audit/export.csv",
    "admin.audit.read",
    object_rule="限速 admin.export（按用户，服务内）+ 审计 admin.export；过滤同 GET /admin/audit",
)
_api("GET", "/admin/settings", "admin.settings.manage")
_api("PATCH", "/admin/settings", "admin.settings.manage")
_api("GET", "/admin/featured-watchlist", "admin.featured.manage")
_api("POST", "/admin/featured-watchlist", "admin.featured.manage", public_note="审计 featured.add")
_api("DELETE", "/admin/featured-watchlist/{instrument_id}", "admin.featured.manage", public_note="审计 featured.remove")

# ── /admin/workflows（研究流程图，report-workflow-graph §5.1）────────────────────
# 全站共享的管理资产，无归属 → 无对象级规则；越权/不存在一律 workflow_not_found。
_WF = "admin.workflow.manage"
_api("GET", "/admin/workflows", _WF)
_api("POST", "/admin/workflows", _WF, public_note="审计 workflow.create")
_api("PUT", "/admin/workflows/enabled", _WF, public_note="审计 settings.update（workflow.enabled）")
_api("GET", "/admin/workflows/default-summary", _WF)
_api("GET", "/admin/workflows/{template_id}", _WF)
_api("PATCH", "/admin/workflows/{template_id}", _WF, object_rule="内置模板不可改名 409")
_api("DELETE", "/admin/workflows/{template_id}", _WF, public_note="归档；默认/内置 409；审计 workflow.archive")
_api("POST", "/admin/workflows/{template_id}/ops", _WF, public_note="草稿编辑不入审计；CAS 并发（RW-1）")
_api("PUT", "/admin/workflows/{template_id}/draft", _WF, public_note="仅用于重置损坏的草稿，不是导入（§1.5）")
_api("POST", "/admin/workflows/{template_id}/validate", _WF)
_api("GET", "/admin/workflows/{template_id}/preview", _WF)
_api("POST", "/admin/workflows/{template_id}/publish", _WF, public_note="审计 workflow.publish")
_api("POST", "/admin/workflows/{template_id}/set-default", _WF, public_note="审计 workflow.default.set")
_api("GET", "/admin/workflows/{template_id}/versions", _WF)
_api("GET", "/admin/workflows/{template_id}/versions/{version}", _WF)
_api("POST", "/admin/workflows/{template_id}/versions/{version}/restore", _WF)
_api("POST", "/admin/workflows/{template_id}/versions/{version}/rollback", _WF, public_note="审计 workflow.rollback")
_api("GET", "/admin/workflows/{template_id}/usage", _WF)
_api("GET", "/admin/workflows/{template_id}/chat", _WF, object_rule="仅当前管理员自己的对话")
_api("DELETE", "/admin/workflows/{template_id}/chat", _WF, object_rule="仅清空当前管理员自己的对话")
_api(
    "POST",
    "/admin/workflows/{template_id}/assist",
    _WF,
    public_note="限速 admin.workflow_assist（按用户，服务内）；同模板单并发",
)
_api("POST", "/admin/workflows/assists/{assist_id}/events-token", _WF, public_note="令牌绑定 assist_id，60 s")
_api(
    "GET",
    "/admin/workflows/assists/{assist_id}/events",
    _WF,
    public_note="只验 ?token=（绑定 assist_id，60 s）；令牌即授权（同 /asks/{ask_id}/events）",
)
_api("POST", "/admin/workflows/assists/{assist_id}/cancel", _WF)


#: ★TEMPORARY. Table rows whose route does not exist yet. Empty since M50 (`/me/*` left with M48,
#: `/admin/*` with M50); kept as the mechanism for the next milestone that registers rows early. An entry
#: here whose route *does* exist makes `verify_against_app()` raise, so the set cannot rot.
PENDING_ROUTES: frozenset[RouteKey] = frozenset()


def policy_for(method: str, path_format: str) -> Policy | None:
    return POLICIES.get((method.upper(), path_format))


def _is_governed(path: str) -> bool:
    return path.startswith(f"{API_PREFIX}/") and not path.startswith(EXEMPT_PREFIXES)


def _iter_route_entries(route: Any) -> Iterator[tuple[str | None, Any]]:
    """Yield `(path_format, methods)` for one entry of `app.routes`.

    FastAPI >= 0.116 stores each `include_router(...)` as a single **lazy**
    `_IncludedRouter` on `app.routes`; that object carries neither `path` nor
    `methods`, so a flat scan sees zero governed routes and every row of the table
    looks stale ("策略表里有真实不存在的路由" — the whole table, not a few rows).

    `effective_route_contexts()` is the supported way to expand it: it recurses into
    nested includes and already applies the include prefix to `path_format`. Unlike
    `app.openapi()["paths"]` it also keeps `include_in_schema=False` routes, which
    matters here — bare `POST /api/v1/runs` is exactly one of those.
    """
    expand = getattr(route, "effective_route_contexts", None)
    if callable(expand):
        for context in expand():
            original = getattr(context, "original_route", None)
            yield (
                getattr(context, "path_format", None) or getattr(original, "path_format", None),
                getattr(original, "methods", None),
            )
        return
    # Plain route (Starlette Route / APIRoute / older FastAPI): read it directly.
    yield (
        getattr(route, "path_format", None) or getattr(route, "path", None),
        getattr(route, "methods", None),
    )


def route_keys(app: Any) -> set[RouteKey]:
    """`(METHOD, path_format)` of every governed route on `app` (HEAD/OPTIONS ignored)."""
    keys: set[RouteKey] = set()
    for route in getattr(app, "routes", []):
        for path, methods in _iter_route_entries(route):
            if not path or not methods or not _is_governed(path):
                continue
            for method in methods:
                if method not in _IGNORED_METHODS:
                    keys.add((method, path))
    return keys


def verify_against_app(app: Any) -> None:
    """Raise `RuntimeError` unless the table and the app's routes agree (spec §24.3.3).

    * a real governed route missing from `POLICIES` → fail (it would be un-gated);
    * a `POLICIES` key with no real route that is not in `PENDING_ROUTES` → fail (typo/stale);
    * a `PENDING_ROUTES` key whose route now exists → fail (the exemption outlived its reason).
    """
    real = route_keys(app)
    declared = set(POLICIES)
    problems: list[str] = []

    unregistered = sorted(real - declared)
    if unregistered:
        problems.append("路由未登记进策略表（security/policy.py）：" + ", ".join(f"{m} {p}" for m, p in unregistered))

    dangling = sorted(declared - real - PENDING_ROUTES)
    if dangling:
        problems.append("策略表里有真实不存在的路由：" + ", ".join(f"{m} {p}" for m, p in dangling))

    stale = sorted(PENDING_ROUTES & real)
    if stale:
        problems.append("PENDING_ROUTES 里的路由已经实现，请从中删除：" + ", ".join(f"{m} {p}" for m, p in stale))

    unknown_pending = sorted(PENDING_ROUTES - declared)
    if unknown_pending:
        problems.append("PENDING_ROUTES 含未登记键：" + ", ".join(f"{m} {p}" for m, p in unknown_pending))

    if problems:
        raise RuntimeError("策略表与应用路由不一致：\n  " + "\n  ".join(problems))
