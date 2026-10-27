"""Out-of-sample and walk-forward testing (issue #10).

Every combination of the signal parameters in ``config.yaml`` (``robustness.grid``)
is backtested once over the full sample. That is valid for out-of-sample work
because signals, sizing and the backtest are causal, so a day's return does not
depend on later data. Choosing parameters then only looks at returns before a
cut-off, and judging them only at returns after it.

Returns are daily net P&L over the fixed ``sizing.capital`` (see ``src/analysis.py``).
A variant that needs a longer warm-up starts later and is NaN before that.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import analysis, backtest, portfolio, signals

TRADING_DAYS = signals.TRADING_DAYS


def variants(grid: dict[str, list]) -> list[dict]:
    """Every combination of the grid, as a list of ``{parameter: value}`` dicts."""
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def label(variant: dict) -> str:
    """Short readable name of a variant, e.g. ``lookback_days=252, skip_days=0``."""
    return ", ".join(f"{k}={v}" for k, v in variant.items())


def variant_config(config: dict, variant: dict) -> dict:
    """Copy of ``config`` with ``variant`` overriding the signal parameters."""
    config = copy.deepcopy(config)
    config["signal"].update(variant)
    return config


def variant_positions(prices: dict[str, pd.DataFrame], config: dict, variant: dict,
                      always_long: bool = False) -> pd.DataFrame:
    """Whole-contract target positions of a variant; with ``always_long`` every signal is +1.

    The always-long book keeps the variant's warm-up and sizing, so it isolates what the
    momentum signal adds on top of volatility targeting.
    """
    config = variant_config(config, variant)
    sig = signals.generate_signals(prices, config)
    if always_long:
        sig = sig.where(sig.isna(), 1.0)
    return portfolio.size_positions(sig, prices, config)


def run_variant(prices: dict[str, pd.DataFrame], config: dict, variant: dict) -> pd.Series:
    """Daily fixed-capital returns of the strategy with ``variant`` overriding ``config['signal']``."""
    config = variant_config(config, variant)
    positions = variant_positions(prices, config, {})
    results = backtest.run_backtest(positions, prices, config)
    return analysis.returns_on_capital(results, float(config["sizing"]["capital"]))


def run_grid(prices: dict[str, pd.DataFrame], config: dict, grid: dict[str, list]) -> pd.DataFrame:
    """Returns of every variant, one column each, columns named by ``label``."""
    columns = {label(v): run_variant(prices, config, v) for v in variants(grid)}
    return pd.DataFrame(columns).sort_index()


def sharpe(returns: pd.Series | pd.DataFrame):
    """Annualised Sharpe ratio of daily returns, ignoring NaN (no risk-free rate, as in ``analysis``).

    A float for a Series, a Series with one value per column for a DataFrame, and NaN
    where the volatility is zero.
    """
    frame = returns.to_frame() if isinstance(returns, pd.Series) else returns
    std = frame.std()
    out = frame.mean() / std.where(std > 0) * np.sqrt(TRADING_DAYS)
    return float(out.iloc[0]) if isinstance(returns, pd.Series) else out


def split(returns: pd.DataFrame | pd.Series, split_date: str):
    """Returns before ``split_date`` (in sample) and from it onward (out of sample)."""
    cut = pd.Timestamp(split_date)
    return returns[returns.index < cut], returns[returns.index >= cut]


def in_vs_out(grid_returns: pd.DataFrame, split_date: str) -> pd.DataFrame:
    """Sharpe of every variant in and out of sample, with the best in-sample variant flagged."""
    in_sample, out_sample = split(grid_returns, split_date)
    table = pd.DataFrame({"sharpe_in": sharpe(in_sample), "sharpe_out": sharpe(out_sample)})
    table["rank_in"] = table["sharpe_in"].rank(ascending=False)
    table["rank_out"] = table["sharpe_out"].rank(ascending=False)
    return table


def rank_correlation(table: pd.DataFrame) -> float:
    """Spearman correlation between in-sample and out-of-sample Sharpe across variants.

    Near 1 means in-sample performance predicts out-of-sample performance; near 0
    means picking the best in-sample variant is no better than picking at random.
    """
    valid = table[["sharpe_in", "sharpe_out"]].dropna()
    return float(stats.spearmanr(valid["sharpe_in"], valid["sharpe_out"])[0])


def walk_forward(grid_returns: pd.DataFrame, first_test_year: int, min_train_years: float,
                 last_year: int | None = None) -> tuple[pd.Series, pd.DataFrame]:
    """Each test year, use the variant with the best Sharpe over all earlier years.

    Training is expanding: everything before 1 January of the test year, and a year
    is skipped if fewer than ``min_train_years`` of history exist. Returns the
    stitched out-of-sample returns and one row per test year with the chosen
    variant and its training Sharpe.
    """
    last_year = last_year or grid_returns.index.max().year
    pieces, chosen = [], {}
    for year in range(first_test_year, last_year + 1):
        start = pd.Timestamp(year=year, month=1, day=1)
        train = grid_returns[grid_returns.index < start]
        years = (train.dropna(how="all").index.max() - train.dropna(how="all").index.min()).days / 365.25
        if years < min_train_years:
            continue
        scores = sharpe(train).dropna()
        best = scores.idxmax()
        test = grid_returns.loc[start:pd.Timestamp(year=year, month=12, day=31), best]
        pieces.append(test)
        chosen[year] = {"variant": best, "train_sharpe": scores[best],
                        "test_sharpe": sharpe(test)}
    return pd.concat(pieces).rename("walk_forward"), pd.DataFrame(chosen).T


def cache_key(config: dict) -> str:
    """Fingerprint of everything that changes a variant's returns, to detect a stale cache."""
    parts = {k: config.get(k) for k in ("universe", "start_date", "end_date", "roll", "costs",
                                        "margin", "sizing", "signal")}
    parts["grid"] = config["robustness"]["grid"]
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_or_run_grid(prices: dict[str, pd.DataFrame], config: dict, path: str | Path) -> pd.DataFrame:
    """``run_grid`` with the result cached at ``path`` (Parquet).

    The grid takes a few minutes. The cache is reused only if the config fingerprint
    stored beside it matches, so editing the grid, costs or signal settings reruns it.
    """
    path = Path(path)
    stamp = path.with_suffix(".key")
    key = cache_key(config)
    if path.exists() and stamp.exists() and stamp.read_text() == key:
        return pd.read_parquet(path)
    grid_returns = run_grid(prices, config, config["robustness"]["grid"])
    path.parent.mkdir(parents=True, exist_ok=True)
    grid_returns.to_parquet(path)
    stamp.write_text(key)
    return grid_returns


# --- parameter sensitivity (task 2) ----------------------------------------

def variant_params(columns) -> pd.DataFrame:
    """Parameter values of each variant, parsed back from its ``label`` (one row per variant)."""
    rows = {}
    for name in columns:
        pairs = (item.split("=") for item in name.split(", "))
        rows[name] = {key: ast.literal_eval(value) for key, value in pairs}
    return pd.DataFrame(rows).T


def sharpe_table(grid_returns: pd.DataFrame, row: str, col: str, fixed: dict | None = None,
                 start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Sharpe of the variants laid out by two parameters, holding the others at ``fixed`` values.

    ``row`` and ``col`` name the parameters on each axis. Every other parameter in the
    grid must be pinned in ``fixed`` so each cell is one variant. ``start`` and ``end``
    limit the dates used (inclusive).
    """
    params = variant_params(grid_returns.columns)
    fixed = fixed or {}
    free = set(params.columns) - {row, col} - set(fixed)
    if free:
        raise ValueError(f"Pin {sorted(free)} in `fixed`; each cell must be a single variant.")
    keep = np.logical_and.reduce([params[k] == v for k, v in fixed.items()]) if fixed else np.ones(len(params), bool)
    names = params.index[keep]
    scores = sharpe(grid_returns.loc[start:end, names])
    table = pd.DataFrame({"sharpe": scores, row: params.loc[names, row], col: params.loc[names, col]})
    return table.pivot(index=row, columns=col, values="sharpe")


# --- cost and margin sensitivity (task 3) ----------------------------------

def cost_variant(positions: pd.DataFrame, prices: dict[str, pd.DataFrame], config: dict,
                 commission: float | None = None, slippage_ticks: float | None = None,
                 free_rolls: bool = False) -> pd.Series:
    """Daily fixed-capital returns of the same positions under different trading costs.

    Positions do not depend on costs, so they are sized once and only the backtest is
    rerun. ``commission`` (USD per contract per fill) and ``slippage_ticks`` replace the
    config values when given. With ``free_rolls`` the second leg of every roll costs
    nothing and a roll only pays for the change in position, a lower bound on what
    calendar-spread execution would save.
    """
    config = copy.deepcopy(config)
    if commission is not None:
        config["costs"]["commission_per_contract"] = commission
    if slippage_ticks is not None:
        config["costs"]["slippage_ticks"] = slippage_ticks
    if free_rolls:
        prices = {root: df.assign(roll=False) for root, df in prices.items()}
    results = backtest.run_backtest(positions, prices, config)
    return analysis.returns_on_capital(results, float(config["sizing"]["capital"]))


def margin_usage_table(results: pd.DataFrame, base_pct: float, pcts: list[float]) -> pd.DataFrame:
    """Average and peak margin as a share of equity if margin were ``pcts`` of notional.

    Margin is reported by the backtest and does not change returns (no margin calls and
    no interest), so it scales in proportion to the percentage used.
    """
    usage = results["margin_usage"]
    return pd.DataFrame({pct: {"mean_usage": usage.mean() * pct / base_pct,
                               "peak_usage": usage.max() * pct / base_pct} for pct in pcts}).T


# --- market regimes (task 4) -----------------------------------------------

def episode_table(returns: pd.DataFrame, episodes: list[dict]) -> pd.DataFrame:
    """Return on capital and worst drawdown of each strategy inside named date ranges.

    ``episodes`` is a list of ``{"name", "start", "end"}`` dicts (inclusive dates). A
    strategy with no returns in an episode (it had not started yet) shows NaN.
    """
    rows = {}
    for episode in episodes:
        window = returns.loc[episode["start"]:episode["end"]]
        row = {}
        for name, col in window.items():
            col = col.dropna()
            row[("return", name)] = col.sum() if len(col) else np.nan
            row[("max_drawdown", name)] = analysis.drawdown(col).min() if len(col) else np.nan
        rows[episode["name"]] = row
    return pd.DataFrame(rows).T


def label_periods(index: pd.DatetimeIndex, periods: list[dict]) -> pd.Series:
    """Label each date with the period it falls in; ``periods`` are ``{"name", "start"}`` dicts."""
    starts = pd.DatetimeIndex([pd.Timestamp(p["start"]) for p in periods])
    position = starts.searchsorted(index, side="right") - 1
    return pd.Series([periods[i]["name"] if i >= 0 else np.nan for i in position], index=index, name="period")


def vol_regimes(market_returns: pd.Series, window: int, names: list[str]) -> pd.Series:
    """Low, medium or high market volatility, from the volatility known the day before.

    The thresholds are the quantiles of the whole sample, so this describes how the
    strategy did in each kind of market, not a rule that could have been traded.
    """
    vol = market_returns.rolling(window, min_periods=window).std().shift(1)
    return pd.qcut(vol, len(names), labels=names).rename("regime")


def regime_summary(returns: pd.DataFrame, labels: pd.Series) -> pd.DataFrame:
    """Days, annualised return and volatility and Sharpe of each strategy in each regime."""
    rows = {}
    for regime, group in returns.groupby(labels, observed=True):
        for name, col in group.items():
            col = col.dropna()
            rows[(regime, name)] = {
                "days": len(col), "ann_return": col.mean() * TRADING_DAYS,
                "ann_vol": col.std() * np.sqrt(TRADING_DAYS), "sharpe": sharpe(col)}
    return pd.DataFrame(rows).T


# --- tables for the entry point --------------------------------------------

def report_tables(prices: dict[str, pd.DataFrame], config: dict, grid_returns: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The headline robustness tables of ``notebooks/10_robustness.ipynb``, keyed by file name.

    ``split``: Sharpe in and out of sample for the default, the best in-sample variant and
    the median variant. ``walk_forward``: the variant chosen each test year. ``slippage``:
    full-sample Sharpe against slippage. ``episodes``: return inside each stress episode.
    The default variant is the ``signal`` block of ``config`` and must be on the grid.
    """
    rc = config["robustness"]
    default_variant = {k: config["signal"][k] for k in rc["grid"]}
    default = label(default_variant)
    table = in_vs_out(grid_returns, rc["split_date"])
    best = table["sharpe_in"].idxmax()
    best_variant = variant_params([best]).iloc[0].to_dict()

    split_table = pd.DataFrame({
        "in_sample": [table.loc[default, "sharpe_in"], table.loc[best, "sharpe_in"], table["sharpe_in"].median()],
        "out_of_sample": [table.loc[default, "sharpe_out"], table.loc[best, "sharpe_out"], table["sharpe_out"].median()],
    }, index=["default", f"best in sample ({best})", "median of all variants"])
    split_table["change"] = split_table["out_of_sample"] - split_table["in_sample"]

    wf = rc["walk_forward"]
    _, chosen = walk_forward(grid_returns, wf["first_test_year"], wf["min_train_years"])

    books = {
        "default": variant_positions(prices, config, default_variant),
        "best in sample": variant_positions(prices, config, best_variant),
        "always long (vol-targeted)": variant_positions(prices, config, default_variant, always_long=True),
    }
    ticks = rc["costs"]["slippage_ticks"]
    slippage = pd.DataFrame(
        {name: [sharpe(cost_variant(book, prices, config, slippage_ticks=t)) for t in ticks]
         for name, book in books.items()},
        index=pd.Index(ticks, name="slippage_ticks_per_fill"))

    stress = pd.DataFrame({name: cost_variant(book, prices, config) for name, book in books.items()})
    stress["ES buy-and-hold"] = analysis.buy_and_hold_returns(prices[config["metrics"]["benchmark"]], stress.index)
    episodes = episode_table(stress, rc["episodes"])
    episodes.columns = [f"{kind}: {name}" for kind, name in episodes.columns]

    return {"robustness_split": split_table, "robustness_walk_forward": chosen,
            "robustness_slippage": slippage, "robustness_episodes": episodes}
