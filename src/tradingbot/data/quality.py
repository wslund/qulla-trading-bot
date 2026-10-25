"""Data quality checks on a normalized daily-bar frame.

Status semantics: FAIL = data must not be used (structural errors); WARN = usable but
flagged (gaps, suspicious jumps); OK = no findings. No exchange holiday calendar is used
in the pilot, so gaps are judged on weekdays only and reported, not failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingbot.data.schema import PRICE_COLUMNS, validate_columns

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class QualityReport:
    status: str = OK
    issues: list[str] = field(default_factory=list)

    def add(self, level: str, msg: str) -> None:
        self.issues.append(f"{level}: {msg}")
        if level == FAIL or (level == WARN and self.status == OK):
            self.status = level


def check_quality(
    df: pd.DataFrame,
    *,
    max_weekday_gap: int = 4,
    jump_tolerance: float = 0.4,
    split_tolerance: float = 0.2,
) -> QualityReport:
    """Run all checks. ``max_weekday_gap`` = consecutive missing weekdays before WARN."""
    rep = QualityReport()
    problems = validate_columns(df)
    for p in problems:
        rep.add(FAIL, p)
    if problems or df.empty:
        if df.empty:
            rep.add(FAIL, "empty frame")
        return rep

    _check_structure(df, rep)
    _check_ohlc(df, rep)
    _check_calendar(df, rep, max_weekday_gap)
    _check_splits(df, rep, jump_tolerance, split_tolerance)
    return rep


def _check_structure(df: pd.DataFrame, rep: QualityReport) -> None:
    dup = df["date"].duplicated().sum()
    if dup:
        rep.add(FAIL, f"{dup} duplicated dates")
    if not df["date"].is_monotonic_increasing:
        rep.add(FAIL, "dates not sorted ascending")
    nulls = df[[*PRICE_COLUMNS, "volume", "adj_close"]].isna().sum()
    for col, n in nulls.items():
        if n:
            rep.add(FAIL, f"{n} missing values in {col}")
    if df["ticker"].nunique() != 1:
        rep.add(FAIL, "frame contains more than one ticker")


def _check_ohlc(df: pd.DataFrame, rep: QualityReport) -> None:
    px = df[PRICE_COLUMNS]
    nonpos = (px <= 0).any(axis=1).sum()
    if nonpos:
        rep.add(FAIL, f"{nonpos} rows with non-positive prices")
    bad_low = (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9).sum()
    bad_high = (df["high"] < df[["open", "close"]].max(axis=1) - 1e-9).sum()
    if bad_low:
        rep.add(FAIL, f"{bad_low} rows where low > min(open, close)")
    if bad_high:
        rep.add(FAIL, f"{bad_high} rows where high < max(open, close)")
    negvol = (df["volume"] < 0).sum()
    if negvol:
        rep.add(FAIL, f"{negvol} rows with negative volume")
    zerovol = (df["volume"] == 0).sum()
    if zerovol:
        rep.add(WARN, f"{zerovol} rows with zero volume")


def _check_calendar(df: pd.DataFrame, rep: QualityReport, max_weekday_gap: int) -> None:
    dates = pd.to_datetime(df["date"])
    weekend = (dates.dt.day_of_week >= 5).sum()
    if weekend:
        rep.add(FAIL, f"{weekend} rows dated on a weekend")
    if len(dates) < 2:
        return
    # Number of weekdays strictly between consecutive observations.
    d64 = dates.to_numpy().astype("datetime64[D]")
    missing = np.busday_count(d64[:-1], d64[1:]) - 1
    total_missing = int(missing.sum())
    big = int((missing > max_weekday_gap).sum())
    if big:
        rep.add(WARN, f"{big} gaps longer than {max_weekday_gap} weekdays")
    if total_missing:
        rep.add(WARN, f"{total_missing} weekdays without data (holidays not excluded)")


def _check_splits(
    df: pd.DataFrame, rep: QualityReport, jump_tolerance: float, split_tolerance: float
) -> None:
    """As-traded close should drop by ~1/ratio on reported split days and not jump elsewhere."""
    if len(df) < 2:
        return
    ratio = df["close"].to_numpy(float)[1:] / df["close"].to_numpy(float)[:-1]
    split = df["split_ratio"].to_numpy(float)[1:]
    reported = split != 1.0
    expected = 1.0 / split[reported]
    off = np.abs(ratio[reported] / expected - 1.0) > split_tolerance
    if off.any():
        rep.add(WARN, f"{int(off.sum())} reported splits where price move does not match ratio")
    jumps = (~reported) & ((ratio > 1 + jump_tolerance) | (ratio < 1 - jump_tolerance))
    if jumps.any():
        days = df["date"].to_numpy()[1:][jumps][:5]
        rep.add(WARN, f"{int(jumps.sum())} unexplained close jumps >{jump_tolerance:.0%}: {days}")
