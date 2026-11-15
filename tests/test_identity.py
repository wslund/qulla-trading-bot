"""D-025: a ticker-keyed price series must be tied to exactly one instrument before any
feature, level or candidate is computed from it. Deterministic, offline, synthetic rows."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from tradingbot.backtesting.portfolio import run_portfolio
from tradingbot.backtesting.universe import AmbiguousTicker, PilotUniverse, PointInTimeUniverse
from tradingbot.data.schema import FetchMeta
from tradingbot.data.store import ParquetStore
from tradingbot.data.universe_model import (
    EXACT,
    IDENTITY_GIVEN,
    IDENTITY_PILOT,
    IDENTITY_RESOLVED,
    REGISTER,
    AmbiguousOwnerError,
    Identifier,
    Instrument,
    Listing,
    MixedSeriesError,
    TickerSpan,
    UniverseModel,
    UnresolvedSeriesError,
    pilot_identity,
)
from tradingbot.scanner.features import compute_features
from tradingbot.scanner.pipeline import build_candidates, build_features, read_features

from .test_portfolio import params, universe
from .test_trades import RISE_THEN_FADE

OBS = datetime(2000, 1, 1, tzinfo=UTC)
D = date


def _dates(series: pd.DataFrame) -> list[date]:
    return [pd.Timestamp(d).date() for d in series["date"]]


def _model(version: str = "id-1") -> UniverseModel:
    return UniverseModel(version=version)


def _add(m: UniverseModel, iid: str, frm: date, to: date | None, spans: list[tuple]) -> None:
    m.instruments.append(Instrument(iid, f"Synthetic {iid}", "common_stock", "US", "test", OBS))
    m.listings.append(Listing(iid, "XTST", frm, to, REGISTER, "test", OBS))
    for ticker, vf, vt in spans:
        m.ticker_spans.append(TickerSpan(iid, ticker, vf, vt, "test", OBS))
    m.identifiers.append(Identifier(iid, "figi", f"BBG{iid}", None, None, EXACT, "test", OBS))


def _store_series(tmp_path, series: dict[str, pd.DataFrame]) -> ParquetStore:
    store = ParquetStore(tmp_path)
    for t, s in series.items():
        meta = FetchMeta(
            source="test",
            ticker=t,
            requested_start=s["date"].iloc[0],
            requested_end=s["date"].iloc[-1],
            fetched_at=datetime.now(UTC),
            provider_version="x",
            adjustment_method="none",
            actual_start=s["date"].iloc[0],
            actual_end=s["date"].iloc[-1],
            rows=len(s),
            fetch_rows=len(s),
        )
        meta.quality_status = "OK"
        store.write_processed(meta, s)
    return store


# --- model level -------------------------------------------------------------------------------


def test_reused_ticker_series_spanning_two_owners_is_refused():
    series, _, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    mid = ds[len(ds) // 2]
    m = _model()
    _add(m, "I-OLD", ds[0], mid - timedelta(days=1), [("AAA", ds[0], mid)])
    _add(m, "I-NEW", mid, None, [("AAA", mid, None)])
    assert [i for i in m.validate() if i.level == "FAIL"] == []  # disjoint spans are legal …
    with pytest.raises(MixedSeriesError, match="2 instruments"):  # … the series is not
        m.identify_series("AAA", ds)
    # declaring either owner does not make the other owner's bars disappear
    with pytest.raises(MixedSeriesError, match="I-OLD"):
        m.identify_series("AAA", ds, instrument_id="I-NEW")
    with pytest.raises(MixedSeriesError, match="I-NEW"):
        m.identify_series("AAA", ds, instrument_id="I-OLD")
    # each owner's own slice is fine
    old_part = [d for d in ds if d < mid]
    assert m.identify_series("AAA", old_part).instrument_id == "I-OLD"
    assert m.identify_series("AAA", [d for d in ds if d >= mid]).instrument_id == "I-NEW"


def test_ticker_change_resolves_to_one_instrument_with_unowned_prefix():
    series, _, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    change = ds[10]
    m = _model()
    # vendor keys the whole series by today's symbol AAA; before `change` it traded as OLD
    _add(m, "I-X", ds[0], None, [("OLD", ds[0], change), ("AAA", change, None)])
    ident = m.identify_series("AAA", ds)
    assert ident.instrument_id == "I-X" and ident.method == IDENTITY_RESOLVED
    assert ident.owners_seen == ("I-X",) and ident.unowned_dates == 10
    assert "10 bar(s) without an owner" in ident.note
    assert ident.first_date == ds[0] and ident.last_date == ds[-1]
    given = m.identify_series("AAA", ds, instrument_id="I-X")
    assert given.method == IDENTITY_GIVEN and given.instrument_id == "I-X"


def test_overlapping_spans_are_ambiguous_never_a_choice():
    series, _, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    m = _model()
    _add(m, "I-1", ds[0], None, [("AAA", ds[0], None)])
    _add(m, "I-2", ds[0], None, [("AAA", ds[5], ds[8])])
    assert any(i.level == "FAIL" and i.table == "ticker_spans" for i in m.validate())
    with pytest.raises(AmbiguousOwnerError, match=str(ds[5])):
        m.identify_series("AAA", ds)
    with pytest.raises(AmbiguousOwnerError):
        m.identify_series("AAA", ds, instrument_id="I-1")
    assert m.identify_series("AAA", ds[:5]).instrument_id == "I-1"  # before the overlap


def test_unresolved_and_unknown_ids_are_refused():
    series, _, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    m = _model()
    _add(m, "I-1", ds[0], None, [("ZZZ", ds[0], None)])
    with pytest.raises(UnresolvedSeriesError, match="no instrument owns"):
        m.identify_series("AAA", ds)
    with pytest.raises(UnresolvedSeriesError, match="not in universe"):
        m.identify_series("AAA", ds, instrument_id="I-9")
    with pytest.raises(UnresolvedSeriesError, match="empty"):
        m.identify_series("AAA", [])
    # an explicit, known id over unowned bars is accepted (register knows no other owner)
    ident = m.identify_series("AAA", ds, instrument_id="I-1")
    assert ident.method == IDENTITY_GIVEN and ident.unowned_dates == len(ds)


def test_pilot_identity_is_marked_unverified():
    ident = pilot_identity("NVDA", [D(2016, 1, 4), D(2016, 1, 5)])
    assert ident.instrument_id == "NVDA" and ident.method == IDENTITY_PILOT
    assert ident.universe_version is None and "unverified" in ident.note
    assert ident.to_dict()["first_date"] == "2016-01-04"
    assert PilotUniverse(["NVDA"]).identify_series("NVDA", [D(2016, 1, 4)]).method == IDENTITY_PILOT


# --- before feature computation ---------------------------------------------------------------


def test_build_features_refuses_mixed_series_and_writes_nothing(tmp_path):
    series, _, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    mid = ds[len(ds) // 2]
    m = _model()
    _add(m, "I-OLD", ds[0], mid - timedelta(days=1), [("AAA", ds[0], mid)])
    _add(m, "I-NEW", mid, None, [("AAA", mid, None)])
    store = _store_series(tmp_path, series)
    with pytest.raises(MixedSeriesError):
        build_features(store, "test", "AAA", universe_model=m)
    assert not (tmp_path / "features").exists()  # refused before anything was written
    with pytest.raises(FileNotFoundError):  # and so no candidates can be built either
        build_candidates(store, "test", "AAA")


def test_build_features_records_identity_and_pilot_path_is_unchanged(tmp_path):
    series, _, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    store = _store_series(tmp_path, series)
    pilot_meta = build_features(store, "test", "AAA")
    pilot_feats = read_features(store, "test", "AAA")
    assert pilot_meta["identity"]["method"] == IDENTITY_PILOT
    assert pilot_meta["identity"]["instrument_id"] == "AAA"
    pd.testing.assert_frame_equal(pilot_feats, compute_features(series["AAA"]))  # same numbers
    cmeta = build_candidates(store, "test", "AAA")
    assert cmeta["identity"] == pilot_meta["identity"]

    m = _model("id-7")
    _add(m, "I-X", ds[0], None, [("OLD", ds[0], ds[10]), ("AAA", ds[10], None)])
    meta = build_features(store, "test", "AAA", universe_model=m)
    assert meta["identity"]["instrument_id"] == "I-X"
    assert meta["identity"]["method"] == IDENTITY_RESOLVED
    assert meta["identity"]["universe_version"] == "id-7"
    pd.testing.assert_frame_equal(read_features(store, "test", "AAA"), pilot_feats)
    on_disk = json.loads(
        (tmp_path / "features" / "test" / "AAA.meta.json").read_text(encoding="utf-8")
    )
    assert (
        on_disk["identity"]["owners_seen"] == ["I-X"] and on_disk["identity"]["unowned_dates"] == 10
    )


# --- portfolio engine uses the same contract ---------------------------------------------------


def test_engine_refuses_mixed_series_before_simulating():
    series, cands, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    mid = ds[len(ds) // 2]
    m = _model()
    _add(m, "I-OLD", ds[0], mid - timedelta(days=1), [("AAA", ds[0], mid)])
    _add(m, "I-NEW", mid, None, [("AAA", mid, None)])
    with pytest.raises(MixedSeriesError):
        run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m))
    # the "owner on the last bar" rule would have accepted this series as I-NEW; D-025 does not
    assert m.owners_of("AAA", [ds[-1]])[ds[-1]] == "I-NEW"


def test_engine_accepts_ticker_change_and_records_identity():
    series, cands, t0, _ = universe(RISE_THEN_FADE, ["AAA"])
    ds = _dates(series["AAA"])
    base = run_portfolio(series, cands, params())
    m = _model()
    _add(m, "I-X", ds[0], None, [("OLD", ds[0], ds[10]), ("AAA", ds[10], None)])
    res = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m))
    assert [t.to_row() for t in res.trades] == [t.to_row() for t in base.trades]
    ident = res.universe_audit["identity"]["AAA"]
    assert ident["instrument_id"] == "I-X" and ident["method"] == IDENTITY_RESOLVED
    assert base.universe_audit["identity"]["AAA"]["method"] == IDENTITY_PILOT
    m2 = _model()
    _add(m2, "I-1", ds[0], None, [("AAA", ds[0], None)])
    pit = PointInTimeUniverse(m2)
    m2.instruments.append(Instrument("I-2", "Other", "common_stock", "US", "test", OBS))
    m2.ticker_spans.append(TickerSpan("I-2", "AAA", ds[3], ds[4], "test", OBS))  # corrupted later
    with pytest.raises(AmbiguousTicker):
        run_portfolio(series, cands, params(), universe=pit)
