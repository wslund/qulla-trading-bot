"""Quality summary per source from stored metadata (no network)."""

from __future__ import annotations

from collections import Counter

from tradingbot.data.store import ParquetStore


def quality_rows(store: ParquetStore, source: str) -> list[dict]:
    rows = []
    for ticker in store.list_tickers(source):
        m = store.read_meta(source, ticker)
        rows.append(
            {
                "source": source,
                "ticker": ticker,
                "rows": m.get("rows", 0),
                "start": m.get("actual_start") or "-",
                "end": m.get("actual_end") or "-",
                "status": m.get("quality_status", "UNKNOWN"),
                "issues": m.get("quality_issues", []),
                "fetched_at": m.get("fetched_at", "-"),
            }
        )
    return rows


def render_markdown(store: ParquetStore, sources: list[str] | None = None) -> str:
    """Markdown report: one table per source plus a status count line."""
    out = ["# Datakvalitet per leverantör", ""]
    for source in sources or store.list_sources():
        rows = quality_rows(store, source)
        counts = Counter(r["status"] for r in rows)
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "inga serier"
        out += [f"## {source} ({summary})", ""]
        out += ["| Ticker | Rader | Från | Till | Status | Fynd |", "|---|---|---|---|---|---|"]
        for r in rows:
            issues = "<br>".join(r["issues"]) if r["issues"] else "-"
            out.append(
                f"| {r['ticker']} | {r['rows']} | {r['start']} | {r['end']} | "
                f"{r['status']} | {issues} |"
            )
        out.append("")
    return "\n".join(out)
