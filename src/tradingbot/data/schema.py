"""Normalized daily bar schema and fetch metadata shared by all providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd

SCHEMA_VERSION = "2"

# What open/high/low/close/volume represent. Never compare frames with different bases.
AS_TRADED = "as_traded"  # prices and volume as they were on the day
SPLIT_ADJUSTED = "split_adjusted"  # backward split-adjusted, not dividend-adjusted
TOTAL_RETURN = "total_return"  # split- and dividend-adjusted
PRICE_BASES = (AS_TRADED, SPLIT_ADJUSTED, TOTAL_RETURN)

# Column order of a normalized daily-bar frame. price_basis says what open/high/low/close/
# volume represent (as_traded when the source allows it). adj_close is the source's
# split- and dividend-adjusted close; adj_factor = adj_close / close.
NORMALIZED_COLUMNS: list[str] = [
    "date",
    "ticker",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "adj_factor",
    "split_ratio",
    "dividend",
    "source",
    "price_basis",
]

PRICE_COLUMNS = ["open", "high", "low", "close"]


@dataclass
class FetchMeta:
    """Metadata written next to every stored series."""

    source: str
    ticker: str
    requested_start: date
    requested_end: date
    fetched_at: datetime
    provider_version: str
    adjustment_method: str
    actual_start: date | None = None  # coverage of the STORED series after merge
    actual_end: date | None = None
    rows: int = 0  # rows in the stored series after merge
    fetch_rows: int = 0  # rows returned by this fetch
    write_mode: str = "upsert"  # upsert (merge by date) or replace
    fetch_history: list[str] = field(default_factory=list)  # one line per fetch, newest last
    quality_status: str = "UNKNOWN"
    quality_issues: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, datetime | date):
                d[k] = v.isoformat()
        return d


def empty_normalized() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in NORMALIZED_COLUMNS})


def validate_columns(df: pd.DataFrame) -> list[str]:
    """Return a list of missing or unexpected columns (empty if the frame conforms)."""
    missing = [c for c in NORMALIZED_COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in NORMALIZED_COLUMNS]
    problems = []
    if missing:
        problems.append(f"missing columns: {missing}")
    if extra:
        problems.append(f"unexpected columns: {extra}")
    return problems
