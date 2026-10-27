# Equity Futures Trading Strategy

A systematic trading strategy for equity index futures (e.g., E-mini S&P 500, Nasdaq-100), built as the team project for our Futures and Other Derivatives course.

## Goal

Design, backtest, and evaluate a rules-based strategy that trades equity index futures. By the end of the project, a user should be able to:

1. Load historical futures price data
2. Generate trading signals from a defined strategy
3. Run a backtest that accounts for realistic frictions (transaction costs, slippage, roll costs, margin)
4. View performance and risk results (returns, Sharpe ratio, drawdown, exposure, turnover)

## Strategy Overview

The team chose **time-series momentum with volatility targeting** (issue #2; write-ups of
all candidates are in [docs/strategy_research.md](docs/strategy_research.md)). Parameter
values are the standard literature choices, fixed before backtesting, and were then
stress-tested in the robustness work (#10).

- **Universe:** ES, NQ, RTY and YM daily futures, June 2010 to December 2025 (RTY from mid-2017). International index futures (e.g., FESX, NKD) are left as a possible extension
- **Signal:** Time-series momentum. Each contract is long when its trailing 252-day return is positive and short when negative, using only data known at each date
- **Position sizing:** Inverse-volatility weights scaled to a 10% annual volatility target (60-day covariance), capped at 3x gross leverage and 1.5x per product, rounded to whole contracts on $10 million
- **Execution:** Trades fill at the settle of the signal date, paying $2.50 commission plus 1 tick of slippage per contract per fill; a roll pays for both legs. Margin is 8% of notional and is reported, not enforced
- **Contract rolling:** Roll 5 trading days before expiry, with ratio-adjusted continuous prices (#4; see `data/README.md`)
- **Evaluation:** Out-of-sample testing and comparison against a volatility-matched buy-and-hold benchmark and against an always-long book with the same sizing

## Results

Momentum did not beat simply staying long once volatility targeting is applied. The default's
Sharpe ratio falls from 0.59 before 2019 to 0.05 from 2019 on, and the decay affects the whole
parameter grid. Momentum cut losses in sharp sell-offs (COVID crash: -7% against -18% for
always-long) but gave them back in rebounds. A 378-day lookback held up best out of sample
(Sharpe 0.47) but is the edge of the grid and needs a fresh hold-out. Trading costs and margin are
not the reason: slippage would have to reach about 20 ticks per fill to erase the default's return.

The full write-up (hypothesis, method, results, limitations, next steps) is in
[docs/final_report.md](docs/final_report.md). The robustness analysis with charts is in
[notebooks/10_robustness.ipynb](notebooks/10_robustness.ipynb).

Mean reversion, carry / calendar spreads and cross-sectional momentum were considered and
set aside for now. They remain possible extensions once the core strategy works.

## Setup

Requires Python 3.10+.

```bash
# 1. Clone the repository
git clone https://github.com/suniltrivedi7/finm37000-group12.git
cd finm37000-group12

# 2. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                 # makes `import src` work from notebooks and scripts

# 3. Add your Databento API key (only needed to download data)
cp .env.example .env             # then edit .env; it is git-ignored

# 4. Check everything works
pytest
```

## How to Run

```bash
# Download raw daily data for every contract in the universe (needs DATABENTO_API_KEY).
# Cached per product and year, so re-running does not re-download; --force refreshes.
python -m src.data --config config.yaml
# Writes data/raw/<ROOT>_daily.parquet; columns are described in data/README.md

python run_backtest.py --config config.yaml
# Prints the performance, annual-return and trading tables and saves them as CSV in results/
# Saves the charts as PNG in reports/figures/

# Everything from scratch in one command: download, backtest, charts and robustness tests.
python run_backtest.py --download --robustness
# --download needs DATABENTO_API_KEY. --robustness adds the out-of-sample, walk-forward, cost
# and stress-episode tables (results/robustness_*.csv). The 30-variant grid takes a few
# minutes the first time and is cached in results/.

# The notebooks (05_eda, 10_robustness) also read data/raw, so run the download first.
```

## Repository Structure

```
.
├── README.md
├── CONTRIBUTING.md     # branch / PR workflow
├── requirements.txt
├── pyproject.toml      # makes src/ an installable package
├── config.yaml         # strategy and backtest parameters
├── run_backtest.py     # single entry point (--download, --robustness)
├── .env.example        # template for API keys (copy to .env)
├── data/
│   ├── raw/            # <ROOT>_daily.parquet per product (git-ignored)
│   └── processed/      # derived data (git-ignored); continuous series are built in memory
├── docs/
│   ├── strategy_research.md  # candidate strategies and the choice (#2)
│   └── final_report.md       # final write-up (#11)
├── notebooks/          # EDA (#5) and robustness analysis (#10)
├── src/
│   ├── contracts.py    # contract specs: multiplier, tick size, symbols, margin
│   ├── config.py       # loads config.yaml
│   ├── data.py         # data loading and continuous contracts   (#3, #4)
│   ├── signals.py      # signal generation                       (#6)
│   ├── portfolio.py    # position sizing and risk limits         (#6)
│   ├── backtest.py     # backtest engine                         (#7)
│   ├── analysis.py     # performance metrics and plots           (#8, #9)
│   └── robustness.py   # out-of-sample, cost and regime tests    (#10)
├── tests/
├── reports/figures/    # charts written by run_backtest.py
└── results/            # backtest output (git-ignored)
```

## Team

| Name | Role |
|------|------|
| Sunil Trivedi | Tech leader |
| Ayush Bhupal | Communication leader |
| Jon Nie | Design leader |
| Brandon Feng | Team member |

## Roadmap

Tasks are tracked in this repository's [Issues](../../issues). Each Issue is a piece of work that contributes to the goal above.

## Disclaimer

This is an academic project. Nothing here is investment advice, and backtested results do not guarantee future performance.
