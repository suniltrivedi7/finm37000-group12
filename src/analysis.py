"""Performance metrics (issue #8) and charts (issue #9).

Return convention
-----------------
Positions are sized on the fixed ``sizing.capital`` and never compound
(``src/portfolio.py``), so a day's return here is ``net_pnl / capital``. The
backtest's ``ret`` divides by the current equity instead, which understates the
risk taken once equity has grown (full-sample vol 8.4% on ``ret`` against 10.9%
on capital). Every series compared here is on the same fixed-capital basis:

* annualized return is the mean daily return times 252 (arithmetic, not CAGR);
* wealth is ``1 + cumsum(returns)``, so drawdowns are a share of capital;
* a buy-and-hold benchmark holds 1x capital in one product, resized daily so it
  does not compound either.

Futures returns are already in excess of cash and the backtest pays no interest,
so Sharpe and Sortino use no risk-free rate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import get_spec
from .portfolio import settle_panel
from .signals import TRADING_DAYS


def returns_on_capital(results: pd.DataFrame, capital: float) -> pd.Series:
    """Daily net P&L of a ``backtest.run_backtest`` result as a share of the fixed capital."""
    return results["net_pnl"] / capital


def buy_and_hold_returns(prices: pd.DataFrame, index: pd.Index) -> pd.Series:
    """Daily return of holding 1x capital in one continuous series, on ``index``.

    ``prices`` is one product's ``data.build_continuous`` output. Dates the
    product has no row (its holidays) earn 0, since the next row's ``ret``
    spans the gap; dates before its first return stay NaN.
    """
    ret = prices["ret"]
    started = index >= ret.first_valid_index()
    return ret.reindex(index).fillna(0.0).where(started)


def drawdown(returns: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """Distance below the running peak of ``1 + cumsum(returns)``, as a share of capital (<= 0)."""
    wealth = 1 + returns.cumsum()
    return wealth - wealth.cummax().clip(lower=1.0)


def longest_drawdown_years(returns: pd.Series) -> float:
    """Longest time spent below a previous peak, in years, counting one still open at the end."""
    dd = drawdown(returns)
    dates = dd.index.to_series()
    last_peak = dates.where(dd >= 0).ffill().fillna(dates.iloc[0])
    return (dates - last_peak).max().days / 365.25


def _metrics(r: pd.Series) -> dict:
    ann_return = r.mean() * TRADING_DAYS
    ann_vol = r.std() * np.sqrt(TRADING_DAYS)
    downside = np.sqrt((r.clip(upper=0) ** 2).mean() * TRADING_DAYS)
    max_dd = drawdown(r).min()
    monthly = r.resample("ME").sum()
    return {
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": ann_return / ann_vol if ann_vol > 0 else np.nan,
        "sortino": ann_return / downside if downside > 0 else np.nan,
        "max_drawdown": max_dd,
        "longest_drawdown_years": longest_drawdown_years(r),
        "calmar": ann_return / -max_dd if max_dd < 0 else np.nan,
        "monthly_hit_rate": (monthly > 0).mean(),
    }


def performance_summary(returns: pd.Series | pd.DataFrame,
                        benchmark: pd.Series | None = None) -> pd.DataFrame:
    """Return, volatility, Sharpe, Sortino, drawdown, Calmar and hit-rate statistics.

    ``returns`` holds daily fixed-capital returns, one column per strategy; each
    column is measured from its own first value. With a ``benchmark`` series the
    table adds the daily correlation with it. One row per strategy.
    """
    returns = returns.to_frame() if isinstance(returns, pd.Series) else returns
    rows = {}
    for name, col in returns.items():
        r = col.dropna()
        row = _metrics(r)
        if benchmark is not None:
            row["corr_benchmark"] = r.corr(benchmark.reindex(r.index))
        rows[name] = row
    return pd.DataFrame(rows).T


def annual_returns(returns: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """Calendar-year returns: the year's P&L as a share of capital (the sum of daily returns)."""
    return returns.groupby(returns.index.year).sum(min_count=1).rename_axis("year")


def net_exposure(results: pd.DataFrame, prices: dict[str, pd.DataFrame], capital: float) -> pd.DataFrame:
    """Signed notional per product as a share of capital, after each date's trades."""
    held = results.filter(like="pos_").rename(columns=lambda c: c.removeprefix("pos_"))
    settles = settle_panel({root: prices[root] for root in held.columns}, results.index)
    multipliers = pd.Series({root: get_spec(root).multiplier for root in held.columns})
    return held * settles * multipliers / capital


def holding_period_days(held: pd.Series) -> float:
    """Average length, in trading days, of a run with an unchanged long or short direction.

    Resizing within a direction does not end a run; a flip or going flat does.
    """
    direction = np.sign(held)
    run = direction.ne(direction.shift()).cumsum()
    lengths = direction.groupby(run).agg(["first", "size"])
    return lengths.loc[lengths["first"] != 0, "size"].mean()


def trading_summary(results: pd.DataFrame, prices: dict[str, pd.DataFrame], capital: float) -> pd.Series:
    """Turnover, costs, leverage, time short and holding period of one backtest result."""
    years = len(results) / TRADING_DAYS
    exposure = net_exposure(results, prices, capital)
    net = exposure.sum(axis=1)
    held = results.filter(like="pos_")
    return pd.Series({
        "contracts_per_year": results["contracts_traded"].sum() / years,
        "costs_per_year": results["costs"].sum() / capital / years,
        "costs_share_of_gross_pnl": results["costs"].sum() / results["gross_pnl"].sum(),
        "avg_gross_leverage": (results["gross_notional"] / capital).mean(),
        "avg_net_exposure": net.mean(),
        "days_net_short": (net < 0).mean(),
        "days_any_short": (held < 0).any(axis=1).mean(),
        "avg_holding_days": np.mean([holding_period_days(held[c]) for c in held.columns]),
    })


def make_figures(results: pd.DataFrame, output_dir: str) -> None:
    """Save the report figures to ``output_dir``."""
    raise NotImplementedError("Issue #9: generate charts and visualizations")
