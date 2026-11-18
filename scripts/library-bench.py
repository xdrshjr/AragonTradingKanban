#!/usr/bin/env python
"""`python scripts/library-bench.py` —— 报告库列表的**合成压测**（§23.6.1，证明 §23.5.1 的 p95）。

## 它回答的是哪一个问题

R-66 说首页从 14 次串行 fetch 降到 2 次，但"那 1 次 `/reports` 变慢了"就是新的瓶颈。
所以这里造 **1 万行**报告，量三件事：

1. `report_library.list_page(scope="all", limit=20)` 的 **p50 / p95 / max**（20 次取样）；
2. `counts()`（4 个 tab 徽章 + 首页 `counts.all`）的耗时；
3. **`EXPLAIN QUERY PLAN`** 里那三条语句**是否真的走了** `0006` 建的部分索引
   （`ix_reports_feed` / `ix_reports_starred` / `ix_reports_trash`）。

第 3 条比第 1 条重要：一个 3 ms 的查询如果走的是全表扫描，它会在 10 万行时变成 300 ms，
而你不会收到任何提醒。

## 为什么它**不进** pytest

它要 1 万行 × 16 KB 的写入（≈ 200 MB 临时文件、十几秒），而 pytest 的门禁必须在 CI 上稳定
跑完。§23.6.1 的原文就是"**不进 pytest**"。所以它是一把**手动的尺子**：

    python scripts/library-bench.py                 # 默认 1 万行
    python scripts/library-bench.py --rows 50000    # 想推极限时
    python scripts/library-bench.py --keep          # 保留临时库，方便自己再跑 EXPLAIN

## 纪律

* **不碰 `api/data/app.db`**：全程一个 `tempfile.mkdtemp()` 里的 sqlite 文件，跑完删掉。
* **不复制查询**：直接调用 `report_library` 的 `list_page` / `counts` / `_base_statement`，
  压的必须是产品实际执行的那条语句（复制一份 SQL 就等于压测一个不存在的系统）。
* **索引来自迁移**：临时库跑一次真正的 `alembic upgrade head`，而不是手抄 `0006` 的
  `CREATE INDEX`——手抄的索引会在下一次迁移改动后静默失真。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        with contextlib.suppress(ValueError, OSError):
            _stream.reconfigure(errors="replace")

import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import Instrument, Report  # noqa: E402
from app.services import report_library  # noqa: E402

MIGRATIONS_DIR = ROOT / "api" / "app" / "db" / "migrations"

#: 一份 ~16 KB 的 doc 骨架（与真实报告同量级：`avg(doc_bytes)=16074`，E-124）。
#: 段落里塞满中文，避免"压缩比异常好"的合成数据把 IO 量测成假的。
_SENTENCE = "公司当季营收同比增长，毛利率环比改善，渠道库存回到健康水位，费用率保持稳定。"


def _doc(index: int) -> str:
    sections = [
        {
            "id": f"s{n}",
            "title": f"第 {n} 章 · 经营与财务",
            "blocks": [
                {"type": "paragraph", "text": _SENTENCE * 6},
                {"type": "paragraph", "text": _SENTENCE * 5},
                {"type": "bullets", "items": [_SENTENCE[:40], _SENTENCE[:38], _SENTENCE[:42]]},
            ],
        }
        for n in range(1, 9)
    ]
    doc = {
        "schema": "aragon.research.report/v1",
        "instrument": {"symbol": f"6005{index % 100:02d}.SH", "market": "CN", "currency": "CNY"},
        "asOf": "2026-11-18T00:00:00Z",
        "headline": f"{_SENTENCE[:60]} #{index}",
        "rating": {"stance": "neutral", "score": 50},
        "summary": _SENTENCE * 2,
        "sections": sections,
        "sources": [{"id": f"S{n}", "title": _SENTENCE[:40], "url": "https://example.invalid"} for n in range(1, 6)],
        "disclaimer": "本报告由 AI 生成，仅供研究参考，不构成任何投资建议。",
    }
    return json.dumps(doc, ensure_ascii=False)


def _config_for(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _seed(engine: sa.Engine, rows: int) -> None:
    """`rows` 行报告，均匀分给 200 个标的（每个 50 版 = 正好是新的裁剪上限）。"""
    instruments = 200
    per_instrument = max(1, rows // instruments)
    now = time.time()
    with Session(engine) as session:
        session.bulk_insert_mappings(
            Instrument,
            [
                {
                    "id": f"CN:{600000 + n}.SH",
                    "symbol": f"{600000 + n}.SH",
                    "name": f"合成标的 {n}",
                    "market": "CN",
                    "exchange": "SSE",
                    "currency": "CNY",
                    "sector": "合成",
                    "tz": "Asia/Shanghai",
                    "created_at": "2026-02-10T00:00:00Z",
                    "updated_at": "2026-02-10T00:00:00Z",
                }
                for n in range(instruments)
            ],
        )
        batch: list[dict] = []
        for index in range(rows):
            instrument = index % instruments
            version = index // instruments + 1
            # 时间戳严格递减：keyset 游标压的正是这条路径（同秒并列会退化成 id 比较）。
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - index * 60))
            payload = _doc(index)
            batch.append(
                {
                    "id": f"{index:032x}",
                    "instrument_id": f"CN:{600000 + instrument}.SH",
                    "version": version,
                    "status": "ready",
                    "title": f"合成报告 {index}",
                    "as_of": "2026-11-18T00:00:00Z",
                    "stance": "hold",
                    "doc_json": payload,
                    "doc_bytes": len(payload.encode("utf-8")),
                    "doc_sha1": f"{index:040x}",
                    "is_latest": 1 if version == per_instrument else 0,
                    "pinned": 1 if index % 37 == 0 else 0,
                    "archived": 1 if index % 53 == 0 else 0,
                    "provisional": 0,
                    "generated_at": stamp,
                    "deleted_at": stamp if index % 29 == 0 else None,
                    "schema_version": "aragon.research.report/v1",
                }
            )
            if len(batch) >= 500:
                session.bulk_insert_mappings(Report, batch)
                session.commit()
                batch = []
        if batch:
            session.bulk_insert_mappings(Report, batch)
            session.commit()


def _percentiles(values: list[float]) -> str:
    ordered = sorted(values)
    p50 = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return f"p50 {p50:6.2f} ms   p95 {p95:6.2f} ms   max {max(ordered):6.2f} ms"


def _plan(session: Session, statement) -> list[str]:
    sql = str(statement.compile(session.get_bind(), compile_kwargs={"literal_binds": True}))
    rows = session.execute(sa.text(f"EXPLAIN QUERY PLAN {sql}")).all()
    return [str(row[-1]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="报告库列表的合成压测（不进 pytest）")
    parser.add_argument("--rows", type=int, default=10_000, help="合成报告行数（默认 1 万）")
    parser.add_argument("--samples", type=int, default=20, help="取样次数（默认 20）")
    parser.add_argument("--keep", action="store_true", help="保留临时库文件")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="library-bench-"))
    db_path = workdir / "bench.db"
    url = f"sqlite:///{db_path.as_posix()}"
    # `env.py:19-20` 的 URL 来自 `get_settings().database_url`（不是 `config` 里那个），所以
    # 临时库只能通过覆盖这份设置来生效——`test_migration_0006.py:39-48` 用的是同一招。
    # 写错这里不会报错，而是**迁移到真库上去**（静默写坏 `api/data/app.db`），故显式断言。
    settings = get_settings()
    original_url = settings.database_url
    settings.database_url = url  # type: ignore[misc]
    if get_settings().database_url != url:  # pragma: no cover - 设置被冻结时立刻失败
        raise SystemExit("无法把 DATABASE_URL 指向临时库，已中止（绝不迁移真库）")
    engine = sa.create_engine(url, future=True)
    try:
        print(f"临时库：{db_path}")
        command.upgrade(_config_for(url), "head")
        started = time.perf_counter()
        _seed(engine, args.rows)
        print(f"写入 {args.rows} 行：{time.perf_counter() - started:.1f} s"
              f"（{db_path.stat().st_size / 1e6:.0f} MB）")

        with Session(engine) as session:
            # 预热一次：第一页的编译与页缓存不属于被测对象。
            report_library.list_page(session, scope="all", limit=20)

            timings: list[float] = []
            cursor: str | None = None
            for _ in range(max(1, args.samples)):
                sample = time.perf_counter()
                page = report_library.list_page(
                    session, scope="all", limit=20, before=cursor, with_counts=False
                )
                timings.append((time.perf_counter() - sample) * 1000)
                cursor = page.nextCursor or None
            print(f"list_page(scope=all, limit=20)  {_percentiles(timings)}")

            counts_timings: list[float] = []
            for _ in range(max(1, args.samples)):
                sample = time.perf_counter()
                counted = report_library.counts(session)
                counts_timings.append((time.perf_counter() - sample) * 1000)
            print(f"counts()                        {_percentiles(counts_timings)}")
            print(f"  counts = {counted}")

            print("\nEXPLAIN QUERY PLAN（必须出现 ix_reports_feed / ix_reports_starred / ix_reports_trash）")
            for scope in ("all", "starred", "trash"):
                statement = report_library._base_statement(
                    session, scope=scope, q="", instrument_id=None
                ).limit(20)
                print(f"\n[{scope}]")
                for line in _plan(session, statement):
                    print(f"  {line}")
    finally:
        engine.dispose()
        settings.database_url = original_url  # type: ignore[misc]
        if args.keep:
            print(f"\n保留临时库：{db_path}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
