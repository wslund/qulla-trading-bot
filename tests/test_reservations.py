"""P3: same-day priority and PRE_OPEN reservations (PORTFOLIO_DESIGN §4, §6; D-015, D-016)."""

from datetime import date

import pytest

from tradingbot.backtesting.account import Account, AccountError
from tradingbot.backtesting.ledger import check_identities
from tradingbot.backtesting.priority import (
    BASELINE,
    RESEARCH,
    SEEDED_RANDOM,
    Candidate,
    PriorityParams,
    prioritize,
)
from tradingbot.backtesting.reservations import (
    RESERVATION_EXCEEDED,
    classify_overrun,
    plan_reservation,
    release_unfilled,
    reserve_day,
)
from tradingbot.risk.costs import CostParams
from tradingbot.risk.limits import HALTED, NORMAL, LimitParams
from tradingbot.risk.sizing import COST_TO_RISK, INSUFFICIENT_CAPITAL, RiskParams
from tradingbot.signals.daily_trigger import Signal, TriggerParams

COSTS = CostParams()  # Tiered, per_trade, 25 bps
RISK = RiskParams(risk_per_trade=0.01)
LIM = LimitParams()
T = date(2024, 7, 2)
ASOF = date(2024, 7, 1)
PEAK = date(2024, 6, 1)


def signal(ticker, level=100.0, low_prev=96.0, adr=0.05, split=False) -> Signal:
    return Signal((ticker, PEAK), ticker, T, ASOF, level, low_prev, adr, split, TriggerParams())


def cand(ticker, **features) -> Candidate:
    return Candidate(signal(ticker), features)


def account(sek=200_000.0, costs=COSTS) -> Account:
    a = Account.from_sek(sek, costs, ASOF)
    a.mark({}, ASOF)
    return a


def run_day(a, cands, limits=LIM, risk=RISK, state=NORMAL, params=None, pending=None):
    pending = {} if pending is None else pending
    return reserve_day(
        a,
        prioritize(cands, params),
        asof=ASOF,
        on=T,
        state=state,
        risk=risk,
        costs=COSTS,
        limits=limits,
        stops={},
        pending_stops=pending,
    )


# --- priority (D-015) ---


def test_baseline_is_alphabetical_and_input_order_invariant() -> None:
    cands = [cand("MSFT"), cand("AAPL"), cand("NVDA")]
    a = [p.candidate.ticker for p in prioritize(cands)]
    b = [p.candidate.ticker for p in prioritize(list(reversed(cands)))]
    assert a == b == ["AAPL", "MSFT", "NVDA"]
    assert [p.rank for p in prioritize(cands)] == [1, 2, 3] and prioritize(cands)[
        0
    ].rule == BASELINE
    with pytest.raises(ValueError, match="more than one candidate per ticker"):
        prioritize([cand("AAPL"), cand("AAPL")])


def test_seeded_random_is_deterministic_and_needs_a_seed() -> None:
    cands = [cand(t) for t in ("A", "B", "C", "D", "E", "F")]
    p = PriorityParams(SEEDED_RANDOM, seed=7)
    once = [x.candidate.ticker for x in prioritize(cands, p)]
    again = [x.candidate.ticker for x in prioritize(list(reversed(cands)), p)]
    assert once == again and sorted(once) == ["A", "B", "C", "D", "E", "F"]
    other = [x.candidate.ticker for x in prioritize(cands, PriorityParams(SEEDED_RANDOM, seed=8))]
    assert other != once  # different pre-registered seed, different order
    with pytest.raises(ValueError, match="seed"):
        PriorityParams(SEEDED_RANDOM)
    with pytest.raises(ValueError, match="unknown priority mode"):
        PriorityParams("best_guess")


def test_research_hypothesis_ranks_on_t_minus_1_features_and_refuses_missing() -> None:
    cands = [
        cand("AAA", consol_pct_below_high=0.03, consol_tightness=0.02),
        cand("BBB", consol_pct_below_high=0.01, consol_tightness=0.03),
        cand("CCC", consol_pct_below_high=0.01, consol_tightness=0.01),
    ]
    order = [p.candidate.ticker for p in prioritize(cands, PriorityParams(RESEARCH))]
    assert order == ["CCC", "BBB", "AAA"]
    assert PriorityParams().mode == BASELINE  # never the default
    with pytest.raises(ValueError, match="finite 'consol_pct_below_high'"):
        prioritize([cand("AAA", consol_tightness=0.02)], PriorityParams(RESEARCH))
    with pytest.raises(ValueError, match="finite"):
        prioritize(
            [cand("AAA", consol_pct_below_high=float("nan"), consol_tightness=0.02)],
            PriorityParams(RESEARCH),
        )


# --- planning (3d on the plan) ---


def test_plan_reservation_sizes_on_daily_plan_and_prices_entry_costs() -> None:
    a = account()
    p = plan_reservation(signal("AAA"), a.equity_usd, RISK, COSTS)
    # risk 1 % of 19 047.6 = 190.5; plan fill 100.1, stop 96 -> 46 shares; cap 47.
    assert p.shares_ordered == 46 and p.plan.fill_est == pytest.approx(100.1)
    assert p.plan.stop_plan == 96.0 and p.risk_usd == pytest.approx(46 * 4.1)
    assert p.entry_commission == 0.35 and p.entry_fx == pytest.approx(46 * 100.1 * 0.0025)
    assert p.reservation_usd == pytest.approx(46 * 100.1 + 0.35 + p.entry_fx)
    assert p.cost_to_risk_plan < 0.25
    # 10 000 SEK at 0.5 %: one share, cost_to_risk on the plan fails -> no reservation.
    small = account(10_000.0)
    assert (
        plan_reservation(signal("AAA"), small.equity_usd, RiskParams(0.005), COSTS) == COST_TO_RISK
    )
    assert (
        plan_reservation(
            signal("AAA", level=5000.0, low_prev=4800.0), small.equity_usd, RISK, COSTS
        )
        == "zero_shares"
    )


# --- reserve_day: shared budget, limits, determinism ---


def test_four_accepted_fifth_max_positions_and_permutation_invariance() -> None:
    tickers = ["EEE", "BBB", "DDD", "AAA", "CCC"]

    def run(order):
        a = account()
        day = run_day(a, [cand(t) for t in order])
        return a, day

    a1, d1 = run(tickers)
    a2, d2 = run(sorted(tickers, reverse=True))
    assert [p.ticker for p in d1.accepted] == ["AAA", "BBB", "CCC", "DDD"]
    assert [(r.ticker, r.reason, r.stage) for r in d1.rejected] == [
        ("EEE", "max_positions", "limits")
    ]
    assert d1.priority_binding is True
    assert a1.ledger.events_frame().equals(a2.ledger.events_frame())  # bit-identical bookkeeping
    assert d1.rows()[:4] == d2.rows()[:4]
    # Reservations are earmarks, not assets: equity unchanged, identities hold.
    assert a1.equity_usd == pytest.approx(a1.start_cash_usd) and a1.reserved_usd > 0
    check_identities(a1)
    assert d1.snapshot_after.n_pending == 4 and d1.snapshot_before.n_pending == 0


def test_shared_risk_budget_across_same_day_candidates() -> None:
    a = account()
    # 46 shares × 4.1 = 188.6 risk each; 2 % of 19 047.6 = 381 -> two fit, third does not.
    day = run_day(a, [cand(t) for t in ("A", "B", "C")], limits=LimitParams(max_open_risk_pct=0.02))
    assert [p.ticker for p in day.accepted] == ["A", "B"]
    assert day.rejected[0].reason == "open_risk" and "open_risk=" in day.rejected[0].detail
    assert day.snapshot_after.open_risk_usd == pytest.approx(2 * 46 * 4.1)


def test_exact_capital_boundary_gross_exposure_and_insufficient_cash() -> None:
    # Sizing scales with equity, so pin the order to the position cap: risk 50 %, cap 100 %
    # -> shares = floor(equity / 100.1). With equity 15 060 that is 150 shares and a
    # reservation of 15 015 + 0.525 (150 × 0.0035) + 37.54 = 15 053.06: one fits, two do not.
    big = RiskParams(risk_per_trade=0.5, max_position_pct=1.0, max_cost_to_risk=1.0)

    def with_cash(cash):
        a = account()
        a.cash_usd = a.start_cash_usd = cash
        a.hwm_usd = cash
        a.mark({}, ASOF)
        return a

    a = with_cash(15_060.0)
    one = plan_reservation(signal("A"), a.equity_usd, big, COSTS)
    assert one.shares_ordered == 150 and one.reservation_usd == pytest.approx(15_053.0625)
    wide = LimitParams(max_open_risk_pct=None)  # 150 × 4.1 = 615 risk > 4 %: not the point here
    day = run_day(a, [cand("A"), cand("B")], risk=big, limits=wide)
    assert [p.ticker for p in day.accepted] == ["A"]
    assert day.rejected[0].reason == "gross_exposure"  # gross = 1.0 binds before cash
    assert a.available_usd == pytest.approx(15_060.0 - one.reservation_usd)
    # One cent short of the reservation: refused; the reason depends on which check is on.
    short = with_cash(one.reservation_usd - 0.01)
    assert run_day(short, [cand("A")], risk=big, limits=wide).rejected[0].reason == "gross_exposure"
    short2 = with_cash(one.reservation_usd - 0.01)
    none = LimitParams(max_open_risk_pct=None, max_gross_exposure=None)
    day2 = run_day(short2, [cand("A")], risk=big, limits=none)
    assert day2.accepted == () and day2.rejected[0].reason == "insufficient_cash"
    exact = with_cash(one.reservation_usd)
    assert len(run_day(exact, [cand("A")], risk=big, limits=wide).accepted) == 1  # exactly enough
    check_identities(a)
    check_identities(exact)


def test_state_blocks_everything_and_pending_stops_feed_the_snapshot() -> None:
    a = account()
    day = run_day(a, [cand("A"), cand("B")], state=HALTED)
    assert day.accepted == () and {r.reason for r in day.rejected} == {"halted"}
    assert a.reserved_usd == 0
    pending = {}
    run_day(a, [cand("A")], pending=pending)
    assert pending == {("A", PEAK): 96.0}  # planned stop recorded for the next snapshot
    # Same ticker again the same day: the account refuses a second reservation (stage account).
    day2 = run_day(a, [cand("A")], pending=pending)
    assert day2.rejected[0].stage in ("limits", "account")


def test_split_on_entry_day_plan_is_in_t_units_and_survives_open_phase_split() -> None:
    a = account()
    # Levels from compute_levels are already divided by split_ratio[T]: a 4:1 split day shows
    # level 25 (not 100). The reservation is priced in those units and apply_split leaves it.
    day = run_day(a, [Candidate(signal("A", level=25.0, low_prev=24.0, adr=0.05, split=True))])
    p = day.accepted[0]
    assert p.plan.fill_est == pytest.approx(25.025) and p.shares_ordered == 185  # 190.5 / 1.025
    before = a.reservations[("A", PEAK)]
    assert a.apply_split("A", 4.0, 25.0, T) == []
    assert a.reservations[("A", PEAK)] == before
    check_identities(a)


def test_classify_overrun_and_release_unfilled() -> None:
    a = account()
    day = run_day(a, [cand("A"), cand("B")])
    key = ("A", PEAK)
    res = a.reservations[key]
    assert classify_overrun(a, key, res.amount_usd) is None
    assert classify_overrun(a, key, res.amount_usd * 1.03) == RESERVATION_EXCEEDED  # fits in cash
    assert classify_overrun(a, key, a.cash_usd + 1.0) == INSUFFICIENT_CAPITAL
    with pytest.raises(AccountError, match="no_reservation"):
        classify_overrun(a, ("Z", PEAK), 1.0)
    released = release_unfilled(a, T)
    assert released == [("A", PEAK), ("B", PEAK)] and a.reservations == {}
    assert a.available_usd == pytest.approx(a.cash_usd) and len(day.accepted) == 2
    kinds = [e.kind for e in a.ledger.events]
    assert kinds.count("reserve") == 2 and kinds.count("release") == 2 and "buy" not in kinds
    check_identities(a)


def test_rows_are_loggable() -> None:
    a = account()
    day = run_day(a, [cand(t) for t in ("A", "B", "C", "D", "E")])
    rows = day.rows()
    assert len(rows) == 5 and sum(r["accepted"] for r in rows) == 4
    assert {r["reason"] for r in rows} == {"reserved", "max_positions"}
    assert all("key" in r and "ticker" in r for r in rows) and day.version == "1"
