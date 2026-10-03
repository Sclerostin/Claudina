"""Headline numbers for DT Core 4's VWAP spring at its two selectivity settings."""
import sys
import numpy as np
import pandas as pd
from backtest import SPLIT, backtest

data = pd.read_pickle(sys.argv[1])
days = data.t.dt.date.nunique()
for name, rv in (("Normal (RVOL >= 1.5)", 1.5), ("Strict (RVOL >= 2.0)", 2.0)):
    p = dict(target_r=2.0, max_trades=3, day_stop_r=2.0, levels=("vband",), band=2.0, min_rvol=rv)
    t = backtest(data, "sweep", p).sort_values(["date"]).reset_index(drop=True)
    eq = t.r.cumsum()
    dd = (eq.cummax() - eq).max()
    losing_streak = max((len(list(g)) for k, g in __import__("itertools").groupby(t.r < 0) if k), default=0)
    ins, oos = t[t.date < SPLIT], t[t.date >= SPLIT]
    print(f"{name}: {len(t)} trades in {days} days ({len(t)/days:.2f}/day across 20 stocks), "
          f"win {(t.r>0).mean()*100:.1f}%, avg {t.r.mean():+.3f}R, total {t.r.sum():+.1f}R, "
          f"max drawdown {dd:.1f}R, longest losing streak {losing_streak}")
    print(f"   in-sample {len(ins)} trades avg {ins.r.mean():+.3f}R | out-of-sample {len(oos)} trades avg {oos.r.mean():+.3f}R")
    print(f"   at 0.5% risk per trade: total {t.r.sum()*0.5:+.1f}% of account, worst drawdown {dd*0.5:.1f}%")
