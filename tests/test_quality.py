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
    assert any("weekdays without data" in i for i in rep.issues)
    assert any("unexplained close jumps" in i for i in rep.issues)


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


def test_empty_frame_fails() -> None:
    from tradingbot.data.schema import empty_normalized

    assert check_quality(empty_normalized()).status == FAIL
