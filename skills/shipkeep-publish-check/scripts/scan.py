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

Check = dict[str, Any]

LICENSE_NAMES = {"license", "license.md", "license.txt", "licence", "licence.md", "copying"}
README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}


def git(root: Path, *args: str) -> str:
    """Run a read-only Git command in ``root`` and return its standard output."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def has_commits(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def top_level_files(root: Path) -> list[str]:
    """Return the names of the files at the top level of the HEAD commit."""
    return git(root, "ls-tree", "--name-only", "HEAD").splitlines()


def result(check_id: str, status: str, summary: str) -> Check:
    return {"id": check_id, "status": status, "summary": summary, "findings": []}


def check_license(root: Path) -> Check:
    names = [name for name in top_level_files(root) if name.lower() in LICENSE_NAMES]
    if names:
        return result("license", "pass", f"HEAD has a license file: {names[0]}")
    return result("license", "fail", "HEAD has no license file at the top level")


def check_readme(root: Path) -> Check:
    names = [name for name in top_level_files(root) if name.lower() in README_NAMES]
    for name in names:
        if git(root, "show", f"HEAD:{name}").strip():
            return result("readme", "pass", f"HEAD has a README: {name}")
    if names:
        return result("readme", "fail", f"the README at HEAD is empty: {names[0]}")
    return result("readme", "fail", "HEAD has no README at the top level")


def is_git_repository(path: Path) -> bool:
    """Return whether ``path`` is inside a work tree or is a bare repository.

    Raises ``OSError`` when Git cannot be started.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def scan(root: Path) -> dict[str, Any]:
    """Return the scan result for the repository at ``root``."""
    if not has_commits(root):
        checks = [
            result(check_id, "unknown", "the repository has no commits")
            for check_id in ("license", "readme")
        ]
    else:
        checks = [check_license(root), check_readme(root)]
    return {"schema": SCHEMA_VERSION, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("root", type=Path, help="path to the repository to scan")
    args = parser.parse_args(argv)
    root: Path = args.root
    try:
        is_repository = root.is_dir() and is_git_repository(root)
    except OSError:
        # The exception message can contain local paths, so it is not printed.
        print("cannot run git: install Git and make sure it is on PATH", file=sys.stderr)
        return 2
    if not is_repository:
        print("not a Git repository: the given path", file=sys.stderr)
        return 2
    json.dump(scan(root), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
