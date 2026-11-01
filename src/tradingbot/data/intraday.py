"""Minute bars for one session: session filter, normalization, append-only store (design §5/§9).

Alpaca SIP minute bars for a day include pre-/post-market and, before 2016-ish, the whole UTC
day (AAPL 2016-01-04: 718 bars from 09:00Z; SIVB 2023-03-09: 750 bars from 00:00Z). Every
bar is therefore tagged ``in_session`` with the XNYS calendar's actual ``session_open <= t <
session_close`` for that date (DST and half days come from the calendar, never hard-coded).
The 16:00 ET bar (closing auction prints) is outside the session by this rule; it is kept in
the raw file for the daily-volume comparison but never used for decisions.

``n_session`` versus the expected minute count is DIAGNOSTIC only: 390 bars can hold wrong
prices and 380 can be correct for a thin stock. ``sparse_session`` (> 5 % missing) is a flag
on the outcome, not a refusal.

Historical minute bars can be revised after first publication (late prints, corrections).
Raw responses are therefore stored append-only per fetch with ``fetched_at``;
``compare_fetches`` reports the bars that differ between two fetches of the same session so a
decision that would change can be flagged ``revised_bars`` (log-only in v1).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot.data.quality import _CALENDARS

CALENDAR = "XNYS"
NY_TZ = "America/New_York"
MINUTE_COLUMNS = [
    "ts_utc",
    "ts_ny",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "n",
    "vw",
    "in_session",
]
SPARSE_THRESHOLD = 0.05


def _calendar():
    import exchange_calendars as xc

    if CALENDAR not in _CALENDARS:
        _CALENDARS[CALENDAR] = xc.get_calendar(CALENDAR, start="1990-01-01")
    return _CALENDARS[CALENDAR]


def is_session(session_date: date) -> bool:
    return bool(_calendar().is_session(pd.Timestamp(session_date)))


def session_bounds(session_date: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(open, close) as tz-aware UTC timestamps for an XNYS session; raises if not a session."""
    cal = _calendar()
    ts = pd.Timestamp(session_date)
    if not cal.is_session(ts):
        raise ValueError(f"{session_date} is not an XNYS session")
    return cal.session_open(ts).tz_convert("UTC"), cal.session_close(ts).tz_convert("UTC")


def expected_session_minutes(session_date: date) -> int:
    o, c = session_bounds(session_date)
    return int((c - o).total_seconds() // 60)


def normalize_minute(raw: pd.DataFrame, session_date: date) -> pd.DataFrame:
    """Raw Alpaca bars (t,o,h,l,c,v,n,vw) -> MINUTE_COLUMNS with the session flag."""
    if raw.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    o, c = session_bounds(session_date)
    ts = pd.to_datetime(raw["t"], utc=True)
    out = pd.DataFrame(
        {
            "ts_utc": ts,
            "ts_ny": ts.dt.tz_convert(NY_TZ),
            "open": raw["o"].to_numpy(float),
            "high": raw["h"].to_numpy(float),
            "low": raw["l"].to_numpy(float),
            "close": raw["c"].to_numpy(float),
            "volume": raw["v"].to_numpy(float),
            "n": raw["n"].to_numpy(float) if "n" in raw else np.nan,
            "vw": raw["vw"].to_numpy(float) if "vw" in raw else np.nan,
        }
    )
    out["in_session"] = (out["ts_utc"] >= o) & (out["ts_utc"] < c)
    out = out.sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
    return out[MINUTE_COLUMNS]


def session_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Only in-session bars, sorted. This is what the ORB logic may see."""
    if frame.empty or "in_session" not in frame.columns:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    return frame[frame["in_session"].astype(bool)].sort_values("ts_utc").reset_index(drop=True)


@dataclass(frozen=True)
class SessionReport:
    ticker: str
    session_date: date
    n_raw: int
    n_session: int
    expected: int
    sparse_session: bool
    has_close_bar: bool  # a bar at exactly session_close (16:00 ET on a full day)
    first_ts: str | None
    last_ts: str | None
    pages: int
    fetched_at: str
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["session_date"] = self.session_date.isoformat()
        return d


def session_report(
    ticker: str,
    session_date: date,
    frame: pd.DataFrame,
    *,
    pages: int,
    fetched_at: datetime,
    note: str = "",
) -> SessionReport:
    o, c = session_bounds(session_date)
    expected = int((c - o).total_seconds() // 60)
    n_session = int(frame["in_session"].sum()) if len(frame) else 0
    return SessionReport(
        ticker=ticker.upper(),
        session_date=session_date,
        n_raw=int(len(frame)),
        n_session=n_session,
        expected=expected,
        sparse_session=n_session < expected * (1 - SPARSE_THRESHOLD),
        has_close_bar=bool(len(frame) and (frame["ts_utc"] == c).any()),
        first_ts=str(frame["ts_utc"].iloc[0]) if len(frame) else None,
        last_ts=str(frame["ts_utc"].iloc[-1]) if len(frame) else None,
        pages=pages,
        fetched_at=fetched_at.isoformat(),
        note=note,
    )


def compare_fetches(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """Bars that differ between two fetches of the same session (late corrections)."""
    a = old.set_index("ts_utc")[["open", "high", "low", "close", "volume"]]
    b = new.set_index("ts_utc")[["open", "high", "low", "close", "volume"]]
    common = a.index.intersection(b.index)
    diff = ~np.isclose(a.loc[common].to_numpy(float), b.loc[common].to_numpy(float))
    changed = [str(t) for t in common[diff.any(axis=1)]]
    return {
        "only_in_old": [str(t) for t in a.index.difference(b.index)],
        "only_in_new": [str(t) for t in b.index.difference(a.index)],
        "changed": changed,
        "revised": bool(changed) or len(a.index.symmetric_difference(b.index)) > 0,
    }


class IntradayStore:
    """data/intraday/<source>/<TICKER>/<session>_<fetched_at>.parquet (+ .json report).
    Append-only: a new fetch never overwrites an earlier one."""

    def __init__(self, root: Path, source: str = "alpaca") -> None:
        self.root = Path(root) / "intraday" / source

    def dir(self, ticker: str) -> Path:
        return self.root / ticker.upper()

    def write(self, report: SessionReport, frame: pd.DataFrame) -> Path:
        d = self.dir(report.ticker)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromisoformat(report.fetched_at).strftime("%Y%m%dT%H%M%SZ")
        p = d / f"{report.session_date.isoformat()}_{stamp}.parquet"
        frame.to_parquet(p, index=False)
        p.with_suffix(".json").write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return p

    def fetches(self, ticker: str, session_date: date) -> list[Path]:
        d = self.dir(ticker)
        return sorted(d.glob(f"{session_date.isoformat()}_*.parquet")) if d.exists() else []

    def read_latest(self, ticker: str, session_date: date) -> pd.DataFrame | None:
        files = self.fetches(ticker, session_date)
        return pd.read_parquet(files[-1]) if files else None

    def read_report(self, path: Path) -> dict:
        return json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))

    def sessions(self, ticker: str) -> list[date]:
        d = self.dir(ticker)
        if not d.exists():
            return []
        return sorted({date.fromisoformat(p.name[:10]) for p in d.glob("*.parquet")})


def fetch_window(session_date: date) -> tuple[datetime, datetime]:
    """Request window: session open .. close + 1 minute (to capture the 16:00 bar)."""
    o, c = session_bounds(session_date)
    return o.to_pydatetime().astimezone(UTC), (c + timedelta(minutes=1)).to_pydatetime().astimezone(
        UTC
    )
