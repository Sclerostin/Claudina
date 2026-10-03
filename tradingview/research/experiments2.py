"""Round 2: decompose the sweep setup; ORB variants; exits."""
import sys
import pandas as pd
from backtest import backtest, report

data = pd.read_pickle(sys.argv[1])
base = dict(target_r=2.0, max_trades=3, day_stop_r=2.0)
runs = [
    ("SWEEP level=ORL", "sweep", dict(base, levels=("orl",))),
    ("SWEEP level=PDL", "sweep", dict(base, levels=("pdl",))),
    ("SWEEP level=swing12", "sweep", dict(base, levels=("swing",))),
    ("SWEEP level=VWAP-2sd", "sweep", dict(base, levels=("vband",))),
    ("SWEEP level=VWAP-1sd", "sweep", dict(base, levels=("vband",), band=1.0)),
    ("SWEEP ORL+PDL  09:45-11:00 (AMD window)", "sweep", dict(base, levels=("orl", "pdl"), last_entry=11 * 60)),
    ("SWEEP ORL+PDL  11:00-15:00", "sweep", dict(base, levels=("orl", "pdl"), first_entry=11 * 60)),
    ("SWEEP all levels rvol>=1.5", "sweep", dict(base, min_rvol=1.5)),
    ("SWEEP all levels daily trend up", "sweep", dict(base, daily_trend=True)),
    ("ORB mid no-vol-filter", "orb", dict(base, orb_stop="mid", orb_vol=0)),
    ("ORB mid no-vol NR7 (Crabel)", "orb", dict(base, orb_stop="mid", orb_vol=0, nr7=True)),
    ("ORB mid no-vol rvol>=1.5", "orb", dict(base, orb_stop="mid", orb_vol=0, min_rvol=1.5)),
]
for name, setup, p in runs:
    report(name, backtest(data, setup, p))
