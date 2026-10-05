# AMD Day Trader research: what the tests showed

**Short version.** Most bullish intraday setups lost money once realistic costs were included,
including every AMD variant tested on ordinary days. One combination held up in both halves of the
data: the morning AMD pattern on **market fear days** (grade A), at **+0.49R** per trade in
development and **+0.45R** in the holdout. It rests on only 16 days, and it was found *after* the
original holdout test failed, so treat it as a strong lead to verify, not a proven edge.

## Data and method

- **5-minute candles**, regular session, **Feb 23 – Oct 2, 2026** (155 days), for 50 liquid US stocks
  and ETFs: 604,500 candles with no gaps, from the Robinhood market-data API. SPY, QQQ and IWM are
  used for market context only and are not traded, which leaves 47 stocks.
- **Daily candles, 2015 – Oct 2026**, the same 50 symbols (113,742 stock-days), for the day-bias study.
- **Simulator (`lab.py`)** works the way the Pine strategy does: signal on a finished candle, entry at
  its close, stop and target checked on later candles, and the stop counted first when one candle
  touches both. Each market fill (entry, stop, 3:55 PM exit) costs **$0.01 + 0.01% of price** in
  slippage. Targets are limit orders and fill only when price trades *through* them. All positions
  are closed on the 3:50 candle's close.
- **Development: Feb 23 – Jun 30. Holdout: Jul 1 – Oct 2.** Rules were frozen before the holdout ran.
- **`pine_parity.py`** re-runs the Pine script's logic line by line and confirms it produces exactly
  the research signals (713 of 713 with the 15-minute range, 431 of 431 with 30).

`run_all.py` reproduces every number below; its output is in `results.txt`.

## 1. What did not work

| Idea (development period) | Trades | Avg R | Verdict |
|---|---|---|---|
| Random entries, 1 ATR stop, 2R target (control) | 4,303 | −0.12 | The cost of the bracket plus slippage |
| VWAP −2 sd spring | 3,434 | −0.07 | Losing |
| VWAP −2 sd spring, relative volume ≥ 1.5 | 299 | +0.04 | Not significant |
| 5-minute swing AMD: any swing low swept, then a structure shift | 1,672 | −0.06 | Losing |
| Afternoon AMD on the lunch range (4 variants) | 109–641 | −0.03 to −0.27 | Losing |
| Re-entries after the first trade (5-min AMD, retest of the range, VWAP pullback) | 47–72 | −0.18 to −0.21 | Losing |
| Time stops (12 or 24 candles) on the morning AMD | 475 | +0.00 to +0.09 | Worse than holding |

A **daily trend filter** (above the 20- or 50-day average) and a **relative-volume filter** both made the
morning AMD worse, not better.

## 2. The morning AMD (session "Power of Three") and its holdout

The one family that was positive in development was the **session-level** AMD:

- **Accumulation:** the opening range.
- **Manipulation:** before 11:00, a dip below the opening-range low or yesterday's low.
- **Distribution:** before 12:00, a strong green candle closes back above the opening-range high.

Development results by trigger line (15-minute range): reclaim of the swept level −0.00R; range
middle +0.06R; VWAP +0.03R; last swing high +0.07R; **range high +0.08R** (+0.14R with the market
filter and a stop capped at 2 ATR). Every neighbouring setting stayed positive (+0.07 to +0.28R).

**The frozen rules then failed the holdout:**

| Frozen rules | Development | Holdout (Jul 1 – Oct 2) |
|---|---|---|
| 30-minute range (the pre-registered choice) | 291 trades, +0.22R, 49.5% won | 143 trades, **−0.22R**, 32.2% won |
| 15-minute range | 475 trades, +0.14R | 252 trades, **−0.09R** |

The month-by-month view shows why. Most of the development profit came from **April 2026**
(+0.89R a trade during a V-shaped rally). In August and September only a third of SPY's sessions
closed above their open, even though SPY rose overall: its gains came **overnight**, the
well-documented split between overnight and intraday returns (Cooper, Cliff & Gulen 2008;
Lou, Polk & Skouras 2019). Random long entries lost money in the same months.

| Month | AMD 30-min | AMD 15-min | Random | SPY up-days |
|---|---|---|---|---|
| Mar | +0.09 | +0.03 | −0.22 | 36% |
| Apr | +0.89 | +0.73 | +0.03 | 71% |
| May | +0.05 | −0.00 | −0.07 | 60% |
| Jun | −0.15 | −0.16 | −0.16 | 48% |
| Jul | −0.12 | +0.02 | −0.07 | 64% |
| Aug | −0.31 | −0.01 | −0.07 | 33% |
| Sep | −0.23 | −0.27 | −0.18 | 33% |

Two later ideas also failed both halves: buying stocks that held their range while SPY swept its lows
(+0.05 to +0.07R, then −0.10 to −0.12R), and trading only while the setup's last 10 days were
positive (+0.28R, then −0.36R).

## 3. The day bias: 11 years of daily data

Average move from the open to the close, in daily ATRs, by what was known at the open
(2015–22 | 2023–Feb 2026 | the 2026 intraday sample):

| Condition at the open | 2015–22 | 2023–26 | 2026 |
|---|---|---|---|
| All days | +0.008 | +0.019 | +0.020 |
| **SPY gaps up more than 1 daily ATR** | **−0.113** | **−0.229** | **−0.071** |
| SPY gaps down more than 1 daily ATR | +0.065 | +0.075 | +0.071 |
| SPY RSI(2) at or below 10 (oversold) | +0.043 | +0.044 | +0.184 |
| Mondays | +0.031 | +0.101 | +0.092 |

Stocks drift **down** from the open after the market gaps up hard, and **up** after it gaps down
hard or is short-term oversold: buy fear, not euphoria. This matches Connors' RSI(2) research and the
Wyckoff selling-climax → automatic-rally sequence. The effects are small on their own (a few
hundredths of a daily ATR) but consistent across 11 years.

## 4. What ships: grade A = morning AMD on a market fear day

Combining the two: **grade A** when SPY's RSI(2) closed at 10 or lower yesterday or SPY opens at least
1 daily ATR lower; **grade X** (no longs) when SPY opens at least 1 daily ATR higher; **grade B**
otherwise. Of 155 days: 16 A, 135 B, 4 X.

| Morning AMD, 15-minute range | Development | Holdout |
|---|---|---|
| **Grade A** | **60 trades, +0.49R, 58% won** | **32 trades, +0.45R, 59% won** |
| Grade B | 406 trades, +0.09R | 215 trades, **−0.15R** |
| Grade X | 9 trades, +0.12R | 5 trades, −0.69R |
| Random entries on grade A days (control) | 526 trades, −0.04R | 222 trades, −0.02R |

With the 30-minute range, grade A was +0.56R and +0.16R.

Grade A details (15-minute range, all 92 trades):

- 39 different stocks, 27 of them positive. The two best (SMCI, AFRM) made 32% of the total R.
- 10 of 16 days were positive. Without the two best days: +0.31R a trade.
- Exits: 32 at the 2R target, 28 at the stop, 32 at 3:55 PM. Average hold: 3 hours 15 minutes.
- Biggest drawdown: 4.7R. Longest losing streak: 5 trades.
- Chance the true average is zero or below, resampling whole days: 0.6%.

### Read this before trading it

- **Small sample.** 16 days, and half the trades came from March 2026's sell-off. A few fear
  episodes drive the result.
- **Found after the holdout.** The grade rule uses round thresholds from the daily study (RSI(2) ≤ 10
  is Connors' standard; 1 ATR), not values tuned on these trades. It was still chosen after the
  original holdout had failed, so it hasn't had a truly fresh test.
- **Rare.** About two fear days a month, and several stocks trigger at once on those days. They move
  together, which is why the bridge caps open positions.
- **Grade B is not an edge.** It's shown on the chart so you can paper-test it, nothing more.
- **Costs matter.** Random entries lose 0.12R a trade with this bracket. Slippage on volatile names
  and late fills can be larger than modelled.
- **Most day traders lose money.** Barber, Lee, Liu and Odean (2014): fewer than 1% of Taiwanese day
  traders were predictably profitable. Chague, De-Losso and Giovannetti (2019): 97% of Brazilian
  day traders who persisted for 300 days lost money.

### Next test: yours, on years of data

The grade rule can be checked on far more history than this study had. With TradingView's
**Deep Backtesting**, run AMD Day Trader on 10–20 of your stocks from 2018 onward and add up the
grade A results across stocks. If grade A stays clearly positive across years and stocks, paper trade
it for at least a month before risking money. The guide page walks through this tap by tap.

## Reproduce

1. Save `get_equity_historicals` results (Robinhood market-data API) for these 50 symbols:
   AAPL ABNB ADBE AFRM AMD AMZN ANET APP ARM AVGO BA COIN CRM CRWD CVNA DELL DIS DKNG GOOGL HOOD INTC
   IWM JPM LLY MARA META MSFT MSTR MU NFLX NKE NVDA ORCL PLTR PYPL QQQ RBLX RIVN ROKU SHOP SMCI SNOW
   SOFI SPY TSLA UBER UNH UPST WMT XOM. You need 5-minute candles from Feb 23 to Oct 2, 2026
   (10 symbols per call, at most about two months per call) and daily candles from Jan 2015 to Oct 2026.
2. ```
   python load_bars.py "<folder>/*.txt" bars5.pkl 5minute
   python load_bars.py "<folder>/*.txt" daily_long.pkl day      # also use as daily.pkl
   python run_all.py bars5.pkl daily_long.pkl daily_long.pkl > results.txt
   python pine_parity.py bars5.pkl daily_long.pkl daily_long.pkl
   ```
