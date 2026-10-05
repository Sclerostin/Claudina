"""Check that AMD_Day_Trader.pine's rules match the research simulator, signal for signal.

Re-runs the Pine script's logic line by line in Python: one pass over every 5-minute candle of
a stock, with `var` state carried across days exactly as Pine does. Then compares its signals
(time, grade, stop, target) with lab.signals_po3 + lab.grade + the filters in lab.passes.

    python pine_parity.py bars5.pkl daily.pkl daily_long.pkl
"""
import sys

import numpy as np
import pandas as pd

import lab


def pine_signals(x, grades, or_min=15, cap_atr=2.0, target_r=2.0, use_mkt_vwap=True):
    """x: one symbol's prepared candles (lab.prepare output), in time order."""
    out = []
    cur_date = None
    orH = orL = np.nan
    swept, manip_low, setup_used = False, np.nan, False
    o, h, l, c = (x[k].to_numpy() for k in "ohlc")
    atr, pdl, mins, dates = x.atr.to_numpy(), x.pdl.to_numpy(), x.mins.to_numpy(), x.date.to_numpy()
    mkt_vwap = x.spy_above_vwap.to_numpy()
    or_end = 570 + or_min
    for i in range(len(c)):
        rth = 570 <= mins[i] < 960
        day_start = rth and (cur_date is None or dates[i] != cur_date)
        if day_start:
            cur_date = dates[i]
        in_or = rth and mins[i] < or_end
        after_or = rth and mins[i] >= or_end
        manip_win = after_or and mins[i] < 660
        trig_win = after_or and mins[i] < 720
        eod = rth and mins[i] + 5 >= 955
        grade = grades.get(dates[i], "B")
        if day_start:
            orH, orL = h[i], l[i]
        elif in_or:
            orH, orL = max(orH, h[i]), min(orL, l[i])
        if day_start:
            swept, manip_low, setup_used = False, np.nan, False
        sweep_line = orL if np.isnan(pdl[i]) else max(orL, pdl[i])
        sweep_now = manip_win and not swept and not np.isnan(orL) and l[i] < sweep_line
        if sweep_now:
            swept, manip_low = True, l[i]
        elif swept and not setup_used:
            manip_low = min(manip_low, l[i])
        rng = h[i] - l[i]
        strong = c[i] > o[i] and rng > 0 and (c[i] - l[i]) >= 0.6 * rng
        trigger = trig_win and swept and not setup_used and strong and c[i] > orH and i > 0 and c[i - 1] <= orH
        stop = max(manip_low, c[i] - cap_atr * atr[i]) - 0.1 * atr[i]
        risk = c[i] - stop
        ok = trigger and risk >= 0.3 * atr[i] and (not use_mkt_vwap or mkt_vwap[i]) and not eod
        if trigger:
            setup_used = True
        if ok and grade in ("A", "B"):
            out.append((x.sym.iat[i], dates[i], int(mins[i]), grade, round(stop, 6), round(c[i] + target_r * risk, 6)))
    return out


def lab_signals(DL, grades, or_bars=3):
    p = dict(trigger="orh", or_bars=or_bars, levels=("orl", "pdl"), manip_end=660, trig_end=720, close_pos=0.6,
             stop="cap", cap=2.0, buf=0.1, spy="above_vwap", max_risk=12, min_risk=0.3)
    out = []
    for a in DL:
        for i, stop, _ in lab.signals_po3(a, p):
            g = grades.get(a["date"], "B")
            if g in ("A", "B") and lab.passes(a, i, stop, p) and a["mins"][i] < lab.EOD_MINS:
                risk = a["c"][i] - stop
                out.append((a["sym"], a["date"], int(a["mins"][i]), g, round(stop, 6), round(a["c"][i] + 2 * risk, 6)))
    return out


def main(bars5, daily, daily_long):
    data = lab.prepare(pd.read_pickle(bars5), pd.read_pickle(daily))
    data = data[~data.sym.isin(["SPY", "QQQ", "IWM"])]
    m = lab.market_days(pd.read_pickle(daily_long))
    grades = {dt: lab.grade(m, dt) for dt in sorted(data.date.unique())}
    for or_min, or_bars in ((15, 3), (30, 6)):
        pine = []
        for _, x in data.groupby("sym", sort=False):
            pine += pine_signals(x.reset_index(drop=True), grades, or_min=or_min)
        ref = lab_signals(lab.days(data), grades, or_bars=or_bars)
        a, b = set(pine), set(ref)
        print(f"{or_min}-minute range: Pine mirror {len(a)} signals, research {len(b)}, "
              f"only in Pine {len(a - b)}, only in research {len(b - a)}")
        for s in sorted(a ^ b)[:10]:
            print("   differs:", s, "Pine" if s in a else "research")
        assert a == b, "Pine rules and research rules disagree"
    print("OK: the Pine rules reproduce the research signals exactly")


if __name__ == "__main__":
    main(*sys.argv[1:4])
