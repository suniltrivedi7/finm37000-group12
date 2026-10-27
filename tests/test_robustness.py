"""Tests for issue #10: out-of-sample helpers. Fake returns only, no data files."""

import numpy as np
import pandas as pd
import pytest

from src import robustness

INDEX = pd.bdate_range("2011-06-01", "2021-12-31")


def noise(mean, seed, sd=0.01, index=INDEX):
    return pd.Series(np.random.default_rng(seed).normal(mean, sd, len(index)), index=index)


def test_variants_are_every_combination():
    out = robustness.variants({"lookback_days": [63, 252], "skip_days": [0, 21]})
    assert len(out) == 4
    assert {"lookback_days": 252, "skip_days": 21} in out


def test_label_names_the_parameters():
    assert robustness.label({"lookback_days": 252, "skip_days": 0}) == "lookback_days=252, skip_days=0"


def test_sharpe_scales_to_a_year_and_handles_series_and_frames():
    r = pd.Series([0.01, 0.0, 0.02, -0.01] * 50)
    expected = r.mean() / r.std() * np.sqrt(252)
    assert robustness.sharpe(r) == pytest.approx(expected)
    assert robustness.sharpe(pd.DataFrame({"a": r, "b": -r})).tolist() == pytest.approx([expected, -expected])


def test_sharpe_of_a_flat_series_is_nan():
    assert np.isnan(robustness.sharpe(pd.Series([0.0] * 20)))


def test_sharpe_ignores_nan():
    r = pd.Series([np.nan] * 10 + [0.01, -0.01, 0.02, 0.0] * 10)
    assert robustness.sharpe(r) == pytest.approx(robustness.sharpe(r.dropna()))


def test_split_puts_the_cut_date_out_of_sample():
    r = noise(0, 0)
    before, after = robustness.split(r, "2019-01-01")
    assert before.index.max() < pd.Timestamp("2019-01-01") <= after.index.min()
    assert len(before) + len(after) == len(r)


def test_in_vs_out_reports_both_periods_and_ranks():
    grid = pd.DataFrame({"good": noise(0.002, 1), "bad": noise(-0.002, 2)})
    table = robustness.in_vs_out(grid, "2019-01-01")
    assert table.loc["good", "sharpe_in"] > 0 > table.loc["bad", "sharpe_in"]
    assert table.loc["good", "rank_out"] == 1


def test_rank_correlation_is_one_when_rankings_agree_and_minus_one_when_reversed():
    agree = pd.DataFrame({"sharpe_in": [0.1, 0.5, 0.9], "sharpe_out": [0.0, 0.2, 0.3]})
    assert robustness.rank_correlation(agree) == pytest.approx(1.0)
    assert robustness.rank_correlation(agree.assign(sharpe_out=[0.3, 0.2, 0.0])) == pytest.approx(-1.0)


def test_walk_forward_picks_using_only_earlier_years():
    # "late" only becomes the best variant from 2018; walk-forward must not pick it before then.
    early = noise(0.003, 3)
    late = noise(0.0, 4)
    late[late.index.year >= 2018] += 0.02
    grid = pd.DataFrame({"early": early, "late": late})
    out, chosen = robustness.walk_forward(grid, first_test_year=2017, min_train_years=5)
    assert chosen.loc[2017, "variant"] == "early"
    assert chosen.loc[2018, "variant"] == "early"     # 2018's own returns are not yet known
    assert chosen.loc[2021, "variant"] == "late"
    assert out.index.min() >= pd.Timestamp("2017-01-01") and out.index.max().year == 2021


def test_walk_forward_skips_years_without_enough_history():
    grid = pd.DataFrame({"a": noise(0.001, 5)})
    out, chosen = robustness.walk_forward(grid, first_test_year=2013, min_train_years=5)
    assert list(chosen.index) == list(range(2017, 2022))    # first year with 5 years behind it
    assert out.index.min().year == 2017


def test_walk_forward_ignores_a_variant_that_has_not_started():
    a = noise(0.001, 6)
    b = noise(0.01, 7)
    b[b.index.year < 2016] = np.nan                     # starts late, so only a few training years
    out, chosen = robustness.walk_forward(pd.DataFrame({"a": a, "b": b}), 2017, 5)
    assert not out.isna().any()


def test_load_or_run_grid_reuses_the_cache_until_the_config_changes(tmp_path, monkeypatch):
    calls = []

    def fake_run_grid(prices, config, grid):
        calls.append(1)
        return pd.DataFrame({"a": [0.01, 0.02]}, index=pd.bdate_range("2020-01-01", periods=2))

    monkeypatch.setattr(robustness, "run_grid", fake_run_grid)
    config = {"signal": {"lookback_days": 252}, "robustness": {"grid": {"lookback_days": [63]}}}
    path = tmp_path / "grid.parquet"
    first = robustness.load_or_run_grid({}, config, path)
    again = robustness.load_or_run_grid({}, config, path)
    assert len(calls) == 1 and first.equals(again)
    config["robustness"]["grid"]["lookback_days"] = [63, 126]       # a different grid
    robustness.load_or_run_grid({}, config, path)
    assert len(calls) == 2


# --- sensitivity and regimes -------------------------------------------------

GRID_COLUMNS = [robustness.label(v) for v in robustness.variants(
    {"lookback_days": [63, 252], "skip_days": [0, 21], "vol_lookback_days": [20, 60]})]


def test_variant_params_reads_labels_back():
    params = robustness.variant_params(GRID_COLUMNS)
    assert params.shape == (8, 3)
    row = params.loc["lookback_days=252, skip_days=21, vol_lookback_days=20"]
    assert row.tolist() == [252, 21, 20]


def test_sharpe_table_is_one_variant_per_cell():
    grid = pd.DataFrame({name: noise(0.001 * i, i) for i, name in enumerate(GRID_COLUMNS)})
    table = robustness.sharpe_table(grid, "lookback_days", "vol_lookback_days", fixed={"skip_days": 0})
    assert table.shape == (2, 2)
    name = "lookback_days=252, skip_days=0, vol_lookback_days=60"
    assert table.loc[252, 60] == pytest.approx(robustness.sharpe(grid[name]))


def test_sharpe_table_limits_dates_and_demands_pinned_parameters():
    grid = pd.DataFrame({name: noise(0.001, i) for i, name in enumerate(GRID_COLUMNS)})
    with pytest.raises(ValueError, match="skip_days"):
        robustness.sharpe_table(grid, "lookback_days", "vol_lookback_days")
    full = robustness.sharpe_table(grid, "lookback_days", "vol_lookback_days", {"skip_days": 0})
    late = robustness.sharpe_table(grid, "lookback_days", "vol_lookback_days", {"skip_days": 0}, start="2019-01-01")
    assert not full.equals(late)


def test_episode_table_sums_returns_and_finds_the_drawdown():
    idx = pd.bdate_range("2020-01-01", periods=10)
    ret = pd.DataFrame({"s": [0.0, 0.01, -0.02, -0.01, 0.01, 0, 0, 0, 0, 0.5], "late": [np.nan] * 5 + [0.01] * 5}, index=idx)
    out = robustness.episode_table(ret, [{"name": "dip", "start": "2020-01-02", "end": "2020-01-07"},
                                         {"name": "early", "start": "2020-01-01", "end": "2020-01-03"}])
    assert out.loc["dip", ("return", "s")] == pytest.approx(-0.01)
    assert out.loc["dip", ("max_drawdown", "s")] == pytest.approx(-0.03)
    assert np.isnan(out.loc["early", ("return", "late")])      # that strategy had not started


def test_label_periods_assigns_each_date_to_the_latest_start():
    idx = pd.to_datetime(["2011-03-01", "2018-01-02", "2021-12-31", "2022-01-03"])
    labels = robustness.label_periods(idx, [{"name": "a", "start": "2010-01-01"},
                                            {"name": "b", "start": "2018-01-01"},
                                            {"name": "c", "start": "2022-01-01"}])
    assert labels.tolist() == ["a", "b", "b", "c"]


def test_vol_regimes_label_a_calm_and_a_stormy_era():
    r = noise(0, 8)
    r[(r.index >= "2020-01-01") & (r.index < "2021-01-01")] *= 5      # a year of high volatility
    labels = robustness.vol_regimes(r, 60, ["low", "medium", "high"])
    assert labels.isna().sum() == 60                  # 59 days to build the window, plus one for the lag
    assert (labels["2020-04-01":"2020-12-31"] == "high").all()
    calm = labels[labels.index < "2019-10-01"].dropna()
    assert (calm == "high").mean() < 0.25             # high labels in calm years are the exception


def test_vol_regimes_are_known_the_day_before():
    r = noise(0, 8)
    shocked = r.copy()
    shocked.iloc[-1] = 10.0                           # a huge move on the last day only
    base = robustness.vol_regimes(r, 60, ["low", "medium", "high"])
    assert robustness.vol_regimes(shocked, 60, ["low", "medium", "high"]).iloc[-1] == base.iloc[-1]


def test_regime_summary_splits_days_and_sharpe_by_regime():
    idx = pd.bdate_range("2020-01-01", periods=200)
    ret = pd.DataFrame({"s": np.where(np.arange(200) < 100, 0.001, -0.001) + noise(0, 9, 0.002, idx).to_numpy()}, index=idx)
    labels = pd.Series(np.where(np.arange(200) < 100, "calm", "stress"), index=idx)
    out = robustness.regime_summary(ret, labels)
    assert out.loc[("calm", "s"), "days"] == 100
    assert out.loc[("calm", "s"), "sharpe"] > 0 > out.loc[("stress", "s"), "sharpe"]


def test_margin_usage_scales_with_the_percentage():
    results = pd.DataFrame({"margin_usage": [0.04, 0.08]})
    out = robustness.margin_usage_table(results, base_pct=0.08, pcts=[0.04, 0.16])
    assert out.loc[0.16, "peak_usage"] == pytest.approx(0.16)
    assert out.loc[0.04, "mean_usage"] == pytest.approx(0.03)


def test_variant_positions_override_the_signal_and_always_long_is_never_short():
    index = pd.bdate_range("2023-01-02", periods=400)
    price = 4000 * np.cumprod(1 + np.random.default_rng(0).normal(0, 0.01, 400))
    es = pd.DataFrame({"settle": price, "ret": pd.Series(price, index=index).pct_change().to_numpy(),
                       "pnl_points": pd.Series(price).diff().to_numpy(), "roll": False, "next_settle": np.nan},
                      index=index)
    config = {"signal": {"method": "time_series_momentum", "lookback_days": 60, "vol_lookback_days": 20},
              "sizing": {"method": "volatility_target", "capital": 10_000_000, "target_annual_vol": 0.10,
                         "max_gross_leverage": 3.0, "max_weight_per_asset": 1.5}}
    momentum = robustness.variant_positions({"ES": es}, config, {"lookback_days": 120})
    long_only = robustness.variant_positions({"ES": es}, config, {"lookback_days": 120}, always_long=True)
    assert config["signal"]["lookback_days"] == 60                       # the caller's config is untouched
    assert (momentum["ES"] != 0).sum() == (long_only["ES"] != 0).sum() > 0     # same warm-up
    assert (long_only["ES"] >= 0).all()


def test_report_tables_cover_split_walk_forward_costs_and_episodes():
    idx = pd.bdate_range("2011-01-03", "2021-12-31")
    rng = np.random.default_rng(7)
    prices = {}
    for root, start in (("ES", 1200.0), ("NQ", 2400.0)):
        ret = pd.Series(0.0004 + 0.01 * rng.standard_normal(len(idx)), index=idx)
        ret.iloc[0] = np.nan
        settle = start * (1 + ret.fillna(0)).cumprod()
        prices[root] = pd.DataFrame({"settle": settle, "ret": ret, "pnl_points": settle.diff(),
                                     "adj_price": settle, "roll": False, "next_settle": np.nan})
    config = {
        "costs": {"commission_per_contract": 2.5, "slippage_ticks": 1},
        "margin": {"pct_of_notional": 0.08},
        "metrics": {"benchmark": "ES"},
        "sizing": {"method": "volatility_target", "capital": 10_000_000,
                   "target_annual_vol": 0.10, "max_gross_leverage": 3.0},
        "signal": {"method": "time_series_momentum", "lookback_days": 60, "skip_days": 0,
                   "vol_lookback_days": 30},
        "robustness": {
            "split_date": "2017-01-01",
            "walk_forward": {"first_test_year": 2017, "min_train_years": 5},
            "grid": {"lookback_days": [30, 60], "skip_days": [0], "vol_lookback_days": [30]},
            "costs": {"slippage_ticks": [0, 1, 5]},
            "episodes": [{"name": "crash", "start": "2020-02-19", "end": "2020-03-23"}],
        },
    }
    grid_returns = robustness.run_grid(prices, config, config["robustness"]["grid"])
    tables = robustness.report_tables(prices, config, grid_returns)

    assert set(tables) == {"robustness_split", "robustness_walk_forward",
                           "robustness_slippage", "robustness_episodes"}
    assert list(tables["robustness_split"].columns) == ["in_sample", "out_of_sample", "change"]
    assert list(tables["robustness_slippage"].index) == [0, 1, 5]
    assert tables["robustness_slippage"]["default"].is_monotonic_decreasing
    assert "return: default" in tables["robustness_episodes"].columns
