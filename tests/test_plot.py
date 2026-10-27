"""Setup charts: files are written, setup marks use only bars <= T-1, near misses supported."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.data.schema import FetchMeta
from tradingbot.data.store import ParquetStore
from tradingbot.scanner.pipeline import build_candidates, build_features, read_candidates
from tradingbot.scanner.plot import plot_setup, select_rows

from .test_features import apply_split
from .test_rules import setup_series

pytest.importorskip("matplotlib")


def _store(tmp_path: Path, df: pd.DataFrame) -> tuple[ParquetStore, pd.DataFrame]:
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
    build_candidates(store, "test", "SET")
    return store, read_candidates(store, "test", "SET")


def test_candidate_chart_is_written_with_setup_marks(tmp_path: Path) -> None:
    df = setup_series()
    store, cands = _store(tmp_path, df)
    row = cands[cands["is_candidate"]].iloc[-1]
    out = tmp_path / "plots" / "SET_cand.png"
    info = plot_setup(store.read_processed("test", "SET"), row, out, lookback=80, lookahead=10)
    assert out.exists() and out.stat().st_size > 20_000
    assert info.is_candidate and info.rules_passed == info.rules_total == 9
    assert info.asof_date < info.decision_date
    # Setup marks never use bars at or after T.
    assert info.sma_last_date == info.asof_date
    assert info.consol_end == info.asof_date
    assert info.peak_date == row["prior_move_peak_date"] and info.peak_date < info.consol_start
    assert info.bars_before == 80
    assert info.bars_after <= 10
    series = store.read_processed("test", "SET")
    win = series[(series["date"] >= info.consol_start) & (series["date"] <= info.consol_end)]
    assert info.breakout_level == pytest.approx(win["high"].max())


def test_decision_on_last_bar_has_no_context_bars(tmp_path: Path) -> None:
    df = setup_series()
    store, cands = _store(tmp_path, df)
    row = cands.iloc[-1]
    info = plot_setup(store.read_processed("test", "SET"), row, tmp_path / "last.png")
    assert info.bars_after == 1  # only the filler bar T itself exists after T-1
    assert info.sma_last_date == info.asof_date


def test_near_miss_chart_and_selection(tmp_path: Path) -> None:
    df = setup_series(tighten=False)  # fails exactly the tightness rule on the last date
    store, cands = _store(tmp_path, df)
    last_date = cands["date"].iloc[-1]
    assert select_rows(cands, start=last_date, end=last_date).empty
    near = select_rows(cands, start=last_date, end=last_date, near_misses=True)
    assert len(near) == 1 and not bool(near.iloc[0]["is_candidate"])
    info = plot_setup(store.read_processed("test", "SET"), near.iloc[0], tmp_path / "miss.png")
    assert info.path.exists() and not info.is_candidate
    assert info.rules_passed == info.rules_total - 1


def test_explicit_dates_selection(tmp_path: Path) -> None:
    df = setup_series()
    _, cands = _store(tmp_path, df)
    d = cands["date"].iloc[70]
    sel = select_rows(cands, dates=[d])
    assert len(sel) == 1 and sel.iloc[0]["date"] == d


def test_chart_continuous_across_split(tmp_path: Path) -> None:
    """A 4:1 split inside the lookback: chart uses split-adjusted prices, marks unchanged."""
    df = apply_split(setup_series(), at=len(setup_series()) - 35, ratio=4.0)
    store, cands = _store(tmp_path, df)
    row = cands[cands["is_candidate"]].iloc[-1]
    info = plot_setup(store.read_processed("test", "SET"), row, tmp_path / "split.png")
    assert info.path.exists() and info.is_candidate
    # breakout level is in chart (split-adjusted) units: post-split, so ~1/4 of the unsplit
    unsplit_store, unsplit_cands = _store(tmp_path / "u", setup_series())
    u = plot_setup(
        unsplit_store.read_processed("test", "SET"),
        unsplit_cands[unsplit_cands["is_candidate"]].iloc[-1],
        tmp_path / "u.png",
    )
    assert info.breakout_level == pytest.approx(u.breakout_level / 4, rel=1e-6)
    assert info.consol_start == u.consol_start and info.peak_date == u.peak_date
