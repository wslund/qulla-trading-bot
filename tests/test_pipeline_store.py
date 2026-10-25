"""Pipeline (without network) and Parquet store round trip."""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from tradingbot.data.compare import compare_series
from tradingbot.data.pipeline import get_provider, process
from tradingbot.data.providers.base import RawBars
from tradingbot.data.providers.yahoo import YahooProvider
from tradingbot.data.schema import NORMALIZED_COLUMNS
from tradingbot.data.store import ParquetStore


def test_process_writes_raw_processed_and_meta(tmp_path: Path, yahoo_raw: RawBars) -> None:
    store = ParquetStore(tmp_path)
    fetched = datetime(2026, 1, 1, tzinfo=UTC)
    meta = process(
        YahooProvider(), yahoo_raw, store, date(2024, 3, 1), date(2024, 3, 31), fetched_at=fetched
    )
    assert store.raw_path("yahoo", "SYN").exists()
    assert store.processed_path("yahoo", "SYN").exists()

    back = store.read_processed("yahoo", "SYN")
    assert list(back.columns) == NORMALIZED_COLUMNS
    assert len(back) == 12

    m = store.read_meta("yahoo", "SYN")
    assert m["source"] == "yahoo"
    assert m["ticker"] == "SYN"
    assert m["requested_start"] == "2024-03-01"
    assert m["actual_start"] == "2024-03-04"
    assert m["actual_end"] == "2024-03-19"
    assert m["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert m["quality_status"] == "OK"
    assert m["rows"] == 12
    assert "split" in m["adjustment_method"].lower()
    assert meta.quality_status == "OK"


def test_process_empty_frame_is_fail(tmp_path: Path) -> None:
    raw = RawBars("yahoo", "GONE", pd.DataFrame(), "fixture")
    meta = process(YahooProvider(), raw, ParquetStore(tmp_path), date(2020, 1, 1), date(2020, 2, 1))
    assert meta.quality_status == "FAIL"
    assert meta.rows == 0
    assert "no rows" in meta.quality_issues[0]


def test_get_provider() -> None:
    assert isinstance(get_provider("yahoo"), YahooProvider)


def test_compare_identical_and_perturbed(normalized: pd.DataFrame) -> None:
    same = compare_series(normalized, normalized)
    assert same.common_days == 12 and same.only_in_a == 0 and same.only_in_b == 0
    assert same.close_max_rel_diff == 0.0 and same.close_days_over_tol == 0

    other = normalized.copy()
    other["source"] = "other"
    other.loc[2, "close"] *= 1.02
    other = other.drop(index=[11])
    rep = compare_series(normalized, other, tol=0.005)
    assert rep.common_days == 11 and rep.only_in_a == 1 and rep.only_in_b == 0
    assert rep.close_days_over_tol == 1
    assert rep.dates_over_tol == ["2024-03-06"]
    assert 0.019 < rep.close_max_rel_diff < 0.021


def test_quality_report_from_meta(tmp_path: Path, yahoo_raw: RawBars) -> None:
    from tradingbot.data.report import quality_rows, render_markdown

    store = ParquetStore(tmp_path)
    process(YahooProvider(), yahoo_raw, store, date(2024, 3, 1), date(2024, 3, 31))
    process(
        YahooProvider(),
        RawBars("yahoo", "GONE", pd.DataFrame(), "fixture"),
        store,
        date(2024, 3, 1),
        date(2024, 3, 31),
    )
    rows = quality_rows(store, "yahoo")
    assert [r["ticker"] for r in rows] == ["GONE", "SYN"]
    assert {r["status"] for r in rows} == {"FAIL", "OK"}
    md = render_markdown(store)
    assert "## yahoo (FAIL=1, OK=1)" in md
    assert "| SYN | 12 | 2024-03-04 | 2024-03-19 | OK | - |" in md
    assert store.list_sources() == ["yahoo"]


def test_schema_v1_file_is_rejected(tmp_path: Path, normalized: pd.DataFrame) -> None:
    store = ParquetStore(tmp_path)
    p = store.processed_path("yahoo", "OLD")
    p.parent.mkdir(parents=True)
    normalized.drop(columns=["price_basis"]).to_parquet(p, index=False)
    import pytest

    with pytest.raises(ValueError, match="re-run ingest"):
        store.read_processed("yahoo", "OLD")
