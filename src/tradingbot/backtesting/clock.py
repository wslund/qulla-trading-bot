"""Shared XNYS session clock with the four phases of PORTFOLIO_DESIGN §2 (P1).

One clock for all tickers. Sessions, open/close times, half days and DST come from the
exchange calendar already used by the data layer (``data.intraday.session_bounds``); nothing
is hard-coded. Phases run in a fixed order inside every session:

  PRE_OPEN  exit orders from T−1, candidates, priority, reservations   (reads <= T−1)
  OPEN      splits, exit fills and stop gaps at open[T]                 (reads open[T])
  INTRADAY  stops on open positions, new entries (daily / ORB stream)   (reads high/low[T])
  CLOSE     mark-to-market, §8 decisions, limits, release, ledger row   (reads close[T])

The clock only yields (session, phase) pairs; what happens in a phase is P2–P4's job.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import pandas as pd

from tradingbot.data.intraday import _calendar, session_bounds

FULL_SESSION_MINUTES = 390


class Phase(StrEnum):
    PRE_OPEN = "pre_open"
    OPEN = "open"
    INTRADAY = "intraday"
    CLOSE = "close"


PHASES: tuple[Phase, ...] = (Phase.PRE_OPEN, Phase.OPEN, Phase.INTRADAY, Phase.CLOSE)


@dataclass(frozen=True)
class Session:
    date: date
    open_utc: pd.Timestamp
    close_utc: pd.Timestamp
    index: int  # position in the clock's session list (0-based)

    @property
    def minutes(self) -> int:
        return int((self.close_utc - self.open_utc).total_seconds() // 60)

    @property
    def is_half_day(self) -> bool:
        return self.minutes < FULL_SESSION_MINUTES

    @property
    def open_ny_hour(self) -> int:
        return int(self.open_utc.tz_convert("America/New_York").hour)


class SessionClock:
    """All XNYS sessions in [start, end], in order; iterate sessions or (session, phase)."""

    def __init__(self, start: date, end: date) -> None:
        if end < start:
            raise ValueError("end before start")
        cal = _calendar()
        days = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
        self._sessions: list[Session] = []
        for i, ts in enumerate(days):
            d = ts.date()
            o, c = session_bounds(d)
            self._sessions.append(Session(d, o, c, i))
        self._by_date = {s.date: s for s in self._sessions}

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions)

    def __len__(self) -> int:
        return len(self._sessions)

    def __iter__(self) -> Iterator[Session]:
        return iter(self._sessions)

    def steps(self) -> Iterator[tuple[Session, Phase]]:
        """Every (session, phase) in chronological order – the engine's main loop."""
        for s in self._sessions:
            for p in PHASES:
                yield s, p

    def is_session(self, d: date) -> bool:
        return d in self._by_date

    def session(self, d: date) -> Session:
        try:
            return self._by_date[d]
        except KeyError:
            raise ValueError(f"{d} is not an XNYS session in this clock") from None

    def previous(self, d: date) -> Session | None:
        s = self.session(d)
        return self._sessions[s.index - 1] if s.index > 0 else None

    def next(self, d: date) -> Session | None:
        s = self.session(d)
        return self._sessions[s.index + 1] if s.index + 1 < len(self._sessions) else None
