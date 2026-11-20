"""提示词与提交纪律（spec §20.3.6 / M22；AC-81）。

实测给了两个反直觉的结论，这个文件守的是把结论落进提示词这件事：

1. **格式难度不是主要成本**（探针 A 3352 vs 探针 B 3375 input tokens，轮次都是 1），
   所以不该为了"少失败"去削薄 schema —— 真正的成本在资料读取（一次真实生成 72,810
   input tokens，其中 `bars.csv` 是 47.6 KB）。
2. **CLI 的修复轮对"取值矛盾"无效**：探针 C 的两次修复请求原文完全一样，`attempts`
   一直是 0，而真正的拒绝原因分别是 `must be <= 0` 与 `must be >= 10`。所以提示词必须
   让模型在**提交之前**自己发现矛盾。

因此 `research_system.md` 多了两节：「提交前自检」6 条与「资料读取顺序与读取预算」4 条，
而且带一个可被机器读到的版本标记（`<!-- version: 7 -->`）——它与 `quality.prompt.sha1`
一起构成"这一版报告是哪个提示词写的"的可追溯链（裁决 H-5：**不进**引擎指纹）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from app.agent.pricing import PRICE_VERSION
from app.services import report_validator

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "app" / "agent" / "prompts"
RESEARCH = PROMPTS_DIR / "research_system.md"
ASK = PROMPTS_DIR / "ask_system.md"


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, title: str) -> str:
    """取一个 `## title` 小节（到下一个 `## ` 为止）。"""
    pattern = re.compile(rf"^## {re.escape(title)}.*?$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    return match.group(1) if match else ""


# ── AC-81：两节内容与编号 ──────────────────────────────────────────────────


def test_submit_checklist_has_six_numbered_items() -> None:
    """「提交前自检」必须恰好 6 条编号——实测那 6 条就是修复轮修不了的部分。"""
    checklist = section(body(RESEARCH), "提交前自检（submit 之前逐条过一遍，任一条不过就先改再提交）")
    items = re.findall(r"^(\d+)\. ", checklist, re.MULTILINE)
    assert [int(item) for item in items] == [1, 2, 3, 4, 5, 6]


def test_reading_budget_has_four_numbered_items() -> None:
    """「资料读取顺序与读取预算」4 条：第 3 条是"只看最后 60 个交易日"（最省的一刀）。"""
    text = section(body(RESEARCH), "资料读取顺序与读取预算（这是最容易花光预算的地方）")
    items = re.findall(r"^(\d+)\. ", text, re.MULTILINE)
    assert [int(item) for item in items] == [1, 2, 3, 4]
    assert "最后 60 个交易日" in text
    assert "bars.csv" in text


def test_the_checklist_names_the_fields_that_the_schema_requires() -> None:
    """自检清单必须点名 11 个顶层必填字段——只说"检查必填项"等于没说。"""
    text = section(body(RESEARCH), "提交前自检（submit 之前逐条过一遍，任一条不过就先改再提交）")
    required = (
        "schema",
        "instrument",
        "asOf",
        "headline",
        "rating",
        "summary",
        "keyMetrics",
        "sections",
        "risks",
        "sources",
        "disclaimer",
    )
    for field in required:
        assert field in text, f"{field} 没被点名"
    for section_id in ("thesis", "fundamentals", "valuation", "risks", "catalysts"):
        assert section_id in text


def test_the_checklist_warns_about_self_contradiction() -> None:
    """实测的修复轮对"取值矛盾"无效，所以提示词必须让模型自己复核取值范围。"""
    text = body(RESEARCH)
    assert "取值范围" in text or "自相矛盾" in text
    assert "不要重复提交同一份内容" in text


# ── 版本标记与 `quality.prompt` ────────────────────────────────────────────


@pytest.mark.parametrize("path", [RESEARCH, ASK])
def test_both_prompts_carry_a_version_marker(path: Path) -> None:
    """两个提示词都要有版本标记：指纹要能区分两版提示词（§20.4 的改动表）。"""
    match = report_validator.PROMPT_VERSION_RE.search(body(path))
    assert match is not None, f"{path.name} 缺少 `<!-- version: N -->`"
    # 追问增加多轮上下文边界，独立递增版本；研究提示词维持原契约。
    assert int(match.group(1)) == (9 if path == ASK else 8)


def test_prompt_quality_matches_the_file_on_disk() -> None:
    """AC-81 后半：`quality.prompt.sha1` 与实际文件 sha1 相等（同一个函数算出来的）。"""
    quality = report_validator.prompt_quality(RESEARCH)
    assert quality is not None
    raw = RESEARCH.read_bytes()
    assert quality["sha1"] == hashlib.sha1(raw).hexdigest()[:12]
    assert quality["bytes"] == len(raw)
    # ★v14：同上。`bytes` 那一条**保持原样**——它正是「quality.prompt 量的是 system
    # 而不是 task」的证据（P0-3）。
    assert quality["version"] == 8


def test_two_prompts_have_different_fingerprints() -> None:
    """内容不变也要能区分两版提示词——否则"这份报告用的哪套规则"永远说不清。"""
    assert report_validator.prompt_quality(RESEARCH)["sha1"] != report_validator.prompt_quality(ASK)["sha1"]


def test_prompt_quality_is_none_for_a_missing_file(tmp_path: Path) -> None:
    """文件不存在就返回 `None`，不编造一个 sha1。"""
    assert report_validator.prompt_quality(tmp_path / "nope.md") is None


# ── 提示词不进引擎指纹（裁决 H-5 / R-40） ──────────────────────────────────


def test_prompt_fingerprint_does_not_leak_into_the_engine_fingerprint() -> None:
    """`quality.engine` 固定为四元组 + `schemaSha1`；**不含** prompt / model / provider。

    裁决 H-5：提示词一旦进了指纹，**所有历史报告**都会变成"引擎不同"，版本比较的琥珀
    横幅就常驻了——那恰好违反 G4 的初衷（只提示真正的差异，R-40）。
    """
    from app.agent.fingerprint import EngineFingerprint

    fingerprint = EngineFingerprint(fingerprint="abc123abc123", cli_version="0.6.68", schema_bytes=18392)
    quality = fingerprint.as_quality()
    assert set(quality) == {"fingerprint", "cliVersion", "allowTools", "schemaBytes"}
    with_sha = {**quality, "schemaSha1": "7a93509904b4"}
    assert "prompt" not in with_sha
    assert "model" not in with_sha and "provider" not in with_sha


def test_price_version_is_visible_for_estimates() -> None:
    """估算要能解释"用哪版价格算的"，所以它必须跟着 `quality.cost` 一起出。"""
    assert PRICE_VERSION == "2026-03"
