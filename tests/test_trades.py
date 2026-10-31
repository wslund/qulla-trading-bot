"""Step 3f: simulated trades and exits (STEP3_DESIGN §7–8, spec §6.1/§8, D-013)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtesting.trades import (
    AMBIGUOUS_SAME_DAY,
    OPEN_AT_END,
    STOP,
    STOP_GAP,
    TRAIL,
    ExitParams,
    Position,
    Rejection,
    Trade,
    rejections_frame,
    run_ticker,
    sma_in_bar_units,
    step_position,
    summarize,
    trades_frame,
)
from tradingbot.risk.sizing import COST_TO_RISK, RiskApprover
from tradingbot.scanner.features import compute_features
from tradingbot.scanner.rules import apply_rules
from tradingbot.signals.daily_trigger import GAP_TOO_LARGE, TriggerParams
from tradingbot.signals.state import Status

from .test_daily_trigger import _series_with_breakout
from .test_features import apply_split
from .test_risk import RISK_HALF, RISK_ONE, TIERED, acct

TP = TriggerParams(slippage_bps=10.0)
SLIP = 0.001


def scripted(bars: list[tuple[float, float, float, float]], **kw):
    """Series from _series_with_breakout() cut after T0 and continued with ``bars`` given as
    (open, high, low, close) multiples of the entry fill F = level × (1 + slip).
    Returns (series, candidates, T0, F)."""
    df, _, t0, level = _series_with_breakout(**kw)
    f = level * (1 + SLIP)
    base = df[df["date"] <= t0].reset_index(drop=True)
    last = base.iloc[-1]
    dates = pd.bdate_range(t0, periods=len(bars) + 1)[1:]
    rows = []
    for d, (o, h, lo, c) in zip(dates, bars, strict=True):
        r = last.copy()
        r["date"] = d.date()
        r[["open", "high", "low", "close", "adj_close"]] = [o * f, h * f, lo * f, c * f, c * f]
        r["split_ratio"] = 1.0
        rows.append(r)
    out = pd.concat([base, pd.DataFrame(rows)], ignore_index=True)
    return out, apply_rules(compute_features(out)), t0, f


def big_approver() -> RiskApprover:
    return RiskApprover(acct(sek=200_000.0), RISK_ONE, TIERED)  # 33 shares on the synthetic


def small_approver() -> RiskApprover:
    return RiskApprover(acct(), RISK_ONE, TIERED)  # 1 share


# Rising path: partial decided on the close of bar 3, filled at the open of bar 4; the
# close of bar 6 drops under SMA10 (low stays above breakeven) -> exit at the open of bar 7.
RISE_THEN_FADE = [
    (1.005, 1.015, 1.0, 1.01),
    (1.012, 1.025, 1.008, 1.02),
    (1.022, 1.035, 1.018, 1.03),
    (1.04, 1.05, 1.03, 1.04),
    (1.045, 1.06, 1.04, 1.05),
    (1.045, 1.05, 1.002, 1.003),
    (1.0, 1.01, 0.99, 0.995),
    (0.99, 1.0, 0.98, 0.985),
]


# --- exits on later days ---


def test_gap_through_stop_exits_at_open_with_loss_over_1r() -> None:
    s, cands, t0, f = scripted([(0.9, 0.92, 0.88, 0.9)])
    trades, rej, reg = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == STOP_GAP and t.exit_date > t.entry_date and t.bars_held == 1
    assert t.exit_price == pytest.approx(0.9 * f * (1 - SLIP))
    assert t.initial_stop > 0.9 * f  # filled at the open, not at the stop level
    assert t.r_multiple < -1.0 and t.to_row()["loss_over_1r"] is True
    assert reg.status(("SET", t.peak_date)) == Status.CLOSED and reg.open_positions == {}


def test_stop_touched_intraday_exits_at_stop() -> None:
    s, cands, t0, f = scripted([(0.995, 1.0, 0.97, 0.98)])
    trades, _, _ = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)
    t = trades[0]
    assert t.exit_reason == STOP and t.exit_price == pytest.approx(t.initial_stop * (1 - SLIP))
    # −1R before costs: gross = shares × (stop×(1−slip) − fill)
    assert t.pnl_gross_usd == pytest.approx(t.shares * (t.exit_price - t.entry_fill))
    # Costs on top of 1R: with a 1.8 % stop, two FX legs at 25 bps alone are ~0.28R.
    assert -1.5 < t.r_multiple < -1.0


def test_partial_then_trailing_exit_follow_spec_timing() -> None:
    s, cands, t0, f = scripted(RISE_THEN_FADE)
    trades, rej, reg = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)
    assert len(trades) == 1
    t = trades[0]
    bars = s[s["date"] > t0].reset_index(drop=True)
    assert t.shares == 33 and t.partial_shares == 11 and t.partial_skipped is False
    assert t.partial_date == bars.loc[3, "date"]  # decided close bar 3, filled open bar 4
    assert t.partial_price == pytest.approx(1.04 * f * (1 - SLIP))
    assert t.exit_reason == TRAIL and t.exit_date == bars.loc[6, "date"]
    assert t.exit_price == pytest.approx(1.0 * f * (1 - SLIP))
    assert t.bars_held == 7 and t.r_multiple > 0
    assert [leg.name for leg in t.legs] == ["entry", "partial", "exit"]
    assert [leg.shares for leg in t.legs] == [33, 11, 22]
    # The trailing decision is on the close of bar 5 (0-based): close < SMA10 there, not before.
    c, sr = s["close"].to_numpy(float), s["split_ratio"].to_numpy(float)
    i0 = int(s.index[s["date"] == t0][0])
    below = [c[i] < sma_in_bar_units(c, sr, i, 10) for i in range(i0 + 1, i0 + 7)]
    assert below == [False, False, False, False, False, True]
    assert reg.status(("SET", t.peak_date)) == Status.CLOSED


def test_partial_skipped_moves_stop_to_breakeven_without_sell_leg() -> None:
    # One share: no partial; stop -> breakeven at the open of bar 4, touched the same day.
    bars = RISE_THEN_FADE[:3] + [(1.01, 1.02, 0.995, 1.0)]
    s, cands, t0, f = scripted(bars)
    trades, _, _ = run_ticker(s, cands, small_approver(), costs=TIERED, trigger=TP)
    t = trades[0]
    assert t.shares == 1 and t.partial_skipped is True and t.partial_shares == 0
    assert [leg.name for leg in t.legs] == ["entry", "exit"]
    assert t.exit_reason == STOP and t.exit_price == pytest.approx(f * (1 - SLIP))
    assert t.pnl_gross_usd == pytest.approx(-f * SLIP)  # breakeven minus exit slippage


def test_trailing_exit_at_open_beats_stop_same_day() -> None:
    pos = Position(("SET", date(2024, 2, 1)), date(2024, 3, 4), 100.0, 30, 96.0, 120.0)
    pos.bars_held, pos.partial_done, pos.pending_exit = 5, True, True
    step_position(
        pos, date(2024, 3, 11), 101.0, 102.0, 90.0, 91.0, 105.0, 10.0, TIERED, ExitParams()
    )
    assert (
        pos.closed
        and pos.exit_reason == TRAIL
        and pos.legs[-1].price == pytest.approx(101.0 * 0.999)
    )


def test_partial_fill_then_breakeven_gap_same_day() -> None:
    pos = Position(("SET", date(2024, 2, 1)), date(2024, 3, 4), 100.0, 30, 96.0, 120.0)
    pos.bars_held, pos.pending_partial = 3, 10
    # Opens above breakeven: partial fills at 103, stop becomes 100, then low 99 stops the rest.
    step_position(pos, date(2024, 3, 8), 103.0, 104.0, 99.0, 99.5, 98.0, 10.0, TIERED, ExitParams())
    assert [(leg.name, leg.shares) for leg in pos.legs] == [("partial", 10), ("exit", 20)]
    assert pos.legs[0].price == pytest.approx(103.0 * 0.999)
    assert pos.closed and pos.exit_reason == STOP and pos.legs[1].ref == 100.0


# --- split while open ---


def test_split_during_open_position_changes_units_not_result() -> None:
    s, cands, t0, f = scripted(RISE_THEN_FADE)
    i0 = int(s.index[s["date"] == t0][0])
    split = apply_split(s, at=i0 + 2, ratio=2.0)
    base, _, _ = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)
    halved, _, _ = run_ticker(split, cands, big_approver(), costs=TIERED, trigger=TP)
    a, b = base[0], halved[0]
    assert b.splits == 1 and b.split_cash_in_lieu_usd == 0.0
    assert (a.shares, a.entry_fill, a.initial_stop) == (b.shares, b.entry_fill, b.initial_stop)
    assert [leg.shares for leg in b.legs] == [33, 22, 44]
    assert b.partial_price == pytest.approx(a.partial_price / 2)
    assert b.exit_price == pytest.approx(a.exit_price / 2)
    assert (b.partial_date, b.exit_date, b.exit_reason) == (
        a.partial_date,
        a.exit_date,
        a.exit_reason,
    )
    assert b.pnl_gross_usd == pytest.approx(a.pnl_gross_usd)
    assert b.pnl_net_usd == pytest.approx(a.pnl_net_usd)
    assert b.r_multiple == pytest.approx(a.r_multiple)


def test_odd_split_sells_fraction_as_cash_in_lieu() -> None:
    pos = Position(("SET", date(2024, 2, 1)), date(2024, 3, 4), 100.0, 33, 96.0, 120.0)
    from tradingbot.backtesting.trades import apply_split as pos_split

    pos_split(pos, date(2024, 3, 6), 1.5, open_=70.0)  # 49.5 shares -> 49 + 0.5 in lieu
    assert pos.shares == 49 and pos.split_cash_in_lieu_usd == pytest.approx(35.0)
    assert pos.stop == pytest.approx(64.0) and pos.entry_fill == pytest.approx(100 / 1.5)


def test_sma_uses_bar_units_across_split() -> None:
    close = np.array([100.0, 100.0, 50.0, 50.0])
    sr = np.array([1.0, 1.0, 2.0, 1.0])
    assert sma_in_bar_units(close, sr, 3, 4) == pytest.approx(50.0)
    assert sma_in_bar_units(close, sr, 1, 4) == pytest.approx(100.0)


# --- same-day stop from 3c ---


def test_same_day_stop_gives_one_trade_one_close_and_consumed_key() -> None:
    df, cands, t0, level = _series_with_breakout(crash_low=1.0)
    trades, rej, reg = run_ticker(df, cands, big_approver(), costs=TIERED, trigger=TP)
    assert len(trades) == 1
    t = trades[0]
    assert t.ambiguous_same_day and t.exit_reason == AMBIGUOUS_SAME_DAY
    assert t.exit_date == t.entry_date == t0 and t.bars_held == 0
    assert [leg.name for leg in t.legs] == ["entry", "exit"]
    assert t.exit_price == pytest.approx(t.initial_stop * (1 - SLIP))
    assert -1.5 < t.r_multiple < -1.0
    log = reg.log_frame()
    assert (log["to_status"] == "CLOSED").sum() == 1 and (
        log["to_status"] == "TRIGGERED"
    ).sum() == 1
    assert reg.status(("SET", t.peak_date)) == Status.CLOSED
    later = [r for r in rej if r.date > t0]
    assert later and all(r.reason == "key_consumed" for r in later)


# --- costs ---


def test_cost_estimate_and_actual_are_separate_and_not_double_counted() -> None:
    s, cands, t0, f = scripted(RISE_THEN_FADE)
    t = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)[0][0]
    entry, partial, exit_ = t.legs
    assert t.cost_actual["commission"] == pytest.approx(3 * 0.35)
    assert t.cost_estimate["commission"] == pytest.approx(3 * 0.35)
    exp_slip = 33 * entry.ref * SLIP + 11 * partial.ref * SLIP + 22 * exit_.ref * SLIP
    assert t.cost_actual["slippage"] == pytest.approx(exp_slip)
    assert t.cost_estimate["slippage"] != pytest.approx(t.cost_actual["slippage"])  # est. at F
    exp_fx = (entry.notional + partial.notional + exit_.notional) * 0.0025
    assert t.cost_actual["fx"] == pytest.approx(exp_fx)
    gross = partial.notional + exit_.notional - entry.notional
    assert t.pnl_gross_usd == pytest.approx(gross)
    # Slippage is inside the fill prices: net subtracts only commission and FX.
    assert t.pnl_net_usd == pytest.approx(gross - 3 * 0.35 - exp_fx)
    assert t.r_multiple == pytest.approx(t.pnl_net_usd / t.initial_risk_usd)
    row = t.to_row()
    assert row["cost_actual_total"] == pytest.approx(sum(t.cost_actual.values()))
    assert row["fx_mode"] == "per_trade" and row["slippage_bps"] == 10.0


# --- look-ahead ---


def test_future_bars_change_exits_but_never_the_entry() -> None:
    s1, c1, t0, f = scripted(RISE_THEN_FADE)
    s2, c2, _, _ = scripted([(0.9, 0.92, 0.88, 0.9)])
    i0 = int(s1.index[s1["date"] == t0][0])
    s3 = apply_split(s1, at=i0 + 1, ratio=2.0)  # split the day after entry
    runs = [
        run_ticker(s, c, big_approver(), costs=TIERED, trigger=TP)
        for s, c in ((s1, c1), (s2, c2), (s3, c1))
    ]
    invariant = [
        (
            t[0][0].entry_date,
            t[0][0].entry_fill,
            t[0][0].shares,
            t[0][0].shares_ordered,
            t[0][0].initial_stop,
            t[0][0].initial_risk_usd,
            t[0][0].gap_fill,
            t[0][0].peak_date,
        )
        for t in runs
    ]
    assert invariant[0] == invariant[1] == invariant[2]
    rej_before = [[(r.date, r.reason) for r in t[1] if r.date < t0] for t in runs]
    assert rej_before[0] == rej_before[1] == rej_before[2]
    variant = [(t[0][0].exit_reason, t[0][0].exit_date, round(t[0][0].r_multiple, 6)) for t in runs]
    assert variant[0] != variant[1]  # the test is not empty
    assert runs[2][0][0].splits == 1 and runs[2][0][0].r_multiple == pytest.approx(
        runs[0][0][0].r_multiple
    )


# --- rejections and reporting ---


def test_rejections_are_counted_per_reason_and_stage() -> None:
    df, cands, t0, _ = _series_with_breakout()
    _, rej, _ = run_ticker(
        df, cands, RiskApprover(acct(), RISK_HALF, TIERED), costs=TIERED, trigger=TP
    )
    at_t0 = [r for r in rej if r.date == t0]
    assert at_t0 == [Rejection("SET", t0, at_t0[0].peak_date, "risk", COST_TO_RISK)]
    gap, cg, _, _ = _series_with_breakout(gap=0.08)
    _, rej_gap, _ = run_ticker(gap, cg, big_approver(), costs=TIERED, trigger=TP)
    assert [r for r in rej_gap if r.date == t0][0].reason == GAP_TOO_LARGE
    frame = rejections_frame(rej_gap)
    assert set(frame.columns) == {"ticker", "date", "peak_date", "stage", "reason"}
    assert frame["reason"].isin([GAP_TOO_LARGE, "no_trigger", "key_consumed"]).all()


def test_open_at_end_is_valued_at_last_close_and_kept_apart() -> None:
    s, cands, t0, f = scripted(RISE_THEN_FADE[:2])
    trades, rej, _ = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)
    t = trades[0]
    assert (
        t.open_at_end and t.exit_reason == OPEN_AT_END and t.exit_price == pytest.approx(1.02 * f)
    )
    assert [leg.name for leg in t.legs] == ["entry"]
    summ = summarize(trades, rej)
    assert summ["realistic"]["n"] == 0 and summ["open_at_end"] == 1


def test_summary_separates_counterfactual_and_reports_versions() -> None:
    s, cands, t0, f = scripted(RISE_THEN_FADE)
    trades, rej, _ = run_ticker(s, cands, big_approver(), costs=TIERED, trigger=TP)
    summ = summarize(trades, rej)
    assert summ["realistic"]["n"] == 1 and summ["counterfactual_reduced"]["n"] == 0
    assert summ["realistic"]["exit_reasons"] == {TRAIL: 1}
    assert summ["rejections"].get("key_consumed", 0) >= 1 and summ["partial_skipped"] == 0
    assert summ["versions"]["trades_version"] == "1" and summ["versions"]["spec_version"] == "0.2.1"
    frame = trades_frame(trades)
    for col in (
        "spec_version",
        "feature_version",
        "rules_version",
        "levels_version",
        "trigger_version",
        "sizing_version",
        "cost_version",
        "candidate_id",
    ):
        assert col in frame.columns
    assert frame.loc[0, "candidate_id"] == cands.loc[cands["date"] == t0, "candidate_id"].item()
    # A counterfactual trade is flagged and excluded from the realistic group.
    cf = Trade(**{**asdict_trade(trades[0]), "shares_reduced_at_fill": True})
    s2 = summarize([cf], [])
    assert s2["realistic"]["n"] == 0 and s2["counterfactual_reduced"]["n"] == 1


def asdict_trade(t: Trade) -> dict:
    from dataclasses import fields

    return {f.name: getattr(t, f.name) for f in fields(Trade)}


def test_tuple_approver_is_refused() -> None:
    df, cands, _, _ = _series_with_breakout()
    with pytest.raises(TypeError, match="RiskDecision"):
        run_ticker(df, cands, lambda _: (True, "stub"), costs=TIERED, trigger=TP)
