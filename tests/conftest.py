from pathlib import Path

import pandas as pd
import pytest

from tradingbot.data.providers.base import RawBars
from tradingbot.data.providers.yahoo import YahooProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def yahoo_raw() -> RawBars:
    """Synthetic Yahoo-style frame: 12 bars, 2:1 split on 2024-03-12, dividend on 2024-03-15."""
    frame = pd.read_csv(FIXTURES / "yahoo_raw_synthetic.csv")
    return RawBars("yahoo", "SYN", frame, provider_version="fixture")


@pytest.fixture
def normalized(yahoo_raw: RawBars) -> pd.DataFrame:
    return YahooProvider().normalize(yahoo_raw)
