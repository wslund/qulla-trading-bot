"""U1 step B: Universe in the portfolio engine (D-021–D-024). Deterministic, offline.

The "pilot regression" tests here are the ones that prove an existing pilot run is unchanged:
``test_default_universe_is_pilot_and_bit_identical`` and
``test_non_binding_point_in_time_universe_matches_pilot``; together with the pre-U1B tests
``test_single_ticker_matches_run_ticker_when_limits_do_not_bind`` (test_portfolio),
``test_daily_model_results_are_unchanged_when_no_overrun`` (test_open_risk_at_fill) and
``test_write_run_is_reproducible_and_complete`` (test_report), all of which still call
``run_portfolio`` without a universe.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from tradingbot.backtesting.portfolio import (
    DELISTING_NOT_EXECUTED,
    NOT_IN_UNIVERSE,
    QUALITY_FAIL,
    UNRESOLVED_DELISTING,
    run_portfolio,
)
from tradingbot.backtesting.report import build_report, render_text, write_run
from tradingbot.backtesting.universe import (
    EVALUATION,
    RESEARCH,
    AmbiguousTicker,
    PilotUniverse,
    PointInTimeUniverse,
    UniverseError,
)
from tradingbot.data.universe_model import (
    EXACT,
    REGISTER,
    UNCERTAIN,
    UNKNOWN,
    VERIFIED_REPLACEMENT,
    CorporateAction,
    Delisting,
    Identifier,
    Instrument,
    Listing,
    TickerSpan,
    UniverseModel,
    UnresolvedSeriesError,
)
from tradingbot.signals.state import Status

from .test_features import apply_split
from .test_portfolio import params, universe
from .test_trades import RISE_THEN_FADE

LATE = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # observed in 2026 (U0-style)
EARLY = datetime(2000, 1, 1, tzinfo=UTC)  # observed before every simulated day
FAR = date(2099, 1, 1)


def _first_last(series: dict[str, pd.DataFrame]) -> tuple[date, date]:
    first = min(pd.Timestamp(s["date"].iloc[0]).date() for s in series.values())
    last = max(pd.Timestamp(s["date"].iloc[-1]).date() for s in series.values())
    return first, last


def model_for(
    series: dict[str, pd.DataFrame],
    *,
    listed_from: dict[str, date] | None = None,
    listed_to: dict[str, date] | None = None,
    delist: dict[str, Delisting] | None = None,
    obs: datetime = EARLY,
    version: str = "synthetic-1",
) -> UniverseModel:
    """One instrument I-<ticker> per series key, listed over the whole series unless told
    otherwise, ticker = key the whole time, exact identifier."""
    first, last = _first_last(series)
    m = UniverseModel(version=version)
    for t in sorted(series):
        iid = f"I-{t}"
        frm = (listed_from or {}).get(t, first)
        to = (listed_to or {}).get(t)
        m.instruments.append(Instrument(iid, f"Synthetic {t}", "common_stock", "US", "test", obs))
        m.listings.append(Listing(iid, "XTST", frm, to, REGISTER, "test", obs))
        m.ticker_spans.append(TickerSpan(iid, t, frm, None, "test", obs))
        m.identifiers.append(Identifier(iid, "figi", f"BBG{t}", None, None, EXACT, "test", obs))
        if delist and t in delist:
            m.delistings.append(delist[t])
    assert not [i for i in m.validate() if i.level == "FAIL"], m.validate()
    return m


def same_run(a, b) -> None:
    assert a.ledger_frame().equals(b.ledger_frame())
    assert a.account.ledger.events_frame().equals(b.account.ledger.events_frame())
    assert [t.to_row() for t in a.trades] == [t.to_row() for t in b.trades]
    assert [(r.ticker, r.date, r.stage, r.reason) for r in a.rejections] == [
        (r.ticker, r.date, r.stage, r.reason) for r in b.rejections
    ]


# --- pilot regression ----------------------------------------------------------------------


def test_default_universe_is_pilot_and_bit_identical() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    base = run_portfolio(series, cands, params())
    explicit = run_portfolio(series, cands, params(), universe=PilotUniverse(series))
    same_run(base, explicit)
    ua = base.universe_audit
    assert ua["source"] == "pilot_list" and ua["mode"] == "pilot" and ua["universe_version"]
    assert ua["point_in_time"] is False and ua["not_in_universe"] == 0
    assert ua["valid_for_evaluation"] is True and ua["invalid_reasons"] == []
    assert ua["instrument_map"] == {"AAA": "AAA", "BBB": "BBB"}
    assert ua["membership"]["knowledge_gap_share"] is None  # pilot has no facts to date
    assert base.summary()["versions"]["universe_version"] == ua["universe_version"]
    assert len(base.trades) == 2 and not any(r.stage == "universe" for r in base.rejections)


def test_non_binding_point_in_time_universe_matches_pilot() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    base = run_portfolio(series, cands, params())
    pit = PointInTimeUniverse(model_for(series), mode=EVALUATION)
    res = run_portfolio(series, cands, params(), universe=pit)
    same_run(base, res)
    ua = res.universe_audit
    assert ua["instrument_map"] == {"AAA": "I-AAA", "BBB": "I-BBB"}
    assert ua["point_in_time"] is True and ua["quality"]["status"] == "OK"
    assert ua["membership"]["knowledge_gap_share"] == 0.0  # everything observed in 2000
    assert ua["membership"]["l2_checked"] is False  # no has_bar supplied → L2 unknown


# --- membership rejection (D-021) ------------------------------------------------------------


def test_not_in_universe_blocks_new_order_without_consuming_key() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    base = run_portfolio(series, cands, params())
    # BBB is listed only from the day after its first candidate day: not a member at T−1.
    m = model_for(series, listed_from={"BBB": t0 + timedelta(days=1)})
    res = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m))
    rej = [r for r in res.rejections if r.reason == NOT_IN_UNIVERSE]
    # Rejected on t0 (T−1 = t0−1 < listed_from) and on t0+1 (T−1 = t0 < listed_from); the
    # key is not consumed, so BBB enters on the first candidate day it is a member.
    assert {(r.ticker, r.stage) for r in rej} == {("BBB", "universe")}
    assert [r.date for r in rej] == [t0, t0 + timedelta(days=1)]
    peak = rej[0].peak_date
    bbb = [t for t in res.trades if t.ticker == "BBB"]
    assert len(bbb) == 1 and bbb[0].entry_date > rej[-1].date and bbb[0].peak_date == peak
    a = next(t for t in base.trades if t.ticker == "AAA")
    assert a.to_row() == next(t for t in res.trades if t.ticker == "AAA").to_row()
    assert res.universe_audit["not_in_universe"] == 2
    rep = build_report(res)["summary"]
    assert rep["rejections"]["universe"] == {NOT_IN_UNIVERSE: 2}
    assert rep["universe"]["not_in_universe"] == 2 and rep["universe"]["mode"] == EVALUATION
    # Never a member: every BBB candidate row is rejected, the key stays ARMED throughout.
    # (The symbol cannot be resolved on the last bar, so the map is given; the series then
    # contradicts the listing → quality FAIL, which the audit reports as well.)
    never = run_portfolio(
        series,
        cands,
        params(),
        universe=PointInTimeUniverse(model_for(series, listed_from={"BBB": FAR})),
        instrument_ids={"AAA": "I-AAA", "BBB": "I-BBB"},
    )
    assert [t.ticker for t in never.trades] == ["AAA"]
    assert {(r.ticker, r.stage) for r in never.rejections if r.reason == NOT_IN_UNIVERSE} == {
        ("BBB", "universe")
    }
    assert never.registry.status(("BBB", peak)) == Status.ARMED
    assert never.universe_audit["invalid_reasons"] == [QUALITY_FAIL]


def test_uncertain_match_is_excluded_in_evaluation_but_not_in_research() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    m = model_for(series)
    m.identifiers = [
        Identifier("I-BBB", "figi", "BBGBBB", None, None, UNCERTAIN, "test", EARLY)
        if i.instrument_id == "I-BBB"
        else i
        for i in m.identifiers
    ]
    ev = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m, mode=EVALUATION))
    rs = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m, mode=RESEARCH))
    assert [t.ticker for t in ev.trades] == ["AAA"]
    assert ev.universe_audit["membership"]["excluded_uncertain"] == ["I-BBB"]
    assert sorted(t.ticker for t in rs.trades) == ["AAA", "BBB"]
    assert rs.universe_audit["membership"]["excluded_uncertain"] == []


# --- ticker reuse, ticker change, resolution (D-022) ----------------------------------------


def test_series_key_resolves_to_the_instrument_owning_the_ticker_on_the_last_bar() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    first, last = _first_last(series)
    m = model_for(series)  # I-AAA owns AAA over the whole series
    # An older, dead instrument that used the ticker AAA long before: disjoint span.
    old_to = first - timedelta(days=400)
    m.instruments.append(Instrument("I-OLD", "Old AAA", "common_stock", "US", "test", EARLY))
    m.listings.append(
        Listing("I-OLD", "XTST", old_to - timedelta(days=700), old_to, REGISTER, "test", EARLY)
    )
    m.ticker_spans.append(
        TickerSpan(
            "I-OLD", "AAA", old_to - timedelta(days=700), old_to + timedelta(days=1), "test", EARLY
        )
    )
    m.delistings.append(Delisting("I-OLD", old_to, UNKNOWN, None, "test", EARLY))
    res = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m))
    assert res.universe_audit["instrument_map"] == {"AAA": "I-AAA"}
    assert res.universe_audit["quality"]["status"] == "OK"
    assert len(res.trades) == 1


def test_reused_ticker_series_carrying_old_history_fails_quality() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    first, last = _first_last(series)
    # Today's AAA (I-AAA) was listed only 10 days before the end of the series; the vendor
    # series keyed "AAA" still carries months of bars that belong to someone else.
    m = model_for(series, listed_from={"AAA": last - timedelta(days=10)})
    res = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m))
    ua = res.universe_audit
    assert ua["quality"]["status"] == "FAIL"
    assert any("before listed_from" in i["message"] for i in ua["quality"]["issues"])
    assert ua["valid_for_evaluation"] is False and ua["invalid_reasons"] == [QUALITY_FAIL]
    assert res.trades == [] and all(
        r.reason == NOT_IN_UNIVERSE for r in res.rejections if r.stage == "universe"
    )


def test_ambiguous_or_unresolvable_series_key_raises() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    first, last = _first_last(series)
    m = model_for(series)
    m.instruments.append(Instrument("I-2", "Other", "common_stock", "US", "test", EARLY))
    m.listings.append(Listing("I-2", "XTST", first, None, REGISTER, "test", EARLY))
    m.ticker_spans.append(TickerSpan("I-2", "AAA", last, None, "test", EARLY))  # overlaps
    assert any(i.level == "FAIL" for i in m.validate())
    with pytest.raises(UniverseError):  # the constructor already refuses the model
        PointInTimeUniverse(m)
    # Corrupt after construction (as a loader bug would): lookup raises, never guesses.
    good = model_for(series)
    pit = PointInTimeUniverse(good)
    good.instruments.append(Instrument("I-2", "Other", "common_stock", "US", "test", EARLY))
    good.ticker_spans.append(TickerSpan("I-2", "AAA", last, None, "test", EARLY))
    with pytest.raises(AmbiguousTicker):
        run_portfolio(series, cands, params(), universe=pit)
    # Nobody owns the ticker on the last bar → explicit error, not an empty universe.
    m3 = model_for(series)
    m3.ticker_spans = [TickerSpan("I-AAA", "ZZZ", first, None, "test", EARLY)]
    with pytest.raises(UnresolvedSeriesError, match="no instrument owns"):  # D-025
        run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m3))
    # … unless the caller maps it explicitly.
    res = run_portfolio(
        series, cands, params(), universe=PointInTimeUniverse(m3), instrument_ids={"AAA": "I-AAA"}
    )
    assert len(res.trades) == 1


def test_ticker_change_keeps_the_instrument_a_member() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    first, last = _first_last(series)
    base = run_portfolio(series, cands, params())
    m = model_for(series)
    frm = first
    change = t0 + timedelta(days=3)  # renamed while the position is open
    m.ticker_spans = [
        TickerSpan("I-AAA", "OLD", frm, change, "test", EARLY),
        TickerSpan("I-AAA", "AAA", change, None, "test", EARLY),
    ]
    pit = PointInTimeUniverse(m)
    assert pit.ticker_on("I-AAA", t0) == "OLD" and pit.ticker_on("I-AAA", last) == "AAA"
    res = run_portfolio(series, cands, params(), universe=pit)
    same_run(base, res)  # membership is per instrument; the series key is today's symbol
    assert res.universe_audit["instrument_map"] == {"AAA": "I-AAA"}


# --- observed_at after the simulated day (D-023) ---------------------------------------------


def test_facts_observed_after_the_simulated_day_are_reported_as_knowledge_gap() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    late = run_portfolio(
        series, cands, params(), universe=PointInTimeUniverse(model_for(series, obs=LATE))
    )
    early = run_portfolio(
        series, cands, params(), universe=PointInTimeUniverse(model_for(series, obs=EARLY))
    )
    same_run(late, early)  # effective time decides membership (D-023) …
    ml, me = late.universe_audit["membership"], early.universe_audit["membership"]
    assert (
        ml["knowledge_gap_share"] == 1.0 and ml["max_knowledge_gap_share"] == 1.0
    )  # … gap reported
    assert me["knowledge_gap_share"] == 0.0
    assert ml["sessions_checked"] == me["sessions_checked"] >= 1
    assert ml["facts_total"] == ml["facts_observed_after_asof"] == 2 * ml["sessions_checked"]
    assert "100%" in render_text(build_report(late)["summary"])


# --- delisting reached by an open position (D-020/D-021/D-024 p. 1) --------------------------


def _delist_run(category: str, value: float | None, *, after_exit: bool = False):
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    base = run_portfolio(series, cands, params())
    tr = base.trades[0]
    if after_exit:
        eff = tr.exit_date + timedelta(days=1)
        s = series
    else:  # the main-list series ends on the delisting day, position still open
        dates = [pd.Timestamp(d).date() for d in series["AAA"]["date"]]
        eff = dates[dates.index(tr.entry_date) + 2]
        s = {"AAA": series["AAA"][series["AAA"]["date"] <= eff].reset_index(drop=True)}
    m = model_for(
        s,
        listed_to={"AAA": eff},
        delist={"AAA": Delisting("I-AAA", eff, category, value, "test", EARLY)},
    )
    res = run_portfolio(s, cands, params(), universe=PointInTimeUniverse(m))
    return res, eff, tr


def test_unresolved_delisting_in_open_position_invalidates_the_run() -> None:
    res, eff, tr = _delist_run(UNKNOWN, None)
    ua = res.universe_audit
    assert ua["valid_for_evaluation"] is False and ua["invalid_reasons"] == [UNRESOLVED_DELISTING]
    assert len(ua["position_events"]) == 1
    ev = ua["position_events"][0]
    assert ev["kind"] == "delisting" and ev["category"] == UNKNOWN and ev["date"] == eff
    assert ev["ticker"] == "AAA" and ev["shares_held"] == tr.shares
    # No execution: the position is simply open at the end, valued at the last main-list close.
    assert len(res.trades) == 1 and res.trades[0].open_at_end
    assert res.account.holdings["AAA"].shares == tr.shares
    assert {e["event"] for e in res.events} == {"universe_delisting"}
    rep = build_report(res)
    assert rep["summary"]["performance"]["valid_for_evaluation"] is False
    assert rep["summary"]["performance"]["invalid_reasons"] == [UNRESOLVED_DELISTING]
    assert rep["summary"]["universe"]["position_events"][0]["category"] == UNKNOWN
    assert not rep["equity_curve"]["valid_for_evaluation"].any()
    text = render_text(rep["summary"])
    assert "EJ GILTIG FÖR PRESTANDAUTVÄRDERING" in text and UNRESOLVED_DELISTING in text
    assert "Slut-equity" not in text and "Max drawdown" not in text
    assert "Avslag strategi" in text  # diagnostics are still printed


def test_verified_delisting_is_still_not_executed_and_flagged() -> None:
    res, eff, tr = _delist_run(VERIFIED_REPLACEMENT, 12.5)
    ua = res.universe_audit
    assert ua["invalid_reasons"] == [DELISTING_NOT_EXECUTED]
    assert ua["position_events"][0]["value_per_share"] == 12.5
    assert res.trades[0].open_at_end  # nothing was sold at 12.5


def test_delisting_after_the_exit_does_not_touch_the_run() -> None:
    res, eff, tr = _delist_run(UNKNOWN, None, after_exit=True)
    base = run_portfolio(*universe(RISE_THEN_FADE, ["AAA"])[:2], params())
    same_run(base, res)
    assert res.universe_audit["valid_for_evaluation"] is True
    assert res.universe_audit["position_events"] == []
    text = render_text(build_report(res)["summary"])
    assert "EJ GILTIG" not in text and "Slut-equity" in text


# --- split: series drives the engine, the universe only observes --------------------------------


def test_split_with_matching_corporate_action_is_unchanged_and_observed() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    s = series["AAA"]
    i0 = int(s.index[s["date"] == t0][0])
    split = {"AAA": apply_split(s, at=i0 + 2, ratio=2.0)}
    split_day = pd.Timestamp(split["AAA"].loc[i0 + 2, "date"]).date()
    base = run_portfolio(split, cands, params())
    m = model_for(split)
    m.corporate_actions.append(
        CorporateAction("I-AAA", "split", split_day, 2.0, None, "test", EARLY)
    )
    res = run_portfolio(split, cands, params(), universe=PointInTimeUniverse(m))
    same_run(base, res)
    assert res.trades[0].splits == 1
    ua = res.universe_audit
    assert ua["valid_for_evaluation"] is True
    assert [(e["kind"], e["date"]) for e in ua["position_events"]] == [("split", split_day)]


# --- L2 never reads day T (requirement 9) --------------------------------------------------------


def test_l2_check_only_sees_sessions_before_t() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    dates = [pd.Timestamp(d).date() for d in series["AAA"]["date"]]
    queried: list[tuple[str, date]] = []

    def has_bar(iid: str, d: date) -> bool | None:
        queried.append((iid, d))
        return True

    res = run_portfolio(
        series, cands, params(), universe=PointInTimeUniverse(model_for(series), has_bar=has_bar)
    )
    assert queried, "membership was never consulted"
    cand_days = {r.date for r in res.rejections} | {t.entry_date for t in res.trades}
    for _, d in queried:
        assert d < min(cand_days) or d in dates
        assert d not in cand_days or any(
            dates[i + 1] in cand_days for i in range(len(dates) - 1) if dates[i] == d
        )
    # every query is for the session before a candidate day, never the candidate day itself
    prev_of = {dates[i + 1]: dates[i] for i in range(len(dates) - 1)}
    assert {d for _, d in queried} <= {prev_of[c] for c in cand_days if c in prev_of}
    assert res.universe_audit["membership"]["l2_checked"] is True
    # L1 but no bar at T−1 → not orderable on T
    blocked = run_portfolio(
        series,
        cands,
        params(),
        universe=PointInTimeUniverse(model_for(series), has_bar=lambda iid, d: iid != "I-BBB"),
    )
    assert [t.ticker for t in blocked.trades] == ["AAA"]
    assert {(r.ticker, r.reason) for r in blocked.rejections if r.stage == "universe"} == {
        ("BBB", NOT_IN_UNIVERSE)
    }


# --- config / summary identity (requirement 6) ---------------------------------------------------


def test_run_files_carry_universe_identity(tmp_path) -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    m = model_for(series, version="synthetic-7")
    res = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m, mode=RESEARCH))
    d = write_run(res, tmp_path, run_id="r", args={"tickers": ["AAA"]})
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    sm = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    assert cfg["universe"] == {
        "universe_version": "synthetic-7",
        "source": "universe_model:synthetic-7",
        "mode": RESEARCH,
        "checksum": m.checksum(),
        "point_in_time": True,
        "instrument_map": {"AAA": "I-AAA"},
    }
    assert (
        sm["universe"]["checksum"] == m.checksum()
        and sm["versions"]["universe_version"] == "synthetic-7"
    )
    assert sm["report_version"] == "2" and sm["versions"]["portfolio_version"] == "4"
    assert sm["performance"]["valid_for_evaluation"] is True
    assert (
        "universe_version" not in pd.read_parquet(d / "trades.parquet").columns
    )  # Trade unchanged
    # a different model content → different checksum → different config hash
    m2 = model_for(series, version="synthetic-7", obs=LATE)
    res2 = run_portfolio(series, cands, params(), universe=PointInTimeUniverse(m2, mode=RESEARCH))
    d2 = write_run(res2, tmp_path / "b", run_id="r", args={"tickers": ["AAA"]})
    cfg2 = json.loads((d2 / "config.json").read_text(encoding="utf-8"))
    assert cfg2["config_hash"] != cfg["config_hash"]
    pilot = run_portfolio(series, cands, params())
    assert pilot.universe_audit["universe_version"].startswith("pilot")


# --- CLI: --universe <version> reads data/universe/<version> -------------------------------------


def test_cli_runs_with_a_stored_universe_model(tmp_path, capsys) -> None:
    from tradingbot.data.schema import FetchMeta
    from tradingbot.data.store import ParquetStore
    from tradingbot.scanner.pipeline import build_candidates, build_features

    from .test_portfolio import TP
    from .test_report import _load_cli

    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
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
        build_features(store, "test", t)
        build_candidates(store, "test", t)
    m = model_for(series, listed_from={"BBB": FAR}, version="hand-1")
    m.ticker_spans = [
        TickerSpan("I-AAA", "AAA", _first_last(series)[0], None, "test", EARLY),
        TickerSpan("I-BBB", "BBB", _first_last(series)[0], None, "test", EARLY),
    ]
    m.write(tmp_path)
    cli = _load_cli()
    common = ["--source", "test", "--tickers", "AAA", "BBB", "--equity-sek", "200000"]
    common += ["--risk", "0.01", "--slippage", str(TP.slippage_bps), "--data-dir", str(tmp_path)]
    out = cli.main(
        [*common, "--run-id", "u1", "--universe", "hand-1", "--universe-mode", "research"]
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    cfg = json.loads((out / "config.json").read_text(encoding="utf-8"))
    assert summary["universe"]["universe_version"] == "hand-1"
    assert (
        summary["universe"]["mode"] == "research"
        and summary["universe"]["checksum"] == m.checksum()
    )
    assert summary["rejections"]["universe"][NOT_IN_UNIVERSE] >= 1
    assert summary["performance"]["all_trades"]["n"] == 1
    assert cfg["universe"]["instrument_map"] == {"AAA": "I-AAA", "BBB": "I-BBB"}
    printed = capsys.readouterr().out
    assert "Universum: hand-1" in printed and "EJ GILTIG" in printed  # BBB bars before listed_from
    pilot = cli.main([*common, "--run-id", "p1"])
    ps = json.loads((pilot / "summary.json").read_text(encoding="utf-8"))
    assert ps["universe"]["universe_version"] == "pilot-2" and ps["universe"]["mode"] == "pilot"
    assert ps["performance"]["all_trades"]["n"] == 2 and ps["rejections"]["universe"] == {}
