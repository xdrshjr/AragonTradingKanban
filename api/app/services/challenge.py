"""`ChallengeProvider` — "prove you are a person" behind one interface (spec §24.3.6, D-3).

`captcha` (`services/captcha.py`) is the only implementation. `email` / `sms` are named
here only to mark where they would plug in; they need an outbound channel this project
does not have. Swapping in Turnstile/hCaptcha later means adding a provider, not
touching `auth_service`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Purpose = Literal["register", "login", "recover"]


class ChallengeUnavailable(Exception):
    """The provider cannot issue a challenge right now (e.g. Pillow or its font is missing).

    Registration treats this as `503 captcha_unavailable` and closes itself: better a
    short outage than letting accounts in without a challenge.
    """


@dataclass(frozen=True, slots=True)
class IssuedChallenge:
    challenge_id: str
    #: `data:image/png;base64,…`
    image: str
    #: CSS pixel size the front end renders at (the PNG itself is @2x).
    width: int
    height: int
    expires_in: int

    def to_dict(self) -> dict[str, object]:
        return {
            "challengeId": self.challenge_id,
            "image": self.image,
            "width": self.width,
            "height": self.height,
            "expiresIn": self.expires_in,
        }


class ChallengeProvider(Protocol):
    kind: str

    def available(self) -> bool: ...

    def issue(self, purpose: Purpose, ip: str) -> IssuedChallenge: ...

    def verify(self, challenge_id: str, answer: str, purpose: str, ip: str) -> bool: ...


def get_provider(kind: str = "captcha") -> ChallengeProvider:
    if kind != "captcha":
        raise ChallengeUnavailable(f"unknown challenge provider {kind!r}")
    from .captcha import get_captcha_provider

    return get_captcha_provider()
