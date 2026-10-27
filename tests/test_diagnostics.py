"""Setup diagnostics: consistent with stored features, T-1 safe, grouping, chart output."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.schema import FetchMeta
from tradingbot.data.store import ParquetStore
from tradingbot.scanner.diagnostics import (
    add_group_diagnostics,
    extra_measures,
    prior_move_detail,
)
from tradingbot.scanner.features import compute_features
from tradingbot.scanner.pipeline import build_candidates, build_features, read_candidates
from tradingbot.scanner.rules import RULE_NAMES, apply_rules

from .test_features import apply_split
from .test_rules import setup_series


def _cands(df: pd.DataFrame) -> pd.DataFrame:
    return add_group_diagnostics(apply_rules(compute_features(df)))


def test_prior_move_detail_matches_stored_feature() -> None:
    df = setup_series()
    cands = _cands(df)
    for _, row in cands.iloc[70:].iterrows():
        pm = prior_move_detail(df, row)
        assert pm.consistent, (row["date"], pm)
        assert pm.peak_date == row["prior_move_peak_date"]
        assert pm.start_date < pm.peak_date <= row["asof_date"]
        assert pm.pct == pytest.approx(row["prior_move_pct"])


def test_prior_move_detail_identifies_start_and_peak_on_synthetic_run() -> None:
    df = setup_series()
    row = _cands(df).iloc[-1]
    pm = prior_move_detail(df, row)
    # Start = lowest close inside the 63-bar window before the peak (not the global low).
    win = df[(df["date"] >= pm.window_start) & (df["date"] < pm.peak_date)]
    assert pm.start_date == win.loc[win["close"].idxmin(), "date"]
    assert pm.start_close == pytest.approx(win["close"].min())
    assert pm.peak_close == pytest.approx(df["close"].iloc[89])
    assert pm.pct == pytest.approx(0.45, rel=0.02)


def test_diagnostics_ignore_bars_at_t_and_later() -> None:
    df = setup_series()
    row = _cands(df).iloc[-1]
    t = row["date"]
    mutated = df.copy()
    at = mutated["date"] >= t
    for c in ("open", "high", "low", "close", "adj_close"):
        mutated.loc[at, c] = mutated.loc[at, c] * 5  # a huge move from T on
    a, b = prior_move_detail(df, row), prior_move_detail(mutated, row)
    assert (a.start_date, a.peak_date, a.pct) == (b.start_date, b.peak_date, b.pct)
    assert extra_measures(df, row) == extra_measures(mutated, row)


def test_extra_measures_values() -> None:
    df = setup_series()
    row = _cands(df).iloc[-1]
    m = extra_measures(df, row)
    assert m["close_vs_sma10"] == pytest.approx(row["close_prev"] / row["sma10"] - 1)
    closes = df["close"].to_numpy()
    i = int(np.flatnonzero(df["date"].to_numpy() == row["asof_date"])[0])
    assert m["ret_5d"] == pytest.approx(closes[i] / closes[i - 5] - 1)


def test_extra_measures_missing_when_history_short() -> None:
    df = setup_series().iloc[:4]
    feats = compute_features(df)
    row = apply_rules(feats).iloc[-1]
    m = extra_measures(df, row)
    assert m["ret_5d"] is None and m["close_vs_sma10"] is None


def test_group_consecutive_candidates_with_same_peak() -> None:
    df = setup_series()
    cands = _cands(df)
    c = cands[cands["is_candidate"].astype(bool)]
    assert len(c) >= 3
    assert c["setup_group_id"].nunique() == 1  # one consolidation seen on successive days
    assert list(c["group_rank"]) == list(range(1, len(c) + 1))
    assert (c["group_size"] == len(c)).all()
    assert cands.loc[~cands["is_candidate"].astype(bool), "setup_group_id"].isna().all()
    # Rule columns and candidate flags are untouched by grouping.
    plain = apply_rules(compute_features(df))
    cols = [f"rule_{n}" for n in RULE_NAMES] + ["is_candidate", "rules_passed", "candidate_id"]
    pd.testing.assert_frame_equal(plain[cols], cands[cols])


def test_group_splits_when_peak_changes_or_run_breaks() -> None:
    df = setup_series()
    cands = apply_rules(compute_features(df))
    idx = cands.index[cands["is_candidate"].astype(bool)]
    # Break the run in the middle and change the peak date for the tail.
    mid = idx[len(idx) // 2]
    cands.loc[mid, "is_candidate"] = False
    cands.loc[idx[-1], "prior_move_peak_date"] = cands.loc[idx[0], "prior_move_peak_date"]
    cands.loc[idx[-1], "prior_move_peak_date"] = pd.Timestamp("2000-01-01").date()
    g = add_group_diagnostics(cands)
    ids = g.loc[g["is_candidate"].astype(bool), "setup_group_id"]
    assert ids.nunique() == 3  # before the break, after the break, and the odd-peak tail
    assert g.loc[mid, "setup_group_id"] is None or pd.isna(g.loc[mid, "setup_group_id"])


def test_chart_includes_diagnostics(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from tradingbot.scanner.plot import plot_setup

    df = apply_split(setup_series(), at=75, ratio=2.0)
    store = ParquetStore(tmp_path)
    m = FetchMeta(
        "test",
        "SET",
        df["date"].iloc[0],
        df["date"].iloc[-1],
        datetime(2026, 1, 1, tzinfo=UTC),
        "x",
        "x",
    )
    m.actual_start, m.actual_end, m.rows, m.quality_status = (
        df["date"].iloc[0],
        df["date"].iloc[-1],
        len(df),
        "OK",
    )
    store.write_processed(m, df)
    build_features(store, "test", "SET")
    meta = build_candidates(store, "test", "SET")
    assert meta["setup_groups"] == 1 and meta["diagnostics_version"] == "1"
    cands = read_candidates(store, "test", "SET")
    row = cands[cands["is_candidate"].astype(bool)].iloc[-1]
    info = plot_setup(store.read_processed("test", "SET"), row, tmp_path / "d.png")
    assert info.path.exists()
    assert info.prior_move_consistent and info.prior_move_start is not None
    assert info.prior_move_pct_recomputed == pytest.approx(row["prior_move_pct"])
    assert info.close_vs_sma10 is not None and info.ret_5d is not None
    assert info.group_id == row["setup_group_id"] and info.group_id.startswith("SET:peak")
