"""Setup state and dedupe per key (ticker, prior_move_peak_date) – STEP3_DESIGN §3, D-014.

States per key:
  ARMED     the key has a valid candidate row for day T and is not consumed (a per-day
            property: a day whose row fails a rule simply has no order; the key is ARMED
            again on the next candidate day without any reset)
  TRIGGERED a simulated fill happened; the key is consumed permanently
  CLOSED    the position opened by this key was closed; the key stays consumed
  EXPIRED   log-only: the key had candidate days, never filled, and its peak left the window

A new trade requires a candidate row with an unconsumed key and no open position in the
ticker. A missed trigger never consumes a key. Nothing here decides whether a trigger
happened (3c); callers report fills and closes and this module enforces the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import pandas as pd

STATE_VERSION = "1"


class Status(StrEnum):
    NONE = "NONE"  # key never seen
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class Reason(StrEnum):
    ARMED = "armed"  # order may be placed
    NOT_CANDIDATE = "not_candidate"
    NO_PEAK = "no_peak"
    KEY_CONSUMED = "key_consumed"
    POSITION_OPEN = "position_open"


SetupKey = tuple[str, date]


class InvalidTransition(RuntimeError):
    """A state change that the rules do not allow (programming error upstream)."""


@dataclass(frozen=True)
class Decision:
    key: SetupKey | None
    date: date
    ticker: str
    allow_order: bool
    reason: Reason
    status: Status


@dataclass(frozen=True)
class Transition:
    date: date
    ticker: str
    key: SetupKey | None
    from_status: Status
    to_status: Status
    reason: str


def setup_key(row: pd.Series | dict) -> SetupKey | None:
    peak = row.get("prior_move_peak_date")
    if peak is None or pd.isna(peak):
        return None
    return (str(row["ticker"]), pd.Timestamp(peak).date())


@dataclass
class SetupRegistry:
    """Mutable state for one simulation run; one instance per run, all tickers."""

    consumed: dict[SetupKey, date] = field(default_factory=dict)  # key -> fill date
    closed: dict[SetupKey, date] = field(default_factory=dict)
    expired: dict[SetupKey, date] = field(default_factory=dict)
    open_positions: dict[str, SetupKey] = field(default_factory=dict)  # ticker -> key
    seen: set[SetupKey] = field(default_factory=set)
    log: list[Transition] = field(default_factory=list)

    # --- queries -----------------------------------------------------------------------------

    def status(self, key: SetupKey) -> Status:
        if key in self.closed:
            return Status.CLOSED
        if key in self.consumed:
            return Status.TRIGGERED
        if key in self.expired:
            return Status.EXPIRED
        return Status.ARMED if key in self.seen else Status.NONE

    def is_consumed(self, key: SetupKey) -> bool:
        return key in self.consumed

    # --- per-day decision --------------------------------------------------------------------

    def decide(self, row: pd.Series | dict) -> Decision:
        """Whether an order may be placed for this candidate/feature row on its date T."""
        d = pd.Timestamp(row["date"]).date()
        ticker = str(row["ticker"])
        key = setup_key(row)
        if key is None:
            return Decision(None, d, ticker, False, Reason.NO_PEAK, Status.NONE)
        if not bool(row.get("is_candidate", False)):
            return Decision(key, d, ticker, False, Reason.NOT_CANDIDATE, self.status(key))
        if key in self.consumed:
            return Decision(key, d, ticker, False, Reason.KEY_CONSUMED, self.status(key))
        if ticker in self.open_positions:
            return Decision(key, d, ticker, False, Reason.POSITION_OPEN, self.status(key))
        if key not in self.seen:
            self.seen.add(key)
            self._log(d, ticker, key, Status.NONE, Status.ARMED, "first candidate day")
        return Decision(key, d, ticker, True, Reason.ARMED, Status.ARMED)

    # --- transitions reported by the fill/exit layer -----------------------------------------

    def record_fill(self, key: SetupKey, on: date) -> None:
        """A simulated fill happened: consume the key and open the ticker's position."""
        ticker = key[0]
        if key in self.consumed:
            raise InvalidTransition(f"{key}: fill on a consumed key")
        if ticker in self.open_positions:
            raise InvalidTransition(f"{key}: fill while {ticker} has an open position")
        if key not in self.seen:
            raise InvalidTransition(f"{key}: fill without a prior ARMED decision")
        self.consumed[key] = on
        self.open_positions[ticker] = key
        self._log(on, ticker, key, Status.ARMED, Status.TRIGGERED, "filled")

    def record_no_fill(self, key: SetupKey, on: date, reason: str) -> None:
        """A missed trigger (no_trigger, gap_too_large, ...): logged, key stays unconsumed."""
        if key in self.consumed:
            raise InvalidTransition(f"{key}: no-fill reported on a consumed key")
        self._log(on, key[0], key, Status.ARMED, Status.ARMED, reason)

    def record_close(self, key: SetupKey, on: date, reason: str) -> None:
        """The position opened by ``key`` was closed (stop, trailing exit, ...)."""
        ticker = key[0]
        if self.open_positions.get(ticker) != key:
            raise InvalidTransition(f"{key}: close without an open position for this key")
        del self.open_positions[ticker]
        self.closed[key] = on
        self._log(on, ticker, key, Status.TRIGGERED, Status.CLOSED, reason)

    def record_expiry(self, key: SetupKey, on: date) -> None:
        """Log-only: an armed key whose peak left the window without a fill."""
        if key in self.consumed:
            raise InvalidTransition(f"{key}: cannot expire a consumed key")
        if key not in self.seen:
            raise InvalidTransition(f"{key}: cannot expire a key never armed")
        self.expired[key] = on
        self._log(on, key[0], key, Status.ARMED, Status.EXPIRED, "peak left window")

    # --- trace ----------------------------------------------------------------------------------

    def _log(self, d: date, ticker: str, key: SetupKey | None, a: Status, b: Status, why: str):
        self.log.append(Transition(d, ticker, key, a, b, why))

    def log_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": t.date,
                    "ticker": t.ticker,
                    "peak_date": t.key[1] if t.key else None,
                    "from_status": t.from_status.value,
                    "to_status": t.to_status.value,
                    "reason": t.reason,
                    "state_version": STATE_VERSION,
                }
                for t in self.log
            ]
        )


def walk_candidates(
    cands: pd.DataFrame,
    fill_dates: dict[SetupKey, date] | None = None,
    close_dates: dict[SetupKey, date] | None = None,
) -> tuple[pd.DataFrame, SetupRegistry]:
    """Replay a candidate table day by day with scripted fills/closes (tests and dry runs).

    ``fill_dates``: key -> date on which the trigger is assumed to fill (only honoured when
    the registry allows an order that day). ``close_dates``: key -> date the position closes.
    Returns one Decision row per input row plus the registry. 3c replaces the scripted
    fills with real trigger evaluation.
    """
    fill_dates = fill_dates or {}
    close_dates = close_dates or {}
    reg = SetupRegistry()
    out = []
    ordered = cands.assign(_d=pd.to_datetime(cands["date"]).dt.date).sort_values(["_d", "ticker"])
    by_day = {d: day for d, day in ordered.groupby("_d", sort=True)}
    all_days = sorted(set(by_day) | set(close_dates.values()))
    for d in all_days:
        # Closes first (exit orders fill at the open before new entries, spec §6.1).
        for key, cd in close_dates.items():
            if cd == d and reg.open_positions.get(key[0]) == key:
                reg.record_close(key, d, "scripted close")
        for _, row in by_day.get(d, pd.DataFrame()).iterrows():
            dec = reg.decide(row)
            filled = False
            if dec.allow_order and dec.key is not None:
                if fill_dates.get(dec.key) == d:
                    reg.record_fill(dec.key, d)
                    filled = True
                else:
                    reg.record_no_fill(dec.key, d, "no_trigger")
            out.append(
                {
                    "date": d,
                    "ticker": dec.ticker,
                    "peak_date": dec.key[1] if dec.key else None,
                    "allow_order": dec.allow_order,
                    "reason": dec.reason.value,
                    "status_before": dec.status.value,
                    "filled": filled,
                }
            )
    return pd.DataFrame(out), reg
