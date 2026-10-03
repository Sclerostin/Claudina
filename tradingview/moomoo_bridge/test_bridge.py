"""Tests for the DT Core -> moomoo bridge.  Run: python -m unittest test_bridge.py

The Bridge and HTTP tests need only the standard library. The MoomooBroker tests
check every SDK call against the real moomoo-api method signatures, so they run
only where moomoo-api is installed.
"""
import inspect
import json
import threading
import unittest
import urllib.error
import urllib.request

import bridge

SECRET = "abc123abc123abc123"
CFG = dict(bridge.DEFAULTS, secret=SECRET, listen_port=0)


def entry(action="buy", qty=100, price=102.0, stop=100.75, target=104.5, ticker="NVDA"):
    return {"ticker": ticker, "action": action, "quantity": qty, "price": price,
            "takeProfit": {"limitPrice": target}, "stopLoss": {"type": "stop", "stopPrice": stop}}


class FakeBroker:
    def __init__(self, positions=None, fill=None):
        self.positions = dict(positions or {})
        self.fill = fill
        self.calls = []

    def position(self, code):
        return self.positions.get(code, 0)

    def market(self, code, side, qty, ref):
        self.calls.append(("market", code, side, qty))
        return qty if self.fill is None else self.fill

    def stop(self, code, side, qty, price):
        self.calls.append(("stop", code, side, qty, price))

    def cancel_open(self, code):
        self.calls.append(("cancel", code))


class BridgeLogic(unittest.TestCase):
    def run_msg(self, msg, cfg=None, **broker_kw):
        broker = FakeBroker(**broker_kw)
        result = bridge.Bridge(dict(CFG, **(cfg or {})), broker).handle(msg)
        return result, broker.calls

    def test_buy_places_market_then_protective_stop(self):
        _, calls = self.run_msg(entry())
        self.assertEqual(calls, [("market", "US.NVDA", "buy", 100), ("stop", "US.NVDA", "sell", 100, 100.75)])

    def test_sell_is_a_short_with_buy_to_cover_stop(self):
        _, calls = self.run_msg(entry("sell", price=99.0, stop=100.25, target=96.5))
        self.assertEqual(calls, [("market", "US.NVDA", "short", 100), ("stop", "US.NVDA", "cover", 100, 100.25)])

    def test_stop_uses_filled_quantity(self):
        _, calls = self.run_msg(entry(), fill=60)
        self.assertEqual(calls[-1], ("stop", "US.NVDA", "sell", 60, 100.75))

    def test_exit_closes_long_short_or_nothing(self):
        _, calls = self.run_msg({"ticker": "NVDA", "action": "exit"}, positions={"US.NVDA": 100})
        self.assertEqual(calls, [("cancel", "US.NVDA"), ("market", "US.NVDA", "sell", 100)])
        _, calls = self.run_msg({"ticker": "NVDA", "action": "exit"}, positions={"US.NVDA": -40})
        self.assertEqual(calls, [("cancel", "US.NVDA"), ("market", "US.NVDA", "cover", 40)])
        result, calls = self.run_msg({"ticker": "NVDA", "action": "exit"})
        self.assertEqual(calls, [("cancel", "US.NVDA")])
        self.assertIn("already flat", result)

    def test_no_second_entry_while_in_a_position(self):
        result, calls = self.run_msg(entry(), positions={"US.NVDA": 100})
        self.assertEqual(calls, [])
        self.assertIn("skipped", result)

    def test_refusals(self):
        cases = [
            (entry(qty=5000), None, "quantity"),
            (entry(qty=1000, price=102.0), None, "max_order_value"),
            (entry(stop=103.0), None, "out of order"),
            (entry("sell", price=99.0, stop=100.25, target=96.5), {"allow_short": False}, "short selling is off"),
            (entry(ticker="AAPL"), {"allowed_tickers": ["NVDA"]}, "allowed_tickers"),
            (entry(ticker="nv da"), None, "bad ticker"),
            ({"ticker": "NVDA", "action": "buy"}, None, "missing or bad field"),
            ({"ticker": "NVDA", "action": "hold"}, None, "unknown action"),
            (["not", "a", "dict"], None, "not a JSON object"),
        ]
        for msg, cfg, why in cases:
            with self.subTest(why=why):
                with self.assertRaisesRegex(bridge.OrderError, why):
                    self.run_msg(msg, cfg)

    def test_several_trades_in_one_day(self):
        b = bridge.Bridge(CFG, bridge.DryRunBroker())
        for _ in range(3):                      # DT Core 4 re-enters after each exit
            self.assertIn("entered BUY", b.handle(entry()))
            self.assertIn("closed 100", b.handle({"ticker": "NVDA", "action": "exit", "price": 104.5}))

    def test_dry_run_broker_tracks_a_round_trip(self):
        b = bridge.Bridge(CFG, bridge.DryRunBroker())
        self.assertIn("entered BUY", b.handle(entry()))
        self.assertIn("skipped", b.handle(entry()))
        self.assertIn("closed 100", b.handle({"ticker": "NVDA", "action": "exit"}))
        self.assertIn("already flat", b.handle({"ticker": "NVDA", "action": "exit"}))


class Webhook(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.server = bridge.make_server(CFG, self.broker)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body.encode(), method="POST",
                                     headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_valid_alert_is_accepted_and_traded(self):
        self.assertEqual(self.post(f"/hook/{SECRET}", json.dumps(entry())), 200)
        for _ in range(50):
            if len(self.broker.calls) == 2:
                break
            threading.Event().wait(0.05)
        self.assertEqual(self.broker.calls[0], ("market", "US.NVDA", "buy", 100))

    def test_wrong_secret_and_bad_body_are_rejected(self):
        self.assertEqual(self.post("/hook/wrongsecret", json.dumps(entry())), 404)
        self.assertEqual(self.post(f"/hook/{SECRET}", "BUY NVDA @ 102"), 400)
        self.assertEqual(self.broker.calls, [])

    def test_health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as r:
            self.assertIn(b"dry_run", r.read())


try:
    import moomoo
    import pandas as pd
except ImportError:
    moomoo = None


@unittest.skipIf(moomoo is None, "moomoo-api not installed")
class MoomooCalls(unittest.TestCase):
    """Every call the broker makes must bind to the real SDK method signature."""

    def make(self, mode="paper", orders=None, positions=None):
        real = moomoo.OpenSecTradeContext
        calls = []
        orders = orders if orders is not None else [{"order_id": "1", "order_status": "FILLED_ALL",
                                                     "dealt_qty": 100, "dealt_avg_price": 102.0}]
        positions = positions or []

        class Ctx:
            def __getattr__(self, name):
                method = getattr(real, name)

                def call(*args, **kwargs):
                    inspect.signature(method).bind(None, *args, **kwargs)
                    calls.append((name, kwargs))
                    if name == "place_order":
                        return moomoo.RET_OK, pd.DataFrame([{"order_id": "1"}])
                    if name == "order_list_query":
                        return moomoo.RET_OK, pd.DataFrame(orders)
                    if name == "position_list_query":
                        return moomoo.RET_OK, pd.DataFrame(positions, columns=["qty", "position_side"])
                    return moomoo.RET_OK, pd.DataFrame()
                return call

        cfg = dict(CFG, mode=mode, trade_password="pw" if mode == "live" else "")
        return bridge.MoomooBroker(cfg, mm=moomoo, ctx=Ctx()), calls

    def test_paper_mode_uses_simulate_and_skips_unlock(self):
        broker, calls = self.make("paper")
        self.assertEqual(broker.env, moomoo.TrdEnv.SIMULATE)
        self.assertEqual(calls, [])

    def test_live_mode_unlocks_with_password(self):
        broker, calls = self.make("live")
        self.assertEqual(broker.env, moomoo.TrdEnv.REAL)
        self.assertEqual(calls, [("unlock_trade", {"password": "pw"})])

    def test_market_and_stop_orders(self):
        broker, calls = self.make()
        self.assertEqual(broker.market("US.NVDA", "buy", 100, 102.0), 100)
        broker.stop("US.NVDA", "sell", 100, 100.75)
        market, stop = calls[0][1], calls[2][1]
        self.assertEqual((market["order_type"], market["trd_side"], market["qty"]),
                         (moomoo.OrderType.MARKET, moomoo.TrdSide.BUY, 100))
        self.assertEqual((stop["order_type"], stop["trd_side"], stop["aux_price"]),
                         (moomoo.OrderType.STOP, moomoo.TrdSide.SELL, 100.75))

    def test_short_side_names(self):
        broker, calls = self.make()
        broker.market("US.NVDA", "short", 50, 99.0)
        broker.stop("US.NVDA", "cover", 50, 100.25)
        self.assertEqual(calls[0][1]["trd_side"], moomoo.TrdSide.SELL_SHORT)
        self.assertEqual(calls[2][1]["trd_side"], moomoo.TrdSide.BUY_BACK)

    def test_rejected_order_raises(self):
        broker, _ = self.make(orders=[{"order_id": "1", "order_status": "FAILED", "dealt_qty": 0,
                                       "dealt_avg_price": 0}])
        with self.assertRaisesRegex(bridge.OrderError, "FAILED"):
            broker.market("US.NVDA", "buy", 100, 102.0)

    def test_cancel_only_open_orders_and_signed_positions(self):
        broker, calls = self.make(orders=[
            {"order_id": "7", "order_status": "SUBMITTED", "dealt_qty": 0, "dealt_avg_price": 0},
            {"order_id": "8", "order_status": "FILLED_ALL", "dealt_qty": 100, "dealt_avg_price": 102.0}])
        broker.cancel_open("US.NVDA")
        cancels = [c for c in calls if c[0] == "modify_order"]
        self.assertEqual(len(cancels), 1)
        broker, _ = self.make(positions=[{"qty": 40, "position_side": "SHORT"}])
        self.assertEqual(broker.position("US.NVDA"), -40)
        broker, _ = self.make(positions=[{"qty": 100, "position_side": "LONG"}])
        self.assertEqual(broker.position("US.NVDA"), 100)


if __name__ == "__main__":
    unittest.main()
