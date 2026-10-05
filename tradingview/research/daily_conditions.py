"""Daily chart conditions known at the open, and what the day did next (2015 - 2026).

    build(daily_bars) -> one row per stock-day: 0/1 condition columns plus outcomes
        long_r / short_r  open-to-close in R, where R = half a daily ATR, after slippage
        long_b / short_b  a 2R bracket from the open: stop 0.5 ATR, target 1 ATR, stop first if both
    condition_columns(table) -> the names of the condition columns

Every condition uses yesterday's daily candle and earlier ones, plus today's opening price.
"""
import numpy as np
import pandas as pd

OUTCOMES = ["long_r", "short_r", "long_b", "short_b"]
NOT_CONDITIONS = {"sym", "t", "o", "h", "l", "c", "v", "date", "datr", "ret20", "y"} | set(OUTCOMES)
MARKET = ["tr_up", "tr_dn", "lt_up", "lt_dn", "rsi2_lo", "rsi2_hi", "gap_up_big", "gap_dn_big", "gap_up", "gap_dn",
          "down3", "up3", "str_up", "str_dn"]


def _rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(c, n):
    delta = c.diff()
    return 100 - 100 / (1 + _rma(delta.clip(lower=0), n) / _rma(-delta.clip(upper=0), n))


def _stock(x):
    o, h, l, c, v = x.o, x.h, x.l, x.c, x.v
    pc = c.shift(1)
    atr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs())).rolling(14).mean()
    prev = lambda s: s.shift(1)                       # known at today's open
    sma20, sma50, sma200 = (c.rolling(n).mean() for n in (20, 50, 200))
    hi20, lo20 = h.rolling(20).max(), l.rolling(20).min()
    rng = (h - l).replace(0, np.nan)
    effort = (v * np.sign(c - o)).rolling(5).sum() / v.rolling(5).sum()
    pos20 = prev((c - lo20) / (hi20 - lo20).replace(0, np.nan))
    stretch = prev((c - sma20) / atr)
    rvol = prev(v / v.rolling(20).mean())
    gap = (o - pc) / prev(atr)
    f = {
        "tr_up": prev((c > sma20) & (sma20 > sma50)), "tr_dn": prev((c < sma20) & (sma20 < sma50)),
        "lt_up": prev(c > sma200), "lt_dn": prev(c < sma200),
        "rng_low": pos20 < 0.2, "rng_high": pos20 > 0.8,
        "str_up": stretch > 2, "str_dn": stretch < -2,
        "nr7": prev(rng == rng.rolling(7).min()), "inside": prev((h < h.shift(1)) & (l > l.shift(1))),
        "d_spring": prev((l < l.shift(1)) & (c > l.shift(1)) & ((c - l) >= 0.6 * rng)),
        "d_upthr": prev((h > h.shift(1)) & (c < h.shift(1)) & ((h - c) >= 0.6 * rng)),
        "brk_up": prev(c > hi20.shift(1)), "brk_dn": prev(c < lo20.shift(1)),
        "bull_c": prev((c > o) & ((c - l) >= 0.75 * rng)), "bear_c": prev((c < o) & ((h - c) >= 0.75 * rng)),
        "vol_hi": rvol > 1.5, "vol_lo": rvol < 0.7,
        "buyers": prev(effort > 0.3), "sellers": prev(effort < -0.3),
        "rsi2_lo": prev(_rsi(c, 2) < 10), "rsi2_hi": prev(_rsi(c, 2) > 90),
        "rsi14_lo": prev(_rsi(c, 14) < 30), "rsi14_hi": prev(_rsi(c, 14) > 70),
        "down3": prev((c < pc) & (pc < pc.shift(1)) & (pc.shift(1) < pc.shift(2))),
        "up3": prev((c > pc) & (pc > pc.shift(1)) & (pc.shift(1) > pc.shift(2))),
        "gap_up_big": gap > 1, "gap_up": (gap > 0.25) & (gap <= 1),
        "gap_dn": (gap < -0.25) & (gap >= -1), "gap_dn_big": gap < -1,
        "open_above_pdh": o > prev(h), "open_below_pdl": o < prev(l),
    }
    x = x.copy()
    for k, s in f.items():
        x[k] = s.fillna(False).astype(np.int8)
    x["datr"] = prev(atr)
    x["ret20"] = prev(c / c.shift(20) - 1)
    slip = 0.01 + 0.0001 * o
    R = 0.5 * x.datr
    x["long_r"] = (c - o - 2 * slip) / R
    x["short_r"] = (o - c - 2 * slip) / R
    x["long_b"] = np.where(l <= o - R, -1 - 2 * slip / R, np.where(h >= o + 2 * R, 2 - slip / R, x.long_r))
    x["short_b"] = np.where(h >= o + R, -1 - 2 * slip / R, np.where(l <= o - 2 * R, 2 - slip / R, x.short_r))
    return x


def build(daily):
    d = daily.copy()
    d["date"] = pd.to_datetime(d.t.dt.tz_convert("UTC").dt.date)          # daily bars are stamped 00:00 UTC
    d = d.drop_duplicates(["sym", "date"]).sort_values(["sym", "date"]).reset_index(drop=True)
    d = pd.concat([_stock(x) for _, x in d.groupby("sym", sort=False)])
    spy = d[d.sym == "SPY"].set_index("date")
    for k in MARKET:
        d["m_" + k] = d.date.map(spy[k]).fillna(0).astype(np.int8)
    rel = d.ret20 - d.date.map(spy.ret20)
    d["rs_up"], d["rs_dn"] = (rel > 0.05).astype(np.int8), (rel < -0.05).astype(np.int8)
    d["mon"] = (d.date.dt.dayofweek == 0).astype(np.int8)
    d["fri"] = (d.date.dt.dayofweek == 4).astype(np.int8)
    d["tom"] = (d.date.dt.day <= 3).astype(np.int8)
    return d[d.datr.notna() & np.isfinite(d.long_r) & ~d.sym.isin(["SPY", "QQQ", "IWM"])].reset_index(drop=True)


def condition_columns(d):
    return [k for k in d.columns if k not in NOT_CONDITIONS]
