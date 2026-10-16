"""Data download and continuous-contract construction.

Issue #3 (this file): download raw daily data for every listed contract of each
product in the universe from Databento and store it as one Parquet file per
product. Issue #4 (``build_continuous``) stitches those contracts into one
continuous series per product.

One command downloads everything the config asks for::

    python -m src.data --config config.yaml

The Databento key is read from the ``DATABENTO_API_KEY`` environment variable
(a ``.env`` file is picked up automatically) and is never written to disk.
Downloads are cached per product, year and schema, so re-running does not
re-download or re-bill; pass ``--force`` to refresh.

Three Databento schemas are combined, because no single one has everything:

* ``ohlcv-1h``   hourly bars, aggregated here into daily bars by CME trade date
* ``definition`` expiry date of each contract (also used to drop calendar spreads)
* ``statistics`` settlement price (type 3), cleared volume (6), open interest (9)

Databento's own ``ohlcv-1d`` bars are cut at 00:00 UTC, which splits each CME
session in two (the Sunday-evening hour becomes a separate "Sunday" bar and
every close lands after the next session has reopened), so they are not used.

See ``data/README.md`` for the output columns.
"""

from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .config import REPO_ROOT, load_config

STAT_SETTLEMENT = 3
STAT_CLEARED_VOLUME = 6
STAT_OPEN_INTEREST = 9
STAT_TYPES = (STAT_SETTLEMENT, STAT_CLEARED_VOLUME, STAT_OPEN_INTEREST)

# Final settlement and open interest are published after the trade date ends
# (often the next morning, or after a weekend), so statistics are requested a
# few days past each chunk and assigned to a chunk by the date they refer to.
STATS_PAD_DAYS = 7

# Settlement prices come twice: the exchange's official tick-rounded price carries
# this flag bit, and an unrounded value (e.g. 1043.70 next to 1043.75) does not.
ROUNDED_FLAG = 4

# CME trade dates roll at 17:00 Chicago time, when the evening session opens.
EXCHANGE_TZ = "America/Chicago"
SESSION_OPEN_HOUR = 17

# Columns kept from each raw schema (also the shape of an empty result).
DEFINITION_RAW_COLUMNS = ["ts_event", "instrument_class", "instrument_id", "raw_symbol", "activation", "expiration"]
HOURLY_COLUMNS = ["ts_event", "instrument_id", "open", "high", "low", "close", "volume"]
STATS_COLUMNS = ["ts_event", "ts_ref", "instrument_id", "stat_type", "price", "quantity", "stat_flags"]
PRICE_COLUMNS = [
    "date", "symbol", "instrument_id", "expiry",
    "open", "high", "low", "close", "settle",
    "volume", "cleared_volume", "open_interest",
]


def _client():
    """Historical Databento client; the key comes from the environment / .env."""
    import databento as db
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError(
            "DATABENTO_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return db.Historical(key)


def _retry(call, attempts: int = 5, delay: float = 15.0):
    """Run ``call()``, retrying Databento server errors (5xx, gateway timeouts) with backoff."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if type(exc).__name__ != "BentoServerError" or attempt == attempts - 1:
                raise
            wait = delay * 2 ** attempt
            print(f"  Databento server error ({exc}); retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else REPO_ROOT / path


def _utc_date(ts: pd.Series) -> pd.Series:
    """Timezone-aware UTC timestamps -> naive midnight dates."""
    return ts.dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()


# ---------------------------------------------------------------------------
# Pure transformations (no network): unit-tested with small fake frames.
# ---------------------------------------------------------------------------

def outright_definitions(defs: pd.DataFrame) -> pd.DataFrame:
    """One row per outright futures contract (spreads dropped): symbol, expiry, listing date.

    Databento repeats a definition on every session, so duplicates are removed.
    Instrument ids are **reused** once a contract expires (an expired outright's id
    can later belong to a calendar spread), so a contract is identified by its id
    together with its listing and expiry dates, not by the id alone.
    """
    outright = defs[defs["instrument_class"] == "F"]
    out = outright[["instrument_id", "raw_symbol", "activation", "expiration"]].rename(
        columns={"raw_symbol": "symbol"})
    out = out.assign(listed=_utc_date(out.pop("activation")), expiry=_utc_date(out.pop("expiration")))
    # The repeated definitions can disagree on the listing time by a day: keep the earliest.
    return out.groupby(["instrument_id", "symbol", "expiry"], as_index=False)["listed"].min()


def trade_date(ts: pd.Series) -> pd.Series:
    """CME trade date of UTC timestamps: the session opens 17:00 Chicago the evening before."""
    local = ts.dt.tz_convert(EXCHANGE_TZ)
    return (local + pd.Timedelta(hours=24 - SESSION_OPEN_HOUR)).dt.normalize().dt.tz_localize(None)


def daily_bars(hourly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate hourly bars into daily bars per contract and CME trade date."""
    bars = hourly.sort_values("ts_event").assign(date=trade_date(hourly["ts_event"]))
    daily = bars.groupby(["instrument_id", "date"], sort=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    )
    return daily.reset_index()


def stat_dates(stats: pd.DataFrame) -> pd.Series:
    """Trade date each statistics record refers to (naive midnight dates).

    Normally this is ``ts_ref``. Older cleared-volume and open-interest records
    (seen for 2010-2013) have no ``ts_ref``; they are published during trade date
    T and describe the previous business day, so that is the date used. Undated
    settlement records are left as NaT and dropped.
    """
    if stats.empty:
        return pd.Series(pd.NaT, index=stats.index, dtype="datetime64[ns]")
    dated = _utc_date(stats["ts_ref"])
    previous_day = trade_date(stats["ts_event"]) - pd.offsets.BDay(1)
    return dated.where(dated.notna() | (stats["stat_type"] == STAT_SETTLEMENT), previous_day)


def latest_statistic(stats: pd.DataFrame, stat_type: int, column: str) -> pd.DataFrame:
    """Final value of one statistic per contract and trade date.

    Statistics are revised (preliminary settlement, then final; open interest is
    restated), so the latest record wins. For settlement, the official tick-rounded
    price is preferred over an unrounded one even if the latter was published later.
    """
    value = "price" if stat_type == STAT_SETTLEMENT else "quantity"
    sel = stats[stats["stat_type"] == stat_type]
    sel = sel.assign(date=stat_dates(sel)).dropna(subset=["date"])
    if sel.empty:   # nothing published: every contract-day gets a missing value
        return pd.DataFrame({"instrument_id": pd.Series(dtype="uint32"),
                             "date": pd.Series(dtype="datetime64[ns]"),
                             column: pd.Series(dtype="float64")})
    rounded = (sel["stat_flags"] & ROUNDED_FLAG > 0) if stat_type == STAT_SETTLEMENT else False
    sel = sel.assign(rounded=rounded).sort_values(["rounded", "ts_event"])
    sel = sel.drop_duplicates(["instrument_id", "date"], keep="last")
    return sel[["instrument_id", "date", value]].rename(columns={value: column})


def build_contract_prices(defs: pd.DataFrame, hourly: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """Join daily bars, expiry, settlement, volume and open interest for one product."""
    contracts = outright_definitions(defs)
    prices = daily_bars(hourly).merge(contracts, on="instrument_id", how="inner")  # inner drops spreads
    # A reused instrument id also carries later contracts' bars; keep only the contract's own life.
    prices = prices[(prices["date"] >= prices["listed"]) & (prices["date"] <= prices["expiry"])]
    for stat_type, column in ((STAT_SETTLEMENT, "settle"),
                              (STAT_CLEARED_VOLUME, "cleared_volume"),
                              (STAT_OPEN_INTEREST, "open_interest")):
        prices = prices.merge(latest_statistic(stats, stat_type, column),
                              on=["instrument_id", "date"], how="left")

    prices["volume"] = prices["volume"].astype("int64")
    prices["cleared_volume"] = prices["cleared_volume"].astype("Int64")
    prices["open_interest"] = prices["open_interest"].astype("Int64")
    prices = prices[PRICE_COLUMNS].sort_values(["date", "expiry", "symbol"])
    return prices.reset_index(drop=True)


def summarize_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Row count, date range and missing values per contract, ordered by expiry."""
    g = prices.groupby("symbol", sort=False)
    out = pd.DataFrame({
        "expiry": g["expiry"].first(),
        "rows": g.size(),
        "first_date": g["date"].min(),
        "last_date": g["date"].max(),
        "missing_settle": g["settle"].apply(lambda s: int(s.isna().sum())),
        "missing_open_interest": g["open_interest"].apply(lambda s: int(s.isna().sum())),
    })
    return out.sort_values("expiry")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _chunk_bounds(year: int, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Start (inclusive) and end (exclusive) of the part of ``year`` inside [start, end]."""
    return (max(start, pd.Timestamp(year, 1, 1)),
            min(end + pd.Timedelta(days=1), pd.Timestamp(year + 1, 1, 1)))


def _day(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def _fetch_definitions(dataset, root, start, end, cache_dir, force):
    """Outright-contract definitions for the whole range: one request per product.

    Databento repeats every definition on every session, so only the outright
    futures (``instrument_class == "F"``) are kept and later de-duplicated.
    """
    path = cache_dir / f"{root}_definition_{_day(start)}_{_day(end)}.parquet"
    if path.exists() and not force:
        return pd.read_parquet(path)
    try:
        store = _retry(lambda: _client().timeseries.get_range(
            dataset=dataset, symbols=[f"{root}.FUT"], stype_in="parent", schema="definition",
            start=_day(start), end=_day(end + pd.Timedelta(days=1)),
        ))
    except Exception as exc:
        if type(exc).__name__ == "BentoClientError" and "symbology_invalid_request" in str(exc):
            return pd.DataFrame(columns=DEFINITION_RAW_COLUMNS)   # product not listed in this range
        raise
    df = store.to_df(map_symbols=False).reset_index()
    df = df[df["instrument_class"] == "F"][DEFINITION_RAW_COLUMNS]
    df.to_parquet(path, index=False)
    return df


def _concat(frames, columns):
    """Concatenate non-empty frames; an empty frame with ``columns`` if there are none."""
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def _active_ids(defs, chunk_start, chunk_end):
    """Instrument ids of contracts listed at some point in [chunk_start, chunk_end)."""
    contracts = outright_definitions(defs)
    live = contracts[(contracts["listed"] < chunk_end) & (contracts["expiry"] >= chunk_start)]
    return sorted(int(i) for i in live["instrument_id"].unique())


def _fetch_year(dataset, root, year, schema, ids, start, end, cache_dir, force):
    """One product-year of hourly bars or statistics for the given outright contracts.

    Requesting the outright instrument ids (rather than the ``.FUT`` parent) skips
    calendar spreads, which are most of the records and are never used.
    """
    chunk_start, chunk_end = _chunk_bounds(year, start, end)
    path = cache_dir / f"{root}_{schema}_{_day(chunk_start)}_{_day(chunk_end)}.parquet"
    if path.exists() and not force:
        return pd.read_parquet(path)
    if not ids:   # no contract of this product was listed in this year
        return pd.DataFrame(columns=HOURLY_COLUMNS if schema == "ohlcv-1h" else STATS_COLUMNS)

    request_end = chunk_end + pd.Timedelta(days=STATS_PAD_DAYS) if schema == "statistics" else chunk_end
    store = _retry(lambda: _client().timeseries.get_range(
        dataset=dataset, symbols=[str(i) for i in ids], stype_in="instrument_id", schema=schema,
        start=_day(chunk_start), end=_day(request_end),
    ))
    df = store.to_df(map_symbols=False).reset_index()
    if schema == "ohlcv-1h":
        df = df[HOURLY_COLUMNS]
    else:
        df = df[df["stat_type"].isin(STAT_TYPES)]
        ref = stat_dates(df)       # keep the records describing this chunk's trade dates
        df = df[(ref >= chunk_start) & (ref < chunk_end)][STATS_COLUMNS]
    df.to_parquet(path, index=False)
    return df


def download_raw(config: dict, force: bool = False, workers: int = 6) -> dict[str, Path]:
    """Download every product in ``config['universe']``; one Parquet per product.

    Stage 1 fetches each product's contract definitions; stage 2 fetches hourly
    bars and statistics per product-year for just those outright contracts.
    Requests run in parallel and each finished chunk is cached on disk, so an
    interrupted run resumes where it stopped.
    """
    data_cfg = config["data"]
    dataset = data_cfg["dataset"]
    raw_dir = _resolve(data_cfg["raw_dir"])
    cache_dir = raw_dir / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    available = pd.Timestamp(
        _retry(lambda: _client().metadata.get_dataset_range(dataset))["start"]).tz_localize(None)
    requested = pd.Timestamp(config["start_date"])
    start = max(requested, available.normalize())
    end = pd.Timestamp(config["end_date"])
    if start > requested:
        print(f"{dataset} starts {start.date()}; requested start {requested.date()} was clamped.")
    roots, years = config["universe"], range(start.year, end.year + 1)

    print(f"Stage 1: contract definitions for {len(roots)} products ...", flush=True)

    def get_defs(root):
        defs = _fetch_definitions(dataset, root, start, end, cache_dir, force)
        print(f"  {root} definition: {len(defs):,} records", flush=True)
        return root, defs

    with ThreadPoolExecutor(max_workers=workers) as pool:
        defs = dict(pool.map(get_defs, roots))

    for root in [r for r in roots if defs[r].empty]:
        print(f"  {root}: no contracts listed on {dataset} between {start.date()} and {end.date()}; skipped.")
    roots = [r for r in roots if not defs[r].empty]

    jobs = [(root, year, schema) for root in roots for year in years for schema in ("ohlcv-1h", "statistics")]
    print(f"Stage 2: {len(jobs)} requests ({len(roots)} products x {len(years)} years x 2 schemas), "
          f"{workers} in parallel; cached ones are skipped.", flush=True)

    def get_year(job):
        root, year, schema = job
        chunk_start, chunk_end = _chunk_bounds(year, start, end)
        ids = _active_ids(defs[root], chunk_start, chunk_end)
        frame = _fetch_year(dataset, root, year, schema, ids, start, end, cache_dir, force)
        print(f"  {root} {year} {schema}: {len(frame):,} records", flush=True)
        return job, frame

    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = dict(pool.map(get_year, jobs))

    written = {}
    for root in roots:
        hourly, stats = (_concat([frames[(root, y, s)] for y in years], cols)
                         for s, cols in (("ohlcv-1h", HOURLY_COLUMNS), ("statistics", STATS_COLUMNS)))
        prices = build_contract_prices(defs[root], hourly, stats)
        path = raw_dir / f"{root}_daily.parquet"
        prices.to_parquet(path, index=False)
        written[root] = path
        print(f"{root}: {len(prices):,} rows, {prices['symbol'].nunique()} contracts -> {path}")
    return written


def load_contract_prices(root: str, start: str, end: str, raw_dir: str | Path | None = None) -> pd.DataFrame:
    """Daily OHLCV, settle, volume and open interest for every listed contract of ``root``.

    Columns: date, symbol, instrument_id, expiry, open, high, low, close,
    settle, volume, cleared_volume, open_interest. Reads the file written by
    ``download_raw``.
    """
    raw = _resolve(raw_dir if raw_dir is not None else load_config()["data"]["raw_dir"])
    path = raw / f"{root.upper()}_daily.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `python -m src.data` to download it.")
    prices = pd.read_parquet(path)
    in_range = (prices["date"] >= pd.Timestamp(start)) & (prices["date"] <= pd.Timestamp(end))
    return prices[in_range].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Continuous contracts (issue #4)
# ---------------------------------------------------------------------------

ROLL_RULES = ("days_before_expiry", "volume_switch", "open_interest_switch")
SWITCH_COLUMNS = {"volume_switch": "volume", "open_interest_switch": "open_interest"}
ADJUSTMENTS = ("ratio_adjusted", "back_adjusted", "unadjusted")
# A settle this far from the same day's close is a source error, not a price move
# (NQM5 on 2025-06-18 settled at 2172025.00 against a close of 21732.00).
BAD_SETTLE_TOLERANCE = 0.5
CONTINUOUS_COLUMNS = [
    "symbol", "expiry", "settle", "pnl_points", "ret", "adj_price",
    "roll", "next_symbol", "next_settle", "volume", "open_interest",
]


def clean_settles(prices: pd.DataFrame) -> pd.DataFrame:
    """Blank settlements that are more than ``BAD_SETTLE_TOLERANCE`` away from the close.

    Genuine gaps between settle and close are a few percent at most, even for thin
    back months, so this only catches errors in the source data.
    """
    bad = (prices["settle"] / prices["close"] - 1).abs() > BAD_SETTLE_TOLERANCE
    return prices.assign(settle=prices["settle"].mask(bad))


def settle_table(prices: pd.DataFrame) -> pd.DataFrame:
    """Settlement price by trade date (rows) and contract expiry (columns).

    Contracts are keyed by expiry because symbols repeat every decade (``ESH1``
    is 2011 and 2021). Exchange holidays have no settlement and drop out, so the
    index is the product's own trading calendar.
    """
    settled = prices.dropna(subset=["settle"])
    return settled.pivot(index="date", columns="expiry", values="settle").sort_index(axis=1)


def roll_dates(settles: pd.DataFrame, days_before_expiry: int) -> pd.Series:
    """Date each contract is rolled out of, indexed by its expiry.

    The roll is ``days_before_expiry`` trading days before expiry, counted on the
    settlement calendar so holidays are skipped, and happens at that day's settle.
    If the next contract has no settlement that day, the roll moves back to the
    latest day on which both contracts settled. Days after the data ends are
    counted as business days, so a contract expiring later is simply held to the end.
    A contract with less than a full window left when the data starts gets NaT and
    is never held.
    """
    calendar = settles.index
    expiries = settles.columns
    days = calendar.append(pd.bdate_range(calendar[-1] + pd.offsets.BDay(1), expiries.max()))
    rolls = {}
    for front, nxt in zip(expiries[:-1], expiries[1:]):
        before = days[days < front]
        if len(before) < days_before_expiry:   # expires too soon after the data starts
            rolls[front] = pd.NaT
            continue
        target = before[-days_before_expiry]
        if target > calendar[-1]:
            rolls[front] = target
            continue
        both = calendar[(calendar <= target) & settles[front].notna() & settles[nxt].notna()]
        if both.empty:
            raise ValueError(f"No day on or before {target.date()} on which contracts expiring "
                             f"{front.date()} and {nxt.date()} both settled.")
        rolls[front] = both[-1]
    out = pd.Series(list(rolls.values()), index=pd.DatetimeIndex(list(rolls), name="expiry"),
                    name="roll_date", dtype="datetime64[ns]")
    if not out.dropna().is_monotonic_increasing:
        raise ValueError("Roll dates are out of order; check the contract data around the rolls.")
    return out


def switch_roll_dates(settles: pd.DataFrame, activity: pd.DataFrame) -> pd.Series:
    """Roll dates for a volume or open-interest switch, indexed by expiry.

    ``activity`` is volume or open interest by trade date and expiry. The roll
    happens at the settle of the trading day **after** the next contract first
    beats the front: open interest for D is only published on D+1, and D's volume
    includes the hour traded after the settlement. The search starts after the
    previous roll. If the next contract never takes over, the roll is on the last
    day before expiry on which both contracts settled.
    """
    calendar = settles.index
    expiries = settles.columns
    activity = activity.reindex(index=calendar, columns=expiries).astype("float64")
    rolls, previous = {}, pd.Timestamp.min
    for front, nxt in zip(expiries[:-1], expiries[1:]):
        window = calendar[(calendar > previous) & (calendar < front)]
        if window.empty:
            rolls[front] = front if previous > calendar[-1] else pd.NaT   # past the data / too late at the start
            continue
        ahead = activity.loc[window, nxt] > activity.loc[window, front]   # NaN compares False
        both = settles.loc[window, front].notna() & settles.loc[window, nxt].notna()
        act = ahead.shift(1, fill_value=False) & both
        if act.any():
            rolls[front] = act.idxmax()
        elif front > calendar[-1]:
            rolls[front] = front        # no switch yet when the data ends: held to the end
        elif both.any():
            rolls[front] = both[both].index[-1]
        else:
            raise ValueError(f"Contracts expiring {front.date()} and {nxt.date()} never both settled "
                             "before the front expired.")
        if pd.notna(rolls[front]):
            previous = rolls[front]
    return pd.Series(list(rolls.values()), index=pd.DatetimeIndex(list(rolls), name="expiry"),
                     name="roll_date", dtype="datetime64[ns]")


def held_expiry(calendar: pd.DatetimeIndex, rolls: pd.Series, expiries: pd.Index) -> pd.Series:
    """Expiry of the contract held over each trade date.

    A contract is held from the day after the previous roll up to and including
    its own roll date; the switch happens at the roll date's settle. The last
    contract in the chain has no roll date and is held after the last roll.
    """
    rolled = rolls.dropna()
    chain = rolled.index.append(pd.DatetimeIndex([expiries[-1]]))
    held = pd.Series(chain[rolled.searchsorted(calendar)], index=calendar, name="expiry")
    if (held.to_numpy() < calendar.to_numpy()).any():
        raise ValueError("A contract would be held past its expiry; check the contract data.")
    return held


def adjust_prices(cont: pd.DataFrame, adjustment: str) -> pd.Series:
    """Continuous price level, anchored so the last value equals the last raw settle.

    ``ratio_adjusted`` compounds same-contract returns, so its percentage changes
    equal ``ret``. ``back_adjusted`` adds up point changes, so its differences equal
    ``pnl_points``, but the summed roll gaps shift early prices a long way (NQ's 2010
    level moves from about 1,800 to 4,700), so its percentage changes are wrong.
    ``unadjusted`` is the raw settle and jumps at every roll.
    """
    last = cont["settle"].iloc[-1]
    if adjustment == "unadjusted":
        return cont["settle"].copy()
    if adjustment == "ratio_adjusted":
        growth = (1 + cont["ret"].fillna(0)).cumprod()
        return last * growth / growth.iloc[-1]
    steps = cont["pnl_points"].fillna(0)
    return last - (steps.sum() - steps.cumsum())


def build_continuous(prices: pd.DataFrame, roll_rule: dict) -> pd.DataFrame:
    """Stitch one product's contracts into a continuous series using ``roll_rule``.

    ``prices`` is the output of ``load_contract_prices`` for one root, and
    ``roll_rule`` is the ``roll`` block of ``config.yaml``. Returns one row per
    trade date (index ``date``) with the contract held over that day:

    * ``symbol``, ``expiry``: the contract held from the previous settle to this one
    * ``settle``: its raw settlement price, for sizing and P&L with the multiplier
    * ``pnl_points``, ``ret``: point and simple change since the previous settle,
      always within the same contract, so rolls create no jumps
    * ``adj_price``: continuous price level per ``roll_rule["adjustment"]``
    * ``roll``: True on the day the position moves to the next contract at the
      settle; ``next_symbol`` and ``next_settle`` give the price of the roll trade
    * ``volume``, ``open_interest``: of the held contract. Open interest for date D
      is only published on D+1, so lag it before using it in a signal.

    Holidays (no settlement) are dropped, as are the rare days on which the held
    contract has no settlement. Settlements that are clearly wrong in the source
    (see ``clean_settles``) count as missing.
    """
    rule = roll_rule.get("rule", "days_before_expiry")
    adjustment = roll_rule.get("adjustment", "ratio_adjusted")
    if rule not in ROLL_RULES:
        raise ValueError(f"Roll rule {rule!r} is not implemented; use one of {ROLL_RULES}.")
    if adjustment not in ADJUSTMENTS:
        raise ValueError(f"Unknown adjustment {adjustment!r}; use one of {ADJUSTMENTS}.")
    days = roll_rule.get("days_before_expiry", 5)
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError(f"days_before_expiry must be a whole number of days >= 1, got {days!r}.")
    empty = pd.DataFrame(columns=CONTINUOUS_COLUMNS, index=pd.DatetimeIndex([], name="date"))
    if prices.empty:
        return empty

    prices = clean_settles(prices)
    settles = settle_table(prices)
    if settles.empty:
        return empty
    if rule == "days_before_expiry":
        rolls = roll_dates(settles, days)
    else:
        activity = prices.pivot(index="date", columns="expiry", values=SWITCH_COLUMNS[rule])
        rolls = switch_roll_dates(settles, activity)
    held = held_expiry(settles.index, rolls, settles.columns)
    nxt = pd.Series(settles.columns[1:], index=settles.columns[:-1])
    symbols = prices.drop_duplicates("expiry").set_index("expiry")["symbol"]
    rows = prices.set_index(["date", "expiry"])

    cont = rows.reindex(pd.MultiIndex.from_arrays([held.index, held.values], names=["date", "expiry"]))
    cont = cont[["symbol", "settle", "volume", "open_interest"]].reset_index("expiry")
    cont = cont[cont["settle"].notna()]
    cont["volume"] = cont["volume"].astype("int64")
    # reindex rather than map: pandas 3 cannot map through an empty datetime Series.
    cont["roll"] = rolls.reindex(cont["expiry"]).to_numpy() == cont.index.to_numpy()
    next_expiry = pd.Series(nxt.reindex(cont["expiry"]).to_numpy(), index=cont.index).where(cont["roll"])
    cont["next_symbol"] = next_expiry.map(symbols)
    cont["next_settle"] = [settles.at[d, e] if pd.notna(e) else float("nan")
                           for d, e in zip(cont.index, next_expiry)]

    # The day after a roll is measured from the new contract's settle on the roll day.
    base = cont["settle"].shift(1).where(~cont["roll"].shift(1, fill_value=False), cont["next_settle"].shift(1))
    cont["pnl_points"] = cont["settle"] - base
    cont["ret"] = cont["pnl_points"] / base
    cont["adj_price"] = adjust_prices(cont, adjustment)
    return cont[CONTINUOUS_COLUMNS]


def main() -> None:
    parser = argparse.ArgumentParser(description="Download raw futures data from Databento.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--start", help="override start_date (YYYY-MM-DD), e.g. for a shorter first pull")
    parser.add_argument("--end", help="override end_date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    parser.add_argument("--workers", type=int, default=6, help="parallel requests")
    args = parser.parse_args()
    config = load_config(args.config)
    config["start_date"] = args.start or config["start_date"]
    config["end_date"] = args.end or config["end_date"]
    written = download_raw(config, force=args.force, workers=args.workers)
    for root in written:
        prices = load_contract_prices(root, config["start_date"], config["end_date"],
                                      config["data"]["raw_dir"])
        print(f"{root}: {len(prices):,} rows, {prices['date'].min().date()} to "
              f"{prices['date'].max().date()}")


if __name__ == "__main__":
    main()
