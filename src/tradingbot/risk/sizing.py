"""Position size and acceptance of an assumed fill (RISK_MODEL §2.1–2.2, STEP3_DESIGN §6).

Pure functions plus one small stateful wrapper (``RiskApprover``) that plugs into 3c's
``Approver`` slot. Single ticker, single trade: portfolio limits across tickers (open
positions, open risk, gross exposure) belong to the backtest engine and are not here.

Two-stage sizing, as the design requires:
  1. ``position_size`` on the ORDER: ``fill_est = level × (1 + slip)`` and the planned stop
     from bars <= T−1. This is the share count the order would have been placed with.
  2. ``assess_fill`` on the ASSUMED FILL (``FillCandidate``): ``actual_risk = shares ×
     (fill − stop)``. If it exceeds ``risk_amount × (1 + risk_overshoot_tolerance)`` the
     count is recomputed downwards – a COUNTERFACTUAL (D-013, ``shares_reduced_at_fill``),
     reported separately from trades that passed with the ordered count. A close-of-day
     reduction (RISK_MODEL §2.1.7) is never used in simulation.

Check order and rejection reasons (the first failing check is reported):
  zero_shares          risk_amount / stop distance rounds down to 0 shares
  too_small            shares × price < min_position_value
  risk_exceeded        actual_risk over tolerance and no reduced count passes too_small
  insufficient_capital cash < shares × fill + entry commission + entry FX; no margin
  cost_to_risk         assumed costs of the planned legs / risk_amount > max_cost_to_risk
``risk_amount`` is ``equity × risk_per_trade`` (planned), the denominator in cost_to_risk.
All parameter values are test variables (RISK_MODEL §3, D-009), not decisions.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from tradingbot.risk.costs import AssumedCosts, CostParams, assumed_costs, deposit_fx_cost
from tradingbot.signals.daily_trigger import FILL_CANDIDATE, TriggerOutcome

SIZING_VERSION = "1"

EQUITY_START_SEK = 10_000.0  # D-008; also run 50 000 / 200 000

ZERO_SHARES = "zero_shares"
TOO_SMALL = "too_small"
RISK_EXCEEDED = "risk_exceeded"
INSUFFICIENT_CAPITAL = "insufficient_capital"
COST_TO_RISK = "cost_to_risk"
APPROVED = "approved"
NOT_A_FILL = "not_a_fill"


@dataclass(frozen=True)
class RiskParams:
    """RISK_MODEL §3 starting values; every one is a test variable."""

    risk_per_trade: float = 0.005  # test 0.005 / 0.01
    max_position_pct: float = 0.25  # test 0.20 / 0.30
    min_position_value_usd: float = 100.0  # test 50 / 100 / 200
    max_cost_to_risk: float = 0.25  # test 0.10 / 0.15 / 0.25
    risk_overshoot_tolerance: float = 0.25  # §2.1.7


@dataclass(frozen=True)
class Account:
    """What sizing sees for one trade. ``equity_usd`` = account value at close T−1
    (§2.1.1); ``cash_usd`` = what the purchase may use, no margin (§2.2)."""

    equity_usd: float
    cash_usd: float
    fx_rate: float  # SEK per USD, pilot constant
    fx_mode: str
    fx_deposit_cost_usd: float = 0.0  # usd_balance: paid once, reported separately


def account_from_sek(
    equity_sek: float, costs: CostParams, cash_sek: float | None = None
) -> Account:
    """Convert SEK capital at the pilot rate. In ``usd_balance`` mode the one-off FX cost is
    deducted from equity and cash here; in ``per_trade`` mode nothing is deducted (each leg
    pays its own FX)."""
    dep = deposit_fx_cost(equity_sek, costs)
    equity = equity_sek / costs.fx_rate - dep
    cash = (equity_sek if cash_sek is None else cash_sek) / costs.fx_rate - dep
    return Account(equity, cash, costs.fx_rate, costs.fx_mode, dep)


@dataclass(frozen=True)
class SizeResult:
    shares: int
    risk_amount: float
    stop_distance: float
    reason: str  # APPROVED / ZERO_SHARES / TOO_SMALL
    capped_by_position_pct: bool


def position_size(
    equity_usd: float, fill_est: float, stop: float, params: RiskParams
) -> SizeResult:
    """RISK_MODEL §2.1.1–2.1.4: whole shares, capped by max_position_pct, floored by
    min_position_value. Raises if ``stop >= fill_est`` (the caller must have refused that)."""
    if fill_est <= 0 or stop >= fill_est:
        raise ValueError(f"stop {stop} must be below fill_est {fill_est}")
    risk_amount = equity_usd * params.risk_per_trade
    dist = fill_est - stop
    shares = math.floor(risk_amount / dist)
    cap = math.floor(equity_usd * params.max_position_pct / fill_est)
    capped = shares > cap
    shares = min(shares, cap)
    if shares <= 0:
        return SizeResult(0, risk_amount, dist, ZERO_SHARES, capped)
    if shares * fill_est < params.min_position_value_usd:
        return SizeResult(0, risk_amount, dist, TOO_SMALL, capped)
    return SizeResult(shares, risk_amount, dist, APPROVED, capped)


@dataclass(frozen=True)
class RiskDecision:
    """Structured approval/rejection of one FillCandidate. Satisfies 3c's ``Approval``."""

    approved: bool
    reason: str
    shares: int  # 0 when rejected
    shares_ordered: int  # count the order would have carried (stage 1)
    shares_reduced_at_fill: bool  # counterfactual (D-013) – report separately
    risk_amount: float  # equity × risk_per_trade
    actual_risk: float  # shares × (fill − stop) on the assumed fill
    costs: AssumedCosts | None
    cost_to_risk: float
    capital_required: float  # shares × fill + entry commission + entry FX
    cash_available: float
    fill_price: float
    stop_price: float
    version: str = SIZING_VERSION

    def trace(self) -> dict[str, object]:
        """Columns for walk_daily's trace."""
        return {
            "shares": self.shares,
            "shares_ordered": self.shares_ordered,
            "shares_reduced_at_fill": self.shares_reduced_at_fill,
            "risk_amount_usd": self.risk_amount,
            "actual_risk_usd": self.actual_risk,
            "cost_usd": self.costs.total if self.costs else float("nan"),
            "cost_to_risk": self.cost_to_risk,
            "capital_required_usd": self.capital_required,
            "partial_skipped": self.costs.partial_skipped if self.costs else None,
            "sizing_version": self.version,
        }


def _reject(
    reason: str,
    shares_ordered: int,
    risk_amount: float,
    actual: float,
    cash: float,
    fill: float,
    stop: float,
    costs: AssumedCosts | None = None,
    ctr: float = float("nan"),
    capital: float = float("nan"),
) -> RiskDecision:
    return RiskDecision(
        False,
        reason,
        0,
        shares_ordered,
        False,
        risk_amount,
        actual,
        costs,
        ctr,
        capital,
        cash,
        fill,
        stop,
    )


def assess_fill(
    outcome: TriggerOutcome, account: Account, risk: RiskParams, costs: CostParams
) -> RiskDecision:
    """Stage 1 + 2 + capital + cost_to_risk on an assumed fill. Pure; see module docstring."""
    sig = outcome.signal
    fc = outcome.fill
    if outcome.code != FILL_CANDIDATE or fc is None:
        return _reject(
            NOT_A_FILL,
            0,
            account.equity_usd * risk.risk_per_trade,
            0.0,
            account.cash_usd,
            float("nan"),
            float("nan"),
        )
    slip_bps = sig.params.slippage_bps
    slip = slip_bps / 10_000
    # Stage 1: the order, sized on the level and the planned stop (bars <= T−1 only).
    fill_est = sig.breakout_level * (1 + slip)
    stop_plan = max(sig.low_prev, fill_est * (1 - sig.adr20))
    order = position_size(account.equity_usd, fill_est, stop_plan, risk)
    fill, stop = fc.fill_price, fc.stop_price
    if order.shares == 0:
        return _reject(order.reason, 0, order.risk_amount, 0.0, account.cash_usd, fill, stop)
    # Stage 2: the assumed fill. Stop is never moved to rescue the risk.
    shares = order.shares
    actual = shares * (fill - stop)
    reduced = False
    if actual > order.risk_amount * (1 + risk.risk_overshoot_tolerance):
        shares = math.floor(order.risk_amount / (fill - stop))
        reduced = True
        if shares <= 0 or shares * fill < risk.min_position_value_usd:
            return _reject(
                RISK_EXCEEDED, order.shares, order.risk_amount, actual, account.cash_usd, fill, stop
            )
        actual = shares * (fill - stop)
    ac = assumed_costs(shares, fill, slip_bps, costs)
    capital = shares * fill + ac.entry_cash_cost
    ctr = ac.total / order.risk_amount
    if capital > account.cash_usd:
        return _reject(
            INSUFFICIENT_CAPITAL,
            order.shares,
            order.risk_amount,
            actual,
            account.cash_usd,
            fill,
            stop,
            ac,
            ctr,
            capital,
        )
    if ctr > risk.max_cost_to_risk:
        return _reject(
            COST_TO_RISK,
            order.shares,
            order.risk_amount,
            actual,
            account.cash_usd,
            fill,
            stop,
            ac,
            ctr,
            capital,
        )
    return RiskDecision(
        True,
        APPROVED,
        shares,
        order.shares,
        reduced,
        order.risk_amount,
        actual,
        ac,
        ctr,
        capital,
        account.cash_usd,
        fill,
        stop,
    )


@dataclass
class RiskApprover:
    """3c ``Approver``: ``assess_fill`` with a fixed account and parameters. Keeps every
    decision for reporting (approved, rejected per reason, counterfactual reductions).

    The account is a constant per run: no equity update after fills (that is the backtest
    engine's job, 3f+). Approving through this object is the only way to get a
    ``RiskDecision`` with ``approved=True`` from real inputs."""

    account: Account
    risk: RiskParams = field(default_factory=RiskParams)
    costs: CostParams = field(default_factory=CostParams)
    decisions: list[RiskDecision] = field(default_factory=list)

    def __call__(self, outcome: TriggerOutcome) -> RiskDecision:
        d = assess_fill(outcome, self.account, self.risk, self.costs)
        self.decisions.append(d)
        return d

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.decisions:
            out[d.reason] = out.get(d.reason, 0) + 1
        out["shares_reduced_at_fill"] = sum(d.shares_reduced_at_fill for d in self.decisions)
        return out

    def params_dict(self) -> dict:
        return {
            "risk": asdict(self.risk),
            "costs": asdict(self.costs),
            "account": asdict(self.account),
        }
