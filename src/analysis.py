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

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MultipleLocator, PercentFormatter

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


# Charts (issue #9). Colors are a colorblind-checked categorical order, assigned by
# column position so a series keeps its color in every chart.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
DIVERGING = ["#b8322f", "#e34948", "#f0efec", "#2a78d6", "#1c5cab"]
STYLE = {
    "font.family": "sans-serif", "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size": 10, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "axes.edgecolor": AXIS, "axes.labelcolor": INK_2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.axisbelow": True, "axes.spines.top": False,
    "axes.spines.right": False, "axes.titlesize": 12, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "axes.titlelocation": "left", "legend.frameon": False,
    "lines.linewidth": 2,
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _percent_axis(axis, decimals: int = 0) -> None:
    axis.set_major_formatter(PercentFormatter(1.0, decimals=decimals))


def _shade(ax, mask: pd.Series) -> None:
    """Shade each run of True in ``mask``, from its first date up to the next date."""
    run = mask.ne(mask.shift()).cumsum()
    dates = mask.index
    for _, days in mask[mask].groupby(run[mask]):
        end = dates.get_loc(days.index[-1]) + 1
        ax.axvspan(days.index[0], dates[min(end, len(dates) - 1)], color=GRID, alpha=0.9, linewidth=0)


def _legend_above(ax, ncol: int, x: float = 0.0) -> None:
    ax.legend(loc="lower left", bbox_to_anchor=(x, 1.0), ncol=ncol)


def _cumulative(returns: pd.DataFrame, colors: dict) -> Figure:
    fig = Figure(figsize=(9, 4.5))
    ax = fig.subplots()
    cum = returns.cumsum()
    for name in returns:
        ax.plot(cum.index, cum[name], color=colors[name], label=name)
    _percent_axis(ax.yaxis)
    ax.set_xlim(cum.index[0], cum.index[-1])
    ax.set_title("Cumulative return, share of the fixed capital")
    ax.legend(loc="upper left")
    # Direct labels at the right edge, nudged apart so they don't overlap.
    lo, hi = ax.get_ylim()
    last = None
    for value, name in sorted(((cum[n].dropna().iloc[-1], n) for n in cum), reverse=True):
        y = value if last is None else min(value, last - 0.06 * (hi - lo))
        last = y
        ax.annotate(f"{name}  {value:.0%}", (cum.index[-1], y), xytext=(6, 0),
                    textcoords="offset points", va="center", color=INK_2, fontsize=9)
    return fig


def _drawdowns(returns: pd.DataFrame, colors: dict) -> Figure:
    fig = Figure(figsize=(9, 3.5))
    ax = fig.subplots()
    dd = drawdown(returns)
    for name in returns:
        ax.plot(dd.index, dd[name], color=colors[name], label=name, linewidth=1.5)
    _percent_axis(ax.yaxis)
    ax.set_xlim(dd.index[0], dd.index[-1])
    ax.set_title("Drawdown from the running peak, share of capital")
    ax.legend(loc="lower left")
    return fig


def _annual(returns: pd.DataFrame, colors: dict) -> Figure:
    yearly = annual_returns(returns)
    fig = Figure(figsize=(10, 4.2))
    ax = fig.subplots()
    x = np.arange(len(yearly))
    width = 0.8 / len(yearly.columns)
    for i, name in enumerate(yearly):
        offset = (i - (len(yearly.columns) - 1) / 2) * width
        ax.bar(x + offset, yearly[name], width=width - 0.03, color=colors[name], label=name,
               edgecolor=SURFACE, linewidth=1)
    first = returns.dropna(how="all").index[0]
    partial = first.month != 1 or first.day > 7
    ticks = [f"{year}*" if partial and i == 0 else str(year) for i, year in enumerate(yearly.index)]
    ax.set_xticks(x, ticks)
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.grid(axis="x", visible=False)
    _percent_axis(ax.yaxis)
    ax.set_title("Calendar-year return" + (" (*partial year)" if partial else ""), pad=28)
    _legend_above(ax, ncol=len(yearly.columns))
    return fig


def _exposure(exposure: pd.DataFrame, price: pd.Series, color: str, strategy: str) -> Figure:
    net = exposure.sum(axis=1)
    gross = exposure.abs().sum(axis=1)
    price = price.loc[net.index[0]:net.index[-1]]
    fig = Figure(figsize=(9, 5.5))
    top, bottom = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [1, 1.2]})
    top.plot(price.index, price, color=INK_2, linewidth=1.5)
    top.set_title(f"{price.name} (ratio-adjusted level), shaded where {strategy.lower()} is net short")
    for ax in (top, bottom):
        _shade(ax, net < 0)
    bottom.plot(gross.index, gross, color=MUTED, linewidth=0.8, label="Gross (sum of |notional|)", zorder=2)
    bottom.plot(net.index, net, color=color, linewidth=1.4, label="Net (long minus short)", zorder=3)
    bottom.axhline(0, color=AXIS, linewidth=1)
    bottom.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}x".replace("-", "\u2212")))
    bottom.set_xlim(net.index[0], net.index[-1])
    bottom.set_title(f"{strategy} exposure, notional / capital")
    _legend_above(bottom, ncol=2, x=0.45)
    fig.tight_layout()
    return fig


def _rolling(returns: pd.DataFrame, colors: dict, target_vol: float | None) -> Figure:
    window = returns.rolling(TRADING_DAYS)
    sharpe = window.mean() / window.std() * np.sqrt(TRADING_DAYS)
    vol = window.std() * np.sqrt(TRADING_DAYS)
    fig = Figure(figsize=(9, 5.5))
    top, bottom = fig.subplots(2, 1, sharex=True)
    for name in returns:
        top.plot(sharpe.index, sharpe[name], color=colors[name], label=name, linewidth=1.5)
        bottom.plot(vol.index, vol[name], color=colors[name], linewidth=1.5)
    top.axhline(0, color=AXIS, linewidth=1)
    top.set_title("Rolling 1-year Sharpe ratio", pad=22)
    _legend_above(top, ncol=len(returns.columns), x=0.3)
    if target_vol is not None:
        bottom.axhline(target_vol, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
        bottom.annotate(f"{target_vol:.0%} target", (returns.index[0], target_vol), xytext=(4, 4),
                        textcoords="offset points", color=INK_2, fontsize=9)
    _percent_axis(bottom.yaxis)
    bottom.set_xlim(returns.index[0], returns.index[-1])
    bottom.set_title("Rolling 1-year volatility")
    fig.tight_layout()
    return fig


def _monthly_heatmap(returns: pd.Series) -> Figure:
    monthly = returns.dropna().resample("ME").sum()
    table = (pd.DataFrame({"year": monthly.index.year, "month": monthly.index.month, "r": monthly.values})
             .pivot(index="year", columns="month", values="r").reindex(columns=range(1, 13)))
    values = table.to_numpy()
    limit = np.nanmax(np.abs(values))
    fig = Figure(figsize=(9, 0.32 * len(table) + 0.9))
    ax = fig.subplots()
    image = ax.imshow(values, cmap=LinearSegmentedColormap.from_list("diverging", DIVERGING),
                      norm=TwoSlopeNorm(0, -limit, limit), aspect="auto")
    for (i, j), v in np.ndenumerate(values):
        if not np.isnan(v):
            ax.text(j, i, f"{abs(v) * 100:.1f}" if round(v * 100, 1) == 0 else f"{v * 100:.1f}",
                    ha="center", va="center", fontsize=7.5,
                    color="white" if abs(v) > 0.6 * limit else INK)
    ax.set_xticks(range(12), MONTHS)
    ax.set_yticks(range(len(table)), table.index)
    ax.grid(False)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"{returns.name} monthly return, % of capital")
    bar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02, format=PercentFormatter(1.0, 0))
    bar.outline.set_visible(False)
    return fig


def _distribution(returns: pd.DataFrame, colors: dict) -> Figure:
    monthly = returns.resample("ME").sum(min_count=1)
    edge = max(0.01, np.ceil(monthly.abs().max().max() * 100) / 100)
    bins = np.arange(-edge, edge + 0.005, 0.01)
    fig = Figure(figsize=(9, 3.8))
    ax = fig.subplots()
    for name in monthly:
        m = monthly[name].dropna()
        ax.hist(m, bins=bins, histtype="step", linewidth=2, color=colors[name],
                label=f"{name} (mean {m.mean():.1%}, sd {m.std():.1%})")
    ax.axvline(0, color=AXIS, linewidth=1)
    ax.xaxis.set_major_locator(MultipleLocator(0.02))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{round(v * 100):.0f}%".replace("-", "\u2212")))
    ax.set_ylabel("Months")
    ax.set_title("Distribution of monthly returns")
    ax.legend(loc="upper left")
    return fig


def make_figures(returns: pd.DataFrame, exposure: pd.DataFrame, price: pd.Series,
                 output_dir: str | Path, target_vol: float | None = None) -> list[Path]:
    """Save the report figures as PNG files in ``output_dir`` and return their paths.

    ``returns`` holds daily fixed-capital returns, one column per series, named
    as they should appear in the charts. The first column is the strategy, and
    the last is the buy-and-hold benchmark. The benchmark runs at its own
    volatility, so it only appears in the cumulative and calendar-year charts.
    ``exposure`` is the strategy's ``net_exposure`` and ``price`` the benchmark's
    level (e.g. ``adj_price``), named after the product.
    """
    if not 2 <= len(returns.columns) <= len(SERIES_COLORS):
        raise ValueError(f"returns needs 2 to {len(SERIES_COLORS)} columns (strategy first, "
                         f"benchmark last), got {len(returns.columns)}")
    colors = dict(zip(returns.columns, SERIES_COLORS))
    strategy, matched = returns.columns[0], returns.iloc[:, :-1]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with matplotlib.rc_context(STYLE):
        figures = {
            "cumulative_return": _cumulative(returns, colors),
            "drawdown": _drawdowns(matched, colors),
            "annual_returns": _annual(returns, colors),
            "exposure": _exposure(exposure, price, colors[strategy], strategy),
            "rolling_sharpe_and_vol": _rolling(matched, colors, target_vol),
            "monthly_returns": _monthly_heatmap(returns[strategy]),
            "return_distribution": _distribution(matched, colors),
        }
        paths = []
        for name, fig in figures.items():
            path = output_dir / f"{name}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            paths.append(path)
    return paths
