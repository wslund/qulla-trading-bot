"""Yahoo Finance adapter via the unofficial ``yfinance`` package.

Terms: yfinance is not affiliated with Yahoo; Yahoo's API is intended for personal use
only. See docs/DATA_PILOT.md before using this data for anything but research.

Adjustment semantics (as implemented in yfinance; Yahoo publishes no formal spec, so the
owner must confirm with ``scripts/verify_split.py`` on a known split, e.g. AAPL 2020-08-31 4:1):
- With ``auto_adjust=False`` the Open/High/Low/Close/Volume columns are *split-adjusted*
  backwards but not dividend-adjusted. ``Adj Close`` is split- and dividend-adjusted.
- ``Stock Splits`` holds the split ratio on the split date (4.0 for 4:1), 0 otherwise.
This adapter reconstructs as-traded prices by multiplying back the cumulative ratio of all
splits that occur *after* each bar, and divides volume by the same factor.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from tradingbot.data.providers.base import MarketDataProvider, RawBars
from tradingbot.data.schema import NORMALIZED_COLUMNS

RAW_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"]


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    adjustment_method = (
        "yfinance auto_adjust=False: OHLCV split-adjusted by Yahoo, Adj Close split+dividend "
        "adjusted. As-traded OHLCV reconstructed by multiplying back cumulative future "
        "'Stock Splits' ratios (volume divided). adj_factor = Adj Close / as-traded close."
    )

    def fetch_daily(self, ticker: str, start: date, end: date) -> RawBars:
        import yfinance as yf  # imported lazily so tests never need the package

        # yfinance treats ``end`` as exclusive; our interface is inclusive.
        frame = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=True,
            repair=False,
        )
        frame = frame.reset_index()
        return RawBars(self.name, ticker, frame, provider_version=f"yfinance {yf.__version__}")

    def normalize(self, raw: RawBars) -> pd.DataFrame:
        df = raw.frame.copy()
        missing = [c for c in RAW_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"yahoo raw frame missing columns {missing}")
        date_col = "Date" if "Date" in df.columns else "index"
        dates = pd.to_datetime(df[date_col], utc=True).dt.tz_convert(None).dt.normalize()
        df = df.assign(date=dates.dt.date).sort_values("date").reset_index(drop=True)

        split_ratio = df["Stock Splits"].astype(float).replace(0.0, 1.0)
        # Cumulative product of ratios strictly after each bar: reverse cumprod shifted by one.
        future_factor = split_ratio[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
        future_factor = future_factor.astype(float).to_numpy()

        out = pd.DataFrame(
            {
                "date": df["date"],
                "ticker": raw.ticker,
                "open": df["Open"].to_numpy(float) * future_factor,
                "high": df["High"].to_numpy(float) * future_factor,
                "low": df["Low"].to_numpy(float) * future_factor,
                "close": df["Close"].to_numpy(float) * future_factor,
                "volume": np.round(df["Volume"].to_numpy(float) / future_factor),
                "adj_close": df["Adj Close"].to_numpy(float),
                "adj_factor": df["Adj Close"].to_numpy(float)
                / (df["Close"].to_numpy(float) * future_factor),
                "split_ratio": split_ratio.to_numpy(float),
                "dividend": df["Dividends"].to_numpy(float),
                "source": self.name,
            }
        )
        return out[NORMALIZED_COLUMNS]
