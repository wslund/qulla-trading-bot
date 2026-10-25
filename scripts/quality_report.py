"""Print a Markdown quality report from stored metadata. Example:

uv run python scripts/quality_report.py > data/quality_report.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.data.report import render_markdown
from tradingbot.data.store import ParquetStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", nargs="*", default=None)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    args = ap.parse_args()
    print(render_markdown(ParquetStore(args.data_dir), args.sources))


if __name__ == "__main__":
    main()
