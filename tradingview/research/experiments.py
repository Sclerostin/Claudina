"""Round 1: each setup alone, fixed 2R exits vs partial exits, with and without filters."""
import sys
import pandas as pd
from backtest import backtest, report

data = pd.read_pickle(sys.argv[1])
base = dict(target_r=2.0, max_trades=3, day_stop_r=2.0)
runs = [
    ("ORB  stop=mid  fixed2R", "orb", dict(base, orb_stop="mid")),
    ("ORB  stop=low  fixed2R", "orb", dict(base, orb_stop="low")),
    ("SWEEP fixed2R", "sweep", dict(base)),
    ("SWEEP fixed2R +A/D rising", "sweep", dict(base, need_ad=True)),
    ("SWEEP fixed2R +vol", "sweep", dict(base, need_vol=True)),
    ("PULLBACK fixed2R", "pullback", dict(base)),
    ("VWAP_RECLAIM fixed2R", "vwap_reclaim", dict(base)),
]
for name, setup, p in runs:
    report(name, backtest(data, setup, p))
    report(name + " +SPY filter", backtest(data, setup, dict(p, spy_filter=True)))
