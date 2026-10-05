# Edge Reader kit

One method for every chart. On each 5-minute candle, Edge Reader checks 97 conditions from the trading
literature (51 on the daily chart, 46 on the 5-minute chart): trend, location, Wyckoff/ICT phase,
volume, momentum, the market and time of day. It adds up what each condition has been worth in past
data. The result is the expected R of a trade from that candle (stop 1.5 ATR, target 2R, out by
3:55 PM) after slippage. **BUY** when it clears your minimum, **SELL** when the trade ends, **WAIT**
otherwise.

| Folder / file | What it is |
|---|---|
| `pine/Edge_Reader.pine` | The strategy: expected R for long and short on every candle, the reasons behind it, signals, Measure mode, phone and webhook alerts |
| `pine/Edge_Radar.pine` | Ranks up to 15 stocks by today's expected R (daily part, after each stock's costs) |
| `pine/DT_Volume.pine` | Volume bars that light up at 1.5× average (optional) |
| `pine/DT_Momentum.pine` | Squeeze, 3/10 or RSI pane from the earlier kit (optional, not part of the method) |
| `moomoo_bridge/` | Webhook server that places the trades in moomoo through OpenAPI, with a stop at moomoo on every position |
| `research/METHODOLOGY.md` | How the method was built and tested, and what the evidence says. Start here |
| `research/refit.py` | Learns new 5-minute points from your Measure-mode Deep Backtesting exports, and writes them only if they hold up out of sample |
| `research/RESULTS.md` | The earlier AMD setup study (kept for reference; superseded) |
| `Edge_Kit.html` | The phone guide (built from `guide_template.html` by `build_guide.py`) |

**Status of the evidence:** the daily points (11 years of data) rank days correctly in every unseen
year, but the edge is smaller than the cost of a 2R day trade. No 5-minute weighting survived
out-of-sample testing on 7 months of data, so the 5-minute points start at zero. Expect WAIT most of
the time until Measure-mode results from years of TradingView data show otherwise. Educational tool,
not financial advice.
