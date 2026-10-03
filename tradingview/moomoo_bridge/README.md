# DT Core → moomoo bridge

Places DT Core's trades in your moomoo account automatically. TradingView sends each order to this
small program through a webhook, and it passes the order to moomoo through moomoo OpenAPI.
TradersPost and similar services don't support moomoo, which is why this bridge exists.

| DT Core sends | The bridge does |
|---|---|
| `buy` | Market buy, then a protective stop-loss order at moomoo |
| `sell` | Market short sale, then a protective buy-to-cover stop (DT Core 4 only sends buys) |
| `exit` (target, stop or 3:55 PM) | Cancels the ticker's open orders and closes the position at market |

The take-profit is handled by DT Core: when TradingView's simulation reaches the target, DT Core
sends `exit`. The stop order at moomoo is a safety net if an alert arrives late or not at all.

## What you need

- A Mac, Windows or Linux computer that stays on and awake from 9:25 AM to 4:05 PM New York time,
  or a small cloud server.
- A moomoo account. US stocks; short selling needs a margin account.
- Your paid TradingView plan, for webhook alerts.
- DT Core 4 on the 5-minute chart of each stock you want traded.

## Setup

1. **Install OpenD**, moomoo's OpenAPI gateway, from moomoo's OpenAPI download page. Log in with your
   moomoo ID and accept the API agreement on first login. Leave it running; it listens on port 11111.
2. **Install Python 3.9 or newer**, then run `pip install moomoo-api`.
3. **Get this folder** from the repository, copy `config.example.json` to `config.json`, and set
   `secret` to 16+ random letters and digits. Leave `mode` as `dry_run`.
4. **Start the bridge:** `python bridge.py`. Opening http://127.0.0.1:8080/health in a browser should
   show `ok - DT Core bridge in dry_run mode`.
5. **Give it an HTTPS address.** TradingView only sends webhooks to port 443. With a free ngrok account
   and its free static domain:
   `ngrok http --url=YOUR-NAME.ngrok-free.app 8080`
   Your webhook URL is then `https://YOUR-NAME.ngrok-free.app/hook/YOUR-SECRET`.
6. **Create the TradingView alert** on each stock's 5-minute chart with DT Core on it:
   - Condition: DT Core → **Order fills only**
   - Message: delete the default text and enter exactly `{{strategy.order.alert_message}}`
   - Notifications: turn on **Webhook URL** and paste your URL
7. **Roll out in three stages** by changing `mode` in `config.json` and restarting the bridge:
   - `dry_run` for a day or two. Every order is written to `bridge.log` and nothing is sent.
   - `paper` for one to two weeks. Orders go to your moomoo paper trading account.
   - `live` with `trade_password` filled in, and a small risk per trade in DT Core's settings.

## Safety limits (config.json)

| Setting | Default | Effect |
|---|---|---|
| `max_qty` | 1000 | Refuses orders above this many shares |
| `max_order_value` | 25000 | Refuses orders worth more than this in dollars |
| `allowed_tickers` | `[]` (any) | For example `["NVDA", "AAPL"]` to trade only these |
| `allow_short` | `true` | `false` refuses every SELL (short) signal |

The bridge also refuses a new entry while it already holds that ticker, prices that are out of order,
and any request that doesn't carry your secret. Keep `config.json` private: it holds your secret and,
in live mode, your trading password. `.gitignore` keeps it out of the repository.

## Things that can go wrong

- **The computer sleeps or the bridge stops.** Entries and exits are missed. The protective stop is a
  day order, so if the 3:55 PM exit is missed, the position stays open overnight without a stop.
  Check moomoo before the close on any day you're unsure.
- **Your fill price differs from TradingView's.** DT Core's simulated entry fills at the candle's close;
  your market order goes out a few seconds later.
- **"protective stop not placed" in paper mode.** moomoo's paper account may not accept stop orders.
  DT Core's exit alert still closes the trade.
- **"not JSON" in the log.** The alert's Message box doesn't contain exactly
  `{{strategy.order.alert_message}}`.
- **Connection errors.** OpenD isn't running or isn't logged in.

## Tests

`python -m unittest test_bridge.py` runs the order logic and webhook tests. With `moomoo-api`
installed, it also checks every call against the real SDK method signatures.

Educational tool, not financial advice. Automated trading can lose money quickly. Start in dry_run,
then paper, and keep position sizes small.
