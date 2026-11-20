"""Request / response shapes for `/api/v1/admin` (spec §24.5.3).

camelCase on the wire like every other schema here. Two conventions worth knowing:

* **Request models only bound the payload** (lengths, enums, numeric ranges that have no
  product-specific error code). The rules that *do* have a code — username charset, password
  strength, setting ranges — are enforced by the service and answered with
  `invalid_username` / `weak_password` / `invalid_setting`, which the admin UI renders.
* **`dailyLimit` is tri-state**: absent = leave alone, `null` = "follow the default", `-1` =
  unlimited, `N >= 0` = N per day. Pydantic's `model_fields_set` is what tells absent from
  `null`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Resource = Literal["report", "deep_ask"]


# ── users ────────────────────────────────────────────────────────────────────


class QuotaLite(BaseModel):
    """The per-row quota a list page can afford: one `GROUP BY` for the whole page."""

    limit: int | None = None  # null = unlimited
    usedToday: int = 0
    bonus: int = 0
    remaining: int | None = None  # null = unlimited


class AdminUserQuotaLite(BaseModel):
    report: QuotaLite


class AdminUser(BaseModel):
    id: str
    username: str
    displayName: str | None = None
    role: Literal["root", "admin", "user"]
    status: Literal["active", "disabled", "deleted"]
    createdAt: str
    lastLoginAt: str | None = None
    quota: AdminUserQuotaLite
    locked: bool = False
    invitedBy: str | None = None


class AdminUsersOut(BaseModel):
    items: list[AdminUser]
    total: int
    nextCursor: str | None = None


class AdminRunRow(BaseModel):
    id: str
    instrumentId: str
    symbol: str | None = None
    status: str
    startedAt: str
    finishedAt: str | None = None
    costUsd: float | None = None


class AdminSessionRow(BaseModel):
    id: str
    createdAt: str
    lastSeenAt: str
    expiresAt: str
    ip: str | None = None
    userAgent: str | None = None
    remember: bool = False


class AdminUserDetail(AdminUser):
    disabledReason: str | None = None
    disabledAt: str | None = None
    mustChangePassword: bool = False
    passwordIsDefault: bool = False
    failedLogins: int = 0
    lockedUntil: str | None = None
    lastLoginIp: str | None = None
    createdBy: str | None = None
    #: The full `QuotaStatus` (`{report, deepAsk, tz}`) of `GET /me/quota`, from the quota engine.
    quotaStatus: dict[str, Any] | None = None
    recentRuns: list[AdminRunRow] = Field(default_factory=list)
    sessions: list[AdminSessionRow] = Field(default_factory=list)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    #: Only root may ask for `admin` (`403 admin_protected` otherwise).
    role: Literal["user", "admin"] = "user"
    #: Omitted → a 12-character temporary password is generated and returned **once**.
    password: str | None = Field(default=None, min_length=1, max_length=256)
    displayName: str | None = Field(default=None, min_length=1, max_length=40)
    bonusCredits: int = Field(default=0, ge=0, le=20)


class CreateUserOut(BaseModel):
    user: AdminUser
    #: Present only when the server generated the password; never retrievable afterwards.
    temporaryPassword: str | None = None


class UserPatch(BaseModel):
    displayName: str | None = Field(default=None, min_length=1, max_length=40)
    role: Literal["user", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None
    disabledReason: str | None = Field(default=None, max_length=200)


class TemporaryPasswordOut(BaseModel):
    temporaryPassword: str


class RevokedOut(BaseModel):
    revoked: int


class UserQuotaRequest(BaseModel):
    resource: Resource = "report"
    #: Signed, non-zero. Negative = take back (clamped to the current bonus balance).
    grant: int | None = Field(default=None, ge=-50, le=50)
    #: Absent = untouched; `null` = follow the default; `-1` = unlimited; `N >= 0` = N a day.
    dailyLimit: int | None = Field(default=None, ge=-1, le=100000)
    note: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _something_to_do(self) -> UserQuotaRequest:
        if self.grant == 0:
            raise ValueError("grant must be non-zero")
        if self.grant is None and "dailyLimit" not in self.model_fields_set:
            raise ValueError("need `grant` and/or `dailyLimit`")
        return self


class BulkQuotaRequest(BaseModel):
    """`POST /admin/users/bulk-quota` (spec §25.3.5b). Range = the engine's own `GRANT_MIN..GRANT_MAX`."""

    userIds: list[str] = Field(min_length=1, max_length=100)
    grant: int = Field(ge=-50, le=50)
    note: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _non_zero(self) -> BulkQuotaRequest:
        if self.grant == 0:
            raise ValueError("grant must be non-zero")
        return self


class BulkQuotaOut(BaseModel):
    applied: int
    #: Take-backs clamped to nothing (the user had no bonus balance left): nothing was written.
    skipped: int
    batchId: str


class DeleteUserBody(BaseModel):
    confirm: str | None = Field(default=None, max_length=64)


# ── invites ──────────────────────────────────────────────────────────────────


class InviteCreateRequest(BaseModel):
    maxUses: int = Field(default=1, ge=1, le=100)
    expiresInDays: int | None = Field(default=None, ge=1, le=90)
    bonusCredits: int = Field(default=0, ge=0, le=20)
    note: str | None = Field(default=None, max_length=80)


class InviteOut(BaseModel):
    id: str
    code: str
    note: str | None = None
    maxUses: int
    usedCount: int
    bonusCredits: int
    expiresAt: str | None = None
    disabledAt: str | None = None
    createdAt: str
    createdBy: str | None = None
    #: `active | exhausted | expired | revoked` — derived, so the table needs no client logic.
    status: Literal["active", "exhausted", "expired", "revoked"]


class InvitesOut(BaseModel):
    items: list[InviteOut]


# ── quota requests ───────────────────────────────────────────────────────────


class QuotaRequestUser(BaseModel):
    id: str
    username: str
    displayName: str | None = None


class QuotaRequestRow(BaseModel):
    id: str
    user: QuotaRequestUser | None = None
    resource: str
    amount: int
    reason: str
    status: str
    createdAt: str
    userRuns7d: int = 0
    granted: int | None = None
    resolvedBy: str | None = None
    resolvedAt: str | None = None
    resolutionNote: str | None = None


class QuotaRequestsOut(BaseModel):
    items: list[QuotaRequestRow]
    total: int
    nextCursor: str | None = None


class ResolveQuotaRequest(BaseModel):
    decision: Literal["approve", "reject"]
    grant: int | None = Field(default=None, ge=1, le=20)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _reject_needs_a_reason(self) -> ResolveQuotaRequest:
        if self.decision == "reject" and not (self.note or "").strip():
            raise ValueError("a rejection needs a note (the user reads it)")
        return self


# ── audit ────────────────────────────────────────────────────────────────────


class AuditRowOut(BaseModel):
    id: int
    at: str
    actorId: str | None = None
    actorName: str | None = None
    actorRole: str | None = None
    action: str
    targetType: str | None = None
    targetId: str | None = None
    targetName: str | None = None
    ip: str | None = None
    requestId: str | None = None
    detail: dict[str, Any] | None = None


class AuditOut(BaseModel):
    items: list[AuditRowOut]
    nextCursor: str | None = None


# ── settings ─────────────────────────────────────────────────────────────────


class SettingsOut(BaseModel):
    registrationMode: Literal["open", "invite", "closed"]
    defaultDailyReport: int
    defaultDailyDeepAsk: int
    signupBonus: int
    siteDailyCap: int
    authRecoveryEnabled: bool = True


# ── featured watchlist ───────────────────────────────────────────────────────


class FeaturedAddRequest(BaseModel):
    instrumentId: str = Field(min_length=1, max_length=64)


# ── overview ─────────────────────────────────────────────────────────────────


class DayCount(BaseModel):
    day: str
    count: int


class UsersOverview(BaseModel):
    total: int
    byRole: dict[str, int]
    active7d: int
    disabled: int
    locked: int


class RunsOverview(BaseModel):
    today: int
    succeeded: int
    failed: int
    successRate: float | None = None


class QuotaOverview(BaseModel):
    consumedToday: int
    refundedToday: int


class TopUser(BaseModel):
    userId: str
    username: str | None = None
    runs: int


class Security24h(BaseModel):
    """Rolling 24 h of security-relevant audit actions (spec §25.3.5c). Failure counts are
    *attempts* (Σ `detail.count`), not rows — failures are merged per (name, ip) per minute."""

    loginFailures: int
    loginFailureBursts: int
    loginFailuresCapped: bool
    lockouts: int
    recoveryFailures: int
    recoveries: int
    selfDeletes: int
    exports: int


class OverviewOut(BaseModel):
    users: UsersOverview
    signups: list[DayCount]
    runs: RunsOverview
    runsDaily: list[DayCount]
    quota: QuotaOverview
    topUsers7d: list[TopUser]
    pendingQuotaRequests: int
    recentAudit: list[AuditRowOut]
    security24h: Security24h
