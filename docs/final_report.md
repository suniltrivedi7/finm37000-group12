# Final report: time-series momentum on equity index futures

Group 12 (Sunil Trivedi, Ayush Bhupal, Jon Nie, Brandon Feng). FINM 37000, Futures and Other Derivatives.

## Summary

We tested whether a rules-based time-series momentum strategy, sized to a volatility target, earns
a positive return on E-mini S&P 500, Nasdaq-100, Russell 2000 and Dow futures after realistic costs.
The answer is a qualified no. The strategy makes money in the sample as a whole, but out of sample
it adds little beyond what the volatility targeting does by itself:

- The default strategy's Sharpe ratio falls from 0.59 before 2019 to 0.05 from 2019 on.
- A book that is always long, with the same volatility targeting, beat it in and out of sample
  (out of sample 0.59 against 0.05).
- Momentum did cut losses in sharp sell-offs (COVID crash: -7% against -18% for always-long and
  -39% for ES buy-and-hold) but gave the gains back in rebounds.
- Costs and margin are not the reason. Slippage would have to be about 20 ticks per fill to erase
  the default's return.

## 1. Hypothesis

Equity index futures trend: an index that has risen over the past year is more likely to keep
rising than to fall, and the reverse for a falling index (Moskowitz, Ooi and Pedersen, 2012).
A strategy that goes long after a positive year and short after a negative one, and sizes the
book to a constant volatility, should earn a positive risk-adjusted return. The team chose this
strategy from four candidates in [strategy_research.md](strategy_research.md) (the others were
mean reversion, carry and calendar spreads, and cross-sectional momentum).

## 2. Method

**Data.** Databento's CME Globex feed (GLBX.MDP3): every outright contract of ES, NQ, RTY and YM,
daily, from June 2010 to December 2025. Daily bars are built from hourly bars by CME trade date.
Marks and returns use the official settlement price. RTY only exists in the feed from mid-2017.
Details and known data issues are in [data/README.md](../data/README.md).

**Continuous contracts.** One contract is held at a time and rolled at the settle 5 trading days
before expiry, about when volume moves to the next contract. Daily returns are always same-contract
returns, so there is no artificial jump at a roll. The price series used by the signal is
ratio-adjusted (built by compounding those returns), so the signal only uses ratios of past
prices. Back-adjustment is implemented but not used. The choice of roll rule barely matters: ES
total return was 548.7%, 549.0% and 549.4% under the three rules we tested.

**Signal.** For each product separately: long if the trailing 252-day compounded return is
positive, short if negative, flat if exactly zero. The signal dated *t* uses settlements up to and
including *t* and sets the position held from the settle of *t* to the settle of *t+1*. Tests check
that changing a later price never changes an earlier signal. The 252-day window is the standard
twelve-month choice from the literature and was fixed before any backtest was run.

**Sizing.** Inverse-volatility weights scaled so the portfolio targets 10% annual volatility, using
a 60-day trailing covariance. Gross leverage is capped at 3x and any one product at 1.5x. Positions
are rounded to whole contracts on a fixed $10 million of capital.

**Execution and costs.**
- Trades fill at the settle of the signal date, with no price impact.
- Every contract bought or sold pays $2.50 commission plus 1 tick of slippage.
- A roll trades two legs (close the old contract, open the new one), so it pays costs twice.
- Margin is 8% of notional. It is reported, but margin calls and forced liquidation are not
  modelled, and cash earns no interest.
- Returns are daily net P&L over the fixed capital, with no risk-free rate.

**Evaluation.** Performance is compared with a volatility-matched ES buy-and-hold, and with an
*always-long* book that has the same sizing, costs and warm-up as the strategy but ignores the
signal. The always-long book isolates what the momentum signal adds on top of volatility targeting.

**Robustness (issue #10).** Thirty variants are backtested (lookback 63 to 378 days, skip 0 or 21
days, volatility window 20, 60 or 120 days; grid in `config.yaml`). We use a split test (choose on
data before 2019, judge on 2019 to 2025), a walk-forward test (each year from 2017, pick the variant
with the best Sharpe over all earlier years), parameter heatmaps, cost and margin sensitivity, and
performance in stress episodes, volatility regimes and rate eras. The full analysis, with charts, is
in [notebooks/10_robustness.ipynb](../notebooks/10_robustness.ipynb).

## 3. Results

All numbers are Sharpe ratios (annualized, no risk-free rate) unless stated.

**In sample against out of sample.**

| | In sample (to 2018) | Out of sample (2019 to 2025) |
|---|---|---|
| Default (252 / 0 / 60) | 0.59 | 0.05 |
| Best in sample (378-day lookback) | 0.90 | 0.47 |
| Median of the 30 variants | 0.54 | -0.02 |
| Always long, vol-targeted | 0.96 | 0.59 |
| ES buy-and-hold | 0.90 | 0.75 |

All 30 variants make money in sample, and only half do out of sample. The decay is across the whole
parameter family, so it is a property of the strategy and not of one bad parameter choice. The size
of the decay depends on the cut-off: across six cut-offs from 2016 to 2021 the default's
out-of-sample Sharpe ranges from 0.05 to 0.40, always positive but small.

**Which parameters matter.** The lookback does. Averaged over the other settings, out-of-sample
Sharpe is negative for lookbacks of 63, 126 and 189 days, about 0.08 at 252 and about 0.43 at 378.
The skip window and the volatility window make little difference. The surface is jagged
(neighbouring lookbacks differ by 0.2 to 0.3 in Sharpe), so only the broad pattern is reliable.

**Walk-forward.** Choosing parameters from earlier years gave a Sharpe of 0.52 from 2017 to 2025,
against 0.40 for the default and 0.30 for the median fixed variant. It picked the 378-day lookback
in every year, which is the edge of the grid, so this repeats the finding above and is not
independent confirmation. It still trails always-long (0.74) and its maximum drawdown is -26%
against -19%. Yearly results swing from a Sharpe of +3.2 in 2017 to -1.3 in 2022.

**Does momentum beat staying long?** No. Out of sample the default earned 0.6% a year with a
-20% maximum drawdown. Always-long with the same sizing earned 6.3% with -19%. The steady risk
control comes from volatility targeting, not from the signal.

**Market regimes.**

| Episode | Default | Always long | ES buy-and-hold |
|---|---|---|---|
| COVID crash (2020) | -7% | -18% | -39% |
| Q4 2018 sell-off | -4% | -16% | -22% |
| 2022 rate-hike bear market | -7% | -14% | -26% |
| 2025 tariff shock | -5% | -13% | -21% |
| China and oil sell-off (2015 to 2016) | -17% | -10% | -12% |
| COVID rebound (2020) | +0.1% | +12% | +48% |

Return on capital inside each episode. The default protects in fast sell-offs and loses in
rebounds and whipsaws. By volatility regime its Sharpe is 0.33 in calm markets, 0.85 in medium and
-0.09 in high volatility (always-long: 0.35, 1.11, 0.95). By era it was best in 2011 to 2017 (0.68),
weaker in 2018 to 2021 (0.15) and about zero in 2022 to 2025 (-0.02).

**Costs and margin.** The default's full-sample Sharpe falls from 0.36 with no slippage to 0.26 at
5 ticks per fill (five times our assumption) and reaches about zero only near 20 ticks. Commission
from $0 to $10 per fill moves it by about 0.03. Charging both roll legs changes it by about 0.006.
At 8% margin the strategy uses about 5% of equity on average and 20% at its peak (30% at 12%), far
from binding at $10 million of capital.

## 4. Limitations

- **The out-of-sample test is not blind.** The default was chosen from the literature before any
  backtest, but the team saw full-sample results in issue #8, and the 2019 cut-off is a judgement
  call.
- **The 378-day finding is not confirmed.** It is the longest lookback tried, so the best value may
  lie beyond the grid. Extending the grid would need a fresh hold-out period, because it is one
  more search over the same data.
- **Fills at the settle.** A signal computed from the settle trades at that same settle. A real
  order would go in near the close or at the next open. We have not tested next-day execution.
- **Short sample, one bull market.** The data starts in June 2010, so 2008 is not covered, and most
  of the sample is a rising market. RTY is in the book for under half of it.
- **Four correlated products.** The four indices move together, so the book is close to one bet and
  results are noisy.
- **Simplified frictions.** Slippage is a fixed one tick, not measured. There are no margin calls,
  no interest on cash, and no financing or tax effects.
- **No inference on significance.** We report Sharpe ratios without confidence intervals. With
  nine out-of-sample years, the differences between variants are within noise.

## 5. Next steps

1. Extend the lookback grid beyond 378 days and judge it on a period the team has not looked at.
2. Test next-day-settle execution, and measure slippage from intraday data.
3. Model margin calls and interest on cash.
4. Add international index futures (for example FESX and NKD) as a second sample.
5. Revisit the candidates we set aside, especially cross-sectional momentum and carry, now that the
   backtest and robustness tools exist.

## Reproducing the results

See the README. In short: `python run_backtest.py --download --robustness` downloads the data,
runs the backtest and writes tables to `results/` and charts to `reports/figures/`.
