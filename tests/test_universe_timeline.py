"""U2B-FREE-3A: linking observations from several synthetic Massive inventories into
timelines. No real data; nothing here invents a date."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tradingbot.data.universe_massive import (
    DERIVED,
    EXACT,
    UNCERTAIN,
    ImportReport,
    InstrumentObservation,
    MassiveInventory,
)
from tradingbot.data.universe_timeline import (
    CONSISTENT,
    EARLIEST,
    INCONSISTENT,
    NOT_OBSERVED,
    UNKNOWN,
    TickerChangeBound,
    TimelineError,
    link_inventories,
    revision_report,
    select_revisions,
)

D1, D2, D3 = date(2025, 11, 20), date(2026, 5, 20), date(2026, 11, 20)
F1, F2, F3 = (
    datetime(2026, 11, 21, 7, 0, tzinfo=UTC),
    datetime(2026, 11, 21, 8, 0, tzinfo=UTC),
    datetime(2026, 11, 21, 9, 0, tzinfo=UTC),
)


def ob(
    eff: date,
    fetched: datetime,
    ticker: str,
    iid: str,
    *,
    active: bool = True,
    conf: str = EXACT,
    src: str = "share_class_figi",
    cik: str | None = "1",
    cf: str | None = None,
    scf: str | None = None,
    delisted: date | None = None,
    dl_unc: str | None = None,
    typ: str = "CS",
    exch: str = "XNAS",
    flags: tuple[str, ...] = (),
    lu: str = "2026-11-20T00:00:00Z",
) -> InstrumentObservation:
    if scf is None and src == "share_class_figi":
        scf = iid.removeprefix("FIGI-")
    if cf is None and src == "composite_figi":
        cf = iid.removeprefix("CFIGI-")
    return InstrumentObservation(
        eff, fetched, ticker, iid, src, conf, active, f"Synthetic {ticker}", exch, typ, "us",
        cik, cf, scf, delisted, "massive:delisted_utc" if delisted else None,
        dl_unc if dl_unc else (None if (active or delisted) else "no_date"), lu, flags,
    )  # fmt: skip


def inv(eff: date, fetched: datetime, obs: list[InstrumentObservation]) -> MassiveInventory:
    return MassiveInventory(
        f"massive-{eff}-{fetched.strftime('%Y%m%dT%H%M%SZ')}",
        eff,
        fetched,
        "effective_dated_reconstructed",
        f"synthetic/{eff}",
        "0" * 64,
        obs,
        ImportReport(eff.isoformat(), fetched.isoformat(), "effective_dated_reconstructed"),
    )


def by_id(timelines):
    return {t.instrument_id: t for t in timelines}


# 1 ---------------------------------------------------------------------------------------------
def test_same_unique_figi_on_two_dates_links_exact_without_interval_claims():
    tls, rep = link_inventories(
        [inv(D1, F1, [ob(D1, F1, "AAA", "FIGI-A")]), inv(D2, F2, [ob(D2, F2, "AAA", "FIGI-A")])]
    )
    t = by_id(tls)["FIGI-A"]
    assert t.linked and t.link_confidence == EXACT and t.dates_observed == (D1, D2)
    assert t.dates_not_observed == () and t.ticker_change_bounds == [] and t.delisting is None
    assert t.state_on(D1) == "active" and t.state_on(D2) == "active"
    assert (
        t.state_on(D1 + timedelta(days=1)) == NOT_OBSERVED
    )  # nothing is said about the day between
    assert not hasattr(t, "listed_from") and not hasattr(t, "valid_from")
    assert t.point_in_time is False and rep.point_in_time is False
    assert rep.linked == 1 and rep.linked_by_confidence == {EXACT: 1} and rep.timelines == 1


# 2 ---------------------------------------------------------------------------------------------
def test_same_figi_with_conflicting_cik_is_uncertain_not_merged_silently():
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "AAA", "FIGI-A", cik="100")]),
        inv(D2, F2, [ob(D2, F2, "AAA", "FIGI-A", cik="200")]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-A"]
    assert t.link_confidence == UNCERTAIN and "cik_conflict" in t.flags
    assert len(t.observations) == 2  # both facts kept, none adjusted
    assert {o.cik for o in t.observations} == {"100", "200"}
    assert rep.cik_conflicts == 1 and rep.linked_by_confidence == {UNCERTAIN: 1}
    # a missing CIK on one side is not a conflict
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "BBB", "FIGI-B", cik=None)]),
        inv(D2, F2, [ob(D2, F2, "BBB", "FIGI-B", cik="300")]),
    ])  # fmt: skip
    assert by_id(tls)["FIGI-B"].link_confidence == EXACT and rep.cik_conflicts == 0
    # derived ids link as derived; conflicting composite_figi on a share_class link → uncertain
    tls, _ = link_inventories([
        inv(D1, F1, [ob(D1, F1, "CCC", "CFIGI-C", conf=DERIVED, src="composite_figi")]),
        inv(D2, F2, [ob(D2, F2, "CCC", "CFIGI-C", conf=DERIVED, src="composite_figi")]),
    ])  # fmt: skip
    assert by_id(tls)["CFIGI-C"].link_confidence == DERIVED
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "DDD", "FIGI-D", cf="BBGCF1")]),
        inv(D2, F2, [ob(D2, F2, "DDD", "FIGI-D", cf="BBGCF2")]),
    ])  # fmt: skip
    assert (
        "composite_figi_conflict" in by_id(tls)["FIGI-D"].flags
        and rep.composite_figi_conflicts == 1
    )


# 3 ---------------------------------------------------------------------------------------------
def test_surrogates_are_never_linked_across_dates():
    s1 = ob(
        D1,
        F1,
        "EEE",
        f"MSV-EEE-active-{D1}",
        conf=UNCERTAIN,
        src="surrogate",
        scf=None,
        flags=("no_figi",),
    )
    s2 = ob(
        D2,
        F2,
        "EEE",
        f"MSV-EEE-active-{D2}",
        conf=UNCERTAIN,
        src="surrogate",
        scf=None,
        flags=("no_figi",),
    )
    tls, rep = link_inventories([inv(D1, F1, [s1]), inv(D2, F2, [s2])])
    assert len(tls) == 2 and all(not t.linked for t in tls)
    assert all("surrogate_unlinked" in t.flags and t.link_confidence == UNCERTAIN for t in tls)
    assert rep.unlinked_surrogates == 2 and rep.linked == 0 and rep.single_observation == 2
    assert (
        rep.not_observed_gaps == 2
    )  # each surrogate is absent on the other date: a gap, not a delisting
    assert all(t.delisting is None for t in tls)


# 4 ---------------------------------------------------------------------------------------------
def test_shared_figi_stays_uncertain_even_when_unique_later():
    shared = [
        ob(D1, F1, "FFF", "FIGI-S", conf=UNCERTAIN, flags=("figi_shared",)),
        ob(D1, F1, "FFG", "FIGI-S", conf=UNCERTAIN, flags=("figi_shared",)),
    ]
    later = [ob(D2, F2, "FFF", "FIGI-S")]  # unique in D2
    tls, rep = link_inventories([inv(D1, F1, shared), inv(D2, F2, later)])
    t = by_id(tls)["FIGI-S"]
    assert t.link_confidence == UNCERTAIN and "figi_shared" in t.flags
    assert len(t.observations) == 3 and t.tickers == ("FFF", "FFG")
    assert (
        t.ticker_change_bounds == [] and "ticker_set_changed" in t.flags
    )  # no single bound from a cluster
    assert rep.figi_shared == 1 and rep.linked_by_confidence == {UNCERTAIN: 1}


# 5 ---------------------------------------------------------------------------------------------
def test_ticker_change_is_a_bound_not_a_date():
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "OLD", "FIGI-X")]),
        inv(D2, F2, [ob(D2, F2, "NEW", "FIGI-X")]),
        inv(D3, F3, [ob(D3, F3, "NEW", "FIGI-X")]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-X"]
    assert t.ticker_change_bounds == [TickerChangeBound("FIGI-X", "OLD", "NEW", D1, D2)]
    b = t.ticker_change_bounds[0]
    assert b.lo_exclusive == D1 and b.hi_inclusive == D2  # (D1, D2]
    assert not hasattr(b, "change_date")
    assert t.link_confidence == EXACT and rep.ticker_change_bounds == 1
    assert t.tickers == ("NEW", "OLD")


# 6 ---------------------------------------------------------------------------------------------
def test_reused_ticker_never_merges_two_instruments():
    old_owner_d1 = ob(D1, F1, "RRR", "FIGI-OLD", cik="10")
    old_owner_d2 = ob(
        D2, F2, "RRR", "FIGI-OLD", cik="10", active=False, delisted=D1 + timedelta(days=30)
    )
    new_owner_d2 = ob(D2, F2, "RRR", "FIGI-NEW", cik="20")
    tls, rep = link_inventories(
        [inv(D1, F1, [old_owner_d1]), inv(D2, F2, [old_owner_d2, new_owner_d2])]
    )
    assert set(by_id(tls)) == {"FIGI-OLD", "FIGI-NEW"}
    for t in tls:
        assert "ticker_reused" in t.flags and "ticker_reused_unverified" not in t.flags
    assert by_id(tls)["FIGI-OLD"].delisting.consistency == CONSISTENT
    assert rep.tickers_reused == 1 and rep.tickers_reused_unverified == 0
    # the old owner is not seen inactive by D2: reuse unverified, still two timelines
    tls, rep = link_inventories([inv(D1, F1, [old_owner_d1]), inv(D2, F2, [new_owner_d2])])
    assert set(by_id(tls)) == {"FIGI-OLD", "FIGI-NEW"}
    assert all("ticker_reused_unverified" in t.flags for t in tls)
    assert (
        by_id(tls)["FIGI-OLD"].dates_not_observed == (D2,)
        and by_id(tls)["FIGI-OLD"].delisting is None
    )
    assert rep.tickers_reused_unverified == 2


# 7 ---------------------------------------------------------------------------------------------
def test_active_to_inactive_with_consistent_delisting_date():
    dl = D1 + timedelta(days=100)
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "GGG", "FIGI-G")]),
        inv(D2, F2, [ob(D2, F2, "GGG", "FIGI-G", active=False, delisted=dl)]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-G"]
    d = t.delisting
    assert (d.delisted_date, d.source, d.consistency) == (dl, "massive:delisted_utc", CONSISTENT)
    assert (d.last_active_observed, d.first_inactive_observed) == (D1, D2)
    assert t.state_on(D1) == "active" and t.state_on(D2) == "inactive"
    assert rep.delisting_consistent == 1 and rep.delisting_inconsistent == 0
    # boundary values are allowed: delisted on D1 itself or on D2 itself
    for edge in (D1, D2):
        tls, _ = link_inventories([
            inv(D1, F1, [ob(D1, F1, "GGG", "FIGI-G")]),
            inv(D2, F2, [ob(D2, F2, "GGG", "FIGI-G", active=False, delisted=edge)]),
        ])  # fmt: skip
        assert by_id(tls)["FIGI-G"].delisting.consistency == CONSISTENT


# 8 ---------------------------------------------------------------------------------------------
def test_reactivation_and_contradictory_delisting_dates_are_flagged_not_fixed():
    before = D1 - timedelta(days=10)
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "HHH", "FIGI-H")]),
        inv(D2, F2, [ob(D2, F2, "HHH", "FIGI-H", active=False, delisted=before)]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-H"]
    assert t.delisting.consistency == INCONSISTENT and "delisting_inconsistent" in t.flags
    assert t.delisting.delisted_date == before  # kept as the vendor said it
    assert rep.delisting_inconsistent == 1
    # inactive first, active later: reactivated, never one continuous listing
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "III", "FIGI-I", active=False, delisted=before)]),
        inv(D2, F2, [ob(D2, F2, "III", "FIGI-I")]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-I"]
    assert "reactivated" in t.flags and t.delisting.consistency == INCONSISTENT
    assert t.state_on(D1) == "inactive" and t.state_on(D2) == "active" and rep.reactivated == 1
    # two inactive observations with different delisted dates: revised, both kept
    earlier = before - timedelta(days=5)
    tls, _ = link_inventories([
        inv(D1, F1, [ob(D1, F1, "JJJ", "FIGI-J", active=False, delisted=before)]),
        inv(D2, F2, [ob(D2, F2, "JJJ", "FIGI-J", active=False, delisted=earlier)]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-J"]
    assert (
        "delisted_date_revised" in t.flags and len({o.delisted_date for o in t.observations}) == 2
    )
    # inactive without any date → unknown
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "KKK", "FIGI-K")]),
        inv(D2, F2, [ob(D2, F2, "KKK", "FIGI-K", active=False)]),
    ])  # fmt: skip
    assert by_id(tls)["FIGI-K"].delisting.consistency == UNKNOWN and rep.delisting_unknown == 1
    # an effective-dated artifact (delisted after effective_date) is inconsistent
    art = ob(
        D2, F2, "LLL", "FIGI-L", active=False, delisted=D2 + timedelta(days=3),
        dl_unc="after_effective_date",
    )  # fmt: skip
    tls, _ = link_inventories([inv(D1, F1, [ob(D1, F1, "LLL", "FIGI-L")]), inv(D2, F2, [art])])
    assert by_id(tls)["FIGI-L"].delisting.consistency == INCONSISTENT


# 9 ---------------------------------------------------------------------------------------------
def test_missing_in_later_snapshot_is_not_observed_not_delisted():
    tls, rep = link_inventories([
        inv(D1, F1, [ob(D1, F1, "MMM", "FIGI-M")]),
        inv(D2, F2, []),
        inv(D3, F3, [ob(D3, F3, "MMM", "FIGI-M")]),
    ])  # fmt: skip
    t = by_id(tls)["FIGI-M"]
    assert t.dates_observed == (D1, D3) and t.dates_not_observed == (D2,)
    assert t.state_on(D2) == NOT_OBSERVED and t.delisting is None
    assert "reactivated" not in t.flags  # absence is not an inactive observation
    assert rep.not_observed_gaps == 1 and rep.delisting_unknown == 0


# 10 --------------------------------------------------------------------------------------------
def test_two_revisions_of_the_same_effective_date_are_revisions_not_events():
    a = inv(
        D3,
        F1,
        [
            ob(D3, F1, "NNN", "FIGI-N", cik="1"),
            ob(D3, F1, "OOO", "FIGI-O", active=False, delisted=D2),
        ],
    )
    b = inv(D3, F2, [ob(D3, F2, "NNN", "FIGI-N", cik="9"), ob(D3, F2, "PPP", "FIGI-P")])
    tls, rep = link_inventories([a, b])  # default: latest revision
    assert rep.effective_dates == [D3.isoformat()]
    assert rep.observed_at == {D3.isoformat(): F2.isoformat()}
    assert rep.revisions_skipped == {D3.isoformat(): [F1.isoformat()]}
    assert set(by_id(tls)) == {"FIGI-N", "FIGI-P"} and all(not t.linked for t in tls)
    assert by_id(tls)["FIGI-N"].observations[0].observed_at == F2
    tls, rep = link_inventories([a, b], revision=EARLIEST)
    assert set(by_id(tls)) == {"FIGI-N", "FIGI-O"} and rep.revision_policy == EARLIEST
    with pytest.raises(TimelineError, match="unknown revision"):
        link_inventories([a, b], revision="newest")
    with pytest.raises(TimelineError, match="same observed_at"):
        select_revisions([a, inv(D3, F1, [])])
    r = revision_report(b, a)  # order does not matter: a is earlier
    assert (r.observed_at_a, r.observed_at_b) == (F1.isoformat(), F2.isoformat())
    assert (r.rows_a, r.rows_b, r.unchanged, r.changed, r.added, r.removed) == (2, 2, 0, 1, 1, 1)
    assert r.changed_by_field == {"cik": 1} and r.identity_changed == 0
    assert "vendor revisions" in r.note
    with pytest.raises(TimelineError, match="same effective_date"):
        revision_report(a, inv(D2, F3, []))
    with pytest.raises(TimelineError, match="duplicates"):
        revision_report(a, inv(D3, F1, []))
    # identity change: same ticker and state, different instrument id between fetches
    c = inv(D3, F3, [ob(D3, F3, "NNN", "FIGI-N2")])
    r2 = revision_report(b, c)
    assert r2.identity_changed == 1 and r2.added == 1 and r2.removed == 2
    # surrogates are matched by ticker+state across fetches (their ids carry the date)
    s_a = inv(
        D3,
        F1,
        [ob(D3, F1, "QQQ", f"MSV-QQQ-active-{D3}", conf=UNCERTAIN, src="surrogate", scf=None)],
    )
    s_b = inv(
        D3,
        F2,
        [
            ob(
                D3,
                F2,
                "QQQ",
                f"MSV-QQQ-active-{D3}",
                conf=UNCERTAIN,
                src="surrogate",
                scf=None,
                exch="XNYS",
            )
        ],
    )
    r3 = revision_report(s_a, s_b)
    assert r3.changed == 1 and r3.changed_by_field == {"primary_exchange": 1}


# 11 --------------------------------------------------------------------------------------------
def test_sorting_determinism_and_timestamp_preservation():
    obs1 = [ob(D1, F1, "ZZZ", "FIGI-Z"), ob(D1, F1, "AAA", "FIGI-A", lu="2025-01-01T00:00:00Z")]
    obs2 = [ob(D2, F2, "AAA", "FIGI-A"), ob(D2, F2, "ZZZ", "FIGI-Z")]
    t1, r1 = link_inventories([inv(D2, F2, obs2), inv(D1, F1, list(reversed(obs1)))])
    t2, r2 = link_inventories([inv(D1, F1, obs1), inv(D2, F2, list(reversed(obs2)))])
    assert [t.instrument_id for t in t1] == ["FIGI-A", "FIGI-Z"] == [t.instrument_id for t in t2]
    assert [[(o.effective_date, o.ticker) for o in t.observations] for t in t1] == [
        [(o.effective_date, o.ticker) for o in t.observations] for t in t2
    ]
    assert r1.to_dict() == r2.to_dict() and r1.effective_dates == [D1.isoformat(), D2.isoformat()]
    a = by_id(t1)["FIGI-A"]
    assert [o.observed_at for o in a.observations] == [
        F1,
        F2,
    ]  # fetched_at preserved per observation
    assert (a.observed_at_first, a.observed_at_last) == (F1, F2)
    assert (
        a.observations[0].last_updated_utc == "2025-01-01T00:00:00Z"
    )  # vendor time carried, untouched
    assert a.observations[0].observed_at != a.observations[0].effective_date
    with pytest.raises(TimelineError, match="no inventories"):
        link_inventories([])
    pit = inv(D1, F1, obs1)
    pit.point_in_time = True
    with pytest.raises(TimelineError, match="point_in_time"):
        link_inventories([pit])
