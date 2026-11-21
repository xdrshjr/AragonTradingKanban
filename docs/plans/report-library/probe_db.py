"""只读探针：把「已生成的研报到底存成了什么样」变成可复现的数字（§23 证据 E-136~E-142）。

用法（仓库根）：

    python docs/plans/report-library/probe_db.py

只读打开 `api/data/app.db`（`mode=ro`），**不写任何东西**。
本文档里的每个数字都由这个脚本产出，评审节点可以原样重跑复核。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DB = Path("api/data/app.db")

REPORT_QUERY = """
select r.id as id, i.symbol as symbol, i.name as name, r.version as v, r.status as status,
       r.is_latest as latest, r.pinned as pin, r.archived as arch, r.deleted_at as deleted,
       r.generated_at as generated_at, r.doc_bytes as bytes, json_valid(r.doc_json) as json_ok,
       (select count(*) from watchlist w where w.instrument_id = i.id) as in_watchlist
from reports r join instruments i on i.id = r.instrument_id
order by r.generated_at desc
"""


def main() -> int:
    if not DB.exists():
        print(f"未找到 {DB}（示例模式未初始化？）")
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    print("== E-136 每一行报告（按 generated_at 倒序） ==")
    rows = list(con.execute(REPORT_QUERY))
    header = ("id", "symbol", "name", "v", "status", "latest", "pin", "arch", "deleted", "generated_at", "bytes", "json_ok", "in_watchlist")
    print(" | ".join(header))
    for row in rows:
        print(" | ".join(str(row[key]) for key in header))

    live = [row for row in rows if row["deleted"] is None]
    print()
    print(f"总行数={len(rows)}  未删除={len(live)}  最新版={sum(1 for row in live if row['latest'] == 1)}")
    print(f"软删除={len(rows) - len(live)}  置顶={sum(1 for row in live if row['pin'] == 1)}  归档={sum(1 for row in live if row['arch'] == 1)}")
    print(f"doc_json 非法 JSON 的行={sum(1 for row in rows if row['json_ok'] != 1)}")

    print()
    print("== E-137 首页今天能看见什么（watchlist 口径） vs 库里有什么 ==")
    watched = [row for row in live if row["in_watchlist"] == 1 and row["latest"] == 1]
    invisible = [row for row in live if row["in_watchlist"] == 0]
    print(f"首页最近标的块可见的最新版 = {len(watched)} 份")
    print(f"不在自选里（首页永远看不见）的未删除版本 = {len(invisible)} 份：")
    for row in invisible:
        print(f"  - {row['symbol']} {row['name']} v{row['version']} ({row['status']}, {row['generated_at']})")

    print()
    print("== E-138 每个标的的版本代数（裁剪上限 MAX_VERSIONS_PER_INSTRUMENT=20，pinned 豁免） ==")
    for row in con.execute(
        """
        select i.symbol, count(r.id) as total,
               sum(case when r.id is not null and r.deleted_at is null then 1 else 0 end) as live,
               min(r.version) as vmin, max(r.version) as vmax
        from instruments i left join reports r on r.instrument_id = i.id
        group by i.id order by i.symbol
        """
    ):
        print(f"  {row['symbol']}: total={row['total']} live={row['live']} v{row['vmin']}..v{row['vmax']}")

    print()
    print("== E-139 有多少份报告带 quality（决定首页能不能显示可信度/降级标记） ==")
    for row in con.execute(
        """
        select case when quality_json is null then 'null' else 'present' end as k, count(*) as n
        from reports where deleted_at is null group by k
        """
    ):
        print(f"  quality_json={row['k']}: {row['n']}")

    print()
    print("== E-140 runs 状态分布（首页「正在生成」卡片的真源） ==")
    for row in con.execute("select status, count(*) as n from runs group by status order by n desc"):
        print(f"  {row['status']}: {row['n']}")

    print()
    print("== E-141 未删除版本的可读性抽查（最小可渲染字段是否齐全） ==")
    broken = 0
    for row in live:
        doc = json.loads(con.execute("select doc_json from reports where id = ?", (row["id"],)).fetchone()[0])
        missing = [key for key in ("schema", "instrument", "asOf", "headline", "rating", "summary", "sections") if key not in doc]
        if missing:
            broken += 1
            print(f"  {row['symbol']} v{row['version']} 缺 {missing}")
    print(f"  抽查 {len(live)} 份，缺关键字段 {broken} 份")

    print()
    print("== E-142 报告年龄（首页相对时间的量级） ==")
    now = datetime.now(UTC)
    age_query = "select i.symbol as symbol, r.version as v, r.generated_at as stamp from reports r join instruments i on i.id = r.instrument_id where r.deleted_at is null order by r.generated_at desc"
    for row in con.execute(age_query):
        print(f"  {row['symbol']} v{row['v']}: {row['stamp']} → {_age(now, row['stamp'])}")
    return 0


def _age(now: datetime, stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return "时间格式无法解析"
    hours = (now - moment).total_seconds() / 3600
    return f"{hours:.1f} 小时前"


if __name__ == "__main__":
    sys.exit(main())
