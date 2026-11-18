#!/usr/bin/env python
"""`python scripts/backfill-report-sha1.py [--dry-run|--apply]` —— **运维可选工具**（§23.6.1）。

## 先读这一段（这是本脚本存在的**唯一**理由，也是它默认什么都不做的原因）

硬约束 5（§23.0.3，延续 V11-P0-1）写的是：**老数据不回填**。`doc_sha1` 为 NULL 的历史行
保持 NULL，而 NULL 的语义是"不知道"，**不是**"不同"——去重只在两侧都非 NULL 时生效。

因此 **跑过 `--apply` 就等于推翻那条硬约束**，需要**用户显式同意**。所以：

* 默认**没有参数**时是 `--dry-run`（只打印"会有多少行被写入"），真正落库必须显式 `--apply`；
* 它**不**进任何门禁、**不**进任何里程碑判据、**不**在应用启动时执行（2026-11-18 的设计评审
  P1-16 明确要求这一点：一个脚本不可能同时是"不回填"与"必须回填"）；
* 它只碰 `doc_sha1 IS NULL` 的行，幂等，按标的**分批 commit**（一个标的的事务失败不会
  让前面 900 个标的白干）。

## 它为什么不自己算一遍指纹

`content_sha1()` 是**唯一**算法（`api/app/services/report_service.py:110`：
`sha1(json.dumps(doc, sort_keys=True, separators=(",", ":")))`）。这里 import 它，
而不复制一份 `json.dumps(...)`——第二份实现会在某次改 canonical 形态时静默漂移，
而漂移的表现是"去重命中率下降"，没人会想到去查脚本。

## 用法

    python scripts/backfill-report-sha1.py                    # 只统计（默认）
    python scripts/backfill-report-sha1.py --apply             # 真的写
    python scripts/backfill-report-sha1.py --apply --instrument 600519.SH
    python scripts/backfill-report-sha1.py --apply --include-deleted --batch 50

`--include-deleted`：回收站里的行也要。默认**包含**它们——`doc_sha1` 是列一致性的一部分，
一行在回收站里不等于它不需要指纹（恢复之后它就要参与比较了）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

# Windows 控制台是 cp936，这里要打印标的名称（中文），编码不出来的字符不该让一个只读
# 统计崩在半途（与 `scripts/grounding-explain.py:39-42` 同一条纪律）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        with contextlib.suppress(ValueError, OSError):
            _stream.reconfigure(errors="replace")

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import Instrument, Report  # noqa: E402
from app.services.report_service import content_sha1  # noqa: E402

BANNER = """
╭─ backfill-report-sha1 ────────────────────────────────────────────────────────╮
│ 这是**运维可选工具**，不是门禁的一部分。                                        │
│ 跑 `--apply` = 推翻硬约束 5（§23.0.3 / V11-P0-1）的「老数据不回填」。           │
│ 继续之前请确认：用户已显式同意。默认行为是 `--dry-run`（什么都不写）。          │
╰───────────────────────────────────────────────────────────────────────────────╯
"""


def _rows(*, instrument: str | None) -> list[tuple[str, str, str, str]]:
    """`(report_id, instrument_id, symbol, doc_json)`，只要 `doc_sha1 IS NULL` 的行。"""
    with SessionLocal() as session:
        stmt = (
            select(Report.id, Report.instrument_id, Instrument.symbol, Report.doc_json)
            .join(Instrument, Instrument.id == Report.instrument_id)
            .where(Report.doc_sha1.is_(None))
            .order_by(Report.instrument_id, Report.version)
        )
        if instrument:
            stmt = stmt.where(
                (Instrument.symbol == instrument) | (Instrument.id == instrument)
            )
        return [tuple(row) for row in session.execute(stmt).all()]  # type: ignore[misc]


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 reports.doc_sha1（运维可选）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真的写库；不加这个参数就是 dry-run（默认）",
    )
    parser.add_argument("--instrument", default=None, help="只处理这个标的（symbol 或 id）")
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="（默认已包含）保留参数以兼容脚本化调用",
    )
    parser.add_argument("--batch", type=int, default=200, help="每多少行 commit 一次")
    args = parser.parse_args()

    print(BANNER)
    rows = _rows(instrument=args.instrument)
    total = len(rows)
    broken: list[str] = []
    by_instrument: dict[str, int] = {}
    for report_id, instrument_id, _symbol, doc_json in rows:
        try:
            json.loads(doc_json)
        except (TypeError, ValueError):
            broken.append(report_id)
            continue
        by_instrument[instrument_id] = by_instrument.get(instrument_id, 0) + 1

    print(f"待回填：{total} 行（{len(by_instrument)} 个标的）")
    for instrument_id, count in sorted(by_instrument.items(), key=lambda kv: -kv[1]):
        print(f"  {instrument_id:<16} {count:>5} 行")
    if broken:
        # 损坏的 doc 不猜指纹：NULL 的语义是"不知道"，猜出来的 sha1 会让去重**命中**一份
        # 读不出来的报告——那是把"不知道"变成"错误地知道"。
        print(f"\n跳过 {len(broken)} 行损坏 doc（保持 NULL）：{', '.join(broken[:5])}…")

    if not args.apply:
        print("\n[dry-run] 没有写入任何东西。确认用户同意后加 --apply。")
        return 0

    written = 0
    pending: list[tuple[str, str]] = []
    with SessionLocal() as session:
        for report_id, _instrument_id, _symbol, doc_json in rows:
            if report_id in set(broken):
                continue
            try:
                doc = json.loads(doc_json)
            except (TypeError, ValueError):  # pragma: no cover - 上面已筛过
                continue
            pending.append((content_sha1(doc), report_id))
            if len(pending) >= max(1, args.batch):
                written += _flush(session, pending)
                pending = []
        written += _flush(session, pending)

    print(f"\n[apply] 写入 {written} 行 doc_sha1，跳过 {len(broken)} 行损坏 doc。")
    print("提醒：从这一刻起这些行会参与去重比较，与本轮硬约束 5 的『不回填』不再一致。")
    return 0


def _flush(session, pending: list[tuple[str, str]]) -> int:
    """一批一起 commit：单个标的失败只丢一批，不会回滚前面已经落库的几千行。"""
    if not pending:
        return 0
    count = 0
    for sha1, report_id in pending:
        row = session.get(Report, report_id)
        if row is None or row.doc_sha1 is not None:  # 幂等：别人已经写了就不覆盖
            continue
        row.doc_sha1 = sha1
        count += 1
    session.commit()
    return count


if __name__ == "__main__":
    raise SystemExit(main())
