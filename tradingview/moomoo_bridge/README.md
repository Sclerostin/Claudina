# AMD Day Trader → moomoo bridge

Places AMD Day Trader's trades in your moomoo account automatically. TradingView sends each order to
this small program as a webhook, and the program places it through moomoo OpenAPI (the OpenD gateway).

| AMD Day Trader sends | The bridge does |
|---|---|
| `buy` (grade A by default) | Limit buy at the alert price + 0.15%. Whatever fills gets a **STOP order at moomoo** right away. Unfilled shares are cancelled, never chased. |
| `exit` (target, stop or 3:55 PM) | Cancels the stock's open orders and sells the position at market. |

The bridge also works **without** TradingView once a trade is open:

- **3:55 PM:** it sells every position it opened that day, by its own clock.
- **Stops:** the stop sits at moomoo, so it works even if this computer or TradingView goes down.
  When the stop fills, the bridge notices and books the loss.
- **Targets:** if your OpenD login has US quotes, the bridge sells the moment the price reaches the
  target. If it doesn't, the TradingView `exit` alert does it, a few seconds later.
- **Live mode:** if moomoo refuses the stop order, the bridge sells the shares at once rather than
  hold them unprotected.

## What you need

- A Mac, Windows or Linux computer that stays on and awake from 9:25 AM to 4:05 PM New York time,
  or a small cloud server (OpenD has a command-line version for Linux).
- A moomoo account with OpenAPI access.
- Your paid TradingView plan (webhooks).
- AMD Day Trader on the 5-minute chart of each stock you want traded, with an alert on each
  (or one watchlist alert, if your plan has them).

## Setup

1. **Install OpenD** from moomoo's OpenAPI download page. Log in with your moomoo ID and accept the
   API agreement on first login. Leave it running; it listens on port 11111.
2. **Install Python 3.9 or newer**, then run `pip install moomoo-api`.
   If that fails with `install_layout`, run
   `pip install "setuptools<58" wheel` and then `pip install --no-build-isolation moomoo-api`.
3. **Copy this folder** to the computer. Copy `config.example.json` to `config.json` and set
   `secret` to 16 or more random letters and digits. Leave `mode` as `dry_run`.
4. **Start the bridge:** `python bridge.py`. Opening http://127.0.0.1:8080/health in a browser should
   show `{"mode": "dry_run", ...}`.
5. **Give it an HTTPS address.** TradingView only sends webhooks to ports 80 and 443. With a free ngrok
   account and its free static domain: `ngrok http --url=YOUR-NAME.ngrok-free.app 8080`.
   Your webhook URL is then `https://YOUR-NAME.ngrok-free.app/hook/YOUR-SECRET`.
6. **Create the order alert** in TradingView on each stock's 5-minute chart with AMD Day Trader on it:
   - Condition: AMD Day Trader → **Order fills only**
   - Message: delete the text and enter exactly `{{strategy.order.alert_message}}`
   - Notifications: turn on **Webhook URL** and paste your URL
7. **Roll out in three stages** by changing `mode` in `config.json` and restarting the bridge:
   - `dry_run` for a few days. Every order is written to `bridge.log` and nothing is sent.
   - `paper` for at least four weeks. Orders go to your moomoo paper account.
   - `live` with `trade_password` filled in and a small risk per trade in AMD Day Trader's settings.

## Your limits (config.json)

| Setting | Default | What it does |
|---|---|---|
| `grades` | `["A"]` | Only grade A signals (market fear days) are traded. `["A", "B"]` adds normal days, which lost money in testing. |
| `max_open_positions` | 3 | Fear days trigger many stocks at once, and they move together. This caps how many you hold. |
| `max_trades_per_day` | 6 | No new entries after this many trades in a day. |
| `max_daily_loss_r` | 3.0 | No new entries once today's closed trades add up to −3R. |
| `max_qty` / `max_order_value` | 1000 / 25000 | Refuses any single order above these. |
| `allowed_tickers` | `[]` (any) | For example `["NVDA", "AMD"]` to trade only these. |
| `entry_cushion_pct` | 0.15 | How far above the alert price the limit buy may fill. |
| `entry_window` | 09:45–12:05 | Entries outside this New York time window are refused. |
| `flatten_at` | 15:55 | Time the bridge sells everything it opened. |
| `watch_targets` | true | Sell at the target from moomoo quotes when OpenD has them. |

It also refuses a second trade in the same stock on the same day, a stock you already hold, prices
that are out of order, and any request without your secret.

## Pause button

Open `https://YOUR-NAME.ngrok-free.app/pause/YOUR-SECRET` in your phone's browser to stop new entries.
Exits keep working. `/resume/YOUR-SECRET` turns entries back on. Creating an empty file named `PAUSE`
in this folder also blocks entries. To get out of everything at once, close the positions in the moomoo app.

## Things that can go wrong

- **The computer sleeps or the bridge stops.** New signals are missed. Open positions keep their
  moomoo stop, but the 3:55 PM sale won't happen. The stop is a day order, so a position left open
  overnight has **no** stop. Check moomoo before the close on any day you're unsure.
- **Your fill differs from TradingView's.** The strategy fills at the candle's close; your limit order
  goes out a second or two later. If price has already run more than 0.15%, the buy is skipped.
- **"protective stop not placed" in paper mode.** moomoo's paper account may not accept stop orders.
  The bridge then exits from quotes or from TradingView's exit alert.
- **"ignored a plain-text alert".** That alert's message isn't `{{strategy.order.alert_message}}`.
  Phone-notification alerts should not use the webhook.
- **Connection errors.** OpenD isn't running or isn't logged in.

## Tests

`python -m unittest test_bridge.py` runs 30 tests of the order rules, limits, watcher and webhook. With
`moomoo-api` installed, 6 of them also check every call against the real SDK's method signatures.

Educational tool, not financial advice. Automated trading can lose money quickly. Start in dry_run,
then paper, and keep position sizes small.
