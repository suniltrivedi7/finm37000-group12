# Equity Futures Trading Strategy

A systematic trading strategy for equity index futures (e.g., E-mini S&P 500, Nasdaq-100), built as the team project for our Futures and Other Derivatives course.

## Goal

Design, backtest, and evaluate a rules-based strategy that trades equity index futures. By the end of the project, a user should be able to:

1. Load historical futures price data
2. Generate trading signals from a defined strategy
3. Run a backtest that accounts for realistic frictions (transaction costs, slippage, roll costs, margin)
4. View performance and risk results (returns, Sharpe ratio, drawdown, exposure, turnover)

## Strategy Overview

*Aspirational; the specifics will be refined as the team's research progresses.*

- **Universe:** Liquid equity index futures contracts
- **Signal:** To be decided (candidates: momentum / trend-following, mean reversion, carry or basis-based signals)
- **Position sizing:** Volatility-targeted sizing with leverage and margin limits
- **Contract rolling:** Systematic roll rule between expiries
- **Evaluation:** Out-of-sample testing and comparison against a buy-and-hold benchmark

## How to Run

*Aspirational; this will be updated as the code is written.*

```bash
# 1. Clone the repository
git clone https://github.com/<tech-leader-username>/<repo-name>.git
cd <repo-name>

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the backtest
python run_backtest.py --config config.yaml

# 4. Results (metrics and plots) are written to ./results/
```

## Planned Repository Structure

```
.
├── README.md
├── requirements.txt
├── config.yaml          # strategy and backtest parameters
├── data/                # historical futures data (or download scripts)
├── src/
│   ├── data.py          # data loading and cleaning
│   ├── signals.py       # signal generation
│   ├── portfolio.py     # position sizing and risk limits
│   ├── backtest.py      # backtest engine
│   └── analysis.py      # performance metrics and plots
├── tests/
└── results/
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
