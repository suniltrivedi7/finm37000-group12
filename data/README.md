# Raw data dictionary (issue #3)

`python -m src.data --config config.yaml` writes one Parquet file per product to
`data/raw/<ROOT>_daily.parquet` (`ES`, `NQ`, `RTY`, `YM`). Each row is **one outright
futures contract on one CME trade date**, so a single date has several rows (one per
listed expiry). `data.load_contract_prices(root, start, end)` reads these files.

Raw files are git-ignored. Re-running the command reuses the per-year cache in
`data/raw/_cache/` and does not re-download; `--force` refreshes it.

## Source

Databento, dataset `GLBX.MDP3` (CME Globex), parent symbology (`ES.FUT` etc., every
expiry). Three schemas are combined, because no single one has everything:

| Schema | Used for |
|---|---|
| `ohlcv-1h` | open, high, low, close, volume (hourly bars, aggregated to daily here) |
| `definition` | expiry date; also identifies outright contracts so calendar spreads can be dropped |
| `statistics` | settlement price (stat type 3), cleared volume (6), open interest (9) |

Databento's own `ohlcv-1d` bars are **not** used. They are cut at 00:00 UTC, which splits
each CME session in two: the Sunday-evening hour becomes a separate "Sunday" bar, and
each bar's close falls after the next session has already reopened. Daily bars are
instead built from hourly bars by CME trade date (see `date` below).

## Columns

| Column | Type | Description |
|---|---|---|
| `date` | datetime64 | CME **trade date**. A session opens at 17:00 Chicago time the evening before, so Sunday-evening trading belongs to Monday. Weekdays only, except one Sunday row on 2018-08-05 (no settlement). |
| `symbol` | string | Exchange symbol, e.g. `ESH4` (H = March, M = June, U = September, Z = December; last digit = year). **Not unique across decades** (`ESH1` is 2011 and 2021); use `expiry` (with `symbol`) or `instrument_id` with `expiry` to identify a contract. |
| `instrument_id` | uint32 | Databento's numeric contract id. **Not unique over time:** Databento reuses an id after a contract expires (for example, an expired `ESM1`'s id later belonged to a calendar spread), and bars outside a contract's listing-to-expiry life are dropped for that reason. Identify a contract by `instrument_id` plus `expiry`. |
| `expiry` | datetime64 | Last trading day, from the contract definition (UTC date). Equity index futures expire on the third Friday of the quarterly month, except when
  that day is an exchange holiday (for example `ESM7` expires Thursday 2027-06-17, because
  Friday the 18th is the observed Juneteenth holiday). All expiries in the 2025 sample are third Fridays. |
| `open` | float | First trade of the trade date (index points). |
| `high` | float | Highest trade of the trade date. |
| `low` | float | Lowest trade of the trade date. |
| `close` | float | Price of the last hourly bar of the trade date (about 16:00 Chicago). This is **not** the settlement price. |
| `settle` | float | Official CME daily settlement price (the exchange's tick-rounded value). Use this, not `close`, for marking positions and returns. Missing on exchange holidays (see below). |
| `volume` | int64 | Contracts traded in the bars, summed over the trade date. Counts the contract's own order book only. |
| `cleared_volume` | Int64 | Official cleared volume from the exchange. Larger than `volume` because it also counts spread legs and off-book trades (typically 5–8% more for ES). Nullable. Databento reported **0** for trade date 2025-06-05 on every product (a source glitch: traded `volume` was normal), so treat `cleared_volume == 0` with `volume > 0` as missing. |
| `open_interest` | Int64 | Open contracts at the end of the trade date. Nullable. |

All prices are in index points. Multiply by the contract multiplier in `src/contracts.py`
to get dollars. Prices are the raw, unadjusted prices of each individual contract:
**no roll adjustment** is applied here (that is issue #4).

## Things to know before using the data

- **Open interest is published the morning after.** The value for trade date D is first
  available on D+1. A signal computed at the close of D must not use D's open interest
  (look-ahead). Lag it by one day, which matters for any open-interest roll rule in #4.
- **Settlement and open interest are final values.** Both are revised after first
  publication (preliminary then final settlement; restated open interest). The latest
  record for each contract and date is kept. Databento also publishes an unrounded
  settlement next to the official tick-rounded one (e.g. 1043.70 beside 1043.75); the
  rounded record (flag bit 4) is preferred even if the unrounded one arrives later.
- **Older open interest and cleared volume have no date.** In the 2010 and 2013 samples
  checked, these records carry no `ts_ref`. They are published during trade date T and
  describe the previous business day, so that is the date assigned (the implied
  cleared-to-traded volume ratio moves smoothly day to day, as the dated data does).
  This rule was checked on sample weeks only.
- **Exchange holidays.** On days such as Martin Luther King Day and Presidents' Day,
  CME equity futures trade a short session and publish no settlement, open interest or
  cleared volume. Those rows have `settle`, `cleared_volume` and `open_interest` empty.
  Drop them or forward-fill when building continuous series (#4).
- **Expiry day.** Open interest can be missing on the expiry date itself (seen for `ESH4` on 2024-03-15).
- **Start date.** `config.yaml` asks for 2010-01-01, but `GLBX.MDP3` data begins
  2010-06-06, so the earliest rows are from June 2010.
- **RTY starts in 2017.** Russell 2000 futures only exist in Databento's CME data from
  2017-06-04. ES, NQ and YM go back to 2010. A range with no RTY contracts skips RTY with a
  message; a longer range simply has RTY rows from mid-2017.
- **Thin contracts.** Back-month contracts list years ahead of expiry and trade a few
  contracts a day, so their prices are unreliable. Roll logic should only look at the
  front few expiries.
- **Data quality flags from Databento.** A partial pull of 2010–2014 ES data warned that
  some days were "degraded" in the source (2014-06-11 to 2014-06-13 and possibly
  others). Check these dates for gaps before relying on them.

## Coverage

The full configured range (`config.yaml`: 2010-01-01 to 2025-12-31, clamped to Databento's
first available day, 2010-06-06) was downloaded for issue #4:

| Product | Rows | Contracts | First date | Missing `settle` | Missing `cleared_volume` / `open_interest` | Rows after expiry | Duplicate (id, date) |
|---|---|---|---|---|---|---|---|
| ES | 14,514 | 40 | 2010-06-07 | 276 | 321 / 321 | 0 | 0 |
| NQ | 11,243 | 40 | 2010-06-07 | 226 | 277 / 277 | 0 | 0 |
| RTY | 4,670 | 37 | 2017-07-10 | 116 | 126 / 126 | 0 | 0 |
| YM | 10,220 | 40 | 2010-06-07 | 209 | 243 / 243 | 0 | 0 |

The two sample runs below match the full download except on their first day, where the sample
misses the Sunday-evening hours before its start. Running `python -m src.data --config config.yaml` with
no overrides downloads everything; cached chunks are reused. A shorter range can be pulled
with `--start` / `--end`, for example `--start 2025-06-02 --end 2025-06-06` for the sample.

Sample run, 2025-06-02 to 2025-06-06 (5 trade dates), all four products:

| Product | Rows | Contracts | Rows per contract | Missing `settle` | Missing `open_interest` |
|---|---|---|---|---|---|
| ES | 15 | `ESM5`, `ESU5`, `ESZ5` | 5, 5, 5 | 0 | 0 |
| NQ | 15 | `NQM5`, `NQU5`, `NQZ5` | 5, 5, 5 | 0 | 0 |
| RTY | 12 | `RTYM5`, `RTYU5`, `RTYZ5` | 5, 5, 2 | 0 | 0 |
| YM | 15 | `YMM5`, `YMU5`, `YMZ5` | 5, 5, 5 | 0 | 0 |

Expiries are 2025-06-20 (M), 2025-09-19 (U) and 2025-12-19 (Z), all third Fridays. `RTYZ5` has
only 2 rows because it barely traded that week. Dates are Monday to Friday only. For the
front contract, `|close - settle| / settle` is at most 0.3%.

A second real run covered the earliest data, 2010-06-07 to 2011-12-30 (RTY skipped, no contracts yet):

| Product | Rows | Contracts | Missing `settle` | Missing `cleared_volume` / `open_interest` | Rows after expiry | Duplicate (id, date) |
|---|---|---|---|---|---|---|
| ES | 1,321 | 11 | 24 | 21 / 21 | 0 | 0 |
| NQ | 1,009 | 11 | 21 | 17 / 17 | 0 | 0 |
| YM | 957 | 10 | 17 | 17 / 17 | 0 | 0 |

Missing `settle` rows are exchange holidays (9 dates, e.g. 2010-07-05, 2011-01-17), and the few missing
open-interest rows are mostly expiry days. All non-expiry settlements are on valid ticks, and front-month
`|close - settle| / settle` is at most 1.4%. This run is what exposed the reused-instrument-id, undated
open interest and rounded-settlement issues described above.


# Continuous series (issue #4)

`data.build_continuous(prices, config["roll"])` turns one product's contracts (the output of
`load_contract_prices`) into one row per trade date, holding a single contract at a time.
Nothing is written to disk; `run_backtest.py` builds the series in memory.

## Roll rule

Three rules are implemented (`roll.rule` in `config.yaml`); the default is the first.

- `days_before_expiry` (default, `days_before_expiry: 5`): roll at the settle N trading days
  before expiry. Days are counted on the product's settlement calendar, so exchange holidays
  are skipped.
- `volume_switch`: roll at the settle of the day **after** the next contract first trades more
  than the front. The day's volume includes the hour traded after the settlement, so acting the
  same day would look ahead.
- `open_interest_switch`: the same with open interest. Open interest for D is only published
  on D+1, so it also acts one day late.

For both switches the search starts after the previous roll. If the next contract never takes
over, the roll is on the last day before expiry that both contracts settled. A missing
next-contract settle moves a `days_before_expiry` roll back to the latest earlier day both
contracts settled, and a switch roll forward to the next day both settled. The switches react
to a single day's crossing, so one volume spike in the next contract can trigger an early roll.

Why the default, from the full 2010-2025 data (223 rolls across ES, NQ, RTY and YM): the next
contract first out-trades the front 5 trading days before expiry in 143 rolls, 4 days before in
69 and 3 days before in 11. So the fixed rule rolls on the switch day or one or two days early,
and the series holds a contract that is not the most traded one on 154 of about 13,900 days.
The volume switch, acting a day late, rolls 2-4 days before expiry and holds the less traded
contract on 446 days; the open-interest switch rolls 1-5 days before expiry and does so on 652
days. Total returns differ little between the rules (ES 548.7% vs 549.0% vs 549.4%), so the
choice is about trading the liquid contract and needing no data published after the fact.

## Columns

| Column | Type | Description |
|---|---|---|
| `date` (index) | datetime64 | Trade date. Exchange holidays (no settlement) are dropped. |
| `symbol`, `expiry` | string, datetime64 | Contract held from the previous settle to this one. Never held on its expiry day. |
| `settle` | float | Raw settlement price of that contract. Use it with the multiplier for sizing and P&L. |
| `pnl_points` | float | Settle change since the previous date **in the same contract** (index points). Day after a roll: new contract's settle minus its settle on the roll day. |
| `ret` | float | `pnl_points` divided by the previous settle of the same contract. No jump at rolls. Missing on the first row only. |
| `adj_price` | float | Continuous price level for signals, per `adjustment` (below). The last value equals the last raw settle. |
| `roll` | bool | True on the day the position moves to the next contract, at that day's settle. |
| `next_symbol`, `next_settle` | string, float | On roll days only: the contract rolled into and its settle that day, i.e. the price of the roll trade. |
| `volume` | int64 | Traded volume of the held contract. Includes trading after the settle, so lag it one day before using it in a signal. |
| `open_interest` | Int64 | Open interest of the held contract. Published on D+1, so lag it one day before using it in a signal. |

## Adjustment

- `ratio_adjusted` (default): compounds `ret`, so `adj_price.pct_change() == ret`. Safe for
  returns, volatility and the 252-day momentum signal.
- `back_adjusted`: adds up `pnl_points`, so `adj_price.diff() == pnl_points`. The summed roll
  gaps shift early prices a long way: the 2010 level moves from 1,048 to 1,527 for ES and from
  1,796 to 4,733 for NQ, so percentage changes of it understate early returns (by about 2.6x
  for NQ). Do not take percentage changes of it.
- `unadjusted`: the raw `settle`, which jumps at every roll (e.g. about -0.4% for ES in 2010-11,
  +0.9% in 2025).

`ret` and `pnl_points` are the same under all three, so the choice only affects `adj_price`.
Its level is rescaled with the whole sample (anchored to the last settle), so use its ratios or
differences in signals, never its level for sizing or thresholds.

## Using it in a backtest

P&L on date D is the position held since D-1 times `pnl_points` times the multiplier. On a
roll day the old contract is closed at `settle` and the new one opened at `next_settle`, so the
position after D's settle is in `next_symbol` and should be sized at `next_settle`; on other
days it stays in `symbol`. A roll trades both legs, so it pays costs twice.

## Data issues handled

- **Bad settlements:** a settle more than 50% away from the same day's close is treated as
  missing. In the full data this only catches NQM5 on 2025-06-18 (settle 2172025.00, close
  21732.00). Genuine settle-close gaps stay under 8%; the largest are thin back months on 2020-03-13.
- **Missing days:** some normal trading days have no row for a product and drop out of its
  calendar like holidays; the next `pnl_points` and `ret` then span the gap, so no price move is
  lost. ES and NQ have no rows on 2014-06-12, 2014-06-13, 2014-09-23 to 2014-09-25 and
  2014-12-31, and YM none on 2014-10-03; the June 2014 roll therefore happens on 2014-06-11.
  Most are source gaps (no bars and no settlement; Databento flags 2014-06-12 and 2014-09-23
  to 2014-09-25 as degraded). But ES and NQ on 2014-06-13 and 2014-12-31, and YM on 2014-10-03,
  do have an official settlement with no hourly bars, and `build_contract_prices` builds rows
  from the bars, so it drops them. YM also trades but has no settlement on 2020-02-27 and 2020-06-30.
- **Calendars differ by product** because of these gaps (ES and NQ have 3,920 rows, YM 3,923).
  When combining products, a product's missing day means no price, no P&L and no trade for it
  that day; its next row carries the move.
- **Sunday rows:** 2018-08-05 (a Sunday) has one row each for ES, NQ and YM, with 24-302
  contracts traded and no settlement; it is dropped with the holidays.
- **Session and time zone:** already handled in the raw data, whose dates are CME trade dates.
