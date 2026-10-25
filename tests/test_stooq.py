"""Stooq adapter and cross-source comparison on a common price basis (no network)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.compare import compare_series, rebase
from tradingbot.data.providers.base import RawBars
from tradingbot.data.providers.stooq import StooqError, StooqProvider, parse_csv
from tradingbot.data.schema import AS_TRADED, NORMALIZED_COLUMNS, SPLIT_ADJUSTED, TOTAL_RETURN

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def stooq_norm() -> pd.DataFrame:
    text = (FIXTURES / "stooq_raw_synthetic.csv").read_text()
    raw = RawBars("stooq", "SYN", parse_csv(text), "fixture")
    return StooqProvider().normalize(raw)


def test_symbol_suffix() -> None:
    assert StooqProvider().symbol("AAPL") == "aapl.us"
    assert StooqProvider(suffix=".de").symbol("BMW") == "bmw.de"


def test_normalize_schema_and_basis(stooq_norm: pd.DataFrame) -> None:
    assert list(stooq_norm.columns) == NORMALIZED_COLUMNS
    assert (stooq_norm["price_basis"] == SPLIT_ADJUSTED).all()
    assert (stooq_norm["split_ratio"] == 1.0).all()
    assert (stooq_norm["adj_factor"] == 1.0).all()
    assert len(stooq_norm) == 12


def test_parse_csv_quota_error_raises() -> None:
    with pytest.raises(StooqError):
        parse_csv("Exceeded the daily hits limit")


def test_parse_csv_no_data_is_empty() -> None:
    assert parse_csv("").empty
    assert parse_csv("No data").empty


def test_rebase_yahoo_to_split_adjusted_matches_stooq(
    normalized: pd.DataFrame, stooq_norm: pd.DataFrame
) -> None:
    """Yahoo as-traded, rebased to split_adjusted, must equal the Stooq-style series."""
    y = rebase(normalized, SPLIT_ADJUSTED)
    np.testing.assert_allclose(y["close"], stooq_norm["close"], rtol=1e-6)
    np.testing.assert_allclose(y["volume"], stooq_norm["volume"], rtol=1e-6)
    assert (y["price_basis"] == SPLIT_ADJUSTED).all()
    # Original frame untouched.
    assert (normalized["price_basis"] == AS_TRADED).all()


def test_rebase_to_total_return(normalized: pd.DataFrame) -> None:
    tr = rebase(normalized, TOTAL_RETURN)
    np.testing.assert_allclose(tr["close"], normalized["adj_close"], rtol=1e-9)


def test_rebase_split_adjusted_cannot_go_elsewhere(stooq_norm: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="cannot rebase"):
        rebase(stooq_norm, AS_TRADED)


def test_compare_defaults_to_b_basis(normalized: pd.DataFrame, stooq_norm: pd.DataFrame) -> None:
    rep = compare_series(normalized, stooq_norm)
    assert rep.basis == SPLIT_ADJUSTED
    assert rep.common_days == 12
    assert rep.close_days_over_tol == 0
    assert rep.close_max_rel_diff < 1e-6


def test_compare_refuses_impossible_basis(
    normalized: pd.DataFrame, stooq_norm: pd.DataFrame
) -> None:
    with pytest.raises(ValueError):
        compare_series(normalized, stooq_norm, basis=AS_TRADED)


def test_infer_basis_picks_split_adjusted(
    normalized: pd.DataFrame, stooq_norm: pd.DataFrame
) -> None:
    """Stooq-style numbers match Yahoo rebased to split_adjusted, not to total_return."""
    sa = compare_series(normalized, stooq_norm, basis=SPLIT_ADJUSTED, assume_b_basis=True)
    tr = compare_series(normalized, stooq_norm, basis=TOTAL_RETURN, assume_b_basis=True)
    assert sa.close_mean_rel_diff < 1e-6
    assert tr.close_mean_rel_diff > 0.001
