"""Research lab for AMD Day Trader: features, setups and a candle-by-candle simulator.

Everything is causal: a signal on a candle uses only that candle and earlier ones, the way
the Pine strategy sees them, and fills at that candle's close plus slippage.

    prepare(bars5, daily)   one table with every feature a setup needs
    days(table)             per stock-day numpy arrays
    simulate(days, setup, params)  trades table (one position per stock at a time)
    stats(trades)           summary numbers

Setups: po3 (session AMD, the one AMD Day Trader uses), amd (5-minute swing AMD), bias
(po3 + re-entries), rs (relative strength while SPY sweeps), spring, vband, random (control).
"""
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

DEV_END = date(2026, 6, 30)     # development: Feb 23 - Jun 30. Holdout: Jul 1 - Oct 2 (looked at once).
SLIP_CENTS, SLIP_PCT = 0.01, 0.0001   # each market fill (entry, stop, end of day) costs $0.01 + 0.01% of price
EOD_MINS = 15 * 60 + 50         # the 3:50 candle closes at 3:55: flat by then
LAST_ENTRY = 15 * 60            # no new entries on candles that open at or after 3:00 PM
FIRST_BAR = 3                   # setups other than po3 start after the first 15 minutes


# ------------------------------------------------------------------ features
def _rma(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def prepare(bars, daily):
    """bars: 5-minute regular-session OHLCV with sym and NY-time t. daily: daily OHLCV."""
    d = daily.copy()
    d["date"] = d.t.dt.tz_convert("UTC").dt.date           # daily bars are stamped 00:00 UTC
    d = d.sort_values(["sym", "date"])
    g = d.groupby("sym")
    d["pdh"], d["pdl"], d["pdc"] = g.h.shift(1), g.l.shift(1), g.c.shift(1)
    d["sma20d"] = g.c.transform(lambda s: s.rolling(20).mean().shift(1))
    d["sma50d"] = g.c.transform(lambda s: s.rolling(50).mean().shift(1))
    pc = g.c.shift(1)
    tr = np.maximum(d.h - d.l, np.maximum((d.h - pc).abs(), (d.l - pc).abs()))
    d["datr"] = tr.groupby(d.sym).transform(lambda s: s.rolling(14).mean().shift(1))
    dcols = ["sym", "date", "pdh", "pdl", "pdc", "sma20d", "sma50d", "datr"]

    out = []
    for sym, x in bars.groupby("sym", sort=False):
        x = x.sort_values("t").reset_index(drop=True).copy()
        x["date"] = x.t.dt.date
        x["mins"] = x.t.dt.hour * 60 + x.t.dt.minute
        x["bar"] = x.groupby("date").cumcount()
        o, h, l, c, v = (x[k].to_numpy() for k in "ohlcv")
        prev_c = np.r_[c[0], c[:-1]]
        x["atr"] = _rma(np.maximum(h - l, np.maximum(abs(h - prev_c), abs(l - prev_c))), 14)
        hlc3 = (h + l + c) / 3
        day = x["date"]
        cv = pd.Series(v).groupby(day).cumsum()
        x["vwap"] = pd.Series(hlc3 * v).groupby(day).cumsum() / cv
        var = pd.Series(hlc3 * hlc3 * v).groupby(day).cumsum() / cv - x["vwap"] ** 2
        x["vsd"] = np.sqrt(np.clip(var, 0, None))
        x["cumv"] = cv
        # Relative volume at the same time of day, against the previous 10 sessions (needs 5)
        past = lambda s: s.shift(1).rolling(10, min_periods=5).mean()
        x["rvol"] = x["cumv"] / x.groupby("bar")["cumv"].transform(past)
        x["vrel"] = x["v"] / x.groupby("bar")["v"].transform(past)
        first = x[x.bar < FIRST_BAR].groupby("date").agg(orh=("h", "max"), orl=("l", "min"), o0=("o", "first"))
        x = x.join(first, on="date")
        x["hod"] = x.groupby("date")["h"].cummax()
        x["lod"] = x.groupby("date")["l"].cummin()
        x["sym"] = sym
        out.append(x)
    full = pd.concat(out, ignore_index=True)
    full = full.merge(d[dcols], on=["sym", "date"], how="left")
    full["gap"] = full.o0 / full.pdc - 1
    spy = full[full.sym == "SPY"][["t", "c", "vwap", "o0", "pdc"]]
    spy = spy.rename(columns={"c": "spy_c", "vwap": "spy_vwap", "o0": "spy_o0", "pdc": "spy_pdc"})
    full = full.merge(spy, on="t", how="left")
    full["spy_above_vwap"] = full.spy_c > full.spy_vwap
    full["spy_day"] = full.spy_c / full.spy_pdc - 1
    return full.sort_values(["sym", "t"]).reset_index(drop=True)


def days(data):
    """Split into per-stock-day dicts of numpy arrays (fast to loop over)."""
    cols = ["o", "h", "l", "c", "v", "mins", "bar", "atr", "vwap", "vsd", "rvol", "vrel", "orh", "orl",
            "pdh", "pdl", "pdc", "sma20d", "sma50d", "datr", "gap", "spy_above_vwap", "spy_day", "hod", "lod"]
    out = []
    for (sym, dt), x in data.groupby(["sym", "date"], sort=False):
        a = {k: x[k].to_numpy() for k in cols}
        a["sym"], a["date"] = sym, dt
        out.append(a)
    return out


# ------------------------------------------------------------------ setups
def pivots(h, l, i, left, right):
    """Pivot high/low at candle i-right, confirmed on candle i (same day only)."""
    k = i - right
    if k - left < 0:
        return None, None
    win_h, win_l = h[k - left:i + 1], l[k - left:i + 1]
    ph = h[k] if h[k] == win_h.max() and (win_h == h[k]).sum() == 1 else None
    pl = l[k] if l[k] == win_l.min() and (win_l == l[k]).sum() == 1 else None
    return ph, pl


def signals_amd(a, p):
    """Accumulation -> manipulation -> expansion (Wyckoff spring + sign of strength / ICT sweep + MSS).

    Manipulation: a candle trades below untouched sell-side liquidity: the latest swing low,
    the opening-range low or the prior day's low.
    Expansion: within `window` candles, a candle closes above the last swing high before
    the sweep (market structure shift), green and closing near its high.
    Entry at that close; stop under the lowest point of the sweep.
    Returns a list of (candle index, stop, tag).
    """
    o, h, l, c = a["o"], a["h"], a["l"], a["c"]
    atr, n = a["atr"], len(c)
    L, R = p.get("piv", 2), p.get("piv", 2)
    window = p.get("window", 12)
    swing_lows, swing_highs = [], []          # [price, taken?]
    orl_live, pdl_live = True, not np.isnan(a["pdl"][0])
    armed, sweep_bar, sweep_low, mss, swept_kind = False, -1, np.nan, np.nan, ""
    sigs = []
    for i in range(n):
        ph, pl = pivots(h, l, i, L, R)
        if ph is not None:
            swing_highs.append(ph)
        if pl is not None:
            swing_lows.append([pl, False])
        if i < FIRST_BAR:
            continue
        # --- manipulation: untouched liquidity taken on this candle
        kinds = []
        for s in swing_lows:
            if not s[1] and l[i] < s[0]:
                s[1] = True
                kinds.append("swing")
        if orl_live and i >= FIRST_BAR and l[i] < a["orl"][i]:
            orl_live = False
            kinds.append("orl")
        if pdl_live and l[i] < a["pdl"][i]:
            pdl_live = False
            kinds.append("pdl")
        if kinds and p.get("levels"):
            kinds = [k for k in kinds if k in p["levels"]]
        if kinds:
            prior_high = swing_highs[-1] if swing_highs else h[max(0, i - 6):i].max()
            if not armed:
                armed, sweep_low, mss = True, l[i], prior_high
            else:
                sweep_low = min(sweep_low, l[i])
                mss = min(mss, prior_high) if p.get("mss_lowest") else prior_high
            sweep_bar, swept_kind = i, "+".join(kinds)
        elif armed and l[i] < sweep_low:
            sweep_low = l[i]
        if not armed:
            continue
        if i - sweep_bar > window:
            armed = False
            continue
        # --- expansion: structure shift on a strong candle
        rng = h[i] - l[i]
        strong = c[i] > o[i] and rng > 0 and (c[i] - l[i]) >= p.get("close_pos", 0.6) * rng
        if strong and c[i] > mss and (i > sweep_bar or p.get("same_bar", True)):
            sigs.append((i, sweep_low - p.get("buf", 0.1) * atr[i], swept_kind))
            armed = False
    return sigs


def signals_po3(a, p):
    """Session Power of Three (ICT AMD) / Dalton open-test-drive / Wyckoff spring of the open.

    Accumulation: the opening range (first `or_bars` candles).
    Manipulation: before `manip_end`, price runs below the opening-range low and/or the prior
    day's low (the "Judas swing" that takes the stops under the range).
    Expansion: a strong green candle closes back above the trigger line: the swept level
    ('reclaim'), VWAP ('vwap'), the opening-range middle ('mid') or high ('orh'), or the last
    swing high before the low ('mss'). Entry at that close, stop under the manipulation low.
    """
    o, h, l, c, mins, atr, vwap = a["o"], a["h"], a["l"], a["c"], a["mins"], a["atr"], a["vwap"]
    n = len(c)
    if p.get("acc"):                                   # a later balance window, e.g. the lunch range
        s, e = p["acc"]
        win = np.where((mins >= s) & (mins < e))[0]
        if len(win) == 0:
            return []
        start, orb = win[0], win[-1] + 1
        or_hi, or_lo = h[start:orb].max(), l[start:orb].min()
        pdl = np.nan
    else:
        orb = p.get("or_bars", 3)
        or_hi, or_lo = h[:orb].max(), l[:orb].min()
        pdl = a["pdl"][0]
    levels = p.get("levels", ("orl", "pdl"))
    trig, buf = p.get("trigger", "reclaim"), p.get("buf", 0.1)
    swept, low, level, mss, swing_highs, sigs = False, np.inf, np.nan, np.nan, [], []
    for i in range(orb, n):
        ph, _ = pivots(h, l, i, 2, 2)
        if ph is not None:
            swing_highs.append(ph)
        hit = []
        if mins[i] < p.get("manip_end", 11 * 60):
            if "orl" in levels and l[i] < or_lo and not (swept and level >= or_lo):
                hit.append(or_lo)
            if "pdl" in levels and not np.isnan(pdl) and l[i] < pdl and not (swept and level >= pdl):
                hit.append(pdl)
        if hit:
            if not swept:
                mss = swing_highs[-1] if swing_highs else h[max(0, i - 6):i].max()
            swept, level = True, max([level] + hit) if swept else max(hit)
        if not swept:
            continue
        if l[i] < low:
            low = l[i]
            mss = swing_highs[-1] if swing_highs else mss
        if mins[i] >= p.get("trig_end", 12 * 60):
            break
        line = {"reclaim": level, "vwap": vwap[i], "orh": or_hi, "mid": (or_hi + or_lo) / 2, "mss": mss}[trig]
        rng = h[i] - l[i]
        strong = c[i] > o[i] and rng > 0 and (c[i] - l[i]) >= p.get("close_pos", 0.6) * rng
        if strong and c[i] > line and (p.get("first_cross") is False or c[i - 1] <= line or i == orb):
            stop_mode = p.get("stop", "manip")
            stop = low if stop_mode == "manip" else l[i] if stop_mode == "candle" else min(l[max(orb, i - 2):i + 1])
            if stop_mode == "cap":                     # under the manipulation low, but no more than `cap` ATR
                stop = max(low, c[i] - p.get("cap", 3.0) * atr[i])
            sigs.append((i, stop - buf * atr[i], "po3-" + trig, low))
            if p.get("once", True):
                break
    return [s[:3] for s in sigs] if not p.get("_with_low") else sigs


def signals_bias(a, p):
    """Two layers. The session's Power of Three sets the bias (first signal = first entry);
    after that, every 5-minute AMD (swing-low sweep + structure shift) is a re-entry, while
    price holds above the session's manipulation low."""
    first = signals_po3(a, dict(p, _with_low=True, stop=p.get("first_stop", "candle"), once=True))
    if not first:
        return []
    i0, stop0, tag0, manip_low = first[0]
    out = [(i0, stop0, tag0)] if p.get("take_first", True) else []
    o, h, l, c, atr = a["o"], a["h"], a["l"], a["c"], a["atr"]
    broken = next((k for k in range(i0 + 1, len(c)) if c[k] < manip_low), len(c))
    re = p.get("re", "amd")
    if re == "amd":
        sub = dict(p, levels=p.get("re_levels", ("swing",)), window=p.get("re_window", 12))
        for i, stop, tag in signals_amd(a, sub):
            if i0 < i < broken:
                out.append((i, stop, "re-" + tag))
        return out
    # Back-up to the edge of the creek (Wyckoff): a dip to the broken range high, or to VWAP,
    # that holds, closing green near its high.
    orb = p.get("or_bars", 3)
    creek = h[:orb].max()
    for i in range(i0 + 1, broken):
        line = creek if re == "retest_orh" else a["vwap"][i]
        rng = h[i] - l[i]
        if l[i] <= line + p.get("touch", 0.1) * atr[i] and c[i] > line and c[i] > o[i] and rng > 0 \
                and (c[i] - l[i]) >= p.get("close_pos", 0.6) * rng:
            out.append((i, min(l[i], line) - p.get("buf", 0.1) * atr[i], "re-" + re))
    return out


def signals_rs(a, p):
    """Relative strength during the market's manipulation (Wyckoff composite operator /
    Bellafiore). SPY runs below its own opening-range low or prior-day low while this stock
    holds above its opening-range low (accumulation). Then the stock closes above its
    opening-range high ('orh') or its last swing high ('mss') on a strong candle."""
    o, h, l, c, mins, atr = a["o"], a["h"], a["l"], a["c"], a["mins"], a["atr"]
    s = a["spy_l_below"]                      # True on candles where SPY trades below its OR low / PDL
    n, orb = len(c), p.get("or_bars", 6)
    or_hi, or_lo = h[:orb].max(), l[:orb].min()
    armed, low, mss, swing_highs = False, np.inf, np.nan, []
    for i in range(orb, n):
        ph, _ = pivots(h, l, i, 2, 2)
        if ph is not None:
            swing_highs.append(ph)
        if l[i] < or_lo:                      # the stock lost its own range: no relative strength today
            return []
        if not armed and s[i] and mins[i] < p.get("manip_end", 12 * 60):
            armed, low = True, l[i]
            mss = swing_highs[-1] if swing_highs else h[max(0, i - 6):i].max()
        if not armed:
            continue
        low = min(low, l[i])
        if mins[i] >= p.get("trig_end", 13 * 60):
            return []
        line = or_hi if p.get("trigger", "orh") == "orh" else mss
        rng = h[i] - l[i]
        if c[i] > o[i] and rng > 0 and (c[i] - l[i]) >= 0.6 * rng and c[i] > line and c[i - 1] <= line:
            stop = max(low, c[i] - p.get("cap", 2.0) * atr[i]) - p.get("buf", 0.1) * atr[i]
            return [(i, stop, "rs-" + p.get("trigger", "orh"))]
    return []


def add_spy_sweep(day_list, or_bars):
    """Mark, on every stock-day, the candles where SPY trades below its own opening-range low or
    its prior-day low (used by signals_rs)."""
    spy = {a["date"]: a for a in day_list if a["sym"] == "SPY"}
    for a in day_list:
        s = spy[a["date"]]
        below = (s["l"] < s["l"][:or_bars].min()) | (s["l"] < np.nan_to_num(s["pdl"], nan=-1.0))
        below[:or_bars] = False
        m = dict(zip(s["mins"], below))
        a["spy_l_below"] = np.array([m.get(x, False) for x in a["mins"]])


def signals_spring(a, p):
    """Baseline: the sweep candle itself closes back above the level (no structure shift)."""
    p = dict(p, window=0, same_bar=True)
    return signals_amd(a, p)


def signals_vwap_band(a, p):
    """Reference: dip below VWAP - k standard deviations and close back above, near the high."""
    o, h, l, c, atr = a["o"], a["h"], a["l"], a["c"], a["atr"]
    band = a["vwap"] - p.get("band", 2.0) * a["vsd"]
    sigs = []
    for i in range(FIRST_BAR, len(c)):
        rng = h[i] - l[i]
        if l[i] < band[i] < c[i] and c[i] > o[i] and rng > 0 and (c[i] - l[i]) >= 0.6 * rng:
            sigs.append((i, l[i] - 0.1 * atr[i], "vband"))
    return sigs


def signals_random(a, p, rng=np.random.default_rng(11)):
    """Control: random entry candles with a 1-ATR stop; shows what the 2R bracket alone earns."""
    n = len(a["c"])
    pick = [i for i in range(FIRST_BAR, n) if rng.random() < p.get("rate", 0.02)]
    return [(i, a["c"][i] - p.get("stop_atr", 1.0) * a["atr"][i], "random") for i in pick]


SETUPS = {"amd": signals_amd, "po3": signals_po3, "bias": signals_bias, "rs": signals_rs,
          "spring": signals_spring, "vband": signals_vwap_band, "random": signals_random}


def passes(a, i, stop, p):
    """Context filters on the signal candle."""
    c, atr = a["c"][i], a["atr"][i]
    risk = c - stop
    if not (p.get("min_risk", 0.3) * atr <= risk <= p.get("max_risk", 3.0) * atr):
        return False
    if a["mins"][i] >= p.get("last_entry", LAST_ENTRY) or a["mins"][i] < p.get("first_entry", 0):
        return False
    rv = a["rvol"][i]
    if p.get("min_rvol") and not (rv >= p["min_rvol"]):
        return False
    if p.get("max_rvol") and not (rv < p["max_rvol"]):
        return False
    if p.get("min_vrel") and not (a["vrel"][i] >= p["min_vrel"]):
        return False
    if p.get("spy") == "above_vwap" and not a["spy_above_vwap"][i]:
        return False
    if p.get("spy") == "not_down" and not (a["spy_day"][i] > -0.003):
        return False
    if p.get("trend") == "sma20" and not (a["pdc"][i] > a["sma20d"][i]):
        return False
    if p.get("trend") == "sma50" and not (a["pdc"][i] > a["sma50d"][i]):
        return False
    if p.get("discount") == "vwap" and not (stop < a["vwap"][i]):
        return False
    if p.get("discount") == "half" and not (stop < (a["hod"][i] + a["lod"][i]) / 2):
        return False
    if p.get("above_vwap") and not (c > a["vwap"][i]):
        return False
    if p.get("gap") == "up" and not (a["gap"][i] > 0):
        return False
    if p.get("gap") == "down" and not (a["gap"][i] < 0):
        return False
    return True


# ------------------------------------------------------------------ market day grade
def market_days(daily_long):
    """SPY's RSI(2) at yesterday's close and today's opening gap in daily ATRs, for every date.

    The same numbers AMD Day Trader reads with request.security(): RSI(2) is Wilder's, the ATR
    is a 14-day average of true range ending yesterday."""
    d = daily_long[daily_long.sym == "SPY"].copy()
    d["date"] = d.t.dt.tz_convert("UTC").dt.date
    d = d.drop_duplicates("date").sort_values("date").set_index("date")
    pc = d.c.shift(1)
    tr = np.maximum(d.h - d.l, np.maximum((d.h - pc).abs(), (d.l - pc).abs()))
    atr = tr.rolling(14).mean().shift(1)
    delta = d.c.diff()
    up = delta.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    return pd.DataFrame({"rsi2": (100 - 100 / (1 + up / dn)).shift(1), "gap": (d.o - pc) / atr})


def grade(m, dt, rsi_max=10.0, gap_atr=1.0):
    """'A' on a market fear day (SPY oversold or gapping down hard), 'X' when SPY gaps up
    hard (longs drift down from the open), otherwise 'B'."""
    if dt not in m.index:
        return "B"
    r, g = m.rsi2[dt], m.gap[dt]
    if g >= gap_atr:
        return "X"
    return "A" if (r <= rsi_max or g <= -gap_atr) else "B"


# ------------------------------------------------------------------ simulator
def slip(price):
    return SLIP_CENTS + SLIP_PCT * price


@dataclass
class Trade:
    sym: str
    date: object
    tag: str
    mins: int
    entry: float
    stop: float
    r: float
    how: str
    bars: int
    rvol: float


def run_day(a, sigs, p):
    o, h, l, c, mins = a["o"], a["h"], a["l"], a["c"], a["mins"]
    n, target_r = len(c), p.get("target_r", 2.0)
    trades, day_r, busy_until = [], 0.0, -1
    for i, stop, tag in sigs:
        if i <= busy_until or len(trades) >= p.get("max_trades", 3) or day_r <= -p.get("day_stop_r", 2.0):
            continue
        if not passes(a, i, stop, p) or mins[i] >= EOD_MINS:
            continue
        entry = c[i] + slip(c[i])
        risk = entry - stop
        if risk <= 0:
            continue
        tgt = entry + target_r * risk
        how, j, px = "eod", i + 1, c[-1]
        while j < n:
            if l[j] <= stop:                                   # stop first when a candle hits both
                px = min(o[j], stop)
                px, how = px - slip(px), "stop"
                break
            if h[j] > tgt:                                     # limit order: price must trade through
                px, how = max(o[j], tgt), "target"
                break
            if mins[j] >= EOD_MINS:
                px, how = c[j] - slip(c[j]), "eod"
                break
            if p.get("time_stop") and j - i >= p["time_stop"]:
                px, how = c[j] - slip(c[j]), "time"
                break
            j += 1
        r = (px - entry) / risk
        trades.append(Trade(a["sym"], a["date"], tag, int(mins[i]), entry, stop, r, how, j - i, float(a["rvol"][i])))
        day_r += r
        busy_until = j
    return trades


def simulate(day_list, setup, p, syms=None, exclude=("SPY", "QQQ", "IWM")):
    fn = SETUPS[setup]
    out = []
    for a in day_list:
        if (syms and a["sym"] not in syms) or (exclude and a["sym"] in exclude and not syms):
            continue
        out += run_day(a, fn(a, p), p)
    t = pd.DataFrame([x.__dict__ for x in out])
    if len(t):
        t = t.sort_values(["date", "mins", "sym"]).reset_index(drop=True)
    return t


# ------------------------------------------------------------------ statistics
def stats(t):
    if t is None or len(t) == 0:
        return dict(n=0)
    eq = t.r.cumsum()
    se = t.r.std(ddof=1) / np.sqrt(len(t)) if len(t) > 1 else np.nan
    return dict(n=len(t), win=round((t.r > 0).mean() * 100, 1), avgR=round(t.r.mean(), 3),
                totR=round(t.r.sum(), 1), t=round(t.r.mean() / se, 2) if se else np.nan,
                maxDD=round((eq.cummax() - eq).max(), 1), perDay=round(len(t) / t.date.nunique(), 2))


def split(t):
    return t[t.date <= DEV_END], t[t.date > DEV_END]


def day_bootstrap_p(t, n=5000, seed=7):
    """Chance the true average R is <= 0, resampling whole days (trades on one day are correlated)."""
    rng = np.random.default_rng(seed)
    g = t.groupby("date").r.agg(["sum", "size"])
    s, k = g["sum"].to_numpy(), g["size"].to_numpy()
    idx = rng.integers(0, len(g), size=(n, len(g)))
    means = s[idx].sum(1) / k[idx].sum(1)
    return float((means <= 0).mean())
