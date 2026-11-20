"""Built-in weak-password table and sequence detection (spec §24.3.5).

A small, *honest* list: the point is to refuse the passwords an attacker tries first,
not to ship a 10 MB dictionary. Everything is compared in lower case.
"""

from __future__ import annotations

WEAK_PASSWORDS: frozenset[str] = frozenset(
    {
        "12345678",
        "123456789",
        "1234567890",
        "123456",
        "1234567",
        "11111111",
        "00000000",
        "88888888",
        "66666666",
        "12341234",
        "password",
        "password1",
        "password123",
        "passw0rd",
        "p@ssw0rd",
        "p@ssword",
        "qwerty123",
        "qwertyuiop",
        "qwerty12",
        "1q2w3e4r",
        "1qaz2wsx",
        "q1w2e3r4",
        "abc12345",
        "abcd1234",
        "abc123456",
        "admin123",
        "admin1234",
        "admin888",
        "administrator",
        "root1234",
        "root123456",
        "aragon123",
        "aragon1234",
        "aragon888",
        "aragontrading",
        "iloveyou",
        "iloveyou1",
        "letmein123",
        "welcome123",
        "welcome1",
        "monkey123",
        "dragon123",
        "sunshine1",
        "football1",
        "baseball1",
        "trustno1",
        "changeme",
        "changeme123",
        "zaq12wsx",
        "asdfghjkl",
        "asdf1234",
        "zxcvbnm123",
        "qwertyui",
        "asdfghjk",
        "zxcvbnm1",
        "1qaz@wsx",
        "qazwsxedc",
        "woaini1314",
        "5201314520",
        "a123456789",
        "a1234567",
        "aa123456",
        "13519126390",
    }
)


def is_weak(password: str) -> bool:
    return password.lower() in WEAK_PASSWORDS


def is_single_char_repeat(password: str) -> bool:
    """`aaaaaaaa`, `11111111` — one distinct character."""
    return len(password) >= 2 and len(set(password)) == 1


def is_sequence(password: str) -> bool:
    """The whole password is one strictly ascending or descending code-point run, ≥ 4 long
    (`abcdefgh`, `12345678`, `87654321`).

    Deliberately the *same* definition `web/lib/passwordPolicy.ts` evaluates client-side, so
    the live checklist and the server's `weak_password.detail.rules` can never disagree about
    this rule. Keyboard rows (`qwertyuiop`) are not code-point runs; they are on the weak list.
    """
    if len(password) < 4:
        return False
    step = ord(password[1]) - ord(password[0])
    if step not in (1, -1):
        return False
    return all(ord(b) - ord(a) == step for a, b in zip(password, password[1:], strict=False))
