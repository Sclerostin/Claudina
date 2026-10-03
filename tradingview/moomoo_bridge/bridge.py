"""DT Core -> moomoo bridge.

Receives TradingView webhook alerts from the DT Core strategy and places the
orders in your moomoo account through moomoo OpenAPI (the OpenD gateway).

  buy   market buy, then a protective stop-loss order at moomoo
  sell  market short sale, then a protective buy-to-cover stop
  exit  cancel this ticker's open orders and close the position at market

DT Core handles the target: when TradingView's simulation reaches the target,
the stop or the end of the day, it sends "exit" and the bridge closes the trade.
The stop order at moomoo is a safety net in case an alert is late or lost.

Modes (set "mode" in config.json):
  dry_run  log what would be sent, touch nothing (start here)
  paper    moomoo paper trading account
  live     real money

Run:  python bridge.py            reads config.json next to this file
"""
import json
import logging
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("bridge")

DEFAULTS = {
    "secret": "",
    "mode": "dry_run",
    "trade_password": "",
    "opend_host": "127.0.0.1",
    "opend_port": 11111,
    "listen_host": "127.0.0.1",
    "listen_port": 8080,
    "max_qty": 1000,
    "max_order_value": 25000,
    "allowed_tickers": [],
    "allow_short": True,
}
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class OrderError(Exception):
    """An order was refused by the bridge's checks or by moomoo."""


def load_config(path):
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(Path(path).read_text()))
    if len(cfg["secret"]) < 16 or cfg["secret"].startswith("CHANGE"):
        raise SystemExit("config.json: set 'secret' to a random string of 16+ letters and digits")
    if not cfg["secret"].isalnum():
        raise SystemExit("config.json: 'secret' may only contain letters and digits")
    if cfg["mode"] not in ("dry_run", "paper", "live"):
        raise SystemExit("config.json: 'mode' must be dry_run, paper or live")
    if cfg["mode"] == "live" and not cfg["trade_password"]:
        raise SystemExit("config.json: live mode needs 'trade_password' (your moomoo trading password)")
    cfg["allowed_tickers"] = [t.upper() for t in cfg["allowed_tickers"]]
    return cfg


class DryRunBroker:
    """Logs every order instead of sending it. Tracks positions so the log reads like a real day."""

    def __init__(self):
        self.positions = {}

    def position(self, code):
        return self.positions.get(code, 0)

    def market(self, code, side, qty, ref_price):
        log.info("DRY RUN  market %s %s x%d (ref %.2f)", side.upper(), code, qty, ref_price)
        sign = 1 if side in ("buy", "cover") else -1
        self.positions[code] = self.positions.get(code, 0) + sign * qty
        return qty

    def stop(self, code, side, qty, stop_price):
        log.info("DRY RUN  stop %s %s x%d at %.2f", side.upper(), code, qty, stop_price)

    def cancel_open(self, code):
        log.info("DRY RUN  cancel open orders for %s", code)


class MoomooBroker:
    """Talks to moomoo through the OpenD gateway running on this computer."""

    OPEN_STATUSES = ("UNSUBMITTED", "WAITING_SUBMIT", "SUBMITTING", "SUBMITTED", "FILLED_PART")
    DEAD_STATUSES = ("SUBMIT_FAILED", "TIMEOUT", "FAILED", "CANCELLED_ALL", "DISABLED", "DELETED", "FILL_CANCELLED")

    def __init__(self, cfg, mm=None, ctx=None):
        if mm is None:
            import moomoo as mm
        self.mm = mm
        self.env = mm.TrdEnv.REAL if cfg["mode"] == "live" else mm.TrdEnv.SIMULATE
        self.ctx = ctx or mm.OpenSecTradeContext(
            filter_trdmarket=mm.TrdMarket.US,
            host=cfg["opend_host"],
            port=cfg["opend_port"],
            security_firm=mm.SecurityFirm.FUTUINC,
        )
        if self.env == mm.TrdEnv.REAL:
            self._ok(self.ctx.unlock_trade(password=cfg["trade_password"]), "unlock trading")

    def _ok(self, result, what):
        ret, data = result
        if ret != self.mm.RET_OK:
            raise OrderError(f"{what} failed: {data}")
        return data

    def position(self, code):
        """Signed share count: positive long, negative short, 0 flat."""
        data = self._ok(self.ctx.position_list_query(code=code, trd_env=self.env), "position query")
        total = 0.0
        for _, row in data.iterrows():
            qty = abs(float(row["qty"]))
            total += -qty if str(row.get("position_side", "")) == "SHORT" else qty
        return total

    def market(self, code, side, qty, ref_price):
        sides = {"buy": self.mm.TrdSide.BUY, "sell": self.mm.TrdSide.SELL,
                 "short": self.mm.TrdSide.SELL_SHORT, "cover": self.mm.TrdSide.BUY_BACK}
        data = self._ok(self.ctx.place_order(price=ref_price, qty=qty, code=code, trd_side=sides[side],
                                             order_type=self.mm.OrderType.MARKET, trd_env=self.env),
                        f"market {side} {code}")
        order_id = str(data["order_id"].iloc[0])
        log.info("sent market %s %s x%d (order %s)", side.upper(), code, qty, order_id)
        return self._wait_fill(order_id)

    def _wait_fill(self, order_id, timeout=15.0):
        deadline = time.time() + timeout
        dealt = 0.0
        while time.time() < deadline:
            data = self._ok(self.ctx.order_list_query(order_id=order_id, trd_env=self.env), "order status")
            if len(data):
                row = data.iloc[0]
                status = str(row["order_status"])
                dealt = float(row["dealt_qty"])
                if status == "FILLED_ALL":
                    log.info("order %s filled x%g at %s", order_id, dealt, row.get("dealt_avg_price"))
                    return dealt
                if status in self.DEAD_STATUSES:
                    raise OrderError(f"order {order_id} ended as {status}")
            time.sleep(1.0)
        log.warning("order %s not fully filled after %.0fs (filled x%g)", order_id, timeout, dealt)
        return dealt

    def stop(self, code, side, qty, stop_price):
        trd_side = self.mm.TrdSide.SELL if side == "sell" else self.mm.TrdSide.BUY_BACK
        data = self._ok(self.ctx.place_order(price=stop_price, qty=qty, code=code, trd_side=trd_side,
                                             order_type=self.mm.OrderType.STOP, aux_price=stop_price,
                                             time_in_force=self.mm.TimeInForce.DAY, trd_env=self.env),
                        f"protective stop for {code}")
        log.info("protective stop %s %s x%d at %.2f (order %s)", side.upper(), code, qty, stop_price,
                 data["order_id"].iloc[0])

    def cancel_open(self, code):
        data = self._ok(self.ctx.order_list_query(code=code, trd_env=self.env), "order list")
        for _, row in data.iterrows():
            if str(row["order_status"]) in self.OPEN_STATUSES:
                self._ok(self.ctx.modify_order(self.mm.ModifyOrderOp.CANCEL, row["order_id"], 0, 0,
                                               trd_env=self.env), f"cancel order {row['order_id']}")
                log.info("cancelled order %s for %s", row["order_id"], code)


class Bridge:
    """Checks each DT Core message and turns it into broker orders, one at a time."""

    def __init__(self, cfg, broker):
        self.cfg = cfg
        self.broker = broker
        self.lock = threading.Lock()

    def handle(self, msg):
        if not isinstance(msg, dict):
            raise OrderError("message is not a JSON object")
        ticker = str(msg.get("ticker", "")).upper().strip()
        action = str(msg.get("action", "")).lower().strip()
        if not TICKER.match(ticker):
            raise OrderError(f"bad ticker {ticker!r}")
        if self.cfg["allowed_tickers"] and ticker not in self.cfg["allowed_tickers"]:
            raise OrderError(f"{ticker} is not in allowed_tickers")
        code = "US." + ticker
        with self.lock:
            if action == "exit":
                return self._exit(code, float(msg.get("price") or 0))
            if action in ("buy", "sell"):
                return self._enter(code, action, msg)
        raise OrderError(f"unknown action {action!r}")

    def _enter(self, code, action, msg):
        if action == "sell" and not self.cfg["allow_short"]:
            raise OrderError("short selling is off (allow_short is false)")
        try:
            qty = int(float(msg["quantity"]))
            price = float(msg.get("price") or 0)
            stop = float(msg["stopLoss"]["stopPrice"])
            target = float(msg["takeProfit"]["limitPrice"])
        except (KeyError, TypeError, ValueError) as e:
            raise OrderError(f"missing or bad field: {e}") from e
        if not 1 <= qty <= self.cfg["max_qty"]:
            raise OrderError(f"quantity {qty} outside 1..{self.cfg['max_qty']}")
        ref = price if price > 0 else max(stop, target)
        if qty * ref > self.cfg["max_order_value"]:
            raise OrderError(f"order value {qty * ref:,.0f} is over max_order_value {self.cfg['max_order_value']:,}")
        if price > 0:
            ordered = stop < price < target if action == "buy" else target < price < stop
            if not ordered:
                raise OrderError(f"prices out of order: stop {stop}, price {price}, target {target}")
        if self.broker.position(code) != 0:
            return f"skipped {action} {code}: already in a position"
        filled = int(self.broker.market(code, "buy" if action == "buy" else "short", qty, ref))
        if filled <= 0:
            raise OrderError(f"{action} {code} did not fill")
        try:
            self.broker.stop(code, "sell" if action == "buy" else "cover", filled, stop)
        except OrderError as e:
            log.warning("protective stop not placed (%s). DT Core's exit alert will still close the trade.", e)
        return f"entered {action.upper()} {code} x{filled}, stop {stop}, target {target}"

    def _exit(self, code, ref_price):
        self.broker.cancel_open(code)
        pos = self.broker.position(code)
        if pos > 0:
            self.broker.market(code, "sell", int(pos), ref_price)
        elif pos < 0:
            self.broker.market(code, "cover", int(-pos), ref_price)
        else:
            return f"exit {code}: already flat"
        return f"exit {code}: closed {abs(int(pos))} shares"


class Handler(BaseHTTPRequestHandler):
    bridge = None
    secret = ""
    mode = ""

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, f"ok - DT Core bridge in {self.mode} mode")
        else:
            self._reply(404, "not found")

    def do_POST(self):
        if self.path.rstrip("/") != "/hook/" + self.secret:
            log.warning("rejected request to an unknown path from %s", self.client_address[0])
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
            log.error("not JSON (is the alert message set to {{strategy.order.alert_message}}?): %.200s", body)
            self._reply(400, "expected JSON")
            return
        # Answer TradingView right away; it gives up after a few seconds.
        self._reply(200, "accepted")
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


def make_server(cfg, broker):
    Handler.bridge = Bridge(cfg, broker)
    Handler.secret = cfg["secret"]
    Handler.mode = cfg["mode"]
    return ThreadingHTTPServer((cfg["listen_host"], cfg["listen_port"]), Handler)


def main():
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else HERE / "config.json")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "bridge.log")])
    broker = DryRunBroker() if cfg["mode"] == "dry_run" else MoomooBroker(cfg)
    server = make_server(cfg, broker)
    log.info("DT Core bridge in %s mode on %s:%d", cfg["mode"].upper(), cfg["listen_host"], cfg["listen_port"])
    log.info("TradingView webhook URL: https://<your tunnel address>/hook/<your secret>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
