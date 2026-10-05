"""Tests for the AMD Day Trader -> moomoo bridge.   Run: python -m unittest test_bridge.py

The Bridge and HTTP tests use only the standard library. The MoomooBroker tests check every
SDK call against the real moomoo-api method signatures, so they run only where it is installed.
"""
import inspect
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import bridge

SECRET = "abc123abc123abc123"
CFG = dict(bridge.DEFAULTS, secret=SECRET, listen_port=0)


def buy(ticker="NVDA", qty=100, price=182.40, stop=180.90, target=185.40, grade="A"):
    return {"v": 1, "ticker": ticker, "action": "buy", "qty": qty, "price": price, "stop": stop,
            "target": target, "grade": grade}


def at(h, m, day=5):
    return datetime(2026, 10, day, h, m, tzinfo=bridge.NY)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeBroker:
    """Records calls. Fills buys at the limit's reference price unless told otherwise."""

    def __init__(self, fill=None, stop_refused=False, last=None):
        self.positions, self.calls = {}, []
        self.fill, self.stop_refused, self.last = fill, stop_refused, last
        self.stop_fills = {}

    def position(self, code):
        return self.positions.get(code, 0)

    def buy_limit(self, code, qty, limit, ref):
        self.calls.append(("buy", code, qty, limit))
        got = qty if self.fill is None else self.fill
        self.positions[code] = self.positions.get(code, 0) + got
        return got, ref

    def sell_market(self, code, qty, ref):
        self.calls.append(("sell", code, qty))
        self.positions[code] = self.positions.get(code, 0) - qty
        return qty, ref

    def place_stop(self, code, qty, stop):
        if self.stop_refused:
            raise bridge.OrderError("stop orders not supported")
        self.calls.append(("stop", code, qty, stop))
        return "S-" + code

    def cancel_open(self, code):
        self.calls.append(("cancel", code))

    def order_fill(self, order_id):
        return self.stop_fills.get(order_id, (0, 0.0, "SUBMITTED"))

    def last_price(self, code):
        return self.last


def make(cfg=None, clock=None, **broker_kw):
    broker = FakeBroker(**broker_kw)
    b = bridge.Bridge(dict(CFG, **(cfg or {})), broker, now=clock or Clock(at(10, 5)))
    return b, broker


class Entries(unittest.TestCase):
    def test_buy_is_a_limit_order_then_a_stop_at_moomoo(self):
        b, br = make()
        self.assertIn("BUY NVDA x100", b.handle(buy()))
        self.assertEqual(br.calls, [("buy", "US.NVDA", 100, round(182.40 * 1.0015, 2)), ("stop", "US.NVDA", 100, 180.90)])
        self.assertEqual(b.open_trades()["NVDA"]["qty"], 100)

    def test_stop_covers_only_the_shares_that_filled(self):
        b, br = make(fill=40)
        b.handle(buy())
        self.assertEqual(br.calls[-1], ("stop", "US.NVDA", 40, 180.90))

    def test_nothing_filled_means_no_trade_and_no_retry_today(self):
        b, br = make(fill=0)
        self.assertIn("not filled", b.handle(buy()))
        self.assertEqual(b.open_trades(), {})
        with self.assertRaisesRegex(bridge.OrderError, "already traded today"):
            b.handle(buy())

    def test_live_mode_never_keeps_shares_without_a_stop(self):
        b, br = make(cfg={"mode": "live"}, stop_refused=True)
        self.assertIn("sold again", b.handle(buy()))
        self.assertEqual(br.positions["US.NVDA"], 0)
        self.assertEqual(b.trades["NVDA"]["status"], "closed")

    def test_paper_mode_keeps_the_trade_when_stops_are_not_supported(self):
        b, br = make(cfg={"mode": "paper"}, stop_refused=True)
        b.handle(buy())
        self.assertIn("NVDA", b.open_trades())
        self.assertIsNone(b.open_trades()["NVDA"]["stop_id"])

    def test_refusals(self):
        cases = [
            (buy(grade="B"), {}, None, "grade B is not in grades"),
            (buy(grade="X"), {"grades": ["A", "B"]}, None, "grade X"),
            (buy(ticker="AAPL"), {"allowed_tickers": ["NVDA"]}, None, "allowed_tickers"),
            (buy(), {}, at(9, 40), "outside the entry window"),
            (buy(), {}, at(12, 30), "outside the entry window"),
            (buy(qty=5000), {}, None, "quantity"),
            (buy(qty=200), {}, None, "max_order_value"),
            (buy(stop=183.0), {}, None, "out of order"),
            (buy(target=182.0), {}, None, "out of order"),
            (buy(ticker="nv da"), {}, None, "bad ticker"),
            ({"ticker": "NVDA", "action": "buy"}, {}, None, "missing or bad field"),
            ({"ticker": "NVDA", "action": "hold"}, {}, None, "unknown action"),
            (["not", "an", "object"], {}, None, "not a JSON object"),
        ]
        for msg, cfg, t, why in cases:
            with self.subTest(why=why):
                b, br = make(cfg=cfg, clock=Clock(t) if t else None)
                with self.assertRaisesRegex(bridge.OrderError, why):
                    b.handle(msg)
                self.assertEqual(br.calls, [])

    def test_grade_b_can_be_switched_on(self):
        b, _ = make(cfg={"grades": ["A", "B"]})
        self.assertIn("grade B", b.handle(buy(grade="B")))

    def test_one_setup_per_stock_per_day(self):
        b, _ = make()
        b.handle(buy())
        b.handle({"ticker": "NVDA", "action": "exit", "reason": "target", "price": 185.4})
        with self.assertRaisesRegex(bridge.OrderError, "already traded today"):
            b.handle(buy())

    def test_open_position_and_trade_count_limits(self):
        b, _ = make(cfg={"max_open_positions": 2, "max_trades_per_day": 3})
        b.handle(buy("NVDA", qty=10))
        b.handle(buy("AMD", qty=10))
        with self.assertRaisesRegex(bridge.OrderError, "2 positions already open"):
            b.handle(buy("META", qty=10))
        b.handle({"ticker": "AMD", "action": "exit", "price": 185.4})
        b.handle(buy("META", qty=10))
        b.handle({"ticker": "META", "action": "exit", "price": 185.4})
        with self.assertRaisesRegex(bridge.OrderError, "3 trades already today"):
            b.handle(buy("TSLA", qty=10))

    def test_daily_loss_limit_in_r(self):
        b, _ = make(cfg={"max_daily_loss_r": 2.0})
        for t in ("NVDA", "AMD"):
            b.handle(buy(t, qty=10))
            b.handle({"ticker": t, "action": "exit", "reason": "stop", "price": 180.90})
        self.assertAlmostEqual(b.day_r(), -2.0, places=2)
        with self.assertRaisesRegex(bridge.OrderError, "daily loss limit"):
            b.handle(buy("META", qty=10))

    def test_pause_blocks_entries_but_not_exits(self):
        b, br = make()
        b.handle(buy())
        b.paused = True
        with self.assertRaisesRegex(bridge.OrderError, "paused"):
            b.handle(buy("AMD"))
        self.assertIn("exit NVDA", b.handle({"ticker": "NVDA", "action": "exit", "price": 183.0}))

    def test_refuses_a_stock_already_held_in_the_account(self):
        b, br = make()
        br.positions["US.NVDA"] = 50
        with self.assertRaisesRegex(bridge.OrderError, "already held"):
            b.handle(buy())


class Exits(unittest.TestCase):
    def test_exit_cancels_the_stop_and_sells(self):
        b, br = make()
        b.handle(buy())
        out = b.handle({"ticker": "NVDA", "action": "exit", "reason": "target", "price": 185.40})
        self.assertIn("+2.00R", out)
        self.assertEqual(br.calls[-2:], [("cancel", "US.NVDA"), ("sell", "US.NVDA", 100)])

    def test_exit_for_something_the_bridge_did_not_open(self):
        b, br = make()
        self.assertIn("nothing open", b.handle({"ticker": "NVDA", "action": "exit"}))
        br.positions["US.NVDA"] = 30
        self.assertIn("left alone", b.handle({"ticker": "NVDA", "action": "exit"}))
        self.assertEqual(br.calls, [])

    def test_watcher_books_a_stop_that_filled_at_moomoo(self):
        b, br = make()
        b.handle(buy())
        br.positions["US.NVDA"] = 0
        br.stop_fills["S-US.NVDA"] = (100, 180.85, "FILLED_ALL")
        b.tick()
        t = b.trades["NVDA"]
        self.assertEqual((t["status"], t["why"], t["exit"]), ("closed", "stop", 180.85))
        self.assertLess(t["r"], -1.0)

    def test_watcher_sells_at_the_target_from_quotes(self):
        b, br = make(last=185.50)
        b.handle(buy())
        b.tick()
        self.assertEqual(b.trades["NVDA"]["why"], "target")
        self.assertEqual(br.positions["US.NVDA"], 0)

    def test_watcher_uses_quotes_as_the_stop_when_moomoo_has_none(self):
        b, br = make(cfg={"mode": "paper"}, stop_refused=True, last=180.80)
        b.handle(buy())
        b.tick()
        self.assertEqual(b.trades["NVDA"]["why"], "stop")

    def test_everything_is_sold_at_355(self):
        clock = Clock(at(10, 5))
        b, br = make(clock=clock)
        b.handle(buy("NVDA", qty=10))
        b.handle(buy("AMD", qty=10))
        clock.t = at(15, 54)
        b.tick()
        self.assertEqual(len(b.open_trades()), 2)
        clock.t = at(15, 55)
        b.tick()
        self.assertEqual(b.open_trades(), {})
        self.assertEqual({x["why"] for x in b.trades.values()}, {"end of day"})

    def test_a_new_day_starts_a_new_book(self):
        clock = Clock(at(10, 5))
        b, _ = make(clock=clock)
        b.handle(buy())
        b.handle({"ticker": "NVDA", "action": "exit", "price": 185.4})
        clock.t = at(10, 5, day=6)
        self.assertIn("BUY NVDA", b.handle(buy()))

    def test_book_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            broker = FakeBroker()
            b = bridge.Bridge(CFG, broker, state_path=path, now=Clock(at(10, 5)))
            b.handle(buy())
            b2 = bridge.Bridge(CFG, broker, state_path=path, now=Clock(at(11, 0)))
            self.assertIn("NVDA", b2.open_trades())
            with self.assertRaisesRegex(bridge.OrderError, "already traded today"):
                b2.handle(buy())


class DryRun(unittest.TestCase):
    def test_round_trip_in_dry_run(self):
        b = bridge.Bridge(CFG, bridge.DryRunBroker(), now=Clock(at(10, 5)))
        self.assertIn("BUY NVDA", b.handle(buy()))
        self.assertIn("exit NVDA (target)", b.handle({"ticker": "NVDA", "action": "exit", "reason": "target", "price": 185.4}))
        self.assertEqual(b.summary()["trades_today"], 1)


class Webhook(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.bridge = bridge.Bridge(CFG, self.broker, now=Clock(at(10, 5)))
        self.server = bridge.make_server(CFG, self.bridge)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body.encode() if body else None,
                                     method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, ""

    def test_order_alert_is_accepted_and_traded(self):
        self.assertEqual(self.request(f"/hook/{SECRET}", json.dumps(buy()))[0], 200)
        for _ in range(100):
            if len(self.broker.calls) == 2:
                break
            threading.Event().wait(0.05)
        self.assertEqual(self.broker.calls[0][:3], ("buy", "US.NVDA", 100))

    def test_wrong_secret_and_plain_text(self):
        self.assertEqual(self.request("/hook/wrongsecret", json.dumps(buy()))[0], 404)
        self.assertEqual(self.request(f"/hook/{SECRET}", "NVDA: BUY 100 shares at 182.40"), (200, "ignored: not an order"))
        self.assertEqual(self.broker.calls, [])

    def test_health_pause_and_resume(self):
        status, body = self.request("/health")
        self.assertEqual(json.loads(body)["mode"], "dry_run")
        self.assertEqual(self.request(f"/pause/{SECRET}")[0], 200)
        self.assertTrue(self.bridge.paused)
        self.assertEqual(self.request("/pause/wrongsecret")[0], 404)
        self.request(f"/resume/{SECRET}")
        self.assertFalse(self.bridge.paused)


try:
    import moomoo
    import pandas as pd
except ImportError:
    moomoo = None


@unittest.skipIf(moomoo is None, "moomoo-api not installed")
class MoomooCalls(unittest.TestCase):
    """Every call the broker makes must bind to the real SDK method signature."""

    def make(self, mode="paper", orders=None, positions=None, snapshot=None):
        calls = []
        orders = orders if orders is not None else [{"order_id": "1", "order_status": "FILLED_ALL", "dealt_qty": 100,
                                                      "dealt_avg_price": 182.45}]
        positions = positions or []

        def fake(real_cls, answers):
            class Ctx:
                def __getattr__(self, name):
                    method = getattr(real_cls, name)

                    def call(*args, **kwargs):
                        inspect.signature(method).bind(None, *args, **kwargs)
                        calls.append((name, args, kwargs))
                        return answers(name)
                    return call
            return Ctx()

        def trade_answers(name):
            if name == "place_order":
                return moomoo.RET_OK, pd.DataFrame([{"order_id": "1"}])
            if name == "order_list_query":
                return moomoo.RET_OK, pd.DataFrame(orders)
            if name == "position_list_query":
                return moomoo.RET_OK, pd.DataFrame(positions, columns=["qty", "position_side"])
            return moomoo.RET_OK, pd.DataFrame()

        def quote_answers(name):
            if snapshot is None:
                return moomoo.RET_ERROR, "no quote right"
            return moomoo.RET_OK, pd.DataFrame([{"last_price": snapshot}])

        cfg = dict(CFG, mode=mode, trade_password="pw" if mode == "live" else "", fill_timeout_sec=0)
        broker = bridge.MoomooBroker(cfg, mm=moomoo, ctx=fake(moomoo.OpenSecTradeContext, trade_answers),
                                     quote_ctx=fake(moomoo.OpenQuoteContext, quote_answers))
        return broker, calls

    def test_paper_uses_simulate_and_live_unlocks(self):
        broker, calls = self.make("paper")
        self.assertEqual((broker.env, calls), (moomoo.TrdEnv.SIMULATE, []))
        broker, calls = self.make("live")
        self.assertEqual(broker.env, moomoo.TrdEnv.REAL)
        self.assertEqual(calls[0][0], "unlock_trade")

    def test_limit_buy_market_sell_and_stop(self):
        broker, calls = self.make()
        self.assertEqual(broker.buy_limit("US.NVDA", 100, 182.67, 182.40), (100, 182.45))
        broker.sell_market("US.NVDA", 100, 185.40)
        broker.place_stop("US.NVDA", 100, 180.90)
        places = [kw for name, _, kw in calls if name == "place_order"]
        self.assertEqual((places[0]["order_type"], places[0]["trd_side"], places[0]["price"]),
                         (moomoo.OrderType.NORMAL, moomoo.TrdSide.BUY, 182.67))
        self.assertEqual((places[1]["order_type"], places[1]["trd_side"]), (moomoo.OrderType.MARKET, moomoo.TrdSide.SELL))
        self.assertEqual((places[2]["order_type"], places[2]["aux_price"]), (moomoo.OrderType.STOP, 180.90))
        self.assertTrue(all(p["time_in_force"] == moomoo.TimeInForce.DAY for p in places))

    def test_unfilled_limit_buy_is_cancelled(self):
        broker, calls = self.make(orders=[{"order_id": "1", "order_status": "SUBMITTED", "dealt_qty": 0,
                                           "dealt_avg_price": 0}])
        self.assertEqual(broker.buy_limit("US.NVDA", 100, 182.67, 182.40)[0], 0)
        self.assertIn("modify_order", [c[0] for c in calls])

    def test_cancel_only_open_orders_and_signed_positions(self):
        broker, calls = self.make(orders=[
            {"order_id": "7", "order_status": "SUBMITTED", "dealt_qty": 0, "dealt_avg_price": 0},
            {"order_id": "8", "order_status": "FILLED_ALL", "dealt_qty": 100, "dealt_avg_price": 182.4}])
        broker.cancel_open("US.NVDA")
        self.assertEqual(len([c for c in calls if c[0] == "modify_order"]), 1)
        broker, _ = self.make(positions=[{"qty": 40, "position_side": "SHORT"}])
        self.assertEqual(broker.position("US.NVDA"), -40)
        broker, _ = self.make(positions=[{"qty": 100, "position_side": "LONG"}])
        self.assertEqual(broker.position("US.NVDA"), 100)

    def test_quotes_are_optional(self):
        broker, _ = self.make(snapshot=185.5)
        self.assertEqual(broker.last_price("US.NVDA"), 185.5)
        broker, _ = self.make(snapshot=None)
        self.assertIsNone(broker.last_price("US.NVDA"))
        self.assertIsNone(broker.quotes)

    def test_rejected_order_raises(self):
        broker, _ = self.make()
        broker.ctx = type("Bad", (), {"place_order": lambda *a, **k: (moomoo.RET_ERROR, "insufficient buying power")})()
        with self.assertRaisesRegex(bridge.OrderError, "insufficient"):
            broker.place_stop("US.NVDA", 100, 180.9)


if __name__ == "__main__":
    unittest.main()
