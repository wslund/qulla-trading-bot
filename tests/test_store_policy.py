"""Write policy: upsert by date never shrinks a stored series; raw files are append-only."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.data.compare import compare_series
from tradingbot.data.pipeline import process
from tradingbot.data.providers.base import RawBars
from tradingbot.data.providers.yahoo import YahooProvider
from tradingbot.data.schema import AS_TRADED
from tradingbot.data.store import ParquetStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _raw_slice(yahoo_raw: RawBars, lo: int, hi: int, bump: float = 1.0) -> RawBars:
    f = yahoo_raw.frame.iloc[lo:hi].copy()
    for c in ("Open", "High", "Low", "Close", "Adj Close"):
        f[c] = f[c] * bump
    return RawBars("yahoo", "SYN", f, "fixture")


def _full(tmp_path: Path, yahoo_raw: RawBars) -> ParquetStore:
    store = ParquetStore(tmp_path)
    process(YahooProvider(), yahoo_raw, store, date(2024, 3, 1), date(2024, 3, 31), fetched_at=T0)
    return store


def test_short_fetch_does_not_shrink_stored_series(tmp_path, yahoo_raw) -> None:
    store = _full(tmp_path, yahoo_raw)
    assert len(store.read_processed("yahoo", "SYN")) == 12
    meta = process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 9, 12),
        store,
        date(2024, 3, 15),
        date(2024, 3, 19),
        fetched_at=T1,
    )
    df = store.read_processed("yahoo", "SYN")
    assert len(df) == 12
    assert meta.fetch_rows == 3 and meta.rows == 12
    assert meta.actual_start == date(2024, 3, 4) and meta.actual_end == date(2024, 3, 19)
    assert len(meta.fetch_history) == 2
    assert meta.fetch_history[0].startswith("2024-03-01..2024-03-31 fetched 2026-01-01")
    assert meta.fetch_history[1].startswith("2024-03-15..2024-03-19 fetched 2026-01-02")
    m = store.read_meta("yahoo", "SYN")
    assert m["rows"] == 12 and m["fetch_rows"] == 3 and m["write_mode"] == "upsert"


def test_overlapping_rows_are_replaced_by_newer_fetch(tmp_path, yahoo_raw) -> None:
    store = _full(tmp_path, yahoo_raw)
    before = store.read_processed("yahoo", "SYN")
    process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 9, 12, bump=1.10),
        store,
        date(2024, 3, 15),
        date(2024, 3, 19),
        fetched_at=T1,
    )
    after = store.read_processed("yahoo", "SYN")
    assert len(after) == 12 and after["date"].is_monotonic_increasing
    assert not after["date"].duplicated().any()
    pd.testing.assert_frame_equal(after.iloc[:9].reset_index(drop=True), before.iloc[:9])
    assert (after["close"].iloc[9:].to_numpy() > before["close"].iloc[9:].to_numpy() * 1.09).all()


def test_extending_forward_and_backward(tmp_path, yahoo_raw) -> None:
    store = ParquetStore(tmp_path)
    process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 4, 8),
        store,
        date(2024, 3, 8),
        date(2024, 3, 13),
        fetched_at=T0,
    )
    process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 0, 4),
        store,
        date(2024, 3, 4),
        date(2024, 3, 7),
        fetched_at=T1,
    )
    meta = process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 8, 12),
        store,
        date(2024, 3, 14),
        date(2024, 3, 19),
        fetched_at=T1,
    )
    df = store.read_processed("yahoo", "SYN")
    assert len(df) == 12 and list(df["date"]) == list(YahooProvider().normalize(yahoo_raw)["date"])
    assert meta.rows == 12 and len(meta.fetch_history) == 3


def test_replace_mode_discards_stored_series(tmp_path, yahoo_raw) -> None:
    store = _full(tmp_path, yahoo_raw)
    meta = process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 9, 12),
        store,
        date(2024, 3, 15),
        date(2024, 3, 19),
        fetched_at=T1,
        write_mode="replace",
    )
    assert len(store.read_processed("yahoo", "SYN")) == 3
    assert meta.rows == 3 and meta.write_mode == "replace" and len(meta.fetch_history) == 1


def test_invalid_write_mode(tmp_path, yahoo_raw) -> None:
    with pytest.raises(ValueError, match="write_mode"):
        process(
            YahooProvider(),
            yahoo_raw,
            ParquetStore(tmp_path),
            date(2024, 3, 1),
            date(2024, 3, 31),
            write_mode="append",
        )


def test_empty_fetch_keeps_stored_series(tmp_path, yahoo_raw) -> None:
    store = _full(tmp_path, yahoo_raw)
    meta = process(
        YahooProvider(),
        RawBars("yahoo", "SYN", pd.DataFrame(), "fixture"),
        store,
        date(2026, 10, 1),
        date(2026, 10, 26),
        fetched_at=T1,
    )
    assert len(store.read_processed("yahoo", "SYN")) == 12
    assert meta.rows == 12 and meta.fetch_rows == 0 and meta.quality_status == "OK"
    assert "stored series kept" in meta.notes


def test_price_basis_mismatch_refuses_merge(tmp_path, yahoo_raw) -> None:
    store = _full(tmp_path, yahoo_raw)
    new = YahooProvider().normalize(_raw_slice(yahoo_raw, 9, 12))
    new["price_basis"] = "split_adjusted"
    from tradingbot.data.schema import FetchMeta

    meta = FetchMeta("yahoo", "SYN", date(2024, 3, 15), date(2024, 3, 19), T1, "x", "x")
    with pytest.raises(ValueError, match="price_basis"):
        store.merge_processed(meta, new)
    meta.write_mode = "replace"
    assert len(store.merge_processed(meta, new)) == 3


def test_schema_v1_stored_series_gets_replace_hint(tmp_path, normalized) -> None:
    store = ParquetStore(tmp_path)
    p = store.processed_path("yahoo", "SYN")
    p.parent.mkdir(parents=True)
    normalized.drop(columns=["price_basis"]).to_parquet(p, index=False)
    from tradingbot.data.schema import FetchMeta

    meta = FetchMeta("yahoo", "SYN", date(2024, 3, 1), date(2024, 3, 31), T0, "x", "x")
    with pytest.raises(ValueError, match="replace"):
        store.merge_processed(meta, normalized)


def test_raw_files_are_append_only(tmp_path, yahoo_raw) -> None:
    store = _full(tmp_path, yahoo_raw)
    process(
        YahooProvider(),
        _raw_slice(yahoo_raw, 9, 12),
        store,
        date(2024, 3, 15),
        date(2024, 3, 19),
        fetched_at=T1,
    )
    files = sorted(store.raw_dir("yahoo", "SYN").glob("*.parquet"))
    assert [f.name for f in files] == [
        "2024-03-01_2024-03-31_20260101T000000Z.parquet",
        "2024-03-15_2024-03-19_20260102T000000Z.parquet",
    ]
    assert len(pd.read_parquet(files[0])) == 12 and len(pd.read_parquet(files[1])) == 3
    assert store.raw_path("yahoo", "SYN").name.startswith("2024-03-15")


def test_compare_summary_states_date_ranges(normalized) -> None:
    short = normalized.iloc[-3:].copy()
    short["source"] = "b"
    rep = compare_series(normalized, short, basis=AS_TRADED)
    assert rep.common_days == 3
    assert rep.first_common == "2024-03-15" and rep.last_common == "2024-03-19"
    assert rep.range_a == ("2024-03-04", "2024-03-19") and rep.range_b == (
        "2024-03-15",
        "2024-03-19",
    )
    assert "common=3 days 2024-03-15..2024-03-19 (A stored 2024-03-04..2024-03-19" in rep.summary()
