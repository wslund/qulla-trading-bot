"""Quality checks on normalized frames, with injected defects."""

import pandas as pd

from tradingbot.data.quality import FAIL, OK, WARN, check_quality


def test_clean_fixture_is_ok(normalized: pd.DataFrame) -> None:
    rep = check_quality(normalized)
    assert rep.status == OK, rep.issues


def test_ohlc_violation_fails(normalized: pd.DataFrame) -> None:
    df = normalized.copy()
    df.loc[3, "low"] = df.loc[3, "close"] * 1.5
    rep = check_quality(df)
    assert rep.status == FAIL
    assert any("low > min" in i for i in rep.issues)


def test_duplicate_date_fails(normalized: pd.DataFrame) -> None:
    df = pd.concat([normalized, normalized.iloc[[5]]]).sort_values("date").reset_index(drop=True)
    rep = check_quality(df)
    assert rep.status == FAIL
    assert any("duplicated" in i for i in rep.issues)


def test_missing_value_fails(normalized: pd.DataFrame) -> None:
    df = normalized.copy()
    df.loc[2, "close"] = float("nan")
    assert check_quality(df).status == FAIL


def test_gap_warns(normalized: pd.DataFrame) -> None:
    df = normalized.drop(index=[4, 5, 6, 7, 8]).reset_index(drop=True)
    # Dropping the split day removes the reported split; the price jump is now unexplained.
    rep = check_quality(df)
    assert rep.status == WARN
    assert any("5 XNYS sessions without data" in i for i in rep.issues)
    assert any("unexplained close jumps" in i for i in rep.issues)


def test_exchange_holiday_is_not_a_gap() -> None:
    """Good Friday 2024-03-29 and Memorial Day 2024-05-27 are closed: no WARN expected."""
    import exchange_calendars as xc

    sessions = xc.get_calendar("XNYS").sessions_in_range("2024-03-25", "2024-06-03")
    n = len(sessions)
    df = pd.DataFrame(
        {
            "date": [d.date() for d in sessions],
            "ticker": "HOL",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1000.0,
            "adj_close": 10.0,
            "adj_factor": 1.0,
            "split_ratio": 1.0,
            "dividend": 0.0,
            "source": "test",
            "price_basis": "as_traded",
        }
    )
    assert n == 49  # 51 weekdays minus Good Friday and Memorial Day
    rep = check_quality(df)
    assert rep.status == OK, rep.issues
    # Same frame with the weekday fallback reports the two holidays as missing weekdays.
    rep_wd = check_quality(df, calendar=None)
    assert rep_wd.status == WARN
    assert any("2 weekdays without data" in i for i in rep_wd.issues)


def test_row_on_holiday_fails(normalized: pd.DataFrame) -> None:
    df = normalized.copy()
    df.loc[0, "date"] = pd.Timestamp("2024-01-15").date()  # MLK day, closed
    rep = check_quality(df)
    assert rep.status == FAIL
    assert any("non-trading days" in i for i in rep.issues)


def test_split_mismatch_warns(normalized: pd.DataFrame) -> None:
    df = normalized.copy()
    df.loc[df["split_ratio"] == 2.0, "split_ratio"] = 3.0
    rep = check_quality(df)
    assert rep.status == WARN
    assert any("does not match ratio" in i for i in rep.issues)


def test_weekend_date_fails(normalized: pd.DataFrame) -> None:
    df = normalized.copy()
    df.loc[0, "date"] = pd.Timestamp("2024-03-02").date()  # Saturday
    assert check_quality(df).status == FAIL
    assert check_quality(df, calendar=None).status == FAIL


def test_empty_frame_fails() -> None:
    from tradingbot.data.schema import empty_normalized

    assert check_quality(empty_normalized()).status == FAIL
