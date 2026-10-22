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
values are initial choices and will be tested in the robustness work (#10).

- **Universe:** Core: ES, NQ, RTY and YM daily futures. Extension (#10): a few international index futures (e.g., FESX, NKD) as a robustness check
- **Signal:** Time-series momentum. Each contract is long when its trailing return is positive and short when negative, using only data known at each date
- **Position sizing:** Volatility-targeted sizing with leverage and margin limits
- **Contract rolling:** Roll 5 trading days before expiry, with ratio-adjusted continuous prices (#4; see `data/README.md`)
- **Evaluation:** Out-of-sample testing and comparison against a volatility-matched buy-and-hold benchmark

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

*The figures step (#9) is still a placeholder, so `run_backtest.py` ends with a `NotImplementedError` after printing the tables.*

```bash
# Download raw daily data for every contract in the universe (needs DATABENTO_API_KEY).
# Cached per product and year, so re-running does not re-download; --force refreshes.
python -m src.data --config config.yaml
# Writes data/raw/<ROOT>_daily.parquet; columns are described in data/README.md

python run_backtest.py --config config.yaml
# Prints the performance, annual-return and trading tables and saves them as CSV in results/

# notebooks/05_eda.ipynb also reads data/raw, so run the download first.
```

## Repository Structure

```
.
├── README.md
├── CONTRIBUTING.md     # branch / PR workflow
├── requirements.txt
├── pyproject.toml      # makes src/ an installable package
├── config.yaml         # strategy and backtest parameters
├── run_backtest.py     # single entry point
├── .env.example        # template for API keys (copy to .env)
├── data/
│   ├── raw/            # <ROOT>_daily.parquet per product (git-ignored)
│   └── processed/      # derived data (git-ignored); continuous series are built in memory
├── notebooks/          # exploration and EDA
├── src/
│   ├── contracts.py    # contract specs: multiplier, tick size, symbols, margin
│   ├── config.py       # loads config.yaml
│   ├── data.py         # data loading and continuous contracts   (#3, #4)
│   ├── signals.py      # signal generation                       (#6)
│   ├── portfolio.py    # position sizing and risk limits         (#6)
│   ├── backtest.py     # backtest engine                         (#7)
│   └── analysis.py     # performance metrics and plots           (#8, #9)
├── tests/
├── reports/figures/    # figures for the final report
└── results/            # backtest output (git-ignored)
```

## Team

| Name | Role |
|------|------|
| Sunil Trivedi | Tech leader |
| Ayush Bhupal | Communication leader |
| Jon Nie | Design leader |

## Roadmap

Tasks are tracked in this repository's [Issues](../../issues). Each Issue is a piece of work that contributes to the goal above.

## Disclaimer

This is an academic project. Nothing here is investment advice, and backtested results do not guarantee future performance.
