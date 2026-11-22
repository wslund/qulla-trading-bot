"""U2B-FREE-2B: offline import of a massive_tickers snapshot into an inventory of
observations. Synthetic pages only (U2B-FREE-1 fixtures); nothing from a real snapshot."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradingbot.data.snapshots import SnapshotStore, snapshot_massive_tickers
from tradingbot.data.universe_massive import (
    DERIVED,
    EXACT,
    UNCERTAIN,
    MassiveInventory,
    SnapshotImportError,
    build_inventory,
    exclusion_reason,
    load_snapshot,
    observed_members,
    to_universe_model,
)

from .test_snapshots import FakeHttp
from .test_snapshots_massive import A1, A2, HDR, I1, I2, KEY, page, row

D0 = date(2026, 11, 20)
T0 = datetime(2026, 11, 21, 7, 30, 0, tzinfo=UTC)
A1D, I1D = A1.replace("2026-11-01", "2026-11-20"), I1.replace("2026-11-01", "2026-11-20")

# --- synthetic universe: every situation the import must handle -----------------------------
ACTIVE = [
    row("AAA", share_class_figi="BBG0SC000AAA", composite_figi="BBG0CF000AAA", cik="1"),
    row("BBB", share_class_figi=None, composite_figi="BBG0CF000BBB", cik=None),  # derived id
    row("CCC", share_class_figi=None, composite_figi=None, cik="3"),  # surrogate id
    row("DDD", share_class_figi="BBG0SC00SHRD", composite_figi="BBG0CF000DDD"),  # shared scf …
    row("DDE", share_class_figi="BBG0SC00SHRD", composite_figi="BBG0CF000DDE"),  # … with DDE
    row("EEE", share_class_figi="BBG0SC000EE1"),  # same ticker twice in the active list:
    row("EEE", share_class_figi="BBG0SC000EE2"),  # ambiguous, never a member
    row("FFF", share_class_figi="BBG0SC000FFF", type="ADRC"),  # excluded: ADR
    row("GGG", share_class_figi="BBG0SC000GGG", type="ETF"),  # excluded: ETF
    row("HHH", share_class_figi="BBG0SC000HHH", type=None),  # excluded: type missing
    row("III", share_class_figi="BBG0SC000III", primary_exchange=None),  # excluded: exchange
    row("JJJ", share_class_figi="BBG0SC000JJJ", primary_exchange="OTCM"),  # excluded: OTC
    row("KKK", share_class_figi="BBG0SC000KKK", locale="gb"),  # excluded: locale
    row("LLL", share_class_figi="BBG0SC000LLL", type="PFD"),  # excluded: preferred
]
INACTIVE = [
    row(  # the ticker AAA was used before by another issuer: expected reuse
        "AAA",
        active=False,
        share_class_figi="BBG0SC00OLDA",
        composite_figi="BBG0CF00OLDA",
        cik="7",
        delisted_utc="2019-06-28T00:00:00Z",
        last_updated_utc="2025-03-01T00:00:00Z",
    ),
    row("MMM", active=False, share_class_figi="BBG0SC000MMM", delisted_utc="2025-02-14T00:00:00Z"),
    row("NNN", active=False, share_class_figi="BBG0SC000NNN", delisted_utc=None),  # no date
    row(  # delisted "after" the effective date: effective-dated artifact
        "OOO", active=False, share_class_figi="BBG0SC000OOO", delisted_utc="2026-11-25T00:00:00Z"
    ),
    row(
        "PPP",
        active=False,
        share_class_figi="BBG0SC000PPP",
        type="WARRANT",
        delisted_utc="2024-01-02T00:00:00Z",
    ),
]


def make_snapshot(tmp_path, active=ACTIVE, inactive=INACTIVE) -> Path:
    http = FakeHttp(
        {
            A1D: (200, HDR, page(active[:8], next_url=A2)),
            A2: (200, HDR, page(active[8:])),
            I1D: (200, HDR, page(inactive, next_url=I2)),
            I2: (200, HDR, page([])),
        }
    )
    return snapshot_massive_tickers(
        SnapshotStore(tmp_path),
        effective_date=D0,
        http_get=http,
        now=T0,
        api_key=KEY,
        sleep=lambda s: None,
        clock=lambda: 0.0,
    )


# --- load_snapshot: verification before interpretation ---------------------------------------


def test_load_complete_snapshot_verifies_and_returns_raw_rows(tmp_path):
    d = make_snapshot(tmp_path)
    snap = load_snapshot(d)
    assert snap.effective_date == D0 and snap.fetched_at == T0
    assert snap.label == "effective_dated_reconstructed"
    assert len(snap.active) == len(ACTIVE) and len(snap.inactive) == len(INACTIVE)
    assert snap.active[0] == ACTIVE[0]  # untouched
    assert len(snap.sha256_combined) == 64


def test_tampered_page_is_refused(tmp_path):
    d = make_snapshot(tmp_path)
    p = d / "active_p0002.json"
    p.write_bytes(p.read_bytes().replace(b"GGG", b"GGX"))
    with pytest.raises(SnapshotImportError, match="sha256 mismatch"):
        load_snapshot(d)


def test_incomplete_or_wrong_source_snapshot_is_refused(tmp_path):
    d = make_snapshot(tmp_path)
    mp = d / "manifest.json"
    man = json.loads(mp.read_text())
    man["status"] = "incomplete"
    mp.write_text(json.dumps(man))
    with pytest.raises(SnapshotImportError, match="only complete"):
        load_snapshot(d)
    man["status"] = "complete"
    man["source"] = "nasdaq_trader"
    mp.write_text(json.dumps(man))
    with pytest.raises(SnapshotImportError, match="source"):
        load_snapshot(d)


def test_page_and_row_count_mismatches_are_refused(tmp_path):
    d = make_snapshot(tmp_path)
    mp = d / "manifest.json"
    orig = mp.read_text()
    man = json.loads(orig)
    man["pages"] = 3
    mp.write_text(json.dumps(man))
    with pytest.raises(SnapshotImportError, match="page count"):
        load_snapshot(d)
    man = json.loads(orig)
    man["extra"]["active_count"] = 99
    mp.write_text(json.dumps(man))
    with pytest.raises(SnapshotImportError, match="active total"):
        load_snapshot(d)
    man = json.loads(orig)
    man["files"][0]["record_count"] = 1
    man["files"][0]["sha256"] = (
        __import__("hashlib").sha256((d / "active_p0001.json").read_bytes()).hexdigest()
    )
    mp.write_text(json.dumps(man))
    with pytest.raises(SnapshotImportError, match="row count"):
        load_snapshot(d)
    mp.write_text(orig)
    (d / "inactive_p0002.json").unlink()
    with pytest.raises(SnapshotImportError, match="missing"):
        load_snapshot(d)


# --- selection ----------------------------------------------------------------------------------


def test_strict_selection_reports_every_exclusion_reason(tmp_path):
    inv = build_inventory(load_snapshot(make_snapshot(tmp_path)))
    rep = inv.report
    assert rep.rows_total == len(ACTIVE) + len(INACTIVE)
    assert rep.excluded_by_reason == {
        "exchange_OTCM": 1,
        "exchange_missing": 1,
        "locale_gb": 1,
        "type_ADRC": 1,
        "type_ETF": 1,
        "type_PFD": 1,
        "type_WARRANT": 1,
        "type_missing": 1,
    }
    assert rep.excluded == 8 and rep.included == rep.rows_total - 8
    assert rep.included_active == 7 and rep.included_inactive == 4
    assert exclusion_reason({**row("X"), "ticker": ""}) == "no_ticker"
    assert exclusion_reason(row("X")) is None


# --- identity ----------------------------------------------------------------------------------


def _by_ticker(inv):
    out = {}
    for o in inv.observations:
        out.setdefault(o.ticker, []).append(o)
    return out


def test_identity_rules_exact_derived_surrogate_and_shared_figi(tmp_path):
    inv = build_inventory(load_snapshot(make_snapshot(tmp_path)))
    by = _by_ticker(inv)
    aaa = next(o for o in by["AAA"] if o.active)
    assert (aaa.instrument_id, aaa.id_source, aaa.match_confidence) == (
        "FIGI-BBG0SC000AAA",
        "share_class_figi",
        EXACT,
    )
    bbb = by["BBB"][0]
    assert (bbb.instrument_id, bbb.match_confidence) == ("CFIGI-BBG0CF000BBB", DERIVED)
    ccc = by["CCC"][0]
    assert ccc.instrument_id == "MSV-CCC-active-2026-11-20" and ccc.match_confidence == UNCERTAIN
    assert "no_figi" in ccc.flags and ccc.id_source == "surrogate"
    for t in ("DDD", "DDE"):
        o = by[t][0]
        assert o.match_confidence == UNCERTAIN and "figi_shared" in o.flags
        assert o.instrument_id == "FIGI-BBG0SC00SHRD"  # same id kept, confidence lowered
    rep = inv.report
    assert rep.figi_shared_groups == 1 and rep.figi_shared_rows == 2
    assert rep.identity_by_source == {"composite_figi": 1, "share_class_figi": 9, "surrogate": 1}
    assert rep.identity_by_confidence == {DERIVED: 1, EXACT: 7, UNCERTAIN: 3}
    assert rep.unresolved == 1


def test_reused_ticker_keeps_two_instruments_apart_and_ambiguous_ticker_is_not_a_member(tmp_path):
    inv = build_inventory(load_snapshot(make_snapshot(tmp_path)))
    by = _by_ticker(inv)
    ids = {o.instrument_id for o in by["AAA"]}
    assert ids == {"FIGI-BBG0SC000AAA", "FIGI-BBG0SC00OLDA"}
    assert all("ticker_reused" in o.flags for o in by["AAA"])
    assert not any(o.ambiguous for o in by["AAA"])  # reuse across lists is expected, not ambiguous
    eee = by["EEE"]
    assert len(eee) == 2 and all(o.ambiguous and o.active for o in eee)
    members = observed_members(inv)
    assert "FIGI-BBG0SC000AAA" in members and "FIGI-BBG0SC00OLDA" not in members  # inactive
    assert not any(o.instrument_id in members for o in eee)
    assert "CFIGI-BBG0CF000BBB" in members  # derived counts
    assert "MSV-CCC-active-2026-11-20" not in members  # surrogate: inventory only
    assert "FIGI-BBG0SC00SHRD" not in members  # shared figi: uncertain
    assert observed_members(inv, confidences=(EXACT, DERIVED, UNCERTAIN)) >= members | {
        "MSV-CCC-active-2026-11-20"
    }
    assert inv.report.observed_members == len(members) == 2
    assert inv.report.reused_tickers == 1 and inv.report.ambiguous_ticker_rows == 2


# --- times and delistings -------------------------------------------------------------------------


def test_observed_at_is_fetched_at_and_no_start_dates_exist(tmp_path):
    inv = build_inventory(load_snapshot(make_snapshot(tmp_path)))
    assert all(o.observed_at == T0 for o in inv.observations)
    old = next(o for o in inv.observations if o.instrument_id == "FIGI-BBG0SC00OLDA")
    assert old.last_updated_utc == "2025-03-01T00:00:00Z" and old.observed_at == T0
    assert inv.point_in_time is False and inv.label == "effective_dated_reconstructed"
    assert inv.effective_date == D0 and inv.observed_at == T0
    assert not any(hasattr(o, "listed_from") or hasattr(o, "valid_from") for o in inv.observations)
    with pytest.raises(NotImplementedError, match="start dates"):
        to_universe_model(inv)


def test_delisting_dates_carry_source_and_uncertainty_and_no_reason(tmp_path):
    inv = build_inventory(load_snapshot(make_snapshot(tmp_path)))
    by = _by_ticker(inv)
    mmm = by["MMM"][0]
    assert (mmm.delisted_date, mmm.delisted_source, mmm.delisted_uncertainty) == (
        date(2025, 2, 14),
        "massive:delisted_utc",
        None,
    )
    nnn = by["NNN"][0]
    assert (
        nnn.delisted_date is None
        and nnn.delisted_source is None
        and nnn.delisted_uncertainty == "no_date"
    )
    ooo = by["OOO"][0]
    assert (
        ooo.delisted_uncertainty == "after_effective_date"
        and "delisted_after_effective" in ooo.flags
    )
    assert not any(hasattr(o, "category") or hasattr(o, "reason") for o in inv.observations)
    rep = inv.report
    assert (
        rep.delisted_with_date,
        rep.delisted_without_date,
        rep.delisted_after_effective_date,
    ) == (3, 1, 1)
    assert "no delisting reasons" in rep.notes


# --- persistence ----------------------------------------------------------------------------------


def test_inventory_roundtrip_is_lossless_and_immutable(tmp_path):
    inv = build_inventory(load_snapshot(make_snapshot(tmp_path)))
    assert inv.version == "massive-2026-11-20-20261121T073000Z"
    d = inv.write(tmp_path)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["point_in_time"] is False and man["report"]["included"] == inv.report.included
    assert (
        man["source_sha256"] == inv.source_sha256 and "KEY" not in (d / "manifest.json").read_text()
    )
    back = MassiveInventory.read(tmp_path, inv.version)
    assert back.observations == inv.observations and back.report == inv.report
    assert back.effective_date == D0 and back.observed_at == T0
    with pytest.raises(SnapshotImportError):
        inv.write(tmp_path)
    # the stored frame carries no field that looks like a start date
    cols = set(inv.frame().columns)
    assert "delisted_date" in cols and not {"listed_from", "valid_from", "list_date"} & cols


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_imports_latest_complete_snapshot_and_prints_counts_only(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "inv_cli", Path(__file__).resolve().parents[1] / "scripts" / "build_universe_inventory.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["--data-dir", str(tmp_path)]) == 1  # nothing to import yet
    make_snapshot(tmp_path)
    assert cli.main(["--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert '"included": 11' in out and "point_in_time=False" in out
    assert "BBG0" not in out and "AAA" not in out and KEY not in out
    assert (
        tmp_path / "universe_obs" / "massive-2026-11-20-20261121T073000Z" / "manifest.json"
    ).exists()
    assert cli.main(["--data-dir", str(tmp_path)]) == 1  # append-only: second import refused
    assert "refused" in capsys.readouterr().err
