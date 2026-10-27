"""Provider interface. Every data source implements fetch_daily() and normalize()."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass
class RawBars:
    """Raw provider output, stored unchanged under data/raw/."""

    source: str
    ticker: str
    frame: pd.DataFrame
    provider_version: str
    notes: str = ""  # provider remarks, e.g. an end date capped for subscription limits


class MarketDataProvider(ABC):
    """Daily OHLCV source. Subclasses must not alter raw data in fetch_daily()."""

    name: str
    adjustment_method: str

    @abstractmethod
    def fetch_daily(self, ticker: str, start: date, end: date) -> RawBars:
        """Fetch raw daily bars for [start, end] inclusive. Network access happens here only."""

    @abstractmethod
    def normalize(self, raw: RawBars) -> pd.DataFrame:
        """Convert raw bars to the normalized schema (see schema.NORMALIZED_COLUMNS).

        Must be a pure function of ``raw`` so it can be tested offline from fixtures.
        """
