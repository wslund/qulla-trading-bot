"""U2B-FREE-3B-1: UniverseModel from linked timelines under an explicit policy. Synthetic
observations only. Nothing here is point-in-time and no test pretends otherwise."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from tradingbot.backtesting.universe import PointInTimeUniverse
from tradingbot.data.universe_build import (
    LAST_OBSERVATION,
    OBSERVED_ONLY,
    QUARANTINE_FLAGS,
    BuildError,
    ModelPolicy,
    compare_models,
    quarantine_reasons,
    timeline_to_model,
)
from tradingbot.data.universe_massive import DERIVED, EXACT, UNCERTAIN, observed_members
from tradingbot.data.universe_model import OBSERVED, UNKNOWN, UniverseModel
from tradingbot.data.universe_timeline import link_inventories

from .test_universe_timeline import F1, F2, F3, inv, ob

# dense dates (gaps <= 95 days) so the default policy accepts them
E1, E2, E3 = date(2026, 3, 2), date(2026, 5, 20), date(2026, 8, 10)
SPARSE = (date(2025, 11, 20), date(2026, 11, 20))


def build(invs, **pol):
    tls, link = link_inventories(invs)
    return timeline_to_model(tls, link, ModelPolicy(**pol))


def members(model: UniverseModel, d: date, **kw) -> set[str]:
    return {
        m.instrument_id
        for m in PointInTimeUniverse(model, mode="research").members(d, **kw).members
    }


# --- basic build -------------------------------------------------------------------------------


def test_exact_timeline_becomes_listing_spans_and_identifiers_with_sources():
    model, rep = build(
        [
            inv(E1, F1, [ob(E1, F1, "AAA", "FIGI-A", cik="1")]),
            inv(E2, F2, [ob(E2, F2, "AAA", "FIGI-A", cik="1")]),
        ]
    )
    assert model.version == f"massive-recon-{E1}-{E2}-last_observation"
    notes = json.loads(model.notes)
    assert notes["label"] == "effective_dated_reconstructed" and notes["point_in_time"] is False
    assert notes["policy"]["continuity"] == LAST_OBSERVATION and notes["coverage"] == "dense"
    assert [i.instrument_id for i in model.instruments] == ["FIGI-A"]
    ls = model.listings[0]
    assert (ls.listed_from, ls.listed_to, ls.listed_from_source) == (E1, None, OBSERVED)
    assert ls.source.startswith("policy:last_observation;observed_from=")
    sp = model.ticker_spans[0]
    assert (sp.ticker, sp.valid_from, sp.valid_to) == ("AAA", E1, None) and sp.source.startswith(
        "massive:observed"
    )
    assert {(i.id_type, i.match_confidence) for i in model.identifiers} == {
        ("share_class_figi", EXACT),
        ("cik", DERIVED),
    }
    assert model.delistings == [] and model.validate() == []
    assert (
        rep.included == 1
        and rep.quarantined == 0
        and rep.coverage == "dense"
        and rep.point_in_time is False
    )
    u = PointInTimeUniverse(model, mode="research")
    assert u.describe()["point_in_time"] is False
    assert (
        members(model, E1)
        == {"FIGI-A"}
        == members(model, E2)
        == members(model, E1 + timedelta(days=30))
    )
    assert members(model, E1 - timedelta(days=1)) == set()  # nothing before the first observation


def test_quarantine_every_flag_and_low_confidence():
    invs = [inv(E1, F1, [ob(E1, F1, "AAA", "FIGI-A")]), inv(E2, F2, [ob(E2, F2, "AAA", "FIGI-A")])]
    tls, link = link_inventories(invs)
    base = tls[0]
    for flag in QUARANTINE_FLAGS:
        base.flags = (flag,)
        assert quarantine_reasons(base, ModelPolicy()) == [flag]
        model, rep = timeline_to_model([base], link, ModelPolicy())
        assert (
            rep.quarantined == 1
            and rep.quarantined_by_reason == {flag: 1}
            and model.instruments == []
        )
        assert rep.quarantined_ids == ["FIGI-A"]
    base.flags = ()
    base.link_confidence = UNCERTAIN
    assert quarantine_reasons(base, ModelPolicy()) == ["confidence_uncertain"]
    base.link_confidence = DERIVED
    assert quarantine_reasons(base, ModelPolicy()) == ["confidence_derived"]
    assert quarantine_reasons(base, ModelPolicy(confidence=(EXACT, DERIVED))) == []
    with pytest.raises(BuildError, match="exact and/or derived"):
        ModelPolicy(confidence=(UNCERTAIN,)).validate()
    with pytest.raises(BuildError, match="unknown continuity"):
        ModelPolicy(continuity="interpolate").validate()


def test_real_world_flags_quarantine_via_link_inventories():
    before = E1 - timedelta(days=5)
    invs = [
        inv(E1, F1, [
            ob(E1, F1, "CCC", "FIGI-C", cik="1"),
            ob(E1, F1, "RRR", "FIGI-R", active=False, delisted=before),
            ob(E1, F1, "SSS", "FIGI-S", conf=UNCERTAIN, flags=("figi_shared",)),
            ob(E1, F1, "SST", "FIGI-S", conf=UNCERTAIN, flags=("figi_shared",)),
            ob(E1, F1, "OKK", "FIGI-OK"),
        ]),
        inv(E2, F2, [
            ob(E2, F2, "CCC", "FIGI-C", cik="2"),  # cik conflict
            ob(E2, F2, "RRR", "FIGI-R"),  # reactivated
            ob(E2, F2, "SSS", "FIGI-S"),
            ob(E2, F2, "OKK", "FIGI-OK"),
            ob(E2, F2, "NOD", "FIGI-N", active=False),  # inactive without date: unknown
        ]),
    ]  # fmt: skip
    model, rep = build(invs)
    assert [i.instrument_id for i in model.instruments] == ["FIGI-OK"]
    assert rep.quarantined_by_reason == {  # one timeline can carry several reasons
        "cik_conflict": 1,
        "confidence_uncertain": 2,  # the cik conflict and the shared figi both lower confidence
        "delisting_inconsistent": 1,
        "delisting_unknown": 1,
        "figi_shared": 1,
        "reactivated": 1,
        "ticker_set_changed": 1,
    }
    assert rep.quarantined == 4 and rep.included == 1 and rep.timelines == 5
    assert rep.quarantined + rep.included + rep.skipped_inactive_only == rep.timelines


# --- delisting and ticker change ---------------------------------------------------------------


def test_consistent_delisting_closes_listing_with_unknown_category():
    dl = E1 + timedelta(days=20)
    model, rep = build(
        [
            inv(E1, F1, [ob(E1, F1, "DDD", "FIGI-D")]),
            inv(E2, F2, [ob(E2, F2, "DDD", "FIGI-D", active=False, delisted=dl)]),
        ]
    )
    ls = model.listings[0]
    assert (ls.listed_from, ls.listed_to) == (
        E1,
        dl,
    ) and ";closed_by=massive:delisted_utc" in ls.source
    assert ls.observed_at == F2  # the closing fact was learned at the second fetch
    d = model.delistings[0]
    assert (d.effective_date, d.category, d.replacement_value_per_share, d.source) == (
        dl,
        UNKNOWN,
        None,
        "massive:delisted_utc",
    )
    assert model.ticker_spans[0].valid_to == dl + timedelta(days=1)
    assert members(model, dl) == {"FIGI-D"} and members(model, dl + timedelta(days=1)) == set()
    assert rep.listings_closed_by_delisting == 1 and rep.delistings == 1 and model.validate() == []
    # inactive-only timelines express no membership and are counted, not quarantined
    model, rep = build(
        [
            inv(
                E1, F1, [ob(E1, F1, "III", "FIGI-I", active=False, delisted=E1 - timedelta(days=9))]
            ),
            inv(E2, F2, []),
        ]
    )
    assert model.instruments == [] and rep.skipped_inactive_only == 1 and rep.quarantined == 0


def test_ticker_change_materialises_at_the_observation_never_inside_the_bound():
    model, rep = build([
        inv(E1, F1, [ob(E1, F1, "OLD", "FIGI-X")]),
        inv(E2, F2, [ob(E2, F2, "NEW", "FIGI-X")]),
        inv(E3, F3, [ob(E3, F3, "NEW", "FIGI-X")]),
    ])  # fmt: skip
    spans = sorted(model.ticker_spans, key=lambda s: s.valid_from)
    assert [(s.ticker, s.valid_from, s.valid_to) for s in spans] == [
        ("OLD", E1, E2),
        ("NEW", E2, None),
    ]
    assert spans[1].source == f"policy:last_observation;bound=({E1},{E2}]"
    assert spans[0].source.startswith("massive:observed")
    u = PointInTimeUniverse(model, mode="research")
    mid = E1 + timedelta(days=10)
    assert u.ticker_on("FIGI-X", mid) == "OLD" and u.ticker_on("FIGI-X", E2) == "NEW"
    assert u.instrument_for("NEW", mid) is None  # no day inside the bound claims the new ticker
    assert rep.ticker_change_bounds_materialised == 1 and model.validate() == []


# --- coverage ------------------------------------------------------------------------------------


def test_sparse_snapshots_are_refused_unless_allowed_and_then_marked():
    invs = [
        inv(SPARSE[0], F1, [ob(SPARSE[0], F1, "AAA", "FIGI-A")]),
        inv(SPARSE[1], F2, [ob(SPARSE[1], F2, "AAA", "FIGI-A")]),
    ]
    with pytest.raises(BuildError, match="coverage sparse: max gap 365 days > 95"):
        build(invs)
    model, rep = build(invs, allow_sparse=True)
    assert model.version.endswith("-last_observation-sparse") and rep.coverage == "sparse"
    assert json.loads(model.notes)["coverage"] == "sparse" and rep.max_gap_days_observed == 365
    assert json.loads(model.notes)["max_gap_days_observed"] == 365
    # a single snapshot is "single" coverage: also refused by default
    with pytest.raises(BuildError, match="coverage single"):
        build(invs[:1])
    model, rep = build(invs[:1], allow_sparse=True)
    assert rep.coverage == "single" and model.version.endswith("-single")
    # the threshold itself is inclusive
    d2 = E1 + timedelta(days=95)
    build([inv(E1, F1, [ob(E1, F1, "AAA", "FIGI-A")]), inv(d2, F2, [ob(d2, F2, "AAA", "FIGI-A")])])


# --- observed_only oracle ------------------------------------------------------------------------


def test_observed_only_matches_observed_members_and_is_empty_between():
    invs = [
        inv(E1, F1, [ob(E1, F1, "AAA", "FIGI-A"), ob(E1, F1, "BBB", "FIGI-B")]),
        inv(E2, F2, [ob(E2, F2, "AAA", "FIGI-A")]),
    ]
    oracle, _ = build(invs, continuity=OBSERVED_ONLY)
    step, _ = build(invs)
    assert members(oracle, E1) == observed_members(invs[0]) == {"FIGI-A", "FIGI-B"}
    assert members(oracle, E2) == observed_members(invs[1]) == {"FIGI-A"}
    assert members(oracle, E1 + timedelta(days=1)) == set()
    for d in (E1, E2):  # whatever the oracle has on an observed date, the step policy has too
        assert members(oracle, d) <= members(step, d)
    # BBB was not observed on E2 and never delisted: the policy keeps it (not_observed ≠ delisted)
    assert members(step, E2) == {"FIGI-A", "FIGI-B"} and members(oracle, E2) == {"FIGI-A"}
    assert build(invs)[1].listings_open_past_last_observation == 1
    assert members(step, E1 + timedelta(days=1)) == {"FIGI-A", "FIGI-B"}  # policy, not observation
    # one-day listings end without a delisting event: WARN by design, never FAIL
    assert all(i.level == "WARN" for i in oracle.validate()) and oracle.version.endswith(
        "-observed_only"
    )
    assert build(invs, continuity=OBSERVED_ONLY)[1].validate_warnings == len(oracle.validate()) > 0
    assert all(ls.listed_from == ls.listed_to for ls in oracle.listings)


# --- knowledge time: no future information, prefix immutability, visible revisions ---------------


def test_observed_at_is_fetched_at_never_effective_date():
    invs = [inv(E1, F1, [ob(E1, F1, "AAA", "FIGI-A")]), inv(E2, F2, [ob(E2, F2, "AAA", "FIGI-A")])]
    model, _ = build(invs)
    rows = (
        model.instruments
        + model.listings
        + model.ticker_spans
        + model.identifiers
        + model.delistings
    )
    assert {r.observed_at for r in rows} <= {F1, F2}
    assert all(r.observed_at.date() > E2 for r in rows)
    # tampered input: an observation whose observed_at is not its snapshot's fetched_at is refused
    tls, link = link_inventories(invs)
    bad = ob(E1, F2, "ZZZ", "FIGI-Z")  # fetched_at of E2 on an E1 observation
    tls[0].observations.append(bad)
    with pytest.raises(BuildError, match="not the fetched_at"):
        timeline_to_model(tls, link)


def test_prefix_model_is_immutable_and_full_rebuild_revisions_are_visible():
    dl = E2 + timedelta(days=15)
    d1 = inv(
        E1,
        F1,
        [
            ob(E1, F1, "AAA", "FIGI-A", cik="1"),
            ob(E1, F1, "BBB", "FIGI-B"),
            ob(E1, F1, "CCC", "FIGI-C"),
        ],
    )
    d2 = inv(
        E2,
        F2,
        [
            ob(E2, F2, "AAA", "FIGI-A", cik="1"),
            ob(E2, F2, "BBB", "FIGI-B"),
            ob(E2, F2, "CCC", "FIGI-C"),
        ],
    )
    d3 = inv(E3, F3, [
        ob(E3, F3, "AAA", "FIGI-A", cik="1"),
        ob(E3, F3, "BBB", "FIGI-B", active=False, delisted=dl),  # retrospective delisting
        ob(E3, F3, "CCC", "FIGI-C", cik="9"),  # new identity conflict → quarantine
        ob(E3, F3, "DDD", "FIGI-D"),  # new instrument
    ])  # fmt: skip
    prefix, _ = build([d1, d2])
    prefix_again, _ = build([d1, d2])
    full, rep_full = build([d1, d2, d3])
    # 1) the prefix build is unchanged by later data (deterministic checksum)
    assert prefix.checksum() == prefix_again.checksum()
    # 2) the full rebuild may change earlier conclusions – here BBB and CCC on dates <= E2 …
    diff = compare_models(prefix, full, [E1, E2, dl + timedelta(days=1), E3])
    assert diff["per_date"][E2.isoformat()]["removed_in_b"] == [
        "FIGI-C"
    ]  # cik conflict learned later
    assert diff["per_date"][(dl + timedelta(days=1)).isoformat()]["removed_in_b"] == [
        "FIGI-B",
        "FIGI-C",
    ]
    assert diff["per_date"][E3.isoformat()]["added_in_b"] == ["FIGI-D"]
    assert diff["instruments_only_in_b"] == ["FIGI-D"] and diff["instruments_only_in_a"] == [
        "FIGI-C"
    ]
    assert rep_full.quarantined_by_reason == {"cik_conflict": 1, "confidence_uncertain": 1}
    # 3) no future information at a given knowledge_at: facts fetched at F3 are invisible …
    assert members(full, E2, knowledge_at=F2) <= members(prefix, E2)
    assert members(full, E3, knowledge_at=F2) == {"FIGI-A"}  # BBB's listing row now carries F3
    assert members(full, E1, knowledge_at=F1 - timedelta(seconds=1)) == set()
    # … but knowledge_at never makes the model point-in-time: the gap is 100 % and the label stays
    ms = PointInTimeUniverse(full, mode="research").members(E1, knowledge_at=F3)
    assert ms.knowledge_gap == ms.facts_total > 0
    assert PointInTimeUniverse(full, mode="research").describe()["point_in_time"] is False
    assert json.loads(full.notes)["point_in_time"] is False and full.version.startswith(
        "massive-recon-"
    )


def test_determinism_and_refusals():
    invs = [
        inv(E1, F1, [ob(E1, F1, "BBB", "FIGI-B"), ob(E1, F1, "AAA", "FIGI-A")]),
        inv(E2, F2, [ob(E2, F2, "AAA", "FIGI-A"), ob(E2, F2, "BBB", "FIGI-B")]),
    ]
    m1, _ = build(invs)
    m2, _ = build(list(reversed(invs)))
    assert m1.checksum() == m2.checksum() and m1.version == m2.version
    tls, link = link_inventories(invs)
    link.point_in_time = True
    with pytest.raises(BuildError, match="point_in_time"):
        timeline_to_model(tls, link)
    with pytest.raises(BuildError, match="listed_from='observed'"):
        ModelPolicy(listed_from="first_bar").validate()
