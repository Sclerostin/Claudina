"""Robustness of the VWAP-spring setup: neighbouring parameters, by month, by symbol, significance."""
import sys
import numpy as np
import pandas as pd
from backtest import backtest, report

data = pd.read_pickle(sys.argv[1])
P = dict(target_r=2.0, max_trades=3, day_stop_r=2.0, levels=("vband",), band=2.0, min_rvol=1.5)
print("Neighbouring parameters (all 87 days):")
for band in (1.5, 2.0, 2.5):
    for rv in (1.2, 1.5, 2.0):
        t = backtest(data, "sweep", dict(P, band=band, min_rvol=rv))
        print(f"  band {band}sd  RVOL>={rv}:  n {len(t):>4}  avgR {t.r.mean():+.3f}  win {(t.r > 0).mean()*100:4.1f}%")
t = backtest(data, "sweep", P)
t["month"] = pd.to_datetime(t.date).dt.strftime("%Y-%m")
print("\nBy month:\n", t.groupby("month").r.agg(n="size", avgR="mean", totR="sum").round(2).to_string())
by_sym = t.groupby("sym").r.agg(n="size", totR="sum").sort_values("totR")
print("\nBy symbol (total R):", ", ".join(f"{s} {r:+.1f} ({n})" for s, (n, r) in by_sym.iterrows()))
top = by_sym.totR.nlargest(2).sum()
print(f"Total {t.r.sum():+.1f}R; without the best 2 symbols {t.r.sum() - top:+.1f}R")
se = t.r.std(ddof=1) / np.sqrt(len(t))
print(f"\nMean {t.r.mean():+.3f}R, standard error {se:.3f}, t = {t.r.mean() / se:.2f} over {len(t)} trades")
rng = np.random.default_rng(7)
boot = [rng.choice(t.r.to_numpy(), len(t)).mean() for _ in range(5000)]
print(f"Bootstrap: chance the true average is <= 0: {np.mean(np.array(boot) <= 0) * 100:.1f}%")
print("\nExit reasons:", t.how.value_counts().to_dict(), " avg bars held:", round(t.bars.mean(), 1))
t.to_csv(sys.argv[2], index=False)
