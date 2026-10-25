"""Yahoo adapter: as-traded reconstruction from split-adjusted raw data."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.providers.base import RawBars
from tradingbot.data.providers.yahoo import YahooProvider
from tradingbot.data.schema import NORMALIZED_COLUMNS, validate_columns


def test_schema(normalized: pd.DataFrame) -> None:
    assert list(normalized.columns) == NORMALIZED_COLUMNS
    assert validate_columns(normalized) == []
    assert len(normalized) == 12
    assert (normalized["ticker"] == "SYN").all()
    assert (normalized["source"] == "yahoo").all()


def test_as_traded_close_reconstructed_before_split(normalized: pd.DataFrame) -> None:
    pre = normalized[normalized["date"] < pd.Timestamp("2024-03-12").date()]
    post = normalized[normalized["date"] >= pd.Timestamp("2024-03-12").date()]
    # Raw pre-split closes were ~50 (split-adjusted); as traded they were ~100.
    assert pre["close"].between(99, 105).all()
    assert post["close"].between(51, 55).all()
    # Volume goes the other way: raw volume was doubled pre-split.
    assert pre["volume"].between(0.8e6, 1.4e6).all()
    assert post["volume"].between(1.8e6, 2.5e6).all()


def test_split_ratio_column(normalized: pd.DataFrame) -> None:
    on_split = normalized.loc[normalized["date"] == pd.Timestamp("2024-03-12").date()]
    assert on_split["split_ratio"].item() == 2.0
    assert (normalized["split_ratio"].drop(on_split.index) == 1.0).all()


def test_adj_factor_adjusts_any_column(normalized: pd.DataFrame) -> None:
    adj_close = normalized["close"] * normalized["adj_factor"]
    np.testing.assert_allclose(adj_close, normalized["adj_close"], rtol=1e-9)
    # Dividend adjustment: factor < 1 before the ex-date, == 1 from the ex-date on.
    ex = pd.Timestamp("2024-03-15").date()
    assert (normalized.loc[normalized["date"] < ex, "adj_factor"] < 1).all()
    np.testing.assert_allclose(normalized.loc[normalized["date"] >= ex, "adj_factor"], 1.0)
    assert normalized.loc[normalized["date"] == ex, "dividend"].item() == 0.5


def test_ohlc_scaled_consistently(normalized: pd.DataFrame) -> None:
    assert (normalized["low"] <= normalized[["open", "close"]].min(axis=1)).all()
    assert (normalized["high"] >= normalized[["open", "close"]].max(axis=1)).all()


def test_missing_columns_raise() -> None:
    bad = pd.DataFrame({"Date": ["2024-01-02"], "Close": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        YahooProvider().normalize(RawBars("yahoo", "X", bad, "fixture"))
