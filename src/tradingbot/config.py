"""Minimal .env loader: KEY=VALUE lines into os.environ without overriding existing values.

Values are never logged. Kept dependency-free on purpose.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str = ".env") -> int:
    """Return the number of variables set. Missing file -> 0."""
    p = Path(path)
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n
