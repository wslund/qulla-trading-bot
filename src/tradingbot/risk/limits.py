"""Portfolio limits and loss/drawdown modes (RISK_MODEL §2.2, §2.5–2.6; PORTFOLIO_DESIGN §5; P2).

Pure functions over a ``PortfolioSnapshot`` – a frozen picture of the account taken at the
previous close (equity, cash, reservations, positions with their *planned* stop risk). The
engine (P4) builds the snapshot in PRE_OPEN from T−1 data and calls ``allow_entry`` for each
candidate order in priority order, feeding accepted orders back into the snapshot with
``snapshot.with_order`` so later orders see earlier reservations. Nothing here reads prices
of day T.

Checks, in this order (first failure is the reason; RISK_MODEL §2.2 table):
  1 state          HALTED / DAILY_LIMIT / WEEKLY_LIMIT from ``LossState`` (decided at close T−1)
  2 max_positions  open + pending + 1 <= max_open_positions
  3 open_risk      Σ planned stop risk (open with current stop, pending with plan) + order
                   <= equity × max_open_risk_pct   – PLANNED risk only (§2.7: a gap through
                   several stops is not bounded by this; that loss shows up in equity and the
                   loss limits)
  4 gross_exposure market value + reserved + order reservation <= equity × max_gross_exposure
  5 cash           reservation <= cash − reserved (no margin)
  6 sector         [H] count in the order's sector < max_per_sector; off when None
``cost_to_risk`` (table row 7) is NOT re-checked here: 3d's ``assess_fill`` computes it on the
order plan and again on the assumed fill, and it would be double work and double logging.

Loss modes (§2.5–2.6) on daily data are measured at CLOSE and apply to the NEXT session's
PRE_OPEN (PORTFOLIO_DESIGN §14 point 4 – no intraday equity exists):
  DAILY_LIMIT   equity[T] <= equity[T−1] × (1 − daily_loss_limit_pct)  → blocks T+1 only
  WEEKLY_LIMIT  equity[T] <= week_anchor × (1 − weekly_loss_limit_pct) → blocks the rest of
                the ISO calendar week; week_anchor = equity at the last close before the week
  HALTED        equity[T] <= hwm × (1 − max_drawdown_halt_pct)          → permanent in
                simulation (no RESUME, RISK_MODEL §2.6); positions keep their own exit rules
``LossState.update_at_close`` takes only the previous state and equity at this close; it is
the only place a mode changes. All parameter values are RISK_MODEL §3 starting values and
test variables (D-009) – nothing here is a new threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

LIMITS_VERSION = "1"

# states
NORMAL = "NORMAL"
DAILY_LIMIT = "DAILY_LIMIT"
WEEKLY_LIMIT = "WEEKLY_LIMIT"
HALTED = "HALTED"

# rejection reasons
ALLOWED = "allowed"
R_HALTED = "halted"
R_DAILY = "loss_limit_daily"
R_WEEKLY = "loss_limit_weekly"
R_MAX_POSITIONS = "max_positions"
R_OPEN_RISK = "open_risk"
R_GROSS = "gross_exposure"
R_CASH = "insufficient_cash"
R_SECTOR = "sector_limit"

TOL = 1e-9


@dataclass(frozen=True)
class LimitParams:
    """RISK_MODEL §3 starting values. ``None`` switches a check off (reported as such)."""

    max_open_positions: int | None = 4  # test 3 / 4 / 6
    max_open_risk_pct: float | None = 0.04  # test 0.03 / 0.05
    max_gross_exposure: float | None = 1.0  # no leverage; not a test variable
    max_per_sector: int | None = None  # [H] 2 when sector data exists (Q-002)
    daily_loss_limit_pct: float | None = 0.03  # on / off
    weekly_loss_limit_pct: float | None = 0.06  # on / off
    max_drawdown_halt_pct: float | None = 0.15  # test 0.10 / 0.15 / 0.20


@dataclass(frozen=True)
class OpenRisk:
    """Planned stop risk of one open position or pending order, in USD and current units."""

    ticker: str
    shares: int
    price: float  # mark (open position) or fill_est (pending)
    stop: float  # current stop (open) or planned stop (pending)
    sector: str | None = None
    pending: bool = False

    @property
    def risk_usd(self) -> float:
        return max(0.0, self.shares * (self.price - self.stop))


@dataclass(frozen=True)
class EntryRequest:
    """What PRE_OPEN knows about a candidate order (all from bars <= T−1 and the plan)."""

    key: tuple
    ticker: str
    shares_ordered: int
    fill_est: float
    stop_plan: float
    reservation_usd: float  # shares × fill_est + entry costs (3d capital_required on the plan)
    sector: str | None = None

    @property
    def risk_usd(self) -> float:
        return self.shares_ordered * (self.fill_est - self.stop_plan)


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Account state at the previous close plus the planned risk of positions and orders."""

    asof: date
    equity_usd: float
    cash_usd: float
    reserved_usd: float
    market_value_usd: float
    positions: tuple[OpenRisk, ...] = ()  # open positions (pending=False)
    pending: tuple[OpenRisk, ...] = ()  # reservations already accepted (pending=True)
    state: str = NORMAL

    @property
    def available_usd(self) -> float:
        return self.cash_usd - self.reserved_usd

    @property
    def open_risk_usd(self) -> float:
        return sum(r.risk_usd for r in self.positions) + sum(r.risk_usd for r in self.pending)

    @property
    def n_positions(self) -> int:
        return len(self.positions)

    @property
    def n_pending(self) -> int:
        return len(self.pending)

    def with_order(self, req: EntryRequest) -> PortfolioSnapshot:
        """The snapshot after accepting ``req`` (reservation made), for the next candidate."""
        pend = OpenRisk(
            req.ticker, req.shares_ordered, req.fill_est, req.stop_plan, req.sector, True
        )
        return replace(
            self,
            reserved_usd=self.reserved_usd + req.reservation_usd,
            pending=(*self.pending, pend),
        )


@dataclass(frozen=True)
class Check:
    name: str
    value: float
    limit: float | None
    ok: bool


@dataclass(frozen=True)
class LimitDecision:
    ok: bool
    reason: str  # ALLOWED or the first failing check's reason code
    checks: tuple[Check, ...]
    key: tuple
    ticker: str
    asof: date
    version: str = LIMITS_VERSION

    def to_row(self) -> dict:
        d = {
            "key": self.key,
            "ticker": self.ticker,
            "asof": self.asof,
            "ok": self.ok,
            "reason": self.reason,
            "limits_version": self.version,
        }
        for c in self.checks:
            d[f"{c.name}_value"] = c.value
            d[f"{c.name}_limit"] = c.limit
        return d


def allow_entry(snap: PortfolioSnapshot, req: EntryRequest, params: LimitParams) -> LimitDecision:
    """RISK_MODEL §2.2 in the order of the module docstring; every check is recorded."""
    checks: list[Check] = []
    reason = ALLOWED

    def fail(code: str) -> None:
        nonlocal reason
        if reason == ALLOWED:
            reason = code

    # 1 state
    state_ok = snap.state == NORMAL
    checks.append(Check("state", 0.0 if state_ok else 1.0, 0.0, state_ok))
    if not state_ok:
        fail({HALTED: R_HALTED, DAILY_LIMIT: R_DAILY, WEEKLY_LIMIT: R_WEEKLY}[snap.state])
    # 2 positions
    n = snap.n_positions + snap.n_pending + 1
    ok = params.max_open_positions is None or n <= params.max_open_positions
    checks.append(Check("positions", n, params.max_open_positions, ok))
    if not ok:
        fail(R_MAX_POSITIONS)
    # 3 planned open risk
    risk = snap.open_risk_usd + req.risk_usd
    lim = None if params.max_open_risk_pct is None else snap.equity_usd * params.max_open_risk_pct
    ok = lim is None or risk <= lim + TOL
    checks.append(Check("open_risk", risk, lim, ok))
    if not ok:
        fail(R_OPEN_RISK)
    # 4 gross exposure
    gross = snap.market_value_usd + snap.reserved_usd + req.reservation_usd
    lim = None if params.max_gross_exposure is None else snap.equity_usd * params.max_gross_exposure
    ok = lim is None or gross <= lim + TOL
    checks.append(Check("gross_exposure", gross, lim, ok))
    if not ok:
        fail(R_GROSS)
    # 5 cash
    ok = req.reservation_usd <= snap.available_usd + TOL
    checks.append(Check("cash", req.reservation_usd, snap.available_usd, ok))
    if not ok:
        fail(R_CASH)
    # 6 sector [H]
    if params.max_per_sector is not None and req.sector is not None:
        same = sum(1 for r in (*snap.positions, *snap.pending) if r.sector == req.sector)
        ok = same + 1 <= params.max_per_sector
        checks.append(Check("sector", same + 1, params.max_per_sector, ok))
        if not ok:
            fail(R_SECTOR)
    else:
        checks.append(Check("sector", 0.0, None, True))
    return LimitDecision(reason == ALLOWED, reason, tuple(checks), req.key, req.ticker, snap.asof)


# --- loss and drawdown modes ----------------------------------------------------------------------


def iso_week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


@dataclass(frozen=True)
class LossState:
    """Mode decided at close T, applied to PRE_OPEN of the next session. Immutable: every
    close returns a new state, so the history is a list of states."""

    state: str = NORMAL
    asof: date | None = None  # close the state was decided on
    prev_close_equity: float | None = None  # daily anchor
    week_anchor_equity: float | None = None  # equity at the last close before this ISO week
    week: tuple[int, int] | None = None
    hwm: float = 0.0
    halted_on: date | None = None
    reason: str = ""
    history: tuple[str, ...] = field(default=())

    @classmethod
    def start(cls, equity: float, on: date) -> LossState:
        """State before the first session: anchors = start equity, HWM = start equity."""
        return cls(NORMAL, on, equity, equity, iso_week(on), equity)

    def update_at_close(self, equity: float, on: date, params: LimitParams) -> LossState:
        """Only inputs: this state, equity at close ``on``. Returns the state for the next
        session. HALTED is sticky; WEEKLY lasts the ISO week; DAILY lasts one session."""
        if self.asof is not None and on <= self.asof:
            raise ValueError(f"close {on} not after previous close {self.asof}")
        hwm = max(self.hwm, equity)
        week = iso_week(on)
        week_anchor = self.week_anchor_equity if week == self.week else self.prev_close_equity
        if week_anchor is None:
            week_anchor = equity
        daily_anchor = self.prev_close_equity if self.prev_close_equity is not None else equity

        state, reason = NORMAL, ""
        if self.state == HALTED:
            state, reason = HALTED, self.reason
        elif (
            params.max_drawdown_halt_pct is not None
            and hwm > 0
            and equity <= hwm * (1 - params.max_drawdown_halt_pct) + TOL
        ):
            state = HALTED
            dd = 1 - equity / hwm
            reason = f"drawdown {dd:.2%} >= {params.max_drawdown_halt_pct:.0%} from hwm {hwm:.2f}"
        elif (
            params.weekly_loss_limit_pct is not None
            and week_anchor > 0
            and equity <= week_anchor * (1 - params.weekly_loss_limit_pct) + TOL
        ) or (self.state == WEEKLY_LIMIT and week == self.week):
            state = WEEKLY_LIMIT
            reason = (
                self.reason
                if self.state == WEEKLY_LIMIT and week == self.week
                else f"wk loss {1 - equity / week_anchor:.2%} >= {params.weekly_loss_limit_pct:.0%}"
            )
        elif (
            params.daily_loss_limit_pct is not None
            and daily_anchor > 0
            and equity <= daily_anchor * (1 - params.daily_loss_limit_pct) + TOL
        ):
            state = DAILY_LIMIT
            reason = (
                f"day loss {1 - equity / daily_anchor:.2%} >= {params.daily_loss_limit_pct:.0%}"
            )
        return LossState(
            state=state,
            asof=on,
            prev_close_equity=equity,
            week_anchor_equity=week_anchor,
            week=week,
            hwm=hwm,
            halted_on=self.halted_on or (on if state == HALTED else None),
            reason=reason,
            history=(*self.history, state),
        )

    @property
    def blocks_entries(self) -> bool:
        return self.state != NORMAL

    def to_row(self) -> dict:
        return {
            "asof": self.asof,
            "state": self.state,
            "hwm": self.hwm,
            "prev_close_equity": self.prev_close_equity,
            "week_anchor_equity": self.week_anchor_equity,
            "halted_on": self.halted_on,
            "reason": self.reason,
        }


# --- adapter from the P1 account (duck-typed: risk does not import backtesting) ----------------


def snapshot_from_account(
    account,
    stops: dict[str, float],
    pending_stops: dict[tuple, float],
    state: str,
    asof: date,
    sectors: dict[str, str] | None = None,
) -> PortfolioSnapshot:
    """Build the PRE_OPEN snapshot from a marked P1 ``Account``: ``stops`` = current stop per
    open ticker (from 3f positions, in current units), ``pending_stops`` = planned stop per
    reservation key. Missing entries are errors – open risk must never be silently zero."""
    sectors = sectors or {}
    if account.last_mark_date != asof:
        raise ValueError(f"account marked {account.last_mark_date}, snapshot asof {asof}")
    positions = []
    for t, h in sorted(account.holdings.items()):
        if t not in stops:
            raise ValueError(f"no current stop for open position {t}")
        positions.append(OpenRisk(t, h.shares, h.mark, stops[t], sectors.get(t)))
    pending = []
    for key, r in sorted(account.reservations.items(), key=lambda kv: (kv[1].ticker, str(kv[0]))):
        if key not in pending_stops:
            raise ValueError(f"no planned stop for reservation {key}")
        pending.append(
            OpenRisk(
                r.ticker,
                r.shares_ordered,
                r.fill_est,
                pending_stops[key],
                sectors.get(r.ticker),
                True,
            )
        )
    return PortfolioSnapshot(
        asof=asof,
        equity_usd=account.equity_usd,
        cash_usd=account.cash_usd,
        reserved_usd=account.reserved_usd,
        market_value_usd=account.market_value,
        positions=tuple(positions),
        pending=tuple(pending),
        state=state,
    )
