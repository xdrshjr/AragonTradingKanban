"""Graphical captcha: Pillow-rendered, one-shot, IP-bucket bound (spec §24.3.6).

Storage is an **in-process** ordered dict (cap 5000, oldest evicted first). That leans on
the single-worker invariant (`assert_single_worker()`, spec E-8 / R-A7); a restart simply
invalidates the challenges in flight, which costs a person one click on "换一张".

Properties pinned by tests (AC-165 … AC-168):

* 5 characters from an ambiguity-free alphabet (no `0 O 1 I L`), **case-insensitive**.
* The answer is stored as `sha256(salt + answer)`; the plaintext exists only inside the
  rendered picture (plus an opt-in test-only side table, see `set_debug_answers`).
* **One-shot**: every `verify` bumps the attempt counter; success, or the 5th attempt,
  deletes the challenge. A 6th try on the same id finds nothing.
* TTL 300 s; bound to the **IP bucket** (IPv4 /24, IPv6 /48) the challenge was issued to — a
  challenge farmed out to another network fails there (and, deliberately, does not burn
  the owner's attempts).
* For `purpose="register"` an answer submitted less than 1.2 s after issue is a script and
  is refused like any wrong answer.

The picture is rendered at 2x (320×112) and displayed at 160×56 CSS pixels (the JSON says
160×56) so it is crisp on high-DPI screens with stable layout. It brings its own dark
rounded background (`#17181c`) because an `<img>` cannot inherit CSS variables, and uses
Pillow's built-in scalable font (`ImageFont.load_default(size=…)`, Pillow ≥ 10.1) — no
system font, so Windows dev machines and Ubuntu servers render identically.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import math
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass

from ..config import get_settings
from ..logging import get_logger
from ..security import clock
from ..security.ipaddr import bind_bucket
from .challenge import ChallengeUnavailable, IssuedChallenge, Purpose

logger = get_logger("captcha")

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LENGTH = 5
TTL_SECONDS = 300
MAX_ATTEMPTS = 5
CAPACITY = 5000
#: Registration answers faster than this are bots (spec §24.3.6 "两个廉价信号").
MIN_SOLVE_SECONDS_REGISTER = 1.2

#: CSS size reported to the client; the PNG is rendered at `SCALE`x.
CSS_WIDTH, CSS_HEIGHT = 160, 56
SCALE = 2
BACKGROUND = (0x17, 0x18, 0x1C)
#: Light green / blue / yellow, rotated per character (readable on the dark tile).
INK_COLORS = ((134, 239, 172), (125, 211, 252), (253, 224, 71))


@dataclass(slots=True)
class _Entry:
    answer_hash: str
    salt: str
    purpose: str
    bucket: str
    issued_at: float
    expires_at: float
    attempts: int = 0


def _digest(salt: str, answer: str) -> str:
    return hashlib.sha256(f"{salt}{answer.strip().upper()}".encode()).hexdigest()


class CaptchaProvider:
    kind = "captcha"

    def __init__(self) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._debug_answers: dict[str, str] = {}
        self._keep_plain = False
        self._font_cache: dict[int, object] = {}

    # ── availability ────────────────────────────────────────────────────────

    def _load_pil(self):  # noqa: ANN202 - returns the PIL modules
        image = importlib.import_module("PIL.Image")
        draw = importlib.import_module("PIL.ImageDraw")
        font = importlib.import_module("PIL.ImageFont")
        filt = importlib.import_module("PIL.ImageFilter")
        return image, draw, font, filt

    def unavailable_reason(self) -> str | None:
        """`None` when Pillow and its built-in scalable font work; otherwise why not."""
        try:
            _image, _draw, font_module, _filter = self._load_pil()
            self._font(font_module)
        except Exception as exc:  # ImportError, OSError (no FreeType), AttributeError (old Pillow)
            return f"{type(exc).__name__}: {exc}"
        return None

    def available(self) -> bool:
        return self.unavailable_reason() is None

    def _font(self, font_module):  # noqa: ANN001, ANN202
        size = 64
        cached = self._font_cache.get(size)
        if cached is None:
            cached = font_module.load_default(size=size)
            self._font_cache[size] = cached
        return cached

    # ── test support ────────────────────────────────────────────────────────

    def set_debug_answers(self, enabled: bool) -> None:
        """Keep the plaintext answers in a side table (tests / `AUTH_TEST_HOOKS` only)."""
        self._keep_plain = enabled
        if not enabled:
            self._debug_answers.clear()

    def debug_answer(self, challenge_id: str) -> str | None:
        return self._debug_answers.get(challenge_id)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self._debug_answers.clear()

    def __len__(self) -> int:
        return len(self._entries)

    # ── issue / verify ──────────────────────────────────────────────────────

    def issue(self, purpose: Purpose, ip: str) -> IssuedChallenge:
        try:
            image_modules = self._load_pil()
            font = self._font(image_modules[2])
        except Exception as exc:
            raise ChallengeUnavailable(f"{type(exc).__name__}: {exc}") from exc

        answer = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        png = self._render(answer, image_modules, font)
        salt = secrets.token_hex(8)
        challenge_id = secrets.token_urlsafe(12)
        now = clock.epoch()
        entry = _Entry(
            answer_hash=_digest(salt, answer),
            salt=salt,
            purpose=purpose,
            bucket=bind_bucket(ip),
            issued_at=now,
            expires_at=now + TTL_SECONDS,
        )
        with self._lock:
            self._entries[challenge_id] = entry
            while len(self._entries) > CAPACITY:
                evicted, _ = self._entries.popitem(last=False)
                self._debug_answers.pop(evicted, None)
            if self._keep_plain or get_settings().auth_test_hooks:
                self._debug_answers[challenge_id] = answer
        return IssuedChallenge(
            challenge_id=challenge_id,
            image="data:image/png;base64," + base64.b64encode(png).decode("ascii"),
            width=CSS_WIDTH,
            height=CSS_HEIGHT,
            expires_in=TTL_SECONDS,
        )

    def verify(self, challenge_id: str, answer: str, purpose: str, ip: str) -> bool:
        if not challenge_id or not isinstance(answer, str):
            return False
        now = clock.epoch()
        with self._lock:
            entry = self._entries.get(challenge_id)
            if entry is None:
                return False
            if entry.expires_at <= now:
                self._drop(challenge_id)
                return False
            if entry.purpose != purpose or entry.bucket != bind_bucket(ip):
                # Someone else's challenge: fail without touching the owner's attempts.
                return False
            entry.attempts += 1
            too_fast = purpose == "register" and now - entry.issued_at < MIN_SOLVE_SECONDS_REGISTER
            correct = secrets.compare_digest(entry.answer_hash, _digest(entry.salt, answer))
            if correct and not too_fast:
                self._drop(challenge_id)
                return True
            if entry.attempts >= MAX_ATTEMPTS:
                self._drop(challenge_id)
            return False

    def _drop(self, challenge_id: str) -> None:
        self._entries.pop(challenge_id, None)
        self._debug_answers.pop(challenge_id, None)

    # ── rendering ───────────────────────────────────────────────────────────

    def _render(self, text: str, modules, font) -> bytes:  # noqa: ANN001
        image_mod, draw_mod, _font_mod, filter_mod = modules
        rng = secrets.SystemRandom()
        width, height = CSS_WIDTH * SCALE, CSS_HEIGHT * SCALE

        canvas = image_mod.new("RGB", (width, height), BACKGROUND)
        pitch = (width - 80) // LENGTH
        for index, char in enumerate(text):
            tile = image_mod.new("RGBA", (76, height), (0, 0, 0, 0))
            tile_draw = draw_mod.Draw(tile)
            ink = INK_COLORS[index % len(INK_COLORS)]
            tile_draw.text((16, rng.randint(2, 14)), char, font=font, fill=(*ink, 255))
            tile = tile.rotate(rng.uniform(-20, 20), resample=image_mod.Resampling.BICUBIC)
            canvas.paste(tile, (4 + index * pitch + rng.randint(-3, 3), rng.randint(-3, 5)), tile)

        draw = draw_mod.Draw(canvas)
        for _ in range(3):  # anti-aliased-looking quadratic Béziers as interference
            p0 = (rng.randint(0, width // 4), rng.randint(10, height - 10))
            p1 = (rng.randint(width // 4, 3 * width // 4), rng.randint(0, height))
            p2 = (rng.randint(3 * width // 4, width), rng.randint(10, height - 10))
            points = [
                (
                    (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1],
                )
                for t in (step / 40 for step in range(41))
            ]
            draw.line(points, fill=rng.choice(INK_COLORS), width=3)
        for _ in range(60):
            x, y = rng.randint(0, width - 1), rng.randint(0, height - 1)
            draw.ellipse((x, y, x + 2, y + 2), fill=rng.choice(INK_COLORS))
        canvas = canvas.filter(filter_mod.GaussianBlur(0.6 * SCALE / 2))

        # Rounded dark tile on a transparent corner, so it sits cleanly on any page background.
        mask = image_mod.new("L", (width, height), 0)
        draw_mod.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=math.ceil(10 * SCALE), fill=255)
        out = image_mod.new("RGBA", (width, height), (0, 0, 0, 0))
        out.paste(canvas, (0, 0), mask)
        buffer = io.BytesIO()
        out.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


_provider = CaptchaProvider()


def get_captcha_provider() -> CaptchaProvider:
    return _provider


def startup_status() -> str:
    """The one-line `captcha: ready|unavailable(reason)` logged at startup."""
    reason = _provider.unavailable_reason()
    return "captcha: ready" if reason is None else f"captcha: unavailable({reason})"
