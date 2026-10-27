"""Single entry point: data -> signals -> positions -> backtest -> results.

Usage:
    python run_backtest.py --config config.yaml                 # backtest and charts
    python run_backtest.py --download --robustness              # everything, from scratch

Prints the performance tables, writes them as CSV to ``output_dir`` and saves
the charts as PNG to ``figures_dir``.

``--download`` first fetches the raw data (needs DATABENTO_API_KEY; cached, so a
rerun does not download again). ``--robustness`` then also runs the issue #10
tests and writes ``robustness_*.csv`` to ``output_dir``. The 30-variant grid takes
a few minutes the first time and is cached in ``output_dir``.
"""

import argparse

import pandas as pd

from src import analysis, backtest, data, portfolio, robustness, signals
from src.config import REPO_ROOT, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--download", action="store_true",
                        help="download the raw data first (needs DATABENTO_API_KEY)")
    parser.add_argument("--robustness", action="store_true",
                        help="also run the out-of-sample, cost and regime tests (issue #10)")
    args = parser.parse_args()
    config = load_config(args.config)

    if args.download:
        data.download_raw(config)

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

    if args.robustness:
        grid_returns = robustness.load_or_run_grid(prices, config, output_dir / "grid_returns.parquet")
        for name, table in robustness.report_tables(prices, config, grid_returns).items():
            print(f"\n{name}\n{table.astype(float, errors='ignore').round(3).to_string()}")
            table.to_csv(output_dir / f"{name}.csv")


if __name__ == "__main__":
    main()
