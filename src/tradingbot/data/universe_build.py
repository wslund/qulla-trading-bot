"""U2B-FREE-3B-1: build a ``UniverseModel`` from linked instrument timelines – under an
explicit policy, from unambiguous timelines only, with everything else quarantined.

Input: the ``InstrumentTimeline`` list and ``LinkReport`` from ``link_inventories`` (3A).
Output: ``(UniverseModel, BuildReport)`` or ``BuildError``.

What goes into the model (every row carries a source code):
- an ``Instrument`` per *eligible* timeline (``link_confidence`` in ``policy.confidence``,
  no quarantine flag, at least one active observation);
- ``Listing``: ``listed_from`` = first observed active date, ``listed_from_source =
  "observed"`` ("listed no later than" – never the real first trading day);
  ``listed_to`` = the vendor's ``delisted_date`` only when the timeline judged it
  ``consistent``; a ``Delisting`` row with ``category = unknown`` (no reason is known);
- ``TickerSpan`` rows: the ticker observed on each date; a change between D1 and D2 is
  materialised at D2 (``hi_inclusive`` of the bound) and the span's ``source`` names the
  policy and the bound – no day inside (D1, D2] is ever claimed;
- ``Identifier`` rows: share_class_figi (exact) and cik (derived, company level).

Continuity policy for the days *between* observations:
- ``last_observation``: the state seen at the latest observation <= d persists (a step
  function). Nothing is a member before its first observation.
- ``observed_only``: membership only on observed dates (one-day listings and spans); the
  oracle the other policy is tested against.
Snapshots further apart than ``max_gap_days`` make the build refuse unless
``allow_sparse=True``; then ``coverage = "sparse"`` is written into version, notes and report.

Knowledge time: every row's ``observed_at`` is the ``fetched_at`` of the latest observation
the row depends on – never ``effective_date``. A row that bundles facts of two fetches (a
listing opened at D1 and closed by a delisting learned at D2) therefore carries the later
``fetched_at``; ``members(asof, knowledge_at)`` on a fully rebuilt model can only *under*-
report what was known earlier, never leak later facts. The honest object for "what was
known at F1" is the model built from the F1 prefix alone (``compare_models`` makes the
difference between a prefix build and a full rebuild visible). ``knowledge_at`` never turns
reconstructed data into point-in-time data: ``point_in_time`` stays False and the version
prefix ``massive-recon-`` and the label in ``notes`` go with the model everywhere.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from tradingbot.data.universe_model import (
    DERIVED,
    EXACT,
    OBSERVED,
    UNKNOWN,
    Delisting,
    Identifier,
    Instrument,
    Listing,
    TickerSpan,
    UniverseModel,
)
from tradingbot.data.universe_timeline import CONSISTENT, InstrumentTimeline, LinkReport

BUILD_VERSION = "1"
LAST_OBSERVATION, OBSERVED_ONLY = "last_observation", "observed_only"
VERSION_PREFIX = "massive-recon-"
LABEL = "effective_dated_reconstructed"
SRC_OBSERVED = "massive:observed"
SRC_DELISTED = "massive:delisted_utc"
QUARANTINE_FLAGS = (
    "figi_shared",
    "cik_conflict",
    "composite_figi_conflict",
    "ambiguous_ticker",
    "mixed_state_same_date",
    "delisting_inconsistent",
    "delisted_date_revised",
    "reactivated",
    "ticker_reused_unverified",
    "attribute_changed",
    "ticker_set_changed",
    "surrogate_unlinked",
)


class BuildError(ValueError):
    """The model cannot be built safely; nothing is produced."""


@dataclass(frozen=True)
class ModelPolicy:
    continuity: str = LAST_OBSERVATION
    confidence: tuple[str, ...] = (EXACT,)
    listed_from: str = OBSERVED
    max_gap_days: int = 95
    allow_sparse: bool = False

    def validate(self) -> None:
        if self.continuity not in (LAST_OBSERVATION, OBSERVED_ONLY):
            raise BuildError(f"unknown continuity policy {self.continuity!r}")
        if not self.confidence or any(c not in (EXACT, DERIVED) for c in self.confidence):
            raise BuildError("policy.confidence may only contain exact and/or derived")
        if self.listed_from != OBSERVED:
            raise BuildError(
                "only listed_from='observed' is implemented (first_bar is U2B-FREE-3C)"
            )
        if self.max_gap_days < 1:
            raise BuildError("max_gap_days must be >= 1")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BuildReport:
    policy: dict
    effective_dates: list[str]
    observed_at: dict[str, str]
    revisions_skipped: dict[str, list[str]]
    max_gap_days_observed: int
    coverage: str  # dense | sparse | single
    timelines: int = 0
    included: int = 0
    quarantined: int = 0
    quarantined_by_reason: dict[str, int] = field(default_factory=dict)
    quarantined_ids: list[str] = field(default_factory=list)
    skipped_inactive_only: int = 0
    listings: int = 0
    listings_closed_by_delisting: int = 0
    listings_open_past_last_observation: int = 0  # last_observation keeps them members (policy)
    ticker_spans: int = 0
    ticker_change_bounds_materialised: int = 0
    delistings: int = 0
    identifiers: int = 0
    validate_warnings: int = 0
    point_in_time: bool = False
    label: str = LABEL
    build_version: str = BUILD_VERSION
    note: str = (
        "reconstructed vendor lists under an explicit continuity policy; listed_from = first "
        "observed active date (not the first trading day); delisting reasons unknown; "
        "observed_at = snapshot fetched_at; never point-in-time"
    )

    def to_dict(self) -> dict:
        return asdict(self)


def quarantine_reasons(tl: InstrumentTimeline, policy: ModelPolicy) -> list[str]:
    """Why a timeline stays out of the model (empty = eligible). Deterministic order."""
    reasons = [f for f in QUARANTINE_FLAGS if f in tl.flags]
    if tl.link_confidence not in policy.confidence:
        reasons.append(f"confidence_{tl.link_confidence}")
    if tl.delisting is not None and tl.delisting.consistency != CONSISTENT:
        reasons.append(f"delisting_{tl.delisting.consistency}")
    for d in tl.dates_observed:
        if tl.state_on(d) == "ambiguous":
            reasons.append("ambiguous_state")
            break
    return sorted(set(reasons))


def timeline_to_model(
    timelines: list[InstrumentTimeline],
    link: LinkReport,
    policy: ModelPolicy | None = None,
) -> tuple[UniverseModel, BuildReport]:
    pol = policy or ModelPolicy()
    pol.validate()
    dates = [date.fromisoformat(d) for d in link.effective_dates]
    if not dates:
        raise BuildError("no observation dates")
    fetched = {
        date.fromisoformat(k): datetime.fromisoformat(v) for k, v in link.observed_at.items()
    }
    if any(tl.point_in_time for tl in timelines) or link.point_in_time:
        raise BuildError("timelines claim point_in_time; reconstructed input expected")
    for tl in timelines:  # every observed_at must be the fetched_at of its snapshot
        for o in tl.observations:
            if o.observed_at != fetched.get(o.effective_date):
                raise BuildError(
                    f"{tl.instrument_id}: observed_at {o.observed_at} is not the fetched_at of "
                    f"snapshot {o.effective_date}"
                )
            if o.observed_at.date() <= o.effective_date:
                raise BuildError(f"{tl.instrument_id}: observed_at not after effective_date")
    gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
    max_gap = max(gaps) if gaps else 0
    if len(dates) == 1:
        coverage = "single"
    elif max_gap > pol.max_gap_days:
        coverage = "sparse"
    else:
        coverage = "dense"
    if coverage != "dense" and not pol.allow_sparse:
        raise BuildError(
            f"coverage {coverage}: max gap {max_gap} days > {pol.max_gap_days} (or a single "
            "snapshot); pass allow_sparse=True to build a research-only model"
        )
    rep = BuildReport(
        pol.to_dict(), link.effective_dates, dict(link.observed_at), dict(link.revisions_skipped),
        max_gap, coverage,
    )  # fmt: skip
    rep.timelines = len(timelines)
    version = f"{VERSION_PREFIX}{dates[0].isoformat()}-{dates[-1].isoformat()}-{pol.continuity}"
    if coverage != "dense":
        version += f"-{coverage}"
    model = UniverseModel(version=version)
    model.notes = json.dumps(
        {
            "label": LABEL,
            "point_in_time": False,
            "policy": pol.to_dict(),
            "coverage": coverage,
            "max_gap_days_observed": max_gap,
            "effective_dates": link.effective_dates,
            "observed_at": dict(link.observed_at),
            "revisions_skipped": dict(link.revisions_skipped),
            "build_version": BUILD_VERSION,
        },
        sort_keys=True,
    )
    quarantined: Counter[str] = Counter()
    for tl in sorted(timelines, key=lambda t: t.instrument_id):
        reasons = quarantine_reasons(tl, pol)
        if reasons:
            rep.quarantined += 1
            rep.quarantined_ids.append(tl.instrument_id)
            quarantined.update(reasons)
            continue
        if not any(o.active for o in tl.observations):
            rep.skipped_inactive_only += 1  # never seen listed: no membership to express
            continue
        _add_timeline(model, tl, pol, rep)
        rep.included += 1
    rep.quarantined_by_reason = dict(sorted(quarantined.items()))
    issues = model.validate()
    fails = [i for i in issues if i.level == "FAIL"]
    if fails:
        raise BuildError(f"model validation failed: {fails[0]} (+{len(fails) - 1} more)")
    rep.validate_warnings = len(issues)
    rep.listings = len(model.listings)
    rep.ticker_spans = len(model.ticker_spans)
    rep.delistings = len(model.delistings)
    rep.identifiers = len(model.identifiers)
    return model, rep


def _add_timeline(
    model: UniverseModel, tl: InstrumentTimeline, pol: ModelPolicy, rep: BuildReport
) -> None:
    obs = sorted(tl.observations, key=lambda o: o.effective_date)
    latest = obs[-1]
    observed_at_all = max(o.observed_at for o in obs)
    exch = obs[0].primary_exchange  # attribute_changed is quarantined, so one exchange
    model.instruments.append(
        Instrument(
            tl.instrument_id, latest.name or "", "common_stock", "US", SRC_OBSERVED, observed_at_all
        )
    )
    if obs[0].share_class_figi:
        model.identifiers.append(
            Identifier(
                tl.instrument_id,
                "share_class_figi",
                obs[0].share_class_figi,
                None,
                None,
                EXACT,
                SRC_OBSERVED,
                observed_at_all,
            )
        )
    ciks = {o.cik for o in obs if o.cik}
    if len(ciks) == 1:
        model.identifiers.append(
            Identifier(
                tl.instrument_id,
                "cik",
                ciks.pop(),
                None,
                None,
                DERIVED,
                SRC_OBSERVED,
                observed_at_all,
            )
        )
    active = [o for o in obs if o.active]
    dl = tl.delisting
    delisted = dl.delisted_date if (dl is not None and dl.consistency == CONSISTENT) else None
    if pol.continuity == OBSERVED_ONLY:
        for o in active:  # one-day facts, nothing in between
            src = f"policy:{OBSERVED_ONLY};observed={o.effective_date}"
            model.listings.append(
                Listing(
                    tl.instrument_id,
                    exch,
                    o.effective_date,
                    o.effective_date,
                    OBSERVED,
                    src,
                    o.observed_at,
                )
            )
            model.ticker_spans.append(
                TickerSpan(
                    tl.instrument_id,
                    o.ticker,
                    o.effective_date,
                    o.effective_date + timedelta(days=1),
                    src,
                    o.observed_at,
                )
            )
        if delisted is not None:
            model.delistings.append(
                Delisting(
                    tl.instrument_id,
                    delisted,
                    UNKNOWN,
                    None,
                    SRC_DELISTED,
                    latest.observed_at,
                    "reason unknown; date is the vendor's, consistent with observations",
                )
            )
        return
    # last_observation: one listing from the first active observation, closed only by a
    # consistent delisting date; ticker spans change at the observation that shows the new ticker
    first_active = active[0]
    listed_from = first_active.effective_date
    src_listing = f"policy:{LAST_OBSERVATION};observed_from={listed_from}"
    if delisted is not None:
        if delisted < listed_from:
            raise BuildError(
                f"{tl.instrument_id}: delisted {delisted} before first active observation"
            )
        src_listing += f";closed_by={SRC_DELISTED}"
        rep.listings_closed_by_delisting += 1
        model.delistings.append(
            Delisting(
                tl.instrument_id,
                delisted,
                UNKNOWN,
                None,
                SRC_DELISTED,
                latest.observed_at,
                "reason unknown; date is the vendor's, consistent with observations",
            )
        )
    if (
        delisted is None
        and tl.dates_not_observed
        and max(tl.dates_not_observed) > active[-1].effective_date
    ):
        rep.listings_open_past_last_observation += (
            1  # not_observed is not delisted (policy keeps it)
        )
    model.listings.append(
        Listing(
            tl.instrument_id,
            exch,
            listed_from,
            delisted,
            OBSERVED,
            src_listing,
            observed_at_all if delisted else first_active.observed_at,
        )
    )
    end_span = delisted + timedelta(days=1) if delisted is not None else None
    bounds = {b.hi_inclusive: b for b in tl.ticker_change_bounds}
    cur_ticker, cur_from, cur_obs_at = first_active.ticker, listed_from, first_active.observed_at
    cur_src = f"{SRC_OBSERVED};observed_from={listed_from}"
    for o in active[1:]:
        if o.ticker != cur_ticker:
            b = bounds.get(o.effective_date)
            if b is None or b.from_ticker != cur_ticker or b.to_ticker != o.ticker:
                raise BuildError(f"{tl.instrument_id}: ticker change without a matching bound")
            model.ticker_spans.append(
                TickerSpan(
                    tl.instrument_id, cur_ticker, cur_from, o.effective_date, cur_src, cur_obs_at
                )
            )
            rep.ticker_change_bounds_materialised += 1
            cur_ticker, cur_from, cur_obs_at = o.ticker, o.effective_date, o.observed_at
            cur_src = f"policy:{LAST_OBSERVATION};bound=({b.lo_exclusive},{b.hi_inclusive}]"
    if end_span is not None and end_span <= cur_from:
        raise BuildError(f"{tl.instrument_id}: delisting before the last ticker observation")
    model.ticker_spans.append(
        TickerSpan(
            tl.instrument_id,
            cur_ticker,
            cur_from,
            end_span,
            cur_src,
            observed_at_all if end_span else cur_obs_at,
        )
    )


# --- revisions between builds ------------------------------------------------------------------


def compare_models(a: UniverseModel, b: UniverseModel, dates: list[date]) -> dict:
    """Membership (listing coverage) differences per date between two builds – makes the
    effect of a later rebuild on earlier dates visible. Aggregated counts plus ids."""
    out = {"a": a.version, "b": b.version, "per_date": {}}
    for d in dates:
        ma = {ls.instrument_id for ls in a.listings if ls.covers(d)}
        mb = {ls.instrument_id for ls in b.listings if ls.covers(d)}
        out["per_date"][d.isoformat()] = {
            "a": len(ma),
            "b": len(mb),
            "added_in_b": sorted(mb - ma),
            "removed_in_b": sorted(ma - mb),
        }
    out["instruments_only_in_a"] = sorted(
        {i.instrument_id for i in a.instruments} - {i.instrument_id for i in b.instruments}
    )
    out["instruments_only_in_b"] = sorted(
        {i.instrument_id for i in b.instruments} - {i.instrument_id for i in a.instruments}
    )
    return out
