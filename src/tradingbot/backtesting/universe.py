"""Universe for the portfolio engine (UNIVERSE_DESIGN §1, §9; D-021–D-023). U1 step A.

Two things, kept apart on purpose (D-021):

* ``members(asof)`` – which instruments may receive *new* orders when the pre_open phase
  of day T looks at candidate rows built at T−1. L1 = listed on the main list on ``asof``;
  L2 = L1 and tradable, which in this step means "the price source has a bar with volume
  on ``asof``" when bar information is supplied, otherwise L2 is reported as unknown and
  equals L1. L3 (price/volume rules) and L4 (signal) stay in the existing scanner.
* ``events_for_positions(start, end)`` – delisting and corporate-action events that
  affect positions already open, regardless of membership. The engine does not act on
  them yet (U1 step A is model + tests only); the accessor exists so step B/U3 cannot
  conflate the two flows.

``PilotUniverse`` wraps today's fixed ticker list (no listing history): every ticker is a
member on every day, membership_source = "pilot_list", and knowledge gap is unknown. It
exists so the engine can get a ``Universe`` without pretending the pilot is point-in-time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from tradingbot.data.universe_model import (
    UNCERTAIN,
    CorporateAction,
    Delisting,
    Issue,
    UniverseModel,
)

EVALUATION, RESEARCH, PILOT = "evaluation", "research", "pilot"


class UniverseError(ValueError):
    pass


class AmbiguousTicker(UniverseError):
    pass


@dataclass(frozen=True)
class Member:
    instrument_id: str
    ticker: str
    exchange: str
    layer: str  # "L1" (listed) or "L2" (listed and a bar with volume exists on asof)
    tradable_known: bool  # False when no bar information was supplied


@dataclass(frozen=True)
class Membership:
    asof: date
    members: tuple[Member, ...]
    excluded_uncertain: tuple[str, ...]  # instrument_ids excluded by match_confidence
    knowledge_gap: int | None  # facts used whose observed_at > asof (None = unknown)
    facts_total: int | None
    source: str

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(m.ticker for m in self.members)


@dataclass(frozen=True)
class PositionEvent:
    instrument_id: str
    ticker: str | None
    kind: str  # "delisting" | corporate action type
    effective_date: date
    category: str | None  # D-020 category for delistings
    value_per_share: float | None
    observed_at: datetime
    source: str


class Universe(Protocol):
    version: str

    def members(self, asof: date, *, knowledge_at: datetime | None = None) -> Membership: ...

    def instrument_for(self, ticker: str, asof: date) -> str | None: ...

    def ticker_on(self, instrument_id: str, asof: date) -> str | None: ...

    def events_for_positions(self, start: date, end: date) -> list[PositionEvent]: ...

    def describe(self) -> dict: ...

    def quality(self, bar_dates: dict[str, Iterable[date]]) -> list[Issue]: ...


class PilotUniverse:
    """Fixed ticker list = the pilot's survivors. Not point-in-time (D-012)."""

    def __init__(self, tickers: Iterable[str], version: str = "pilot") -> None:
        self.tickers = tuple(sorted(set(tickers)))
        self.version = version

    def members(self, asof: date, *, knowledge_at: datetime | None = None) -> Membership:
        ms = tuple(Member(t, t, "unknown", "L1", False) for t in self.tickers)
        return Membership(asof, ms, (), None, None, "pilot_list")

    def instrument_for(self, ticker: str, asof: date) -> str | None:
        return ticker if ticker in self.tickers else None

    def ticker_on(self, instrument_id: str, asof: date) -> str | None:
        return instrument_id if instrument_id in self.tickers else None

    def events_for_positions(self, start: date, end: date) -> list[PositionEvent]:
        return []

    def describe(self) -> dict:
        h = hashlib.sha256("\n".join(self.tickers).encode()).hexdigest()
        return {
            "universe_version": self.version,
            "source": "pilot_list",
            "mode": PILOT,
            "checksum": h,
            "n_instruments": len(self.tickers),
            "point_in_time": False,
        }

    def quality(self, bar_dates: dict[str, Iterable[date]]) -> list[Issue]:
        return []  # no listing history to check against


HasBar = Callable[[str, date], bool | None]  # (instrument_id, day) -> bar with volume? None=unknown


class PointInTimeUniverse:
    """Membership from a ``UniverseModel``.

    ``mode``: ``evaluation`` excludes instruments whose only identifier links are
    ``uncertain`` (D-022); ``research`` keeps them and lists them in
    ``Membership.excluded_uncertain`` as empty but flags nothing else – the report is
    expected to print the count either way.
    ``has_bar``: optional callback telling whether the price source has a bar with
    volume for (instrument_id, day). Without it L2 is unknown.
    """

    def __init__(
        self, model: UniverseModel, *, has_bar: HasBar | None = None, mode: str = EVALUATION
    ) -> None:
        if mode not in (EVALUATION, RESEARCH):
            raise UniverseError(f"unknown mode {mode!r}")
        fails = [i for i in model.validate() if i.level == "FAIL"]
        if fails:
            raise UniverseError(f"model {model.version} has {len(fails)} FAIL issue(s): {fails[0]}")
        self.model = model
        self.has_bar = has_bar
        self.mode = mode
        self.version = model.version

    # -- helpers ------------------------------------------------------------------------------

    def _uncertain_only(self) -> set[str]:
        by_inst: dict[str, set[str]] = {}
        for idf in self.model.identifiers:
            by_inst.setdefault(idf.instrument_id, set()).add(idf.match_confidence)
        return {iid for iid, confs in by_inst.items() if confs == {UNCERTAIN}}

    def ticker_on(self, instrument_id: str, asof: date) -> str | None:
        spans = [
            s
            for s in self.model.ticker_spans
            if s.instrument_id == instrument_id and s.covers(asof)
        ]
        if len(spans) > 1:
            raise AmbiguousTicker(f"{instrument_id} has {len(spans)} tickers on {asof}")
        return spans[0].ticker if spans else None

    def instrument_for(self, ticker: str, asof: date) -> str | None:
        """Date-aware lookup; never merges a reused ticker silently (D-022)."""
        hits = {
            s.instrument_id
            for s in self.model.ticker_spans
            if s.ticker == ticker and s.covers(asof)
        }
        if len(hits) > 1:
            raise AmbiguousTicker(f"ticker {ticker} maps to {sorted(hits)} on {asof}")
        return next(iter(hits)) if hits else None

    def members(self, asof: date, *, knowledge_at: datetime | None = None) -> Membership:
        excluded = self._uncertain_only() if self.mode == EVALUATION else set()
        gap = 0
        total = 0
        out: list[Member] = []
        for ls in self.model.listings:
            if knowledge_at is not None and ls.observed_at > knowledge_at:
                continue  # fact not known at knowledge_at (D-023)
            if not ls.covers(asof):
                continue
            total += 1
            if ls.observed_at.date() > asof:
                gap += 1
            if ls.instrument_id in excluded:
                continue
            ticker = self._ticker_known(ls.instrument_id, asof, knowledge_at)
            if ticker is None:
                continue  # listed but no ticker known for the day: cannot be ordered
            tradable = self.has_bar(ls.instrument_id, asof) if self.has_bar else None
            if tradable is False:
                continue  # L1 but not L2: no bar / no volume on asof
            out.append(
                Member(
                    ls.instrument_id,
                    ticker,
                    ls.exchange,
                    "L2" if tradable else "L1",
                    tradable is not None,
                )
            )
        out.sort(key=lambda m: (m.ticker, m.instrument_id))
        tickers = [m.ticker for m in out]
        dup = {t for t in tickers if tickers.count(t) > 1}
        if dup:
            raise AmbiguousTicker(f"ticker(s) {sorted(dup)} map to several instruments on {asof}")
        return Membership(
            asof,
            tuple(out),
            tuple(sorted(excluded)),
            gap,
            total,
            f"universe:{self.version}:{self.mode}",
        )

    def _ticker_known(self, iid: str, asof: date, knowledge_at: datetime | None) -> str | None:
        spans = [
            s
            for s in self.model.ticker_spans
            if s.instrument_id == iid
            and s.covers(asof)
            and (knowledge_at is None or s.observed_at <= knowledge_at)
        ]
        if len(spans) > 1:
            raise AmbiguousTicker(f"{iid} has {len(spans)} tickers on {asof}")
        return spans[0].ticker if spans else None

    def describe(self) -> dict:
        return {
            "universe_version": self.version,
            "source": f"universe_model:{self.version}",
            "mode": self.mode,
            "checksum": self.model.checksum(),
            "n_instruments": len(self.model.instruments),
            "point_in_time": True,
            "model_status": self.model.status(),
        }

    def quality(self, bar_dates: dict[str, Iterable[date]]) -> list[Issue]:
        return self.model.check_prices(bar_dates, only_given=True)

    def delisting_for(self, instrument_id: str) -> Delisting | None:
        hits = [d for d in self.model.delistings if d.instrument_id == instrument_id]
        return min(hits, key=lambda d: d.effective_date) if hits else None

    def events_for_positions(self, start: date, end: date) -> list[PositionEvent]:
        """Delistings and corporate actions with effective date in [start, end], for
        positions already open – independent of membership (D-021)."""
        ev: list[PositionEvent] = []
        for d in self.model.delistings:
            if start <= d.effective_date <= end:
                ev.append(self._event(d.instrument_id, "delisting", d.effective_date, d))
        for ca in self.model.corporate_actions:
            if start <= ca.ex_date <= end:
                ev.append(self._event(ca.instrument_id, ca.type, ca.ex_date, ca))
        ev.sort(key=lambda e: (e.effective_date, e.instrument_id, e.kind))
        return ev

    def _event(
        self, iid: str, kind: str, day: date, row: Delisting | CorporateAction
    ) -> PositionEvent:
        # the ticker *the day before* the event: the one a position would be held under
        try:
            ticker = self.ticker_on(iid, day)
        except AmbiguousTicker:
            ticker = None
        if isinstance(row, Delisting):
            return PositionEvent(
                iid, ticker, kind, day, row.category, row.replacement_value_per_share,
                row.observed_at, row.source,
            )  # fmt: skip
        return PositionEvent(iid, ticker, kind, day, None, row.cash, row.observed_at, row.source)
