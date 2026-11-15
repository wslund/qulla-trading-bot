"""Point-in-time universe data model (UNIVERSE_DESIGN §2, §3, §7; D-020–D-024). U1 step A.

Tables (frozen rows, kept as lists; Parquet on disk under data/universe/<version>/):

  instruments     instrument_id, name, security_type, country, source, observed_at
  listings        instrument_id, exchange, listed_from, listed_to (None = still listed),
                  listed_from_source (register | first_bar), source, observed_at
  ticker_spans    instrument_id, ticker, valid_from, valid_to (None = current), source,
                  observed_at
  identifiers     instrument_id, id_type (figi | cik | cusip | permaticker | …), id_value,
                  valid_from, valid_to, match_confidence (exact | derived | manual |
                  uncertain), source, observed_at
  delistings      instrument_id, effective_date, category (D-020: verified_replacement |
                  last_trade | otc_continuation | verified_worthless | unknown),
                  replacement_value_per_share, source, observed_at, note
  corporate_actions  instrument_id, type, ex_date, ratio, cash, source, observed_at

Time conventions (D-022/D-023):
- Listing membership is inclusive of both ends: member on d iff listed_from <= d <= listed_to
  (listed_to = last trading day on the main list).
- Ticker spans are half-open [valid_from, valid_to): the day a ticker changes, the new one
  is valid and the old one is not.
- ``observed_at`` is OUR knowledge time (UTC date-time when the fact was fetched/recorded),
  ``effective`` dates are market time. ``members(asof, knowledge_at=...)`` can restrict the
  model to facts known by ``knowledge_at``; the default uses every fact and reports how
  many of them were observed after ``asof`` (knowledge gap).

Nothing here decides how a delisted position is valued; the ``category`` just carries the
D-020 classification so the engine (later) can route ``unknown`` to the unresolved group.
Nothing here can rebuild 2016 from the 2026 U0 snapshots: a snapshot taken in 2026 only
says what was listed in 2026 (see ``from_u0_snapshot`` note).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from pathlib import Path

import pandas as pd

UNIVERSE_MODEL_VERSION = "1"

REGISTER, FIRST_BAR = "register", "first_bar"
EXACT, DERIVED, MANUAL, UNCERTAIN = "exact", "derived", "manual", "uncertain"
CONFIDENCES = (EXACT, DERIVED, MANUAL, UNCERTAIN)
VERIFIED_REPLACEMENT = "verified_replacement"
LAST_TRADE = "last_trade"
OTC_CONTINUATION = "otc_continuation"
VERIFIED_WORTHLESS = "verified_worthless"
UNKNOWN = "unknown"
DELIST_CATEGORIES = (
    VERIFIED_REPLACEMENT,
    LAST_TRADE,
    OTC_CONTINUATION,
    VERIFIED_WORTHLESS,
    UNKNOWN,
)
FAIL, WARN = "FAIL", "WARN"


class UniverseModelError(ValueError):
    pass


class SeriesIdentityError(UniverseModelError):
    """A ticker-keyed price series could not be tied to exactly one instrument (D-025)."""


class MixedSeriesError(SeriesIdentityError):
    """Bars of the series belong to more than one instrument (reused ticker)."""


class UnresolvedSeriesError(SeriesIdentityError):
    """No instrument owns the symbol on any bar date (or the given instrument_id is unknown)."""


class AmbiguousOwnerError(SeriesIdentityError):
    """Two instruments claim the symbol on the same date (overlapping ticker spans)."""


IDENTITY_PILOT, IDENTITY_RESOLVED, IDENTITY_GIVEN = "pilot", "date_resolved", "instrument_id"


@dataclass(frozen=True)
class SeriesIdentity:
    """Who a ticker-keyed series belongs to, and how that was established (D-025).

    ``method``: ``instrument_id`` (caller keyed the series by a stable id, verified against
    the model), ``date_resolved`` (every bar date resolved through the ticker spans to the
    same single owner), ``pilot`` (no universe model: symbol used as id, unverified).
    ``owners_seen`` lists every instrument that owned the symbol on some bar date;
    ``unowned_dates`` counts bar dates on which nobody owned it (pre-listing or
    post-delisting bars – left to ``check_prices``, they are not another company's bars)."""

    symbol: str
    instrument_id: str
    method: str
    universe_version: str | None
    first_date: date | None
    last_date: date | None
    owners_seen: tuple[str, ...]
    unowned_dates: int
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["first_date"] = str(self.first_date) if self.first_date else None
        d["last_date"] = str(self.last_date) if self.last_date else None
        d["owners_seen"] = list(self.owners_seen)
        return d


def pilot_identity(
    symbol: str, dates: Iterable[date], instrument_id: str | None = None
) -> SeriesIdentity:
    """Compatibility path (D-025 §b): no universe model, so the symbol itself is the id. The
    pilot's 19 series are hand-checked to be single-issuer; nothing here verifies that."""
    ds = sorted(dates)
    return SeriesIdentity(
        symbol,
        instrument_id or symbol,
        IDENTITY_PILOT,
        None,
        ds[0] if ds else None,
        ds[-1] if ds else None,
        (),
        0,
        "unverified: no universe model (pilot compatibility path)",
    )


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    name: str
    security_type: str  # common_stock | etf | adr | preferred | unknown …
    country: str
    source: str
    observed_at: datetime


@dataclass(frozen=True)
class Listing:
    instrument_id: str
    exchange: str
    listed_from: date
    listed_to: date | None
    listed_from_source: str  # REGISTER | FIRST_BAR
    source: str
    observed_at: datetime

    def covers(self, d: date) -> bool:
        return self.listed_from <= d and (self.listed_to is None or d <= self.listed_to)


@dataclass(frozen=True)
class TickerSpan:
    instrument_id: str
    ticker: str
    valid_from: date
    valid_to: date | None  # half-open
    source: str
    observed_at: datetime

    def covers(self, d: date) -> bool:
        return self.valid_from <= d and (self.valid_to is None or d < self.valid_to)


@dataclass(frozen=True)
class Identifier:
    instrument_id: str
    id_type: str
    id_value: str
    valid_from: date | None
    valid_to: date | None
    match_confidence: str
    source: str
    observed_at: datetime


@dataclass(frozen=True)
class Delisting:
    instrument_id: str
    effective_date: date  # last trading day on the main list
    category: str  # D-020
    replacement_value_per_share: float | None
    source: str
    observed_at: datetime
    note: str = ""


@dataclass(frozen=True)
class CorporateAction:
    instrument_id: str
    type: str  # split | reverse_split | cash_dividend | spin_off | cash_merger | stock_merger …
    ex_date: date
    ratio: float | None
    cash: float | None
    source: str
    observed_at: datetime


@dataclass(frozen=True)
class Issue:
    level: str
    table: str
    message: str


@dataclass
class UniverseModel:
    version: str
    instruments: list[Instrument] = field(default_factory=list)
    listings: list[Listing] = field(default_factory=list)
    ticker_spans: list[TickerSpan] = field(default_factory=list)
    identifiers: list[Identifier] = field(default_factory=list)
    delistings: list[Delisting] = field(default_factory=list)
    corporate_actions: list[CorporateAction] = field(default_factory=list)
    notes: str = ""

    # --- structural validation (UNIVERSE_DESIGN §8 points 1, 5) ---------------------------

    def validate(self) -> list[Issue]:
        out: list[Issue] = []
        ids = [i.instrument_id for i in self.instruments]
        if len(set(ids)) != len(ids):
            out.append(Issue(FAIL, "instruments", "duplicate instrument_id"))
        known = set(ids)
        for name in ("listings", "ticker_spans", "identifiers", "delistings", "corporate_actions"):
            for row in getattr(self, name):
                if row.instrument_id not in known:
                    out.append(Issue(FAIL, name, f"{row.instrument_id}: unknown instrument_id"))
        # listings: ordered, non-overlapping per instrument
        by_inst: dict[str, list[Listing]] = {}
        for ls in self.listings:
            if ls.listed_to is not None and ls.listed_to < ls.listed_from:
                out.append(
                    Issue(FAIL, "listings", f"{ls.instrument_id}: listed_to before listed_from")
                )
            if ls.listed_from_source not in (REGISTER, FIRST_BAR):
                out.append(Issue(FAIL, "listings", f"{ls.instrument_id}: bad listed_from_source"))
            by_inst.setdefault(ls.instrument_id, []).append(ls)
        for iid, rows in by_inst.items():
            rows = sorted(rows, key=lambda r: r.listed_from)
            for a, b in zip(rows, rows[1:], strict=False):
                if a.listed_to is None or b.listed_from <= a.listed_to:
                    out.append(Issue(FAIL, "listings", f"{iid}: overlapping listing intervals"))
        # ticker spans: half-open, no overlap per ticker (reuse must be disjoint)
        by_ticker: dict[str, list[TickerSpan]] = {}
        for ts in self.ticker_spans:
            if ts.valid_to is not None and ts.valid_to <= ts.valid_from:
                out.append(Issue(FAIL, "ticker_spans", f"{ts.ticker}: empty or negative span"))
            by_ticker.setdefault(ts.ticker, []).append(ts)
        for t, rows in by_ticker.items():
            rows = sorted(rows, key=lambda r: r.valid_from)
            for a, b in zip(rows, rows[1:], strict=False):
                if a.valid_to is None or b.valid_from < a.valid_to:
                    out.append(
                        Issue(
                            FAIL,
                            "ticker_spans",
                            f"{t}: overlapping spans ({a.instrument_id}, {b.instrument_id})",
                        )
                    )
        # identifiers
        for idf in self.identifiers:
            if idf.match_confidence not in CONFIDENCES:
                out.append(Issue(FAIL, "identifiers", f"{idf.instrument_id}: bad match_confidence"))
        # delistings: category known, one per listing end, matches listed_to when present
        ends = {
            (ls.instrument_id, ls.listed_to) for ls in self.listings if ls.listed_to is not None
        }
        for dl in self.delistings:
            if dl.category not in DELIST_CATEGORIES:
                out.append(
                    Issue(FAIL, "delistings", f"{dl.instrument_id}: bad category {dl.category!r}")
                )
            if dl.category == VERIFIED_REPLACEMENT and dl.replacement_value_per_share is None:
                out.append(
                    Issue(
                        FAIL,
                        "delistings",
                        f"{dl.instrument_id}: verified_replacement without value",
                    )
                )
            if (dl.instrument_id, dl.effective_date) not in ends:
                out.append(
                    Issue(
                        WARN,
                        "delistings",
                        f"{dl.instrument_id}: no listing ends on {dl.effective_date}",
                    )
                )
        for iid, to in ends:
            if not any(d.instrument_id == iid and d.effective_date == to for d in self.delistings):
                out.append(
                    Issue(WARN, "listings", f"{iid}: listed_to {to} without a delisting event")
                )
        return out

    def status(self) -> str:
        issues = self.validate()
        return FAIL if any(i.level == FAIL for i in issues) else ("WARN" if issues else "OK")

    # --- price reconciliation (UNIVERSE_DESIGN §8 points 2–3) --------------------------------

    def checksum(self) -> str:
        """sha256 over every row (sorted, JSON with str() for dates): identifies the model
        content in run configs independently of where it was stored; stable across a
        Parquet round trip."""
        h = hashlib.sha256()
        for name in self.TABLES:
            rows = (json.dumps(asdict(r), sort_keys=True, default=str) for r in getattr(self, name))
            for rec in sorted(rows):
                h.update(rec.encode())
                h.update(b"\n")
        return h.hexdigest()

    def check_prices(
        self,
        bar_dates: dict[str, Iterable[date]],
        tolerance_sessions: int = 5,
        *,
        only_given: bool = False,
    ) -> list[Issue]:
        """Compare listing intervals with the dates that have price bars, per instrument.
        FAIL: more than ``tolerance`` bars before the first listed_from (a series keyed by a
        reused ticker that carries another instrument's history), more than ``tolerance``
        bars after a listed_to, or no bar at all for a closed listing. ``only_given``
        restricts the check to instruments present in ``bar_dates``.
        WARN: first bar more than 2×tolerance days after a register listed_from (the register
        claims trading the price source does not show), bars in a gap between two listings."""
        out: list[Issue] = []
        by_inst: dict[str, list[Listing]] = {}
        for ls in self.listings:
            if only_given and ls.instrument_id not in bar_dates:
                continue  # judge only the instruments a run actually loaded
            by_inst.setdefault(ls.instrument_id, []).append(ls)
        for iid, lss in by_inst.items():
            lss = sorted(lss, key=lambda r: r.listed_from)
            dates = sorted(bar_dates.get(iid, []))
            closed = all(ls.listed_to is not None for ls in lss)
            if not dates:
                out.append(Issue(FAIL if closed else WARN, "prices", f"{iid}: no price bars"))
                continue
            before = [d for d in dates if d < lss[0].listed_from]
            if len(before) > tolerance_sessions:
                out.append(
                    Issue(
                        FAIL,
                        "prices",
                        f"{iid}: {len(before)} bars before listed_from (reused ticker?)",
                    )
                )
            last_to = lss[-1].listed_to
            after = [d for d in dates if last_to is not None and d > last_to]
            if len(after) > tolerance_sessions:
                out.append(Issue(FAIL, "prices", f"{iid}: {len(after)} bars after listed_to"))
            covered = [d for d in dates if any(ls.covers(d) for ls in lss)]
            if not covered:
                out.append(Issue(FAIL, "prices", f"{iid}: no bars inside listing"))
                continue
            in_gap = len(dates) - len(covered) - len(before) - len(after)
            if in_gap > tolerance_sessions:
                out.append(Issue(WARN, "prices", f"{iid}: {in_gap} bars between listings"))
            first = lss[0]
            first_offset = (covered[0] - first.listed_from).days
            if first.listed_from_source == REGISTER and first_offset > tolerance_sessions * 2:
                out.append(
                    Issue(
                        WARN,
                        "prices",
                        f"{iid}: first bar {first_offset} days after listed_from",
                    )
                )
        return out

    # --- series identity (D-025) --------------------------------------------------------------

    def owners_of(self, symbol: str, dates: Iterable[date]) -> dict[date, str | None]:
        """instrument_id that owned ``symbol`` on each date (None = nobody). Two owners on one
        date → AmbiguousOwnerError, never a choice."""
        spans = [s for s in self.ticker_spans if s.ticker == symbol]
        out: dict[date, str | None] = {}
        for d in dates:
            hits = {s.instrument_id for s in spans if s.covers(d)}
            if len(hits) > 1:
                raise AmbiguousOwnerError(f"{symbol}: {sorted(hits)} both own the ticker on {d}")
            out[d] = next(iter(hits)) if hits else None
        return out

    def identify_series(
        self, symbol: str, dates: Iterable[date], *, instrument_id: str | None = None
    ) -> SeriesIdentity:
        """Tie a ticker-keyed series to exactly one instrument, or refuse (D-025).

        Without ``instrument_id`` every bar date is resolved through the ticker spans; the
        series is accepted only if all owned dates point to the same instrument
        (``date_resolved``). With ``instrument_id`` the series is accepted only if no bar
        date is owned by another instrument (``instrument_id``). Bars nobody owns are
        allowed and counted – they are the same issuer's pre-listing/post-delisting bars as
        far as the register knows, and ``check_prices`` judges them."""
        ds = sorted(set(dates))
        if not ds:
            raise UnresolvedSeriesError(f"{symbol}: empty series")
        owners = self.owners_of(symbol, ds)
        counts: dict[str, int] = {}
        for o in owners.values():
            if o is not None:
                counts[o] = counts.get(o, 0) + 1
        unowned = sum(1 for o in owners.values() if o is None)
        seen = tuple(sorted(counts))
        known = {i.instrument_id for i in self.instruments}
        if instrument_id is not None:
            if instrument_id not in known:
                raise UnresolvedSeriesError(
                    f"{symbol}: instrument_id {instrument_id!r} not in universe {self.version}"
                )
            others = {o: n for o, n in counts.items() if o != instrument_id}
            if others:
                raise MixedSeriesError(
                    f"{symbol}: declared {instrument_id} but {sum(others.values())} bar(s) are "
                    f"owned by {sorted(others)} – another issuer's history in the series"
                )
            method, iid = IDENTITY_GIVEN, instrument_id
        else:
            if len(seen) > 1:
                raise MixedSeriesError(
                    f"{symbol}: bars owned by {len(seen)} instruments "
                    f"{dict(sorted(counts.items()))} – split the series per instrument or key "
                    "it by instrument_id"
                )
            if not seen:
                raise UnresolvedSeriesError(
                    f"{symbol}: no instrument owns this ticker on any of the {len(ds)} bar dates "
                    f"in universe {self.version}; pass instrument_id explicitly"
                )
            method, iid = IDENTITY_RESOLVED, seen[0]
        note = f"{unowned} bar(s) without an owner (see check_prices)" if unowned else ""
        return SeriesIdentity(symbol, iid, method, self.version, ds[0], ds[-1], seen, unowned, note)

    TABLES = (
        "instruments",
        "listings",
        "ticker_spans",
        "identifiers",
        "delistings",
        "corporate_actions",
    )

    def write(self, root: Path) -> Path:
        d = Path(root) / "universe" / self.version
        if d.exists():
            raise UniverseModelError(f"{d} exists; universe versions are immutable")
        d.mkdir(parents=True)
        for name in self.TABLES:
            rows = getattr(self, name)
            pd.DataFrame([asdict(r) for r in rows]).to_parquet(d / f"{name}.parquet", index=False)
        (d / "manifest.json").write_text(
            json.dumps(
                {"universe_version": self.version, "model_version": UNIVERSE_MODEL_VERSION,
                 "rows": {n: len(getattr(self, n)) for n in self.TABLES}, "status": self.status(),
                 "checksum": self.checksum(),
                 "notes": self.notes},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )  # fmt: skip
        return d

    @classmethod
    def read(cls, root: Path, version: str) -> UniverseModel:
        d = Path(root) / "universe" / version
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        types = {
            "instruments": Instrument,
            "listings": Listing,
            "ticker_spans": TickerSpan,
            "identifiers": Identifier,
            "delistings": Delisting,
            "corporate_actions": CorporateAction,
        }
        model = cls(version=man["universe_version"], notes=man.get("notes", ""))
        for name, typ in types.items():
            frame = pd.read_parquet(d / f"{name}.parquet")
            setattr(model, name, [typ(**_coerce(typ, rec)) for rec in frame.to_dict("records")])
        return model


def _coerce(typ, rec: dict) -> dict:
    out = {}
    for f in fields(typ):
        v = rec.get(f.name)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out[f.name] = None
        elif f.type in ("date", "date | None") and not isinstance(v, date):
            out[f.name] = pd.Timestamp(v).date()
        elif f.type == "datetime" and not isinstance(v, datetime):
            out[f.name] = pd.Timestamp(v).to_pydatetime()
        else:
            out[f.name] = v
    return out


def from_u0_snapshot(*_args, **_kwargs):
    """Not implemented on purpose (U1 step B). A U0 snapshot taken on day S says which
    instruments were listed on S – it cannot say anything about 2016–2025. Building a
    2016+ point-in-time universe needs a historical register (UNIVERSE_DESIGN §5)."""
    raise NotImplementedError("U0 snapshots describe the day they were taken, not the past")
