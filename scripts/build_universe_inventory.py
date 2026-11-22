"""U2B-FREE-2B: import a local massive_tickers snapshot into an inventory of observations.

uv run python scripts/build_universe_inventory.py                      # latest complete snapshot
uv run python scripts/build_universe_inventory.py --snapshot data/snapshots/massive_tickers/<stamp>

Verifies the snapshot (complete, sha256, page and row counts), applies the strict common-stock
selection, assigns FIGI-based identities (surrogates for inventory only) and writes
data/universe_obs/<version>/observations.parquet + manifest.json (append-only). Prints the
ImportReport – aggregated counts only, never a ticker or an identifier. No network, no keys.
This is NOT a UniverseModel: one effective-dated snapshot has no start dates (module docstring).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tradingbot.data.snapshots import MASSIVE_SOURCE, SnapshotStore
from tradingbot.data.universe_massive import (
    SnapshotImportError,
    build_inventory,
    load_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--snapshot", type=Path, default=None, help="default: latest complete")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args(argv)
    store = SnapshotStore(args.data_dir)
    d = args.snapshot or store.latest_complete(MASSIVE_SOURCE)
    if d is None:
        print("no complete massive_tickers snapshot found", file=sys.stderr)
        return 1
    try:
        inv = build_inventory(load_snapshot(d))
        out = inv.write(args.data_dir)
    except SnapshotImportError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(json.dumps(inv.report.to_dict(), indent=2, ensure_ascii=False))
    print(f"{inv.version}  point_in_time={inv.point_in_time}  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
