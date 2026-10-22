"""Tests for issue #8: performance metrics.

Hand-computed cases on short fake return series and backtest results, no data
files. Returns are fixed-capital: wealth is 1 + cumsum(returns).
"""

import numpy as np
import pandas as pd
import pytest

from src.analysis import (annual_returns, buy_and_hold_returns, drawdown, holding_period_days,
                          longest_drawdown_years, make_figures, net_exposure, performance_summary,
                          returns_on_capital, trading_summary)


def series(values, start="2024-01-01", freq="B"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


def test_returns_on_capital_divides_by_the_fixed_capital_not_equity():
    res = pd.DataFrame({"net_pnl": [100_000.0, -50_000.0], "equity": [1.1e6, 1.05e6]})
    assert returns_on_capital(res, 1_000_000).tolist() == [0.1, -0.05]


def test_drawdown_is_a_share_of_capital_below_the_running_peak():
    dd = drawdown(series([0.1, -0.05, -0.05, 0.2, -0.1]))
    # wealth 1.10, 1.05, 1.00, 1.20, 1.10
    assert dd.tolist() == pytest.approx([0.0, -0.05, -0.10, 0.0, -0.10])


def test_a_first_day_loss_is_a_drawdown_from_the_starting_capital():
    assert drawdown(series([-0.02, 0.01])).tolist() == pytest.approx([-0.02, -0.01])


def test_longest_drawdown_counts_one_still_open_at_the_end():
    r = series([0.1, -0.05, 0.05, -0.01, 0.0, 0.0], freq="365D")    # one step about a year
    # peak on day 0, recovered on day 2; new peak on day 2, never recovered
    assert longest_drawdown_years(r) == pytest.approx(3 * 365 / 365.25)


def test_summary_metrics_match_hand_calculations():
    r = series([0.01, -0.02, 0.03, -0.01])
    row = performance_summary(r.rename("s")).loc["s"]
    mean, std = r.mean(), r.std()
    downside = np.sqrt((0.02 ** 2 + 0.01 ** 2) / 4)
    assert row["ann_return"] == pytest.approx(mean * 252)
    assert row["ann_vol"] == pytest.approx(std * np.sqrt(252))
    assert row["sharpe"] == pytest.approx(mean / std * np.sqrt(252))
    assert row["sortino"] == pytest.approx(mean / downside * np.sqrt(252))
    assert row["max_drawdown"] == pytest.approx(-0.02)
    assert row["calmar"] == pytest.approx(mean * 252 / 0.02)


def test_hit_rate_counts_positive_months():
    r = pd.Series([0.01, -0.03, 0.02, 0.01, -0.01],
                  index=pd.to_datetime(["2024-01-05", "2024-01-20", "2024-02-05", "2024-03-05", "2024-03-20"]))
    # January -0.02, February +0.02, March 0.00
    assert performance_summary(r.rename("s")).loc["s", "monthly_hit_rate"] == pytest.approx(1 / 3)


def test_each_strategy_is_measured_from_its_own_start_and_correlated_with_the_benchmark():
    bench = series([0.01, -0.01, 0.02, -0.02])
    late = series([np.nan, np.nan, 0.04, -0.04])
    table = performance_summary(pd.DataFrame({"bench": bench, "late": late}), benchmark=bench)
    assert table.loc["late", "ann_return"] == pytest.approx(0.0)
    assert table.loc["late", "corr_benchmark"] == pytest.approx(1.0)
    assert table.loc["bench", "corr_benchmark"] == pytest.approx(1.0)


def test_flat_returns_give_nan_ratios_not_errors():
    row = performance_summary(series([0.0, 0.0, 0.0]).rename("s")).loc["s"]
    assert np.isnan(row["sharpe"]) and np.isnan(row["sortino"]) and np.isnan(row["calmar"])


def test_annual_returns_sum_the_year_on_fixed_capital():
    r = pd.Series([0.1, 0.1, -0.05], index=pd.to_datetime(["2023-06-01", "2023-12-29", "2024-01-02"]))
    assert annual_returns(r).to_dict() == pytest.approx({2023: 0.2, 2024: -0.05})


def test_buy_and_hold_earns_zero_on_its_holidays_and_nothing_before_it_starts():
    idx = pd.bdate_range("2024-01-01", periods=5)
    prices = pd.DataFrame({"ret": [np.nan, 0.01, 0.02]}, index=idx[[1, 2, 4]])
    r = buy_and_hold_returns(prices, idx)
    assert r.iloc[:2].isna().all()                      # before the first return
    assert r.iloc[2:].tolist() == [0.01, 0.0, 0.02]     # holiday on day 3 earns 0


def test_holding_period_ignores_resizing_and_flat_spells():
    held = pd.Series([0, 3, 5, 2, -1, -4, 0, 0, 2])
    # runs: long 3 days, short 2 days, long 1 day
    assert holding_period_days(held) == pytest.approx(2.0)


def test_trading_summary_on_a_small_book():
    idx = pd.bdate_range("2024-01-01", periods=4)
    res = pd.DataFrame({
        "net_pnl": 0.0, "gross_pnl": [0.0, 1000.0, 1000.0, 2000.0], "costs": [30.0, 0.0, 30.0, 0.0],
        "contracts_traded": [2, 0, 4, 0], "gross_notional": 400_000.0,
        "pos_ES": [2, 2, -2, -2],
    }, index=idx)
    prices = {"ES": pd.DataFrame({"settle": 4000.0, "roll": False, "next_settle": np.nan}, index=idx)}
    exposure = net_exposure(res, prices, 1_000_000)
    assert exposure["ES"].tolist() == pytest.approx([0.4, 0.4, -0.4, -0.4])     # 2 x 4000 x 50

    row = trading_summary(res, prices, 1_000_000)
    assert row["contracts_per_year"] == pytest.approx(6 / 4 * 252)
    assert row["costs_share_of_gross_pnl"] == pytest.approx(60 / 4000)
    assert row["avg_gross_leverage"] == pytest.approx(0.4)
    assert row["days_net_short"] == pytest.approx(0.5)
    assert row["days_any_short"] == pytest.approx(0.5)
    assert row["avg_holding_days"] == pytest.approx(2.0)


def test_make_figures_saves_every_chart(tmp_path):
    idx = pd.bdate_range("2021-03-01", periods=600)
    rng = np.random.default_rng(0)
    returns = pd.DataFrame(0.0003 + 0.006 * rng.standard_normal((len(idx), 3)), index=idx,
                           columns=["Strategy", "Always long", "ES buy-and-hold"])
    exposure = pd.DataFrame({"ES": np.where(np.arange(len(idx)) % 200 < 150, 0.8, -0.5),
                             "NQ": 0.3}, index=idx)
    price = pd.Series(4000 * (1 + returns["ES buy-and-hold"]).cumprod(), name="ES")

    paths = make_figures(returns, exposure, price, tmp_path / "figures", target_vol=0.10)
    assert [p.name for p in paths] == [
        "cumulative_return.png", "drawdown.png", "annual_returns.png", "exposure.png",
        "rolling_sharpe_and_vol.png", "monthly_returns.png", "return_distribution.png"]
    assert all(p.stat().st_size > 10_000 for p in paths)


def test_make_figures_needs_a_strategy_and_a_benchmark(tmp_path):
    one = pd.DataFrame({"Strategy": [0.01, -0.01]}, index=pd.bdate_range("2024-01-01", periods=2))
    with pytest.raises(ValueError, match="2 to 3 columns"):
        make_figures(one, one, one["Strategy"], tmp_path)

