"""U1 step A: universe data model and Universe membership – deterministic, offline,
synthetic rows only (no real instruments)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tradingbot.backtesting.universe import (
    EVALUATION,
    RESEARCH,
    AmbiguousTicker,
    PilotUniverse,
    PointInTimeUniverse,
    UniverseError,
)
from tradingbot.data.universe_model import (
    DERIVED,
    EXACT,
    FAIL,
    REGISTER,
    UNCERTAIN,
    UNKNOWN,
    VERIFIED_REPLACEMENT,
    WARN,
    CorporateAction,
    Delisting,
    Identifier,
    Instrument,
    Listing,
    TickerSpan,
    UniverseModel,
    UniverseModelError,
    from_u0_snapshot,
)

OBS = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # our observation time (U0 run)
D = date


def inst(iid: str, obs: datetime = OBS) -> Instrument:
    return Instrument(iid, f"Synthetic {iid}", "common_stock", "US", "test", obs)


def listing(iid, frm, to=None, obs: datetime = OBS, src=REGISTER) -> Listing:
    return Listing(iid, "XTST", frm, to, src, "test", obs)


def span(iid, ticker, frm, to=None, obs: datetime = OBS) -> TickerSpan:
    return TickerSpan(iid, ticker, frm, to, "test", obs)


def base_model() -> UniverseModel:
    """AAA: listed 2016-01-04 → 2020-06-30 (cash merger 12.5/share), ticker AAA whole time.
    BBB: listed 2021-01-04 → open; took over ticker AAA from 2022-01-03 (reuse).
    CCC: listed 2018-03-01 → 2019-12-31, delisting category unknown.
    DDD: listed 2016-01-04 → open, identifier match uncertain only."""
    return UniverseModel(
        version="test-1",
        instruments=[inst("I-AAA"), inst("I-BBB"), inst("I-CCC"), inst("I-DDD")],
        listings=[
            listing("I-AAA", D(2016, 1, 4), D(2020, 6, 30)),
            listing("I-BBB", D(2021, 1, 4)),
            listing("I-CCC", D(2018, 3, 1), D(2019, 12, 31)),
            listing("I-DDD", D(2016, 1, 4)),
        ],
        ticker_spans=[
            span("I-AAA", "AAA", D(2016, 1, 4), D(2020, 7, 1)),
            span("I-BBB", "BBBX", D(2021, 1, 4), D(2022, 1, 3)),
            span("I-BBB", "AAA", D(2022, 1, 3)),
            span("I-CCC", "CCC", D(2018, 3, 1), D(2020, 1, 1)),
            span("I-DDD", "DDD", D(2016, 1, 4)),
        ],
        identifiers=[
            Identifier("I-AAA", "figi", "BBG000000AAA", None, None, EXACT, "test", OBS),
            Identifier("I-BBB", "figi", "BBG000000BBB", None, None, DERIVED, "test", OBS),
            Identifier("I-DDD", "figi", "BBG000000DDD", None, None, UNCERTAIN, "test", OBS),
        ],
        delistings=[
            Delisting(
                "I-AAA", D(2020, 6, 30), VERIFIED_REPLACEMENT, 12.5, "test", OBS, "cash merger"
            ),
            Delisting("I-CCC", D(2019, 12, 31), UNKNOWN, None, "test", OBS),
        ],
        corporate_actions=[
            CorporateAction("I-DDD", "split", D(2019, 5, 2), 4.0, None, "test", OBS),
        ],
    )


# --- structure ---------------------------------------------------------------------------------


def test_base_model_is_structurally_valid():
    assert [i for i in base_model().validate() if i.level == FAIL] == []


def test_overlapping_listing_intervals_fail():
    m = base_model()
    m.listings.append(listing("I-AAA", D(2020, 6, 30)))  # starts on the old listed_to
    assert any(i.level == FAIL and "overlapping listing" in i.message for i in m.validate())


def test_listed_to_before_listed_from_fails():
    m = base_model()
    m.listings.append(listing("I-BBB", D(2025, 1, 2), D(2024, 1, 2)))
    assert any("listed_to before listed_from" in i.message for i in m.validate())


def test_reused_ticker_must_have_disjoint_spans():
    m = base_model()
    # BBB takes AAA one day too early: [2020-06-30, …) overlaps AAA's [2016-01-04, 2020-07-01)
    m.ticker_spans = [
        s for s in m.ticker_spans if not (s.ticker == "AAA" and s.instrument_id == "I-BBB")
    ]
    m.ticker_spans.append(span("I-BBB", "AAA", D(2020, 6, 30)))
    issues = m.validate()
    assert any(i.level == FAIL and i.table == "ticker_spans" and "AAA" in i.message for i in issues)
    with pytest.raises(UniverseError):
        PointInTimeUniverse(m)  # a model with FAIL issues is never accepted silently


def test_adjacent_half_open_spans_do_not_overlap():
    m = base_model()
    m.ticker_spans.append(span("I-BBB", "ZZZ", D(2020, 7, 1), D(2020, 7, 2)))
    m.ticker_spans.append(span("I-CCC", "ZZZ", D(2020, 7, 2), D(2020, 7, 3)))
    assert not any(i.table == "ticker_spans" for i in m.validate())


def test_empty_span_and_bad_enums_fail():
    m = base_model()
    m.ticker_spans.append(span("I-BBB", "EEE", D(2021, 1, 4), D(2021, 1, 4)))
    m.identifiers.append(Identifier("I-BBB", "cik", "1", None, None, "guess", "test", OBS))
    m.delistings.append(Delisting("I-BBB", D(2021, 1, 4), "zeroed", None, "test", OBS))
    msgs = [i.message for i in m.validate() if i.level == FAIL]
    assert any("empty or negative span" in s for s in msgs)
    assert any("bad match_confidence" in s for s in msgs)
    assert any("bad category" in s for s in msgs)


def test_verified_replacement_requires_value_and_unknown_is_allowed():
    m = base_model()
    m.delistings[0] = Delisting("I-AAA", D(2020, 6, 30), VERIFIED_REPLACEMENT, None, "test", OBS)
    assert any("verified_replacement without value" in i.message for i in m.validate())
    # category unknown (CCC) is structurally fine: valuation policy is D-020's job, not the model's
    assert not any("I-CCC" in i.message and i.level == FAIL for i in m.validate())


def test_listing_end_without_delisting_event_warns():
    m = base_model()
    m.delistings = [d for d in m.delistings if d.instrument_id != "I-CCC"]
    issues = m.validate()
    assert any(
        i.level == WARN and "I-CCC" in i.message and "without a delisting event" in i.message
        for i in issues
    )
    assert m.status() == "WARN"


# --- membership and interval boundaries --------------------------------------------------------


def test_listing_membership_is_inclusive_at_both_ends():
    u = PointInTimeUniverse(base_model(), mode=RESEARCH)
    assert "AAA" in u.members(D(2016, 1, 4)).tickers
    assert "AAA" not in u.members(D(2016, 1, 3)).tickers
    assert "AAA" in u.members(D(2020, 6, 30)).tickers  # last trading day still a member
    assert "AAA" not in u.members(D(2020, 7, 1)).tickers


def test_ticker_spans_are_half_open_on_change_day():
    u = PointInTimeUniverse(base_model(), mode=RESEARCH)
    assert u.ticker_on("I-BBB", D(2021, 12, 31)) == "BBBX"
    assert u.ticker_on("I-BBB", D(2022, 1, 3)) == "AAA"  # new ticker valid on the change day
    assert u.instrument_for("BBBX", D(2022, 1, 3)) is None
    assert u.members(D(2022, 1, 3)).tickers == ("AAA", "DDD")
    assert u.members(D(2021, 12, 31)).tickers == ("BBBX", "DDD")


def test_reused_ticker_resolves_by_date_never_merged():
    u = PointInTimeUniverse(base_model(), mode=RESEARCH)
    assert u.instrument_for("AAA", D(2019, 6, 3)) == "I-AAA"
    assert u.instrument_for("AAA", D(2020, 7, 1)) is None  # gap: nobody owns the ticker
    assert u.instrument_for("AAA", D(2023, 1, 3)) == "I-BBB"


def test_ambiguous_ticker_raises_instead_of_picking_one():
    m = base_model()
    # two instruments valid for the same ticker on one day (bypass validate: build the universe
    # first, then corrupt the model, as a loader bug would)
    u = PointInTimeUniverse(m, mode=RESEARCH)
    m.ticker_spans.append(span("I-DDD", "AAA", D(2023, 1, 1), D(2023, 1, 5)))
    with pytest.raises(AmbiguousTicker):
        u.instrument_for("AAA", D(2023, 1, 3))
    with pytest.raises(AmbiguousTicker):
        u.members(D(2023, 1, 3))


def test_uncertain_matches_excluded_in_evaluation_but_counted():
    ev = PointInTimeUniverse(base_model(), mode=EVALUATION).members(D(2019, 6, 3))
    rs = PointInTimeUniverse(base_model(), mode=RESEARCH).members(D(2019, 6, 3))
    assert ev.tickers == ("AAA", "CCC")
    assert ev.excluded_uncertain == ("I-DDD",)
    assert rs.tickers == ("AAA", "CCC", "DDD")
    assert rs.excluded_uncertain == ()


def test_l2_requires_a_bar_with_volume_when_bar_info_supplied():
    bars = {("I-AAA", D(2019, 6, 3)): True, ("I-CCC", D(2019, 6, 3)): False}
    u = PointInTimeUniverse(base_model(), has_bar=lambda i, d: bars.get((i, d)))
    ms = {m.instrument_id: m for m in u.members(D(2019, 6, 3)).members}
    assert ms["I-AAA"].layer == "L2" and ms["I-AAA"].tradable_known
    assert "I-CCC" not in ms  # listed (L1) but no bar → not orderable
    u0 = PointInTimeUniverse(base_model())
    m0 = u0.members(D(2019, 6, 3)).members[0]
    assert m0.layer == "L1" and not m0.tradable_known


# --- observed_at vs effective (D-023) ---------------------------------------------------------


def test_knowledge_gap_counts_facts_observed_after_asof():
    ms = PointInTimeUniverse(base_model(), mode=RESEARCH).members(D(2019, 6, 3))
    # every fact was observed in 2026: none of it was knowable in 2019
    assert ms.facts_total == 3 and ms.knowledge_gap == 3
    pit_obs = datetime(2019, 6, 3, 23, 0, tzinfo=UTC)  # a U0-style snapshot taken that day
    m = base_model()
    m.listings[0] = listing("I-AAA", D(2016, 1, 4), D(2020, 6, 30), obs=pit_obs)
    ms2 = PointInTimeUniverse(m, mode=RESEARCH).members(D(2019, 6, 3))
    assert ms2.knowledge_gap == 2


def test_knowledge_at_hides_facts_observed_later():
    m = base_model()
    early = datetime(2018, 1, 1, tzinfo=UTC)
    m.listings[3] = listing("I-DDD", D(2016, 1, 4), obs=early)
    m.ticker_spans[4] = span("I-DDD", "DDD", D(2016, 1, 4), obs=early)
    u = PointInTimeUniverse(m, mode=RESEARCH)
    assert u.members(D(2019, 6, 3)).tickers == ("AAA", "CCC", "DDD")
    assert u.members(D(2019, 6, 3), knowledge_at=early).tickers == ("DDD",)


# --- delisting and position events (D-020/D-021) ----------------------------------------------


def test_position_events_are_separate_from_membership():
    u = PointInTimeUniverse(base_model(), mode=RESEARCH)
    ev = u.events_for_positions(D(2019, 1, 1), D(2020, 12, 31))
    kinds = [(e.instrument_id, e.kind, e.effective_date, e.category, e.value_per_share) for e in ev]
    assert kinds == [
        ("I-DDD", "split", D(2019, 5, 2), None, None),
        ("I-CCC", "delisting", D(2019, 12, 31), UNKNOWN, None),
        ("I-AAA", "delisting", D(2020, 6, 30), VERIFIED_REPLACEMENT, 12.5),
    ]
    assert ev[2].ticker == "AAA"
    # the day after its delisting AAA is no longer a member, yet the event is still returned
    assert "AAA" not in u.members(D(2020, 7, 1)).tickers
    assert u.delisting_for("I-CCC").category == UNKNOWN
    assert u.delisting_for("I-BBB") is None
    assert u.events_for_positions(D(2021, 1, 1), D(2021, 12, 31)) == []


# --- prices vs listing (missing prices) --------------------------------------------------------


def test_check_prices_flags_bars_after_delisting_and_missing_bars():
    m = base_model()
    bars = {
        "I-AAA": [D(2016, 1, 4) + timedelta(days=i) for i in range(0, 1700)],
        "I-CCC": [D(2018, 3, 1)],
        "I-DDD": [D(2016, 1, 4)],
    }  # AAA keeps printing bars ~months past 2020-06-30; BBB has none
    issues = m.check_prices(bars)
    msgs = {(i.level, i.message) for i in issues}
    n_after = sum(1 for d in bars["I-AAA"] if d > D(2020, 6, 30))
    assert n_after == 60
    assert (FAIL, f"I-AAA: {n_after} bars after listed_to") in msgs
    assert (WARN, "I-BBB: no price bars") in msgs  # open listing: warn only
    assert not any("I-CCC" in s for _, s in msgs)
    m2 = base_model()
    assert (FAIL, "I-CCC: no price bars") in {(i.level, i.message) for i in m2.check_prices({})}


def test_check_prices_warns_when_first_bar_is_late_vs_register():
    m = base_model()
    issues = m.check_prices({"I-CCC": [D(2018, 6, 1), D(2018, 6, 4)]})
    assert any(
        i.level == WARN and "first bar" in i.message and "I-CCC" in i.message for i in issues
    )


# --- persistence, pilot wrapper, U0 limitation ----------------------------------------------


def test_parquet_roundtrip_is_lossless_and_immutable(tmp_path):
    m = base_model()
    d = m.write(tmp_path)
    assert (d / "manifest.json").exists()
    back = UniverseModel.read(tmp_path, "test-1")
    assert back == m
    with pytest.raises(UniverseModelError):
        m.write(tmp_path)


def test_pilot_universe_is_not_point_in_time():
    u = PilotUniverse(["NVDA", "AMD", "AMD"], version="pilot-19")
    ms = u.members(D(2016, 1, 4))
    assert ms.tickers == ("AMD", "NVDA")
    assert ms.knowledge_gap is None and ms.source == "pilot_list"
    assert not ms.members[0].tradable_known
    assert u.instrument_for("AMD", D(2016, 1, 4)) == "AMD"
    assert u.events_for_positions(D(2016, 1, 1), D(2026, 1, 1)) == []


def test_u0_snapshots_cannot_rebuild_the_past():
    with pytest.raises(NotImplementedError):
        from_u0_snapshot("data/snapshots/nasdaq_trader/20261101T120000Z")
