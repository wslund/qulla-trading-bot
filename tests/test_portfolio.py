"""P4: portfolio engine for daily_stop_entry (PORTFOLIO_DESIGN §2–§8; D-015, D-016)."""

from datetime import date

import pandas as pd
import pytest

from tradingbot.backtesting.ledger import check_identities
from tradingbot.backtesting.portfolio import PortfolioParams, run_portfolio
from tradingbot.backtesting.trades import STOP_GAP, run_ticker
from tradingbot.data.quality import trading_sessions
from tradingbot.risk.limits import DAILY_LIMIT, LimitParams
from tradingbot.risk.sizing import RiskApprover, RiskParams
from tradingbot.scanner.features import compute_features
from tradingbot.scanner.rules import apply_rules
from tradingbot.signals.daily_trigger import TriggerParams
from tradingbot.signals.state import Status

from .test_features import apply_split
from .test_risk import RISK_ONE, TIERED, acct
from .test_trades import RISE_THEN_FADE, scripted

TP = TriggerParams(slippage_bps=10.0)
SLIP = 0.001


def on_sessions(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Re-date a synthetic weekday series onto consecutive XNYS sessions (the engine's clock
    skips exchange holidays, so a bar on Memorial Day would silently shift every count)."""
    out = df.copy()
    sessions = trading_sessions("XNYS", df["date"].iloc[0], "2026-12-31")[: len(df)]
    out["date"] = [d.date() for d in sessions]
    out["ticker"] = ticker
    return out


def universe(bars, tickers, **kw):
    """Same scripted path for every ticker (same T0), each on XNYS sessions."""
    series, cands = {}, {}
    base, _, _, f = scripted(bars, **kw)
    for t in tickers:
        s = on_sessions(base, t)
        series[t] = s
        cands[t] = apply_rules(compute_features(s))
    t0 = cands[tickers[0]].loc[cands[tickers[0]]["is_candidate"].astype(bool), "date"].iloc[0]
    return series, cands, t0, f


def params(sek=200_000.0, risk=RISK_ONE, limits=None, **kw):
    return PortfolioParams(sek, risk, TIERED, limits or LimitParams(), TP, **kw)


# --- equivalence with run_ticker ---


def test_single_ticker_matches_run_ticker_when_limits_do_not_bind() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["SET"])
    s, c = series["SET"], cands["SET"]
    ref = run_ticker(
        s, c, RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED), costs=TIERED, trigger=TP
    )
    res = run_portfolio(series, cands, params())
    assert len(ref[0]) == len(res.trades) == 1
    a, b = ref[0][0], res.trades[0]
    for field in (
        "entry_date",
        "entry_fill",
        "shares",
        "shares_ordered",
        "initial_stop",
        "initial_risk_usd",
        "partial_date",
        "partial_price",
        "partial_shares",
        "exit_date",
        "exit_price",
        "exit_reason",
        "bars_held",
        "pnl_gross_usd",
        "pnl_net_usd",
        "r_multiple",
        "cost_actual",
        "partial_skipped",
    ):
        assert getattr(a, field) == getattr(b, field), field
    assert [(x.name, x.shares, x.price) for x in a.legs] == [
        (x.name, x.shares, x.price) for x in b.legs
    ]
    # Same rejections (date, reason); the portfolio only adds stages, never different reasons.
    assert {(r.date, r.reason) for r in ref[1]} == {(r.date, r.reason) for r in res.rejections}
    # The account realized exactly the trade's net result; identities hold at the end.
    acc = res.account
    assert acc.realized_usd == pytest.approx(b.pnl_net_usd) and acc.holdings == {}
    assert acc.equity_usd == pytest.approx(acc.start_cash_usd + b.pnl_net_usd)
    check_identities(acc)
    assert res.registry.status(("SET", b.peak_date)) == Status.CLOSED


# --- concurrent signals, capacity, order invariance ---


def test_concurrent_signals_respect_priority_and_capacity_and_are_order_invariant() -> None:
    tickers = ["DDD", "AAA", "CCC", "BBB"]
    series, cands, t0, f = universe(RISE_THEN_FADE, tickers)
    lim = LimitParams(max_open_positions=2)
    res = run_portfolio(series, cands, params(limits=lim))
    filled = sorted(t.ticker for t in res.trades if t.entry_date == t0)
    assert filled == ["AAA", "BBB"]  # D-015 baseline: alphabetical
    rej = [(r.ticker, r.reason) for r in res.rejections if r.date == t0]
    assert sorted(rej) == [("CCC", "max_positions"), ("DDD", "max_positions")]
    binding = res.summary()["portfolio"]["binding_limits"]
    assert set(binding) == {"max_positions"} and binding["max_positions"] >= 2  # later days too
    assert res.summary()["portfolio"]["priority_binding_days"] >= 1
    # Rejected keys stay armed (no consumption), filled keys are consumed.
    assert res.registry.status(("CCC", res.trades[0].peak_date)) == Status.ARMED
    assert all(res.registry.is_consumed((t.ticker, t.peak_date)) for t in res.trades)
    # Permuting the input order changes nothing: bit-identical ledger and trades.
    other = run_portfolio(
        {t: series[t] for t in reversed(tickers)},
        {t: cands[t] for t in reversed(tickers)},
        params(limits=lim),
    )
    assert res.ledger_frame().equals(other.ledger_frame())
    assert res.account.ledger.events_frame().equals(other.account.ledger.events_frame())
    assert [t.to_row() for t in res.trades] == [t.to_row() for t in other.trades]
    check_identities(res.account)


def test_capital_shortage_rejects_later_candidates_and_releases_at_close() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    # Pin the order to the position cap so one order needs ~all cash (see P3 test).
    big = RiskParams(risk_per_trade=0.5, max_position_pct=0.9, max_cost_to_risk=1.0)
    lim = LimitParams(max_open_risk_pct=None)
    res = run_portfolio(series, cands, params(risk=big, limits=lim))
    day0 = [(r.ticker, r.reason) for r in res.rejections if r.date == t0]
    assert day0 == [("BBB", "gross_exposure")]
    assert [t.ticker for t in res.trades if t.entry_date == t0] == ["AAA"]
    row = res.ledger_frame().set_index("date").loc[t0]
    assert row["reserved"] == 0.0  # the fill consumed AAA's reservation; nothing pending at close
    assert row["n_positions"] == 1 and row["gross_exposure_pct"] <= 1.0 + 1e-9


# --- exits before entries, no same-day re-use (D-016) ---


def test_exit_at_open_frees_cash_only_for_the_next_session() -> None:
    # AAA fills at T0 and gaps 10 % under its stop at T0+1. BBB's candidate day is moved to
    # T0+1 by shifting its series one session; capital is tight enough that BBB only fits
    # once AAA's proceeds are back – which is the day after, not the same day.
    series, cands, t0, f = universe([(0.9, 0.92, 0.88, 0.9), (0.9, 0.95, 0.88, 0.94)], ["AAA"])
    a = series["AAA"]
    b = a.copy()
    sessions = trading_sessions("XNYS", a["date"].iloc[0], "2026-12-31")[1 : len(a) + 1]
    b["date"] = [d.date() for d in sessions]  # BBB lags one session
    b["ticker"] = "BBB"
    series["BBB"], cands["BBB"] = b, apply_rules(compute_features(b))
    t1 = cands["BBB"].loc[cands["BBB"]["is_candidate"].astype(bool), "date"].iloc[0]
    assert t1 > t0
    big = RiskParams(risk_per_trade=0.5, max_position_pct=0.9, max_cost_to_risk=1.0)
    res = run_portfolio(series, cands, params(risk=big, limits=LimitParams(max_open_risk_pct=None)))
    aaa = next(t for t in res.trades if t.ticker == "AAA")
    assert aaa.exit_reason == STOP_GAP and aaa.exit_date == t1  # AAA leaves at the open of t1
    bbb_t1 = [r.reason for r in res.rejections if r.ticker == "BBB" and r.date == t1]
    # At PRE_OPEN of t1 AAA still held ~all capital: BBB cannot be funded that day.
    assert bbb_t1 and bbb_t1[0] in ("gross_exposure", "position_open", "insufficient_cash")
    ledger = res.ledger_frame().set_index("date")
    assert ledger.loc[t1, "n_positions"] == 0 and ledger.loc[t1, "cash"] > ledger.loc[t0, "cash"]
    assert res.registry.status(
        ("BBB", cands["BBB"].loc[cands["BBB"]["date"] == t1, "prior_move_peak_date"].item())
    ) in (Status.ARMED, Status.NONE)


# --- gap through stop, loss state at close ---


def test_gap_through_stop_books_loss_and_loss_state_governs_next_session() -> None:
    series, cands, t0, f = universe(
        [(0.6, 0.62, 0.58, 0.6), (0.6, 0.61, 0.59, 0.6)], ["AAA", "BBB", "CCC", "DDD"]
    )
    big = RiskParams(risk_per_trade=0.05, max_position_pct=0.25, max_cost_to_risk=1.0)
    res = run_portfolio(series, cands, params(risk=big, limits=LimitParams(max_open_risk_pct=None)))
    t1 = next(t.exit_date for t in res.trades)
    assert all(t.exit_reason == STOP_GAP and t.r_multiple < -1 for t in res.trades)
    ledger = res.ledger_frame().set_index("date")
    loss = ledger.loc[t1, "equity"] / ledger.loc[t0, "equity"] - 1
    assert loss < -0.03  # four positions at 25 % each, −40 % gap
    states = {ls.asof: ls.state for ls in res.loss_states if ls.asof}
    assert states[t1] in (DAILY_LIMIT, "WEEKLY_LIMIT", "HALTED")
    assert ledger.loc[t1, "state"] == states[t1]
    assert ledger.loc[t1, "realized"] == pytest.approx(sum(t.pnl_net_usd for t in res.trades))
    check_identities(res.account)


def test_halted_blocks_new_entries_next_session() -> None:
    # Same crash, then a fresh candidate day for a fifth ticker one session later.
    series, cands, t0, f = universe(
        [(0.6, 0.62, 0.58, 0.6), (0.6, 0.61, 0.59, 0.6)], ["AAA", "BBB", "CCC", "DDD"]
    )
    a = series["AAA"]
    e = a.copy()
    sessions = trading_sessions("XNYS", a["date"].iloc[0], "2026-12-31")[2 : len(a) + 2]
    e["date"] = [d.date() for d in sessions]  # EEE's candidate day = the session after the crash
    e["ticker"] = "EEE"
    series["EEE"], cands["EEE"] = e, apply_rules(compute_features(e))
    big = RiskParams(risk_per_trade=0.05, max_position_pct=0.25, max_cost_to_risk=1.0)
    res = run_portfolio(
        series,
        cands,
        params(risk=big, limits=LimitParams(max_open_risk_pct=None, max_open_positions=5)),
    )
    t1 = next(t.exit_date for t in res.trades if t.ticker == "AAA")
    t2 = cands["EEE"].loc[cands["EEE"]["is_candidate"].astype(bool), "date"].iloc[0]
    assert t2 > t1
    eee = [r for r in res.rejections if r.ticker == "EEE" and r.date == t2]
    assert eee and eee[0].reason in ("halted", "loss_limit_daily", "loss_limit_weekly")
    state_t1 = next(ls.state for ls in res.loss_states if ls.asof == t1)
    assert (
        eee[0].reason
        == {
            "HALTED": "halted",
            "DAILY_LIMIT": "loss_limit_daily",
            "WEEKLY_LIMIT": "loss_limit_weekly",
        }[state_t1]
    )
    assert res.registry.status(("EEE", eee[0].peak_date)) == Status.ARMED


# --- split day ---


def test_split_during_open_position_keeps_result_and_account_consistent() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    s = series["AAA"]
    i0 = int(s.index[s["date"] == t0][0])
    split = {"AAA": apply_split(s, at=i0 + 2, ratio=2.0)}
    base = run_portfolio(series, cands, params())
    halved = run_portfolio(split, cands, params())
    a, b = base.trades[0], halved.trades[0]
    assert b.splits == 1 and [leg.shares for leg in b.legs] == [33, 22, 44]
    assert b.pnl_net_usd == pytest.approx(a.pnl_net_usd) and b.r_multiple == pytest.approx(
        a.r_multiple
    )
    la, lb = base.ledger_frame().set_index("date"), halved.ledger_frame().set_index("date")
    assert (la["equity"] - lb["equity"]).abs().max() < 1e-6  # equity path identical
    split_day = split["AAA"].loc[i0 + 2, "date"]
    ev = halved.account.ledger.events_frame()
    assert (ev["kind"] == "split").sum() == 1 and ev.loc[
        ev["kind"] == "split", "date"
    ].item() == split_day
    check_identities(halved.account)


# --- recurring setup key ---


def test_same_key_never_trades_twice() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    res = run_portfolio(series, cands, params())
    assert len(res.trades) == 1
    peak = res.trades[0].peak_date
    assert res.registry.consumed == {("AAA", peak): t0}
    # The scripted path leaves the formation after the breakout (no later candidate rows), so
    # ask the registry directly: a new candidate row with the same key is refused forever.
    probe = {
        "date": date(2026, 1, 5),
        "ticker": "AAA",
        "is_candidate": True,
        "prior_move_peak_date": peak,
    }
    assert res.registry.decide(probe).reason.value == "key_consumed"
    later = [r for r in res.rejections if r.date > t0 and r.stage == "registry"]
    assert all(r.reason == "key_consumed" for r in later)


# --- reservation exceeded (documented difference to run_ticker) ---


def test_gap_fill_beyond_reservation_is_reservation_exceeded_not_a_trade() -> None:
    """run_ticker (constant account) fills a 4.9 % gap-through-the-level day; the portfolio
    reserved on level × (1+slip) and refuses the overrun (D-016) – the only intended
    difference between the two drivers."""
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"], gap=0.049)
    s, c = series["AAA"], cands["AAA"]
    ref = run_ticker(
        s, c, RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED), costs=TIERED, trigger=TP
    )
    assert len(ref[0]) == 1 and ref[0][0].gap_fill
    res = run_portfolio(series, cands, params())
    day0 = [r for r in res.rejections if r.date == t0]
    assert [t for t in res.trades if t.entry_date == t0] == []
    assert day0[0].reason == "reservation_exceeded" and day0[0].stage == "risk"
    assert res.decisions[0]["capital_required"] > res.decisions[0]["reservation_usd"]
    assert res.registry.status(("AAA", day0[0].peak_date)) == Status.ARMED  # not consumed
    assert (
        res.account.holdings == {}
        and res.ledger_frame().set_index("date").loc[t0, "reserved"] == 0.0
    )
    assert res.summary()["portfolio"]["binding_limits"] == {"reservation_exceeded": 1}


def test_reservations_never_read_day_t_prices() -> None:
    """Mutating T0's high/low/close changes fills but not the PRE_OPEN reservation rows."""
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    res = run_portfolio(series, cands, params())
    mutated = {t: s.copy() for t, s in series.items()}
    for s in mutated.values():
        i0 = int(s.index[s["date"] == t0][0])
        s.loc[i0, ["high", "low", "close", "adj_close"]] = [s.loc[i0, "open"]] * 4  # no trigger
    res2 = run_portfolio(mutated, cands, params())
    rows = lambda r: [x for x in r.reservations if x.get("kind") == "row" and x["date"] == t0]  # noqa: E731
    assert rows(res) == rows(res2) and len(rows(res)) == 2
    assert res.trades and not [t for t in res2.trades if t.entry_date == t0]
    assert {r.reason for r in res2.rejections if r.date == t0} == {"no_trigger"}


def test_ledger_identities_hold_every_session() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB", "CCC"])
    res = run_portfolio(series, cands, params())
    rows = res.ledger_frame()
    assert len(rows) == len(trading_sessions("XNYS", rows["date"].iloc[0], rows["date"].iloc[-1]))
    assert ((rows["equity"] - rows["cash"] - rows["market_value"]).abs() < 1e-6).all()
    assert (rows["reserved"] >= 0).all() and (rows["reserved"] <= rows["cash"] + 1e-6).all()
    assert rows["equity_sek"].iloc[-1] == pytest.approx(
        rows["equity"].iloc[-1] * res.account.fx_rate
    )
    assert res.summary()["portfolio"]["max_positions_held"] == 3
    assert rows.loc[rows["date"] == t0, "n_events"].item() >= 3 * 2  # reserve + buy per ticker
