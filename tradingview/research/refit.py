"""Learn Edge Reader's 5-minute points from Measure-mode trades exported from TradingView.

1. On a stock's 5-minute chart, set Edge Reader's Mode to "Measure long" (or "Measure short"),
   run Deep Backtesting over as many years as you have, and export the List of Trades as CSV.
2. Repeat for other stocks (one CSV each; the file name is used as the stock name).
3. python refit.py trades/*.csv                 report only
   python refit.py trades/*.csv --write         also update points.json and both Pine scripts

Each Measure trade's signal name is "M|L|<risk>|<day score>|<46 condition bits>". The script
rebuilds conditions -> R for every trade, tests new points out of sample (fit on earlier years,
score the next), and writes them only if they hold up: the out-of-sample deciles line up
(correlation 0.5 or more) and the best tenth beats the average by at least 2 standard errors.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import points as P

HERE = Path(__file__).parent
POINTS_JSON = HERE / "points.json"
N_BITS = len(P.FIVE_MIN)


def _col(cols, *keys):
    for c in cols:
        low = c.lower().strip()
        if any(low.startswith(k) or k in low for k in keys):
            return c
    raise ValueError(f"no column like {keys} in {cols}")


def read_trades(path, tick=0.01, slip_ticks=2):
    """One TradingView List-of-trades CSV -> one row per Measure trade."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return pd.DataFrame()
    cols = list(rows[0])
    c_no, c_type, c_sig = _col(cols, "trade #", "trade"), _col(cols, "type"), _col(cols, "signal")
    c_time, c_px = _col(cols, "date"), _col(cols, "price")
    trades = {}
    for r in rows:
        t = trades.setdefault(r[c_no], {})
        kind = r[c_type].lower()
        if kind.startswith("entry"):
            t.update(signal=r[c_sig], entry=float(r[c_px].replace(",", "")), time=r[c_time])
        elif kind.startswith("exit"):
            t.update(exit=float(r[c_px].replace(",", "")))
    out = []
    for t in trades.values():
        parts = t.get("signal", "").split("|")
        if len(parts) != 5 or parts[0] != "M" or "exit" not in t:
            continue
        side, risk, day, bits = parts[1], float(parts[2]), float(parts[3]), parts[4]
        if len(bits) != N_BITS or risk <= 0:
            raise ValueError(f"{path}: a trade has {len(bits)} condition bits, this version expects {N_BITS}")
        sign = 1 if side == "L" else -1
        net = sign * (t["exit"] - t["entry"]) / risk
        out.append(dict(sym=Path(path).stem, side=side, time=pd.to_datetime(t["time"]), day=day,
                        r=net + 2 * slip_ticks * tick / risk, **{k: int(b) for k, b in zip(P.FIVE_MIN, bits)}))
    return pd.DataFrame(out)


def fit_side(df, lam):
    """gross R = A + K * day + sum(points of true conditions); ridge on the points only."""
    X = np.c_[np.ones(len(df)), df.day.to_numpy(), df[P.FIVE_MIN].to_numpy(float)]
    pen = np.diag([1e-6, 1e-6] + [lam] * N_BITS)
    b = np.linalg.solve(X.T @ X + pen, X.T @ df.r.to_numpy())
    return dict(a=float(b[0]), k=float(b[1]), five=[float(v) for v in b[2:]])


def predict(fit, df):
    return fit["a"] + fit["k"] * df.day.to_numpy() + df[P.FIVE_MIN].to_numpy(float) @ np.array(fit["five"])


def walk_forward(df, lam, by):
    period = df.time.dt.year if by == "year" else df.time.dt.to_period("M")
    keys = sorted(period.unique())
    out = []
    for k in keys[1:]:
        tr, te = (period < k).to_numpy(), (period == k).to_numpy()
        if tr.sum() < 200 or te.sum() == 0:
            continue
        f = fit_side(df[tr], lam)
        out.append(pd.DataFrame({"period": str(k), "p": predict(f, df[te]), "r": df.r[te].to_numpy()}))
    return pd.concat(out) if out else pd.DataFrame()


def report(side, df, lam, by):
    o = walk_forward(df, lam, by)
    print(f"\n=== {'longs' if side == 'L' else 'shorts'}: {len(df)} Measure trades, {df.sym.nunique()} stocks, "
          f"{df.time.min():%Y-%m-%d} to {df.time.max():%Y-%m-%d}; average gross R {df.r.mean():+.3f}")
    if o.empty or len(o) < 100:
        print("  not enough history for an out-of-sample test (need at least two periods)")
        return False
    q = pd.qcut(o.p.rank(method="first"), 10, labels=False)
    cal = o.groupby(q).agg(pred=("p", "mean"), real=("r", "mean"))
    rho = float(np.corrcoef(cal.pred, cal.real)[0, 1])
    best = o.r[(q == 9).to_numpy()]
    top, edge_t = float(best.mean()), float((best.mean() - o.r.mean()) / (best.std(ddof=1) / np.sqrt(len(best))))
    print(f"  out of sample ({o.period.nunique()} {by}s): predicted vs realized by decile, corr {rho:+.2f}")
    print(f"  realized gross R by decile, low to high: {cal.real.round(3).tolist()}")
    print(f"  best tenth {top:+.3f}R vs all {o.r.mean():+.3f}R ({edge_t:+.1f} standard errors)")
    ok = rho >= 0.5 and edge_t >= 2.0
    print("  verdict:", "holds up out of sample" if ok else "does NOT hold up - keep the current points")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--write", action="store_true", help="update points.json and Edge_Reader.pine if it holds up")
    ap.add_argument("--by", choices=["year", "month"], default="year")
    ap.add_argument("--lam", type=float, default=200.0, help="ridge penalty on the 5-minute points")
    ap.add_argument("--tick", type=float, default=0.01)
    args = ap.parse_args()
    df = pd.concat([read_trades(p, args.tick) for p in args.csv], ignore_index=True)
    if df.empty:
        sys.exit("no Measure trades found (signal names must start with M|)")
    pts = json.loads(POINTS_JSON.read_text())
    changed = []
    for side, key in (("L", "long"), ("S", "short")):
        part = df[df.side == side]
        if part.empty:
            continue
        if report(side, part, args.lam, args.by):
            f = fit_side(part, args.lam)
            pts[key].update(a=f["a"], k=f["k"], five=f["five"])
            changed.append(f"{key} {len(part)} trades {part.time.min():%Y}-{part.time.max():%Y}")
            top = sorted(zip(P.FIVE_MIN_NAMES.values(), f["five"]), key=lambda kv: -abs(kv[1]))[:8]
            print("  biggest points:", ", ".join(f"{n} {v:+.3f}" for n, v in top))
    if changed and args.write:
        pts["note"] = "day points: 2015-2025 daily data; 5-minute points: " + "; ".join(changed)
        POINTS_JSON.write_text(json.dumps(pts, indent=1))
        P.write_all(pts)
        print(f"\nwrote new points to {POINTS_JSON.name}, Edge_Reader.pine and Edge_Radar.pine. Copy both scripts into TradingView again.")
    elif changed:
        print("\nrun again with --write to apply them")


if __name__ == "__main__":
    main()
