"""U0: archive today's instrument lists, append-only (UNIVERSE_DESIGN §10 U0, D-019). Examples:

uv run python scripts/snapshot_listings.py                       # nasdaq + alpaca (paper)
uv run python scripts/snapshot_listings.py --sources nasdaq
uv run python scripts/snapshot_listings.py --sources alpaca --alpaca-host live

Writes data/snapshots/<source>/<UTC stamp>/ with the raw bytes and manifest.json (status
complete | incomplete | failed, sha256, provider time, record count). Nothing is ever
overwritten; a failed run leaves a manifest too. Exit code 1 if any source is not complete.
Keys from .env (ALPACA_API_KEY / ALPACA_SECRET_KEY); they are never printed or stored.

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
from pathlib import Path

from tradingbot.config import load_dotenv
from tradingbot.data.snapshots import (
    COMPLETE,
    SnapshotStore,
    snapshot_alpaca_assets,
    snapshot_nasdaq,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--sources", nargs="+", choices=["nasdaq", "alpaca"], default=["nasdaq", "alpaca"]
    )
    ap.add_argument("--alpaca-host", choices=["paper", "live"], default="paper")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args(argv)
    load_dotenv()
    store = SnapshotStore(args.data_dir)
    rc = 0
    for src in args.sources:
        d = (
            snapshot_nasdaq(store)
            if src == "nasdaq"
            else snapshot_alpaca_assets(store, host=args.alpaca_host)
        )
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        files = ", ".join(
            f"{f['name']}: {f['bytes']} B, {f['record_count']} rows, sha {str(f['sha256'])[:12]}"
            + (f", ERROR {f['error']}" if f["error"] else "")
            for f in man["files"]
        )
        print(f"{man['source']:20s} {man['status']:10s} {d.name}  {files}")
        if man["status"] != COMPLETE:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
