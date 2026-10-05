"""Reproduce every number in RESULTS.md.

    python run_all.py bars5.pkl daily.pkl daily_long.pkl > results.txt

bars5.pkl       5-minute bars, Feb 23 - Oct 2 2026, 50 symbols (load_bars.py ... 5minute)
daily.pkl       daily bars from Jun 2025 (prior-day levels for the 5-minute tests)
daily_long.pkl  daily bars 2015 - Oct 2026 (the day-bias study)
"""
import sys
from datetime import date

import numpy as np
import pandas as pd

import lab

H = lambda h, m=0: h * 60 + m

# The rules AMD Day Trader uses, frozen on Jun 30 before the holdout was run.
FROZEN = dict(trigger="orh", or_bars=6, levels=("orl", "pdl"), manip_end=H(11), trig_end=H(12), close_pos=0.6,
              stop="cap", cap=2.0, buf=0.1, target_r=2.0, spy="above_vwap", max_risk=12, min_risk=0.3)


def line(name, t):
    s = lab.stats(t)
    if s["n"] == 0:
        print(f"  {name:<46} no trades")
        return
    print(f"  {name:<46} n {s['n']:>5}  win {s['win']:>5.1f}%  avg {s['avgR']:+.3f}R  t {s['t']:>6.2f}  "
          f"maxDD {s['maxDD']:>6.1f}R  per day {s['perDay']:.2f}")


def both(name, t):
    dev, hold = lab.split(t)
    line(name + "  [dev]", dev)
    line(name + "  [holdout]", hold)


def section(title):
    print(f"\n=== {title}")


def main(bars5, daily, daily_long):
    data = lab.prepare(pd.read_pickle(bars5), pd.read_pickle(daily))
    DL = lab.days(data)
    DEV = [a for a in DL if a["date"] <= lab.DEV_END]
    print(f"{len(data)} candles, {data.sym.nunique()} symbols, {data.date.nunique()} days "
          f"({data.date.min()} to {data.date.max()}); SPY, QQQ and IWM are not traded")

    section("1. Controls and 5-minute setups, development period only (Feb 23 - Jun 30)")
    line("random entries, 1 ATR stop, 2R target", lab.simulate(DEV, "random", {}))
    line("VWAP -2 sd spring", lab.simulate(DEV, "vband", {}))
    line("VWAP -2 sd spring, RVOL >= 1.5", lab.simulate(DEV, "vband", dict(min_rvol=1.5)))
    line("swing AMD (any swing low swept + MSS)", lab.simulate(DEV, "amd", {}))
    line("swing AMD, RVOL >= 1.5", lab.simulate(DEV, "amd", dict(min_rvol=1.5)))

    section("2. Session AMD (Power of Three) by trigger line, development, 15-min range, entries by 12:00")
    for trig in ("reclaim", "mid", "vwap", "mss", "orh"):
        line(f"trigger {trig}, stop under manipulation low",
             lab.simulate(DEV, "po3", dict(trigger=trig, max_risk=12, last_entry=H(12))))

    section("3. Afternoon AMD on the lunch range, development")
    for acc in ((H(11, 30), H(13)), (H(12), H(13, 30))):
        for trig in ("orh", "mss"):
            p = dict(acc=acc, levels=("orl",), manip_end=acc[1] + 90, trig_end=H(15), trigger=trig, max_risk=12)
            line(f"range {acc[0] // 60}:{acc[0] % 60:02d}-{acc[1] // 60}:{acc[1] % 60:02d}, trigger {trig}",
                 lab.simulate(DEV, "po3", p))

    section("4. Exits for the session AMD (trigger orh, SPY above VWAP), development")
    base = dict(trigger="orh", max_risk=12, spy="above_vwap")
    for st, extra in (("manip", {}), ("cap", dict(cap=2.0))):
        for ts in (None, 12, 24):
            t = lab.simulate(DEV, "po3", dict(base, stop=st, time_stop=ts, **extra))
            hw = t.how.value_counts(normalize=True).round(2).to_dict()
            line(f"stop {st}{extra.get('cap', '')}, time stop {ts or 'none'} {hw}", t)

    section("5. Re-entries after the first trade, development (30-min range)")
    for re in ("amd", "retest_orh", "retest_vwap"):
        p = dict(FROZEN, first_stop="cap", re=re, max_trades=4, day_stop_r=99)
        t = lab.simulate(DEV, "bias", p)
        line(f"re-entries only: {re}", t[t.tag.str.startswith("re-")])

    section("6. One-at-a-time robustness of the frozen rules, development")
    for k, vals in (("cap", (1.5, 2.0, 2.5)), ("or_bars", (3, 6)), ("trig_end", (H(11, 30), H(12), H(13))),
                    ("levels", (("orl",), ("pdl",), ("orl", "pdl"))), ("spy", (None, "above_vwap")),
                    ("trend", (None, "sma50")), ("min_rvol", (None, 1.0))):
        for v in vals:
            line(f"{k} = {v}", lab.simulate(DEV, "po3", dict(FROZEN, **{k: v})))

    section("7. HOLDOUT: frozen rules, run once on Jul 1 - Oct 2")
    t30 = lab.simulate(DL, "po3", FROZEN)
    t15 = lab.simulate(DL, "po3", dict(FROZEN, or_bars=3))
    both("AMD Day Trader, 30-min range", t30)
    both("same rules, 15-min range", t15)
    for name, t in (("30-min", t30), ("15-min", t15)):
        dev, hold = lab.split(t)
        print(f"  {name}: chance true average <= 0 (day bootstrap): holdout {lab.day_bootstrap_p(hold):.2f}, "
              f"all {lab.day_bootstrap_p(t):.2f}; exits {t.how.value_counts().to_dict()}; "
              f"average hold {t.bars.mean() * 5:.0f} min")

    section("8. Average R by month")
    rnd = lab.simulate(DL, "random", {})
    tab = {}
    for name, t in (("AMD 30-min", t30), ("AMD 15-min", t15), ("random", rnd)):
        m = pd.to_datetime(t.date).dt.strftime("%Y-%m")
        tab[name + " avgR"] = t.groupby(m).r.mean().round(2)
        tab[name + " n"] = t.groupby(m).r.size()
    spy = [a for a in DL if a["sym"] == "SPY"]
    oc = pd.Series({a["date"]: a["c"][-1] / a["o"][0] - 1 for a in spy})
    m = pd.to_datetime(pd.Series(oc.index)).dt.strftime("%Y-%m").to_numpy()
    tab["SPY up-days %"] = (oc > 0).groupby(m).mean().mul(100).round(0)
    tab["SPY open-to-close bp"] = oc.groupby(m).mean().mul(1e4).round(1)
    print(pd.DataFrame(tab).to_string())

    section("9. New hypothesis after the holdout: relative strength while SPY sweeps its lows")
    for ob in (3, 6):
        lab.add_spy_sweep(DL, ob)
        both(f"RS, {ob * 5}-min range, trigger orh", lab.simulate(DL, "rs", dict(or_bars=ob, max_risk=12)))

    section("10. New hypothesis after the holdout: trade only while the setup's last 10 days are positive")
    alld = sorted({a["date"] for a in DL})
    daily_r = t30.groupby("date").r.agg(["sum", "size"]).reindex(alld).fillna(0)
    roll = (daily_r["sum"].rolling(10).sum() / daily_r["size"].rolling(10).sum()).shift(1)
    both("AMD 30-min, strategy-momentum gate", t30[t30.date.map(roll) > 0])

    section("11. Day bias from daily bars 2015 - 2026: average open-to-close move in daily ATRs")
    day_bias(daily_long)

    section("12. AMD Day Trader as shipped: grade A = morning AMD on a market fear day")
    m = lab.market_days(pd.read_pickle(daily_long))
    alld = sorted({a["date"] for a in DL})
    grades = {dt: lab.grade(m, dt) for dt in alld}
    print("  days by grade:", pd.Series(grades).value_counts().to_dict())
    for name, t in (("15-min range (default)", t15), ("30-min range", t30), ("random control", rnd)):
        t = t.assign(g=t.date.map(grades))
        for gr in ("A", "B", "X"):
            both(f"{name}, grade {gr}", t[t.g == gr])
    a15 = t15[t15.date.map(grades) == "A"]
    per_day = a15.groupby("date").r.agg(n="size", total="sum", avg="mean").round(2)
    print(per_day.to_string())
    best2 = per_day.total.nlargest(2).index
    print(f"  grade A, 15-min: {len(a15)} trades on {a15.date.nunique()} days; positive days "
          f"{(per_day.total > 0).sum()} of {len(per_day)}; without the best two days {a15[~a15.date.isin(best2)].r.mean():+.3f}R; "
          f"chance true average <= 0 (day bootstrap) {lab.day_bootstrap_p(a15):.3f}; exits {a15.how.value_counts().to_dict()}; "
          f"average hold {a15.bars.mean() * 5:.0f} min")
    a15.to_csv("grade_a_trades.csv", index=False)


def day_bias(path):
    d = pd.read_pickle(path)
    d["date"] = pd.to_datetime(d.t.dt.tz_convert("UTC").dt.date)
    d = d.drop_duplicates(["sym", "date"]).sort_values(["sym", "date"]).reset_index(drop=True)
    g = d.groupby("sym", group_keys=False)
    pc = g.c.shift(1)
    tr = np.maximum(d.h - d.l, np.maximum((d.h - pc).abs(), (d.l - pc).abs()))
    d["atr"] = tr.groupby(d.sym).transform(lambda s: s.rolling(14).mean().shift(1))
    d["oc"] = (d.c - d.o) / d.atr
    d["gap"] = (d.o - pc) / d.atr
    delta = g.c.diff()
    up = delta.clip(lower=0).groupby(d.sym).transform(lambda s: s.ewm(alpha=0.5, adjust=False).mean())
    dn = (-delta.clip(upper=0)).groupby(d.sym).transform(lambda s: s.ewm(alpha=0.5, adjust=False).mean())
    d["rsi2"] = (100 - 100 / (1 + up / dn)).groupby(d.sym).shift(1)
    spy = d[d.sym == "SPY"].set_index("date")
    d = d.join(pd.DataFrame({"m_gap": spy.gap, "m_rsi2": spy.rsi2}), on="date")
    d = d[~d.sym.isin(["SPY", "QQQ", "IWM"]) & np.isfinite(d.oc) & (d.atr > 0)].dropna(subset=["m_gap", "m_rsi2"])
    d["era"] = np.where(d.date < "2023-01-01", "2015-22", np.where(d.date < "2026-02-23", "2023-26", "2026 (5-min sample)"))
    print(f"  {len(d)} stock-days; all days: " +
          ", ".join(f"{e} {x:+.3f}" for e, x in d.groupby("era").oc.mean().items()))
    for name, mask in (("SPY gaps up > 1 ATR", d.m_gap > 1), ("SPY gaps down > 1 ATR", d.m_gap < -1),
                       ("SPY RSI(2) <= 10", d.m_rsi2 <= 10), ("SPY RSI(2) <= 30", d.m_rsi2 <= 30),
                       ("SPY RSI(2) > 30", d.m_rsi2 > 30), ("Mondays", d.date.dt.dayofweek == 0)):
        r = d[mask].groupby("era").oc.agg(["mean", "size"])
        print(f"  {name:<24}" + "  ".join(f"{e}: {row['mean']:+.3f} ({int(row['size'])})" for e, row in r.iterrows()))


if __name__ == "__main__":
    main(*sys.argv[1:4])
