"""Step 3b: setup state and dedupe per (ticker, prior_move_peak_date) – D-014."""

from datetime import date, timedelta

import pandas as pd
import pytest

from tradingbot.scanner.features import compute_features
from tradingbot.scanner.rules import apply_rules
from tradingbot.signals.state import (
    InvalidTransition,
    Reason,
    SetupRegistry,
    Status,
    setup_key,
    walk_candidates,
)

from .test_rules import setup_series

D0 = date(2024, 3, 4)
PEAK_A = date(2024, 2, 1)
PEAK_B = date(2024, 2, 20)


def day(i: int) -> date:
    return D0 + timedelta(days=i)


def table(spec: list[tuple], ticker: str = "SET") -> pd.DataFrame:
    """spec: (day index, is_candidate, peak_date or None)."""
    return pd.DataFrame(
        [
            {"date": day(i), "ticker": ticker, "is_candidate": c, "prior_move_peak_date": p}
            for i, c, p in spec
        ]
    )


def key(ticker: str, peak: date) -> tuple[str, date]:
    return (ticker, peak)


# --- dedupe within one formation ---


def test_consecutive_candidate_days_one_fill_then_consumed() -> None:
    cands = table([(i, True, PEAK_A) for i in range(5)])
    trace, reg = walk_candidates(cands, fill_dates={key("SET", PEAK_A): day(2)})
    assert list(trace["allow_order"]) == [True, True, True, False, False]
    assert list(trace["filled"]) == [False, False, True, False, False]
    assert list(trace["reason"][3:]) == ["key_consumed", "key_consumed"]
    assert reg.status(key("SET", PEAK_A)) == Status.TRIGGERED
    assert reg.open_positions == {"SET": key("SET", PEAK_A)}


def test_missed_trigger_does_not_consume() -> None:
    cands = table([(i, True, PEAK_A) for i in range(4)])
    trace, reg = walk_candidates(cands)  # never fills
    assert trace["allow_order"].all() and not trace["filled"].any()
    assert reg.status(key("SET", PEAK_A)) == Status.ARMED
    assert not reg.is_consumed(key("SET", PEAK_A))
    assert [t.reason for t in reg.log] == ["first candidate day"] + ["no_trigger"] * 4


def test_gap_in_candidate_sequence_keeps_same_key() -> None:
    # A, (rule fails), A  -- same peak -> same key.
    cands = table([(0, True, PEAK_A), (1, False, PEAK_A), (2, True, PEAK_A)])
    trace, reg = walk_candidates(cands)
    assert list(trace["reason"]) == ["armed", "not_candidate", "armed"]
    assert len(reg.seen) == 1  # one key, armed once in the log
    assert sum(t.reason == "first candidate day" for t in reg.log) == 1

    trace2, reg2 = walk_candidates(cands, fill_dates={key("SET", PEAK_A): day(0)})
    assert list(trace2["filled"]) == [True, False, False]
    assert trace2["reason"].iloc[2] == "key_consumed"


def test_stopped_position_blocks_same_key_forever_but_not_new_key() -> None:
    cands = table(
        [(0, True, PEAK_A), (1, True, PEAK_A), (3, True, PEAK_A)]
        + [(20, True, PEAK_B), (21, True, PEAK_B)]
    )
    k_a, k_b = key("SET", PEAK_A), key("SET", PEAK_B)
    trace, reg = walk_candidates(
        cands, fill_dates={k_a: day(0), k_b: day(20)}, close_dates={k_a: day(2)}
    )
    assert reg.status(k_a) == Status.CLOSED
    # Day 3: same key after the stop -> consumed, even though the position is closed.
    assert trace.loc[trace["date"] == day(3), "reason"].item() == "key_consumed"
    # New peak with its own candidate rows -> new key, new trade.
    assert trace.loc[trace["date"] == day(20), "filled"].item() is True
    assert reg.status(k_b) == Status.TRIGGERED


def test_new_peak_while_position_open_is_blocked() -> None:
    cands = table([(0, True, PEAK_A), (5, True, PEAK_B), (9, True, PEAK_B)])
    k_a, k_b = key("SET", PEAK_A), key("SET", PEAK_B)
    trace, reg = walk_candidates(
        cands, fill_dates={k_a: day(0), k_b: day(5)}, close_dates={k_a: day(8)}
    )
    assert trace.loc[trace["date"] == day(5), "reason"].item() == "position_open"
    assert trace.loc[trace["date"] == day(5), "filled"].item() is False
    assert not reg.is_consumed(k_b)  # blocked day did not consume the new key
    # After the close, the new key is armed on its next candidate day.
    assert trace.loc[trace["date"] == day(9), "reason"].item() == "armed"


def test_three_formations_under_same_peak_give_one_trade() -> None:
    cands = table([(0, True, PEAK_A), (15, True, PEAK_A), (40, True, PEAK_A)])
    k = key("SET", PEAK_A)
    trace, reg = walk_candidates(cands, fill_dates={k: day(0)}, close_dates={k: day(5)})
    assert list(trace["filled"]) == [True, False, False]
    assert list(trace["reason"][1:]) == ["key_consumed", "key_consumed"]


def test_rows_without_peak_never_allow_orders() -> None:
    cands = table([(0, True, None), (1, False, None)])
    trace, reg = walk_candidates(cands)
    assert list(trace["reason"]) == ["no_peak", "no_peak"]
    assert not trace["allow_order"].any() and reg.log == []


# --- several tickers ---


def test_tickers_are_independent() -> None:
    a = table([(0, True, PEAK_A), (1, True, PEAK_A)], ticker="AAA")
    b = table([(0, True, PEAK_A), (1, True, PEAK_A)], ticker="BBB")
    cands = pd.concat([a, b], ignore_index=True)
    trace, reg = walk_candidates(cands, fill_dates={key("AAA", PEAK_A): day(0)})
    assert set(reg.open_positions) == {"AAA"}
    bbb = trace[trace["ticker"] == "BBB"]
    assert bbb["allow_order"].all() and bbb["reason"].eq("armed").all()
    aaa = trace[trace["ticker"] == "AAA"]
    assert list(aaa["reason"]) == ["armed", "key_consumed"]


# --- invalid transitions ---


def test_invalid_transitions_raise() -> None:
    reg = SetupRegistry()
    k = key("SET", PEAK_A)
    row = {"date": day(0), "ticker": "SET", "is_candidate": True, "prior_move_peak_date": PEAK_A}
    with pytest.raises(InvalidTransition, match="without a prior ARMED"):
        reg.record_fill(k, day(0))
    with pytest.raises(InvalidTransition, match="without an open position"):
        reg.record_close(k, day(0), "stop")
    with pytest.raises(InvalidTransition, match="never armed"):
        reg.record_expiry(k, day(0))

    assert reg.decide(row).allow_order
    reg.record_fill(k, day(0))
    with pytest.raises(InvalidTransition, match="consumed key"):
        reg.record_fill(k, day(1))
    with pytest.raises(InvalidTransition, match="consumed key"):
        reg.record_no_fill(k, day(1), "no_trigger")
    with pytest.raises(InvalidTransition, match="cannot expire a consumed key"):
        reg.record_expiry(k, day(1))
    k2 = key("SET", PEAK_B)
    reg.decide({**row, "date": day(1), "prior_move_peak_date": PEAK_B})  # blocked, not seen
    with pytest.raises(InvalidTransition):
        reg.record_fill(k2, day(1))
    reg.record_close(k, day(2), "stop")
    assert reg.status(k) == Status.CLOSED and "SET" not in reg.open_positions
    with pytest.raises(InvalidTransition):
        reg.record_close(k, day(3), "again")


def test_expiry_is_log_only_and_terminal() -> None:
    reg = SetupRegistry()
    k = key("SET", PEAK_A)
    row = {"date": day(0), "ticker": "SET", "is_candidate": True, "prior_move_peak_date": PEAK_A}
    reg.decide(row)
    reg.record_expiry(k, day(70))
    assert reg.status(k) == Status.EXPIRED
    frame = reg.log_frame()
    assert list(frame["to_status"]) == ["ARMED", "EXPIRED"]
    assert frame["state_version"].eq("1").all()


def test_decision_reason_order_consumed_before_position() -> None:
    reg = SetupRegistry()
    k = key("SET", PEAK_A)
    row = {"date": day(0), "ticker": "SET", "is_candidate": True, "prior_move_peak_date": PEAK_A}
    reg.decide(row)
    reg.record_fill(k, day(0))
    d = reg.decide({**row, "date": day(1)})
    assert d.reason == Reason.KEY_CONSUMED and d.status == Status.TRIGGERED
    d2 = reg.decide({**row, "date": day(1), "prior_move_peak_date": PEAK_B})
    assert d2.reason == Reason.POSITION_OPEN and d2.status == Status.NONE


# --- against the real scanner output ---


def test_real_candidate_table_uses_peak_key_not_group_id() -> None:
    from tradingbot.scanner.diagnostics import add_group_diagnostics

    df = setup_series()
    cands = add_group_diagnostics(apply_rules(compute_features(df)))
    # Break the group in the middle by failing one rule on one day; the key must survive.
    idx = cands.index[cands["is_candidate"].astype(bool)]
    mid = idx[len(idx) // 2]
    cands.loc[mid, "is_candidate"] = False
    regrouped = add_group_diagnostics(
        cands.drop(columns=["setup_group_id", "group_rank", "group_size"])
    )
    assert regrouped.loc[regrouped["is_candidate"].astype(bool), "setup_group_id"].nunique() == 2
    keys = {setup_key(r) for _, r in regrouped[regrouped["is_candidate"].astype(bool)].iterrows()}
    assert len(keys) == 1  # one consolidation, one key
    k = next(iter(keys))
    trace, reg = walk_candidates(regrouped, fill_dates={k: regrouped.loc[idx[0], "date"]})
    assert trace["filled"].sum() == 1
    after = trace[(trace["date"] > regrouped.loc[idx[0], "date"]) & trace["peak_date"].notna()]
    assert set(after["reason"]) <= {"key_consumed", "not_candidate"}
