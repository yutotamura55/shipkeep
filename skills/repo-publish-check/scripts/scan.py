"""Scan a Git repository for problems to fix before making it public.

The scan only reads the repository. It prints one JSON document to standard
output, so that an agent can turn each check into a report row.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def scan(root: Path) -> dict[str, Any]:
    """Return the scan result for the repository at ``root``."""
    return {"schema": SCHEMA_VERSION, "root": str(root), "checks": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="path to the repository to scan")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    if not (root / ".git").exists():
        print(f"not a Git repository: {root}", file=sys.stderr)
        return 2
    json.dump(scan(root), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
