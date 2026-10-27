"""Scan a Git repository for problems to fix before making it public.

The scan only reads the repository. It prints one JSON document to standard
output, so that an agent can turn each check into a report row. The output
never contains local paths, because they include the local user name.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def is_git_repository(path: Path) -> bool:
    """Return whether ``path`` is inside a work tree or is a bare repository."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def scan(root: Path) -> dict[str, Any]:
    """Return the scan result for the repository at ``root``."""
    return {"schema": SCHEMA_VERSION, "checks": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("root", type=Path, help="path to the repository to scan")
    args = parser.parse_args(argv)
    root: Path = args.root
    if not root.is_dir() or not is_git_repository(root):
        print("not a Git repository: the given path", file=sys.stderr)
        return 2
    json.dump(scan(root), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
