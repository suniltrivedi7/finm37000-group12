"""Tests for issue #4: continuous contracts. Fake frames only, no data files."""

import numpy as np
import pandas as pd
import pytest

from src.config import REPO_ROOT, load_config
from src.data import CONTINUOUS_COLUMNS, build_continuous, roll_dates, settle_table

RULE = {"rule": "days_before_expiry", "days_before_expiry": 5, "adjustment": "ratio_adjusted"}
DATES = pd.bdate_range("2024-03-01", "2024-04-05")
# ESH4 expires Friday 2024-03-15; five trading days before it is Friday 2024-03-08.
ROLL_DAY = pd.Timestamp("2024-03-08")


def contract(symbol, expiry, settle, dates=DATES, volume=1000, open_interest=5000):
    settle = pd.Series(settle, index=dates, dtype="float64")
    volume = pd.Series(volume, index=dates, dtype="int64")
    return pd.DataFrame({
        "date": dates, "symbol": symbol, "instrument_id": 1, "expiry": pd.Timestamp(expiry),
        "open": settle.values, "high": settle.values, "low": settle.values,
        "close": settle.values, "settle": settle.values, "volume": volume.values,
        "cleared_volume": pd.array(volume.values, dtype="Int64"),
        "open_interest": pd.array(pd.Series(open_interest, index=dates).values, dtype="Int64"),
    })


def two_contracts(front=100.0, nxt=110.0, dates=DATES):
    return pd.concat([contract("ESH4", "2024-03-15", front, dates),
                      contract("ESM4", "2024-06-21", nxt, dates)], ignore_index=True)


def trending():
    # Both contracts trend, with the next one about 5% above the front.
    steps = np.arange(len(DATES))
    return two_contracts(front=100 + steps, nxt=105 + 1.1 * steps)


def test_roll_is_n_trading_days_before_expiry():
    cont = build_continuous(two_contracts(), RULE)
    assert cont.index[cont["roll"]].tolist() == [ROLL_DAY]
    assert cont.loc[ROLL_DAY, "symbol"] == "ESH4"                         # held through the roll day
    assert cont.loc[pd.Timestamp("2024-03-11"), "symbol"] == "ESM4"       # next contract from the day after
    assert cont.loc[ROLL_DAY, "next_symbol"] == "ESM4"
    assert cont.loc[ROLL_DAY, "next_settle"] == 110.0
    assert cont["next_symbol"].drop(ROLL_DAY).isna().all()


def test_holidays_are_dropped_and_not_counted_towards_the_roll():
    prices = two_contracts()
    prices.loc[prices["date"] == "2024-03-12", "settle"] = np.nan          # exchange holiday: no settlement
    cont = build_continuous(prices, RULE)
    assert pd.Timestamp("2024-03-12") not in cont.index
    assert cont.index[cont["roll"]].tolist() == [pd.Timestamp("2024-03-07")]


def test_rolls_create_no_jump():
    cont = build_continuous(two_contracts(front=100.0, nxt=110.0), RULE)
    assert cont["settle"].diff().max() == 10.0                              # the raw settle does jump
    assert (cont["ret"].dropna() == 0).all()
    assert (cont["pnl_points"].dropna() == 0).all()
    assert (cont["adj_price"] == 110.0).all()


def test_day_after_roll_is_measured_in_the_new_contract():
    prices = trending()
    cont = build_continuous(prices, RULE)
    nxt = prices[prices["symbol"] == "ESM4"].set_index("date")["settle"]
    after = pd.Timestamp("2024-03-11")
    assert cont.loc[after, "pnl_points"] == pytest.approx(nxt[after] - nxt[ROLL_DAY])
    assert cont.loc[after, "ret"] == pytest.approx(nxt[after] / nxt[ROLL_DAY] - 1)


@pytest.mark.parametrize("adjustment", ["ratio_adjusted", "back_adjusted", "unadjusted"])
def test_adjustments_share_returns_and_end_at_the_raw_settle(adjustment):
    cont = build_continuous(trending(), {**RULE, "adjustment": adjustment})
    reference = build_continuous(trending(), RULE)
    pd.testing.assert_series_equal(cont["ret"], reference["ret"])
    assert cont["adj_price"].iloc[-1] == pytest.approx(cont["settle"].iloc[-1])
    if adjustment == "ratio_adjusted":
        np.testing.assert_allclose(cont["adj_price"].pct_change().iloc[1:], cont["ret"].iloc[1:])
    elif adjustment == "back_adjusted":
        np.testing.assert_allclose(cont["adj_price"].diff().iloc[1:], cont["pnl_points"].iloc[1:])
    else:
        pd.testing.assert_series_equal(cont["adj_price"], cont["settle"], check_names=False)


def test_roll_moves_back_when_next_contract_has_no_settle():
    prices = two_contracts()
    missing = (prices["symbol"] == "ESM4") & (prices["date"] == ROLL_DAY)
    cont = build_continuous(prices[~missing], RULE)
    assert cont.index[cont["roll"]].tolist() == [pd.Timestamp("2024-03-07")]


def test_day_without_a_settle_for_the_held_contract_is_skipped():
    prices = trending()
    gap = pd.Timestamp("2024-03-05")
    prices.loc[(prices["symbol"] == "ESH4") & (prices["date"] == gap), "settle"] = np.nan
    cont = build_continuous(prices, RULE)
    assert gap not in cont.index
    assert cont.loc[pd.Timestamp("2024-03-06"), "pnl_points"] == pytest.approx(2.0)   # two days of +1


def test_settle_far_from_the_close_is_treated_as_missing():
    prices = trending()
    bad = (prices["symbol"] == "ESH4") & (prices["date"] == "2024-03-05")
    prices.loc[bad, "settle"] = prices.loc[bad, "close"] * 100      # decimal error in the source
    cont = build_continuous(prices, RULE)
    assert pd.Timestamp("2024-03-05") not in cont.index
    assert cont["ret"].abs().max() < 0.05


def test_contract_too_close_to_expiry_at_the_start_is_never_held():
    late_start = DATES[DATES >= "2024-03-11"]          # only four trading days left before ESH4 expires
    cont = build_continuous(two_contracts(dates=late_start), RULE)
    assert (cont["symbol"] == "ESM4").all()
    assert not cont["roll"].any()


def test_contracts_are_told_apart_by_expiry_not_symbol():
    # Symbols repeat every decade: a 2034 ESH4 must never be confused with the 2024 one.
    prices = pd.concat([two_contracts(), contract("ESH4", "2034-03-17", 999.0)], ignore_index=True)
    cont = build_continuous(prices, RULE)
    assert (cont["settle"] < 999).all()
    assert cont.loc[cont["symbol"] == "ESH4", "expiry"].unique().tolist() == [pd.Timestamp("2024-03-15")]


def test_only_contracts_with_a_successor_get_a_roll_date():
    rolls = roll_dates(settle_table(two_contracts()), 5)
    assert rolls.index.tolist() == [pd.Timestamp("2024-03-15")]


def test_output_columns_and_types():
    cont = build_continuous(two_contracts(), RULE)
    assert list(cont.columns) == CONTINUOUS_COLUMNS
    assert cont.index.name == "date"
    assert cont.index.is_monotonic_increasing and cont.index.is_unique
    assert cont["roll"].dtype == bool
    assert np.isnan(cont["ret"].iloc[0])


def test_empty_prices_give_an_empty_frame():
    cont = build_continuous(two_contracts().iloc[0:0], RULE)
    assert cont.empty
    assert list(cont.columns) == CONTINUOUS_COLUMNS


def switching(column, from_day):
    # Front and next contract whose volume or open interest moves to the next one on from_day.
    later = (DATES >= from_day).astype(int)
    front = {column: 1000 - 900 * later}
    nxt = {column: 100 + 900 * later + 100}
    return pd.concat([contract("ESH4", "2024-03-15", 100.0, **front),
                      contract("ESM4", "2024-06-21", 110.0, **nxt)], ignore_index=True)


@pytest.mark.parametrize("rule, column", [("volume_switch", "volume"),
                                          ("open_interest_switch", "open_interest")])
def test_switch_rolls_the_day_after_the_next_contract_takes_over(rule, column):
    # Seen at the end of Wednesday 2024-03-06, acted on at Thursday's settle.
    cont = build_continuous(switching(column, "2024-03-06"), {**RULE, "rule": rule})
    assert cont.index[cont["roll"]].tolist() == [pd.Timestamp("2024-03-07")]
    assert cont.loc[pd.Timestamp("2024-03-08"), "symbol"] == "ESM4"


def test_switch_without_a_takeover_rolls_on_the_last_day_before_expiry():
    cont = build_continuous(switching("volume", "2030-01-01"), {**RULE, "rule": "volume_switch"})
    assert cont.index[cont["roll"]].tolist() == [pd.Timestamp("2024-03-14")]


def test_open_interest_switch_ignores_missing_values():
    prices = switching("open_interest", "2024-03-06")
    prices.loc[prices["date"] == "2024-03-06", "open_interest"] = pd.NA   # e.g. not published that day
    cont = build_continuous(prices, {**RULE, "rule": "open_interest_switch"})
    assert cont.index[cont["roll"]].tolist() == [pd.Timestamp("2024-03-08")]


def test_three_contracts_roll_in_order():
    dates = pd.bdate_range("2024-03-01", "2024-07-05")
    prices = pd.concat([contract("ESH4", "2024-03-15", 100.0, dates),
                        contract("ESM4", "2024-06-21", 110.0, dates),
                        contract("ESU4", "2024-09-20", 120.0, dates)], ignore_index=True)
    cont = build_continuous(prices, RULE)
    assert cont.index[cont["roll"]].tolist() == [ROLL_DAY, pd.Timestamp("2024-06-14")]
    assert cont["symbol"].unique().tolist() == ["ESH4", "ESM4", "ESU4"]
    assert cont.loc[pd.Timestamp("2024-06-14"), "next_symbol"] == "ESU4"


def test_switch_with_no_takeover_before_the_data_ends_holds_the_front():
    early = DATES[DATES <= "2024-03-12"]
    prices = pd.concat([contract("ESH4", "2024-03-15", 100.0, early, volume=1000),
                        contract("ESM4", "2024-06-21", 110.0, early, volume=10)], ignore_index=True)
    cont = build_continuous(prices, {**RULE, "rule": "volume_switch"})
    assert not cont["roll"].any()
    assert (cont["symbol"] == "ESH4").all()


def test_no_settlements_give_an_empty_frame():
    prices = two_contracts().assign(settle=np.nan)
    assert build_continuous(prices, RULE).empty


@pytest.mark.parametrize("days", [0, -1, 5.7, True])
def test_days_before_expiry_must_be_a_whole_number_of_days(days):
    with pytest.raises(ValueError):
        build_continuous(two_contracts(), {**RULE, "days_before_expiry": days})


@pytest.mark.parametrize("override", [{"rule": "calendar_spread_switch"}, {"adjustment": "log_adjusted"}])
def test_unsupported_settings_raise(override):
    with pytest.raises(ValueError):
        build_continuous(two_contracts(), {**RULE, **override})


def test_config_roll_block_is_accepted():
    roll = load_config()["roll"]
    assert roll["adjustment"] == "ratio_adjusted"
    cont = build_continuous(two_contracts(), roll)
    assert cont["roll"].sum() == 1


@pytest.mark.skipif(not (REPO_ROOT / "data/raw/ES_daily.parquet").exists(),
                    reason="needs the downloaded data (python -m src.data)")
def test_real_data_has_no_jumps_at_rolls():
    # Issue #4's done-criterion: on the day after a roll, the series moves like the old contract.
    prices = pd.read_parquet(REPO_ROOT / "data/raw/ES_daily.parquet")
    cont = build_continuous(prices, load_config()["roll"])
    settles = prices.pivot(index="date", columns="expiry", values="settle")
    rolls = cont.index[cont["roll"]]
    assert len(rolls) > 0
    assert (cont["expiry"] > cont.index).all()                       # never held on its expiry day
    for day in rolls:
        after = cont.index[cont.index.get_loc(day) + 1]
        old = settles.at[after, cont.at[day, "expiry"]] / settles.at[day, cont.at[day, "expiry"]] - 1
        assert abs(cont.at[after, "ret"] - old) < 0.005, day
