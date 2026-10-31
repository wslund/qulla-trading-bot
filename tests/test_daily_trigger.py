"""Step 3c: daily_stop_entry – trigger, assumed fill, stop, same-day ambiguity, registry."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.scanner.features import compute_features
from tradingbot.scanner.rules import apply_rules
from tradingbot.signals.daily_trigger import (
    FILL_CANDIDATE,
    GAP_TOO_LARGE,
    NO_TRIGGER,
    STOP_INVALID,
    Signal,
    TriggerParams,
    approve_all_for_tests,
    approve_and_record,
    build_signal,
    evaluate_day,
    pending_risk_approval,
    walk_daily,
)
from tradingbot.signals.state import InvalidTransition, SetupRegistry, Status

from .test_features import apply_split
from .test_rules import setup_series

P = TriggerParams(max_gap_entry=0.05, slippage_bps=10.0, min_stop_pct=0.01)


def sig(level=100.0, low_prev=96.0, adr=0.05, params=P) -> Signal:
    return Signal(
        key=("SET", date(2024, 2, 1)),
        ticker="SET",
        date=date(2024, 3, 4),
        asof_date=date(2024, 3, 1),
        breakout_level=level,
        low_prev=low_prev,
        adr20=adr,
        split_on_entry_day=False,
        params=params,
    )


# --- evaluate_day: operators, fill, stop ---


def test_no_trigger_when_high_does_not_exceed_level() -> None:
    assert evaluate_day(sig(), open_=99.0, high=100.0, low=98.0).code == NO_TRIGGER
    assert evaluate_day(sig(), open_=99.0, high=99.9, low=98.0).code == NO_TRIGGER


def test_trigger_fills_at_level_plus_slippage() -> None:
    out = evaluate_day(sig(), open_=99.0, high=100.01, low=98.5)
    assert out.code == FILL_CANDIDATE and out.fill is not None
    assert out.fill.fill_price == pytest.approx(100.0 * 1.001)
    assert out.fill.gap_fill is False
    assert out.fill.status == "PRELIMINARY"
    # stop = max(low_prev 96, fill*(1-0.05)=95.095) = 96
    assert out.fill.stop_price == pytest.approx(96.0)
    assert out.fill.ambiguous_same_day is False


def test_gap_within_limit_fills_at_open() -> None:
    out = evaluate_day(sig(), open_=103.0, high=104.0, low=102.0)
    assert out.code == FILL_CANDIDATE
    assert out.fill.gap_fill is True
    assert out.fill.fill_price == pytest.approx(103.0 * 1.001)


def test_gap_boundary_exact_limit_allowed_one_tick_over_refused() -> None:
    assert evaluate_day(sig(), open_=105.0, high=106.0, low=104.0).code == FILL_CANDIDATE
    out = evaluate_day(sig(), open_=105.01, high=106.0, low=104.0)
    assert out.code == GAP_TOO_LARGE and out.fill is None


def test_stop_uses_adr_cap_when_low_prev_is_far() -> None:
    out = evaluate_day(sig(low_prev=80.0, adr=0.04), open_=99.0, high=101.0, low=98.0)
    assert out.fill.stop_price == pytest.approx(100.1 * 0.96)
    assert out.fill.stop_distance_pct == pytest.approx(0.04)


def test_stop_invalid_when_too_tight() -> None:
    # low_prev just below the fill: distance < 1 %
    out = evaluate_day(sig(low_prev=99.5, adr=0.05), open_=99.0, high=101.0, low=99.6)
    assert out.code == STOP_INVALID and out.fill is None


def test_same_day_stop_is_flagged_and_priced_conservatively() -> None:
    out = evaluate_day(sig(), open_=99.0, high=101.0, low=96.0)  # low == stop -> hit
    assert out.fill.ambiguous_same_day is True
    assert out.fill.same_day_stop_fill == pytest.approx(96.0 * 0.999)
    clean = evaluate_day(sig(), open_=99.0, high=101.0, low=96.01)
    assert clean.fill.ambiguous_same_day is False and clean.fill.same_day_stop_fill is None


def test_evaluate_day_reads_only_bar_t_inputs() -> None:
    """The function has no access to anything but T's open/high/low: identical inputs,
    identical outcome regardless of any 'future' passed elsewhere."""
    a = evaluate_day(sig(), 99.0, 101.0, 98.0)
    b = evaluate_day(sig(), 99.0, 101.0, 98.0)
    assert a == b


# --- registry integration ---


def _armed_registry() -> tuple[SetupRegistry, dict]:
    reg = SetupRegistry()
    row = {
        "date": date(2024, 3, 4),
        "ticker": "SET",
        "is_candidate": True,
        "prior_move_peak_date": date(2024, 2, 1),
    }
    assert reg.decide(row).allow_order
    return reg, row


def test_fill_candidate_alone_never_consumes_key() -> None:
    reg, _ = _armed_registry()
    out = evaluate_day(sig(), 99.0, 101.0, 98.0)
    final = approve_and_record(reg, out, *pending_risk_approval(out))
    assert final == "pending_risk_approval"
    assert reg.status(sig().key) == Status.ARMED and not reg.is_consumed(sig().key)
    assert reg.log[-1].reason == "pending_risk_approval"


def test_no_trigger_and_gap_do_not_consume_key() -> None:
    reg, _ = _armed_registry()
    for bar in [(99.0, 100.0, 98.0), (106.0, 107.0, 105.0)]:
        out = evaluate_day(sig(), *bar)
        approve_and_record(reg, out, True, "irrelevant")  # approval cannot rescue a non-fill
    assert not reg.is_consumed(sig().key)
    assert [t.reason for t in reg.log[-2:]] == [NO_TRIGGER, GAP_TOO_LARGE]


def test_approved_fill_consumes_key_and_opens_position() -> None:
    reg, _ = _armed_registry()
    out = evaluate_day(sig(), 99.0, 101.0, 98.0)
    assert approve_and_record(reg, out, *approve_all_for_tests(out)) == "filled"
    assert reg.status(sig().key) == Status.TRIGGERED and reg.open_positions == {"SET": sig().key}


def test_approved_ambiguous_fill_is_closed_same_day() -> None:
    reg, _ = _armed_registry()
    out = evaluate_day(sig(), 99.0, 101.0, 95.0)
    assert approve_and_record(reg, out, True, "ok") == "filled_stopped_same_day"
    assert reg.status(sig().key) == Status.CLOSED and reg.open_positions == {}
    assert reg.is_consumed(sig().key)


def test_approving_without_armed_decision_is_invalid() -> None:
    reg = SetupRegistry()
    out = evaluate_day(sig(), 99.0, 101.0, 98.0)
    with pytest.raises(InvalidTransition):
        approve_and_record(reg, out, True, "ok")


# --- walk_daily on the synthetic formation ---


def _series_with_breakout(gap: float = 0.0, crash_low: float | None = None):
    """setup_series() with the FIRST candidate day T0 shaped as requested: a breakout bar
    (high 2 % above the level), optionally opening with a gap or crashing to ``crash_low``.
    Returns (series, candidates, T0, breakout level in as-traded units)."""
    df = setup_series()
    cands = apply_rules(compute_features(df))
    first = cands[cands["is_candidate"].astype(bool)].iloc[0]
    t0 = first["date"]
    level = df[(df["date"] > first["prior_move_peak_date"]) & (df["date"] < t0)]["high"].max()
    i = df.index[df["date"] == t0][0]
    open_ = level * (1 + gap) if gap else float(df.loc[i - 1, "close"])
    hi = max(open_, level) * 1.02
    lo = crash_low if crash_low is not None else min(open_, level) * 0.995
    df.loc[i, ["open", "high", "low", "close", "adj_close"]] = [open_, hi, lo, hi * 0.99, hi * 0.99]
    return df, cands, t0, level


def test_walk_daily_default_never_fills() -> None:
    df, cands, t0, level = _series_with_breakout()
    trace, reg = walk_daily(df, cands)
    row = trace[trace["date"] == t0].iloc[0]
    assert row["outcome"] == FILL_CANDIDATE and row["final"] == "pending_risk_approval"
    assert row["level"] == pytest.approx(level)
    assert len(reg.consumed) == 0
    # Every later candidate day was evaluated too (key still armed) and none consumed.
    later = trace[(trace["date"] > t0) & (trace["decision"] == "armed")]
    assert len(later) > 0 and later["final"].isin([NO_TRIGGER, "pending_risk_approval"]).all()


def test_walk_daily_with_stub_fills_once_and_dedupes() -> None:
    df, cands, t0, level = _series_with_breakout()
    trace, reg = walk_daily(df, cands, approver=approve_all_for_tests)
    filled = trace[trace["final"] == "filled"]
    assert len(filled) == 1 and filled.iloc[0]["date"] == t0
    assert filled.iloc[0]["fill_price"] == pytest.approx(level * 1.001)
    after = trace[(trace["date"] > t0) & trace["peak_date"].notna()]
    assert after["decision"].isin(["key_consumed", "not_candidate"]).all()
    assert reg.status(next(iter(reg.consumed))) == Status.TRIGGERED


def test_walk_daily_gap_too_large_keeps_key_armed() -> None:
    df, cands, t0, level = _series_with_breakout(gap=0.08)
    trace, reg = walk_daily(df, cands, approver=approve_all_for_tests)
    row = trace[trace["date"] == t0].iloc[0]
    assert row["outcome"] == GAP_TOO_LARGE and row["final"] == GAP_TOO_LARGE
    assert not reg.is_consumed(("SET", row["peak_date"]))
    # The key is still armed on the next candidate day.
    nxt = trace[(trace["date"] > t0) & (trace["decision"] == "armed")]
    assert len(nxt) > 0


def test_walk_daily_same_day_stop_closes_position() -> None:
    df, cands, t0, level = _series_with_breakout(crash_low=1.0)
    trace, reg = walk_daily(df, cands, approver=approve_all_for_tests)
    row = trace[trace["date"] == t0].iloc[0]
    assert bool(row["ambiguous_same_day"]) is True
    assert row["final"] == "filled_stopped_same_day"
    assert reg.open_positions == {} and len(reg.consumed) == 1


def test_future_bars_do_not_change_outcome_for_t() -> None:
    df, cands, t0, level = _series_with_breakout()
    base, _ = walk_daily(df, cands, approver=approve_all_for_tests)
    mutated = df.copy()
    after = mutated["date"] > t0
    for c in ("open", "high", "low", "close", "adj_close"):
        mutated.loc[after, c] = mutated.loc[after, c] * 0.3
    i_after = int(np.flatnonzero(after.to_numpy())[0])
    mutated = apply_split(mutated, at=i_after, ratio=5.0)
    out, _ = walk_daily(mutated, cands, approver=approve_all_for_tests)
    cols = [
        "date",
        "decision",
        "outcome",
        "final",
        "level",
        "fill_price",
        "stop_price",
        "gap_fill",
        "ambiguous_same_day",
    ]
    pd.testing.assert_frame_equal(
        base[base["date"] <= t0][cols].reset_index(drop=True),
        out[out["date"] <= t0][cols].reset_index(drop=True),
    )


def test_split_on_t_scales_levels_and_fill() -> None:
    df, cands, t0, level = _series_with_breakout()
    i = df.index[df["date"] == t0][0]
    split = apply_split(df, at=i, ratio=4.0)
    base, _ = walk_daily(df, cands, approver=approve_all_for_tests)
    out, _ = walk_daily(split, cands, approver=approve_all_for_tests)
    b, o = base[base["date"] == t0].iloc[0], out[out["date"] == t0].iloc[0]
    assert bool(o["split_on_entry_day"]) is True
    assert o["level"] == pytest.approx(b["level"] / 4)
    assert o["fill_price"] == pytest.approx(b["fill_price"] / 4)
    assert o["stop_price"] == pytest.approx(b["stop_price"] / 4)
    assert o["final"] == b["final"] == "filled"


def test_signal_requires_allowed_decision_and_matching_levels() -> None:
    from tradingbot.scanner.levels import compute_levels

    df = setup_series()
    cands = apply_rules(compute_features(df))
    row = cands[cands["is_candidate"].astype(bool)].iloc[-1]
    reg = SetupRegistry()
    dec = reg.decide(row)
    lv = compute_levels(df, row)
    s = build_signal(dec, lv, P)
    assert s.breakout_level == lv.breakout_level_at and s.asof_date == row["asof_date"]
    other = cands[cands["is_candidate"].astype(bool)].iloc[-2]
    with pytest.raises(ValueError, match="different rows"):
        build_signal(dec, compute_levels(df, other), P)
    blocked = reg.decide({**row.to_dict(), "is_candidate": False})
    with pytest.raises(ValueError, match="allowed decision"):
        build_signal(blocked, lv, P)
    assert not np.isnan(lv.adr20)
