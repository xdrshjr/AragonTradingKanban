"""`security/passwords.py`：scrypt、自描述参数、NFKC、八条策略、KDF 并发上限（spec §24.3.5，RV-7）。

计时类断言一律用**调用次数 / 并发峰值**，不用墙钟——墙钟在 CI 上是抛硬币。
"""

from __future__ import annotations

import hashlib
import threading
import time

import pytest

from app.errors import Busy
from app.security import passwords
from app.security.passwords import (
    DUMMY_HASH,
    LOG2_N,
    hash_password,
    needs_rehash,
    password_rule_failures,
    verify_password,
)

GOOD = "Aragon-Test-9x"


@pytest.fixture(scope="module")
def good_hash() -> str:
    return hash_password(GOOD)


# ── 哈希与校验 ──────────────────────────────────────────────────────────────


def test_roundtrip_and_format_is_self_describing(good_hash: str) -> None:
    scheme, log2_n, r, p, salt, digest = good_hash.split("$")
    assert (scheme, int(log2_n), int(r), int(p)) == ("scrypt", 15, 8, 1)
    assert salt and digest
    assert verify_password(GOOD, good_hash)
    assert not verify_password(GOOD + "x", good_hash)
    assert not verify_password("", good_hash)


def test_two_hashes_of_the_same_password_differ(good_hash: str) -> None:
    other = hash_password(GOOD)
    assert other != good_hash, "盐必须随机"
    assert verify_password(GOOD, other)


def test_nfkc_normalisation_makes_full_width_and_ascii_the_same_password() -> None:
    """全角/半角造成"同一个密码输不进"是真实的支持工单。"""
    stored = hash_password("Ａragon-Test-９x")  # 全角 A 与 9
    assert verify_password("Aragon-Test-9x", stored)
    assert verify_password("Ａragon-Test-９x", stored)


def test_parameters_in_the_stored_string_are_honoured_and_needs_rehash_sees_old_ones() -> None:
    """将来调参只影响新哈希：旧串按**自己存的参数**校验，`needs_rehash` 让登录时透明升级。"""
    import base64
    import os

    salt = os.urandom(16)
    weak = passwords._kdf(GOOD, salt, log2_n=14, r=8, p=1)  # noqa: SLF001
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731
    stored = f"scrypt$14$8$1${b64(salt)}${b64(weak)}"
    assert verify_password(GOOD, stored)
    assert needs_rehash(stored)
    assert not needs_rehash(hash_password(GOOD))
    assert LOG2_N == 15


@pytest.mark.parametrize(
    "bad", ["", "!", "plain", "scrypt$15$8$1$only-four", "scrypt$99$8$1$AAAA$AAAA", "bcrypt$1$2$3$4$5"]
)
def test_tombstones_and_garbage_never_verify(bad: str) -> None:
    assert verify_password(GOOD, bad) is False
    assert needs_rehash(bad) is False


def test_malformed_hash_still_burns_exactly_one_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """墓碑 `!` 与"用户不存在"同样耗时一次（AC-170 的调用次数口径）。"""
    calls = {"n": 0}
    real = hashlib.scrypt

    def counting(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting)
    verify_password(GOOD, "!")
    assert calls["n"] == 1
    verify_password(GOOD, DUMMY_HASH)
    assert calls["n"] == 2


# ── 策略：八条规则逐条 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("password", "username", "expected"),
    [
        ("Ab1", "alice", ["min_length"]),
        ("a" * 129 + "1", "alice", ["max_length"]),
        ("alice", "alice", ["min_length", "contains_username", "char_classes"]),
        ("ALICE", "alice", ["min_length", "contains_username", "char_classes"]),
        ("alice-Secret-1", "alice", ["contains_username"]),
        ("abcdefgh", "bob", ["char_classes", "sequential_chars"]),
        ("password1", "bob", ["common_password"]),
        ("Aragon123", "bob", ["common_password"]),
        ("zzzzzzzz", "bob", ["char_classes", "repeated_chars"]),
        ("12345678", "bob", ["char_classes", "common_password", "sequential_chars"]),
        ("87654321", "bob", ["char_classes", "sequential_chars"]),
        ("qwertyuiop", "bob", ["char_classes", "common_password"]),
    ],
)
def test_each_policy_rule_reports_its_own_id(password: str, username: str, expected: list[str]) -> None:
    assert password_rule_failures(password, username) == expected


def test_a_good_password_passes_every_rule() -> None:
    assert password_rule_failures(GOOD, "alice") == []
    assert password_rule_failures("correct-Horse-7", "bob") == []


def test_two_character_classes_is_enough_and_one_is_not() -> None:
    assert password_rule_failures("lowercase99", "bob") == []
    assert password_rule_failures("lowercaseonly", "bob") == ["char_classes"]


def test_policy_runs_on_the_normalised_password() -> None:
    """全角的 `ＫＲＰＴ５８２９` 归一后是 `KRPT5829`——长度/类别按归一后算。"""
    assert password_rule_failures("ＫＲＰＴ５８２９", "bob") == []
    assert password_rule_failures("ａｂｃｄｅｆｇｈ", "bob") == ["char_classes", "sequential_chars"]


# ── 并发上限（RV-7） ────────────────────────────────────────────────────────


def test_at_most_four_scrypts_run_at_once(monkeypatch: pytest.MonkeyPatch, good_hash: str) -> None:
    """8 个线程同时校验，进入 `hashlib.scrypt` 的线程数峰值 ≤ 4（排队而非并发）。"""
    state = {"now": 0, "peak": 0}
    guard = threading.Lock()
    real = hashlib.scrypt

    def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        with guard:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            time.sleep(0.05)
            return real(*args, **kwargs)
        finally:
            with guard:
                state["now"] -= 1

    monkeypatch.setattr(hashlib, "scrypt", slow)
    results: list[bool] = []

    def work() -> None:
        results.append(verify_password(GOOD, good_hash))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [True] * 8
    assert 1 < state["peak"] <= passwords.KDF_CONCURRENCY == 4


def test_a_caller_that_cannot_get_a_slot_gets_503_busy_not_a_wrong_password(
    monkeypatch: pytest.MonkeyPatch, good_hash: str
) -> None:
    monkeypatch.setattr(passwords, "KDF_WAIT_SECONDS", 0.05)
    held = [passwords._KDF_SLOTS.acquire() for _ in range(passwords.KDF_CONCURRENCY)]  # noqa: SLF001
    try:
        with pytest.raises(Busy) as caught:
            verify_password(GOOD, good_hash)
    finally:
        for _ in held:
            passwords._KDF_SLOTS.release()  # noqa: SLF001
    assert caught.value.status_code == 503
    assert caught.value.code == "busy"
    assert caught.value.headers == {"Retry-After": "2"}
    # 槽位归还后恢复正常。
    assert verify_password(GOOD, good_hash)


# ── 与前端的契约：规则 id ───────────────────────────────────────────────────


def test_the_rule_ids_are_the_ones_the_front_end_renders() -> None:
    """`weak_password.detail.rules` 的 id 词表（spec 没给，见 spec 末节 F-7 / B-1）。

    前端 `web/lib/passwordPolicy.ts` 的 `PasswordRuleId` 联合类型与这里逐个相等；
    "等于用户名"并入 `contains_username`（前端的清单只有这一条）。
    """
    import re
    from pathlib import Path

    assert passwords.ALL_RULES == (
        "min_length",
        "max_length",
        "contains_username",
        "char_classes",
        "common_password",
        "repeated_chars",
        "sequential_chars",
    )
    ts = Path(__file__).resolve().parents[2] / "web" / "lib" / "passwordPolicy.ts"
    if ts.exists():
        block = ts.read_text(encoding="utf-8").split("export type PasswordRuleId =")[1].split(";")[0]
        assert tuple(re.findall(r"'([a-z_]+)'", block)) == passwords.ALL_RULES


def test_sequential_chars_is_the_same_definition_the_client_evaluates() -> None:
    from app.security.weak_passwords import is_sequence

    for run in ("abcd", "1234", "abcdefgh", "87654321", "ZYXW"):
        assert is_sequence(run), run
    for not_run in ("abc", "abce", "aBcD", "13579", "qwertyuiop", "a1b2c3d4"):
        assert not is_sequence(not_run), not_run
