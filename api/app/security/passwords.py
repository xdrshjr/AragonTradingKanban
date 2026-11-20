"""scrypt password hashing and the new-password policy (spec §24.3.5). Zero new dependencies.

Stored format: ``scrypt$<log2 n>$<r>$<p>$<salt b64url>$<hash b64url>`` — the parameters
describe themselves, so tuning them later only affects *new* hashes and `verify_password`
keeps working on old ones; `needs_rehash()` lets a successful login upgrade lazily.

Three properties are load-bearing and each has a test:

* **Every KDF call goes through one `BoundedSemaphore(4)`** (RV-7). One scrypt is
  ≈ 32 MiB and 60–100 ms; the unauthenticated login/register routes run in a 40-thread
  pool, so without a cap 40 concurrent requests is ≈ 1.3 GB. A caller that cannot get a
  slot within 3 s gets `503 busy` (never "wrong password").
* **A missing user costs one scrypt too** (`verify_password(pw, DUMMY_HASH)`), otherwise
  the response time says whether the username exists.
* **`hashlib.scrypt` is looked up on the module at call time** so a test can count or
  slow it down by monkeypatching it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import threading
import unicodedata

from ..errors import Busy
from .weak_passwords import is_sequence, is_single_char_repeat, is_weak

SCHEME = "scrypt"
#: log2(n), r, p for **new** hashes. n = 2**15, r = 8 → ≈ 32 MiB, 60–100 ms.
LOG2_N = 15
R = 8
P = 1
DKLEN = 32
SALT_BYTES = 16

MIN_LENGTH = 8
MAX_LENGTH = 128
MIN_CLASSES = 2

#: Concurrency cap for the KDF (RV-7) and how long a caller queues before `503 busy`.
KDF_CONCURRENCY = 4
KDF_WAIT_SECONDS = 3.0
_KDF_SLOTS = threading.BoundedSemaphore(KDF_CONCURRENCY)

#: Rule ids reported in `weak_password.detail.rules` (the front end lights up its checklist
#: from exactly these strings).
RULE_MIN_LENGTH = "min_length"
RULE_MAX_LENGTH = "max_length"
RULE_CONTAINS_USERNAME = "contains_username"
RULE_CHAR_CLASSES = "char_classes"
RULE_COMMON_PASSWORD = "common_password"
RULE_REPEATED_CHARS = "repeated_chars"
RULE_SEQUENTIAL_CHARS = "sequential_chars"
ALL_RULES: tuple[str, ...] = (
    RULE_MIN_LENGTH,
    RULE_MAX_LENGTH,
    RULE_CONTAINS_USERNAME,
    RULE_CHAR_CLASSES,
    RULE_COMMON_PASSWORD,
    RULE_REPEATED_CHARS,
    RULE_SEQUENTIAL_CHARS,
)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def normalize(password: str) -> str:
    """NFKC, so full-width and half-width forms of the "same" password both work."""
    return unicodedata.normalize("NFKC", password)


#: A syntactically valid hash nobody can match; verifying against it costs exactly one
#: scrypt with the production parameters (the "user does not exist" path).
DUMMY_HASH = f"{SCHEME}${LOG2_N}${R}${P}${_b64(b'aragon-dummy-salt')}${_b64(bytes(DKLEN))}"


def _kdf(password: str, salt: bytes, *, log2_n: int, r: int, p: int) -> bytes:
    n = 1 << log2_n
    maxmem = max(2**26, 256 * n * r)
    if not _KDF_SLOTS.acquire(timeout=KDF_WAIT_SECONDS):
        raise Busy()
    try:
        return hashlib.scrypt(
            normalize(password).encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=maxmem,
            dklen=DKLEN,
        )
    finally:
        _KDF_SLOTS.release()


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    digest = _kdf(password, salt, log2_n=LOG2_N, r=R, p=P)
    return f"{SCHEME}${LOG2_N}${R}${P}${_b64(salt)}${_b64(digest)}"


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != SCHEME:
        return None
    try:
        log2_n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _unb64(parts[4]), _unb64(parts[5])
    except (ValueError, TypeError):
        return None
    # Bound what a (corrupted) stored string can make us allocate.
    if not (10 <= log2_n <= 17 and 1 <= r <= 16 and 1 <= p <= 4 and salt and expected):
        return None
    return log2_n, r, p, salt, expected


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. A malformed/tombstone hash still burns one scrypt."""
    parsed = _parse(stored or "")
    if parsed is None:
        _kdf(password, b"aragon-dummy-salt", log2_n=LOG2_N, r=R, p=P)
        return False
    log2_n, r, p, salt, expected = parsed
    actual = _kdf(password, salt, log2_n=log2_n, r=r, p=p)
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    """True when the stored parameters are weaker than today's (login upgrades it)."""
    parsed = _parse(stored or "")
    if parsed is None:
        return False
    log2_n, r, p, _salt, expected = parsed
    return (log2_n, r, p) < (LOG2_N, R, P) or len(expected) < DKLEN


def _classes(password: str) -> int:
    kinds = [
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"[0-9]", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    ]
    return sum(kinds)


def password_rule_failures(password: str, username: str | None = None) -> list[str]:
    """Ids of every rule the password breaks, in `ALL_RULES` order (empty = acceptable).

    Used for register / change / reset / admin-create. The bootstrap root password is
    **exempt** — it is public by requirement; D-2's forced first-login change covers it.
    """
    value = normalize(password)
    failed: list[str] = []
    if len(value) < MIN_LENGTH:
        failed.append(RULE_MIN_LENGTH)
    if len(value) > MAX_LENGTH:
        failed.append(RULE_MAX_LENGTH)
    if username and username.lower() in value.lower():
        # "等于用户名"是"包含用户名"的特例，同一个 id（前端的清单只有这一条）。
        failed.append(RULE_CONTAINS_USERNAME)
    if _classes(value) < MIN_CLASSES:
        failed.append(RULE_CHAR_CLASSES)
    if is_weak(value):
        failed.append(RULE_COMMON_PASSWORD)
    if is_single_char_repeat(value):
        failed.append(RULE_REPEATED_CHARS)
    if is_sequence(value):
        failed.append(RULE_SEQUENTIAL_CHARS)
    return failed
