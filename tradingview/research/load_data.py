"""Parse saved Robinhood get_equity_historicals results (JSON files) into one bar table."""
import glob
import json
import sys

import pandas as pd

rows = []
for f in sorted(glob.glob(sys.argv[1])):
    for res in json.load(open(f))["data"]["results"]:          # whole file parsed
        for b in res["bars"]:
            if b.get("interpolated"):
                continue
            rows.append((res["symbol"], b["begins_at"], float(b["open_price"]), float(b["high_price"]),
                         float(b["low_price"]), float(b["close_price"]), int(b["volume"])))
df = pd.DataFrame(rows, columns=["sym", "t", "o", "h", "l", "c", "v"])
df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
df = df.drop_duplicates(["sym", "t"]).sort_values(["sym", "t"]).reset_index(drop=True)
df.to_pickle(sys.argv[2])
cov = df.assign(date=df.t.dt.date).groupby("sym").agg(days=("date", "nunique"), bars=("t", "size"))
print(f"{len(df)} bars, {df.sym.nunique()} symbols, days per symbol {cov.days.min()}-{cov.days.max()}")
