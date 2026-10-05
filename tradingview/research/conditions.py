"""Chart conditions on every 5-minute candle, and what a trade from that candle went on to do.

Each condition is a yes/no fact about the chart that a trader can see, taken from the day-trading
literature. Every one is causal (uses only that candle and earlier ones) and is computed the same
way in the Pine script.

    add_conditions(prepared)   -> the table with one 0/1 column per condition (CONDITIONS)
    add_outcomes(prepared)     -> long_r / short_r: the R a 2R bracket earned from each candle

The trade from each candle: enter at its close (plus slippage), stop 1.5 ATR away, target 2R
(3 ATR), whatever is left closes on the 3:50 candle. Stop counted first if one candle touches both.
"""
import numpy as np
import pandas as pd

from lab import EOD_MINS, slip

STOP_ATR = 1.5          # risk = 1.5 x ATR(14) of 5-minute candles
TARGET_R = 2.0
FIRST_BAR = 3           # no signals in the first 15 minutes
LAST_ENTRY = 15 * 60 + 30


def _rma(x, n):
    return x.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(c, n):
    d = c.diff()
    up, dn = _rma(d.clip(lower=0), n), _rma(-d.clip(upper=0), n)
    return 100 - 100 / (1 + up / dn)


def _recent(flag, n):
    """True if flag was true on this candle or one of the n-1 before it."""
    return flag.astype(float).rolling(n, min_periods=1).max().astype(bool)


# name: (family, plain-English meaning)  -- the order is the order of the Pine points table
CONDITIONS = {
    "dtrend_up":    ("trend", "Daily uptrend: yesterday's close above its 20-day average, 20-day above 50-day"),
    "dtrend_dn":    ("trend", "Daily downtrend: yesterday's close below its 20-day average, 20-day below 50-day"),
    "itrend_up":    ("trend", "Intraday uptrend: above VWAP and the 20-candle EMA rising"),
    "itrend_dn":    ("trend", "Intraday downtrend: below VWAP and the 20-candle EMA falling"),
    "z_lt_m2":      ("location", "More than 2 standard deviations below VWAP"),
    "z_m2_m1":      ("location", "1 to 2 standard deviations below VWAP"),
    "z_p1_p2":      ("location", "1 to 2 standard deviations above VWAP"),
    "z_gt_p2":      ("location", "More than 2 standard deviations above VWAP"),
    "discount":     ("location", "In the lower third of today's range (discount)"),
    "premium":      ("location", "In the upper third of today's range (premium)"),
    "above_orh":    ("location", "Above the opening range high"),
    "below_orl":    ("location", "Below the opening range low"),
    "above_pdh":    ("location", "Above yesterday's high"),
    "below_pdl":    ("location", "Below yesterday's low"),
    "ext_up":       ("location", "Stretched: more than 3 ATR above the 20-candle EMA"),
    "ext_dn":       ("location", "Stretched: more than 3 ATR below the 20-candle EMA"),
    "compress":     ("phase", "Accumulation: the last hour's range is under 3 ATR"),
    "spring":       ("phase", "Manipulation down: a low was swept and reclaimed in the last 3 candles"),
    "upthrust":     ("phase", "Manipulation up: a high was swept and rejected in the last 3 candles"),
    "bos_up":       ("phase", "Expansion up: close above the last hour's high"),
    "bos_dn":       ("phase", "Expansion down: close below the last hour's low"),
    "bull_bar":     ("candle", "Strong green candle: closes in its top quarter"),
    "bear_bar":     ("candle", "Strong red candle: closes in its bottom quarter"),
    "rvol_lo":      ("volume", "Quiet: volume since the open under 0.8x normal"),
    "rvol_hi":      ("volume", "In play: volume since the open over 1.5x normal"),
    "climax":       ("volume", "Climax candle: 3x normal volume for this time of day"),
    "buyers":       ("volume", "Buyers in control: last 6 candles' volume mostly on green candles"),
    "sellers":      ("volume", "Sellers in control: last 6 candles' volume mostly on red candles"),
    "rsi_lo":       ("momentum", "Oversold: 5-minute RSI(2) under 10"),
    "rsi_hi":       ("momentum", "Overbought: 5-minute RSI(2) over 90"),
    "rs_up":        ("market", "Stronger than SPY today by over 0.5%"),
    "rs_dn":        ("market", "Weaker than SPY today by over 0.5%"),
    "mkt_up":       ("market", "SPY above its VWAP"),
    "mkt_fear":     ("market", "Market fear day: SPY RSI(2) at or under 10, or a gap down over 1 ATR"),
    "mkt_euph":     ("market", "Market euphoria day: SPY gapped up over 1 daily ATR"),
    "gap_up":       ("market", "This stock gapped up over 1 daily ATR"),
    "gap_dn":       ("market", "This stock gapped down over 1 daily ATR"),
    "t_open":       ("time", "9:45 to 10:30"),
    "t_lunch":      ("time", "12:00 to 2:00"),
    "t_late":       ("time", "2:00 to 3:30"),
    # combinations the literature names
    "spring_up":    ("combo", "Spring inside an intraday uptrend (Wyckoff last point of support)"),
    "spring_disc":  ("combo", "Spring in the discount third of the day"),
    "bos_up_vol":   ("combo", "Breakout of the last hour's high while in play"),
    "dip_in_trend": ("combo", "2+ standard deviations below VWAP in a daily uptrend"),
    "ut_dn":        ("combo", "Upthrust inside an intraday downtrend"),
    "bos_dn_vol":   ("combo", "Breakdown of the last hour's low while in play"),
}


def add_conditions(full, market_days):
    """full: lab.prepare() output. market_days: lab.market_days() (SPY RSI(2) and gap by date)."""
    out = []
    spy = full[full.sym == "SPY"].set_index("t")
    for sym, x in full.groupby("sym", sort=False):
        x = x.sort_values("t").copy()
        o, h, l, c, v = x.o, x.h, x.l, x.c, x.v
        atr = x.atr
        ema = c.ewm(span=20, adjust=False).mean()
        hh = h.shift(1).rolling(12).max()
        ll = l.shift(1).rolling(12).min()
        rng = (h - l).replace(0, np.nan)
        sign = np.sign(c - o)
        effort = (v * sign).rolling(6).sum() / v.rolling(6).sum()
        z = (c - x.vwap) / x.vsd.replace(0, np.nan)
        pos = (c - x.lod) / (x.hod - x.lod).replace(0, np.nan)
        after_or = x.bar >= FIRST_BAR
        sweep_lo = ((l < ll) & (c > ll)) | (after_or & (l < x.orl) & (c > x.orl)) | ((l < x.pdl) & (c > x.pdl))
        sweep_hi = ((h > hh) & (c < hh)) | (after_or & (h > x.orh) & (c < x.orh)) | ((h > x.pdh) & (c < x.pdh))
        s = spy.reindex(x.t)
        spy_ret = (s.c / s.o0 - 1).to_numpy()
        md = market_days.reindex(x.date.to_numpy())
        f = {
            "dtrend_up": (x.pdc > x.sma20d) & (x.sma20d > x.sma50d),
            "dtrend_dn": (x.pdc < x.sma20d) & (x.sma20d < x.sma50d),
            "itrend_up": (c > x.vwap) & (ema > ema.shift(3)),
            "itrend_dn": (c < x.vwap) & (ema < ema.shift(3)),
            "z_lt_m2": z < -2, "z_m2_m1": (z >= -2) & (z < -1), "z_p1_p2": (z > 1) & (z <= 2), "z_gt_p2": z > 2,
            "discount": pos < 1 / 3, "premium": pos > 2 / 3,
            "above_orh": after_or & (c > x.orh), "below_orl": after_or & (c < x.orl),
            "above_pdh": c > x.pdh, "below_pdl": c < x.pdl,
            "ext_up": (c - ema) > 3 * atr, "ext_dn": (ema - c) > 3 * atr,
            "compress": (h.rolling(12).max() - l.rolling(12).min()) < 3 * atr,
            "spring": _recent(sweep_lo, 3), "upthrust": _recent(sweep_hi, 3),
            "bos_up": c > hh, "bos_dn": c < ll,
            "bull_bar": (c > o) & ((c - l) >= 0.75 * rng), "bear_bar": (c < o) & ((h - c) >= 0.75 * rng),
            "rvol_lo": x.rvol < 0.8, "rvol_hi": x.rvol > 1.5, "climax": x.vrel >= 3,
            "buyers": effort > 0.3, "sellers": effort < -0.3,
            "rsi_lo": _rsi(c, 2) < 10, "rsi_hi": _rsi(c, 2) > 90,
            "rs_up": ((c / x.o0 - 1) - spy_ret) > 0.005, "rs_dn": ((c / x.o0 - 1) - spy_ret) < -0.005,
            "mkt_up": x.spy_above_vwap.astype(bool),
            "mkt_fear": ((md.rsi2.to_numpy() <= 10) | (md.gap.to_numpy() <= -1)) & ~(md.gap.to_numpy() >= 1),
            "mkt_euph": md.gap.to_numpy() >= 1,
            "gap_up": (x.o0 - x.pdc) > x.datr, "gap_dn": (x.pdc - x.o0) > x.datr,
            "t_open": x.mins < 10 * 60 + 30, "t_lunch": (x.mins >= 12 * 60) & (x.mins < 14 * 60),
            "t_late": x.mins >= 14 * 60,
        }
        f = {k: pd.Series(np.asarray(val, dtype=bool), index=x.index) for k, val in f.items()}
        f["spring_up"] = f["spring"] & f["itrend_up"]
        f["spring_disc"] = f["spring"] & f["discount"]
        f["bos_up_vol"] = f["bos_up"] & f["rvol_hi"]
        f["dip_in_trend"] = f["z_lt_m2"] & f["dtrend_up"]
        f["ut_dn"] = f["upthrust"] & f["itrend_dn"]
        f["bos_dn_vol"] = f["bos_dn"] & f["rvol_hi"]
        for k in CONDITIONS:
            x[k] = f[k].astype(np.int8)
        out.append(x)
    return pd.concat(out).sort_values(["sym", "t"]).reset_index(drop=True)


def _bracket(o, h, l, c, mins, atr, side):
    """R earned by a trade entered at each candle's close, for one stock-day (side +1 long, -1 short)."""
    n = len(c)
    r = np.full(n, np.nan)
    eod = np.where(mins >= EOD_MINS)[0]
    last = eod[0] if len(eod) else n - 1
    for i in range(FIRST_BAR, last):
        if mins[i] >= LAST_ENTRY or not atr[i] > 0:
            continue
        entry = c[i] + side * slip(c[i])
        risk = STOP_ATR * atr[i]
        stop, tgt = entry - side * risk, entry + side * TARGET_R * risk
        lo, hi = l[i + 1:last + 1], h[i + 1:last + 1]
        hit_stop = np.nonzero(lo <= stop if side > 0 else hi >= stop)[0]
        hit_tgt = np.nonzero(hi > tgt if side > 0 else lo < tgt)[0]
        js = hit_stop[0] if len(hit_stop) else 10 ** 6
        jt = hit_tgt[0] if len(hit_tgt) else 10 ** 6
        if js <= jt and js < 10 ** 6:                           # stop first if both on one candle
            px = (min(o[i + 1 + js], stop) if side > 0 else max(o[i + 1 + js], stop))
            px -= side * slip(px)
        elif jt < 10 ** 6:
            px = max(o[i + 1 + jt], tgt) if side > 0 else min(o[i + 1 + jt], tgt)
        else:
            px = c[last] - side * slip(c[last])
        r[i] = side * (px - entry) / risk
    return r


def add_outcomes(full):
    long_r, short_r = np.full(len(full), np.nan), np.full(len(full), np.nan)
    for _, idx in full.groupby(["sym", "date"], sort=False).indices.items():
        g = full.iloc[idx]
        args = [g[k].to_numpy() for k in ("o", "h", "l", "c", "mins", "atr")]
        long_r[idx] = _bracket(*args, side=1)
        short_r[idx] = _bracket(*args, side=-1)
    full = full.copy()
    full["long_r"], full["short_r"] = long_r, short_r
    return full
