"""Single entry point: data -> signals -> positions -> backtest -> results.

Usage:
    python run_backtest.py --config config.yaml

Prints the performance tables, writes them as CSV to ``output_dir`` and saves
the charts as PNG to ``figures_dir``.
"""

import argparse

import pandas as pd

from src import analysis, backtest, data, portfolio, signals
from src.config import REPO_ROOT, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    config = load_config(args.config)

    prices = {
        root: data.build_continuous(
            data.load_contract_prices(root, config["start_date"], config["end_date"]),
            config["roll"],
        )
        for root in config["universe"]
    }
    sig = signals.generate_signals(prices, config)
    positions = portfolio.size_positions(sig, prices, config)
    results = backtest.run_backtest(positions, prices, config)

    # Same sizing, costs and warm-up, but always long: isolates what the momentum
    # signal adds on top of volatility targeting.
    always_long = sig.where(sig.isna(), 1.0)
    long_results = backtest.run_backtest(portfolio.size_positions(always_long, prices, config),
                                         prices, config)

    capital = float(config["sizing"]["capital"])
    bench = config["metrics"]["benchmark"]
    returns = pd.DataFrame({
        "momentum": analysis.returns_on_capital(results, capital),
        "always_long": analysis.returns_on_capital(long_results, capital),
        f"{bench}_buy_and_hold": analysis.buy_and_hold_returns(prices[bench], results.index),
    }).reindex(results.index)

    tables = {
        "performance": analysis.performance_summary(returns, benchmark=returns[f"{bench}_buy_and_hold"]),
        "annual_returns": analysis.annual_returns(returns),
        "trading": pd.DataFrame({
            "momentum": analysis.trading_summary(results, prices, capital),
            "always_long": analysis.trading_summary(long_results, prices, capital),
        }).T,
    }
    output_dir = REPO_ROOT / config["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        print(f"\n{name}\n{table.astype(float).round(3).to_string()}")
        table.to_csv(output_dir / f"{name}.csv")

    labels = {"momentum": "Momentum", "always_long": "Always long",
              f"{bench}_buy_and_hold": f"{bench} buy-and-hold"}
    paths = analysis.make_figures(
        returns.rename(columns=labels),
        analysis.net_exposure(results, prices, capital),
        prices[bench]["adj_price"].rename(bench),
        REPO_ROOT / config["figures_dir"],
        target_vol=config["sizing"]["target_annual_vol"],
    )
    print(f"\nSaved {len(paths)} figures to {config['figures_dir']}/")


if __name__ == "__main__":
    main()
