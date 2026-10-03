"""Round 4: exit styles on the near-breakeven setups, with and without the in-play gate."""
import sys
import pandas as pd
from backtest import backtest, report

data = pd.read_pickle(sys.argv[1])
base = dict(target_r=2.0, max_trades=3, day_stop_r=2.0)
exits = {
    "fixed 2R": {},
    "half at 1R, BE, rest 2R": dict(exit="partial"),
    "fixed 2R + 12-bar time stop": dict(time_stop=12),
    "half at 1R, BE, trail 9EMA": dict(exit="partial", trail_e9=True, target_r=4.0),
}
setups = [
    ("SWEEP all", "sweep", {}),
    ("SWEEP all  RVOL>=1.5", "sweep", dict(min_rvol=1.5)),
    ("SWEEP VWAP-2sd RVOL>=1.5", "sweep", dict(levels=("vband",), min_rvol=1.5)),
    ("ORB mid RVOL>=1.5", "orb", dict(orb_stop="mid", orb_vol=0, min_rvol=1.5)),
    ("PULLBACK RVOL>=1.5", "pullback", dict(min_rvol=1.5)),
]
for sname, setup, sp in setups:
    for ename, ep in exits.items():
        report(f"{sname} | {ename}", backtest(data, setup, {**base, **sp, **ep}))
