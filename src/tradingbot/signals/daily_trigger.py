"""daily_stop_entry: trigger and assumed fill on daily bars (STEP3_DESIGN §4/§4.1, D-013).

Three separate things, never merged:
- ``Signal``        what is known before the open of T: levels from bars <= T-1 and the
                    parameters of the order. Built from a registry Decision + OrderLevels.
- ``FillCandidate`` what the simulation *assumes* happened on T if the order had been
                    placed: price, initial stop, flags. PRELIMINARY – it is not a trade.
                    Risk/sizing (3d) must approve it before anything is recorded.
- the registry      ``record_fill`` is only called through ``approve_and_record`` with an
                    explicit approval; a trigger alone never consumes a key.

Exits are not modelled here (3f), with one exception needed for state consistency: an
approved fill whose entry bar also touched the stop is closed the same day at the stop
(conservative −1R policy, ``ambiguous_same_day``). Stop gaps on later days (loss > 1R) are
3f. Prices are as-traded via OrderLevels; a split on T has already been applied there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol

import numpy as np
import pandas as pd

from tradingbot.scanner.levels import (
    LevelsError,
    OrderLevels,
    breakout_triggered,
    compute_levels,
    gap_exceeds,
    stop_hit,
)
from tradingbot.signals.state import Decision, SetupKey, SetupRegistry

TRIGGER_VERSION = "1"

# Outcome codes (STEP3_DESIGN §4.1)
NO_TRIGGER = "no_trigger"
GAP_TOO_LARGE = "gap_too_large"
STOP_INVALID = "stop_invalid"
FILL_CANDIDATE = "fill_candidate"
LEVELS_UNAVAILABLE = "levels_unavailable"
NO_BAR = "no_bar"


@dataclass(frozen=True)
class TriggerParams:
    """Spec §5–6 parameters. slippage_bps is a scenario assumption (D-013), not a measurement."""

    max_gap_entry: float = 0.05  # §5.1.4 / P-004
    slippage_bps: float = 10.0  # scenario; run 5/10/20
    min_stop_pct: float = 0.01  # §6


@dataclass(frozen=True)
class Signal:
    """Known before the open of T (all inputs from bars <= T-1 plus split_ratio[T])."""

    key: SetupKey
    ticker: str
    date: date  # T
    asof_date: date  # T-1
    breakout_level: float  # as-traded, share basis of T
    low_prev: float
    adr20: float
    split_on_entry_day: bool
    params: TriggerParams

    def to_dict(self) -> dict:
        d = asdict(self)
        d["params"] = asdict(self.params)
        return d


@dataclass(frozen=True)
class FillCandidate:
    """Assumed fill on T. PRELIMINARY until 3d approves; never a recorded trade by itself."""

    fill_price: float  # including slippage
    stop_price: float  # initial stop (max(low_prev, fill*(1-adr20)))
    stop_distance_pct: float  # (fill - stop) / fill
    gap_fill: bool  # open > level: filled at the open, not at the level
    ambiguous_same_day: bool  # low[T] <= stop: order of fill/stop unknown -> stopped
    same_day_stop_fill: float | None  # conservative stop fill price if ambiguous
    status: str = "PRELIMINARY"


@dataclass(frozen=True)
class TriggerOutcome:
    signal: Signal
    code: str
    fill: FillCandidate | None = None
    detail: str = ""


# --- signal -----------------------------------------------------------------------------------


def build_signal(decision: Decision, levels: OrderLevels, params: TriggerParams) -> Signal:
    if decision.key is None or not decision.allow_order:
        raise ValueError("build_signal requires an allowed decision with a key")
    if levels.decision_date != decision.date or levels.ticker != decision.ticker:
        raise ValueError("levels and decision refer to different rows")
    if np.isnan(levels.adr20):
        raise LevelsError(f"{levels.ticker} {levels.decision_date}: adr20 missing")
    return Signal(
        key=decision.key,
        ticker=decision.ticker,
        date=decision.date,
        asof_date=levels.asof_date,
        breakout_level=levels.breakout_level_at,
        low_prev=levels.low_prev_at,
        adr20=levels.adr20,
        split_on_entry_day=levels.split_on_entry_day,
        params=params,
    )


# --- day T -------------------------------------------------------------------------------------


def evaluate_day(signal: Signal, open_: float, high: float, low: float) -> TriggerOutcome:
    """Apply spec §5.2/§6 to bar T. Only T's open/high/low are read; close is not needed."""
    p = signal.params
    level = signal.breakout_level
    if gap_exceeds(open_, level, p.max_gap_entry):
        return TriggerOutcome(signal, GAP_TOO_LARGE, detail=f"open={open_:.4g} level={level:.4g}")
    if not breakout_triggered(high, level):
        return TriggerOutcome(signal, NO_TRIGGER, detail=f"high={high:.4g} level={level:.4g}")
    slip = p.slippage_bps / 10_000
    gap_fill = open_ > level  # D-013: gap through the level -> filled at the open
    fill = (open_ if gap_fill else level) * (1 + slip)
    stop = max(signal.low_prev, fill * (1 - signal.adr20))
    if stop >= fill:
        return TriggerOutcome(signal, STOP_INVALID, detail=f"stop={stop:.4g} >= fill={fill:.4g}")
    dist = (fill - stop) / fill
    if dist < p.min_stop_pct:
        return TriggerOutcome(signal, STOP_INVALID, detail=f"stop distance {dist:.3%} < min")
    ambiguous = stop_hit(low, stop)
    same_day_stop = stop * (1 - slip) if ambiguous else None
    fc = FillCandidate(
        fill_price=fill,
        stop_price=stop,
        stop_distance_pct=dist,
        gap_fill=gap_fill,
        ambiguous_same_day=ambiguous,
        same_day_stop_fill=same_day_stop,
    )
    return TriggerOutcome(signal, FILL_CANDIDATE, fill=fc)


# --- integration with the registry / 3d -----------------------------------------------------------


class Approval(Protocol):
    """What an approver may return instead of a bare ``(approved, reason)`` tuple (3d's
    ``RiskDecision``): the same two fields plus optional extra trace columns."""

    approved: bool
    reason: str

    def trace(self) -> dict[str, object]: ...


Approver = Callable[[TriggerOutcome], tuple[bool, str] | Approval]


def as_approval(result: tuple[bool, str] | Approval) -> tuple[bool, str, dict[str, object]]:
    """Normalize an approver result; a tuple carries no trace columns."""
    if isinstance(result, tuple):
        approved, reason = result
        return bool(approved), str(reason), {}
    return bool(result.approved), str(result.reason), dict(result.trace())


def approve_and_record(
    registry: SetupRegistry, outcome: TriggerOutcome, approved: bool, reason: str
) -> str:
    """The only path to record_fill. ``approved`` comes from risk/sizing (3d) or a test stub.

    Returns the final state code written to the trace: 'filled', 'filled_stopped_same_day'
    or the rejection reason. Rejected or non-fill outcomes never consume the key.
    """
    key, on = outcome.signal.key, outcome.signal.date
    if outcome.code != FILL_CANDIDATE or outcome.fill is None:
        registry.record_no_fill(key, on, outcome.code)
        return outcome.code
    if not approved:
        registry.record_no_fill(key, on, reason)
        return reason
    registry.record_fill(key, on)
    if outcome.fill.ambiguous_same_day:
        registry.record_close(key, on, "ambiguous_same_day_stop")
        return "filled_stopped_same_day"
    return "filled"


def pending_risk_approval(_: TriggerOutcome) -> tuple[bool, str]:
    """Default approver when no RiskApprover (3d) is passed: nothing is approved, keys are
    never consumed."""
    return False, "pending_risk_approval"


def approve_all_for_tests(_: TriggerOutcome) -> tuple[bool, str]:
    """Test stub: approves every fill candidate. Not a trading decision."""
    return True, "approved_by_test_stub"


# --- driver ------------------------------------------------------------------------------------


def walk_daily(
    series: pd.DataFrame,
    cands: pd.DataFrame,
    *,
    params: TriggerParams | None = None,
    approver: Approver = pending_risk_approval,
    close_dates: dict[SetupKey, date] | None = None,
    registry: SetupRegistry | None = None,
) -> tuple[pd.DataFrame, SetupRegistry]:
    """Replay one ticker's candidate table against its as-traded series, day by day.

    Order per day (spec §6.1): scripted closes (exits fill at the open) before new entries.
    For each allowed decision: levels from bars <= T-1, trigger on bar T, approval via
    ``approver``. Returns a trace with one row per candidate row.
    """
    p = params or TriggerParams()
    close_dates = close_dates or {}
    reg = registry or SetupRegistry()
    bars = series.set_index("date")
    rows = []
    ordered = cands.assign(_d=pd.to_datetime(cands["date"]).dt.date).sort_values("_d")
    by_day = {d: day for d, day in ordered.groupby("_d", sort=True)}
    for d in sorted(set(by_day) | set(close_dates.values())):
        for key, cd in close_dates.items():
            if cd == d and reg.open_positions.get(key[0]) == key:
                reg.record_close(key, d, "scripted close")
        for _, row in by_day.get(d, pd.DataFrame()).iterrows():
            dec = reg.decide(row)
            rec = {
                "date": d,
                "ticker": dec.ticker,
                "peak_date": dec.key[1] if dec.key else None,
                "decision": dec.reason.value,
                "outcome": None,
                "final": None,
                "level": np.nan,
                "fill_price": np.nan,
                "stop_price": np.nan,
                "gap_fill": None,
                "ambiguous_same_day": None,
                "split_on_entry_day": None,
                "detail": "",
                "trigger_version": TRIGGER_VERSION,
            }
            if dec.allow_order:
                try:
                    lv = compute_levels(series, row)
                    sig = build_signal(dec, lv, p)
                except LevelsError as e:
                    reg.record_no_fill(dec.key, d, LEVELS_UNAVAILABLE)
                    rec.update(outcome=LEVELS_UNAVAILABLE, final=LEVELS_UNAVAILABLE, detail=str(e))
                    rows.append(rec)
                    continue
                if d not in bars.index:
                    reg.record_no_fill(dec.key, d, NO_BAR)
                    rec.update(outcome=NO_BAR, final=NO_BAR, level=sig.breakout_level)
                    rows.append(rec)
                    continue
                bar = bars.loc[d]
                out = evaluate_day(sig, float(bar["open"]), float(bar["high"]), float(bar["low"]))
                extra: dict[str, object] = {}
                if out.code == FILL_CANDIDATE:
                    approved, why, extra = as_approval(approver(out))
                else:
                    approved, why = False, out.code
                final = approve_and_record(reg, out, approved, why)
                rec.update(
                    outcome=out.code,
                    final=final,
                    level=sig.breakout_level,
                    split_on_entry_day=sig.split_on_entry_day,
                    detail=out.detail,
                    **extra,
                )
                if out.fill:
                    rec.update(
                        fill_price=out.fill.fill_price,
                        stop_price=out.fill.stop_price,
                        gap_fill=out.fill.gap_fill,
                        ambiguous_same_day=out.fill.ambiguous_same_day,
                    )
            rows.append(rec)
    return pd.DataFrame(rows), reg
