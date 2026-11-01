"""P2: portfolio limits and loss/drawdown modes (RISK_MODEL §2.2, §2.5–2.6; PORTFOLIO_DESIGN §5)."""

from datetime import date

import pytest

from tradingbot.backtesting.account import Account, Reservation, leg_costs
from tradingbot.risk.costs import CostParams
from tradingbot.risk.limits import (
    ALLOWED,
    DAILY_LIMIT,
    HALTED,
    NORMAL,
    R_CASH,
    R_DAILY,
    R_GROSS,
    R_HALTED,
    R_MAX_POSITIONS,
    R_OPEN_RISK,
    R_SECTOR,
    R_WEEKLY,
    WEEKLY_LIMIT,
    EntryRequest,
    LimitParams,
    LossState,
    OpenRisk,
    PortfolioSnapshot,
    allow_entry,
    snapshot_from_account,
)

P = LimitParams()  # RISK_MODEL §3 starting values: 4 positions, 4 % risk, 100 % gross
D = date(2024, 7, 1)


def snap(equity=1000.0, cash=None, reserved=0.0, mv=0.0, positions=(), pending=(), state=NORMAL):
    return PortfolioSnapshot(
        D,
        equity,
        equity - mv if cash is None else cash,
        reserved,
        mv,
        tuple(positions),
        tuple(pending),
        state,
    )


def req(ticker="AAA", shares=2, fill=100.0, stop=98.0, reservation=None, sector=None, key=None):
    return EntryRequest(
        key or (ticker, D),
        ticker,
        shares,
        fill,
        stop,
        shares * fill + 1.0 if reservation is None else reservation,
        sector,
    )


def pos(ticker, shares, price, stop, sector=None):
    return OpenRisk(ticker, shares, price, stop, sector)


# --- single checks at exact values ---


def test_allowed_records_every_check() -> None:
    d = allow_entry(snap(), req(), P)
    assert d.ok and d.reason == ALLOWED
    assert [c.name for c in d.checks] == [
        "state",
        "positions",
        "open_risk",
        "gross_exposure",
        "cash",
        "sector",
    ]
    row = d.to_row()
    assert row["open_risk_value"] == pytest.approx(4.0) and row["open_risk_limit"] == pytest.approx(
        40.0
    )
    assert row["limits_version"] == "1"


def test_max_positions_counts_open_and_pending() -> None:
    s = snap(positions=[pos("A", 1, 10, 9), pos("B", 1, 10, 9)], pending=[pos("C", 1, 10, 9)])
    assert allow_entry(s, req("D"), P).ok  # 2 + 1 + 1 = 4 == max -> allowed
    s2 = s.with_order(req("D"))
    d = allow_entry(s2, req("E"), P)
    assert not d.ok and d.reason == R_MAX_POSITIONS
    assert allow_entry(s2, req("E"), LimitParams(max_open_positions=None)).ok


def test_open_risk_exact_boundary_includes_pending_and_order() -> None:
    # equity 1000, limit 40 USD planned risk. Open 20 + pending 10 + order 10 = 40 -> allowed.
    s = snap(positions=[pos("A", 10, 50.0, 48.0)], pending=[pos("B", 5, 20.0, 18.0, None)])
    assert allow_entry(s, req("C", shares=5, fill=100.0, stop=98.0), P).ok
    d = allow_entry(s, req("C", shares=5, fill=100.0, stop=97.99), P)
    assert not d.ok and d.reason == R_OPEN_RISK
    assert d.checks[2].value == pytest.approx(30 + 5 * 2.01)
    # Open risk is PLANNED risk: a position already below its stop contributes 0, not negative.
    assert pos("X", 10, 40.0, 48.0).risk_usd == 0.0


def test_gross_exposure_counts_market_value_and_reservations() -> None:
    s = snap(
        equity=1000.0,
        cash=400.0,
        mv=600.0,
        reserved=300.0,
        positions=[pos("A", 6, 100, 99)],
        pending=[pos("B", 3, 100, 99)],
    )
    assert allow_entry(s, req("C", shares=1, fill=99.0, stop=98.5, reservation=100.0), P).ok
    d = allow_entry(s, req("C", shares=1, fill=99.0, stop=98.5, reservation=100.01), P)
    assert not d.ok and d.reason == R_GROSS
    assert (
        allow_entry(
            s,
            req("C", reservation=100.01, fill=99.0, stop=98.5, shares=1),
            LimitParams(max_gross_exposure=None),
        ).reason
        == R_CASH
    )


def test_cash_check_uses_available_not_cash() -> None:
    # With max_gross_exposure = 1.0 and equity = cash + market value, the cash check is implied
    # by gross exposure; it binds on its own only when the gross check is off (or > 1.0).
    p = LimitParams(max_gross_exposure=None)
    s = snap(equity=1000.0, cash=1000.0, reserved=900.0)
    assert allow_entry(s, req("A", shares=1, fill=50.0, stop=49.0, reservation=100.0), p).ok
    d = allow_entry(s, req("A", shares=1, fill=50.0, stop=49.0, reservation=100.01), p)
    assert not d.ok and d.reason == R_CASH and d.checks[4].limit == pytest.approx(100.0)
    assert (
        allow_entry(s, req("A", shares=1, fill=50.0, stop=49.0, reservation=100.01), P).reason
        == R_GROSS
    )


def test_sector_limit_is_off_without_parameter_or_sector() -> None:
    s = snap(positions=[pos("A", 1, 10, 9, "Tech"), pos("B", 1, 10, 9, "Tech")])
    assert allow_entry(s, req("C", sector="Tech"), P).ok  # max_per_sector None -> off
    p2 = LimitParams(max_per_sector=2)
    d = allow_entry(s, req("C", sector="Tech"), p2)
    assert not d.ok and d.reason == R_SECTOR
    assert allow_entry(s, req("C", sector="Energy"), p2).ok
    assert allow_entry(s, req("C"), p2).ok  # unknown sector cannot be limited


def test_first_failing_check_is_the_reason_and_state_wins() -> None:
    s = snap(
        equity=1000.0,
        cash=10.0,
        reserved=0.0,
        mv=990.0,
        positions=[pos(t, 1, 247.5, 200.0) for t in "ABCD"],
        state=HALTED,
    )
    d = allow_entry(s, req("E"), P)
    assert d.reason == R_HALTED and [c.ok for c in d.checks][:5] == [
        False,
        False,
        False,
        False,
        False,
    ]
    assert allow_entry(snap(state=DAILY_LIMIT), req(), P).reason == R_DAILY
    assert allow_entry(snap(state=WEEKLY_LIMIT), req(), P).reason == R_WEEKLY


def test_with_order_chains_several_same_day_candidates() -> None:
    s = snap(equity=1000.0)
    accepted = []
    for t in "ABCDE":
        r = req(t, shares=5, fill=100.0, stop=98.0, reservation=200.0)  # risk 10, reserve 200
        d = allow_entry(s, r, P)
        if d.ok:
            s = s.with_order(r)
            accepted.append(t)
        else:
            assert (t, d.reason) == ("E", R_MAX_POSITIONS)
    assert accepted == ["A", "B", "C", "D"] and s.reserved_usd == 800.0 and s.open_risk_usd == 40.0


# --- snapshot from the P1 account ---


def test_snapshot_from_account_needs_stops_and_mark() -> None:
    costs = CostParams()
    a = Account.from_sek(200_000.0, costs, D)
    k = ("AAA", date(2024, 6, 1))
    c, fx = leg_costs(10, 100.0, costs)
    a.reserve(Reservation(k, "AAA", 10, 100.0, 1000.0 + c + fx, D), D)
    a.buy(k, 10, 100.0, c, fx, D)
    k2 = ("BBB", date(2024, 6, 2))
    a.reserve(Reservation(k2, "BBB", 4, 50.0, 201.0, D), D)
    with pytest.raises(ValueError, match="marked"):
        snapshot_from_account(a, {"AAA": 95.0}, {k2: 48.0}, NORMAL, D)
    a.mark({"AAA": 110.0}, D)
    with pytest.raises(ValueError, match="no current stop"):
        snapshot_from_account(a, {}, {k2: 48.0}, NORMAL, D)
    with pytest.raises(ValueError, match="no planned stop"):
        snapshot_from_account(a, {"AAA": 95.0}, {}, NORMAL, D)
    s = snapshot_from_account(a, {"AAA": 95.0}, {k2: 48.0}, NORMAL, D)
    assert s.equity_usd == pytest.approx(a.equity_usd) and s.reserved_usd == pytest.approx(201.0)
    assert s.open_risk_usd == pytest.approx(10 * (110 - 95) + 4 * (50 - 48))  # mark-based
    assert s.n_positions == 1 and s.n_pending == 1 and s.market_value_usd == pytest.approx(1100.0)


# --- loss and drawdown modes ---


def _walk(equities: list[tuple[date, float]], params=P, start=1000.0) -> list[LossState]:
    st = LossState.start(start, date(2024, 6, 28))
    out = []
    for d, e in equities:
        st = st.update_at_close(e, d, params)
        out.append(st)
    return out


def test_daily_limit_blocks_next_session_only_and_measures_against_previous_close() -> None:
    days = [
        (date(2024, 7, 1), 1000.0),
        (date(2024, 7, 2), 970.0),
        (date(2024, 7, 3), 968.0),
        (date(2024, 7, 5), 1100.0),
        (date(2024, 7, 8), 1067.0),
    ]
    states = _walk(days)
    assert [s.state for s in states] == [NORMAL, DAILY_LIMIT, NORMAL, NORMAL, DAILY_LIMIT]
    assert states[1].blocks_entries and "3.00%" in states[1].reason
    # 7/3: −0.2 % vs previous close, although −3.2 % vs the start: no limit (anchor = T−1).
    assert states[2].prev_close_equity == 968.0
    # The decision at close 7/2 is what PRE_OPEN 7/3 sees.
    assert allow_entry(snap(state=states[1].state), req(), P).reason == R_DAILY
    assert allow_entry(snap(state=states[2].state), req(), P).ok
    assert _walk(days, LimitParams(daily_loss_limit_pct=None))[1].state == NORMAL


def test_weekly_limit_lasts_the_iso_week_and_resets_with_new_anchor() -> None:
    # Week 27 (Jul 1–5): slide to −6 % by Wednesday; Thursday recovers but the week stays blocked.
    days = [
        (date(2024, 7, 1), 980.0),
        (date(2024, 7, 2), 960.0),
        (date(2024, 7, 3), 940.0),
        (date(2024, 7, 5), 990.0),
        (date(2024, 7, 8), 985.0),
    ]
    states = _walk(days)
    assert [s.state for s in states] == [NORMAL, NORMAL, WEEKLY_LIMIT, WEEKLY_LIMIT, NORMAL]
    assert states[2].week_anchor_equity == 1000.0 and states[4].week_anchor_equity == 990.0
    assert states[3].reason == states[2].reason  # carried, not recomputed
    # Exactly at the limit counts (<=): 940 == 1000 × (1 − 0.06).
    assert "6%" in states[2].reason


def test_drawdown_halt_is_permanent_and_measured_from_hwm() -> None:
    days = [
        (date(2024, 7, 1), 1200.0),
        (date(2024, 7, 2), 1100.0),
        (date(2024, 7, 3), 1020.0),
        (date(2024, 7, 5), 1500.0),
    ]
    states = _walk(days)
    # 1020 == 1200 × 0.85 -> HALTED at close 7/3; recovery to 1500 does not resume.
    assert [s.state for s in states] == [NORMAL, DAILY_LIMIT, HALTED, HALTED]
    assert states[2].halted_on == date(2024, 7, 3) == states[3].halted_on
    assert states[3].hwm == 1500.0 and states[3].reason == states[2].reason
    assert allow_entry(snap(state=states[3].state), req(), P).reason == R_HALTED
    assert states[2].history == (NORMAL, DAILY_LIMIT, HALTED)
    with pytest.raises(ValueError, match="not after"):
        states[3].update_at_close(1500.0, date(2024, 7, 5), P)


def test_stop_gap_loss_exceeds_planned_open_risk_and_trips_the_limits() -> None:
    """RISK_MODEL §2.7: open_risk bounds PLANNED risk only. Two positions at the 4 % planned
    limit gap 10 % under their stops -> equity −20 %: never refused by open_risk, but the
    daily limit and the drawdown halt react at the close."""
    s = snap(
        equity=1000.0,
        cash=600.0,
        mv=400.0,
        positions=[pos("A", 4, 50.0, 45.0), pos("B", 4, 50.0, 45.0)],
    )
    assert s.open_risk_usd == pytest.approx(40.0)  # exactly 4 % of equity: budget is used up
    assert (
        allow_entry(s, req("C", shares=1, fill=10.0, stop=9.99, reservation=11.0), P).reason
        == R_OPEN_RISK
    )
    # Both stopped at the open 10 % below the stop: realized loss 2 × 4 × 9.5 = 76 (+ costs ~0).
    st = LossState.start(1000.0, date(2024, 6, 28)).update_at_close(924.0, date(2024, 7, 1), P)
    assert st.state == WEEKLY_LIMIT  # 7.6 % > 6 % weekly (ranks above daily), < 15 % drawdown
    st2 = st.update_at_close(840.0, date(2024, 7, 2), P)
    assert st2.state == HALTED  # 16 % from hwm 1000


def test_params_off_switches_and_start_state() -> None:
    off = LimitParams(
        daily_loss_limit_pct=None, weekly_loss_limit_pct=None, max_drawdown_halt_pct=None
    )
    st = LossState.start(1000.0, D)
    assert st.state == NORMAL and st.hwm == 1000.0 and not st.blocks_entries
    assert st.update_at_close(100.0, date(2024, 7, 2), off).state == NORMAL
    row = st.to_row()
    assert row["state"] == NORMAL and row["halted_on"] is None
