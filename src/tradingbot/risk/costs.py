"""Cost model: commission, slippage and FX per planned order leg (RISK_MODEL §2.2/§2.8, Q-003).

Everything here is an ASSUMED cost. Nothing is measured: minute bars carry no bid/ask, so
spread and slippage cannot be observed (STEP3_DESIGN §6). The output type is therefore
named ``AssumedCosts``; a future ``MeasuredCosts`` (paper trading, fills from the broker)
must be a separate type and never mixed into the same column.

Slippage is NOT a parameter of this module. The scenario (5/10/20 bps, D-013) lives in
``TriggerParams.slippage_bps`` and is already embedded in ``FillCandidate.fill_price``.
This module *derives* the entry slippage from that fill price (``fill − fill/(1+slip)``)
instead of adding it a second time, and applies the same bps to the planned sell legs.

Planned legs counted in ``max_cost_to_risk`` (RISK_MODEL §2.2 "entry + delvinst + exit"):
  1. ``entry``   buy ``shares``
  2. ``partial`` sell ``floor(shares × partial_fraction)`` – only if ``shares ≥
                 min_shares_for_partial`` (§2.4; otherwise ``partial_skipped`` and the leg
                 does not exist)
  3. ``exit``    sell the remaining shares
Sell legs are priced at the assumed entry fill (exit price unknown before the trade); this
is an assumption about notional, not a forecast.

FX (§2.8): ``per_trade`` charges ``fx_cost_bps`` on every buy (SEK→USD) and sell
(USD→SEK) leg and counts in ``cost_to_risk``; ``usd_balance`` charges it once at deposit
(``deposit_fx_cost``) and nothing per trade. ``FX_RATE_PILOT`` is a constant pilot
parameter – a placeholder, not a historical rate (STEP3_DESIGN §6, Q-003/Q-008).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

COST_VERSION = "1"

FxMode = Literal["usd_balance", "per_trade"]
FX_MODES: tuple[str, ...] = ("usd_balance", "per_trade")

# Pilot constant, SEK per USD. ASSUMPTION / PLACEHOLDER (STEP3_DESIGN §6): not a historical
# rate and not to be described as one. Replaced by a series when Q-003 is resolved.
FX_RATE_PILOT = 10.5


@dataclass(frozen=True)
class CommissionModel:
    """``commission = max(min_commission, per_share × shares)`` per order, in USD."""

    name: str
    per_share: float
    min_commission: float
    verified: str = "partially verified (public price list, Q-003)"

    def commission(self, shares: int) -> float:
        if shares <= 0:
            return 0.0
        return max(self.min_commission, self.per_share * shares)


# RISK_MODEL §3 / Q-003: minimums verified against the public price list 2026-10-25; the
# per-share rates are the public list values. Exchange/clearing fees (Tiered) and the 1 %
# cap (Fixed) are not modelled. Account-specific terms unknown.
IBKR_TIERED = CommissionModel("ibkr_tiered", per_share=0.0035, min_commission=0.35)
IBKR_FIXED = CommissionModel("ibkr_fixed", per_share=0.005, min_commission=1.0)


@dataclass(frozen=True)
class CostParams:
    """Assumed cost scenario. All values are test variables (RISK_MODEL §3), not decisions."""

    commission: CommissionModel = IBKR_TIERED
    fx_mode: str = "per_trade"  # both modes run until Q-008 is resolved
    fx_cost_bps: float = 25.0  # ASSUMPTION per side; Q-003
    fx_rate: float = FX_RATE_PILOT  # SEK per USD, pilot constant
    min_shares_for_partial: int = 3  # §2.4
    partial_fraction: float = 1 / 3  # spec §8

    def __post_init__(self) -> None:
        if self.fx_mode not in FX_MODES:
            raise ValueError(f"fx_mode must be one of {FX_MODES}, got {self.fx_mode!r}")
        if self.fx_rate <= 0:
            raise ValueError("fx_rate must be positive")


@dataclass(frozen=True)
class OrderLeg:
    name: str  # entry / partial / exit
    side: str  # buy / sell
    shares: int
    price: float  # assumed price before slippage for sells; entry fill (incl. slippage) for buy


@dataclass(frozen=True)
class AssumedCosts:
    """Per-leg cost breakdown in USD. ``basis='assumed'`` – never a measurement."""

    legs: tuple[OrderLeg, ...]
    commission: float
    slippage: float
    fx: float
    partial_skipped: bool
    basis: str = "assumed"
    version: str = COST_VERSION
    per_leg: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.commission + self.slippage + self.fx

    @property
    def entry_cash_cost(self) -> float:
        """Cash needed on top of ``shares × fill_price`` for the entry leg (no slippage: it
        is already inside the fill price)."""
        leg = self.per_leg.get("entry", {})
        return leg.get("commission", 0.0) + leg.get("fx", 0.0)


def planned_legs(shares: int, fill_price: float, params: CostParams) -> tuple[OrderLeg, ...]:
    """The order legs counted in max_cost_to_risk; see module docstring."""
    if shares <= 0:
        return ()
    legs = [OrderLeg("entry", "buy", shares, fill_price)]
    partial = 0
    if shares >= params.min_shares_for_partial:
        partial = math.floor(shares * params.partial_fraction)
    if partial > 0:
        legs.append(OrderLeg("partial", "sell", partial, fill_price))
    legs.append(OrderLeg("exit", "sell", shares - partial, fill_price))
    return tuple(legs)


def assumed_costs(
    shares: int, fill_price: float, slippage_bps: float, params: CostParams
) -> AssumedCosts:
    """Costs for the planned legs of one trade of ``shares`` filled at ``fill_price``.

    ``fill_price`` must already include the entry slippage (as ``FillCandidate.fill_price``
    does); ``slippage_bps`` is the same scenario value so the entry slippage can be derived
    without double counting.
    """
    slip = slippage_bps / 10_000
    fx = params.fx_cost_bps / 10_000 if params.fx_mode == "per_trade" else 0.0
    legs = planned_legs(shares, fill_price, params)
    per_leg: dict[str, dict[str, float]] = {}
    commission = slippage = fx_total = 0.0
    for leg in legs:
        notional = leg.shares * leg.price
        c = params.commission.commission(leg.shares)
        if leg.side == "buy":
            # slippage already inside the fill: fill = ref × (1 + slip) -> ref = fill/(1+slip)
            s = leg.shares * (leg.price - leg.price / (1 + slip))
        else:
            s = notional * slip
        f = notional * fx
        per_leg[leg.name] = {"commission": c, "slippage": s, "fx": f, "notional": notional}
        commission += c
        slippage += s
        fx_total += f
    return AssumedCosts(
        legs=legs,
        commission=commission,
        slippage=slippage,
        fx=fx_total,
        partial_skipped=bool(legs) and not any(leg.name == "partial" for leg in legs),
        per_leg=per_leg,
    )


def deposit_fx_cost(amount_sek: float, params: CostParams) -> float:
    """USD cost of converting the start capital once (``usd_balance`` mode only)."""
    if params.fx_mode != "usd_balance":
        return 0.0
    return amount_sek / params.fx_rate * params.fx_cost_bps / 10_000
