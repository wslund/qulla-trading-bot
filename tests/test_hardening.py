"""Regression tests for the data-pilot hardening: Alpaca end capping, jump classification,
volume-ratio statistics."""

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.compare import compare_series
from tradingbot.data.providers.alpaca import AlpacaProvider
from tradingbot.data.quality import INFO, OK, WARN, check_quality
from tradingbot.data.schema import AS_TRADED


def _alpaca_at(now_utc: datetime) -> AlpacaProvider:
    return AlpacaProvider("k", "s", now=lambda: now_utc)


# --- 1. Alpaca end handling ---------------------------------------------------------------


def test_end_today_is_capped_to_yesterday() -> None:
    p = _alpaca_at(datetime(2026, 10, 26, 15, 0, tzinfo=UTC))  # 11:00 New York, 2026-10-26
    end, note = p.effective_end(date(2026, 10, 26))
    assert end == date(2026, 10, 25)
    assert "capped" in note


def test_end_in_future_is_capped() -> None:
    p = _alpaca_at(datetime(2026, 10, 26, 15, 0, tzinfo=UTC))
    assert p.effective_end(date(2027, 1, 1))[0] == date(2026, 10, 25)


def test_explicit_past_end_unchanged() -> None:
    p = _alpaca_at(datetime(2026, 10, 26, 15, 0, tzinfo=UTC))
    end, note = p.effective_end(date(2026, 10, 23))
    assert end == date(2026, 10, 23) and note == ""


def test_timezone_edge_after_utc_midnight_before_ny_midnight() -> None:
    """01:00 UTC on the 27th is still 21:00 New York on the 26th: today in NY is the 26th."""
    p = _alpaca_at(datetime(2026, 10, 27, 1, 0, tzinfo=UTC))
    assert p.effective_end(date(2026, 10, 27))[0] == date(2026, 10, 25)
    assert p.effective_end(date(2026, 10, 26))[0] == date(2026, 10, 25)
    assert p.effective_end(date(2026, 10, 25))[0] == date(2026, 10, 25)


def test_timezone_edge_just_after_ny_midnight() -> None:
    p = _alpaca_at(datetime(2026, 10, 27, 4, 5, tzinfo=UTC))  # 00:05 New York on the 27th
    assert p.effective_end(date(2026, 10, 27))[0] == date(2026, 10, 26)


def test_dst_boundary_uses_new_york_offset() -> None:
    # 2026-11-01 02:00 New York DST ends; 2026-11-02 04:30 UTC is 23:30 NY on 11-01 (EST).
    p = _alpaca_at(datetime(2026, 11, 2, 4, 30, tzinfo=UTC))
    assert p.effective_end(date(2026, 11, 2))[0] == date(2026, 10, 31)


def test_capped_end_before_start_returns_empty_without_network(monkeypatch) -> None:
    p = _alpaca_at(datetime(2026, 10, 26, 15, 0, tzinfo=UTC))
    calls = []
    monkeypatch.setattr(p, "_get", lambda *a, **k: calls.append(1))
    raw = p.fetch_daily("AAPL", date(2026, 10, 26), date(2026, 10, 26))
    assert raw.frame.empty and calls == [] and "capped" in raw.notes


def test_capping_note_reaches_metadata(monkeypatch, tmp_path) -> None:
    from tradingbot.data.pipeline import process
    from tradingbot.data.store import ParquetStore

    p = _alpaca_at(datetime(2026, 10, 26, 15, 0, tzinfo=UTC))
    monkeypatch.setattr(p, "_get", lambda *a, **k: {"bars": [], "next_page_token": None})
    raw = p.fetch_daily("AAPL", date(2026, 10, 1), date(2026, 10, 26))
    meta = process(p, raw, ParquetStore(tmp_path), date(2026, 10, 1), date(2026, 10, 26))
    assert "capped 2026-10-26 -> 2026-10-25" in meta.notes


# --- 4. Jump classification ---------------------------------------------------------------


def _series(closes: list[float], volumes: list[float], split_known: bool = True) -> pd.DataFrame:
    dates = pd.bdate_range("2023-03-01", periods=len(closes))
    c = np.array(closes, float)
    return pd.DataFrame(
        {
            "date": [d.date() for d in dates],
            "ticker": "T",
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": np.array(volumes, float),
            "adj_close": c if split_known else np.nan,
            "adj_factor": 1.0 if split_known else np.nan,
            "split_ratio": 1.0 if split_known else np.nan,
            "dividend": 0.0 if split_known else np.nan,
            "source": "test",
            "price_basis": AS_TRADED,
        }
    )


def test_crash_is_info_not_warn() -> None:
    """A -60% day on surging volume (SIVB 2023-03-09 style) is a real move: INFO only."""
    df = _series([100, 101, 100, 40, 38], [1e6, 1e6, 1e6, 2.5e7, 2e7], split_known=False)
    rep = check_quality(df)
    assert rep.status == OK
    assert any(i.startswith(INFO) and "large moves" in i and "-60%" in i for i in rep.issues)
    assert not any(i.startswith(WARN) for i in rep.issues)


def test_unreported_split_is_warn() -> None:
    df = _series([100, 101, 100, 50, 51], [1e6, 1e6, 1e6, 2.2e6, 2e6], split_known=False)
    rep = check_quality(df)
    assert rep.status == WARN
    assert any("split-like jumps, source has no split data" in i for i in rep.issues)


def test_reverse_split_is_warn() -> None:
    df = _series([1.0, 1.02, 1.0, 10.0, 10.2], [5e7, 5e7, 5e7, 4e6, 4.5e6])
    rep = check_quality(df)
    assert rep.status == WARN
    assert any("split-like" in i for i in rep.issues)


def test_halving_on_surging_volume_is_not_split_like() -> None:
    """Price halves but volume collapses: not a split pattern -> INFO."""
    df = _series([100, 101, 100, 50, 51], [1e6, 1e6, 1e6, 5e5, 4e5])
    rep = check_quality(df)
    assert rep.status == OK
    assert any(i.startswith(INFO) for i in rep.issues)


# --- 3. Volume ratio statistics -----------------------------------------------------------


def test_volume_ratio_stats_show_systematic_factor() -> None:
    a = _series([10, 11, 12, 13, 14, 15], [100, 200, 300, 400, 500, 600])
    b = a.copy()
    b["source"] = "b"
    b["volume"] = a["volume"] * 0.92  # b consistently reports 8% less volume
    rep = compare_series(a, b, basis=AS_TRADED)
    assert rep.volume_ratio_median == pytest.approx(0.92)
    assert rep.volume_ratio_iqr[0] == pytest.approx(0.92)
    assert rep.volume_ratio_iqr[1] == pytest.approx(0.92)
    assert rep.volume_median_rel_diff == pytest.approx(0.08 / 0.92)
    assert "volB/volA median=0.920" in rep.summary()
