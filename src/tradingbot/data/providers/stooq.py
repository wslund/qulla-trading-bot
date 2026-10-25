"""Stooq adapter (CSV download, no official API documentation).

Endpoint: https://stooq.com/q/d/l/?s=aapl.us&d1=YYYYMMDD&d2=YYYYMMDD&i=d[&apikey=...]
Columns: Date,Open,High,Low,Close,Volume. US tickers use the ``.us`` suffix.

Known from third-party descriptions, to be verified by the owner (docs/DATA_PILOT.md §3):
- An API key (obtained on stooq.com) may be required since early 2026; set STOOQ_API_KEY.
- A daily request quota applies; exceeding it returns an error text with HTTP 200.
- Prices are believed to be split-adjusted. Whether they are dividend-adjusted is unknown,
  and no split or dividend columns are provided, so as-traded prices cannot be reconstructed.
The adapter therefore labels the frame ``price_basis = split_adjusted`` and sets
split_ratio = 1, dividend = 0, adj_close = close, adj_factor = 1 ("unknown").
Use compare.rebase() to put Yahoo on the same basis before comparing.
"""

from __future__ import annotations

import io
import os
from datetime import date

import pandas as pd

from tradingbot.data.providers.base import MarketDataProvider, RawBars
from tradingbot.data.schema import NORMALIZED_COLUMNS, SPLIT_ADJUSTED

RAW_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]
BASE_URL = "https://stooq.com/q/d/l/"
ERROR_MARKERS = ("exceeded the daily hits limit", "przekroczony")  # quota messages, HTTP 200


class StooqError(RuntimeError):
    """Stooq answered with an error text instead of CSV (e.g. daily quota exceeded)."""


class StooqProvider(MarketDataProvider):
    name = "stooq"
    adjustment_method = (
        "Stooq CSV: OHLCV assumed split-adjusted (dividend adjustment unverified). No split or "
        "dividend data, so as-traded prices are not reconstructed; price_basis=split_adjusted, "
        "adj_close=close, adj_factor=1."
    )

    def __init__(self, suffix: str = ".us", api_key: str | None = None) -> None:
        self.suffix = suffix
        self.api_key = api_key if api_key is not None else os.environ.get("STOOQ_API_KEY")

    def symbol(self, ticker: str) -> str:
        return ticker.lower() + self.suffix

    def fetch_daily(self, ticker: str, start: date, end: date) -> RawBars:
        import urllib.parse
        import urllib.request

        params = {
            "s": self.symbol(ticker),
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        if self.api_key:
            params["apikey"] = self.api_key
        url = BASE_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "qulla-trading-bot/0.0 (pilot)"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
            text = resp.read().decode("utf-8", errors="replace")
        return RawBars(self.name, ticker, parse_csv(text), provider_version="stooq csv")

    def normalize(self, raw: RawBars) -> pd.DataFrame:
        df = raw.frame.copy()
        missing = [c for c in RAW_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"stooq raw frame missing columns {missing}")
        df["date"] = pd.to_datetime(df["Date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        close = df["Close"].to_numpy(float)
        out = pd.DataFrame(
            {
                "date": df["date"],
                "ticker": raw.ticker,
                "open": df["Open"].to_numpy(float),
                "high": df["High"].to_numpy(float),
                "low": df["Low"].to_numpy(float),
                "close": close,
                "volume": df["Volume"].to_numpy(float),
                "adj_close": close,
                "adj_factor": 1.0,
                "split_ratio": 1.0,
                "dividend": 0.0,
                "source": self.name,
                "price_basis": SPLIT_ADJUSTED,
            }
        )
        return out[NORMALIZED_COLUMNS]


def parse_csv(text: str) -> pd.DataFrame:
    """Parse Stooq's CSV body. Quota errors raise StooqError; no data yields an empty frame."""
    head = text.strip()[:200]
    if any(m in head.lower() for m in ERROR_MARKERS):
        raise StooqError(head)
    if not head or "Date" not in head:
        return pd.DataFrame(columns=RAW_COLUMNS)
    df = pd.read_csv(io.StringIO(text))
    if "Close" not in df.columns:
        return pd.DataFrame(columns=RAW_COLUMNS)
    return df.dropna(subset=["Close"]).reset_index(drop=True)
