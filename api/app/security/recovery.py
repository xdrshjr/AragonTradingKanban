"""Recovery key — the self-service "forgot my password" credential (spec §25.3.3, D-9).

Pure functions, no I/O. A key is 20 characters of Crockford Base32 (≈ 100 bit), shown as
`XXXXX-XXXXX-XXXXX-XXXXX`. 100 bits of entropy means a plain `sha256` is enough for storage
(there is nothing to brute-force, so no slow KDF is needed). The plaintext lives only in the
one HTTP response that issues it and in the user's own clipboard / downloaded file —
`users.recovery_hash` holds the digest and nothing else does (AC-217).

`normalize()` forgives the mistakes people make when copying a code by hand (spaces, hyphens,
case, `O`↔`0`, `I`/`L`↔`1`); the alphabet has no `I L O U`, so the folding is unambiguous.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
KEY_LENGTH = 20
GROUP = 5

#: What `generate_key()` produces (AC-217).
KEY_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}$")

_FOLD = str.maketrans({"O": "0", "I": "1", "L": "1"})
_STRIP = re.compile(r"[\s\-]+")


def generate_key() -> str:
    """A fresh key, grouped 4×5 (`K7QM4-XNP2R-…`)."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(KEY_LENGTH))
    return "-".join(raw[i : i + GROUP] for i in range(0, KEY_LENGTH, GROUP))


def normalize(value: str | None) -> str:
    """Strip whitespace/hyphens, upper-case, fold `O→0`, `I/L→1`."""
    return _STRIP.sub("", value or "").upper().translate(_FOLD)


def hash_key(value: str | None) -> str:
    """`sha256(normalize(key))` as lowercase hex (64 chars)."""
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()


#: A digest nobody can produce a key for. Verifying a *missing* user against it keeps the
#: "unknown name" path doing the same hash + compare as the real one (AC-219).
DUMMY_HASH = hashlib.sha256(b"aragon-recovery-dummy").hexdigest()


def verify(value: str | None, stored_hash: str | None) -> bool:
    """Constant-time comparison of `hash_key(value)` with a stored digest (bytes, not str)."""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_key(value).encode("ascii"), stored_hash.encode("ascii", "replace"))
