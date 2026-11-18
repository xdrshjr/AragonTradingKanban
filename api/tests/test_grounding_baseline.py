"""S5 · 接地基线（AC-128 / C-35 / C-47 / S-11）。

这条用例的存在理由只有一个：**「上一节点测过」不等于「今天还是那样」**（S-11）。

v11 写的是"6/6 真实报告的比值全在 0.9 以下、`derivedClaims` 恒 0"，而评审节点用
**今天的代码**对同一批 `doc + snapshot` 重算得到 `0.7057 / 0.7875 / 0.8086 / 0.7704 /
0.9453 / 0.7921` —— 于是那条结论整个失效。根因是它抄了 `reports.quality` 里的**落库值**，
而那些落库值的 `grounding` **连 `rules` 键都没有**（旧口径的铁证）。

所以这里钉两件事：

1. **历史报告的重算值**必须与快照里的基线一致（±0.001）。规则再变一次，这条会红——
   而它红了，就说明"口径变了"这件事必须被说出来（`RULES_VERSION` 要跟着升）；
2. **落库值是否可比**由 `rules` 决定：没有 `rules` 就是不可比，界面不许给百分比。

数据来源是本机 `api/data/app.db` + 工作区快照。**库不存在时跳过**，不许伪造。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services import numeric_claims as nc

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "api" / "data" / "app.db"
RUNS = ROOT / "api" / "workspace" / "runs"

#: 2026-11 用 `claims-v3` 重算出的六份真实 aragon 报告（E-102 的原始输出）。
#: 升 `RULES_VERSION` 时必须重算并更新这张表 —— 它就是"口径变了要说出来"的机器判据。
BASELINE_V3: dict[int, float] = {
    6: 0.7057,
    7: 0.7875,
    8: 0.8086,
    10: 0.7704,
    11: 0.9453,
    12: 0.7921,
}

#: `claims-v4` 的重算值（本轮 S5：三类命名派生量 + 区间破折号 + 近似层 + 一位小数豁免）。
BASELINE_V4: dict[int, float] = {
    6: 0.8048,
    7: 0.8432,
    8: 0.8812,
    10: 0.8836,
    11: 0.9531,
    12: 0.9101,
}

pytestmark = pytest.mark.skipif(not DB.exists(), reason="本机没有 api/data/app.db（CI 环境跳过）")


def _rows() -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "select id, version, doc_json, quality_json, grounded_ratio, run_id from reports"
            " where json_extract(quality_json,'$.source')='aragon' order by generated_at"
        ).fetchall()
    finally:
        connection.close()


def _snapshot(run_id: str | None) -> dict | None:
    if not run_id:
        return None
    path = RUNS / run_id / "snapshot.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_stored_grounding_of_old_reports_has_no_rules_key() -> None:
    """E-103 的重放：**落库值是旧口径**的铁证是 `grounding` 里没有 `rules`。

    今天 `GroundingReport.as_payload()` 一定带 `rules` / `docHash` / `snapshotHash` 三个键；
    因此"缺这三个键"意味着那条记录来自更早的判据。这就是"不可比"的机器判据（S-11）。

    ★评审修正（评审节点 I-128）：原来结尾是 `assert checked >= 0` —— 一条**恒真**的断言，
    于是一旦库里的 aragon 报告全被清理、或者 `quality_json` 全为空对象，这条用例会
    悄悄变成"什么都没检查"的绿。真值只有两种：检查了 N 条（N>0），或者本机没有可查的
    报告（那就 `skip`，让 CI 的输出说出"这条没跑"，而不是假装跑过）。
    """
    checked = 0
    for row in _rows():
        quality = json.loads(row["quality_json"] or "{}")
        grounding = quality.get("grounding")
        if not grounding:
            continue  # v6/v7/v8/v10 的 grounding 是**空对象**
        checked += 1
        if "rules" in grounding:
            # 新口径：三个指纹必须齐备，否则"可复算"是假的。
            assert {"docHash", "snapshotHash"} <= set(grounding)
        else:
            # 旧口径：不许拿它做跨口径比较（这条断言本身就是文档）。
            assert row["grounded_ratio"] is not None
    if checked == 0:
        pytest.skip("库里暂时没有带 grounding 的 aragon 报告：这不是通过，是没得查")


def _graded_rows() -> list[tuple[int, float, int, int]]:
    """本机可重算的 `(version, ratio, factMissing, factClaims)`，按 version 升序。

    ★评审修正（评审节点 I-128）：两条基线用例共用同一段"取库 + 取快照 + 重算"的代码，
    于是"重算了 0 条"这件事只可能出现在**一个**地方，而不是每条用例各自忘一次。
    """
    graded: list[tuple[int, float, int, int]] = []
    for row in _rows():
        snapshot = _snapshot(row["run_id"])
        if snapshot is None:
            continue
        report = nc.grounding_report(json.loads(row["doc_json"]), snapshot)
        graded.append((row["version"], report.ratio, report.fact_missing, report.fact_claims))
    return sorted(graded)


def test_the_regraded_ratios_match_the_recorded_baseline() -> None:
    """AC-128：用**今天的代码**重算六份历史报告，比值必须与基线一致（±0.001）。

    这是本仓唯一一条"如果你改了判据而没升版本号，它就会红"的用例。
    """
    baseline = BASELINE_V3 if nc.RULES_VERSION == "claims-v3" else BASELINE_V4
    compared = [row for row in _graded_rows() if row[0] in baseline]
    if not compared:
        pytest.skip("库里没有可重算的历史 aragon 报告（工作区快照过了保留期）")
    mismatches = [
        f"v{version}: 今天={ratio:.4f} 基线={baseline[version]:.4f}"
        f"（fact={claims}/missing={missing}）"
        for version, ratio, missing, claims in compared
        if abs(ratio - baseline[version]) > 0.001
    ]
    assert not mismatches, (
        "接地口径变了而基线没更新 —— 要么改回判据，要么升 RULES_VERSION 并重算基线（S-11）：\n"
        + "\n".join(mismatches)
    )


def test_the_v12_report_is_at_or_above_the_degrade_line() -> None:
    """AC-121：v12 那份报告的 `groundedRatio` 必须 ≥ 0.90（**同一份快照**前后对比）。

    C-35：不许换快照换口径。所以这里固定用 `d57087a2…` 那份快照，重算同一份 doc。
    """
    candidates = [row for row in _graded_rows() if row[0] == 12]
    if not candidates:
        pytest.skip("库里没有可重算的 v12 报告")
    _version, ratio, missing, claims = candidates[-1]
    assert ratio >= 0.90, f"v12 仍是 {ratio:.4f}（fact={claims}/missing={missing}）"
    assert missing <= 17, f"缺口 {missing} 超过目标 17"


def test_the_diagnostic_script_is_read_only_and_runnable() -> None:
    """§22.3.6 的**工具箱**交付：`scripts/grounding-explain.py` 必须存在且只读。"""
    script = ROOT / "scripts" / "grounding-explain.py"
    assert script.exists(), "缺少接地率的可重放诊断脚本"
    source = script.read_text(encoding="utf-8")
    # 只读：不许有写库/写文件的调用（`mode=ro` 是唯一的打开方式）。
    assert "mode=ro" in source
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "write_text(", "open("):
        assert forbidden not in source, f"诊断脚本应当只读，却出现 {forbidden!r}"
