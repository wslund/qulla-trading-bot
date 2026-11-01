"""Ledger: every cash movement as an event, one row per session (PORTFOLIO_DESIGN §3, §9; P1).

Events are produced by ``Account`` methods and never by hand; the session row is written by
``Ledger.close_session`` after the CLOSE-phase mark and asserts the bookkeeping identities:

  equity   == cash + Σ shares × mark
  cash     == start_cash + realized − Σ open cost basis          (realized includes all
                                                                  commissions, FX, cash in lieu)
  equity   == start_cash + realized + unrealized
  reserved == Σ open reservations, 0 <= reserved <= cash

All amounts in USD; ``equity_sek`` uses the constant pilot ``fx_rate`` (not a market rate).
Diagnostic columns that P3/P4 will fill are present from the start with zeros so the schema
does not change later (``reservation_blocked_fills``, ``priority_binding``, ``state``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tradingbot.backtesting.account import Account
    from tradingbot.backtesting.clock import Phase, Session

LEDGER_VERSION = "1"
TOL = 1e-6


class LedgerError(RuntimeError):
    """A bookkeeping identity failed – a programming error, never silently continued."""


@dataclass(frozen=True)
class LedgerEvent:
    date: date
    phase: str
    kind: str  # deposit / reserve / release / buy / sell / split / cash_in_lieu / mark
    ticker: str | None
    shares: float
    price: float
    cash_delta: float
    realized_delta: float
    reserved_delta: float
    note: str = ""


@dataclass(frozen=True)
class LedgerRow:
    date: date
    cash: float
    reserved: float
    available: float
    n_positions: int
    n_reservations: int
    market_value: float
    cost_basis: float
    equity: float
    realized: float
    unrealized: float
    hwm: float
    drawdown_pct: float
    gross_exposure_pct: float  # market value + reserved, over equity
    commission_total: float
    fx_total: float
    cash_in_lieu_total: float
    state: str
    fx_mode: str
    fx_rate: float
    equity_sek: float
    n_events: int
    reservation_blocked_fills: int = 0  # P3
    priority_binding: bool = False  # P3
    ledger_version: str = LEDGER_VERSION


@dataclass
class Ledger:
    events: list[LedgerEvent] = field(default_factory=list)
    rows: list[LedgerRow] = field(default_factory=list)
    _events_at_last_row: int = 0

    def record(self, event: LedgerEvent) -> LedgerEvent:
        self.events.append(event)
        return event

    def close_session(self, account: Account, session: Session) -> LedgerRow:
        """Write the session row after ``account.mark`` for this session; checks identities."""
        if account.last_mark_date != session.date:
            raise LedgerError(
                f"account not marked for {session.date} (last {account.last_mark_date})"
            )
        check_identities(account)
        mv = account.market_value
        eq = account.equity_usd
        row = LedgerRow(
            date=session.date,
            cash=account.cash_usd,
            reserved=account.reserved_usd,
            available=account.available_usd,
            n_positions=len(account.holdings),
            n_reservations=len(account.reservations),
            market_value=mv,
            cost_basis=account.cost_basis,
            equity=eq,
            realized=account.realized_usd,
            unrealized=account.unrealized_usd,
            hwm=account.hwm_usd,
            drawdown_pct=(account.hwm_usd - eq) / account.hwm_usd if account.hwm_usd > 0 else 0.0,
            gross_exposure_pct=(mv + account.reserved_usd) / eq if eq > 0 else float("inf"),
            commission_total=account.commission_usd,
            fx_total=account.fx_usd,
            cash_in_lieu_total=account.cash_in_lieu_usd,
            state=account.state,
            fx_mode=account.fx_mode,
            fx_rate=account.fx_rate,
            equity_sek=eq * account.fx_rate,
            n_events=len(self.events) - self._events_at_last_row,
        )
        self._events_at_last_row = len(self.events)
        self.rows.append(row)
        return row

    def events_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(e) for e in self.events])

    def rows_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(r) for r in self.rows])


def check_identities(account: Account) -> None:
    """Raise LedgerError if any bookkeeping identity in the module docstring is violated."""
    cash, res = account.cash_usd, account.reserved_usd
    if res < -TOL or res > cash + TOL:
        raise LedgerError(f"reserved {res} outside [0, cash {cash}]")
    if abs(res - sum(r.amount_usd for r in account.reservations.values())) > TOL:
        raise LedgerError("reserved != sum of reservations")
    expected_cash = account.start_cash_usd + account.realized_usd - account.cost_basis
    if abs(cash - expected_cash) > TOL:
        raise LedgerError(f"cash {cash} != start + realized − cost basis {expected_cash}")
    if abs(account.equity_usd - (cash + account.market_value)) > TOL:
        raise LedgerError("equity != cash + market value")
    expected_eq = account.start_cash_usd + account.realized_usd + account.unrealized_usd
    if abs(account.equity_usd - expected_eq) > TOL:
        raise LedgerError(
            f"equity {account.equity_usd} != start + realized + unrealized {expected_eq}"
        )
    if cash < -TOL:
        raise LedgerError(f"negative cash {cash}")


def phase_name(phase: Phase | str) -> str:
    return str(getattr(phase, "value", phase))
