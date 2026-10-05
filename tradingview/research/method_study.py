"""Can one method read chart conditions and dictate BUY / SELL on any chart? Reproduces METHODOLOGY.md.

    python method_study.py bars5.pkl daily_long.pkl > method_results.txt

The method: every condition from conditions.py (5-minute chart) or daily_conditions.py (daily
chart) is a 0/1 fact; a points table (ridge regression on those facts) turns them into an
expected R for a long and for a short. It is judged only out of sample: refit on the past,
score the next month (5-minute) or the next year (daily), and compare predicted with realized R.
"""
import sys

import numpy as np
import pandas as pd

import conditions as C
import daily_conditions as D
import lab

DAY_LEVEL = {"dtrend_up", "dtrend_dn", "mkt_fear", "mkt_euph", "gap_up", "gap_dn", "dip_in_trend"}
SAME_FOR_ALL = {"mkt_up", "t_open", "t_lunch", "t_late"}
# The textbook reading for a long (Elder's triple screen, Wyckoff, Raschke, Aziz). A short is the mirror.
TEXTBOOK = dict(itrend_up=1, itrend_dn=-1, rs_up=1, rs_dn=-1, z_m2_m1=1, z_gt_p2=-1, ext_up=-1, discount=1,
                compress=1, spring=1, spring_up=1, upthrust=-1, bos_up=1, bos_dn=-1, bull_bar=1, bear_bar=-1,
                rvol_hi=1, rvol_lo=-1, buyers=1, sellers=-1, rsi_lo=1, rsi_hi=-1, t_open=1, t_lunch=-1)
MIRROR = {"itrend_up": "itrend_dn", "rs_up": "rs_dn", "z_m2_m1": "z_p1_p2", "z_gt_p2": "z_lt_m2", "ext_up": "ext_dn",
          "discount": "premium", "spring": "upthrust", "spring_up": "ut_dn", "bos_up": "bos_dn", "bull_bar": "bear_bar",
          "buyers": "sellers", "rsi_lo": "rsi_hi"}
MIRROR.update({v: k for k, v in MIRROR.items()})


def fit(X, y, lam):
    X1 = np.c_[np.ones(len(X)), X]
    return np.linalg.solve(X1.T @ X1 + lam * np.diag([0] + [1] * X.shape[1]), X1.T @ y)


def predict(b, X):
    return b[0] + X @ b[1:]


def deciles(p, r):
    q = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False).to_numpy()
    cal = pd.DataFrame({"q": q, "p": p, "r": r}).groupby("q").mean()
    return cal.r.round(3).tolist(), round(float(np.corrcoef(cal.p, cal.r)[0, 1]), 2)


def walk_forward_5min(x, cols, target, relative_to=None, lam=3000):
    """Monthly expanding walk-forward. Returns out-of-sample (prediction, relative outcome, raw outcome)."""
    y = x[target] - x.groupby(list(relative_to))[target].transform("mean") if relative_to else x[target]
    months = sorted(x.m.unique())
    P, Y, RAW = [], [], []
    for m in months[2:]:
        tr, te = (x.m < m).to_numpy(), (x.m == m).to_numpy()
        b = fit(x.loc[tr, cols].to_numpy(float), y[tr].to_numpy(), lam)
        P.append(predict(b, x.loc[te, cols].to_numpy(float)))
        Y.append(y[te].to_numpy())
        RAW.append(x.loc[te, target].to_numpy())
    return np.concatenate(P), np.concatenate(Y), np.concatenate(RAW)


def section(t):
    print(f"\n=== {t}")


def intraday(bars5, daily_long):
    full = lab.prepare(pd.read_pickle(bars5), pd.read_pickle(daily_long))
    m = lab.market_days(pd.read_pickle(daily_long))
    x = C.add_outcomes(C.add_conditions(full, m))
    spy = x[x.sym == "SPY"]
    x = x[~x.sym.isin(["SPY", "QQQ", "IWM"]) & x.long_r.notna()].copy()
    x["m"] = pd.to_datetime(x.date).dt.to_period("M")
    x["half"] = np.where(pd.to_datetime(x.date) <= "2026-06-30", "Feb-Jun", "Jul-Oct")
    allc = list(C.CONDITIONS)
    within = [k for k in allc if k not in DAY_LEVEL]
    stock_only = [k for k in within if k not in SAME_FOR_ALL]
    print(f"{len(x)} tradable 5-minute candles, {len(allc)} conditions; base long {x.long_r.mean():+.3f}R, "
          f"short {x.short_r.mean():+.3f}R")

    section("1. 5-minute points table, refit monthly, scored on the next month (Apr - Oct)")
    for name, cols, rel in (("all conditions, raw R", allc, None),
                            ("within-day conditions, raw R", within, None),
                            ("within-day conditions, R relative to that day's average", within, ("date",)),
                            ("stock-specific conditions, R relative to all stocks at the same moment", stock_only, ("t",))):
        for side in ("long_r", "short_r"):
            p, y, raw = walk_forward_5min(x, cols, side, rel)
            dec, rho = deciles(p, y)
            dec_raw, _ = deciles(p, raw)
            print(f"  {name} [{side[:-2]}]: predicted-vs-realized corr {rho:+.2f}")
            print(f"      realized by predicted decile, low to high: {dec}")
            if rel:
                print(f"      the same deciles in raw R:                 {dec_raw}")

    section("2. Textbook points, nothing fitted (all 7 months are out of sample)")
    score_l = sum(w * x[k] for k, w in TEXTBOOK.items())
    score_s = sum(w * x[MIRROR.get(k, k)] for k, w in TEXTBOOK.items())
    for side, s in (("long_r", score_l), ("short_r", score_s)):
        t = x.groupby([pd.cut(s, [-99, -4, -2, 0, 2, 4, 99]), "half"], observed=True)[side].mean().unstack().round(3)
        print(f"  {side[:-2]} R by textbook score\n" + t.to_string())

    section("3. Can the trend-vs-reversal regime be read in advance?")
    hi, lo = x[score_l >= 2], x[score_l <= -2]
    pay = (hi.groupby("date").long_r.mean() - lo.groupby("date").long_r.mean()).dropna()
    print(f"  daily payoff of the textbook reading (high score minus low score): mean {pay.mean():+.3f}R over {len(pay)} days")
    print("  autocorrelation by lag in days:", {k: round(pay.autocorr(k), 2) for k in (1, 2, 5, 10)})
    for n in (5, 10, 20):
        trail = pay.rolling(n).mean().shift(1)
        ok = trail.notna()
        print(f"  trailing {n:>2}-day payoff vs the next day: corr {np.corrcoef(trail[ok], pay[ok])[0, 1]:+.2f}")
    s = spy.sort_values("t")
    lc = np.log(s.c)
    v1 = (lc.diff() ** 2).groupby(s.date.to_numpy()).sum()
    v6 = (lc.diff(6) ** 2).groupby(s.date.to_numpy()).apply(lambda q: q.iloc[::6].sum())
    vr = (v6.rolling(10).sum() / v1.rolling(10).sum()).shift(1)
    j = pd.concat([vr.rename("vr"), pay.rename("pay")], axis=1).dropna()
    print(f"  SPY variance ratio over the trailing 10 days vs the next day's payoff: corr {j.vr.corr(j.pay):+.2f}")
    return x


def daily(daily_long, x5):
    d = D.build(pd.read_pickle(daily_long))
    d["y"] = d.date.dt.year
    cols = D.condition_columns(d)
    section(f"4. Daily points table, refit yearly, scored on the next year (2017 - 2026): {len(d)} stock-days, {len(cols)} conditions")
    for target in ("long_r", "long_b", "short_r", "short_b"):
        P, R, Yr = [], [], []
        for Y in range(2017, 2027):
            tr, te = (d.y < Y).to_numpy(), (d.y == Y).to_numpy()
            b = fit(d.loc[tr, cols].to_numpy(float), d.loc[tr, target].to_numpy(), 1000)
            P.append(predict(b, d.loc[te, cols].to_numpy(float)))
            R.append(d.loc[te, target].to_numpy())
            Yr.append(np.full(te.sum(), Y))
        p, r, yr = map(np.concatenate, (P, R, Yr))
        dec, rho = deciles(p, r)
        o = pd.DataFrame({"p": p, "r": r, "y": yr})
        top = o.groupby("y").apply(lambda g: g.r[g.p >= g.p.quantile(0.9)].mean())
        print(f"  {target}: corr {rho:+.2f}; realized by decile {dec}")
        print(f"      best decile by year: " + ", ".join(f"{y} {v:+.2f}" for y, v in top.items())
              + f"  ({(top > 0).sum()} of {len(top)} positive)")

    section("5. Both timeframes: the daily reading (fitted on 2015-2025) applied to 2026's 5-minute candles")
    tr = (d.y < 2026).to_numpy()
    b = fit(d.loc[tr, cols].to_numpy(float), d.loc[tr, "long_r"].to_numpy(), 1000)
    d["dp"] = predict(b, d[cols].to_numpy(float))
    cut50, cut90 = np.quantile(d.dp[tr], [0.5, 0.9])
    dp = d.loc[~tr, ["sym", "date", "dp"]].assign(date=lambda z: z.date.dt.date)
    x = x5.merge(dp, on=["sym", "date"], how="left")
    x["daily"] = np.select([x.dp >= cut90, x.dp >= cut50], ["top 10%", "top half"], "bottom half")
    cost = 2 * (0.01 + 0.0001 * x.c) / (C.STOP_ATR * x.atr)
    x["gross"] = x.long_r + cost
    print(f"  round-trip slippage per trade: mean {cost.mean():.3f}R, median {cost.median():.3f}R")
    t = x.groupby(["daily", "half"]).agg(net=("long_r", "mean"), before_costs=("gross", "mean")).unstack().round(3)
    print(t.to_string())


if __name__ == "__main__":
    x5 = intraday(sys.argv[1], sys.argv[2])
    daily(sys.argv[2], x5)
