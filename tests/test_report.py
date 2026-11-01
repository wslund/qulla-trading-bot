"""P6: report, run storage and CLI for the daily-model portfolio (PORTFOLIO_DESIGN §9)."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.backtesting.portfolio import run_portfolio
from tradingbot.backtesting.report import (
    PORTFOLIO_REASONS,
    STRATEGY_REASONS,
    build_report,
    classify_rejections,
    render_text,
    write_run,
)
from tradingbot.data.schema import FetchMeta
from tradingbot.data.store import ParquetStore
from tradingbot.risk.costs import CostParams
from tradingbot.risk.limits import LimitParams
from tradingbot.scanner.pipeline import build_candidates, build_features

from .test_portfolio import TP, params, universe
from .test_trades import RISE_THEN_FADE

ROOT = Path(__file__).resolve().parents[1]


def _run(tickers=("AAA", "BBB", "CCC"), bars=RISE_THEN_FADE, limits=None, **kw):
    series, cands, t0, f = universe(bars, list(tickers), **kw)
    return run_portfolio(series, cands, params(limits=limits or LimitParams())), series, cands, t0


# --- summaries reconcile with the account and the trades ---


def test_capital_section_reconciles_with_account_and_trades() -> None:
    res, *_ = _run()
    rep = build_report(res)["summary"]
    c, acc = rep["capital"], res.account
    assert c["deposit_sek"] == pytest.approx(200_000.0) and c["fx_rate_sek_per_usd"] == acc.fx_rate
    assert c["deposit_usd_gross"] == pytest.approx(200_000 / acc.fx_rate)
    assert c["trading_pnl_usd"] == pytest.approx(sum(t.pnl_net_usd for t in res.trades))
    assert c["end_equity_usd"] == pytest.approx(acc.equity_usd)
    assert c["end_equity_usd"] == pytest.approx(c["start_cash_usd"] + c["trading_pnl_usd"])
    assert abs(c["identity_gap_usd"]) < 1e-6
    assert c["net_pnl_sek"] == pytest.approx(c["net_pnl_usd"] * acc.fx_rate)
    k = rep["costs_usd"]
    assert k["commission"] == pytest.approx(sum(t.cost_actual["commission"] for t in res.trades))
    assert k["fx_per_trade"] == pytest.approx(sum(t.cost_actual["fx"] for t in res.trades))
    assert k["fx_deposit"] == 0.0 and k["total_charged"] == pytest.approx(
        k["commission"] + k["fx_per_trade"]
    )
    perf = rep["performance"]["realistic"]
    assert perf["n"] == 3 and perf["win_rate"] == 1.0 and perf["profit_factor"] == float("inf")
    assert perf["mean_r"] == pytest.approx(sum(t.r_multiple for t in res.trades) / 3)
    assert rep["performance"]["max_drawdown_pct"] == pytest.approx(
        res.ledger_frame()["drawdown_pct"].max()
    )
    assert rep["utilization"]["max_positions"] == 3 and rep["utilization"]["sessions"] == len(
        res.ledger_frame()
    )
    assert rep["utilization"]["mean_reserved_per_candidate_day_usd"] > 0
    assert "D-012" in rep["note"]


def test_usd_balance_mode_separates_deposit_fx_from_trading() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    p = params()
    from dataclasses import replace

    p = replace(p, costs=CostParams(fx_mode="usd_balance", fx_cost_bps=25.0))
    res = run_portfolio(series, cands, p)
    c = build_report(res)["summary"]["capital"]
    assert c["deposit_fx_cost_usd"] == pytest.approx(200_000 / 10.5 * 0.0025)
    assert c["start_cash_usd"] == pytest.approx(c["deposit_usd_gross"] - c["deposit_fx_cost_usd"])
    assert c["trading_pnl_usd"] == pytest.approx(sum(t.pnl_net_usd for t in res.trades))
    assert c["end_equity_usd"] == pytest.approx(c["start_cash_usd"] + c["trading_pnl_usd"])
    assert c["net_pnl_usd"] == pytest.approx(c["trading_pnl_usd"] - c["deposit_fx_cost_usd"])
    assert build_report(res)["summary"]["costs_usd"]["fx_per_trade"] == 0.0


# --- zero trades ---


def test_zero_trade_run_reports_cleanly() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    for s in series.values():
        i0 = int(s.index[s["date"] == t0][0])
        flat = float(s.loc[i0 - 1, "close"])  # flat forever: no trigger, no gap
        s.loc[i0:, ["open", "high", "low", "close", "adj_close"]] = flat
    res = run_portfolio(series, cands, params())
    rep = build_report(res)["summary"]
    assert rep["performance"]["all_trades"] == {
        "n": 0,
        "win_rate": None,
        "profit_factor": None,
        "mean_r": None,
        "median_r": None,
        "sum_net_usd": 0.0,
        "avg_win_r": None,
        "avg_loss_r": None,
    }
    assert (
        rep["capital"]["trading_pnl_usd"] == 0.0 and abs(rep["capital"]["identity_gap_usd"]) < 1e-9
    )
    assert (
        rep["limits"]["reservation_blocked_fills"] == 0
        and rep["limits"]["priority_binding_days"] == 0
    )
    assert (
        rep["rejections"]["strategy"].get("no_trigger", 0) >= 1
        and rep["rejections"]["portfolio"] == {}
    )
    text = render_text(rep)
    assert "n=0" in text and "PF=–" in text and "reservation_blocked_fills=0" in text


# --- rejection classification ---


def test_rejection_buckets_are_disjoint_and_complete() -> None:
    assert not set(STRATEGY_REASONS) & set(PORTFOLIO_REASONS)
    out = classify_rejections(
        ["no_trigger", "max_positions", "reservation_exceeded", "weird", "no_trigger"]
    )
    assert out == {
        "strategy": {"no_trigger": 2},
        "portfolio": {"max_positions": 1, "reservation_exceeded": 1},
        "other": {"weird": 1},
    }
    assert classify_rejections([]) == {"strategy": {}, "portfolio": {}, "other": {}}
    res, *_ = _run(
        tickers=("AAA", "BBB", "CCC", "DDD", "EEE"), limits=LimitParams(max_open_positions=2)
    )
    rep = build_report(res)["summary"]
    assert rep["rejections"]["portfolio"]["max_positions"] >= 3 and rep["rejections"]["other"] == {}
    assert rep["limits"]["binding_counts"] == rep["rejections"]["portfolio"]
    assert (
        rep["limits"]["priority_binding_days"] >= 1 and rep["limits"]["days_with_candidates"] >= 1
    )
    # Every engine reason is known to the report.
    assert set(r.reason for r in res.rejections) <= set(STRATEGY_REASONS) | set(PORTFOLIO_REASONS)


def test_reservation_blocked_fills_and_usd_from_decisions() -> None:
    res, *_ = _run(tickers=("AAA",), gap=0.049)
    rep = build_report(res)["summary"]
    assert rep["limits"]["reservation_blocked_fills"] == 1
    d = res.decisions[0]
    assert rep["limits"]["reservation_blocked_usd"] == pytest.approx(
        d["capital_required"] - d["reservation_usd"]
    )
    assert rep["rejections"]["portfolio"] == {"reservation_exceeded": 1}
    assert "reservation_exceeded" not in rep["rejections"]["strategy"]


# --- stale marks ---


def test_open_position_without_bar_is_reported_not_hidden() -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA"])
    s = series["AAA"]
    i0 = int(s.index[s["date"] == t0][0])
    missing = s.loc[i0 + 2, "date"]
    series["AAA"] = s.drop(index=[i0 + 2]).reset_index(drop=True)  # no bar two sessions after entry
    res = run_portfolio(series, cands, params())
    rep = build_report(res)["summary"]
    assert rep["stale_marks"]["sessions"] == 1 and rep["stale_marks"]["by_ticker"] == {
        "AAA": [str(missing)]
    }
    row = res.ledger_frame().set_index("date")
    prev = series["AAA"].loc[i0 + 1, "close"]
    assert row.loc[missing, "market_value"] == pytest.approx(res.trades[0].shares * prev)
    assert str(missing) in render_text(rep)


# --- storage and reproducibility ---


def test_write_run_is_reproducible_and_complete(tmp_path) -> None:
    res1, *_ = _run()
    res2, *_ = _run()
    d1 = write_run(
        res1, tmp_path / "a", run_id="r", args={"tickers": ["AAA"], "data_dir": Path("x")}
    )
    d2 = write_run(
        res2, tmp_path / "b", run_id="r", args={"tickers": ["AAA"], "data_dir": Path("x")}
    )
    names = sorted(p.name for p in d1.iterdir())
    assert names == [
        "config.json",
        "decisions.parquet",
        "equity_curve.parquet",
        "events.parquet",
        "ledger.parquet",
        "rejections.parquet",
        "reservations.parquet",
        "summary.json",
        "trades.parquet",
    ]
    for n in names:
        if n.endswith(".parquet"):
            assert pd.read_parquet(d1 / n).equals(pd.read_parquet(d2 / n)), n
    s1, s2 = (
        json.loads((d1 / "summary.json").read_text()),
        json.loads((d2 / "summary.json").read_text()),
    )
    assert s1 == s2 and s1["run_id"] == "r" and s1["config_hash"] == s2["config_hash"]
    cfg = json.loads((d1 / "config.json").read_text())
    assert cfg["params"]["risk"]["risk_per_trade"] == 0.01 and cfg["args"]["data_dir"] == "x"
    assert cfg["params"]["costs"]["fx_rate"] == 10.5 and "config_hash" in cfg
    curve = pd.read_parquet(d1 / "equity_curve.parquet")
    assert list(curve.columns)[:3] == ["date", "equity", "equity_sek"]
    assert curve["equity"].iloc[-1] == pytest.approx(s1["capital"]["end_equity_usd"])


# --- CLI ---


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "run_portfolio_cli", ROOT / "scripts" / "run_portfolio.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_runs_on_a_stored_universe(tmp_path, capsys) -> None:
    series, cands, t0, f = universe(RISE_THEN_FADE, ["AAA", "BBB"])
    store = ParquetStore(tmp_path)
    for t, s in series.items():
        meta = FetchMeta(
            source="test",
            ticker=t,
            requested_start=s["date"].iloc[0],
            requested_end=s["date"].iloc[-1],
            fetched_at=datetime.now(UTC),
            provider_version="x",
            adjustment_method="none",
            actual_start=s["date"].iloc[0],
            actual_end=s["date"].iloc[-1],
            rows=len(s),
            fetch_rows=len(s),
        )
        meta.quality_status = "OK"
        store.write_processed(meta, s)
        build_features(store, "test", t)
        build_candidates(store, "test", t)
    cli = _load_cli()
    out = cli.main(
        [
            "--source",
            "test",
            "--tickers",
            "AAA",
            "BBB",
            "--equity-sek",
            "200000",
            "--risk",
            "0.01",
            "--slippage",
            str(TP.slippage_bps),
            "--run-id",
            "cli1",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert out == tmp_path / "portfolio" / "test" / "cli1"
    summary = json.loads((out / "summary.json").read_text())
    assert summary["performance"]["all_trades"]["n"] == 2
    printed = capsys.readouterr().out
    assert "funktionstest" in printed and "run written to" in printed
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["args"]["commission"] == "tiered" and cfg["args"]["fx_mode"] == "per_trade"
    # Same inputs again -> identical summary (without run_id) and parquet files.
    out2 = cli.main(
        [
            "--source",
            "test",
            "--tickers",
            "BBB",
            "AAA",
            "--equity-sek",
            "200000",
            "--risk",
            "0.01",
            "--slippage",
            str(TP.slippage_bps),
            "--run-id",
            "cli2",
            "--data-dir",
            str(tmp_path),
        ]
    )
    s2 = json.loads((out2 / "summary.json").read_text())
    for k in ("run_id",):
        summary.pop(k), s2.pop(k)
    assert summary == s2
    assert pd.read_parquet(out / "trades.parquet").equals(pd.read_parquet(out2 / "trades.parquet"))
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--source",
                "test",
                "--tickers",
                "AAA",
                "--priority",
                "nope",
                "--data-dir",
                str(tmp_path),
            ]
        )
