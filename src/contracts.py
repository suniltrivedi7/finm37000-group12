"""Contract specifications for the equity index futures in our universe.

Values are CME Group contract specs for the standard (non-micro) contracts.
Tick value is derived as ``tick_size * multiplier`` so the two can never
disagree.

Margin is an assumption, not data: CME changes performance bonds often and its
published history (mid-2020 onwards, no RTY) covers only part of the backtest,
so initial margin is modelled as a share of notional set in ``config.yaml``
(``margin.pct_of_notional``, with the sources behind the value).
"""

from dataclasses import dataclass

# Futures month codes, used to build symbols such as ESH6.
MONTH_CODES = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

# Equity index futures list the March quarterly cycle.
QUARTERLY_MONTHS = (3, 6, 9, 12)


@dataclass(frozen=True)
class ContractSpec:
    """Static specification for one futures product."""

    root: str
    name: str
    exchange: str
    multiplier: float      # USD per index point
    tick_size: float       # minimum price increment, in index points
    listed_months: tuple = QUARTERLY_MONTHS

    @property
    def tick_value(self) -> float:
        """USD value of one tick for one contract."""
        return self.tick_size * self.multiplier

    def notional(self, price: float, contracts: float = 1.0) -> float:
        """USD notional of ``contracts`` contracts at ``price``."""
        return price * self.multiplier * contracts

    def margin(self, price: float, contracts: float, pct_of_notional: float) -> float:
        """Approximate USD initial margin for ``contracts`` contracts at ``price``."""
        return abs(self.notional(price, contracts)) * pct_of_notional

    def symbol(self, year: int, month: int) -> str:
        """Exchange-style symbol, e.g. ``symbol(2026, 3) -> 'ESH6'``."""
        if month not in self.listed_months:
            raise ValueError(f"{self.root} does not list month {month}")
        return f"{self.root}{MONTH_CODES[month]}{year % 10}"


CONTRACTS = {
    "ES": ContractSpec("ES", "E-mini S&P 500", "CME", multiplier=50.0, tick_size=0.25),
    "NQ": ContractSpec("NQ", "E-mini Nasdaq-100", "CME", multiplier=20.0, tick_size=0.25),
    "RTY": ContractSpec("RTY", "E-mini Russell 2000", "CME", multiplier=50.0, tick_size=0.10),
    "YM": ContractSpec("YM", "E-mini Dow ($5)", "CBOT", multiplier=5.0, tick_size=1.0),
}


def get_spec(root: str) -> ContractSpec:
    """Look up a contract spec by root symbol (case-insensitive)."""
    try:
        return CONTRACTS[root.upper()]
    except KeyError as exc:
        raise KeyError(f"Unknown contract root {root!r}; known: {sorted(CONTRACTS)}") from exc
