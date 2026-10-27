"""Rule-based candidate filtering on the feature table (STRATEGY_SPEC v0.2.1 §1–3, step 2).

Each rule compares one or two feature values with a threshold. A rule whose inputs are
missing (NaN/None) evaluates to False, never True (conservative). Thresholds carry the
spec's tag: [Q] documented by Qullamaggie, [T] our mechanisation (values are the spec's
start proposals, open under P-001/P-002), [H] hypothesis without source support. [H]
rules are off by default (threshold None) and must be switched on explicitly.

The feature table already honours the T-1 convention; rules only read the row dated T,
so no additional look-ahead is introduced. Candidates carry no outcome and no order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from tradingbot.scanner.features import SPEC_VERSION

RULES_VERSION = "1"


@dataclass(frozen=True)
class Thresholds:
    """Values from STRATEGY_SPEC v0.2.1. Tags in ``RULE_TAGS``; None disables a rule."""

    min_prior_move: float = 0.30  # §2 [T], lower bound of the [Q] "30-100%+"
    require_trend: bool = True  # §2 [T]: close > SMA50 and SMA50 rising over 10 bars
    consol_min_days: int = 12  # §3 [T] from [Q] "2 weeks to 2 months"
    consol_max_days: int = 45
    tightness_ratio: float = 0.6  # §3 [T], P-002
    require_higher_lows: bool = True  # §3 [T] from [Q] "higher lows"
    max_consol_depth: float = 0.25  # §3 [T], P-002
    max_pct_below_high: float = 0.10  # §3 [T], P-002
    max_closes_below_sma20: int = 3  # §3 [T], P-002
    require_above_sma20: bool = True  # §3 [T] surf rule
    min_adr: float | None = None  # [H]: source says ADR is not a selection criterion
    max_adr: float | None = None  # [H]
    min_price: float | None = None  # §1 [T] P-001, no spec default -> off
    min_dollar_volume: float | None = None  # §1 [T] P-001, no spec default -> off


RULE_TAGS: dict[str, str] = {
    "prior_move": "[T] §2 (value = lower bound of [Q] 30-100%)",
    "trend": "[T] §2",
    "consol_length": "[T] §3 (from [Q] 2 weeks-2 months)",
    "tightness": "[T] §3 P-002",
    "higher_lows": "[T] §3 (from [Q] higher lows)",
    "depth": "[T] §3 P-002",
    "position": "[T] §3 P-002",
    "closes_below_sma20": "[T] §3 P-002",
    "above_sma20": "[T] §3",
    "adr": "[H] no source support; off by default",
    "price": "[T] §1 P-001; off by default (no spec value)",
    "liquidity": "[T] §1 P-001; off by default (no spec value)",
}
RULE_NAMES = list(RULE_TAGS)


@dataclass
class RuleResult:
    passed: bool | None  # None = rule disabled
    missing: bool = False
    detail: str = ""


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return np.nan
    return v


def _cmp(value, ok, detail: str) -> RuleResult:
    v = _num(value)
    if np.isnan(v):
        return RuleResult(False, missing=True, detail=f"{detail}: missing")
    return RuleResult(bool(ok(v)), detail=f"{detail}={v:.4g}")


def evaluate_row(row: pd.Series | dict, th: Thresholds) -> dict[str, RuleResult]:
    """Evaluate every rule for one feature row. Disabled rules return passed=None."""
    r: dict[str, RuleResult] = {}
    r["prior_move"] = _cmp(row["prior_move_pct"], lambda v: v >= th.min_prior_move, "prior_move")
    if th.require_trend:
        cs = _num(row["close_vs_sma50"])
        sl = _num(row["sma50_slope"])
        if np.isnan(cs) or np.isnan(sl):
            r["trend"] = RuleResult(False, missing=True, detail="trend: missing")
        else:
            r["trend"] = RuleResult(
                cs > 0 and sl > 0, detail=f"close_vs_sma50={cs:.4g} slope={sl:.4g}"
            )
    else:
        r["trend"] = RuleResult(None)
    r["consol_length"] = _cmp(
        row["consol_days"], lambda v: th.consol_min_days <= v <= th.consol_max_days, "consol_days"
    )
    r["tightness"] = _cmp(row["consol_tightness"], lambda v: v <= th.tightness_ratio, "tightness")
    if th.require_higher_lows:
        hl = row["consol_higher_lows"]
        if hl is None or (isinstance(hl, float) and np.isnan(hl)):
            r["higher_lows"] = RuleResult(False, missing=True, detail="higher_lows: missing")
        else:
            r["higher_lows"] = RuleResult(bool(hl), detail=f"higher_lows={bool(hl)}")
    else:
        r["higher_lows"] = RuleResult(None)
    r["depth"] = _cmp(row["consol_depth"], lambda v: v <= th.max_consol_depth, "depth")
    r["position"] = _cmp(
        row["consol_pct_below_high"], lambda v: v <= th.max_pct_below_high, "pct_below_high"
    )
    r["closes_below_sma20"] = _cmp(
        row["consol_closes_below_sma20"],
        lambda v: v <= th.max_closes_below_sma20,
        "closes_below_sma20",
    )
    r["above_sma20"] = (
        _cmp(row["close_vs_sma20"], lambda v: v > 0, "close_vs_sma20")
        if th.require_above_sma20
        else RuleResult(None)
    )
    if th.min_adr is None and th.max_adr is None:
        r["adr"] = RuleResult(None)
    else:
        lo, hi = th.min_adr or 0.0, th.max_adr or np.inf
        r["adr"] = _cmp(row["adr20"], lambda v: lo <= v <= hi, "adr20")
    r["price"] = (
        _cmp(row["close_prev"], lambda v: v >= th.min_price, "close_prev")
        if th.min_price is not None
        else RuleResult(None)
    )
    r["liquidity"] = (
        _cmp(row["dollar_volume_20d"], lambda v: v >= th.min_dollar_volume, "dollar_volume_20d")
        if th.min_dollar_volume is not None
        else RuleResult(None)
    )
    return r


def apply_rules(features: pd.DataFrame, th: Thresholds | None = None) -> pd.DataFrame:
    """Return the feature table with one ``rule_<name>`` column per rule (True/False/None),
    ``rules_passed``, ``rules_total``, ``missing_rules``, ``is_candidate`` and
    ``candidate_id`` (only for candidates)."""
    th = th or Thresholds()
    out = features.copy()
    cols: dict[str, list] = {f"rule_{n}": [] for n in RULE_NAMES}
    passed, total, missing, cand = [], [], [], []
    for _, row in features.iterrows():
        res = evaluate_row(row, th)
        active = [n for n in RULE_NAMES if res[n].passed is not None]
        for n in RULE_NAMES:
            cols[f"rule_{n}"].append(res[n].passed)
        passed.append(sum(1 for n in active if res[n].passed))
        total.append(len(active))
        missing.append(",".join(n for n in active if res[n].missing))
        cand.append(len(active) > 0 and all(res[n].passed for n in active))
    for k, v in cols.items():
        out[k] = pd.array(v, dtype="boolean")
    out["rules_passed"] = passed
    out["rules_total"] = total
    out["missing_rules"] = missing
    out["is_candidate"] = cand
    out["candidate_id"] = [
        f"{t}:{d}:spec{SPEC_VERSION}:rules{RULES_VERSION}" if c else None
        for t, d, c in zip(out["ticker"], out["date"], out["is_candidate"], strict=True)
    ]
    out["rules_version"] = RULES_VERSION
    return out


def thresholds_dict(th: Thresholds) -> dict:
    return asdict(th)


@dataclass
class RuleSummary:
    rows: int
    candidates: int
    pass_rate_by_rule: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def of(df: pd.DataFrame) -> RuleSummary:
        rates = {}
        for n in RULE_NAMES:
            col = df[f"rule_{n}"]
            active = col.notna()
            rates[n] = float(col[active].astype(bool).mean()) if active.any() else float("nan")
        return RuleSummary(len(df), int(df["is_candidate"].sum()), rates)
