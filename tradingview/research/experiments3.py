"""Round 3: two published intraday strategies by Zarattini, Barbon and Aziz (2024).

A) 5-minute opening range breakout on stocks in play
   "A Profitable Day Trading Strategy For The U.S. Equity Market" (SSRN 4729284).
   First 5-minute candle sets direction (green = long only, red = short only).
   Stop-entry at that candle's high (low). Stop = 10% of the 14-day ATR.
   Stocks in play: first-candle volume >= its 14-day average (RVOL >= 1).
   Exit at the close, or variants with a fixed target / breakeven move.

B) Noise-area momentum on SPY / QQQ
   "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF" (SSRN 4824172).
   Noise band at each time of day = average absolute move from the open over 14 days.
   Check at :00 and :30 from 10:00. Long above the upper band, short below the lower band.
   Trailing stop = max(upper band, VWAP) for longs (min(lower band, VWAP) for shorts). Flat at close.
"""
import sys

import numpy as np
import pandas as pd

from backtest import SLIP, SPLIT, stats

data = pd.read_pickle(sys.argv[1])


# ------------------------------------------------------------------ A) ORB-5 stocks in play
def daily_table(g):
    d = g.groupby("date").agg(dh=("h", "max"), dl=("l", "min"), dc=("c", "last"), do=("o", "first"))
    pc = d.dc.shift(1)
    tr = np.maximum(d.dh - d.dl, np.maximum(abs(d.dh - pc), abs(d.dl - pc)))
    d["atr14"] = tr.rolling(14).mean().shift(1)              # known before the open
    first = g[g.bar == 0].set_index("date")
    d["v1"] = first.v
    d["rvol1"] = d.v1 / d.v1.shift(1).rolling(14).mean()
    return d


def orb5(data, sides=("long",), min_rvol=1.0, stop_atr=0.10, target_r=None, be_at_r=None, top_n=None):
    trades = []
    days = {}
    for sym, g in data.groupby("sym", sort=False):
        d = daily_table(g)
        for date, idx in g.groupby("date").indices.items():
            row = d.loc[date]
            if not (row.rvol1 >= min_rvol) or np.isnan(row.atr14):
                continue
            days.setdefault(date, []).append((row.rvol1, sym, g.iloc[idx], row.atr14))
    for date, cands in days.items():
        cands.sort(key=lambda x: -x[0])
        for rv, sym, gd, atr in (cands[:top_n] if top_n else cands):
            o, h, l, c = (gd[k].to_numpy() for k in "ohlc")
            if c[0] == o[0]:
                continue
            side = "long" if c[0] > o[0] else "short"
            if side not in sides:
                continue
            s = 1 if side == "long" else -1
            trig = h[0] if s == 1 else l[0]
            risk = stop_atr * atr
            entered = False
            for j in range(1, len(gd)):
                if not entered:
                    if (s == 1 and h[j] >= trig) or (s == -1 and l[j] <= trig):
                        entry = (max(o[j], trig) if s == 1 else min(o[j], trig)) + s * SLIP
                        stop = entry - s * risk
                        tgt = entry + s * target_r * risk if target_r else None
                        entered, moved = True, False
                        # same candle: assume the stop is hit if the candle reaches it
                        if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
                            trades.append((sym, date, side, -1.0 - 2 * SLIP / risk, "stop"))
                            break
                    continue
                if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
                    px = (min(o[j], stop) if s == 1 else max(o[j], stop)) - s * SLIP
                    trades.append((sym, date, side, s * (px - entry) / risk, "be" if moved else "stop"))
                    break
                if tgt is not None and ((s == 1 and h[j] >= tgt) or (s == -1 and l[j] <= tgt)):
                    trades.append((sym, date, side, float(target_r), "target"))
                    break
                if be_at_r and not moved and s * (c[j] - entry) >= be_at_r * risk:
                    stop, moved = entry, True
                if j == len(gd) - 1:
                    px = c[j] - s * SLIP
                    trades.append((sym, date, side, s * (px - entry) / risk, "eod"))
    t = pd.DataFrame(trades, columns=["sym", "date", "side", "r", "how"])
    return t


def show(name, t):
    ins, oos = t[t.date < SPLIT], t[t.date >= SPLIT]
    f = lambda x: {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in stats(x).items()}
    print(f"{name:<48} IN {f(ins)}\n{'':<48} OUT {f(oos)}")
    if len(t):
        print(f"{'':<48} median R {t.r.median():.2f}, best {t.r.max():.1f}R, share of R from top 10% trades "
              f"{t.r.nlargest(max(1, len(t)//10)).sum() / max(1e-9, t.r[t.r > 0].sum()) * 100:.0f}%")


print("A) ORB-5, stocks in play (Zarattini, Barbon & Aziz 2024)")
show("long+short, RVOL>=1, exit at close", orb5(data, sides=("long", "short")))
show("long only,  RVOL>=1, exit at close", orb5(data))
show("long only,  RVOL>=1.5, exit at close", orb5(data, min_rvol=1.5))
show("long+short, top 5 RVOL per day, exit at close", orb5(data, sides=("long", "short"), top_n=5))
show("long only,  RVOL>=1, fixed 2R target", orb5(data, target_r=2))
show("long only,  RVOL>=1, BE at +1R, exit at close", orb5(data, be_at_r=1))


# ------------------------------------------------------------------ B) noise-area momentum
def noise_area(g, sides=("long",), lookback=14):
    g = g.copy()
    d_open = g.groupby("date")["o"].transform("first")
    g["move"] = (g.c / d_open - 1).abs()
    piv = g.pivot_table(index="date", columns="bar", values="move")
    sigma = piv.shift(1).rolling(lookback).mean()                 # average move at this time, past 14 days
    pc = g.groupby("date")["c"].last().shift(1)
    res = []
    for date, gd in g.groupby("date"):
        if date not in sigma.index or sigma.loc[date].isna().all() or np.isnan(pc.get(date, np.nan)):
            continue
        sig = sigma.loc[date].reindex(gd.bar).to_numpy()
        op = gd.o.iat[0]
        up = max(op, pc[date]) * (1 + sig)
        dn = min(op, pc[date]) * (1 - sig)
        c, vw, mins = gd.c.to_numpy(), gd.vwap.to_numpy(), gd.mins.to_numpy()
        pos, entry, day_ret = 0, 0.0, 0.0
        for j in range(len(gd)):
            check = mins[j] + 5 >= 600 and (mins[j] + 5) % 30 == 0      # candle closing at :00 or :30
            last = j == len(gd) - 1
            if pos != 0:
                stop = max(up[j], vw[j]) if pos == 1 else min(dn[j], vw[j])
                if last or (check and ((pos == 1 and c[j] < stop) or (pos == -1 and c[j] > stop))):
                    day_ret += pos * (c[j] / entry - 1) - 2 * SLIP / entry
                    pos = 0
            if pos == 0 and check and not last and mins[j] < 15 * 60 + 30:
                if c[j] > up[j] and "long" in sides:
                    pos, entry = 1, c[j]
                elif c[j] < dn[j] and "short" in sides:
                    pos, entry = -1, c[j]
        res.append((date, day_ret))
    return pd.DataFrame(res, columns=["date", "ret"])


print("\nB) Noise-area momentum (Zarattini, Aziz & Barbon 2024), daily % return, 1x, after slippage")
for sym in ("SPY", "QQQ"):
    g = data[data.sym == sym]
    for sides in (("long",), ("long", "short")):
        r = noise_area(g, sides)
        for label, part in (("IN", r[r.date < SPLIT]), ("OUT", r[r.date >= SPLIT])):
            traded = part[part.ret != 0]
            print(f"{sym} {'+'.join(sides):<11} {label:<3} days {len(part):>3}, traded {len(traded):>3}, "
                  f"total {part.ret.sum() * 100:+.2f}%, win days {(traded.ret > 0).mean() * 100 if len(traded) else 0:.0f}%, "
                  f"worst day {part.ret.min() * 100:+.2f}%")
    bh = g.groupby("date").c.last()
    print(f"{sym} buy and hold over the same days: {(bh.iat[-1] / g.o.iat[0] - 1) * 100:+.2f}%")
