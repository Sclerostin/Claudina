"""AMD Day Trader -> moomoo bridge.

TradingView sends each AMD Day Trader order to this program as a webhook. The bridge checks it
against your limits and places it in your moomoo account through moomoo OpenAPI (OpenD).

  buy   limit buy at the alert price + a small cushion (never chases further), then a
        protective STOP order at moomoo for the shares that filled
  exit  cancel the stock's open orders and sell the position at market

The bridge also runs on its own clock and does not depend on TradingView for safety:
  - every position it opened is sold at 3:55 PM New York time
  - a stop that fills at moomoo is noticed and booked
  - with moomoo quotes available, it sells at the target the moment the price gets there

Modes (config.json "mode"):  dry_run (log only) -> paper (moomoo paper account) -> live
Run:  python bridge.py           reads config.json next to this file
"""
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")
log = logging.getLogger("bridge")

DEFAULTS = {
    "secret": "",
    "mode": "dry_run",
    "trade_password": "",
    "opend_host": "127.0.0.1",
    "opend_port": 11111,
    "listen_host": "127.0.0.1",
    "listen_port": 8080,
    "grades": ["A"],
    "allowed_tickers": [],
    "max_open_positions": 3,
    "max_trades_per_day": 6,
    "max_daily_loss_r": 3.0,
    "max_qty": 1000,
    "max_order_value": 25000,
    "entry_cushion_pct": 0.15,
    "fill_timeout_sec": 20,
    "entry_window": ["09:45", "12:05"],
    "flatten_at": "15:55",
    "watch_targets": True,
    "watch_every_sec": 3,
}
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class OrderError(Exception):
    """An order was refused by the bridge's checks or by moomoo."""


def hhmm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def load_config(path):
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(Path(path).read_text()))
    if len(cfg["secret"]) < 16 or cfg["secret"].startswith("CHANGE") or not cfg["secret"].isalnum():
        raise SystemExit("config.json: set 'secret' to 16 or more random letters and digits")
    if cfg["mode"] not in ("dry_run", "paper", "live"):
        raise SystemExit("config.json: 'mode' must be dry_run, paper or live")
    if cfg["mode"] == "live" and not cfg["trade_password"]:
        raise SystemExit("config.json: live mode needs 'trade_password' (your moomoo trading password)")
    cfg["allowed_tickers"] = [t.upper() for t in cfg["allowed_tickers"]]
    cfg["grades"] = [g.upper() for g in cfg["grades"]]
    return cfg


# ------------------------------------------------------------------ brokers
class DryRunBroker:
    """Logs orders instead of sending them; fills everything at the alert's price."""

    def __init__(self):
        self.positions, self.orders, self.n = {}, {}, 0

    def position(self, code):
        return self.positions.get(code, 0)

    def buy_limit(self, code, qty, limit, ref):
        log.info("DRY RUN  buy %s x%d, limit %.2f", code, qty, limit)
        self.positions[code] = self.positions.get(code, 0) + qty
        return qty, ref

    def sell_market(self, code, qty, ref):
        log.info("DRY RUN  sell %s x%d at market", code, qty)
        self.positions[code] = self.positions.get(code, 0) - qty
        return qty, ref

    def place_stop(self, code, qty, stop):
        self.n += 1
        self.orders[str(self.n)] = dict(code=code, qty=qty, stop=stop, open=True)
        log.info("DRY RUN  stop %s x%d at %.2f", code, qty, stop)
        return str(self.n)

    def cancel_open(self, code):
        for o in self.orders.values():
            if o["code"] == code:
                o["open"] = False
        log.info("DRY RUN  cancel open orders for %s", code)

    def order_fill(self, order_id):
        return 0, 0.0, "NONE"

    def last_price(self, code):
        return None


class MoomooBroker:
    """Talks to moomoo through the OpenD gateway."""

    OPEN = ("UNSUBMITTED", "WAITING_SUBMIT", "SUBMITTING", "SUBMITTED", "FILLED_PART")
    DEAD = ("SUBMIT_FAILED", "TIMEOUT", "FAILED", "CANCELLED_ALL", "CANCELLED_PART", "DISABLED", "DELETED",
            "FILL_CANCELLED")

    def __init__(self, cfg, mm=None, ctx=None, quote_ctx=None):
        if mm is None:
            import moomoo as mm
        self.mm, self.cfg = mm, cfg
        self.env = mm.TrdEnv.REAL if cfg["mode"] == "live" else mm.TrdEnv.SIMULATE
        self.ctx = ctx or mm.OpenSecTradeContext(filter_trdmarket=mm.TrdMarket.US, host=cfg["opend_host"],
                                                 port=cfg["opend_port"], security_firm=mm.SecurityFirm.FUTUINC)
        self.quotes = quote_ctx
        if self.quotes is None and cfg["watch_targets"]:
            try:
                self.quotes = mm.OpenQuoteContext(host=cfg["opend_host"], port=cfg["opend_port"])
            except Exception as e:                             # quotes are optional
                log.warning("no moomoo quotes (%s): targets will come from TradingView alerts", e)
        if self.env == mm.TrdEnv.REAL:
            self._ok(self.ctx.unlock_trade(password=cfg["trade_password"]), "unlock trading")

    def _ok(self, result, what):
        ret, data = result
        if ret != self.mm.RET_OK:
            raise OrderError(f"{what} failed: {data}")
        return data

    def position(self, code):
        data = self._ok(self.ctx.position_list_query(code=code, trd_env=self.env), "position query")
        total = 0.0
        for _, row in data.iterrows():
            qty = abs(float(row["qty"]))
            total += -qty if str(row.get("position_side", "")) == "SHORT" else qty
        return total

    def _place(self, code, qty, price, side, kind, aux=None):
        kw = dict(price=price, qty=qty, code=code, trd_side=side, order_type=kind, trd_env=self.env,
                  time_in_force=self.mm.TimeInForce.DAY)
        if aux is not None:
            kw["aux_price"] = aux
        data = self._ok(self.ctx.place_order(**kw), f"{kind} order for {code}")
        return str(data["order_id"].iloc[0])

    def order_fill(self, order_id):
        """(filled shares, average price, status) of one order."""
        data = self._ok(self.ctx.order_list_query(order_id=order_id, trd_env=self.env), "order status")
        if not len(data):
            return 0, 0.0, "UNKNOWN"
        row = data.iloc[0]
        return float(row["dealt_qty"]), float(row["dealt_avg_price"] or 0), str(row["order_status"])

    def _wait(self, order_id, timeout):
        deadline = time.time() + timeout
        while True:
            dealt, avg, status = self.order_fill(order_id)
            if status == "FILLED_ALL" or status in self.DEAD:
                return dealt, avg, status
            if time.time() >= deadline:
                return dealt, avg, status
            time.sleep(1.0)

    def _cancel(self, order_id):
        self._ok(self.ctx.modify_order(self.mm.ModifyOrderOp.CANCEL, order_id, 0, 0, trd_env=self.env),
                 f"cancel order {order_id}")

    def buy_limit(self, code, qty, limit, ref):
        oid = self._place(code, qty, limit, self.mm.TrdSide.BUY, self.mm.OrderType.NORMAL)
        log.info("sent limit buy %s x%d at %.2f (order %s)", code, qty, limit, oid)
        dealt, avg, status = self._wait(oid, self.cfg["fill_timeout_sec"])
        if status != "FILLED_ALL" and status not in self.DEAD:
            self._cancel(oid)                                  # don't chase: drop what didn't fill
            time.sleep(1.0)
            dealt, avg, status = self.order_fill(oid)
        log.info("buy %s: filled x%g at %.4f (%s)", code, dealt, avg, status)
        return int(dealt), avg

    def sell_market(self, code, qty, ref):
        oid = self._place(code, qty, ref, self.mm.TrdSide.SELL, self.mm.OrderType.MARKET)
        dealt, avg, status = self._wait(oid, self.cfg["fill_timeout_sec"])
        log.info("sell %s: filled x%g at %.4f (%s, order %s)", code, dealt, avg, status, oid)
        return int(dealt), avg

    def place_stop(self, code, qty, stop):
        oid = self._place(code, qty, stop, self.mm.TrdSide.SELL, self.mm.OrderType.STOP, aux=stop)
        log.info("protective stop %s x%d at %.2f (order %s)", code, qty, stop, oid)
        return oid

    def cancel_open(self, code):
        data = self._ok(self.ctx.order_list_query(code=code, trd_env=self.env), "order list")
        for _, row in data.iterrows():
            if str(row["order_status"]) in self.OPEN:
                self._cancel(row["order_id"])
                log.info("cancelled order %s for %s", row["order_id"], code)

    def last_price(self, code):
        if self.quotes is None:
            return None
        ret, data = self.quotes.get_market_snapshot([code])
        if ret != self.mm.RET_OK or not len(data):
            log.warning("moomoo quotes unavailable (%s): targets will come from TradingView alerts", data)
            self.quotes = None
            return None
        return float(data["last_price"].iloc[0])


# ------------------------------------------------------------------ the bridge
class Bridge:
    """Checks each message, places orders, and keeps today's book of trades."""

    def __init__(self, cfg, broker, state_path=None, now=None):
        self.cfg, self.broker = cfg, broker
        self.now = now or (lambda: datetime.now(NY))
        self.state_path = Path(state_path) if state_path else None
        self.lock = threading.RLock()
        self.paused = False
        self.day, self.trades = None, {}
        self._load()

    # ---- today's book
    def _roll(self):
        today = self.now().date().isoformat()
        if self.day != today:
            self.day, self.trades = today, {}

    def _load(self):
        if self.state_path and self.state_path.exists():
            s = json.loads(self.state_path.read_text())
            self.day, self.trades = s.get("day"), s.get("trades", {})
        self._roll()

    def _save(self):
        if self.state_path:
            self.state_path.write_text(json.dumps({"day": self.day, "trades": self.trades}, indent=1))

    def open_trades(self):
        return {t: x for t, x in self.trades.items() if x["status"] == "open"}

    def day_r(self):
        return sum(x.get("r", 0.0) for x in self.trades.values() if x["status"] == "closed")

    def summary(self):
        with self.lock:
            self._roll()
            return {"mode": self.cfg["mode"], "paused": self.paused, "day": self.day,
                    "open": sorted(self.open_trades()), "trades_today": len(self.trades),
                    "day_r": round(self.day_r(), 2)}

    # ---- messages from TradingView
    def handle(self, msg):
        if not isinstance(msg, dict):
            raise OrderError("message is not a JSON object")
        ticker = str(msg.get("ticker", "")).upper().strip()
        action = str(msg.get("action", "")).lower().strip()
        if not TICKER.match(ticker):
            raise OrderError(f"bad ticker {ticker!r}")
        with self.lock:
            self._roll()
            if action == "buy":
                return self._buy(ticker, msg)
            if action == "exit":
                return self._exit(ticker, str(msg.get("reason", "alert")), float(msg.get("price") or 0))
        raise OrderError(f"unknown action {action!r}")

    def _buy(self, ticker, msg):
        cfg = self.cfg
        try:
            qty = int(float(msg["qty"]))
            price, stop, target = float(msg["price"]), float(msg["stop"]), float(msg["target"])
            grade = str(msg.get("grade", "")).upper()
        except (KeyError, TypeError, ValueError) as e:
            raise OrderError(f"missing or bad field: {e}") from e
        mins = self.now().hour * 60 + self.now().minute
        lo, hi = (hhmm(x) for x in cfg["entry_window"])
        checks = [
            (self.paused or (HERE / "PAUSE").exists(), "the bridge is paused"),
            (grade not in cfg["grades"], f"grade {grade or '?'} is not in grades {cfg['grades']}"),
            (bool(cfg["allowed_tickers"]) and ticker not in cfg["allowed_tickers"], f"{ticker} is not in allowed_tickers"),
            (not lo <= mins <= hi, f"outside the entry window {cfg['entry_window'][0]}-{cfg['entry_window'][1]} New York time"),
            (ticker in self.trades, f"{ticker} was already traded today (one setup per stock per day)"),
            (len(self.open_trades()) >= cfg["max_open_positions"], f"{cfg['max_open_positions']} positions already open"),
            (len(self.trades) >= cfg["max_trades_per_day"], f"{cfg['max_trades_per_day']} trades already today"),
            (self.day_r() <= -cfg["max_daily_loss_r"], f"daily loss limit reached ({self.day_r():.1f}R)"),
            (not 1 <= qty <= cfg["max_qty"], f"quantity {qty} outside 1..{cfg['max_qty']}"),
            (qty * price > cfg["max_order_value"], f"order value {qty * price:,.0f} is over max_order_value"),
            (not stop < price < target, f"prices out of order: stop {stop}, price {price}, target {target}"),
        ]
        for failed, why in checks:
            if failed:
                raise OrderError(why)
        code = "US." + ticker
        if self.broker.position(code) != 0:
            raise OrderError(f"{ticker} is already held in the account")
        limit = round(price * (1 + cfg["entry_cushion_pct"] / 100), 2)
        filled, avg = self.broker.buy_limit(code, qty, limit, price)
        if filled <= 0:
            self.trades[ticker] = dict(status="skipped", grade=grade, why="not filled at or below the limit")
            self._save()
            return f"{ticker}: buy not filled at {limit} or better - skipped"
        avg = avg or price
        trade = dict(status="open", grade=grade, qty=filled, entry=avg, stop=stop, target=target,
                     risk=max(avg - stop, 0.01), stop_id=None, opened=self.now().isoformat(timespec="seconds"))
        self.trades[ticker] = trade
        try:
            trade["stop_id"] = self.broker.place_stop(code, filled, stop)
        except OrderError as e:
            if self.cfg["mode"] == "live":
                log.error("protective stop refused (%s): selling %s now rather than hold it unprotected", e, ticker)
                self._close(ticker, "no stop", price)
                self._save()
                return f"{ticker}: bought x{filled} but the stop was refused, so it was sold again"
            log.warning("protective stop not placed (%s); the bridge and TradingView will exit instead", e)
        self._save()
        return f"BUY {ticker} x{filled} at {avg:.2f} - stop {stop} - target {target} - grade {grade}"

    def _exit(self, ticker, reason, ref):
        if ticker not in self.open_trades():
            code = "US." + ticker
            if self.broker.position(code) == 0:
                return f"exit {ticker} ({reason}): nothing open"
            log.warning("exit %s: position exists but was not opened by the bridge today - left alone", ticker)
            return f"exit {ticker}: not a bridge trade, left alone"
        return self._close(ticker, reason, ref)

    def _close(self, ticker, reason, ref):
        trade, code = self.trades[ticker], "US." + ticker
        self.broker.cancel_open(code)
        held = int(self.broker.position(code))
        px = ref
        if held > 0:
            sold, avg = self.broker.sell_market(code, held, ref or trade["entry"])
            px = avg or ref or trade["entry"]
        else:                                                  # already out: the stop filled at moomoo
            dealt, avg, _ = self.broker.order_fill(trade["stop_id"]) if trade.get("stop_id") else (0, 0.0, "")
            px = avg if dealt else trade["stop"]
            reason = "stop"
        self._book(ticker, reason, px)
        return f"exit {ticker} ({reason}) at {px:.2f}: {trade['r']:+.2f}R"

    def _book(self, ticker, reason, px):
        t = self.trades[ticker]
        t.update(status="closed", exit=px, why=reason, r=round((px - t["entry"]) / t["risk"], 3),
                 pnl=round((px - t["entry"]) * t["qty"], 2), closed=self.now().isoformat(timespec="seconds"))
        log.info("closed %s (%s) at %.2f: %+.2fR, $%+.2f - today %+.2fR", ticker, reason, px, t["r"], t["pnl"], self.day_r())
        self._save()

    # ---- the bridge's own clock: stops filled at moomoo, targets, 3:55 PM
    def tick(self):
        with self.lock:
            self._roll()
            now = self.now()
            flatten = now.hour * 60 + now.minute >= hhmm(self.cfg["flatten_at"])
            for ticker, t in list(self.open_trades().items()):
                code = "US." + ticker
                try:
                    if flatten:
                        log.info("%s: closing %s before the bell", self.cfg["flatten_at"], ticker)
                        self._close(ticker, "end of day", 0.0)
                        continue
                    if self.broker.position(code) == 0:
                        self._close(ticker, "stop", t["stop"])
                        continue
                    last = self.broker.last_price(code) if self.cfg["watch_targets"] else None
                    if last is not None and last >= t["target"]:
                        self._close(ticker, "target", last)
                    elif last is not None and t.get("stop_id") is None and last <= t["stop"]:
                        self._close(ticker, "stop", last)
                except OrderError as e:
                    log.error("watch %s: %s", ticker, e)

    def watch_forever(self):
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("watcher error")
            time.sleep(self.cfg["watch_every_sec"])


# ------------------------------------------------------------------ web server
class Handler(BaseHTTPRequestHandler):
    bridge = None
    secret = ""

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/health":
            self._reply(200, json.dumps(self.bridge.summary()))
        elif path in ("/pause/" + self.secret, "/resume/" + self.secret):
            self.bridge.paused = path.startswith("/pause")
            log.warning("bridge %s from %s", "PAUSED" if self.bridge.paused else "resumed", self.client_address[0])
            self._reply(200, "paused: no new entries (exits still work)" if self.bridge.paused else "resumed")
        else:
            self._reply(404, "not found")

    def do_POST(self):
        if self.path.rstrip("/") != "/hook/" + self.secret:
            log.warning("rejected a request to an unknown path from %s", self.client_address[0])
            self._reply(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 10_000:
            self._reply(413, "bad size")
            return
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            msg = json.loads(body)
        except ValueError:
            log.info("ignored a plain-text alert (not an order): %.120s", body)
            self._reply(200, "ignored: not an order")
            return
        self._reply(200, "accepted")                          # TradingView gives up after a few seconds
        threading.Thread(target=self._process, args=(msg,), daemon=True).start()

    def _process(self, msg):
        log.info("alert: %s", json.dumps(msg))
        try:
            log.info("done: %s", self.bridge.handle(msg))
        except OrderError as e:
            log.error("refused: %s", e)
        except Exception:
            log.exception("unexpected error")

    def _reply(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.debug("http: " + fmt, *args)


def make_server(cfg, bridge):
    Handler.bridge, Handler.secret = bridge, cfg["secret"]
    return ThreadingHTTPServer((cfg["listen_host"], cfg["listen_port"]), Handler)


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else HERE / "config.json")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "bridge.log")])
    broker = DryRunBroker() if cfg["mode"] == "dry_run" else MoomooBroker(cfg)
    bridge = Bridge(cfg, broker, state_path=HERE / f"state_{cfg['mode']}.json")
    threading.Thread(target=bridge.watch_forever, daemon=True).start()
    server = make_server(cfg, bridge)
    log.info("AMD bridge in %s mode on %s:%d - trading grades %s", cfg["mode"].upper(), cfg["listen_host"],
             cfg["listen_port"], ",".join(cfg["grades"]))
    log.info("webhook URL: https://<your tunnel address>/hook/<your secret>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
