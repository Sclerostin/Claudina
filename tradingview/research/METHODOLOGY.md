# One method for every chart: does reading chart conditions dictate BUY / SELL?

**Question.** Not "which setup works" but: can a single method read the conditions on any chart (trend,
location, Wyckoff/ICT phase, volume, momentum, market, time) and dictate BUY, SELL or WAIT, with an
expected value that holds up on charts and months it has never seen?

**Short answer.** The method can be built and it measures well, but in this data the chart
conditions don't carry enough information to pay for a 2R day trade.

- On the **5-minute chart** (7 months, 47 stocks), learned weights reversed from month to month, and the
  textbook weights worked in some months and reversed in others. The switch could not be read in advance.
- On the **daily chart** (11 years), the method ranked days correctly in every test, but the best tenth of
  days earned only about **+0.05R before costs** on a 2R intraday trade, against about **0.09R of costs**.

## The method

1. **Conditions.** 46 yes/no facts on every 5-minute candle (`conditions.py`) and 51 on every daily
   candle (`daily_conditions.py`), each one a thing a trader can see, taken from the literature:
   - trend: daily, intraday, market (Dow, Elder)
   - location: VWAP bands, premium/discount, opening range, prior-day levels (Dalton, Shannon, ICT)
   - phase: compression, spring, upthrust, break of structure (Wyckoff, ICT)
   - candle strength
   - volume: relative volume, climax, buyer/seller effort (Wyckoff, Aziz)
   - momentum: RSI(2) (Connors), relative strength (Bellafiore)
   - market day and time of day (Crabel)
2. **Outcome.** For every candle, the R a trade from its close actually earned, long and short: stop 1.5 ATR,
   target 2R, closed by 3:55 PM, slippage $0.01 + 0.01% per fill. On the daily chart, the day from the
   open, with R = half a daily ATR.
3. **Points table.** A ridge regression turns the conditions into an expected R. It is the same thing as a
   points system: each condition adds or subtracts a fixed number of R.
4. **Out-of-sample only.** The table is refit on the past and scored on the next month (5-minute) or the
   next year (daily). The test is **calibration**: when it predicts +0.1R, does that happen?
5. **Textbook baseline.** The same conditions with +1/−1 points straight from the books, nothing fitted.

`method_study.py` reproduces every number below; its output is in `method_results.txt`.
What ships from it is described at the end.

## Findings

### 1. 5-minute conditions: what worked last month reversed the next month

| Points table, scored on the next month | Predicted vs realized |
|---|---|
| All conditions, raw R | **−0.64** (long), **−0.65** (short): inverted |
| Within-day conditions only, raw R | **−0.79**, **−0.94**: inverted |
| Within-day conditions, R relative to that day's average | +0.97, +0.92, but raw R barely moves (best decile −0.03R) |
| Stock vs other stocks at the same moment | −0.86, +0.26, spread of ±0.02R: no information |

The "relative to the day" result looks strong but can't be traded: the day's average is only known after
the close, and the main driver ("SPY above VWAP is worse for longs") reflects the shape of the day's path.

### 2. Textbook points worked in trending months and reversed in mean-reverting ones

The textbook reading (trend + pullback/value + trigger + volume) didn't give consistently better results
at higher scores. From July to October the highest-scoring longs and shorts did worse than the lowest:
breakouts failed and stretched moves reverted. Averaged over all 155 days, high-score candles did
0.065R *worse* than low-score candles.

### 3. The regime can't be read in advance here

The textbook reading's daily payoff has no day-to-day memory (autocorrelation −0.12 to +0.10). Its
trailing 5-, 10- or 20-day results don't predict the next day (correlation −0.00 to +0.03), and neither
does SPY's variance ratio (+0.03).

### 4. Daily conditions over 11 years: correct ordering, small size

| Daily points table, scored on the next year (2017–2026) | Calibration | Worst decile | Best decile | Best decile positive |
|---|---|---|---|---|
| Long, open to close | +0.85 | −0.048R | +0.034R | 7 of 10 years |
| Long, 2R bracket from the open | +0.96 | −0.084R | −0.004R | 4 of 10 years |
| Short, open to close | +0.98 | −0.133R | −0.029R | 3 of 10 years |

The ranking generalizes to unseen years, in both directions. Shorts lose in most years (stocks drift
up over time).

### 5. Both timeframes together, 2026

The daily reading, fitted on 2015–2025 only, applied to 2026's 5-minute candles:

| Daily reading | Feb–Jun, after costs | Jul–Oct, after costs | Feb–Jun, before costs | Jul–Oct, before costs |
|---|---|---|---|---|
| Top 10% of stock-days | −0.030R | −0.046R | **+0.060R** | **+0.043R** |
| Top half | −0.094R | −0.098R | −0.009R | −0.002R |
| Bottom half | −0.084R | −0.100R | +0.007R | +0.004R |

The daily reading picks better days in both halves (about +0.05R), but slippage on a 1.5-ATR,
5-minute bracket averages 0.093R a trade (median 0.074R), which is more than the edge.

## What would change the answer

- **More intraday history.** Seven months can't separate trending from mean-reverting periods.
  TradingView's Deep Backtesting has years of 5-minute data, which would settle findings 1–3.
- **Lower cost per R.** The edge is about 0.05R. Costs per R fall with a wider stop or a longer hold
  (one daily-ATR-based trade a day), or with better fills on the most liquid stocks.
- **Different questions for the method.** Stocks in play from a full-market scan, and short horizons
  with hedging, weren't testable with this data.

## What ships: Edge Reader

`pine/Edge_Reader.pine` implements the method on any 5-minute chart:

    expected R = A + K x day score + (5-minute points of the conditions that are true) - slippage in R
    day score  = D0 + (daily points of the daily conditions that are true)

- **Daily points:** fitted on 2015–2025 only, so 2026 stays unseen by them.
- **A and K:** fitted on 2026's 5-minute trades. These are the only two numbers that have seen 2026.
- **5-minute points:** start at **zero**, because no 5-minute weighting held up out of sample.
- **Slippage:** computed on every candle from your settings ($0.01 + 0.01% per fill by default) and
  the trade's risk.

With these starting points, expected R on 2026's candles ranged from about −0.32R to +0.01R for longs
(at most +0.05R for shorts). So at the default minimum of +0.05R the engine signals almost never.
That is the honest reading of this evidence.

**The learning loop.** In Measure mode, the strategy takes up to two trades a day at set times
whatever the conditions, and writes each trade's risk, day score and 46 condition bits into the
trade's signal name. `refit.py` reads the List of Trades CSV exports from TradingView's Deep
Backtesting and fits new points. It walk-forward tests them by year, and writes them into both
Pine scripts (through `points.json` and `points.py`) only if the out-of-sample deciles line up
(correlation ≥ 0.5) and the best tenth beats the average by at least 2 standard errors.

The loop was tested two ways:

| Test | Result |
|---|---|
| A planted +0.5R effect in synthetic trades | found and adopted |
| Pure noise, 5 runs | rejected all 5 |
| Measure trades simulated from this study's 2026 data (14,570 per side, 47 stocks) | rejected: out-of-sample correlation −0.86 (longs) and −0.79 (shorts), with the best tenth 2.4 and 3.0 standard errors *below* average |

`test_refit.py` holds the first two tests. The third was a run of `refit.py --by month` on Measure-style
trades built from this study's 2026 candles (same set times, same condition bits).
