"""Tests for refit.py.   Run: python -m unittest test_refit.py"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import points as P
import refit

FIELDS = ["Trade #", "Type", "Signal", "Date/Time", "Price USD", "Contracts", "Profit USD"]


def write_csv(path, trades):
    """trades: (side, risk, day, bits, entry, exit, time). Exit row first, the way TradingView lists them."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for n, (side, risk, day, bits, entry, exit_, t) in enumerate(trades, 1):
            kind = "long" if side == "L" else "short"
            w.writerow({"Trade #": n, "Type": f"Exit {kind}", "Signal": "SELL target", "Date/Time": t,
                        "Price USD": f"{exit_:.2f}", "Contracts": 10, "Profit USD": 0})
            w.writerow({"Trade #": n, "Type": f"Entry {kind}", "Signal": f"M|{side}|{risk:.4f}|{day:.4f}|{bits}",
                        "Date/Time": t, "Price USD": f"{entry:.2f}", "Contracts": 10, "Profit USD": 0})


def planted(n, seed=1):
    """Measure trades where condition 5 adds +0.5R and everything else is noise."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        bits = rng.integers(0, 2, P.FIVE_MIN.__len__())
        r = -0.1 + 0.5 * bits[5] + rng.normal(0, 1.0)
        entry, risk = 100.0, 1.0
        exit_ = entry + r * risk - 4 * 0.01                    # refit adds back 2 ticks per fill
        year = 2018 + i * 6 // n
        out.append(("L", risk, 0.0, "".join(map(str, bits)), entry, exit_, f"{year}-03-02 10:05"))
    return out


class Refit(unittest.TestCase):
    def test_reads_tradingview_rows_and_recovers_r(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "NVDA.csv"
            write_csv(p, [("L", 0.5, 0.02, "1" + "0" * 45, 100.0, 101.0, "2024-01-02 10:00"),
                          ("S", 0.5, -0.01, "0" * 45 + "1", 100.0, 100.5, "2024-01-03 11:00")])
            df = refit.read_trades(p)
        self.assertEqual(list(df.side), ["L", "S"])
        self.assertAlmostEqual(df.r[0], 2.0 + 0.08, places=6)        # +2R net plus 4 ticks added back
        self.assertAlmostEqual(df.r[1], -1.0 + 0.08, places=6)
        self.assertEqual((df[P.FIVE_MIN[0]][0], df[P.FIVE_MIN[-1]][1]), (1, 1))
        self.assertEqual(df.sym[0], "NVDA")

    def test_ignores_trade_mode_rows_and_rejects_wrong_bit_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "AMD.csv"
            write_csv(p, [("L", 0.5, 0.0, "0" * 46, 100, 101, "2024-01-02 10:00")])
            text = p.read_text().replace("M|L|", "BUY +0.06R|", 1)
            p.write_text(text)
            self.assertTrue(refit.read_trades(p).empty)
            write_csv(p, [("L", 0.5, 0.0, "0" * 40, 100, 101, "2024-01-02 10:00")])
            with self.assertRaisesRegex(ValueError, "condition bits"):
                refit.read_trades(p)

    def test_finds_a_planted_edge_out_of_sample(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "TEST.csv"
            write_csv(p, planted(6000))
            df = refit.read_trades(p)
        self.assertTrue(refit.report("L", df, 200.0, "year"))
        f = refit.fit_side(df, 200.0)
        self.assertGreater(f["five"][5], 0.35)
        self.assertLess(max(abs(v) for i, v in enumerate(f["five"]) if i != 5), 0.15)

    def test_pure_noise_is_not_adopted(self):
        passed = 0
        for seed in range(5):
            rng = np.random.default_rng(100 + seed)
            trades = [(s, r, dd, b, e, e + rng.normal(0, 1.0), t) for s, r, dd, b, e, x, t in planted(3000, seed=seed)]
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "TEST.csv"
                write_csv(p, trades)
                passed += refit.report("L", refit.read_trades(p), 200.0, "year")
        self.assertLessEqual(passed, 1)

    def test_points_block_round_trip(self):
        pts = json.loads((Path(__file__).parent / "points.json").read_text())
        block = P.render(pts)
        self.assertTrue(block.startswith(P.BEGIN) and block.endswith(P.END))
        self.assertEqual(block.count("array.from("), 6)
        with tempfile.TemporaryDirectory() as d:
            pine = Path(d) / "x.pine"
            pine.write_text("a\n// POINTS-BEGIN\nOLD-BLOCK-TEXT\n// POINTS-END\nb\n")
            P.write_points(pine, pts)
            text = pine.read_text()
        self.assertTrue(text.startswith("a\n") and text.endswith("\nb\n") and "OLD-BLOCK-TEXT" not in text)


if __name__ == "__main__":
    unittest.main()
