"""U2B-FREE-3A: link instrument observations from several Massive inventories (different
``effective_date``) into per-instrument timelines – without inventing history.

A ``MassiveInventory`` (U2B-FREE-2B) holds point facts: "on effective_date D ticker X belonged
to instrument I, active/inactive, delisted_date d". This module puts the facts for one
instrument next to each other and reports what they *jointly* say and what they do not:

- **Linking** happens only through the FIGI-based ``instrument_id`` (``FIGI-…`` exact,
  ``CFIGI-…`` derived). Surrogate ids (``MSV-…``) carry their effective_date and are never
  linked across dates. A timeline is ``exact`` only when every observation is exact and no
  other identifier contradicts the link (CIK present on both sides but different, or
  composite_figi present on both sides but different → ``uncertain`` + ``cik_conflict`` /
  ``composite_figi_conflict``). A missing identifier is not a contradiction. CIK is a company
  id (several share classes), so it can never *make* a link. A FIGI that was shared in any
  snapshot (``figi_shared``) keeps the whole timeline ``uncertain``; it is never upgraded.
- **Not observed ≠ delisted.** A snapshot date on which a linked instrument is absent is a
  ``not_observed`` gap; only Massive's ``delisted_date`` (or an inactive observation) says
  anything about delisting. An active→inactive transition between D1 and D2 is
  ``consistent`` when D1 <= delisted_date <= D2, ``inconsistent`` otherwise, ``unknown``
  without a date or when the same id is both active and inactive on one date
  (``mixed_state_same_date``). Inactive-then-active is ``reactivated`` (never one
  continuous listing).
- **Ticker changes** are bounds, not dates: ticker X on D1 and Y on D2 for the same
  instrument gives ``TickerChangeBound(lo_exclusive=D1, hi_inclusive=D2)``.
- **Reused tickers**: the same symbol in two timelines is noted (``ticker_reused``), the
  timelines are never merged; if the earlier owner is not seen inactive by the later date it
  is ``ticker_reused_unverified``.
- **Revisions**: inventories with the same ``effective_date`` but different ``observed_at``
  (``fetched_at``) are revisions of one list, not two market events. ``link_inventories``
  takes exactly one per date according to ``revision`` ("latest" | "earliest") and lists the
  rest in the report; ``revision_report`` measures what changed between two revisions.
- Nothing is adjusted: conflicting facts are kept as observed and flagged. ``observed_at``
  stays the snapshot's ``fetched_at``; ``point_in_time`` is False throughout (D-023, D-026).
- Timelines say nothing about the days between observations. Turning them into
  ``UniverseModel`` rows needs a declared policy – U2B-FREE-3B, not here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from tradingbot.data.universe_massive import (
    DERIVED,
    EXACT,
    SRC_SURROGATE,
    UNCERTAIN,
    InstrumentObservation,
    MassiveInventory,
)

TIMELINE_VERSION = "1"
LATEST, EARLIEST = "latest", "earliest"
CONSISTENT, INCONSISTENT, UNKNOWN = "consistent", "inconsistent", "unknown"
NOT_OBSERVED = "not_observed"


class TimelineError(ValueError):
    pass


@dataclass(frozen=True)
class TickerChangeBound:
    """The ticker changed somewhere in (lo_exclusive, hi_inclusive]; the exact day is unknown."""

    instrument_id: str
    from_ticker: str
    to_ticker: str
    lo_exclusive: date
    hi_inclusive: date


@dataclass(frozen=True)
class DelistingFact:
    instrument_id: str
    delisted_date: date | None
    source: str | None  # e.g. massive:delisted_utc
    consistency: str  # consistent | inconsistent | unknown
    last_active_observed: date | None
    first_inactive_observed: date | None
    note: str = ""


@dataclass
class InstrumentTimeline:
    instrument_id: str
    link_confidence: str  # exact | derived | uncertain
    observations: list[InstrumentObservation]  # sorted (effective_date, ticker)
    dates_observed: tuple[date, ...]
    dates_not_observed: tuple[date, ...]  # snapshot dates where the id is absent
    ticker_change_bounds: list[TickerChangeBound] = field(default_factory=list)
    delisting: DelistingFact | None = None
    flags: tuple[str, ...] = ()
    observed_at_first: datetime | None = None
    observed_at_last: datetime | None = None
    point_in_time: bool = False

    @property
    def linked(self) -> bool:
        return len(self.dates_observed) >= 2

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted({o.ticker for o in self.observations}))

    def state_on(self, d: date) -> str:
        """'active' | 'inactive' | 'ambiguous' | 'not_observed' for a snapshot date d."""
        obs = [o for o in self.observations if o.effective_date == d]
        if not obs:
            return NOT_OBSERVED
        states = {o.active for o in obs}
        if len(states) > 1:
            return "ambiguous"
        return "active" if states.pop() else "inactive"


@dataclass
class LinkReport:
    effective_dates: list[str]
    observed_at: dict[str, str]  # effective_date -> observed_at used
    revisions_skipped: dict[str, list[str]]  # effective_date -> observed_at values not used
    revision_policy: str
    timelines: int = 0
    by_confidence: dict[str, int] = field(default_factory=dict)
    linked: int = 0  # observed on >= 2 dates
    linked_by_confidence: dict[str, int] = field(default_factory=dict)
    single_observation: int = 0
    unlinked_surrogates: int = 0
    not_observed_gaps: int = 0
    ticker_change_bounds: int = 0
    delisting_consistent: int = 0
    delisting_inconsistent: int = 0
    delisting_unknown: int = 0
    reactivated: int = 0
    cik_conflicts: int = 0
    composite_figi_conflicts: int = 0
    figi_shared: int = 0
    attribute_changed: int = 0
    tickers_reused: int = 0
    tickers_reused_unverified: int = 0
    point_in_time: bool = False
    timeline_version: str = TIMELINE_VERSION
    note: str = (
        "observations are point facts per effective_date; nothing is asserted about the days "
        "between them; not_observed is not delisted; observed_at = snapshot fetched_at"
    )

    def to_dict(self) -> dict:
        return asdict(self)


# --- revision selection --------------------------------------------------------------------------


def select_revisions(
    inventories: list[MassiveInventory], revision: str = LATEST
) -> tuple[list[MassiveInventory], dict[str, list[str]]]:
    """One inventory per effective_date. Same effective_date + same observed_at is a
    duplicate and an error; different observed_at are revisions, picked by policy."""
    if revision not in (LATEST, EARLIEST):
        raise TimelineError(f"unknown revision policy {revision!r}")
    by_date: dict[date, list[MassiveInventory]] = defaultdict(list)
    for inv in inventories:
        if inv.point_in_time:
            raise TimelineError(f"{inv.version}: point_in_time inventories are not expected here")
        by_date[inv.effective_date].append(inv)
    chosen: list[MassiveInventory] = []
    skipped: dict[str, list[str]] = {}
    for d in sorted(by_date):
        revs = sorted(by_date[d], key=lambda i: i.observed_at)
        stamps = [i.observed_at for i in revs]
        if len(set(stamps)) != len(stamps):
            raise TimelineError(f"{d}: two inventories with the same observed_at")
        pick = revs[-1] if revision == LATEST else revs[0]
        chosen.append(pick)
        rest = [i.observed_at.isoformat() for i in revs if i is not pick]
        if rest:
            skipped[d.isoformat()] = rest
    return chosen, skipped


# --- linking ---------------------------------------------------------------------------------


def link_inventories(
    inventories: list[MassiveInventory], *, revision: str = LATEST
) -> tuple[list[InstrumentTimeline], LinkReport]:
    if not inventories:
        raise TimelineError("no inventories")
    chosen, skipped = select_revisions(inventories, revision)
    dates = [inv.effective_date for inv in chosen]
    rep = LinkReport(
        [d.isoformat() for d in dates],
        {inv.effective_date.isoformat(): inv.observed_at.isoformat() for inv in chosen},
        skipped,
        revision,
    )
    groups: dict[str, list[InstrumentObservation]] = defaultdict(list)
    for inv in chosen:
        for o in inv.observations:
            groups[o.instrument_id].append(o)
    timelines = [_build_timeline(iid, obs, dates) for iid, obs in sorted(groups.items())]
    _flag_reused_tickers(timelines)
    _fill_report(rep, timelines)
    return timelines, rep


def _build_timeline(
    iid: str, obs: list[InstrumentObservation], dates: list[date]
) -> InstrumentTimeline:
    obs = sorted(obs, key=lambda o: (o.effective_date, o.ticker, o.observed_at))
    flags: list[str] = []
    seen = tuple(sorted({o.effective_date for o in obs}))
    not_seen = tuple(d for d in dates if d not in seen)
    # --- confidence: never above the weakest observation, never above "uncertain" when ids clash
    confs = {o.match_confidence for o in obs}
    if obs[0].id_source == SRC_SURROGATE:
        conf = UNCERTAIN
        flags.append("surrogate_unlinked")
    elif UNCERTAIN in confs:
        conf = UNCERTAIN
    elif DERIVED in confs:
        conf = DERIVED
    else:
        conf = EXACT
    if any("figi_shared" in o.flags for o in obs):
        flags.append("figi_shared")
        conf = UNCERTAIN
    ciks = {o.cik for o in obs if o.cik}
    if len(ciks) > 1:
        flags.append("cik_conflict")
        conf = UNCERTAIN
    cfs = {o.composite_figi for o in obs if o.composite_figi}
    if len(cfs) > 1:
        flags.append("composite_figi_conflict")
        conf = UNCERTAIN
    if any(o.ambiguous for o in obs):
        flags.append("ambiguous_ticker")
    # --- per-date state and attribute changes
    by_date: dict[date, list[InstrumentObservation]] = defaultdict(list)
    for o in obs:
        by_date[o.effective_date].append(o)
    attrs = {(o.type, o.primary_exchange) for o in obs}
    if len(attrs) > 1:
        flags.append("attribute_changed")
    # --- ticker change bounds between consecutive single-ticker observations
    bounds: list[TickerChangeBound] = []
    prev_d: date | None = None
    for d in seen:
        tickers_d = {o.ticker for o in by_date[d]}
        if prev_d is not None:
            tickers_prev = {o.ticker for o in by_date[prev_d]}
            if len(tickers_d) == 1 and len(tickers_prev) == 1 and tickers_d != tickers_prev:
                bounds.append(
                    TickerChangeBound(
                        iid, next(iter(tickers_prev)), next(iter(tickers_d)), prev_d, d
                    )
                )
            elif tickers_d != tickers_prev:
                flags.append("ticker_set_changed")  # shared/ambiguous: no single bound
        prev_d = d
    # --- delisting facts from state transitions (never from absence)
    delisting = _delisting(iid, obs, by_date, seen, flags)
    return InstrumentTimeline(
        iid,
        conf,
        obs,
        seen,
        not_seen,
        bounds,
        delisting,
        tuple(sorted(set(flags))),
        min(o.observed_at for o in obs),
        max(o.observed_at for o in obs),
    )


def _delisting(iid, obs, by_date, seen, flags) -> DelistingFact | None:
    """Delisting facts from *observations*, never from absence. A date on which the same
    instrument id carries both an active and an inactive row (shared FIGI cluster, vendor
    duplicate) is a mixed state: it is flagged and makes the consistency judgement
    ``unknown`` – it is neither resolved nor compared."""
    states = []
    for d in seen:
        st = {o.active for o in by_date[d]}
        states.append((d, None if len(st) > 1 else st.pop()))
    inactive_obs = [o for o in obs if not o.active]
    dl_dates = {o.delisted_date for o in inactive_obs if o.delisted_date}
    if len(dl_dates) > 1:
        flags.append("delisted_date_revised")
    reactivated = any(
        a is False and b is True for (_, a), (_, b) in zip(states, states[1:], strict=False)
    )
    if reactivated:
        flags.append("reactivated")
    if not inactive_obs:
        return None
    mixed = [d for d, a in states if a is None]
    if mixed:
        flags.append("mixed_state_same_date")
    # bounds from the observations themselves (a mixed date still *has* an inactive row)
    active_dates = [o.effective_date for o in obs if o.active]
    inactive_dates = [o.effective_date for o in inactive_obs]
    last_active = max(active_dates) if active_dates else None
    first_inactive = min(inactive_dates)
    dl = inactive_obs[-1]  # the latest inactive observation carries the vendor's current view
    if dl.delisted_date is None:
        return DelistingFact(
            iid, None, None, UNKNOWN, last_active, first_inactive, "no delisted_utc"
        )
    if mixed:
        cons = UNKNOWN
        note = f"active and inactive rows on the same date ({mixed[0]}); not compared"
    elif last_active is not None and last_active < first_inactive:
        ok = last_active <= dl.delisted_date <= first_inactive
        cons = CONSISTENT if ok else INCONSISTENT
        note = "" if ok else f"delisted_date outside [{last_active}, {first_inactive}]"
    elif last_active is not None and last_active > first_inactive:
        cons, note = INCONSISTENT, "active observed after an inactive observation (reactivated)"
    else:  # only inactive observations: the date is the vendor's claim, unverified by us
        cons = CONSISTENT if dl.delisted_date <= first_inactive else INCONSISTENT
        note = "" if cons == CONSISTENT else "delisted_date after the inactive observation"
    if dl.delisted_uncertainty == "after_effective_date":
        cons, note = INCONSISTENT, "delisted_date after effective_date (effective-dated artifact)"
    if cons == INCONSISTENT:
        flags.append("delisting_inconsistent")
    return DelistingFact(
        iid, dl.delisted_date, dl.delisted_source, cons, last_active, first_inactive, note
    )


def _flag_reused_tickers(timelines: list[InstrumentTimeline]) -> None:
    """Same symbol in two timelines: note it; verify that the earlier owner is seen inactive
    by the time the later one is active, else mark unverified. Never merge."""
    owners: dict[str, list[tuple[date, InstrumentTimeline]]] = defaultdict(list)
    for tl in timelines:
        for o in tl.observations:
            if o.active:
                owners[o.ticker].append((o.effective_date, tl))
    for ticker, lst in owners.items():
        tls = {id(tl): tl for _, tl in lst}
        if len(tls) < 2:
            continue
        ordered = sorted(lst, key=lambda x: (x[0], x[1].instrument_id))
        for (_d1, t1), (d2, t2) in zip(ordered, ordered[1:], strict=False):
            if t1 is t2:
                continue
            verified = t1.state_on(d2) == "inactive" or any(
                o.effective_date <= d2 and not o.active and o.ticker == ticker
                for o in t1.observations
            )
            for tl in (t1, t2):
                tl.flags = tuple(
                    sorted(
                        set(tl.flags)
                        | {"ticker_reused"}
                        | (set() if verified else {"ticker_reused_unverified"})
                    )
                )


def _fill_report(rep: LinkReport, timelines: list[InstrumentTimeline]) -> None:
    rep.timelines = len(timelines)
    rep.by_confidence = dict(sorted(Counter(t.link_confidence for t in timelines).items()))
    linked = [t for t in timelines if t.linked]
    rep.linked = len(linked)
    rep.linked_by_confidence = dict(sorted(Counter(t.link_confidence for t in linked).items()))
    rep.single_observation = sum(1 for t in timelines if not t.linked)
    rep.unlinked_surrogates = sum(1 for t in timelines if "surrogate_unlinked" in t.flags)
    rep.not_observed_gaps = sum(len(t.dates_not_observed) for t in timelines)
    rep.ticker_change_bounds = sum(len(t.ticker_change_bounds) for t in timelines)
    for t in timelines:
        if t.delisting is not None:
            key = {
                CONSISTENT: "delisting_consistent",
                INCONSISTENT: "delisting_inconsistent",
                UNKNOWN: "delisting_unknown",
            }[t.delisting.consistency]
            setattr(rep, key, getattr(rep, key) + 1)
    rep.reactivated = sum(1 for t in timelines if "reactivated" in t.flags)
    rep.cik_conflicts = sum(1 for t in timelines if "cik_conflict" in t.flags)
    rep.composite_figi_conflicts = sum(1 for t in timelines if "composite_figi_conflict" in t.flags)
    rep.figi_shared = sum(1 for t in timelines if "figi_shared" in t.flags)
    rep.attribute_changed = sum(1 for t in timelines if "attribute_changed" in t.flags)
    reused = {tk for t in timelines if "ticker_reused" in t.flags for tk in t.tickers}
    rep.tickers_reused = len(reused)
    rep.tickers_reused_unverified = sum(
        1 for t in timelines if "ticker_reused_unverified" in t.flags
    )


# --- revisions -------------------------------------------------------------------------------

REVISION_FIELDS = (
    "active", "delisted_date", "type", "primary_exchange", "name", "cik", "composite_figi",
    "share_class_figi", "match_confidence",
)  # fmt: skip


@dataclass
class RevisionReport:
    effective_date: str
    observed_at_a: str
    observed_at_b: str
    rows_a: int
    rows_b: int
    unchanged: int = 0
    added: int = 0  # keys only in b
    removed: int = 0  # keys only in a
    changed: int = 0  # same key, some field differs
    changed_by_field: dict[str, int] = field(default_factory=dict)
    identity_changed: int = (
        0  # same ticker+state, different instrument_id (counted in added/removed too)
    )
    note: str = (
        "same effective_date fetched twice: differences are vendor revisions, not market events"
    )

    def to_dict(self) -> dict:
        return asdict(self)


def _rev_key(o: InstrumentObservation) -> tuple:
    # FIGI-based ids are stable across fetches; surrogates are keyed by ticker + state instead
    if o.id_source == SRC_SURROGATE:
        return ("surrogate", o.ticker, o.active)
    return ("id", o.instrument_id, o.ticker)


def revision_report(a: MassiveInventory, b: MassiveInventory) -> RevisionReport:
    if a.effective_date != b.effective_date:
        raise TimelineError("revision_report compares two fetches of the same effective_date")
    if a.observed_at == b.observed_at:
        raise TimelineError(
            "two inventories with the same observed_at are duplicates, not revisions"
        )
    if a.observed_at > b.observed_at:
        a, b = b, a
    rep = RevisionReport(
        a.effective_date.isoformat(), a.observed_at.isoformat(), b.observed_at.isoformat(),
        len(a.observations), len(b.observations),
    )  # fmt: skip
    ka = {_rev_key(o): o for o in a.observations}
    kb = {_rev_key(o): o for o in b.observations}
    changed: Counter[str] = Counter()
    for k in sorted(set(ka) & set(kb)):
        diff = [f for f in REVISION_FIELDS if getattr(ka[k], f) != getattr(kb[k], f)]
        if diff:
            rep.changed += 1
            changed.update(diff)
        else:
            rep.unchanged += 1
    rep.changed_by_field = dict(sorted(changed.items()))
    only_a, only_b = set(ka) - set(kb), set(kb) - set(ka)
    rep.removed, rep.added = len(only_a), len(only_b)
    ta = {(ka[k].ticker, ka[k].active) for k in only_a}
    tb = {(kb[k].ticker, kb[k].active) for k in only_b}
    rep.identity_changed = len(ta & tb)
    return rep
