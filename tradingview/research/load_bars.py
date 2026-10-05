"""Turn saved Robinhood get_equity_historicals results (JSON files) into one bar table.

    python load_bars.py "<folder>/*.txt" bars5.pkl 5minute
    python load_bars.py "<folder>/*.txt" daily.pkl day

Files with other intervals are skipped, so all saved results can share one folder.
Gap-filling ("interpolated") bars are dropped.
"""
import glob
import json
import sys

import pandas as pd


def main(pattern, out, interval):
    rows = []
    for f in sorted(glob.glob(pattern)):
        try:
            results = json.load(open(f))["data"]["results"]
        except (ValueError, KeyError):
            continue
        for res in results:
            if res.get("interval") != interval:
                continue
            for b in res["bars"]:
                if b.get("interpolated"):
                    continue
                rows.append((res["symbol"], b["begins_at"], float(b["open_price"]), float(b["high_price"]),
                             float(b["low_price"]), float(b["close_price"]), int(b["volume"])))
    df = pd.DataFrame(rows, columns=["sym", "t", "o", "h", "l", "c", "v"])
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = df.drop_duplicates(["sym", "t"]).sort_values(["sym", "t"]).reset_index(drop=True)
    df.to_pickle(out)
    days = df.t.dt.tz_convert("UTC").dt.date if interval == "day" else df.t.dt.date
    cov = df.assign(date=days).groupby("sym").date.agg(["nunique", "min", "max"])
    print(f"{len(df)} bars, {df.sym.nunique()} symbols, days per symbol {cov['nunique'].min()}-{cov['nunique'].max()}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
