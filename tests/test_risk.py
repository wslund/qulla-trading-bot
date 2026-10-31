"""Step 3d: position size, costs and acceptance on the assumed fill (RISK_MODEL §2, design §6)."""

from datetime import date

import pytest

from tradingbot.risk.costs import (
    FX_RATE_PILOT,
    IBKR_FIXED,
    IBKR_TIERED,
    CostParams,
    assumed_costs,
    deposit_fx_cost,
    planned_legs,
)
from tradingbot.risk.sizing import (
    APPROVED,
    COST_TO_RISK,
    EQUITY_START_SEK,
    INSUFFICIENT_CAPITAL,
    NOT_A_FILL,
    RISK_EXCEEDED,
    TOO_SMALL,
    ZERO_SHARES,
    Account,
    RiskApprover,
    RiskParams,
    account_from_sek,
    assess_fill,
    position_size,
)
from tradingbot.signals.daily_trigger import (
    FILL_CANDIDATE,
    TriggerParams,
    approve_and_record,
    as_approval,
    evaluate_day,
    walk_daily,
)
from tradingbot.signals.state import Status

from .test_daily_trigger import _armed_registry, _series_with_breakout, sig

TIERED = CostParams(commission=IBKR_TIERED, fx_mode="per_trade", fx_cost_bps=25.0)
FIXED = CostParams(commission=IBKR_FIXED, fx_mode="per_trade", fx_cost_bps=25.0)
RISK_HALF = RiskParams(risk_per_trade=0.005)
RISK_ONE = RiskParams(risk_per_trade=0.01)


def acct(costs: CostParams = TIERED, sek: float = EQUITY_START_SEK) -> Account:
    return account_from_sek(sek, costs)


def fill_outcome(level=100.0, low_prev=96.0, adr=0.05, open_=99.0, high=101.0, low=98.0):
    return evaluate_day(sig(level=level, low_prev=low_prev, adr=adr), open_, high, low)


# --- costs ---


def test_min_commission_kicks_in_for_small_orders() -> None:
    assert IBKR_TIERED.commission(0) == 0.0
    assert IBKR_TIERED.commission(1) == 0.35
    assert IBKR_TIERED.commission(100) == pytest.approx(0.35)
    assert IBKR_TIERED.commission(200) == pytest.approx(0.70)
    assert IBKR_FIXED.commission(1) == 1.0 and IBKR_FIXED.commission(300) == pytest.approx(1.5)


def test_planned_legs_depend_on_min_shares_for_partial() -> None:
    assert planned_legs(0, 50.0, TIERED) == ()
    two = planned_legs(2, 50.0, TIERED)
    assert [(leg.name, leg.shares) for leg in two] == [("entry", 2), ("exit", 2)]
    three = planned_legs(3, 50.0, TIERED)
    assert [(leg.name, leg.shares) for leg in three] == [("entry", 3), ("partial", 1), ("exit", 2)]
    four = planned_legs(4, 50.0, TIERED)
    assert [(leg.name, leg.shares) for leg in four] == [("entry", 4), ("partial", 1), ("exit", 3)]


def test_entry_slippage_is_derived_from_fill_not_added_twice() -> None:
    ref, slip_bps = 100.0, 10.0
    fill = ref * (1 + slip_bps / 10_000)  # as FillCandidate.fill_price
    ac = assumed_costs(2, fill, slip_bps, TIERED)
    assert ac.basis == "assumed"
    assert ac.per_leg["entry"]["slippage"] == pytest.approx(2 * ref * 0.001)
    assert ac.per_leg["exit"]["slippage"] == pytest.approx(2 * fill * 0.001)
    assert ac.slippage == pytest.approx(
        ac.per_leg["entry"]["slippage"] + ac.per_leg["exit"]["slippage"]
    )
    assert ac.commission == pytest.approx(0.70) and ac.partial_skipped is True
    assert ac.fx == pytest.approx(2 * fill * 0.0025 * 2)
    assert ac.total == pytest.approx(ac.commission + ac.slippage + ac.fx)
    # Cash for the entry: commission + FX only – slippage is already inside the fill.
    assert ac.entry_cash_cost == pytest.approx(0.35 + 2 * fill * 0.0025)


def test_fx_modes_usd_balance_versus_per_trade() -> None:
    per_trade = assumed_costs(2, 100.1, 10.0, TIERED)
    once = assumed_costs(2, 100.1, 10.0, CostParams(fx_mode="usd_balance", fx_cost_bps=25.0))
    assert per_trade.fx > 0 and once.fx == 0.0
    assert deposit_fx_cost(10_000, TIERED) == 0.0
    dep = deposit_fx_cost(10_000, CostParams(fx_mode="usd_balance", fx_cost_bps=25.0))
    assert dep == pytest.approx(10_000 / FX_RATE_PILOT * 0.0025)
    a_once = account_from_sek(10_000, CostParams(fx_mode="usd_balance", fx_cost_bps=25.0))
    a_per = account_from_sek(10_000, TIERED)
    assert (
        a_per.equity_usd == pytest.approx(10_000 / FX_RATE_PILOT) and a_per.fx_deposit_cost_usd == 0
    )
    assert a_once.equity_usd == pytest.approx(a_per.equity_usd - dep)
    assert a_once.cash_usd == pytest.approx(a_once.equity_usd)
    with pytest.raises(ValueError, match="fx_mode"):
        CostParams(fx_mode="hedged")


# --- position_size ---


def test_hand_calculated_example_from_design() -> None:
    # 40 USD stock, stop 38, risk 1 % of 950 USD -> 9.5 / 2 = 4.75 -> 4 shares.
    r = position_size(950.0, 40.0, 38.0, RISK_ONE)
    assert r.shares == 4 and r.reason == APPROVED and r.risk_amount == pytest.approx(9.5)
    assert r.capped_by_position_pct is False


def test_cap_by_max_position_pct() -> None:
    r = position_size(950.0, 40.0, 39.9, RISK_ONE)  # 95 shares by risk, 237.5/40 = 5 by cap
    assert r.shares == 5 and r.capped_by_position_pct is True


def test_zero_shares_and_too_small() -> None:
    assert position_size(952.0, 2000.0, 1900.0, RISK_HALF).reason == ZERO_SHARES
    r = position_size(952.0, 60.0, 55.0, RISK_ONE)  # 1 share × 60 < 100 USD
    assert r.shares == 0 and r.reason == TOO_SMALL
    assert position_size(952.0, 60.0, 55.0, RiskParams(0.01, min_position_value_usd=50)).shares == 1


def test_position_size_refuses_stop_at_or_above_fill() -> None:
    with pytest.raises(ValueError):
        position_size(950.0, 40.0, 40.0, RISK_ONE)


# --- assess_fill on the FillCandidate ---


def test_approved_decision_carries_shares_and_cost_breakdown() -> None:
    out = fill_outcome()  # fill 100.1, stop 96
    d = assess_fill(out, acct(), RISK_ONE, TIERED)
    assert d.approved and d.reason == APPROVED
    assert d.shares == 2 and d.shares_ordered == 2 and d.shares_reduced_at_fill is False
    assert d.actual_risk == pytest.approx(2 * (100.1 - 96))
    assert d.capital_required == pytest.approx(2 * 100.1 + 0.35 + 2 * 100.1 * 0.0025)
    assert d.costs is not None and d.cost_to_risk == pytest.approx(d.costs.total / d.risk_amount)
    assert d.cost_to_risk <= 0.25
    t = d.trace()
    assert t["shares"] == 2 and t["partial_skipped"] is True and t["sizing_version"] == "1"


def test_cost_to_risk_rejects_small_trade_at_half_percent_risk() -> None:
    """10 000 SEK, 0.5 % risk, 100 USD stock with a 4 % stop: one share, and the fixed
    costs of two orders exceed 25 % of the risk amount under Tiered."""
    d = assess_fill(fill_outcome(), acct(), RISK_HALF, TIERED)
    assert not d.approved and d.reason == COST_TO_RISK and d.shares == 0
    assert d.shares_ordered == 1 and d.cost_to_risk > 0.25
    assert d.costs is not None and d.costs.commission == pytest.approx(0.70)


def test_cost_to_risk_fixed_fails_where_tiered_passes() -> None:
    # Design §11 example: 40/38, risk 1 % of 950 USD -> 4 shares.
    out = evaluate_day(sig(level=39.96, low_prev=38.0, adr=0.2), 39.0, 40.5, 38.5)
    assert out.code == FILL_CANDIDATE and out.fill.fill_price == pytest.approx(40.0, rel=1e-4)
    a = Account(950.0, 950.0, FX_RATE_PILOT, "per_trade")
    tiered = assess_fill(out, a, RISK_ONE, TIERED)
    fixed = assess_fill(out, a, RISK_ONE, FIXED)
    assert tiered.approved and tiered.shares == 4 and tiered.cost_to_risk < 0.25
    assert not fixed.approved and fixed.reason == COST_TO_RISK and fixed.cost_to_risk > 0.25
    assert fixed.costs.commission == pytest.approx(3.0)  # three legs at 1 USD minimum


def test_tight_stop_is_capped_by_position_pct() -> None:
    d = assess_fill(fill_outcome(low_prev=98.6), acct(), RISK_ONE, TIERED)  # 1.5 % stop
    assert d.approved and d.shares == 2  # risk alone would give 6; cap 25 % of 952 / 100.1 = 2
    assert d.actual_risk < d.risk_amount
    # At 0.5 % risk the same trade (2 shares by cap) fails on cost_to_risk, not on size.
    half = assess_fill(fill_outcome(low_prev=98.6), acct(), RISK_HALF, TIERED)
    assert not half.approved and half.reason == COST_TO_RISK and half.shares_ordered == 2


def test_gap_fill_over_tolerance_reduces_shares_as_counterfactual() -> None:
    # adr 10 %: stop stays at low_prev 96; ordered on level 100.1 -> dist 4.1 -> 2 shares.
    # Gap open 104.9 -> fill 105.005, actual risk 2 × 9.0 = 18 > 9.52 × 1.25.
    out = fill_outcome(adr=0.10, open_=104.9, high=106.0, low=104.0)
    assert out.fill.gap_fill
    d = assess_fill(out, acct(), RISK_ONE, TIERED)
    assert d.approved and d.shares == 1 and d.shares_ordered == 2
    assert d.shares_reduced_at_fill is True
    assert d.actual_risk <= d.risk_amount
    # The same fill with min_position_value 200: the reduced count is too small -> no trade.
    d2 = assess_fill(out, acct(), RiskParams(0.01, min_position_value_usd=200), TIERED)
    assert not d2.approved and d2.reason == RISK_EXCEEDED and d2.shares == 0
    assert d2.actual_risk == pytest.approx(18.0, abs=0.05)


def test_exact_capital_boundary() -> None:
    out = fill_outcome()
    base = assess_fill(out, acct(), RISK_ONE, TIERED)
    a = Account(base.risk_amount / 0.01, base.capital_required, FX_RATE_PILOT, "per_trade")
    assert assess_fill(out, a, RISK_ONE, TIERED).approved
    short = Account(a.equity_usd, a.cash_usd - 0.01, FX_RATE_PILOT, "per_trade")
    d = assess_fill(out, short, RISK_ONE, TIERED)
    assert not d.approved and d.reason == INSUFFICIENT_CAPITAL
    assert (
        d.capital_required == pytest.approx(base.capital_required)
        and d.cash_available == a.cash_usd - 0.01
    )


def test_expensive_stock_gives_zero_shares() -> None:
    out = fill_outcome(level=2000.0, low_prev=1900.0, open_=1990.0, high=2010.0, low=1985.0)
    d = assess_fill(out, acct(), RISK_ONE, TIERED)
    assert not d.approved and d.reason == ZERO_SHARES and d.costs is None


def test_non_fill_outcome_is_never_approved() -> None:
    out = fill_outcome(high=100.0)  # no trigger
    d = assess_fill(out, acct(), RISK_ONE, TIERED)
    assert not d.approved and d.reason == NOT_A_FILL


def test_usd_balance_mode_lowers_cost_to_risk_but_charges_deposit() -> None:
    out = fill_outcome()
    once = CostParams(fx_mode="usd_balance", fx_cost_bps=25.0)
    d_once = assess_fill(out, acct(once), RISK_ONE, once)
    d_per = assess_fill(out, acct(TIERED), RISK_ONE, TIERED)
    assert d_once.cost_to_risk < d_per.cost_to_risk
    assert d_once.costs.fx == 0 and acct(once).fx_deposit_cost_usd > 0


# --- integration with 3c and the registry ---


def test_rejected_decision_never_consumes_key() -> None:
    reg, _ = _armed_registry()
    out = fill_outcome()
    d = assess_fill(out, acct(), RISK_HALF, TIERED)
    assert d.reason == COST_TO_RISK
    approved, reason, extra = as_approval(d)
    assert (approved, reason) == (False, COST_TO_RISK) and extra["shares"] == 0
    assert approve_and_record(reg, out, approved, reason) == COST_TO_RISK
    assert reg.status(out.signal.key) == Status.ARMED and not reg.is_consumed(out.signal.key)
    assert reg.open_positions == {} and reg.log[-1].reason == COST_TO_RISK


def test_approved_decision_records_fill_through_approve_and_record() -> None:
    reg, _ = _armed_registry()
    out = fill_outcome()
    d = assess_fill(out, acct(), RISK_ONE, TIERED)
    approved, reason, extra = as_approval(d)
    assert approved and extra["shares"] == 2
    assert approve_and_record(reg, out, approved, reason) == "filled"
    assert reg.status(out.signal.key) == Status.TRIGGERED


def test_open_position_blocks_before_the_approver_is_called() -> None:
    reg, row = _armed_registry()
    reg.record_fill(("SET", date(2024, 2, 1)), date(2024, 3, 4))
    approver = RiskApprover(acct(), RISK_ONE, TIERED)
    dec = reg.decide({**row, "date": date(2024, 3, 5), "prior_move_peak_date": date(2024, 2, 20)})
    assert not dec.allow_order and dec.reason.value == "position_open"
    assert approver.decisions == []  # nothing reached risk


def test_walk_daily_with_risk_approver_rejects_then_approves() -> None:
    df, cands, t0, level = _series_with_breakout()  # synthetic stock ~143 USD, stop ~1.8 %
    params = TriggerParams(slippage_bps=10.0)
    # 10 000 SEK at 0.5 % risk: one share, two orders' fixed costs > 25 % of risk -> rejected,
    # and the key must stay armed (no dedupe consumption).
    small = RiskApprover(acct(), RISK_HALF, TIERED)
    trace, reg = walk_daily(df, cands, params=params, approver=small)
    row = trace[trace["date"] == t0].iloc[0]
    assert row["outcome"] == FILL_CANDIDATE and row["final"] == COST_TO_RISK
    assert row["shares"] == 0 and row["shares_ordered"] == 1 and len(reg.consumed) == 0
    assert reg.status(("SET", row["peak_date"])) == Status.ARMED
    assert small.summary()[COST_TO_RISK] >= 1 and small.summary()["shares_reduced_at_fill"] == 0
    later = trace[(trace["date"] > t0) & (trace["decision"] == "armed")]
    assert len(later) > 0  # evaluated again on later candidate days
    # 10 000 SEK at 1 % risk: approved with one share (capped at 25 % of equity), key consumed.
    one = RiskApprover(acct(), RISK_ONE, TIERED)
    trace1, reg1 = walk_daily(df, cands, params=params, approver=one)
    row1 = trace1[trace1["date"] == t0].iloc[0]
    assert row1["final"] == "filled" and row1["shares"] == 1 and row1["partial_skipped"] is True
    assert len(reg1.consumed) == 1 and one.summary()[APPROVED] == 1
    # 200 000 SEK (D-008 variant): more shares, partial leg exists, still within the cap.
    big = RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED)
    trace2, _ = walk_daily(df, cands, params=params, approver=big)
    row2 = trace2[trace2["date"] == t0].iloc[0]
    assert row2["final"] == "filled" and row2["shares"] >= 3 and row2["partial_skipped"] is False
    assert row2["shares"] * row2["fill_price"] <= big.account.equity_usd * 0.25 + 1e-9
    assert trace2["sizing_version"].dropna().eq("1").all()


def test_tuple_approver_remains_valid() -> None:
    assert as_approval((True, "stub")) == (True, "stub", {})
