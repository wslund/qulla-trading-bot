"""U2B-FREE-2B: safe, offline import of a Massive ``massive_tickers`` snapshot (U2B-FREE-1)
into an **inventory of observations** – deliberately not a ``UniverseModel``.

Why an intermediate format: one effective-dated snapshot says, for ``effective_date`` D,
"ticker X belonged to instrument I and was active/inactive on D" (and, for inactive rows,
Massive's ``delisted_utc``). It says nothing about *when* the listing or the ticker started.
``UniverseModel.Listing``/``TickerSpan`` require ``listed_from``/``valid_from`` and the only
honest values would be invented. So this module produces ``InstrumentObservation`` rows
(no start dates at all) plus an ``ImportReport``; turning observations into verified
``TickerSpan``/``Listing`` rows needs a second source (another effective_date, the first
Alpaca bar, or Massive ``events``) and is U2B-FREE-3. ``to_universe_model`` raises on purpose.

Rules (all offline, all deterministic):
- Only ``complete`` snapshots; every page's sha256 is recomputed, page and row counts are
  checked against the manifest before a single row is interpreted.
- Selection: ``type == "CS"``, ``locale == "us"``, ``primary_exchange`` in MAIN_EXCHANGES.
  Everything else is counted per reason in the report, never silently dropped.
- Identity: ``share_class_figi`` that is unique within the snapshot → ``FIGI-<id>``
  (``exact``); otherwise a unique ``composite_figi`` → ``CFIGI-<id>`` (``derived``);
  otherwise a surrogate ``MSV-<ticker>-<active|inactive>-<effective_date>`` (``uncertain``,
  inventory only – never a verified historical identity). A share_class_figi or
  composite_figi shared by several selected rows is a conflict: all its rows become
  ``uncertain`` with flag ``figi_shared`` (could be a ticker change of one class, could be
  a vendor error – not decided here).
- Tickers: the same symbol in the active and the inactive list is expected reuse (flag
  ``ticker_reused``), two instruments are kept apart. The same symbol twice in the active
  list is ambiguous (flag ``ambiguous_ticker``): both rows stay in the inventory but are
  excluded from ``observed_members``. D-025 makes the same refusal on the price side.
- Times: ``observed_at`` = the manifest's ``fetched_at`` for every row (our knowledge time,
  D-023). ``last_updated_utc`` is Massive's own update time and is carried as a field only.
  ``point_in_time`` is always False (label ``effective_dated_reconstructed``, D-026).
- Delistings: ``delisted_utc`` is kept as ``delisted_date`` with ``delisted_source =
  "massive:delisted_utc"``; a date after ``effective_date`` is flagged
  ``delisted_after_effective`` (effective-dated artifact); an inactive row without a date
  gets ``delisted_uncertainty = "no_date"``. No reason is ever assigned (D-020 category
  stays undecided → ``unknown`` downstream).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from tradingbot.data.snapshots import COMPLETE, MASSIVE_LABEL, MASSIVE_SOURCE

INVENTORY_VERSION = "1"
COMMON_STOCK = "CS"
US = "us"
MAIN_EXCHANGES = (
    "XNAS",
    "XNYS",
    "XASE",
    "ARCX",
    "BATS",
)  # Nasdaq, NYSE, NYSE American, Arca, Cboe BZX
EXACT, DERIVED, UNCERTAIN = "exact", "derived", "uncertain"
SRC_SCF, SRC_CF, SRC_SURROGATE = "share_class_figi", "composite_figi", "surrogate"
DELISTED_SOURCE = "massive:delisted_utc"


class SnapshotImportError(ValueError):
    """The snapshot cannot be trusted (status, hash, counts) – nothing is imported."""


@dataclass(frozen=True)
class LoadedSnapshot:
    directory: str
    effective_date: date
    fetched_at: datetime
    label: str
    active: tuple[dict, ...]
    inactive: tuple[dict, ...]
    sha256_combined: str


def load_snapshot(directory: Path) -> LoadedSnapshot:
    """Read a ``massive_tickers`` snapshot after verifying it: status complete, every file
    present with the manifest's sha256, page count = files, row count per file = manifest,
    list totals = manifest. Raises SnapshotImportError otherwise; rows are returned untouched."""
    d = Path(directory)
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if man.get("source") != MASSIVE_SOURCE:
        raise SnapshotImportError(f"{d}: source {man.get('source')!r} is not {MASSIVE_SOURCE}")
    if man.get("status") != COMPLETE:
        raise SnapshotImportError(
            f"{d}: status {man.get('status')!r}, only complete snapshots import"
        )
    x = man.get("extra") or {}
    if x.get("label") != MASSIVE_LABEL:
        raise SnapshotImportError(f"{d}: label {x.get('label')!r} is not {MASSIVE_LABEL}")
    lists = x.get("lists") or {}
    pages_expected = sum(int(v.get("pages", 0)) for v in lists.values())
    if man.get("pages") != pages_expected or len(man.get("files", [])) != pages_expected:
        raise SnapshotImportError(
            f"{d}: page count mismatch (manifest {man.get('pages')}, lists "
            f"{pages_expected}, files {len(man.get('files', []))})"
        )
    rows: dict[str, list[dict]] = {"active": [], "inactive": []}
    for f in man["files"]:
        p = d / f["name"]
        if not p.exists():
            raise SnapshotImportError(f"{d}: {f['name']} missing")
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != f.get("sha256"):
            raise SnapshotImportError(f"{d}: {f['name']} sha256 mismatch")
        if not f.get("complete"):
            raise SnapshotImportError(f"{d}: {f['name']} not complete")
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SnapshotImportError(f"{d}: {f['name']} unreadable: {type(e).__name__}") from e
        res = obj.get("results")
        if not isinstance(res, list) or len(res) != f.get("record_count"):
            raise SnapshotImportError(f"{d}: {f['name']} row count mismatch")
        which = "active" if f["name"].startswith("active_") else "inactive"
        rows[which].extend(res)
    for which in ("active", "inactive"):
        if len(rows[which]) != int(x.get(f"{which}_count", -1)):
            raise SnapshotImportError(
                f"{d}: {which} total {len(rows[which])} != manifest {x.get(f'{which}_count')}"
            )
        if not lists.get(which, {}).get("complete"):
            raise SnapshotImportError(f"{d}: list {which} not complete")
    return LoadedSnapshot(
        str(d),
        date.fromisoformat(x["effective_date"]),
        datetime.fromisoformat(man["fetched_at"]),
        x["label"],
        tuple(rows["active"]),
        tuple(rows["inactive"]),
        str(man.get("sha256_combined") or _combined(man["files"])),
    )


def _combined(files: list[dict]) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda r: r["name"]):
        h.update((f.get("sha256") or "").encode())
    return h.hexdigest()


# --- selection ----------------------------------------------------------------------------------


def exclusion_reason(r: dict) -> str | None:
    """None when the row passes the strict common-stock filter, else the first reason."""
    if not r.get("ticker"):
        return "no_ticker"
    if not r.get("type"):
        return "type_missing"
    if r.get("type") != COMMON_STOCK:
        return f"type_{r['type']}"
    if r.get("locale") != US:
        return f"locale_{r.get('locale') or 'missing'}"
    if not r.get("primary_exchange"):
        return "exchange_missing"
    if r.get("primary_exchange") not in MAIN_EXCHANGES:
        return f"exchange_{r['primary_exchange']}"
    return None


# --- observations ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentObservation:
    """What one selected Massive row says about ``effective_date`` – and nothing more."""

    effective_date: date
    observed_at: datetime  # = snapshot fetched_at (our knowledge time), never last_updated_utc
    ticker: str
    instrument_id: str
    id_source: str  # share_class_figi | composite_figi | surrogate
    match_confidence: str  # exact | derived | uncertain
    active: bool
    name: str | None
    primary_exchange: str
    type: str
    locale: str
    cik: str | None
    composite_figi: str | None
    share_class_figi: str | None
    delisted_date: date | None
    delisted_source: str | None
    delisted_uncertainty: str | None  # None | no_date | after_effective_date
    last_updated_utc: str | None  # Massive's own update time, carried only
    flags: tuple[str, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return "ambiguous_ticker" in self.flags


@dataclass
class ImportReport:
    effective_date: str
    fetched_at: str
    label: str
    rows_total: int = 0
    rows_active: int = 0
    rows_inactive: int = 0
    included: int = 0
    included_active: int = 0
    included_inactive: int = 0
    excluded: int = 0
    excluded_by_reason: dict[str, int] = field(default_factory=dict)
    identity_by_confidence: dict[str, int] = field(default_factory=dict)
    identity_by_source: dict[str, int] = field(default_factory=dict)
    figi_shared_groups: int = 0
    figi_shared_rows: int = 0
    ambiguous_ticker_rows: int = 0
    reused_tickers: int = 0  # symbol present in both lists (expected reuse)
    unresolved: int = 0  # surrogate ids: inventory only
    delisted_with_date: int = 0
    delisted_without_date: int = 0
    delisted_after_effective_date: int = 0
    observed_members: int = 0
    notes: str = (
        "effective_dated_reconstructed: one snapshot, no start dates; not point-in-time; "
        "no delisting reasons; surrogate ids are not verified identities"
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MassiveInventory:
    version: str
    effective_date: date
    observed_at: datetime
    label: str
    source_snapshot: str
    source_sha256: str
    observations: list[InstrumentObservation]
    report: ImportReport
    point_in_time: bool = False
    inventory_version: str = INVENTORY_VERSION

    def frame(self) -> pd.DataFrame:
        recs = [asdict(o) for o in self.observations]
        for r in recs:
            r["flags"] = ",".join(r["flags"])
        return pd.DataFrame(recs)

    def write(self, root: Path) -> Path:
        d = Path(root) / "universe_obs" / self.version
        if d.exists():
            raise SnapshotImportError(f"{d} exists; inventories are immutable")
        d.mkdir(parents=True)
        self.frame().to_parquet(d / "observations.parquet", index=False)
        man = {
            "inventory_version": self.inventory_version,
            "version": self.version,
            "effective_date": self.effective_date.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "label": self.label,
            "point_in_time": self.point_in_time,
            "source_snapshot": self.source_snapshot,
            "source_sha256": self.source_sha256,
            "report": self.report.to_dict(),
        }
        (d / "manifest.json").write_text(
            json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return d

    @classmethod
    def read(cls, root: Path, version: str) -> MassiveInventory:
        d = Path(root) / "universe_obs" / version
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        df = pd.read_parquet(d / "observations.parquet")
        obs = []
        for rec in df.to_dict("records"):
            rec["flags"] = tuple(f for f in str(rec["flags"]).split(",") if f)
            for k in ("effective_date", "delisted_date"):
                v = rec.get(k)
                rec[k] = (
                    None
                    if v is None or (isinstance(v, float) and pd.isna(v))
                    else pd.Timestamp(v).date()
                )
            rec["observed_at"] = pd.Timestamp(rec["observed_at"]).to_pydatetime()
            for k in (
                "name",
                "cik",
                "composite_figi",
                "share_class_figi",
                "delisted_source",
                "delisted_uncertainty",
                "last_updated_utc",
            ):
                if isinstance(rec.get(k), float) and pd.isna(rec[k]):
                    rec[k] = None
            obs.append(InstrumentObservation(**rec))
        return cls(
            man["version"],
            date.fromisoformat(man["effective_date"]),
            datetime.fromisoformat(man["observed_at"]),
            man["label"],
            man["source_snapshot"],
            man["source_sha256"],
            obs,
            ImportReport(**man["report"]),
            bool(man["point_in_time"]),
            man["inventory_version"],
        )


def build_inventory(snap: LoadedSnapshot, *, version: str | None = None) -> MassiveInventory:
    """Selected rows → observations with identity + flags, and the report. Pure function."""
    rep = ImportReport(snap.effective_date.isoformat(), snap.fetched_at.isoformat(), snap.label)
    rep.rows_active, rep.rows_inactive = len(snap.active), len(snap.inactive)
    rep.rows_total = rep.rows_active + rep.rows_inactive
    selected: list[tuple[dict, bool]] = []
    excl: Counter[str] = Counter()
    for rows, active in ((snap.active, True), (snap.inactive, False)):
        for r in rows:
            why = exclusion_reason(r)
            if why is None:
                selected.append((r, active))
            else:
                excl[why] += 1
    rep.excluded = sum(excl.values())
    rep.excluded_by_reason = dict(sorted(excl.items()))
    # identity: FIGI uniqueness is judged within the selected rows of this snapshot
    scf_count = Counter(r.get("share_class_figi") for r, _ in selected if r.get("share_class_figi"))
    cf_count = Counter(r.get("composite_figi") for r, _ in selected if r.get("composite_figi"))
    active_ticker_count = Counter(r["ticker"] for r, a in selected if a)
    tickers_active = set(active_ticker_count)
    tickers_inactive = {r["ticker"] for r, a in selected if not a}
    reused = tickers_active & tickers_inactive
    shared_groups: set[str] = set()
    obs: list[InstrumentObservation] = []
    for r, active in selected:
        flags: list[str] = []
        scf, cf, t = r.get("share_class_figi"), r.get("composite_figi"), r["ticker"]
        if scf and scf_count[scf] == 1:
            iid, src, conf = f"FIGI-{scf}", SRC_SCF, EXACT
        elif scf and scf_count[scf] > 1:
            iid, src, conf = f"FIGI-{scf}", SRC_SCF, UNCERTAIN
            flags.append("figi_shared")
            shared_groups.add(scf)
        elif cf and cf_count[cf] == 1:
            iid, src, conf = f"CFIGI-{cf}", SRC_CF, DERIVED
        elif cf and cf_count[cf] > 1:
            iid, src, conf = f"CFIGI-{cf}", SRC_CF, UNCERTAIN
            flags.append("figi_shared")
            shared_groups.add(cf)
        else:
            state = "active" if active else "inactive"
            iid, src, conf = (
                f"MSV-{t}-{state}-{snap.effective_date.isoformat()}",
                SRC_SURROGATE,
                UNCERTAIN,
            )
            flags.append("no_figi")
        if active and active_ticker_count[t] > 1:
            flags.append("ambiguous_ticker")
        if t in reused:
            flags.append("ticker_reused")
        if not r.get("cik"):
            flags.append("no_cik")
        dl_date, dl_src, dl_unc = None, None, None
        raw = r.get("delisted_utc")
        if raw:
            try:
                dl_date = date.fromisoformat(str(raw)[:10])
                dl_src = DELISTED_SOURCE
                if dl_date > snap.effective_date:
                    dl_unc = "after_effective_date"
                    flags.append("delisted_after_effective")
            except ValueError:
                dl_unc = "unparseable"
                flags.append("delisted_unparseable")
        elif not active:
            dl_unc = "no_date"
        obs.append(
            InstrumentObservation(
                snap.effective_date,
                snap.fetched_at,
                t,
                iid,
                src,
                conf,
                active,
                r.get("name"),
                r["primary_exchange"],
                r["type"],
                r["locale"],
                r.get("cik"),
                cf,
                scf,
                dl_date,
                dl_src,
                dl_unc,
                r.get("last_updated_utc"),
                tuple(flags),
            )  # fmt: skip
        )
    obs.sort(key=lambda o: (o.ticker, not o.active, o.instrument_id))
    rep.included = len(obs)
    rep.included_active = sum(1 for o in obs if o.active)
    rep.included_inactive = rep.included - rep.included_active
    rep.identity_by_confidence = dict(sorted(Counter(o.match_confidence for o in obs).items()))
    rep.identity_by_source = dict(sorted(Counter(o.id_source for o in obs).items()))
    rep.figi_shared_groups = len(shared_groups)
    rep.figi_shared_rows = sum(1 for o in obs if "figi_shared" in o.flags)
    rep.ambiguous_ticker_rows = sum(1 for o in obs if o.ambiguous)
    rep.reused_tickers = len(reused)
    rep.unresolved = sum(1 for o in obs if o.id_source == SRC_SURROGATE)
    rep.delisted_with_date = sum(1 for o in obs if not o.active and o.delisted_date is not None)
    rep.delisted_without_date = sum(1 for o in obs if not o.active and o.delisted_date is None)
    rep.delisted_after_effective_date = sum(
        1 for o in obs if o.delisted_uncertainty == "after_effective_date"
    )
    stamp = snap.fetched_at.strftime("%Y%m%dT%H%M%SZ")
    inv = MassiveInventory(
        version or f"massive-{snap.effective_date.isoformat()}-{stamp}",
        snap.effective_date,
        snap.fetched_at,
        snap.label,
        snap.directory,
        snap.sha256_combined,
        obs,
        rep,
    )
    rep.observed_members = len(observed_members(inv))
    return inv


def observed_members(
    inv: MassiveInventory, *, confidences: tuple[str, ...] = (EXACT, DERIVED)
) -> set[str]:
    """Instrument ids observed *active on effective_date* with an identity of the given
    confidence and no ticker ambiguity. Surrogate (uncertain) ids are inventory only."""
    return {
        o.instrument_id
        for o in inv.observations
        if o.active and not o.ambiguous and o.match_confidence in confidences
    }


def to_universe_model(inv: MassiveInventory):
    """Not implemented on purpose (U2B-FREE-3). ``Listing.listed_from`` and
    ``TickerSpan.valid_from`` would have to be invented from a single effective-dated
    snapshot. Options, to be decided then: (a) a second effective_date (spans between
    observations), (b) ``listed_from_source = first_bar`` from Alpaca, (c) a small model
    change allowing ``listed_from = None`` with source ``unknown``."""
    raise NotImplementedError("one effective-dated snapshot has no start dates; see U2B-FREE-3")
