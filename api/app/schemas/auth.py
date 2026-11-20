"""Request / response shapes for `/api/v1/auth` (spec §24.5.1).

Field names are camelCase on the wire, like every other schema in this project.
Deliberately generous `max_length`s: these are only ceilings against absurd payloads;
the product rules (username charset, password policy) are enforced by the service and
answered with the specific error codes the front end renders (`invalid_username`,
`weak_password`, …) rather than a generic `validation_error`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    captchaId: str | None = Field(default=None, max_length=64)
    captchaAnswer: str | None = Field(default=None, max_length=16)
    inviteCode: str | None = Field(default=None, max_length=32)
    displayName: str | None = Field(default=None, max_length=40)
    #: Honeypot — a person never sees this field; any value means a script filled the form.
    website: str | None = Field(default=None, max_length=256)
    #: Client-side "form shown at" (epoch milliseconds). Submitting < 1.2 s later is a script.
    issuedAt: float | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False
    captchaId: str | None = Field(default=None, max_length=64)
    captchaAnswer: str | None = Field(default=None, max_length=16)


class ChangePasswordRequest(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=1, max_length=256)


class RecoverRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    #: **No format check** (RV-Y13): a malformed key is the same `401 recovery_invalid` as a wrong
    #: one, never a `422 validation_error` that would tell the caller which of the two it was.
    recoveryKey: str = Field(max_length=128)
    newPassword: str = Field(min_length=1, max_length=256)
    captchaId: str | None = Field(default=None, max_length=64)
    captchaAnswer: str | None = Field(default=None, max_length=16)


class RotateRecoveryKeyRequest(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    #: Must equal the current username (case-sensitive) — checked by the service.
    confirm: str = Field(default="", max_length=64)


class ProfilePatch(BaseModel):
    #: `null` clears the display name; a string must be 1..40 characters.
    displayName: str | None = Field(default=None, min_length=1, max_length=40)


class MeUser(BaseModel):
    id: str
    username: str
    displayName: str | None = None
    role: Literal["user", "admin", "root"]
    status: Literal["active"] = "active"
    createdAt: str
    lastLoginAt: str | None = None
    mustChangePassword: bool = False
    passwordIsDefault: bool = False
    #: §25.3.3 — whether a recovery key is set (never the key itself).
    hasRecoveryKey: bool = False


class MeOut(BaseModel):
    """`GET /auth/me` and the body of login / register / profile responses.

    `quota` is `null` until the quota engine (M48) fills it for signed-in users.
    """

    authMode: Literal["enforced", "open"]
    authenticated: bool
    user: MeUser | None = None
    permissions: list[str]
    quota: dict[str, Any] | None = None


class RegisterOut(MeOut):
    """The `201` body of `POST /auth/register` — the **only** response that can carry the
    plaintext recovery key (shown once; `null` when self-service recovery is switched off)."""

    recoveryKey: str | None = None


class RecoveryKeyOut(BaseModel):
    recoveryKey: str
    setAt: str


class PasswordPolicy(BaseModel):
    min: int
    max: int
    minClasses: int


class UsernamePolicy(BaseModel):
    min: int
    max: int
    pattern: str


class AuthConfigOut(BaseModel):
    authMode: Literal["enforced", "open"]
    registrationMode: Literal["open", "invite", "closed"]
    #: Self-service registration with a captcha is possible right now. `False` when the mode
    #: is `closed`, **or** the captcha cannot be rendered (AC-172) — invite registration is a
    #: separate path (see `inviteRegistrationOpen`).
    registrationOpen: bool
    #: ★ addition to §24.5.1: registering *with an invite code* does not need the captcha, so
    #: it stays possible when `registrationOpen` is false only because Pillow is unavailable.
    inviteRegistrationOpen: bool
    #: ★ addition: the captcha provider can issue a challenge right now.
    captchaAvailable: bool
    passwordPolicy: PasswordPolicy
    usernamePolicy: UsernamePolicy
    captchaKind: Literal["captcha"] = "captcha"
    forceRootPasswordChange: bool
    #: §25.3.3 — self-service password recovery is on (the `/recover` page and its link).
    recoveryEnabled: bool = True


class CaptchaOut(BaseModel):
    challengeId: str
    image: str
    width: int
    height: int
    expiresIn: int


class SessionOut(BaseModel):
    id: str
    current: bool
    createdAt: str
    lastSeenAt: str
    ip: str | None = None
    userAgent: str | None = None


class SessionsOut(BaseModel):
    items: list[SessionOut]


class RevokeOthersOut(BaseModel):
    revoked: int
