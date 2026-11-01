"""One account for the whole portfolio (PORTFOLIO_DESIGN §3–§4, §7.1; P1).

Pure bookkeeping, no strategy. Every change to cash goes through one of five methods –
``reserve``/``release``, ``buy``, ``sell``, ``apply_split`` – each of which records a
``LedgerEvent`` and keeps these identities (checked by ``ledger.check_identities``):

  cash   = start_cash + realized − Σ open cost basis
  equity = cash + Σ shares × mark = start_cash + realized + unrealized

``realized`` is net: it absorbs every commission and FX amount when it is paid (entry costs
included, i.e. a fresh position shows a small realized loss), sale P&L against the cost
basis, and cash in lieu from odd splits. Slippage is inside prices and is tracked only as
information (``slippage_usd``). ``cost basis`` is shares × fill price without costs.

Reservations (D-016 mechanics, decided in PORTFOLIO_DESIGN §4): an entry order may only be
placed against a reservation made in PRE_OPEN; ``buy`` consumes that reservation and refuses
to spend more than it (``AccountError('reservation_exceeded')``). Whether the excess would
have fitted in ``available`` is reported by ``would_fit`` so P3/P4 can distinguish
``reservation_exceeded`` from ``insufficient_capital`` – the account itself never spends
unreserved cash on an entry. Exits never need a reservation.

Currency: all amounts USD. ``fx_mode``/``fx_rate`` come from ``risk.costs.CostParams``;
``usd_balance`` pays the deposit FX once in ``Account.from_sek``; ``per_trade`` pays FX on
each leg via the ``fx`` argument (computed by the caller from CostParams, see
``leg_costs``). ``fx_rate`` is the constant pilot parameter, never a market rate.
``state`` is a placeholder for P2 (NORMAL/HALTED/limits) and is only carried here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

from tradingbot.backtesting.ledger import Ledger, LedgerEvent, phase_name
from tradingbot.risk.costs import CostParams, deposit_fx_cost

ACCOUNT_VERSION = "1"
NORMAL = "NORMAL"


class AccountError(RuntimeError):
    """An invalid balance transition (over-reserve, overspend, oversell, unknown holding)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class Holding:
    ticker: str
    key: tuple
    shares: int
    entry_fill: float  # cost basis per share, current share units
    entry_date: date
    mark: float  # last mark price in current units (entry_fill until first mark)

    @property
    def cost_basis(self) -> float:
        return self.shares * self.entry_fill

    @property
    def market_value(self) -> float:
        return self.shares * self.mark


@dataclass(frozen=True)
class Reservation:
    key: tuple
    ticker: str
    shares_ordered: int
    fill_est: float
    amount_usd: float  # shares × fill_est + entry commission + entry FX
    created: date


def leg_costs(shares: int, price: float, costs: CostParams) -> tuple[float, float]:
    """(commission, fx) in USD for one leg at ``price`` under ``costs`` (per_trade pays FX)."""
    commission = costs.commission.commission(shares)
    fx = shares * price * costs.fx_cost_bps / 10_000 if costs.fx_mode == "per_trade" else 0.0
    return commission, fx


@dataclass
class Account:
    start_cash_usd: float
    cash_usd: float
    fx_mode: str
    fx_rate: float
    deposit_fx_cost_usd: float = 0.0
    holdings: dict[str, Holding] = field(default_factory=dict)
    reservations: dict[tuple, Reservation] = field(default_factory=dict)
    realized_usd: float = 0.0
    commission_usd: float = 0.0
    fx_usd: float = 0.0
    slippage_usd: float = 0.0  # information only (inside prices)
    cash_in_lieu_usd: float = 0.0
    hwm_usd: float = 0.0
    last_mark_date: date | None = None
    state: str = NORMAL  # P2
    ledger: Ledger = field(default_factory=Ledger)
    version: str = ACCOUNT_VERSION

    # --- construction -------------------------------------------------------------------------

    @classmethod
    def from_sek(cls, equity_sek: float, costs: CostParams, on: date) -> Account:
        """SEK start capital at the pilot rate; usd_balance pays the deposit FX once."""
        gross = equity_sek / costs.fx_rate
        dep = deposit_fx_cost(equity_sek, costs)
        acc = cls(gross, gross, costs.fx_mode, costs.fx_rate, dep)
        acc.hwm_usd = gross
        acc._event(
            on,
            "pre_open",
            "deposit",
            None,
            0,
            costs.fx_rate,
            0.0,
            0.0,
            0.0,
            f"{equity_sek} SEK at {costs.fx_rate} SEK/USD (pilot constant)",
        )
        if dep:
            acc.cash_usd -= dep
            acc.realized_usd -= dep
            acc.fx_usd += dep
            acc.hwm_usd = acc.cash_usd
            acc._event(on, "pre_open", "deposit_fx", None, 0, 0.0, -dep, -dep, 0.0, "usd_balance")
        return acc

    # --- views ---------------------------------------------------------------------------------

    @property
    def reserved_usd(self) -> float:
        return sum(r.amount_usd for r in self.reservations.values())

    @property
    def available_usd(self) -> float:
        return self.cash_usd - self.reserved_usd

    @property
    def cost_basis(self) -> float:
        return sum(h.cost_basis for h in self.holdings.values())

    @property
    def market_value(self) -> float:
        return sum(h.market_value for h in self.holdings.values())

    @property
    def unrealized_usd(self) -> float:
        return sum(h.shares * (h.mark - h.entry_fill) for h in self.holdings.values())

    @property
    def equity_usd(self) -> float:
        return self.cash_usd + self.market_value

    def to_sek(self, usd: float) -> float:
        return usd * self.fx_rate

    # --- reservations --------------------------------------------------------------------------

    def reserve(self, res: Reservation, on: date, phase: str = "pre_open") -> LedgerEvent:
        if res.key in self.reservations:
            raise AccountError("duplicate_reservation", str(res.key))
        if res.ticker in self.holdings:
            raise AccountError("position_open", res.ticker)
        if res.amount_usd <= 0 or res.shares_ordered <= 0:
            raise AccountError("invalid_reservation", str(res))
        if res.amount_usd > self.available_usd + 1e-9:
            raise AccountError(
                "insufficient_cash", f"{res.amount_usd:.2f} > available {self.available_usd:.2f}"
            )
        self.reservations[res.key] = res
        return self._event(
            on,
            phase,
            "reserve",
            res.ticker,
            res.shares_ordered,
            res.fill_est,
            0.0,
            0.0,
            res.amount_usd,
        )

    def release(self, key: tuple, on: date, phase: str = "close", note: str = "") -> LedgerEvent:
        res = self.reservations.pop(key, None)
        if res is None:
            raise AccountError("unknown_reservation", str(key))
        return self._event(
            on,
            phase,
            "release",
            res.ticker,
            res.shares_ordered,
            res.fill_est,
            0.0,
            0.0,
            -res.amount_usd,
            note,
        )

    def would_fit(self, key: tuple, amount_usd: float) -> bool:
        """Would ``amount_usd`` fit in available cash if this key's reservation were lifted?
        Lets P3/P4 label an overrun ``reservation_exceeded`` (True) or ``insufficient_capital``."""
        own = self.reservations[key].amount_usd if key in self.reservations else 0.0
        return amount_usd <= self.available_usd + own + 1e-9

    # --- fills ---------------------------------------------------------------------------------

    def buy(
        self,
        key: tuple,
        shares: int,
        price: float,
        commission: float,
        fx: float,
        on: date,
        *,
        slippage: float = 0.0,
        phase: str = "intraday",
    ) -> LedgerEvent:
        """Entry fill against the key's reservation; consumes it. Never spends more than it."""
        res = self.reservations.get(key)
        if res is None:
            raise AccountError("no_reservation", str(key))
        if shares <= 0 or price <= 0:
            raise AccountError("invalid_fill", f"shares={shares} price={price}")
        if res.ticker in self.holdings:
            raise AccountError("position_open", res.ticker)
        amount = shares * price + commission + fx
        if amount > res.amount_usd + 1e-9:
            raise AccountError(
                "reservation_exceeded", f"{amount:.2f} > reserved {res.amount_usd:.2f}"
            )
        del self.reservations[key]
        self.cash_usd -= amount
        self.realized_usd -= commission + fx
        self.commission_usd += commission
        self.fx_usd += fx
        self.slippage_usd += slippage
        self.holdings[res.ticker] = Holding(res.ticker, key, shares, price, on, price)
        return self._event(
            on,
            phase,
            "buy",
            res.ticker,
            shares,
            price,
            -amount,
            -(commission + fx),
            -res.amount_usd,
            f"commission={commission:.4f} fx={fx:.4f}",
        )

    def sell(
        self,
        ticker: str,
        shares: int,
        price: float,
        commission: float,
        fx: float,
        on: date,
        *,
        slippage: float = 0.0,
        phase: str = "intraday",
        reason: str = "",
    ) -> LedgerEvent:
        """Partial or full exit at ``price`` (slippage already inside). Realizes P&L."""
        h = self.holdings.get(ticker)
        if h is None:
            raise AccountError("no_position", ticker)
        if shares <= 0 or shares > h.shares:
            raise AccountError("oversell", f"{shares} of {h.shares} {ticker}")
        if price <= 0:
            raise AccountError("invalid_fill", f"price={price}")
        proceeds = shares * price - commission - fx
        pnl = shares * (price - h.entry_fill) - commission - fx
        self.cash_usd += proceeds
        self.realized_usd += pnl
        self.commission_usd += commission
        self.fx_usd += fx
        self.slippage_usd += slippage
        left = h.shares - shares
        if left:
            self.holdings[ticker] = replace(h, shares=left)
        else:
            del self.holdings[ticker]
        return self._event(
            on,
            phase,
            "sell",
            ticker,
            shares,
            price,
            proceeds,
            pnl,
            0.0,
            f"{reason} commission={commission:.4f} fx={fx:.4f}".strip(),
        )

    # --- corporate actions ---------------------------------------------------------------------

    def apply_split(
        self, ticker: str, ratio: float, open_price: float, on: date, phase: str = "open"
    ) -> list[LedgerEvent]:
        """§7.1 step 1–2: holding × r (fraction sold as cash in lieu at ``open_price``),
        entry/mark ÷ r. Same-day entry reservations are already in T units and untouched."""
        if ratio == 1.0:
            return []
        if not ratio > 0:
            raise AccountError("unknown_split", f"{ticker} {on}: split_ratio {ratio}")
        if open_price <= 0:
            raise AccountError("invalid_fill", f"open_price={open_price}")
        events: list[LedgerEvent] = []
        h = self.holdings.get(ticker)
        if h is not None:
            raw = h.shares * ratio
            whole = math.floor(raw + 1e-9)
            frac = raw - whole
            new_fill, new_mark = h.entry_fill / ratio, h.mark / ratio
            if frac > 1e-9:
                cash = frac * open_price
                pnl = frac * (open_price - new_fill)
                self.cash_usd += cash
                self.realized_usd += pnl
                self.cash_in_lieu_usd += cash
                events.append(
                    self._event(
                        on,
                        phase,
                        "cash_in_lieu",
                        ticker,
                        frac,
                        open_price,
                        cash,
                        pnl,
                        0.0,
                        f"split {ratio}",
                    )
                )
            self.holdings[ticker] = replace(h, shares=whole, entry_fill=new_fill, mark=new_mark)
            events.append(
                self._event(
                    on,
                    phase,
                    "split",
                    ticker,
                    whole,
                    new_fill,
                    0.0,
                    0.0,
                    0.0,
                    f"ratio {ratio}: {h.shares} -> {whole}",
                )
            )
        # Reservations are NOT rewritten: they live one session and their plan already is in
        # day-T units (compute_levels divides by split_ratio[T]; PORTFOLIO_DESIGN §7.1 step 3).
        return events

    # --- mark-to-market --------------------------------------------------------------------------

    def mark(self, prices: dict[str, float], on: date, phase: str = "close") -> LedgerEvent:
        """CLOSE phase: mark every holding (a missing price is an error), update HWM."""
        missing = [t for t in self.holdings if t not in prices]
        if missing:
            raise AccountError("missing_mark", ", ".join(sorted(missing)))
        for t, h in self.holdings.items():
            p = float(prices[t])
            if not p > 0:
                raise AccountError("invalid_mark", f"{t}={p}")
            self.holdings[t] = replace(h, mark=p)
        self.last_mark_date = on
        eq = self.equity_usd
        self.hwm_usd = max(self.hwm_usd, eq)
        return self._event(
            on,
            phase,
            "mark",
            None,
            len(self.holdings),
            0.0,
            0.0,
            0.0,
            0.0,
            f"equity={eq:.4f} unrealized={self.unrealized_usd:.4f}",
        )

    # --- internals -------------------------------------------------------------------------------

    def _event(self, on, phase, kind, ticker, shares, price, cash_d, real_d, res_d, note=""):
        ev = LedgerEvent(
            on,
            phase_name(phase),
            kind,
            ticker,
            float(shares),
            float(price),
            float(cash_d),
            float(real_d),
            float(res_d),
            note,
        )
        return self.ledger.record(ev)
