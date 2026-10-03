"""Bar-by-bar backtest of DT Core setup candidates on 5-minute regular-session data.

Mirrors how the Pine strategy runs: signals on a finished candle, entry at that
candle's close (process_orders_on_close), stops and targets checked on later
candles, stop assumed first when one candle touches both, flat by 15:55.

Usage: python backtest.py bars5.pkl
"""
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SLIP = 0.02          # dollars per share on market fills (entry, stop, time exits)
SPLIT = pd.Timestamp("2026-09-01").date()   # in-sample before, out-of-sample from here


# ---------------------------------------------------------------- indicators
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def rma(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def prepare(df):
    """Add every column a setup or filter needs, per symbol."""
    out = []
    spy = None
    for sym, g in df.groupby("sym", sort=False):
        g = g.sort_values("t").reset_index(drop=True).copy()
        g["date"] = g.t.dt.date
        g["mins"] = g.t.dt.hour * 60 + g.t.dt.minute          # bar open, minutes after midnight
        g["bar"] = g.groupby("date").cumcount()                 # 0 = 9:30 candle
        o, h, l, c, v = (g[k].to_numpy() for k in "ohlcv")
        prev_c = np.r_[c[0], c[:-1]]
        tr = np.maximum(h - l, np.maximum(abs(h - prev_c), abs(l - prev_c)))
        g["atr"] = rma(tr, 14)
        g["ema9"], g["ema20"] = ema(c, 9), ema(c, 20)
        g["vavg"] = pd.Series(v).rolling(20, min_periods=5).mean().shift(0).to_numpy()
        hlc3 = (h + l + c) / 3
        g["pv"] = hlc3 * v
        g["vwap"] = g.groupby("date")["pv"].cumsum() / g.groupby("date")["v"].cumsum()
        g["pv2"] = hlc3 * hlc3 * v
        cv = g.groupby("date")["v"].cumsum()
        var = g.groupby("date")["pv2"].cumsum() / cv - g["vwap"] ** 2
        g["vsd"] = np.sqrt(np.clip(var, 0, None))
        g["cumv"] = cv
        g["rvol"] = g["cumv"] / g.groupby("bar")["cumv"].transform(lambda s: s.shift(1).rolling(10, min_periods=5).mean())
        rng = np.where(h > l, h - l, np.nan)
        mfm = np.nan_to_num(((c - l) - (h - c)) / rng)          # Chaikin money-flow multiplier
        g["ad"] = pd.Series(mfm * v).groupby(g["date"]).cumsum()  # session accumulation/distribution
        # opening range = first 3 candles (15 minutes)
        first3 = g[g.bar < 3].groupby("date").agg(orh=("h", "max"), orl=("l", "min"))
        g = g.join(first3, on="date")
        # prior day levels and daily trend
        daily = g.groupby("date").agg(dh=("h", "max"), dl=("l", "min"), dc=("c", "last"))
        daily["pdh"], daily["pdl"], daily["pdc"] = daily.dh.shift(1), daily.dl.shift(1), daily.dc.shift(1)
        daily["sma10"] = daily.dc.rolling(10).mean().shift(1)
        rngd = daily.dh - daily.dl
        daily["nr7"] = (rngd == rngd.rolling(7).min()).shift(1).fillna(False).astype(bool)
        daily["dtrend_up"] = daily.pdc > daily.sma10
        g = g.join(daily[["pdh", "pdl", "pdc", "dtrend_up", "nr7"]], on="date")
        g["sym"] = sym
        if sym == "SPY":
            spy = g[["t", "c", "vwap", "ema9", "ema20"]].rename(columns=lambda k: "spy_" + k if k != "t" else k)
        out.append(g)
    full = pd.concat(out, ignore_index=True)
    full = full.merge(spy, on="t", how="left")
    full["spy_up"] = (full.spy_c > full.spy_vwap) & (full.spy_ema9 > full.spy_ema20)
    return full


# ---------------------------------------------------------------- setups
def signals(g, setup, p):
    """Boolean array of long entry signals at candle close, plus the stop for each."""
    n = len(g)
    o, h, l, c, v = (g[k].to_numpy() for k in "ohlcv")
    atr, vwap, e9, e20, vavg = (g[k].to_numpy() for k in ("atr", "vwap", "ema9", "ema20", "vavg"))
    bar, mins = g.bar.to_numpy(), g.mins.to_numpy()
    orh, orl, pdl = g.orh.to_numpy(), g.orl.to_numpy(), g.pdl.to_numpy()
    ad = g.ad.to_numpy()
    sig = np.zeros(n, bool)
    stop = np.full(n, np.nan)
    window = (bar >= 3) & (mins >= p.get("first_entry", 9 * 60 + 45)) & (mins < p.get("last_entry", 15 * 60))
    if setup == "orb":
        first_cross = (c > orh) & (np.r_[0, c[:-1]] <= orh) & (bar >= 3)
        raw = first_cross & (v >= p.get("orb_vol", 1.5) * vavg) & (c > vwap) & (mins < 11 * 60 + 30)
        if p.get("nr7"):
            raw &= g.nr7.to_numpy()
        sig = raw & (pd.Series(raw).groupby(g.date.to_numpy()).cumsum().to_numpy() == 1)   # once a day
        mid = (orh + orl) / 2
        stop = mid if p.get("orb_stop") == "mid" else orl
    elif setup == "sweep":
        # Wyckoff spring / Turtle Soup / ICT manipulation: trade below a support, close back above it.
        lows_prior = pd.Series(l).groupby(g.date.to_numpy()).transform(
            lambda s: s.shift(1).rolling(p.get("swing", 12), min_periods=3).min()).to_numpy()
        vband = vwap - p.get("band", 2.0) * g.vsd.to_numpy()
        pick = {"orl": orl, "swing": lows_prior, "pdl": pdl, "vband": vband}
        levels = [pick[k] for k in p.get("levels", ("orl", "swing", "pdl"))]
        swept = np.zeros(n, bool)
        for lv in levels:
            swept |= (l < lv - 0.05 * atr) & (c > lv)
        bull = (c > o) & ((c - l) >= 0.6 * (h - l))            # closes in the top 40% of its range
        sig = window & swept & bull
        if p.get("need_ad"):
            sig &= ad > np.r_[ad[:1].repeat(6), ad[:-6]]        # session A/D rising over 30 minutes
        if p.get("need_vol"):
            sig &= v >= 1.2 * vavg
        stop = l - 0.1 * atr
    elif setup == "pullback":
        trend = (c > vwap) & (e9 > e20) & (e20 > np.r_[e20[:3], e20[:-3]])
        touched = (l <= e20 + 0.1 * atr) | (np.r_[l[:1], l[:-1]] <= np.r_[e20[:1], e20[:-1]] + 0.1 * atr)
        trigger = (c > e9) & (c > o) & (c > np.r_[h[:1], h[:-1]])
        sig = window & trend & touched & trigger
        stop = pd.Series(l).rolling(3).min().to_numpy() - 0.1 * atr
    elif setup == "vwap_reclaim":
        below = pd.Series(c < vwap).rolling(6).sum().shift(1).to_numpy() >= 4
        sig = window & below & (c > vwap) & (c > o) & (v >= 1.2 * vavg)
        stop = pd.Series(l).rolling(4).min().to_numpy() - 0.1 * atr
    if p.get("min_rvol"):
        sig &= np.nan_to_num(g.rvol.to_numpy()) >= p["min_rvol"]
    if p.get("daily_trend"):
        sig &= g.dtrend_up.fillna(False).to_numpy()
    if p.get("spy_filter"):
        sig &= g.spy_up.fillna(False).to_numpy()
    risk = c - stop
    sig &= (risk >= p.get("min_risk_atr", 0.3) * atr) & (risk <= p.get("max_risk_atr", 2.5) * atr)
    return sig, stop


# ---------------------------------------------------------------- execution
@dataclass
class Trade:
    sym: str
    date: object
    setup: str
    entry: float
    stop: float
    r: float = 0.0
    how: str = ""
    bars: int = 0


def run_day(g, sig, stop, setup, p):
    """Simulate one symbol-day. Returns trades. One position at a time; re-entry allowed."""
    o, h, l, c = (g[k].to_numpy() for k in "ohlc")
    mins = g.mins.to_numpy()
    trades, i, n = [], 0, len(g)
    day_r, count = 0.0, 0
    while i < n:
        if not sig[i] or count >= p.get("max_trades", 3) or day_r <= -p.get("day_stop_r", 2.0) or mins[i] >= 15 * 60 + 50:
            i += 1
            continue
        entry = c[i] + SLIP
        st = stop[i]
        risk = entry - st
        if risk <= 0:
            i += 1
            continue
        tgt = entry + p.get("target_r", 2.0) * risk
        tp1 = entry + 1.0 * risk
        partial = p.get("exit") == "partial"
        half_done, cur_stop, realized, size = False, st, 0.0, 1.0
        how, j = "eod", i + 1
        while j < n:
            # stop first if a candle touches both
            if l[j] <= cur_stop:
                px = min(o[j], cur_stop) - SLIP
                realized += size * (px - entry) / risk
                how = "be" if half_done and cur_stop >= entry else "stop"
                break
            if partial and not half_done and h[j] >= tp1:
                realized += 0.5 * (max(o[j], tp1) - entry) / risk
                size, half_done, cur_stop = 0.5, True, entry
                if h[j] >= tgt:
                    realized += 0.5 * (max(o[j], tgt) - entry) / risk
                    how = "target"
                    break
                j += 1
                continue
            if h[j] >= tgt:
                realized += size * (max(o[j], tgt) - entry) / risk
                how = "target"
                break
            if p.get("time_stop") and not half_done and j - i >= p["time_stop"] and c[j] < tp1:
                realized += size * (c[j] - SLIP - entry) / risk
                how = "time"
                break
            if p.get("trail_e9") and half_done and c[j] < g.ema9.iat[j]:
                realized += size * (c[j] - SLIP - entry) / risk
                how = "trail"
                break
            if mins[j] >= 15 * 60 + 50:
                realized += size * (c[j] - SLIP - entry) / risk
                how = "eod"
                break
            j += 1
        trades.append(Trade(g.sym.iat[i], g.date.iat[i], setup, entry, st, realized, how, j - i))
        day_r += realized
        count += 1
        i = j + 1
    return trades


def backtest(data, setup, p, syms=None):
    out = []
    for sym, g in data.groupby("sym", sort=False):
        if syms and sym not in syms:
            continue
        sig, stop = signals(g, setup, p)
        for _, idx in g.groupby("date").indices.items():
            gd = g.iloc[idx]
            out += run_day(gd, sig[idx], stop[idx], setup, p)
    return pd.DataFrame([t.__dict__ for t in out])


def stats(t):
    if t.empty:
        return dict(n=0)
    eq = t.r.cumsum()
    return dict(n=len(t), win=round((t.r > 0).mean() * 100, 1), avgR=round(t.r.mean(), 3),
                totR=round(t.r.sum(), 1), maxDD=round((eq.cummax() - eq).max(), 1),
                hit2R=round((t.how == "target").mean() * 100, 1),
                perDay=round(len(t) / max(1, t.date.nunique()), 2))


def report(name, t):
    ins, oos = t[t.date < SPLIT], t[t.date >= SPLIT]
    print(f"{name:<44} IN {stats(ins)}\n{'':<44} OUT {stats(oos)}")


if __name__ == "__main__":
    data = prepare(pd.read_pickle(sys.argv[1]))
    data.to_pickle(sys.argv[1].replace(".pkl", "_prep.pkl"))
    print("prepared", len(data), "bars")
