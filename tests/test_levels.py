"""Step 3a: order levels in as-traded units (STEP3_DESIGN §2, §11 'Nivåer')."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.schema import SPLIT_ADJUSTED
from tradingbot.scanner.features import FeatureError, compute_features
from tradingbot.scanner.levels import (
    LevelsError,
    OrderLevels,
    breakout_triggered,
    compute_levels,
    compute_levels_table,
    gap_exceeds,
    stop_hit,
)
from tradingbot.scanner.rules import apply_rules

from .test_features import apply_split
from .test_rules import setup_series


def _last_candidate(df: pd.DataFrame) -> pd.Series:
    cands = apply_rules(compute_features(df))
    return cands[cands["is_candidate"].astype(bool)].iloc[-1]


def _window(df: pd.DataFrame, row: pd.Series) -> pd.DataFrame:
    return df[(df["date"] > row["prior_move_peak_date"]) & (df["date"] <= row["asof_date"])]


# --- values on as-traded prices ---


def test_levels_are_as_traded_window_high_and_prev_low() -> None:
    df = setup_series()
    row = _last_candidate(df)
    lv = compute_levels(df, row)
    win = _window(df, row)
    assert isinstance(lv, OrderLevels) and lv.units == "as_traded"
    assert lv.breakout_level_at == pytest.approx(win["high"].max())
    prev = df[df["date"] == row["asof_date"]].iloc[0]
    assert lv.low_prev_at == pytest.approx(prev["low"])
    assert lv.window_start == win["date"].iloc[0] and lv.window_end == row["asof_date"]
    assert lv.window_bars == len(win) == row["consol_days"]
    assert lv.peak_date == row["prior_move_peak_date"]
    assert lv.decision_date == row["date"] and lv.asof_date == row["asof_date"]
    assert lv.adr20 == pytest.approx(row["adr20"])
    assert lv.split_ratio_on_t == 1.0 and lv.split_on_entry_day is False
    assert lv.low_prev_at < lv.breakout_level_at


def test_levels_do_not_use_bar_t_prices() -> None:
    """Bar T's high above the window high must not change the level (only T-1 and earlier)."""
    df = setup_series()
    row = _last_candidate(df)
    t_idx = df.index[df["date"] == row["date"]][0]
    mutated = df.copy()
    mutated.loc[t_idx, "high"] = mutated["high"].max() * 2
    mutated.loc[t_idx, "low"] = mutated["low"].min() / 2
    assert compute_levels(mutated, row) == compute_levels(df, row)


# --- refusals ---


def test_refuses_split_adjusted_basis() -> None:
    from tradingbot.data.compare import rebase

    df = setup_series()
    row = _last_candidate(df)
    chart_series = rebase(df, SPLIT_ADJUSTED)
    with pytest.raises(FeatureError, match="as_traded"):
        compute_levels(chart_series, row)


def test_refuses_unknown_splits() -> None:
    df = setup_series()
    row = _last_candidate(df)
    df.loc[5, "split_ratio"] = np.nan
    with pytest.raises(LevelsError, match="split_ratio unknown"):
        compute_levels(df, row)


def test_refuses_missing_peak_and_bad_dates() -> None:
    df = setup_series()
    row = _last_candidate(df).copy()
    nopeak = row.copy()
    nopeak["prior_move_peak_date"] = None
    with pytest.raises(LevelsError, match="no prior_move_peak_date"):
        compute_levels(df, nopeak)
    late = row.copy()
    late["prior_move_peak_date"] = row["asof_date"]  # peak == asof -> empty window
    with pytest.raises(LevelsError, match="expected peak"):
        compute_levels(df, late)
    missing = row.copy()
    missing["asof_date"] = pd.Timestamp("2023-01-01").date()  # not a bar
    with pytest.raises(LevelsError):
        compute_levels(df, missing)


# --- splits ---


def test_split_on_t_divides_levels_and_flags() -> None:
    df = setup_series()
    row = _last_candidate(df)
    base = compute_levels(df, row)
    t_idx = df.index[df["date"] == row["date"]][0]
    split = apply_split(df, at=t_idx, ratio=4.0)
    lv = compute_levels(split, row)
    assert lv.split_ratio_on_t == 4.0 and lv.split_on_entry_day is True
    assert lv.breakout_level_at == pytest.approx(base.breakout_level_at / 4)
    assert lv.low_prev_at == pytest.approx(base.low_prev_at / 4)
    # The decision day before the split (T-1 as decision date) is unaffected.
    cands = apply_rules(compute_features(df))
    prev_row = cands[cands["date"] == df["date"].iloc[t_idx - 1]].iloc[0]
    assert compute_levels(split, prev_row) == compute_levels(df, prev_row)


def test_split_on_t_plus_one_changes_nothing_for_t() -> None:
    df = setup_series()
    row = _last_candidate(df)
    t_idx = df.index[df["date"] == row["date"]][0]
    if t_idx + 1 >= len(df):
        df = pd.concat([df, df.iloc[[-1]].assign(date=pd.Timestamp("2030-01-02").date())])
        df = df.reset_index(drop=True)
    later = apply_split(df, at=t_idx + 1, ratio=4.0)
    assert compute_levels(later, row) == compute_levels(df, row)


def test_split_inside_window_gives_post_split_units() -> None:
    """2:1 split five bars before T: level and low_prev in post-split units, = unsplit / 2."""
    df = setup_series()
    row = _last_candidate(df)
    base = compute_levels(df, row)
    t_idx = df.index[df["date"] == row["date"]][0]
    inside = apply_split(df, at=t_idx - 5, ratio=2.0)
    # Same candidate row (features are split-invariant) -> same window.
    row2 = apply_rules(compute_features(inside)).loc[row.name]
    assert row2["prior_move_peak_date"] == row["prior_move_peak_date"]
    lv = compute_levels(inside, row2)
    assert lv.breakout_level_at == pytest.approx(base.breakout_level_at / 2, rel=1e-9)
    assert lv.low_prev_at == pytest.approx(base.low_prev_at / 2, rel=1e-9)
    assert lv.split_on_entry_day is False
    # Naive max over raw as-traded highs would be wrong (pre-split bars twice as high).
    naive = _window(inside, row2)["high"].max()
    assert naive > lv.breakout_level_at * 1.5


# --- look-ahead: mutate T and later ---


def test_future_mutations_leave_levels_unchanged() -> None:
    df = setup_series()
    cands = apply_rules(compute_features(df))
    rows = cands[cands["is_candidate"].astype(bool)]
    base = [compute_levels(df, r) for _, r in rows.iterrows()]
    t0 = rows["date"].iloc[0]
    mutated = df.copy()
    at = mutated["date"] >= t0
    for c in ("open", "high", "low", "close", "adj_close"):
        mutated.loc[at, c] = mutated.loc[at, c] * 3.0
    mutated.loc[at, "volume"] = 1.0
    # Only the first candidate row's levels are fully before the mutation; check that one
    # exactly, and that a split placed after T0 changes nothing for T0 either.
    assert compute_levels(mutated, rows.iloc[0]) == base[0]
    t0_idx = df.index[df["date"] == t0][0]
    assert compute_levels(apply_split(df, at=t0_idx + 2, ratio=3.0), rows.iloc[0]) == base[0]


# --- table and operators ---


def test_levels_table_marks_rows_without_peak() -> None:
    df = setup_series()
    cands = apply_rules(compute_features(df))
    table = compute_levels_table(df, cands)
    assert len(table) == len(cands)
    early = table.iloc[:60]
    assert early["breakout_level_at"].isna().all() and early["levels_error"].notna().all()
    last = table.iloc[-1]
    assert pd.isna(last["levels_error"]) and last["units"] == "as_traded"
    assert last["breakout_level_at"] == pytest.approx(
        compute_levels(df, cands.iloc[-1]).breakout_level_at
    )


@pytest.mark.parametrize(
    "high, level, expected",
    [(100.0, 100.0, False), (100.01, 100.0, True), (99.99, 100.0, False)],
)
def test_breakout_requires_strictly_above(high, level, expected) -> None:
    assert breakout_triggered(high, level) is expected


@pytest.mark.parametrize("low, stop, expected", [(95.0, 95.0, True), (95.01, 95.0, False)])
def test_stop_touch_counts(low, stop, expected) -> None:
    assert stop_hit(low, stop) is expected


def test_gap_boundary() -> None:
    assert gap_exceeds(105.0, 100.0, 0.05) is False  # exactly at the limit: allowed
    assert gap_exceeds(105.01, 100.0, 0.05) is True
    assert gap_exceeds(99.0, 100.0, 0.05) is False
