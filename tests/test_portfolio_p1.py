"""P1: session clock, account bookkeeping and ledger identities (PORTFOLIO_DESIGN §2–§4, §7.1)."""

from datetime import date

import pytest

from tradingbot.backtesting.account import (
    Account,
    AccountError,
    Reservation,
    leg_costs,
)
from tradingbot.backtesting.clock import PHASES, Phase, SessionClock
from tradingbot.backtesting.ledger import LedgerError, check_identities
from tradingbot.risk.costs import FX_RATE_PILOT, IBKR_TIERED, CostParams

PER_TRADE = CostParams(commission=IBKR_TIERED, fx_mode="per_trade", fx_cost_bps=25.0)
ONCE = CostParams(commission=IBKR_TIERED, fx_mode="usd_balance", fx_cost_bps=25.0)
D0 = date(2024, 7, 1)
KEY = ("AAA", date(2024, 6, 1))


def acct(costs=PER_TRADE, sek=10_000.0) -> Account:
    return Account.from_sek(sek, costs, D0)


def reserve(a: Account, key=KEY, ticker="AAA", shares=5, fill_est=100.0, on=D0) -> Reservation:
    c, fx = leg_costs(shares, fill_est, PER_TRADE)
    res = Reservation(key, ticker, shares, fill_est, shares * fill_est + c + fx, on)
    a.reserve(res, on)
    return res


# --- clock ---


def test_clock_skips_holidays_and_flags_half_days() -> None:
    clock = SessionClock(date(2024, 7, 1), date(2024, 7, 8))
    assert [s.date.day for s in clock] == [1, 2, 3, 5, 8]  # July 4 closed
    assert (
        clock.session(date(2024, 7, 3)).is_half_day
        and clock.session(date(2024, 7, 3)).minutes == 210
    )
    assert not clock.session(date(2024, 7, 5)).is_half_day
    assert not clock.is_session(date(2024, 7, 4))
    with pytest.raises(ValueError, match="not an XNYS session"):
        clock.session(date(2024, 7, 4))
    nov = SessionClock(date(2024, 11, 27), date(2024, 12, 2))
    assert [s.date.day for s in nov] == [27, 29, 2]  # Thanksgiving closed, 29th half day
    assert nov.session(date(2024, 11, 29)).is_half_day
    good_friday = SessionClock(date(2024, 3, 28), date(2024, 4, 1))
    assert [s.date.day for s in good_friday] == [28, 1]


def test_clock_dst_from_calendar_and_phase_order() -> None:
    clock = SessionClock(date(2024, 3, 7), date(2024, 3, 12))  # DST shift Sunday 2024-03-10
    before, after = clock.session(date(2024, 3, 8)), clock.session(date(2024, 3, 11))
    assert before.open_utc.hour == 14 and after.open_utc.hour == 13
    assert before.open_ny_hour == after.open_ny_hour == 9
    steps = list(clock.steps())
    assert len(steps) == 4 * len(clock) and [p for _, p in steps[:4]] == list(PHASES)
    assert PHASES == (Phase.PRE_OPEN, Phase.OPEN, Phase.INTRADAY, Phase.CLOSE)
    assert clock.previous(date(2024, 3, 11)).date == date(2024, 3, 8)
    assert clock.next(date(2024, 3, 12)) is None and clock.previous(date(2024, 3, 7)) is None
    with pytest.raises(ValueError):
        SessionClock(date(2024, 3, 12), date(2024, 3, 7))


# --- account: construction and FX ---


def test_from_sek_fx_modes() -> None:
    per = acct(PER_TRADE)
    once = acct(ONCE)
    gross = 10_000 / FX_RATE_PILOT
    assert per.cash_usd == pytest.approx(gross) and per.deposit_fx_cost_usd == 0
    assert once.deposit_fx_cost_usd == pytest.approx(gross * 0.0025)
    assert once.cash_usd == pytest.approx(gross - once.deposit_fx_cost_usd)
    assert once.realized_usd == pytest.approx(-once.deposit_fx_cost_usd)
    assert once.hwm_usd == pytest.approx(once.cash_usd) and once.equity_usd == once.cash_usd
    assert once.to_sek(once.equity_usd) == pytest.approx(10_000 - 25.0)
    check_identities(per)
    check_identities(once)
    kinds = [e.kind for e in once.ledger.events]
    assert kinds == ["deposit", "deposit_fx"]


# --- reservations ---


def test_reserve_release_and_available() -> None:
    a = acct()
    res = reserve(a)
    assert a.reserved_usd == pytest.approx(res.amount_usd)
    assert a.available_usd == pytest.approx(a.cash_usd - res.amount_usd)
    assert a.equity_usd == pytest.approx(a.cash_usd)  # a reservation is not an asset
    with pytest.raises(AccountError, match="duplicate_reservation"):
        reserve(a)
    too_big = Reservation(("BBB", D0), "BBB", 1, 10_000.0, 10_000.0, D0)
    with pytest.raises(AccountError, match="insufficient_cash"):
        a.reserve(too_big, D0)
    with pytest.raises(AccountError, match="invalid_reservation"):
        a.reserve(Reservation(("CCC", D0), "CCC", 0, 10.0, 0.0, D0), D0)
    a.release(KEY, D0)
    assert a.reserved_usd == 0 and a.available_usd == pytest.approx(a.cash_usd)
    with pytest.raises(AccountError, match="unknown_reservation"):
        a.release(KEY, D0)
    check_identities(a)


def test_exact_cash_boundary_for_reservations() -> None:
    a = acct()
    full = Reservation(KEY, "AAA", 1, a.cash_usd, a.cash_usd, D0)
    a.reserve(full, D0)  # exactly all cash is allowed
    assert a.available_usd == pytest.approx(0.0)
    with pytest.raises(AccountError, match="insufficient_cash"):
        a.reserve(Reservation(("BBB", D0), "BBB", 1, 0.01, 0.01, D0), D0)


# --- fills ---


def test_buy_consumes_reservation_and_books_costs() -> None:
    a = acct()
    res = reserve(a, shares=5, fill_est=100.0)
    cash0 = a.cash_usd
    c, fx = leg_costs(5, 99.0, PER_TRADE)
    a.buy(KEY, 5, 99.0, c, fx, D0, slippage=0.05)
    assert a.reservations == {} and a.reserved_usd == 0
    assert a.cash_usd == pytest.approx(cash0 - (5 * 99.0 + c + fx))
    assert a.realized_usd == pytest.approx(-(c + fx))  # entry costs realized immediately
    assert a.commission_usd == pytest.approx(0.35) and a.fx_usd == pytest.approx(fx)
    assert a.slippage_usd == pytest.approx(0.05)  # information only
    h = a.holdings["AAA"]
    assert h.shares == 5 and h.entry_fill == 99.0 and h.mark == 99.0 and h.cost_basis == 495.0
    assert a.unrealized_usd == 0 and a.equity_usd == pytest.approx(cash0 - c - fx)
    check_identities(a)
    assert res.amount_usd >= 5 * 99.0 + c + fx


def test_buy_without_or_beyond_reservation_is_refused() -> None:
    a = acct()
    with pytest.raises(AccountError, match="no_reservation"):
        a.buy(KEY, 1, 100.0, 0.35, 0.0, D0)
    res = reserve(a, shares=5, fill_est=100.0)
    # Gap fill 5 % above the estimate: exceeds the reservation but fits in available cash.
    c, fx = leg_costs(5, 105.0, PER_TRADE)
    assert a.would_fit(KEY, 5 * 105.0 + c + fx) is True
    with pytest.raises(AccountError, match="reservation_exceeded") as e:
        a.buy(KEY, 5, 105.0, c, fx, D0)
    assert e.value.code == "reservation_exceeded"
    assert a.reservations[KEY] == res and a.cash_usd == pytest.approx(10_000 / FX_RATE_PILOT)
    # The same overrun with the rest of the cash reserved elsewhere does not fit at all.
    other = Reservation(("BBB", D0), "BBB", 1, a.available_usd, a.available_usd, D0)
    a.reserve(other, D0)
    assert a.would_fit(KEY, 5 * 105.0 + c + fx) is False
    with pytest.raises(AccountError, match="invalid_fill"):
        a.buy(KEY, 0, 100.0, 0.0, 0.0, D0)
    check_identities(a)


def test_sell_partial_and_full_realizes_pnl() -> None:
    a = acct()
    reserve(a, shares=6, fill_est=100.0)
    c0, fx0 = leg_costs(6, 100.0, PER_TRADE)
    a.buy(KEY, 6, 100.0, c0, fx0, D0)
    c1, fx1 = leg_costs(2, 110.0, PER_TRADE)
    a.sell("AAA", 2, 110.0, c1, fx1, D0, reason="partial")
    assert a.holdings["AAA"].shares == 4 and a.holdings["AAA"].entry_fill == 100.0
    assert a.realized_usd == pytest.approx(-(c0 + fx0) + 2 * 10.0 - c1 - fx1)
    check_identities(a)
    c2, fx2 = leg_costs(4, 95.0, PER_TRADE)
    a.sell("AAA", 4, 95.0, c2, fx2, D0, reason="stop")
    assert "AAA" not in a.holdings
    expected = -(c0 + fx0) + 20.0 - c1 - fx1 - 20.0 - c2 - fx2
    assert a.realized_usd == pytest.approx(expected)
    assert a.cash_usd == pytest.approx(a.start_cash_usd + expected)
    check_identities(a)
    with pytest.raises(AccountError, match="no_position"):
        a.sell("AAA", 1, 95.0, 0.35, 0.0, D0)


def test_oversell_and_bad_prices_are_refused() -> None:
    a = acct()
    reserve(a, shares=3, fill_est=100.0)
    a.buy(KEY, 3, 100.0, 0.35, 0.75, D0)
    with pytest.raises(AccountError, match="oversell"):
        a.sell("AAA", 4, 100.0, 0.35, 0.0, D0)
    with pytest.raises(AccountError, match="oversell"):
        a.sell("AAA", 0, 100.0, 0.35, 0.0, D0)
    with pytest.raises(AccountError, match="invalid_fill"):
        a.sell("AAA", 1, 0.0, 0.35, 0.0, D0)
    with pytest.raises(AccountError, match="position_open"):
        a.reserve(Reservation(("AAA", date(2024, 6, 15)), "AAA", 1, 100.0, 101.0, D0), D0)


# --- mark-to-market and ledger rows ---


def test_mark_updates_unrealized_equity_and_hwm() -> None:
    a = acct()
    reserve(a, shares=5, fill_est=100.0)
    c, fx = leg_costs(5, 100.0, PER_TRADE)
    a.buy(KEY, 5, 100.0, c, fx, D0)
    clock = SessionClock(D0, date(2024, 7, 3))
    a.mark({"AAA": 110.0}, D0)
    assert a.unrealized_usd == pytest.approx(50.0) and a.market_value == pytest.approx(550.0)
    assert a.equity_usd == pytest.approx(a.cash_usd + 550.0)
    assert a.hwm_usd == pytest.approx(a.equity_usd)
    row = a.ledger.close_session(a, clock.session(D0))
    assert row.n_positions == 1 and row.unrealized == pytest.approx(50.0)
    assert row.drawdown_pct == 0.0 and row.gross_exposure_pct == pytest.approx(550.0 / row.equity)
    assert row.equity_sek == pytest.approx(row.equity * FX_RATE_PILOT)
    a.mark({"AAA": 90.0}, date(2024, 7, 2))
    row2 = a.ledger.close_session(a, clock.session(date(2024, 7, 2)))
    assert row2.unrealized == pytest.approx(-50.0) and row2.hwm == pytest.approx(row.equity)
    assert row2.drawdown_pct == pytest.approx(100.0 / row.equity)
    assert row2.n_events == 1  # only the mark since the previous row
    frame = a.ledger.rows_frame()
    assert list(frame["date"]) == [D0, date(2024, 7, 2)] and frame["ledger_version"].eq("1").all()
    with pytest.raises(AccountError, match="missing_mark"):
        a.mark({}, date(2024, 7, 3))
    with pytest.raises(LedgerError, match="not marked"):
        a.ledger.close_session(a, clock.session(date(2024, 7, 3)))


def test_identities_catch_tampering() -> None:
    a = acct()
    reserve(a, shares=2, fill_est=100.0)
    a.buy(KEY, 2, 100.0, 0.35, 0.5, D0)
    check_identities(a)
    a.cash_usd += 1.0  # money from nowhere
    with pytest.raises(LedgerError, match="cash"):
        check_identities(a)
    a.cash_usd -= 1.0
    a.reservations[("X", D0)] = Reservation(("X", D0), "X", 1, 1e6, 1e6, D0)  # over cash
    with pytest.raises(LedgerError, match="reserved"):
        check_identities(a)


# --- splits (§7.1) ---


def test_split_keeps_equity_and_pays_cash_in_lieu() -> None:
    a = acct()
    reserve(a, shares=33, fill_est=20.0)
    c, fx = leg_costs(33, 20.0, PER_TRADE)
    a.buy(KEY, 33, 20.0, c, fx, D0)
    a.mark({"AAA": 21.0}, D0)
    eq_before = a.equity_usd
    events = a.apply_split("AAA", 1.5, open_price=14.0, on=date(2024, 7, 2))  # 49.5 shares
    h = a.holdings["AAA"]
    assert (
        h.shares == 49
        and h.entry_fill == pytest.approx(20.0 / 1.5)
        and h.mark == pytest.approx(14.0)
    )
    assert a.cash_in_lieu_usd == pytest.approx(0.5 * 14.0)
    assert a.equity_usd == pytest.approx(
        eq_before
    )  # same price in new units: no P&L from the split
    assert [e.kind for e in events] == ["cash_in_lieu", "split"]
    check_identities(a)
    # 2:1 on another holding leaves no fraction and no cash.
    reserve(a, key=("BBB", D0), ticker="BBB", shares=4, fill_est=50.0)
    a.buy(("BBB", D0), 4, 50.0, 0.35, 0.5, D0)
    cash = a.cash_usd
    assert [e.kind for e in a.apply_split("BBB", 2.0, 25.0, date(2024, 7, 2))] == ["split"]
    assert a.holdings["BBB"].shares == 8 and a.cash_usd == cash
    assert a.apply_split("ZZZ", 2.0, 1.0, D0) == []  # no holding, no reservation: nothing
    with pytest.raises(AccountError, match="unknown_split"):
        a.apply_split("AAA", float("nan"), 14.0, D0)


def test_split_leaves_same_day_reservation_untouched() -> None:
    """A reservation is made in PRE_OPEN from levels already expressed in day-T units
    (compute_levels divides by split_ratio[T]), so the OPEN-phase split must not touch it."""
    a = acct()
    res = reserve(a, shares=10, fill_est=40.0)
    assert a.apply_split("AAA", 4.0, 10.0, D0) == []
    assert a.reservations[KEY] == res and a.reserved_usd == pytest.approx(res.amount_usd)
    check_identities(a)


# --- ledger events are the only trace and are deterministic ---


def test_events_sum_to_cash_and_two_runs_are_identical() -> None:
    def run() -> Account:
        a = acct(ONCE)
        reserve(a, shares=5, fill_est=100.0)
        a.buy(KEY, 5, 100.0, 0.35, 0.0, D0)
        a.mark({"AAA": 102.0}, D0)
        a.sell("AAA", 5, 103.0, 0.35, 0.0, date(2024, 7, 2))
        a.mark({}, date(2024, 7, 2))
        return a

    a, b = run(), run()
    ev = a.ledger.events_frame()
    assert ev["cash_delta"].sum() == pytest.approx(a.cash_usd - a.start_cash_usd)
    assert ev["realized_delta"].sum() == pytest.approx(a.realized_usd)
    assert ev["reserved_delta"].sum() == pytest.approx(a.reserved_usd) == 0
    assert ev.equals(b.ledger.events_frame())
    assert set(ev["phase"]) <= {"pre_open", "open", "intraday", "close"}
