# DT Core 4 research: what the backtests showed

**Data:** 5-minute regular-session candles from the Robinhood market-data API, June 1 to October 2, 2026.
That's 87 trading days × 20 liquid US stocks and ETFs (SPY, QQQ, NVDA, TSLA, AMD, AAPL, META, AMZN, MSFT,
PLTR, GOOGL, AVGO, NFLX, COIN, MU, UBER, JPM, XOM, HOOD, SMCI) and 135,720 candles, with no gaps.
The raw data is not stored in this repository.

**Method (`backtest.py`)** mirrors how the Pine strategy runs:
- Signals are taken on a finished candle and filled at its close.
- Stops and targets are checked on later candles. When one candle touches both, it counts as a stop.
- Market fills (entries, stops, time exits) pay $0.02 per share of slippage.
- Every position is closed by 3:55 PM.
- June to August (63 days) was used to choose rules. September 1 to October 2 (24 days) was held out
  to check them.

## Results

| Round | Setup | In-sample avg R | Out-of-sample avg R | Verdict |
|---|---|---|---|---|
| 1 | Opening range breakout, DT Core 3 rules | −0.17 | −0.20 | Losing |
| 1 | Pullback to 20 EMA in trend | −0.11 | −0.11 | Losing |
| 1 | VWAP reclaim | −0.21 | −0.07 | Losing |
| 2 | Sweep of any support and reclaim (20 stocks) | −0.08 | −0.09 | Losing |
| 2 | ORB with Crabel narrow-range filter | −0.40 | +0.10 (12 trades) | Too few trades |
| 3 | 5-minute ORB on stocks in play (Zarattini, Barbon & Aziz 2024) | −0.15 | −0.11 | Not a fair test: 5-minute candles are too coarse for its tight stop, and the universe isn't market-wide stocks in play |
| 3 | Noise-area momentum, SPY / QQQ (Zarattini, Aziz & Barbon 2024) | mixed | mixed | QQQ long-only slightly positive; too few days |
| 4 | Exits: half at 1R + breakeven, time stop, 9 EMA trail | — | — | None beat a fixed 2R target |
| 4 | ORB on stocks in play (RVOL ≥ 1.5) | +0.61 (30 trades) | −0.05 (14 trades) | Failed out-of-sample; optional, off by default |
| **4** | **VWAP spring on stocks in play (RVOL ≥ 1.5)** | **+0.18 (68 trades)** | **+0.22 (33 trades)** | **Chosen as DT Core 4's main setup** |

## The chosen setup

The VWAP spring, on a stock whose volume since the open is at least 1.5× normal for that time of day:

- **Trigger:** a candle trades below VWAP − 2 standard deviations and closes back above that band.
- **Candle:** it must be green and close in the top 40% of its range.
- **Entry:** at that candle's close.
- **Stop:** the candle's low minus 0.1 ATR. The risk must be between 0.3 and 2.5 ATR.
- **Target:** 2R.
- **Daily limits:** up to 3 trades per stock per day, stopping after −2R. Entries between 9:45 AM and 3:00 PM.

| | Normal (RVOL ≥ 1.5) | Strict (RVOL ≥ 2.0) |
|---|---|---|
| Trades (87 days, 20 stocks) | 101 (1.2 per day) | 39 (0.45 per day) |
| Win rate | 42.6% | 51.3% |
| Average | +0.19R | +0.46R |
| In-sample / out-of-sample | +0.18R / +0.22R | +0.62R / +0.10R |
| Max drawdown | 8.4R | 4.9R |
| Longest losing streak | 8 | 4 |
| At 0.5% risk per trade | +9.7%, worst drawdown 4.2% | +8.9%, worst drawdown 2.5% |

Exits were 37 targets, 57 stops and 7 end-of-day closes. The average hold was about one hour.

**Robustness (`robustness.py`):**
- 8 of 9 neighbouring settings (bands 1.5–2.5 standard deviations × RVOL 1.2–2.0) were positive,
  and the average rises steadily with RVOL.
- 4 of 5 months were positive; August was −3.4R.

## Read this before trading it

- **The edge is small and not proven.** The mean is +0.19R with a standard error of 0.14 (t = 1.34).
  A bootstrap still gives about an 8% chance that the true average is zero or negative. Four months
  is a short history.
- **It leans on its best stocks.** The two best symbols account for about three quarters of the
  total R; without them the total falls from +19.5R to +4.9R.
- **Real trading costs more than the test.** Slippage on volatile names, missed fills and alert
  delays aren't fully modelled.
- **Most day traders lose money.** Barber, Lee, Liu and Odean (2014) found fewer than 1% of day
  traders in Taiwan were predictably profitable. Use paper trading, then small size.
- **The phase monitor wasn't tested.** It labels accumulation, manipulation, expansion and
  distribution; it is not part of the entry rules.

## Reproduce

```
python load_data.py "<saved get_equity_historicals JSON files>" bars20.pkl
python backtest.py bars20.pkl            # writes bars20_prep.pkl
python experiments.py bars20_prep.pkl    # round 1 (also experiments2.py, experiments3.py, experiments4.py)
python robustness.py bars20_prep.pkl trades.csv
python final_config.py bars20_prep.pkl
```
