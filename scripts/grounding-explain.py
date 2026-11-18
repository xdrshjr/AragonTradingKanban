#!/usr/bin/env python
"""`python scripts/grounding-explain.py <reportId>` —— 接地率的**可重放诊断**（★v12 S5）。

为什么要有它（§22.3.6 的验收口径最后一行）：这一轮之前的诊断全靠"再写一个一次性
探针脚本"，于是每个数字都没有出处。这个脚本是那张表的**产品化版本**：对一份已落库的
报告，逐条打印未接地 claim 的最近 5 个候选值与差距，以及它有没有一条命名的公式。

设计约束（与 `api/app/services/numeric_claims.py` 一致，**只读**）：

* 判定只调用既有函数（`grounding_report` / `snapshot_number_corpus` / `derived_values` /
  `match_derived` / `match_derived_approx`），**不复制任何判据**——一份实现，两处消费；
* 不联网、不写库、不改任何文件；
* 输出分三段：**汇总**（比值与四分类计数）→ **逐条缺口**（就近候选）→ **口径说明**
  （`rules` / `docHash` / `snapshotHash`），因为"同一份报告换个规则算出的比值不可比"
  这件事必须能被看见，而不是靠记忆。

用法：

    python scripts/grounding-explain.py <reportId> [--limit 40] [--db api/data/app.db]

`reportId` 前缀即可（与界面上的短 id 一致），唯一匹配就展开。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ★评审修正（E-114）：Windows 控制台是 cp936，而这里要打印的是**模型写的原文**
# （上下文片段里可能有 emoji、破折号、数学符号）。`print` 遇到编不出来的字符会抛
# `UnicodeEncodeError`，于是一个只读诊断在**最需要它的时候**（复现一次真实缺口）
# 崩在半途。`errors="replace"` 只影响显示，不影响任何判据。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        with contextlib.suppress(ValueError, OSError):  # 被重定向成非文本流
            _stream.reconfigure(errors="replace")
sys.path.insert(0, str(ROOT / "api"))

from app.services import numeric_claims as nc  # noqa: E402


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}（先用 npm run setup / npm run dev 建库）")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _load_report(con: sqlite3.Connection, report_id: str) -> sqlite3.Row:
    row = con.execute(
        "select id, version, doc_json, quality_json, grounded_ratio, run_id from reports"
        " where id like ? order by generated_at desc",
        (f"{report_id}%",),
    ).fetchone()
    if row is None:
        raise SystemExit(f"找不到报告：{report_id}")
    return row


def _snapshot(con: sqlite3.Connection, report_id: str, row: sqlite3.Row) -> dict:
    """报告的输入快照。先看 `runs.workspace_path`，再退回约定路径。"""
    candidates: list[Path] = []
    if row["run_id"]:
        run = con.execute("select workspace_path from runs where id = ?", (row["run_id"],)).fetchone()
        if run is not None and run["workspace_path"]:
            path = Path(run["workspace_path"])
            candidates.append(path if path.is_absolute() else ROOT / path)
        candidates.append(ROOT / "api" / "workspace" / "runs" / row["run_id"])
    for workspace in candidates:
        snapshot = workspace / "snapshot.json"
        if snapshot.exists():
            return json.loads(snapshot.read_text(encoding="utf-8"))
    raise SystemExit(f"报告 {report_id} 的输入快照已不在工作区（保留期过后会被清理），无法重算")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐条解释一份报告的接地率（只读）")
    parser.add_argument("report_id", help="报告 id（前缀即可）")
    parser.add_argument("--db", default=str(ROOT / "api" / "data" / "app.db"), help="SQLite 路径")
    parser.add_argument("--limit", type=int, default=20, help="逐条打印的缺口数上限")
    parser.add_argument("--json", action="store_true", help="改为输出机器可读的 JSON")
    args = parser.parse_args(argv)

    con = _connect_readonly(Path(args.db))
    con.row_factory = sqlite3.Row
    row = _load_report(con, args.report_id)
    doc = json.loads(row["doc_json"])
    stored_quality = json.loads(row["quality_json"] or "{}")
    snapshot = _snapshot(con, args.report_id, row)
    con.close()

    report = nc.grounding_report(doc, snapshot)
    payload = report.as_payload()
    corpus = nc.snapshot_number_corpus(snapshot)
    derived = nc.derived_values(snapshot, doc)
    candidates = []
    for canonical, provenance in corpus.items():
        try:
            candidates.append((float(canonical), provenance.path))
        except (TypeError, ValueError):
            continue

    gaps: list[dict[str, object]] = []
    for claim in nc.extract_claims(doc):
        if claim.cls in ("identifier", "projection"):
            continue
        if claim.canonical and claim.canonical in corpus:
            continue
        exact = nc.match_derived(claim, derived)
        approx = None if exact is not None else nc.match_derived_approx(claim, derived)
        if exact is not None or approx is not None:
            continue
        value = nc.value_number(claim.raw)
        nearest: list[dict[str, object]] = []
        if value is not None:
            ranked = sorted(
                (
                    (abs(value - number) / abs(number) if number else 9e9, number, path)
                    for number, path in candidates
                ),
                key=lambda entry: entry[0],
            )[:5]
            nearest = [
                {"value": number, "path": path, "gapPct": round(gap * 100, 3)}
                for gap, number, path in ranked
            ]
        gaps.append(
            {
                "raw": claim.raw,
                "canonical": claim.canonical,
                "path": claim.path,
                "blockId": claim.block_id,
                "unit": nc.value_context_unit(claim.context or ""),
                "significantDigits": nc.significant_digits(claim.raw),
                "context": claim.context,
                "nearest": nearest,
            }
        )

    result = {
        "reportId": row["id"],
        "version": row["version"],
        "runId": row["run_id"],
        "rules": payload["rules"],
        "docHash": payload["docHash"],
        "snapshotHash": payload["snapshotHash"],
        "stored": {
            # ★v12（S11）：落库值必须带**它当时的口径**，否则它只是一段没有出处的历史。
            "groundedRatio": row["grounded_ratio"],
            "rules": (stored_quality.get("grounding") or {}).get("rules"),
            "comparable": (stored_quality.get("grounding") or {}).get("rules") == payload["rules"],
        },
        "today": {
            "groundedRatio": round(report.ratio, 4),
            "factClaims": payload["factClaims"],
            "factMissing": payload["factMissing"],
            "derivedClaims": payload["derivedClaims"],
            "approxClaims": payload["approxClaims"],
            "projectionClaims": payload["projectionClaims"],
            "identifierClaims": payload["identifierClaims"],
        },
        "gaps": gaps,
        # ★评审修正（E-114）：`--limit` 只约束**人读**的那一段（`print`），JSON 永远给
        # 全量——但这两件事必须写进载荷，否则「23 条」与「前 3 条」哪个是总数只能靠数。
        # 上一版 `--limit 3 --json` 会打印全部 23 条，而 help 里写着「上限」。
        "gapsTotal": len(gaps),
        "gapsInText": min(len(gaps), args.limit),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    stored = result["stored"]
    today = result["today"]
    print(f"报告 {row['id'][:8]} (v{row['version']}, run={row['run_id']})")
    print(f"  口径 rules={result['rules']}")
    if stored["rules"] is None:
        print("  落库值来自**更早的口径**（quality.grounding 里没有 rules 键）-> 与今天的数不可比")
    elif stored["comparable"]:
        print("  落库值与今天同一口径，可直接对比")
    print(
        f"  落库 {stored['groundedRatio']}  →  今天 {today['groundedRatio']}"
        f"（fact {today['factClaims']} / 缺 {today['factMissing']} /"
        f" 精确推算 {today['derivedClaims']} / 近似 {today['approxClaims']} /"
        f" 预测 {today['projectionClaims']} / 标识符 {today['identifierClaims']}）"
    )
    print(f"\n未接地的数字 {len(gaps)} 条（下列为前 {min(len(gaps), args.limit)} 条，各带最近 5 个候选）：")
    for gap in gaps[: args.limit]:
        print(f"\n  {gap['raw']}  @ {gap['path']}  [{gap['blockId']}]")
        print(f"    上下文：{gap['context']}")
        for candidate in gap["nearest"]:
            print(f"    候选 {candidate['value']:<14} 差 {candidate['gapPct']:>7}%  {candidate['path']}")
        if not gap["nearest"]:
            print("    （这个写法解析不出数值，无法给候选）")
    if len(gaps) > args.limit:
        print(f"\n…… 另有 {len(gaps) - args.limit} 条，用 --limit 调大")
    print(
        "\n口径说明：`近似推算` = 与某条命名公式相差 ≤1%（模型用了盘中价等口径差），"
        "在界面上显示为「推算（近似）」；`预测` 与 `标识符` 不进分母。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
