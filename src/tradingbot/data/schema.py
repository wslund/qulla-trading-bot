"""Normalized daily bar schema and fetch metadata shared by all providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd

SCHEMA_VERSION = "1"

# Column order of a normalized daily-bar frame. Prices in open/high/low/close are
# as traded (not adjusted); adj_close is split- and dividend-adjusted by the source;
# adj_factor = adj_close / close lets any column be adjusted on demand.
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
    actual_start: date | None = None
    actual_end: date | None = None
    rows: int = 0
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
