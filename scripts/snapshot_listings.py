"""U0: archive today's instrument lists, append-only (UNIVERSE_DESIGN §10 U0, D-019). Examples:

uv run python scripts/snapshot_listings.py                       # nasdaq + alpaca (paper)
uv run python scripts/snapshot_listings.py --sources nasdaq
uv run python scripts/snapshot_listings.py --sources alpaca --alpaca-host live
uv run python scripts/snapshot_listings.py --sources massive --date 2026-11-01   # U2B-FREE-1

Writes data/snapshots/<source>/<UTC stamp>/ with the raw bytes and manifest.json (status
complete | incomplete | failed, sha256, provider time, record count). Nothing is ever
overwritten; a failed run leaves a manifest too. Exit code 1 if any source is not complete.
Keys from .env (ALPACA_API_KEY / ALPACA_SECRET_KEY / MASSIVE_API_KEY); they are never printed
or stored.

Massive (--sources massive, requires --date): archives /v3/reference/tickers for one
effective date, active and inactive lists, every page raw, at most one call per
--massive-min-interval seconds (default 12 s = the Basic plan's 5 calls/min; a full date takes
a few minutes). The result is labelled effective_dated_reconstructed – Massive's current record
of a past date, not a verified point-in-time snapshot (UNIVERSE_DESIGN §9.3). Not scheduled by
the timer below; run it by hand per date.

Scheduling (manual, nothing is installed by this script) – systemd user timer on CachyOS:

  ~/.config/systemd/user/qulla-snapshots.service
    [Service]
    Type=oneshot
    WorkingDirectory=%h/Repos/qulla-trading-bot
    ExecStart=%h/.local/bin/uv run python scripts/snapshot_listings.py
  ~/.config/systemd/user/qulla-snapshots.timer
    [Timer]
    OnCalendar=Mon..Fri 23:30 Europe/Stockholm      # after the US close, before Nasdaq's next
    Persistent=true                                 # rebuild of the files (early morning ET)
    [Install]
    WantedBy=timers.target
  systemctl --user enable --now qulla-snapshots.timer ; journalctl --user -u qulla-snapshots

Two files a day for Nasdaq and one JSON for Alpaca: a few MB per year. data/ is git-ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from tradingbot.config import load_dotenv
from tradingbot.data.snapshots import (
    COMPLETE,
    MASSIVE_MIN_INTERVAL,
    SnapshotStore,
    snapshot_alpaca_assets,
    snapshot_massive_tickers,
    snapshot_nasdaq,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--sources",
        nargs="+",
        choices=["nasdaq", "alpaca", "massive"],
        default=["nasdaq", "alpaca"],
    )
    ap.add_argument("--alpaca-host", choices=["paper", "live"], default="paper")
    ap.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="effective date for --sources massive (YYYY-MM-DD); required for massive",
    )
    ap.add_argument(
        "--massive-min-interval",
        type=float,
        default=MASSIVE_MIN_INTERVAL,
        help="seconds between Massive calls (Basic plan: 5/min → 12 s). Do not lower on Basic.",
    )
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args(argv)
    if "massive" in args.sources and args.date is None:
        ap.error("--sources massive requires --date YYYY-MM-DD")
    load_dotenv()
    store = SnapshotStore(args.data_dir)
    rc = 0
    for src in args.sources:
        if src == "nasdaq":
            d = snapshot_nasdaq(store)
        elif src == "alpaca":
            d = snapshot_alpaca_assets(store, host=args.alpaca_host)
        else:
            d = snapshot_massive_tickers(
                store, effective_date=args.date, min_interval=args.massive_min_interval
            )
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        files = ", ".join(
            f"{f['name']}: {f['bytes']} B, {f['record_count']} rows, sha {str(f['sha256'])[:12]}"
            + (f", ERROR {f['error']}" if f["error"] else "")
            for f in man["files"]
        )
        print(f"{man['source']:20s} {man['status']:10s} {d.name}  {files}")
        if src == "massive":
            x = man["extra"]
            print(
                f"  effective_date={x['effective_date']} label={x['label']} "
                f"active={x['active_count']} inactive={x['inactive_count']} "
                f"pages={man['pages']} slept={x.get('slept_s')}s "
                f"last_updated_utc {x['last_updated_utc_min']}..{x['last_updated_utc_max']}"
            )
        if man["status"] != COMPLETE:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
