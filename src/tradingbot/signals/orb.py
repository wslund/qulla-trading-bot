"""orb_entry: opening range breakout on minute bars (STEP3_DESIGN §5, spec §5.1/§6, D-013).

Inputs for day T: the daily ``Signal`` (levels from bars <= T−1, 3a/3c) and the session's
minute bars as an ITERATOR consumed in time order. Nothing here can read a bar that has not
been consumed; the opening range raises ``LookAheadError`` if its high/low are read before
its last bar is in. Mutation tests (§8) assert the invariants on top of this.

Sequence per session (all times from the XNYS calendar, in UTC):
  gap rule        first session bar's open vs breakout_level (§5.1.4) -> ``gap_too_large``
  opening range   bars with open <= t < open + orb_minutes; all must be present and
                  consecutive, else ``orb_incomplete`` (no entry)
  trigger         max(orb_high, breakout_level_at); the order is live from the first bar
                  after the range; a bar that starts at or after latest_entry_time
                  (close − 30 min) cannot fill -> ``lapsed``
  fill            first later bar with high > trigger: at the bar's open if open > trigger
                  (``gap_fill``) else at trigger, × (1 + slip)
  initial stop    max(lowest low of all consumed bars BEFORE the fill bar, fill × (1 − adr20));
                  the fill bar's own low is never used. Invariants as in 3c (``stop_invalid``)
  same bar        fill bar low <= stop -> ``ambiguous_same_bar`` (FillCandidate.
                  ambiguous_same_day=True, conservative −1R, D-013); nothing else is read
  later bars      low <= stop -> stopped unambiguously the same day (``stopped_intraday``;
                  at the bar's open if open <= stop, else at stop, minus slippage). Not
                  stopped -> the position is carried into T+1 and 3f's daily step_position.
Sizing (3d) uses ``OrderPlan(trigger × (1+slip), max(orb_low, ADR cap))`` – what is known at
09:35 when the order is placed – via ``OrbOutcome.plan``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

from tradingbot.risk.sizing import OrderPlan
from tradingbot.scanner.levels import breakout_triggered, gap_exceeds, stop_hit
from tradingbot.signals.daily_trigger import (
    FILL_CANDIDATE,
    GAP_TOO_LARGE,
    STOP_INVALID,
    FillCandidate,
    Signal,
)

ORB_VERSION = "1"
ORB_INCOMPLETE = "orb_incomplete"
LAPSED = "lapsed"
NO_MINUTE_BARS = "no_minute_bars"
STOP_INTRADAY = "stop_intraday"
MODEL = "orb_entry"


class LookAheadError(RuntimeError):
    """Opening-range values were read before the range's last bar was consumed."""


@dataclass(frozen=True)
class OrbParams:
    orb_minutes: int = 5  # spec §5.1, P-009 (1 and 60 as variants)
    latest_entry_before_close_min: int = 30  # [T] close − 30 min
    open_mismatch_tol: float = 0.001  # first minute open vs daily open, diagnostic


class OpeningRange:
    """Accumulates the first ``minutes`` bars of a session; values readable only once complete."""

    def __init__(self, session_open: pd.Timestamp, minutes: int) -> None:
        self.session_open = session_open
        self.minutes = minutes
        self._high: float | None = None
        self._low: float | None = None
        self.n = 0
        self.broken = False  # a minute was missing / out of order

    @property
    def end(self) -> pd.Timestamp:
        return self.session_open + timedelta(minutes=self.minutes)

    @property
    def complete(self) -> bool:
        return self.n == self.minutes and not self.broken

    def feed(self, bar) -> None:
        expected = self.session_open + timedelta(minutes=self.n)
        if bar.ts_utc != expected:
            self.broken = True
            return
        self.n += 1
        self._high = bar.high if self._high is None else max(self._high, bar.high)
        self._low = bar.low if self._low is None else min(self._low, bar.low)

    @property
    def high(self) -> float:
        if not self.complete:
            raise LookAheadError("opening range not complete")
        return float(self._high)

    @property
    def low(self) -> float:
        if not self.complete:
            raise LookAheadError("opening range not complete")
        return float(self._low)


@dataclass(frozen=True)
class OrbOutcome:
    """Duck-compatible with TriggerOutcome (signal, code, fill) plus the ORB specifics."""

    signal: Signal
    code: str
    fill: FillCandidate | None = None
    plan: OrderPlan | None = None
    trigger: float = float("nan")
    orb_high: float = float("nan")
    orb_low: float = float("nan")
    first_open: float = float("nan")
    fill_time: pd.Timestamp | None = None
    stopped_intraday: bool = False
    stop_time: pd.Timestamp | None = None
    stop_ref: float | None = None  # price before slippage the stop filled at (open or stop)
    bars_seen: int = 0
    detail: str = ""
    model: str = MODEL
    orb_version: str = ORB_VERSION

    def intraday_fields(self) -> dict:
        return {
            "trigger": self.trigger,
            "orb_high": self.orb_high,
            "orb_low": self.orb_low,
            "first_open": self.first_open,
            "fill_time": str(self.fill_time) if self.fill_time is not None else None,
            "stop_time": str(self.stop_time) if self.stop_time is not None else None,
            "bars_seen": self.bars_seen,
            "orb_version": self.orb_version,
        }


def _bars_iter(bars):
    if hasattr(bars, "itertuples"):
        return iter(list(bars.itertuples(index=False)))
    return iter(bars)


def simulate_orb_day(
    signal: Signal,
    bars: Iterable,
    session_open: pd.Timestamp,
    session_close: pd.Timestamp,
    orb: OrbParams | None = None,
) -> OrbOutcome:
    """Run one session's in-session minute bars (objects with ts_utc/open/high/low) through
    the rules above. ``bars`` is consumed in order; see module docstring."""
    p = orb or OrbParams()
    tp = signal.params
    slip = tp.slippage_bps / 10_000
    level = signal.breakout_level
    it = _bars_iter(bars)
    first = next(it, None)
    if first is None:
        return OrbOutcome(signal, NO_MINUTE_BARS, detail="no in-session bars")
    seen = 1
    if first.ts_utc != session_open:
        return OrbOutcome(
            signal,
            ORB_INCOMPLETE,
            first_open=first.open,
            bars_seen=seen,
            detail=f"first bar {first.ts_utc} != session open {session_open}",
        )
    if gap_exceeds(first.open, level, tp.max_gap_entry):
        return OrbOutcome(
            signal,
            GAP_TOO_LARGE,
            first_open=first.open,
            bars_seen=seen,
            detail=f"open={first.open:.4g} level={level:.4g}",
        )
    orr = OpeningRange(session_open, p.orb_minutes)
    orr.feed(first)
    while not orr.complete and not orr.broken:
        bar = next(it, None)
        if bar is None or bar.ts_utc >= orr.end:
            orr.broken = True
            break
        seen += 1
        orr.feed(bar)
    if not orr.complete:
        return OrbOutcome(
            signal,
            ORB_INCOMPLETE,
            first_open=first.open,
            bars_seen=seen,
            detail=f"{orr.n} of {p.orb_minutes} opening-range bars",
        )
    trigger = max(orr.high, level)
    latest = session_close - timedelta(minutes=p.latest_entry_before_close_min)
    fill_est = trigger * (1 + slip)
    plan = OrderPlan(fill_est, max(orr.low, fill_est * (1 - signal.adr20)), MODEL)
    common = dict(
        plan=plan, trigger=trigger, orb_high=orr.high, orb_low=orr.low, first_open=first.open
    )
    lod = orr.low  # lowest low of consumed bars, updated AFTER each bar's trigger check
    fill: FillCandidate | None = None
    fill_time = None
    stop = float("nan")
    for bar in it:
        seen += 1
        if fill is None:
            if bar.ts_utc >= latest:
                return OrbOutcome(
                    signal, LAPSED, bars_seen=seen, detail=f"no fill before {latest}", **common
                )
            if breakout_triggered(bar.high, trigger):
                gap_fill = bar.open > trigger
                price = (bar.open if gap_fill else trigger) * (1 + slip)
                stop = max(lod, price * (1 - signal.adr20))
                if stop >= price:
                    return OrbOutcome(
                        signal,
                        STOP_INVALID,
                        bars_seen=seen,
                        detail=f"stop={stop:.4g} >= fill={price:.4g}",
                        **common,
                    )
                dist = (price - stop) / price
                if dist < tp.min_stop_pct:
                    return OrbOutcome(
                        signal,
                        STOP_INVALID,
                        bars_seen=seen,
                        detail=f"stop distance {dist:.3%} < min",
                        **common,
                    )
                ambiguous = stop_hit(bar.low, stop)
                fill = FillCandidate(
                    fill_price=price,
                    stop_price=stop,
                    stop_distance_pct=dist,
                    gap_fill=gap_fill,
                    ambiguous_same_day=ambiguous,  # = ambiguous_same_bar for ORB
                    same_day_stop_fill=stop * (1 - slip) if ambiguous else None,
                )
                fill_time = bar.ts_utc
                if ambiguous:
                    return OrbOutcome(
                        signal,
                        FILL_CANDIDATE,
                        fill=fill,
                        fill_time=fill_time,
                        bars_seen=seen,
                        detail="ambiguous_same_bar",
                        **common,
                    )
                continue
            lod = min(lod, bar.low)
        else:
            if bar.open <= stop or stop_hit(bar.low, stop):
                ref = bar.open if bar.open <= stop else stop
                return OrbOutcome(
                    signal,
                    FILL_CANDIDATE,
                    fill=fill,
                    fill_time=fill_time,
                    stopped_intraday=True,
                    stop_time=bar.ts_utc,
                    stop_ref=ref,
                    bars_seen=seen,
                    detail=STOP_INTRADAY,
                    **common,
                )
    if fill is None:
        return OrbOutcome(signal, LAPSED, bars_seen=seen, detail="session ended", **common)
    return OrbOutcome(
        signal, FILL_CANDIDATE, fill=fill, fill_time=fill_time, bars_seen=seen, **common
    )
