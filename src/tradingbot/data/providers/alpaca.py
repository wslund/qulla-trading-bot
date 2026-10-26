"""Alpaca Market Data API v2 adapter (historical daily bars only; no trading endpoints).

Endpoint: GET https://data.alpaca.markets/v2/stocks/{symbol}/bars
Auth headers: APCA-API-KEY-ID / APCA-API-SECRET-KEY, read from ALPACA_API_KEY and
ALPACA_SECRET_KEY. Keys are never logged, stored in metadata or included in errors.

Configuration used here: timeframe=1Day, feed=sip, adjustment=raw (Alpaca: "raw applies
no adjustments"), limit=10000, sort=asc, pagination via next_page_token.
With adjustment=raw the bars are as traded, so price_basis=as_traded. Alpaca's bars carry
no split or dividend columns, so split_ratio, dividend, adj_close and adj_factor are NaN
("unknown") and the series cannot be rebased to another basis. Alpaca's own adjustment
options (split, dividend, all) are not used in the pilot; see docs/DATA_PILOT.md §3.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tradingbot.data.providers.base import MarketDataProvider, RawBars
from tradingbot.data.schema import AS_TRADED, NORMALIZED_COLUMNS

BASE_URL = "https://data.alpaca.markets/v2/stocks"
RAW_COLUMNS = ["t", "o", "h", "l", "c", "v"]
BAR_TZ = "America/New_York"


class AlpacaError(RuntimeError):
    """HTTP or API error from Alpaca; the message never contains credentials."""


class AlpacaProvider(MarketDataProvider):
    name = "alpaca"
    adjustment_method = (
        "Alpaca v2 bars, feed=sip, adjustment=raw (no adjustments) -> as-traded OHLCV. "
        "No split/dividend data in bars: split_ratio, dividend, adj_close, adj_factor = NaN."
    )

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        feed: str = "sip",
        adjustment: str = "raw",
        asof: str | None = None,
        max_retries: int = 5,
        sleep=time.sleep,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self.feed = feed
        self.adjustment = adjustment
        self.asof = asof
        self.max_retries = max_retries
        self._sleep = sleep
        self._now = now

    def effective_end(self, end: date) -> tuple[date, str]:
        """Cap ``end`` to the last New York date strictly before today (New York time).

        Free/paper SIP subscriptions reject requests that touch the most recent 15 minutes
        (HTTP 403 "subscription does not permit querying recent SIP data"). A date-only
        ``end`` equal to today is interpreted by Alpaca as reaching into that window, so the
        request is capped to yesterday; today's bar is picked up on the next run.
        An explicit ``end`` in the past is sent unchanged.
        """
        ny_today = self._now().astimezone(ZoneInfo(BAR_TZ)).date()
        last_safe = ny_today - timedelta(days=1)
        if end >= ny_today:
            return (
                last_safe,
                f"end capped {end.isoformat()} -> {last_safe.isoformat()} (SIP recent-data limit)",
            )
        return end, ""

    def fetch_daily(self, ticker: str, start: date, end: date) -> RawBars:
        if not self.api_key or not self.secret_key:
            raise AlpacaError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        end, note = self.effective_end(end)
        if end < start:
            return RawBars(self.name, ticker, pd.DataFrame(), "alpaca v2 bars", notes=note)
        params = {
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
            "adjustment": self.adjustment,
            "feed": self.feed,
            "sort": "asc",
        }
        if self.asof:
            params["asof"] = self.asof
        bars: list[dict] = []
        page_token: str | None = None
        while True:
            if page_token:
                params["page_token"] = page_token
            payload = self._get(f"{BASE_URL}/{ticker.upper()}/bars", params)
            bars.extend(payload.get("bars") or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        frame = pd.DataFrame(bars, columns=RAW_COLUMNS + ["n", "vw"]) if bars else pd.DataFrame()
        version = f"alpaca v2 bars feed={self.feed} adjustment={self.adjustment}"
        return RawBars(self.name, ticker, frame, provider_version=version, notes=note)

    def _get(self, url: str, params: dict) -> dict:
        """GET with retries on 429 (Retry-After) and 5xx; other 4xx raise immediately."""
        full = url + "?" + urllib.parse.urlencode(params)
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(full, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed host
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:200]
                if e.code == 429 or e.code >= 500:
                    if attempt == self.max_retries:
                        raise AlpacaError(f"HTTP {e.code} after {attempt} retries: {body}") from e
                    wait = float(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                    self._sleep(wait)
                    continue
                raise AlpacaError(f"HTTP {e.code}: {body}") from e
            except urllib.error.URLError as e:
                raise AlpacaError(f"network error: {e.reason}") from e
        raise AlpacaError("unreachable")

    def normalize(self, raw: RawBars) -> pd.DataFrame:
        df = raw.frame
        if df.empty:
            return pd.DataFrame(columns=NORMALIZED_COLUMNS)
        missing = [c for c in RAW_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"alpaca raw frame missing columns {missing}")
        ts = pd.to_datetime(df["t"], utc=True).dt.tz_convert(BAR_TZ)
        out = pd.DataFrame(
            {
                "date": ts.dt.date,
                "ticker": raw.ticker,
                "open": df["o"].to_numpy(float),
                "high": df["h"].to_numpy(float),
                "low": df["l"].to_numpy(float),
                "close": df["c"].to_numpy(float),
                "volume": df["v"].to_numpy(float),
                "adj_close": np.nan,
                "adj_factor": np.nan,
                "split_ratio": np.nan,
                "dividend": np.nan,
                "source": self.name,
                "price_basis": AS_TRADED,
            }
        )
        out = out.sort_values("date").reset_index(drop=True)
        return out[NORMALIZED_COLUMNS]
