"""Scanner step 2: rules on synthetic passing/failing setups, NaN handling, look-ahead, split."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.schema import AS_TRADED, NORMALIZED_COLUMNS
from tradingbot.scanner.features import FeatureParams, compute_features
from tradingbot.scanner.rules import (
    RULE_NAMES,
    RuleSummary,
    Thresholds,
    apply_rules,
    evaluate_row,
)

from .test_features import apply_split


def setup_series(
    *,
    run_up: float = 0.45,
    consol_days: int = 20,
    depth: float = 0.08,
    tighten: bool = True,
    higher_lows: bool = True,
    end_below_high: float = 0.02,
    n_flat: int = 60,
) -> pd.DataFrame:
    """As-traded series: flat base, a run-up over 30 bars, then a consolidation.

    Defaults produce a setup that passes every spec rule on the last decision date.
    The last bar is a filler: the decision row for the last date uses bars up to the one
    before it (T-1 convention), so the setup is defined on the second-to-last bar.
    """
    n_run = 30
    base = np.full(n_flat, 100.0) * (1 + 0.001 * np.sin(np.arange(n_flat)))
    run = np.linspace(100, 100 * (1 + run_up), n_run + 1)[1:]
    peak = run[-1]
    # Consolidation: an immediate dip to (1 - depth), then a grind higher with rising lows,
    # ending end_below_high under the peak.
    k = consol_days
    x = np.linspace(0, 1, k)
    close = peak * (1 - depth * (1 - x) ** 1.5)
    close[-1] = peak * (1 - end_below_high)
    if not higher_lows:
        close[-2] = peak * (1 - depth - 0.01)  # a new low in the last third
    closes = np.concatenate([base, run, close, close[-1:]])  # + filler bar (T)
    n = len(closes)
    rng = np.full(n, 0.03)
    rng[-k - 1 : -1] = np.linspace(0.03, 0.008, k) if tighten else np.linspace(0.03, 0.08, k)
    high = closes * (1 + rng / 2)
    low = closes * (1 - rng / 2)
    dates = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {
            "date": [d.date() for d in dates],
            "ticker": "SET",
            "open": closes,
            "high": high,
            "low": low,
            "close": closes,
            "volume": 2e6,
            "adj_close": closes,
            "adj_factor": 1.0,
            "split_ratio": 1.0,
            "dividend": 0.0,
            "source": "test",
            "price_basis": AS_TRADED,
        }
    )[NORMALIZED_COLUMNS]


def failed_rules(r: pd.Series) -> list[str]:
    return [n for n in RULE_NAMES if r[f"rule_{n}"] is not pd.NA and not bool(r[f"rule_{n}"])]


def last_row(df: pd.DataFrame, th: Thresholds | None = None) -> pd.Series:
    feats = compute_features(df, FeatureParams())
    return apply_rules(feats, th).iloc[-1]


# --- passing and failing setups -------------------------------------------------------------------


def test_synthetic_setup_passes_all_rules() -> None:
    r = last_row(setup_series())
    assert failed_rules(r) == [], (failed_rules(r), r["missing_rules"])
    assert bool(r["is_candidate"]) is True
    assert r["rules_total"] == 9  # the three [H]/P-001 rules are off by default
    assert r["candidate_id"].startswith("SET:") and "spec0.2.1" in r["candidate_id"]


@pytest.mark.parametrize(
    "kwargs, rule",
    [
        ({"run_up": 0.15}, "prior_move"),
        ({"consol_days": 8}, "consol_length"),
        ({"consol_days": 50}, "consol_length"),
        ({"tighten": False}, "tightness"),
        ({"higher_lows": False}, "higher_lows"),
        ({"depth": 0.35}, "depth"),
        ({"end_below_high": 0.15}, "position"),
    ],
)
def test_each_rule_fails_on_its_own_defect(kwargs, rule) -> None:
    r = last_row(setup_series(**kwargs))
    assert rule in failed_rules(r), (rule, failed_rules(r), r["missing_rules"])
    assert bool(r["is_candidate"]) is False


def test_trend_rule_fails_in_downtrend() -> None:
    df = setup_series()
    df.loc[:, ["open", "high", "low", "close", "adj_close"]] = df[
        ["open", "high", "low", "close", "adj_close"]
    ].to_numpy()[::-1]
    r = last_row(df)
    assert r["rule_trend"] == False  # noqa: E712
    assert bool(r["is_candidate"]) is False


# --- missing data, disabled rules, thresholds ---


def test_missing_feature_is_a_failed_rule_not_a_pass() -> None:
    feats = compute_features(setup_series())
    feats.loc[feats.index[-1], "consol_tightness"] = np.nan
    feats.loc[feats.index[-1], "consol_higher_lows"] = None
    r = apply_rules(feats).iloc[-1]
    assert r["rule_tightness"] == False and r["rule_higher_lows"] == False  # noqa: E712
    assert "tightness" in r["missing_rules"] and "higher_lows" in r["missing_rules"]
    assert bool(r["is_candidate"]) is False


def test_early_rows_without_history_are_never_candidates() -> None:
    out = apply_rules(compute_features(setup_series()))
    early = out.iloc[:60]
    assert not early["is_candidate"].any()
    assert (early["missing_rules"] != "").all()


def test_hypothesis_rules_off_by_default_and_configurable() -> None:
    feats = compute_features(setup_series())
    default = apply_rules(feats).iloc[-1]
    assert default["rule_adr"] is pd.NA or pd.isna(default["rule_adr"])
    assert default["rules_total"] == 9
    strict = apply_rules(feats, Thresholds(min_adr=0.10)).iloc[-1]
    assert strict["rules_total"] == 10
    assert strict["rule_adr"] == False and bool(strict["is_candidate"]) is False  # noqa: E712
    liq = apply_rules(feats, Thresholds(min_dollar_volume=1e9)).iloc[-1]
    assert liq["rule_liquidity"] == False  # noqa: E712


def test_evaluate_row_details_and_threshold_boundary() -> None:
    row = {
        "prior_move_pct": 0.30,
        "close_vs_sma50": 0.01,
        "sma50_slope": 0.0,
        "consol_days": 12,
        "consol_tightness": 0.6,
        "consol_higher_lows": True,
        "consol_depth": 0.25,
        "consol_pct_below_high": 0.10,
        "consol_closes_below_sma20": 3,
        "close_vs_sma20": 0.0,
        "adr20": 0.03,
        "close_prev": 10.0,
        "dollar_volume_20d": 1e7,
    }
    res = evaluate_row(row, Thresholds())
    assert res["prior_move"].passed and res["consol_length"].passed and res["tightness"].passed
    assert res["depth"].passed and res["position"].passed and res["closes_below_sma20"].passed
    assert not res["trend"].passed  # slope must be > 0, not >= 0
    assert not res["above_sma20"].passed  # close must be above SMA20, not equal
    assert "prior_move=0.3" in res["prior_move"].detail


# --- look-ahead and splits ---


def test_candidate_flags_for_t_do_not_depend_on_bars_at_t_or_later() -> None:
    df = setup_series()
    base = apply_rules(compute_features(df))
    t = len(df) - 5
    mutated = df.copy()
    for c in ("open", "high", "low", "close", "adj_close"):
        mutated.loc[t:, c] = mutated.loc[t:, c] * 0.5  # a crash from T on
    out = apply_rules(compute_features(mutated))
    upto = base["date"] <= df["date"].iloc[t]
    pd.testing.assert_frame_equal(base[upto], out[upto])
    assert bool(base.iloc[-1]["is_candidate"]) and not bool(out.iloc[-1]["is_candidate"])


def test_split_inside_lookback_gives_same_candidates_as_unsplit() -> None:
    """AAPL-2020 style: a 4:1 split in the middle of the run-up must not change any rule."""
    df = setup_series()
    split = apply_split(df, at=len(df) - 35, ratio=4.0)
    base = apply_rules(compute_features(df))
    out = apply_rules(compute_features(split))
    rule_cols = [f"rule_{n}" for n in RULE_NAMES] + ["is_candidate", "rules_passed"]
    pd.testing.assert_frame_equal(base[rule_cols], out[rule_cols])
    assert bool(out.iloc[-1]["is_candidate"]) is True
    np.testing.assert_allclose(base["prior_move_pct"].iloc[-1], out["prior_move_pct"].iloc[-1])


def test_as_traded_without_splits_is_refused_before_rules() -> None:
    from tradingbot.scanner.features import FeatureError

    df = setup_series()
    df["split_ratio"] = np.nan
    with pytest.raises(FeatureError):
        compute_features(df)


# --- storage and summary ---


def test_build_candidates_writes_parquet_and_meta(tmp_path: Path) -> None:
    from tradingbot.data.schema import FetchMeta
    from tradingbot.data.store import ParquetStore
    from tradingbot.scanner.pipeline import build_candidates, build_features, read_candidates

    store = ParquetStore(tmp_path)
    df = setup_series()
    meta = FetchMeta(
        "test",
        "SET",
        df["date"].iloc[0],
        df["date"].iloc[-1],
        datetime(2026, 1, 1, tzinfo=UTC),
        "x",
        "x",
    )
    meta.actual_start, meta.actual_end, meta.rows, meta.quality_status = (
        df["date"].iloc[0],
        df["date"].iloc[-1],
        len(df),
        "WARN",
    )
    store.write_processed(meta, df)
    with pytest.raises(FileNotFoundError, match="build_features first"):
        build_candidates(store, "test", "SET")
    build_features(store, "test", "SET")
    m = build_candidates(store, "test", "SET", built_at=datetime(2026, 1, 2, tzinfo=UTC))
    c = read_candidates(store, "test", "SET")
    assert m["candidates"] >= 1 and m["rows"] == len(df) - 1
    assert m["thresholds"]["tightness_ratio"] == 0.6 and m["thresholds"]["min_adr"] is None
    assert m["rule_tags"]["adr"].startswith("[H]")
    assert m["spec_version"] == "0.2.1" and m["rules_version"] == "1"
    assert m["input_quality_status"] == "WARN" and m["input_price_basis"] == AS_TRADED
    assert c["is_candidate"].sum() == m["candidates"]
    assert c.loc[c["is_candidate"], "candidate_id"].notna().all()
    assert c.loc[~c["is_candidate"], "candidate_id"].isna().all()
    s = RuleSummary.of(c)
    assert 0 <= s.pass_rate_by_rule["prior_move"] <= 1 and np.isnan(s.pass_rate_by_rule["adr"])


def test_build_candidates_refuses_quality_fail(tmp_path: Path) -> None:
    from tradingbot.data.schema import FetchMeta
    from tradingbot.data.store import ParquetStore
    from tradingbot.scanner.pipeline import build_candidates, build_features

    store = ParquetStore(tmp_path)
    df = setup_series()
    meta = FetchMeta(
        "test",
        "SET",
        df["date"].iloc[0],
        df["date"].iloc[-1],
        datetime(2026, 1, 1, tzinfo=UTC),
        "x",
        "x",
    )
    meta.quality_status = "FAIL"
    store.write_processed(meta, df)
    build_features(store, "test", "SET")
    with pytest.raises(ValueError, match="quality FAIL"):
        build_candidates(store, "test", "SET")
