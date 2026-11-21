"""Read-only production probe #3: run events (payload json), cache integrity."""

import json
import sqlite3

DB = "/opt/aragon-trading/api/data/app.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=== newest run events (warn/error) ===")
for row in conn.execute(
    """
    select seq, type, payload_json from run_events
    where run_id = (select id from runs order by rowid desc limit 1)
    order by seq
    """
):
    payload = json.loads(row["payload_json"])
    if str(payload.get("level")) in ("warn", "error"):
        print(row["seq"], row["type"], "|", payload.get("message"))

print()
print("=== instruments ===")
for row in conn.execute(
    "select symbol, name, market, sector, currency from instruments order by symbol"
):
    print(dict(row))

print()
print("=== cached bar series ===")
for row in conn.execute(
    """
    select i.symbol, b.source, b.timeframe, b.adjust, count(*) as n,
           datetime(min(b.ts), 'unixepoch') as first_ts,
           datetime(max(b.ts), 'unixepoch') as last_ts
    from bars b join instruments i on i.id = b.instrument_id
    group by i.symbol, b.source, b.timeframe, b.adjust order by n desc limit 20
    """
):
    print(dict(row))

print()
print("=== OHLC-invalid cached bars ===")
bad = conn.execute(
    """
    select i.symbol, datetime(b.ts, 'unixepoch') as day, b.open, b.high, b.low, b.close, b.source
    from bars b join instruments i on i.id = b.instrument_id
    where b.high < b.open - 0.0001 or b.high < b.close - 0.0001
       or b.low > b.open + 0.0001 or b.low > b.close + 0.0001
       or b.high < b.low - 0.0001 or b.close <= 0 or b.open <= 0
    order by b.ts desc limit 20
    """
).fetchall()
print("count:", len(bad))
for row in bad:
    print(dict(row))

print()
print("=== quote vs last cached bar ===")
for row in conn.execute(
    """
    select i.symbol, q.last as quote_last, q.source as quote_source,
           (select b.close from bars b where b.instrument_id = i.id
            order by b.ts desc limit 1) as last_bar_close,
           datetime((select max(b.ts) from bars b where b.instrument_id = i.id), 'unixepoch') as last_bar_day
    from quotes q join instruments i on i.id = q.instrument_id
    """
):
    print(dict(row))

print()
print("=== weekly bars present? ===")
for row in conn.execute(
    "select distinct timeframe, adjust, source from bars"
):
    print(dict(row))
