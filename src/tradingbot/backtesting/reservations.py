"""PRE_OPEN reservations: plan each prioritized order with 3d, check P2 limits against a
snapshot that already contains earlier reservations, reserve in the P1 account (D-016; P3).

For one session T and the prioritized candidates (priority.py):
  1. plan      ``OrderPlan`` on the daily formulas (level × (1+slip), max(low[T−1], ADR cap))
               – also for ORB (D-016: reservation on the daily plan); ``position_size`` (3d)
               gives shares_ordered; ``assumed_costs`` on the plan gives the pre-order
               cost_to_risk (RISK_MODEL §2.1.6 "före order"). Rejections: zero_shares,
               too_small, cost_to_risk (stage "sizing").
  2. limits    ``allow_entry`` (P2) on the snapshot; after each accepted order the snapshot
               is advanced with ``with_order`` so later candidates see the earlier
               reservations (shared budget). Rejections: halted/loss limits, max_positions,
               open_risk, gross_exposure, insufficient_cash, sector_limit (stage "limits").
  3. reserve   ``Account.reserve`` – cash is earmarked, nothing is bought, equity unchanged.
Everything read here is known at the close of T−1 (levels, features, account marks) plus
``split_ratio[T]`` through the levels (STEP3_DESIGN §8.3). Prices of day T never enter.

At fill time (P4) the overrun of a reservation is classified with ``classify_overrun``:
``reservation_exceeded`` when the capital would still fit in available cash (the reservation
rule's own cost), ``insufficient_capital`` when it would not. Unfilled reservations are
released in CLOSE with ``release_unfilled``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from tradingbot.backtesting.account import Account, AccountError, Reservation, leg_costs
from tradingbot.backtesting.priority import Prioritized
from tradingbot.risk.costs import CostParams, assumed_costs
from tradingbot.risk.limits import (
    EntryRequest,
    LimitDecision,
    LimitParams,
    PortfolioSnapshot,
    allow_entry,
    snapshot_from_account,
)
from tradingbot.risk.sizing import (
    COST_TO_RISK,
    INSUFFICIENT_CAPITAL,
    OrderPlan,
    RiskParams,
    SizeResult,
    daily_order_plan,
    position_size,
)

RESERVATIONS_VERSION = "1"
RESERVATION_EXCEEDED = "reservation_exceeded"
CAPACITY_REASONS = ("max_positions", "open_risk", "gross_exposure", "insufficient_cash")


@dataclass(frozen=True)
class ReservationPlan:
    key: tuple
    ticker: str
    plan: OrderPlan
    size: SizeResult
    reservation_usd: float  # shares × fill_est + entry commission + entry FX
    entry_commission: float
    entry_fx: float
    cost_to_risk_plan: float
    risk_usd: float  # shares × (fill_est − stop_plan)

    @property
    def shares_ordered(self) -> int:
        return self.size.shares

    def request(self, sector: str | None = None) -> EntryRequest:
        return EntryRequest(
            self.key,
            self.ticker,
            self.shares_ordered,
            self.plan.fill_est,
            self.plan.stop_plan,
            self.reservation_usd,
            sector,
        )


@dataclass(frozen=True)
class ReservationRejection:
    key: tuple
    ticker: str
    date: date
    stage: str  # sizing / limits / account
    reason: str
    rank: int
    detail: str = ""


@dataclass(frozen=True)
class DayReservations:
    date: date
    accepted: tuple[ReservationPlan, ...]
    rejected: tuple[ReservationRejection, ...]
    snapshot_before: PortfolioSnapshot
    snapshot_after: PortfolioSnapshot
    rule: str
    version: str = RESERVATIONS_VERSION

    @property
    def priority_binding(self) -> bool:
        """Did capacity (not state or sizing) stop at least one candidate? (§6 diagnostics)"""
        return any(r.reason in CAPACITY_REASONS for r in self.rejected)

    def rows(self) -> list[dict]:
        out = []
        for i, a in enumerate(self.accepted, 1):
            out.append(
                {
                    "date": self.date,
                    "key": a.key,
                    "ticker": a.ticker,
                    "accepted": True,
                    "reason": "reserved",
                    "shares_ordered": a.shares_ordered,
                    "fill_est": a.plan.fill_est,
                    "stop_plan": a.plan.stop_plan,
                    "reservation_usd": a.reservation_usd,
                    "risk_usd": a.risk_usd,
                    "cost_to_risk_plan": a.cost_to_risk_plan,
                    "order": i,
                }
            )
        for r in self.rejected:
            out.append({"date": self.date, "accepted": False, **asdict(r)})
        return out


def plan_reservation(
    signal, equity_usd: float, risk: RiskParams, costs: CostParams, plan: OrderPlan | None = None
) -> ReservationPlan | str:
    """Size the order on the plan and price its reservation. Returns a rejection code string
    (zero_shares / too_small / cost_to_risk) instead of a plan when the order is not placed."""
    slip = signal.params.slippage_bps / 10_000
    plan = plan or daily_order_plan(signal, slip)
    size = position_size(equity_usd, plan.fill_est, plan.stop_plan, risk)
    if size.shares == 0:
        return size.reason
    ac = assumed_costs(size.shares, plan.fill_est, signal.params.slippage_bps, costs)
    ctr = ac.total / size.risk_amount
    if ctr > risk.max_cost_to_risk:
        return COST_TO_RISK
    commission, fx = leg_costs(size.shares, plan.fill_est, costs)
    return ReservationPlan(
        key=signal.key,
        ticker=signal.ticker,
        plan=plan,
        size=size,
        reservation_usd=size.shares * plan.fill_est + commission + fx,
        entry_commission=commission,
        entry_fx=fx,
        cost_to_risk_plan=ctr,
        risk_usd=size.shares * (plan.fill_est - plan.stop_plan),
    )


def reserve_day(
    account: Account,
    prioritized: list[Prioritized],
    *,
    asof: date,
    on: date,
    state: str,
    risk: RiskParams,
    costs: CostParams,
    limits: LimitParams,
    stops: dict[str, float],
    pending_stops: dict[tuple, float],
    plans: dict[tuple, OrderPlan] | None = None,
) -> DayReservations:
    """PRE_OPEN of session ``on``: snapshot at ``asof`` (= close T−1), then plan → limits →
    reserve for each candidate in priority order. ``pending_stops`` is extended in place
    with the accepted plans' stops so the same dict can feed the next snapshot."""
    plans = plans or {}
    snap = snapshot_from_account(account, stops, pending_stops, state, asof)
    before = snap
    accepted: list[ReservationPlan] = []
    rejected: list[ReservationRejection] = []
    rule = prioritized[0].rule if prioritized else "none"
    for p in prioritized:
        c = p.candidate
        planned = plan_reservation(c.signal, snap.equity_usd, risk, costs, plans.get(c.key))
        if isinstance(planned, str):
            rejected.append(ReservationRejection(c.key, c.ticker, on, "sizing", planned, p.rank))
            continue
        req = planned.request(c.sector)
        decision: LimitDecision = allow_entry(snap, req, limits)
        if not decision.ok:
            rejected.append(
                ReservationRejection(
                    c.key, c.ticker, on, "limits", decision.reason, p.rank, _detail(decision)
                )
            )
            continue
        try:
            account.reserve(
                Reservation(
                    c.key,
                    c.ticker,
                    planned.shares_ordered,
                    planned.plan.fill_est,
                    planned.reservation_usd,
                    on,
                ),
                on,
            )
        except AccountError as e:
            rejected.append(
                ReservationRejection(c.key, c.ticker, on, "account", e.code, p.rank, str(e))
            )
            continue
        snap = snap.with_order(req)
        pending_stops[c.key] = planned.plan.stop_plan
        accepted.append(planned)
    return DayReservations(on, tuple(accepted), tuple(rejected), before, snap, rule)


def _detail(d: LimitDecision) -> str:
    bad = [c for c in d.checks if not c.ok]
    return "; ".join(f"{c.name}={c.value:.4g} limit={c.limit}" for c in bad)


def classify_overrun(account: Account, key: tuple, capital_required: float) -> str | None:
    """At fill time: None if the capital fits the key's reservation; otherwise
    ``reservation_exceeded`` (would fit in available cash) or ``insufficient_capital``."""
    res = account.reservations.get(key)
    if res is None:
        raise AccountError("no_reservation", str(key))
    if capital_required <= res.amount_usd + 1e-9:
        return None
    return (
        RESERVATION_EXCEEDED if account.would_fit(key, capital_required) else INSUFFICIENT_CAPITAL
    )


def release_unfilled(account: Account, on: date, note: str = "unfilled") -> list[tuple]:
    """CLOSE phase: release every remaining reservation (no fill today). Returns the keys."""
    keys = sorted(account.reservations, key=lambda k: (account.reservations[k].ticker, str(k)))
    for k in keys:
        account.release(k, on, note=note)
    return keys
