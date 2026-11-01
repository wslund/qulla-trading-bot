"""Actual stop risk at fill against the P2 open-risk budget (D-018).

PRE_OPEN checks PLANNED risk (daily plan). An ORB or gap fill can carry more actual risk than
its plan, so several fills on one day could add up to more than ``max_open_risk_pct`` while
every PRE_OPEN check passed. The engine must refuse the fill that would break the budget,
in an order-invariant way (each order gets its planned risk plus an equal share of the
day's slack; it never reads another ticker's fill)."""

from dataclasses import replace

import pytest

from tradingbot.backtesting.ledger import check_identities
from tradingbot.backtesting.portfolio import OPEN_RISK_AT_FILL, run_portfolio
from tradingbot.risk.limits import LimitParams
from tradingbot.risk.sizing import RiskParams
from tradingbot.signals.state import Status

from .test_orb import make_session
from .test_portfolio import params, universe
from .test_trades import RISE_THEN_FADE

SLIP = 0.001
TICKERS = ["AAA", "BBB", "CCC"]
# Sizing must be risk-limited (not cap-limited) for the ORB plan to carry fewer shares than
# the daily plan, which is what leaves room in the reservation for a gap fill.
RISK = RiskParams(risk_per_trade=0.005, max_position_pct=1.0, max_cost_to_risk=1.0)
LIMIT = 0.0155  # budget 295 USD on 19 047: planned 3 × 93 = 280 fits, gap fills do not
GAPS = {"AAA": 0.0, "BBB": 0.08, "CCC": 0.08}


def gap_orb_day(level: float, session_date, gap: float = 0.05, orb_low: float = 0.955):
    """Opening range well below the level with a low far under it (ORB plan sized on the ADR
    cap, fewer shares than the daily plan), then a fill bar that OPENS ``gap`` above the
    trigger: the stop follows the fill (ADR cap) and the actual risk is ~(1+gap) × plan."""
    bars = {k: (0.98 * level, 0.985 * level, orb_low * level, 0.982 * level) for k in range(5)}
    bars[6] = (
        (1 + gap) * level,
        (1 + gap + 0.01) * level,
        (1 + gap - 0.002) * level,
        (1 + gap) * level,
    )
    return make_session(session_date, bars, base=0.98 * level, band=0.002 * level)


def setup(limit_pct=LIMIT, gaps=None):
    gaps = GAPS if gaps is None else gaps
    series, cands, t0, f = universe(RISE_THEN_FADE, TICKERS)
    level = f / (1 + SLIP)
    frames = {(t, t0): gap_orb_day(level, t0, gaps.get(t, 0.0)) for t in TICKERS}
    p = replace(
        params(risk=RISK, limits=LimitParams(max_open_risk_pct=limit_pct)), entry_model="orb"
    )
    return series, cands, t0, frames, p


def audit_day(res, t0):
    return next(a for a in res.risk_audit if a["date"] == t0)


def test_actual_risk_sum_never_exceeds_the_budget_even_when_planned_sum_fits() -> None:
    series, cands, t0, frames, p = setup()
    res = run_portfolio(series, cands, p, minute_bars=lambda t, d: frames.get((t, d)))
    a = audit_day(res, t0)
    # All three orders were reservable on the daily plan: planned sum below the budget.
    assert a["n_orders"] == 3 and a["planned_sum"] <= a["budget"]
    # Had every fill been accepted the actual sum would have broken the budget (the gap
    # raises each fill's risk above its plan): that is what the check prevents.
    assert a["actual_if_all_filled"] > a["budget"]
    assert a["actual_sum"] <= a["budget"] + 1e-9
    refused = [r for r in res.rejections if r.date == t0 and r.reason == OPEN_RISK_AT_FILL]
    filled = [t for t in res.trades if t.entry_date == t0]
    assert [r.ticker for r in refused] == ["BBB", "CCC"] and [t.ticker for t in filled] == ["AAA"]
    # Each accepted fill stayed within its own cap: planned_i + slack / n.
    for d in res.decisions:
        if d["reason"] == "approved":
            assert d["actual_risk"] <= d["risk_cap"] + 1e-9
        if d["reason"] == OPEN_RISK_AT_FILL:
            assert d["actual_risk"] > d["risk_cap"]
    # A refused fill never consumed the key and never touched the account.
    for r in refused:
        assert res.registry.status((r.ticker, r.peak_date)) == Status.ARMED
    assert res.ledger_frame().set_index("date").loc[t0, "n_positions"] == len(filled)
    check_identities(res.account)


def test_without_gap_all_four_fill_and_the_audit_is_within_budget() -> None:
    series, cands, t0, frames, p = setup(gaps={})
    res = run_portfolio(series, cands, p, minute_bars=lambda t, d: frames.get((t, d)))
    a = audit_day(res, t0)
    assert a["actual_sum"] <= a["budget"] and a["actual_if_all_filled"] == pytest.approx(
        a["actual_sum"]
    )
    assert len([t for t in res.trades if t.entry_date == t0]) == 3
    assert not [r for r in res.rejections if r.reason == OPEN_RISK_AT_FILL]


def test_exact_cap_is_allowed_one_cent_over_is_refused() -> None:
    series, cands, t0, frames, p = setup()
    res = run_portfolio(series, cands, p, minute_bars=lambda t, d: frames.get((t, d)))
    d = next(x for x in res.decisions if x["reason"] == OPEN_RISK_AT_FILL)
    n = audit_day(res, t0)["n_orders"]
    e = res.account.start_cash_usd
    # Widen the budget by exactly n × (actual − cap): the refused order's cap becomes its
    # actual risk (every order gets 1/n of the extra slack) -> allowed at the boundary.
    needed = d["actual_risk"] - d["risk_cap"]
    exact = replace(
        p, limits=LimitParams(max_open_risk_pct=p.limits.max_open_risk_pct + n * needed / e)
    )
    r2 = run_portfolio(series, cands, exact, minute_bars=lambda t, d: frames.get((t, d)))
    d2 = next(x for x in r2.decisions if x["ticker"] == d["ticker"] and x["date"] == t0)
    assert d2["risk_cap"] == pytest.approx(d2["actual_risk"]) and d2["reason"] == "approved"
    # One cent less budget per order -> refused again.
    tight = replace(
        p, limits=LimitParams(max_open_risk_pct=exact.limits.max_open_risk_pct - n * 0.01 / e)
    )
    r3 = run_portfolio(series, cands, tight, minute_bars=lambda t, d: frames.get((t, d)))
    d3 = next(x for x in r3.decisions if x["ticker"] == d["ticker"] and x["date"] == t0)
    assert d3["reason"] == OPEN_RISK_AT_FILL and d3["actual_risk"] - d3[
        "risk_cap"
    ] == pytest.approx(0.01)


def test_order_invariance_with_the_fill_check() -> None:
    series, cands, t0, frames, p = setup()
    r1 = run_portfolio(series, cands, p, minute_bars=lambda t, d: frames.get((t, d)))
    rev = list(reversed(TICKERS))
    r2 = run_portfolio(
        {t: series[t] for t in rev},
        {t: cands[t] for t in rev},
        p,
        minute_bars=lambda t, d: frames.get((t, d)),
    )
    assert r1.account.ledger.events_frame().equals(r2.account.ledger.events_frame())
    assert [t.to_row() for t in r1.trades] == [t.to_row() for t in r2.trades]
    assert sorted((r.ticker, r.reason) for r in r1.rejections if r.date == t0) == sorted(
        (r.ticker, r.reason) for r in r2.rejections if r.date == t0
    )
    # The cap of each order depends only on PRE_OPEN quantities (same for every ticker).
    caps = {d["ticker"]: d["risk_cap"] for d in r1.decisions}
    assert len(set(round(c, 9) for c in caps.values())) == 1


def test_open_positions_count_in_the_budget_and_stop_gap_frees_it_next_day() -> None:
    # Day 1: two ORB fills. Day 2: both gap under their stops at the open (exit), and two new
    # candidates appear for other tickers. PRE_OPEN of day 2 still counts the two positions'
    # risk (exits happen later), so the new orders see a smaller budget.
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB", "CCC", "DDD"])
    level = f / (1 + SLIP)
    from tradingbot.data.quality import trading_sessions

    late = {}
    for t in ("CCC", "DDD"):
        s = series[t].copy()
        sessions = trading_sessions("XNYS", s["date"].iloc[0], "2026-12-31")[1 : len(s) + 1]
        s["date"] = [d.date() for d in sessions]
        late[t] = s
        series[t] = s
        from tradingbot.scanner.features import compute_features
        from tradingbot.scanner.rules import apply_rules

        cands[t] = apply_rules(compute_features(s))
    t1 = cands["CCC"].loc[cands["CCC"]["is_candidate"].astype(bool), "date"].iloc[0]
    for t in ("AAA", "BBB"):
        s = series[t]
        i1 = int(s.index[s["date"] == t1][0])
        s.loc[i1, ["open", "high", "low", "close", "adj_close"]] = [0.9 * level] * 5
    frames = {
        ("AAA", t0): gap_orb_day(level, t0, 0.0),
        ("BBB", t0): gap_orb_day(level, t0, 0.0),
        ("CCC", t1): gap_orb_day(level, t1, 0.0),
        ("DDD", t1): gap_orb_day(level, t1, 0.0),
    }
    p = replace(params(risk=RISK, limits=LimitParams(max_open_risk_pct=LIMIT)), entry_model="orb")
    res = run_portfolio(series, cands, p, minute_bars=lambda t, d: frames.get((t, d)))
    a0, a1 = audit_day(res, t0), audit_day(res, t1)
    assert (
        a0["positions_risk"] == 0 and a1["positions_risk"] > 0
    )  # AAA/BBB still open at PRE_OPEN t1
    assert a1["actual_sum"] + a1["positions_risk"] <= a1["budget"] + 1e-9
    assert all(t.exit_reason == "stop_gap" for t in res.trades if t.ticker in ("AAA", "BBB"))
    assert res.ledger_frame().set_index("date").loc[t1, "n_positions"] == len(
        [t for t in res.trades if t.entry_date == t1]
    )
    check_identities(res.account)


def test_daily_model_results_are_unchanged_when_no_overrun() -> None:
    from tradingbot.backtesting.trades import run_ticker
    from tradingbot.risk.sizing import RiskApprover

    from .test_portfolio import TP
    from .test_risk import RISK_ONE, TIERED, acct

    series, cands, t0, f = universe(RISE_THEN_FADE, ["SET"])
    ref = run_ticker(
        series["SET"],
        cands["SET"],
        RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED),
        costs=TIERED,
        trigger=TP,
    )
    res = run_portfolio(series, cands, params())
    assert res.trades[0].to_row() == {**ref[0][0].to_row()}
    assert all(a["actual_sum"] <= a["budget"] + 1e-9 for a in res.risk_audit)
