"""Scanner features: point-in-time proofs, split handling, hand-checked values."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.schema import AS_TRADED, NORMALIZED_COLUMNS, SPLIT_ADJUSTED, TOTAL_RETURN
from tradingbot.scanner.features import (
    FEATURE_COLUMNS,
    FeatureError,
    FeatureParams,
    attach_splits,
    compute_features,
)


def make_series(n: int = 120, seed: int = 7, ticker: str = "SYN") -> pd.DataFrame:
    """As-traded synthetic series: 60 bars drifting up, then 60 bars flat with shrinking
    daily ranges (a consolidation), no splits. Deterministic."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = np.empty(n)
    close[:60] = 100 * np.cumprod(1 + rng.normal(0.006, 0.01, 60))
    close[60:] = close[59] * (1 + rng.normal(0, 0.004, 60)).cumprod() ** 0.1
    rng2 = np.random.default_rng(seed + 1)
    rng_pct = np.concatenate([np.full(60, 0.03), np.linspace(0.03, 0.01, 60)])
    high = close * (1 + rng_pct * rng2.uniform(0.4, 1.0, n))
    low = close * (1 - rng_pct * rng2.uniform(0.4, 1.0, n))
    opn = (high + low) / 2
    return pd.DataFrame(
        {
            "date": [d.date() for d in dates],
            "ticker": ticker,
            "open": opn,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng2.integers(1e6, 2e6, n).astype(float),
            "adj_close": close,
            "adj_factor": 1.0,
            "split_ratio": 1.0,
            "dividend": 0.0,
            "source": "test",
            "price_basis": AS_TRADED,
        }
    )[NORMALIZED_COLUMNS]


def apply_split(df: pd.DataFrame, at: int, ratio: float) -> pd.DataFrame:
    """Simulate a ratio:1 split on bar index ``at`` in an as-traded series: prices from
    ``at`` on are divided by ratio, volume multiplied, split_ratio set on that bar."""
    out = df.copy()
    for c in ("open", "high", "low", "close", "adj_close"):
        out.loc[at:, c] = out.loc[at:, c] / ratio
    out.loc[at:, "volume"] = out.loc[at:, "volume"] * ratio
    out.loc[at, "split_ratio"] = ratio
    return out


# --- schema and convention ------------------------------------------------------------------


def test_schema_dates_and_row_count() -> None:
    df = make_series()
    f = compute_features(df)
    assert list(f.columns) == FEATURE_COLUMNS
    assert len(f) == len(df) - 1
    assert list(f["date"]) == list(df["date"].iloc[1:])
    assert list(f["asof_date"]) == list(df["date"].iloc[:-1])
    assert (f["input_price_basis"] == AS_TRADED).all()
    assert f["sma50"].iloc[:49].isna().all() and f["sma50"].iloc[49:].notna().all()


def test_features_for_t_ignore_bars_at_t_and_later() -> None:
    """Mutating prices and volume on bars >= T leaves every feature row dated <= T intact."""
    df = make_series()
    base = compute_features(df)
    t = 90
    mutated = df.copy()
    for c in ("open", "high", "low", "close", "adj_close"):
        mutated.loc[t:, c] = mutated.loc[t:, c] * 3.0
    mutated.loc[t:, "volume"] = 1.0
    out = compute_features(mutated)
    # Feature row dated df.date[t] uses bars 0..t-1 only.
    rows_upto_t = base["date"] <= df["date"].iloc[t]
    pd.testing.assert_frame_equal(base[rows_upto_t], out[rows_upto_t])
    # And something after T did change (the test is not vacuous).
    assert not base["close_prev"].iloc[t:].equals(out["close_prev"].iloc[t:])


def test_split_on_t_or_later_does_not_change_features_for_t() -> None:
    """A later split rescales earlier bars in the computation basis; features are invariant."""
    df = make_series()
    base = compute_features(df)
    t = 95
    split = apply_split(df, at=t, ratio=4.0)
    out = compute_features(split)
    rows_upto_t = base["date"] <= df["date"].iloc[t]
    pd.testing.assert_frame_equal(base[rows_upto_t], out[rows_upto_t], check_exact=False, rtol=1e-9)


def test_split_before_t_is_handled_consistently() -> None:
    """With a split inside the lookback, features equal those of the unsplit series
    (ratios) or the unsplit series scaled to post-split units (levels)."""
    df = make_series()
    base = compute_features(df)
    t = 70
    split = apply_split(df, at=t, ratio=2.0)
    out = compute_features(split)
    after = base["date"] > df["date"].iloc[t]
    for c in (
        "sma10_slope",
        "sma50_slope",
        "adr20",
        "prior_move_pct",
        "consol_depth",
        "consol_tightness",
    ):
        np.testing.assert_allclose(base.loc[after, c], out.loc[after, c], rtol=1e-9, equal_nan=True)
    np.testing.assert_allclose(base.loc[after, "sma50"] / 2, out.loc[after, "sma50"], rtol=1e-9)
    np.testing.assert_allclose(
        base.loc[after, "close_prev"] / 2, out.loc[after, "close_prev"], rtol=1e-9
    )


# --- basis and split requirements -----------------------------------------------------------


def test_refuses_as_traded_without_known_splits() -> None:
    df = make_series()
    df["split_ratio"] = np.nan
    with pytest.raises(FeatureError, match="attach splits"):
        compute_features(df)


def test_refuses_total_return_basis() -> None:
    df = make_series()
    df["price_basis"] = TOTAL_RETURN
    with pytest.raises(FeatureError, match="not supported"):
        compute_features(df)


def test_split_adjusted_input_used_as_is() -> None:
    df = make_series()
    sa = df.copy()
    sa["price_basis"] = SPLIT_ADJUSTED
    sa["split_ratio"] = np.nan  # Stooq-style: no split data, already adjusted
    f = compute_features(sa)
    assert (f["input_price_basis"] == SPLIT_ADJUSTED).all()
    pd.testing.assert_series_equal(f["adr20"], compute_features(df)["adr20"])


def test_attach_splits_then_compute_matches_native() -> None:
    native = apply_split(make_series(), at=70, ratio=2.0)
    raw_like = native.copy()
    raw_like["split_ratio"] = np.nan
    raw_like["adj_close"] = np.nan
    raw_like["adj_factor"] = np.nan
    raw_like["dividend"] = np.nan
    with pytest.raises(FeatureError):
        compute_features(raw_like)
    attached = attach_splits(raw_like, native)
    pd.testing.assert_frame_equal(
        compute_features(attached).drop(columns=["source"]),
        compute_features(native).drop(columns=["source"]),
    )


def test_attach_splits_refusals() -> None:
    native = apply_split(make_series(), at=70, ratio=2.0)
    target = native.copy()
    target["split_ratio"] = np.nan
    with pytest.raises(FeatureError, match="different tickers"):
        attach_splits(target, native.assign(ticker="OTHER"))
    with pytest.raises(FeatureError, match="does not cover"):
        attach_splits(target, native.iloc[10:])
    with pytest.raises(FeatureError, match="absent in target"):
        attach_splits(target.drop(index=[70]).reset_index(drop=True), native)
    nan_src = native.copy()
    nan_src.loc[5, "split_ratio"] = np.nan
    with pytest.raises(FeatureError, match="contains NaN"):
        attach_splits(target, nan_src)


# --- hand-checked values --------------------------------------------------------------------


def test_hand_checked_values() -> None:
    n = 80
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = np.concatenate([np.linspace(100, 150, 50), np.full(30, 148.0)])
    close[50:] += np.array([2, -2] * 15)  # 150/146 alternating
    high = close + 2
    low = close - 2
    low[70:] = close[70:] - 1  # ranges tighten in the last third
    high[70:] = close[70:] + 1
    df = pd.DataFrame(
        {
            "date": [d.date() for d in dates],
            "ticker": "HC",
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1e6,
            "adj_close": close,
            "adj_factor": 1.0,
            "split_ratio": 1.0,
            "dividend": 0.0,
            "source": "test",
            "price_basis": AS_TRADED,
        }
    )[NORMALIZED_COLUMNS]
    f = compute_features(df, FeatureParams(prior_move_window=63)).set_index("asof_date")
    last = f.loc[df["date"].iloc[-2]]  # row using bars 0..78
    assert last["close_prev"] == close[78]
    assert last["sma10"] == pytest.approx(close[69:79].mean())
    assert last["sma50"] == pytest.approx(close[29:79].mean())
    assert last["sma50_slope"] == pytest.approx(close[29:79].mean() / close[19:69].mean() - 1)
    assert last["adr20"] == pytest.approx(np.mean(high[59:79] / low[59:79] - 1))
    # Window bars 16..78: max 150 first occurs at index 49 (end of the ramp); min before it
    # is close[16].
    assert last["prior_move_pct"] == pytest.approx(150 / close[16] - 1)
    assert last["prior_move_peak_date"] == df["date"].iloc[49]
    assert last["consol_days"] == 29  # bars 50..78
    assert last["consol_depth"] == pytest.approx(1 - 146 / 150)
    assert last["consol_pct_below_high"] == pytest.approx(1 - close[78] / high[50:79].max())
    assert last["consol_higher_lows"] is True or last["consol_higher_lows"] == np.True_
    assert last["consol_tightness"] < 1.0  # last-third ranges are half the first-third ranges
    assert last["consol_closes_below_sma20"] >= 0


# --- storage ------------------------------------------------------------------------------------


def test_build_features_writes_parquet_and_meta(tmp_path: Path) -> None:
    from tradingbot.data.schema import FetchMeta
    from tradingbot.data.store import ParquetStore
    from tradingbot.scanner.pipeline import build_features, read_features

    store = ParquetStore(tmp_path)
    df = make_series()
    meta = FetchMeta(
        "test",
        "SYN",
        df["date"].iloc[0],
        df["date"].iloc[-1],
        datetime(2026, 1, 1, tzinfo=UTC),
        "x",
        "x",
    )
    meta.actual_start, meta.actual_end, meta.rows, meta.quality_status = (
        df["date"].iloc[0],
        df["date"].iloc[-1],
        len(df),
        "OK",
    )
    store.write_processed(meta, df)
    m = build_features(store, "test", "SYN", built_at=datetime(2026, 1, 2, tzinfo=UTC))
    f = read_features(store, "test", "SYN")
    assert len(f) == len(df) - 1
    assert m["input_price_basis"] == AS_TRADED and m["input_rows"] == len(df)
    assert m["input_coverage"] == [str(df["date"].iloc[0]), str(df["date"].iloc[-1])]
    assert m["feature_version"] == "1" and m["spec_version"] == "0.2.1"
    assert m["first_complete_date"] == str(df["date"].iloc[63])  # prior_move needs 63 bars
    assert "not evidence" in m["disclaimer"]
    assert (tmp_path / "features" / "test" / "SYN.meta.json").exists()
