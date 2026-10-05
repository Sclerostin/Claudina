# AMD Day Trader kit

A TradingView + moomoo day-trading kit built around one tested idea: on **market fear days**, buy
the morning shakeout. A stock builds its opening box (accumulation), dips under it to take the stops
(manipulation), then a strong candle closes back above it (distribution). Target 2R, out by 3:55 PM.

| Folder / file | What it is |
|---|---|
| `pine/AMD_Day_Trader.pine` | The strategy: signals, day grade (A/B/X), stop/target, status panel, phone and webhook alerts |
| `pine/AMD_Radar.pine` | One table showing where up to 12 stocks are in today's A → M → D, with one alert for all of them |
| `pine/DT_Volume.pine` | Volume bars that light up at 1.5× average (optional) |
| `pine/DT_Momentum.pine` | Squeeze, 3/10 or RSI pane from the earlier kit (optional, not part of the tested rules) |
| `moomoo_bridge/` | Webhook server that places the trades in moomoo through OpenAPI, with a stop at moomoo on every position |
| `research/` | The backtests behind every rule, including what failed. Start with `RESULTS.md` |
| `AMD_Kit.html` | The phone guide (built from `guide_template.html` by `build_guide.py`) |

**Status of the evidence:** grade A trades earned +0.49R (Feb–Jun 2026) and +0.45R (Jul–Oct 2026)
per trade on 47 stocks, but over only 16 qualifying days, and the grade rule was found after the
first holdout failed. Verify it with TradingView's Deep Backtesting on years of data and paper trade
before using real money. Educational tool, not financial advice.
