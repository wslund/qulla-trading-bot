"""fetch_daily() wiring, with a fake yfinance module (no network)."""

import sys
import types
from datetime import date

import pandas as pd
import pytest

from tradingbot.data.providers.base import RawBars
from tradingbot.data.providers.yahoo import YahooProvider


class _FakeTicker:
    calls: list[dict] = []

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kwargs) -> pd.DataFrame:
        _FakeTicker.calls.append({"symbol": self.symbol, **kwargs})
        frame = _FakeTicker.frame.copy()
        return frame.set_index("Date")


@pytest.fixture
def fake_yfinance(monkeypatch: pytest.MonkeyPatch, yahoo_raw: RawBars):
    mod = types.ModuleType("yfinance")
    mod.Ticker = _FakeTicker
    mod.__version__ = "0.0-fake"
    _FakeTicker.frame = yahoo_raw.frame
    _FakeTicker.calls = []
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    return mod


def test_fetch_daily_calls_yfinance_unadjusted_with_exclusive_end(fake_yfinance) -> None:
    raw = YahooProvider().fetch_daily("SYN", date(2024, 3, 4), date(2024, 3, 19))
    call = _FakeTicker.calls[0]
    assert call["symbol"] == "SYN"
    assert call["auto_adjust"] is False
    assert call["actions"] is True
    assert call["interval"] == "1d"
    assert call["start"] == "2024-03-04"
    assert call["end"] == "2024-03-20"  # inclusive end -> yfinance exclusive end
    assert raw.provider_version == "yfinance 0.0-fake"
    assert "Date" in raw.frame.columns  # index reset so raw parquet keeps the date
    assert len(YahooProvider().normalize(raw)) == 12
