"""Scan a Git repository for problems to fix before making it public.

The scan only reads the repository. It looks at what is committed (HEAD and
the history of every branch), because that is what a push publishes. It prints
one JSON document to standard output, so that an agent can turn each check
into a report row.

The output never contains the value of a secret or of personal data, and never
contains local paths: findings give the kind and the location only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1

Check = dict[str, Any]
Finding = dict[str, Any]

LICENSE_NAMES = {"license", "license.md", "license.txt", "licence", "licence.md", "copying"}
README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}

SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "llm_api_key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "stripe_secret_key": re.compile(r"\b[rs]k_live_[0-9A-Za-z]{24,}"),
}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Documentation domains reserved by RFC 2606. Only the exact domains are public;
# a subdomain such as mail.example.net is still reported.
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net"}
HOME_PATHS = (
    re.compile(r"/home/([A-Za-z_][A-Za-z0-9_.-]*)/"),
    re.compile(r"/Users/([^/\s]+)/"),
    re.compile(r"[A-Za-z]:\\+Users\\+([^\\\s]+)"),
)
# User names that appear in documentation and CI rather than on a real machine.
GENERIC_USER_NAMES = {"user", "username", "runner", "ubuntu", "app", "node", "vscode", "shared"}

ENV_EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")
PRIVATE_KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
PRIVATE_KEY_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
CREDENTIAL_FILE_NAMES = {"credentials.json", ".netrc", ".pypirc"}


def git(root: Path, *args: str) -> str:
    """Run a read-only Git command in ``root`` and return its standard output."""
    completed = subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotePath=false", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout


def has_commits(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def result(
    check_id: str, status: str, summary: str, findings: list[Finding] | None = None
) -> Check:
    return {"id": check_id, "status": status, "summary": summary, "findings": findings or []}


@dataclass(frozen=True)
class AddedLine:
    commit: str
    path: str
    line: int
    text: str


def added_lines(root: Path) -> Iterator[AddedLine]:
    """Yield every line that any commit on any branch added, with its location."""
    log = git(
        root,
        "log",
        "--all",
        "--patch",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "--format=%x00commit %H",
    )
    commit = ""
    path: str | None = None
    line = 0
    for text in log.splitlines():
        if text.startswith("\0commit "):
            commit = text.removeprefix("\0commit ")[:12]
            path = None
        elif text.startswith("+++ "):
            path = text.removeprefix("+++ b/") if text.startswith("+++ b/") else None
        elif text.startswith("@@ "):
            match = re.search(r"\+(\d+)", text)
            line = int(match.group(1)) if match else 0
        elif text.startswith("+") and path is not None:
            yield AddedLine(commit, path, line, text[1:])
            line += 1


def commit_messages(root: Path) -> Iterator[tuple[str, str]]:
    """Yield the abbreviated hash and the full message of every commit on any branch."""
    log = git(root, "log", "--all", "--format=%H%x00%B%x01")
    for entry in log.split("\x01"):
        commit, _, message = entry.strip("\n").partition("\0")
        if commit:
            yield commit[:12], message


def is_noreply_address(address: str) -> bool:
    address = address.lower()
    return address.endswith("@users.noreply.github.com") or address == "noreply@github.com"


def is_public_address(address: str) -> bool:
    """Return whether an address in content is safe to publish."""
    local, _, domain = address.lower().rpartition("@")
    return (
        is_noreply_address(address)
        or "noreply" in local
        or "no-reply" in local
        or local == "git"
        or domain in EXAMPLE_DOMAINS
    )


def personal_data_kinds(text: str) -> list[str]:
    kinds = []
    if any(not is_public_address(address) for address in EMAIL.findall(text)):
        kinds.append("email_address")
    for pattern in HOME_PATHS:
        if any(name.lower() not in GENERIC_USER_NAMES for name in pattern.findall(text)):
            kinds.append("local_path")
            break
    return kinds


def sensitive_file_kind(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name in {".env", ".dev.vars"} or (
        name.startswith(".env.") and not name.endswith(ENV_EXAMPLE_SUFFIXES)
    ):
        return "env_file"
    if name.endswith((".tfstate", ".tfstate.backup")):
        return "terraform_state"
    if name.endswith(".tfvars"):
        return "terraform_variables"
    if name in PRIVATE_KEY_FILE_NAMES or name.endswith(PRIVATE_KEY_FILE_SUFFIXES):
        return "private_key_file"
    if name in CREDENTIAL_FILE_NAMES:
        return "credentials_file"
    return None


def check_commit_identity(root: Path) -> Check:
    """Find commits whose author or committer address is not a GitHub noreply address."""
    findings = []
    log = git(root, "log", "--all", "--format=%H%x00%ae%x00%ce")
    for line in log.splitlines():
        commit, author, committer = line.split("\0")
        for kind, address in (("author_email", author), ("committer_email", committer)):
            if not is_noreply_address(address):
                findings.append({"kind": kind, "commit": commit[:12]})
    if findings:
        commits = len({f["commit"] for f in findings})
        return result(
            "commit_identity",
            "fail",
            f"{commits} commit(s) record an address that is not a GitHub noreply address",
            findings,
        )
    return result("commit_identity", "pass", "every commit uses a GitHub noreply address")


def check_history_content(root: Path) -> list[Check]:
    """Search every added line for secrets and personal data, and commit messages for addresses."""
    secrets: list[Finding] = []
    personal: list[Finding] = []
    for added in added_lines(root):
        location = {"commit": added.commit, "path": added.path, "line": added.line}
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(added.text):
                secrets.append({"kind": kind, **location})
        for kind in personal_data_kinds(added.text):
            personal.append({"kind": kind, **location})
    for commit, message in commit_messages(root):
        if any(not is_public_address(address) for address in EMAIL.findall(message)):
            personal.append({"kind": "email_in_commit_message", "commit": commit})

    if secrets:
        secrets_check = result(
            "history_secrets", "fail", f"{len(secrets)} added line(s) look like secrets", secrets
        )
    else:
        secrets_check = result(
            "history_secrets", "pass", "no added line matches the built-in secret patterns"
        )
    if personal:
        personal_check = result(
            "history_personal_data",
            "fail",
            f"{len(personal)} place(s) in the history contain personal data",
            personal,
        )
    else:
        personal_check = result(
            "history_personal_data", "pass", "no personal address or local home path was found"
        )
    return [secrets_check, personal_check]


def check_sensitive_files(root: Path) -> Check:
    """Find files that should never be committed, in every commit that added one."""
    findings = []
    commit = ""
    log = git(
        root, "log", "--all", "--diff-filter=A", "--name-only", "--no-renames", "--format=%x00%H"
    )
    for line in log.splitlines():
        if line.startswith("\0"):
            commit = line[1:13]
        elif line and (kind := sensitive_file_kind(line)):
            findings.append({"kind": kind, "commit": commit, "path": line})
    if findings:
        return result(
            "sensitive_files",
            "fail",
            f"{len(findings)} file(s) that should not be published were committed",
            findings,
        )
    return result("sensitive_files", "pass", "no file that should not be published was committed")


def top_level_files(root: Path) -> list[str]:
    """Return the names of the files at the top level of the HEAD commit."""
    return git(root, "ls-tree", "--name-only", "HEAD").splitlines()


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


CHECK_IDS = (
    "commit_identity",
    "history_secrets",
    "history_personal_data",
    "sensitive_files",
    "license",
    "readme",
)


def scan(root: Path) -> dict[str, Any]:
    """Return the scan result for the repository at ``root``."""
    if not has_commits(root):
        checks = [
            result(check_id, "unknown", "the repository has no commits") for check_id in CHECK_IDS
        ]
    else:
        checks = [
            check_commit_identity(root),
            *check_history_content(root),
            check_sensitive_files(root),
            check_license(root),
            check_readme(root),
        ]
    return {"schema": SCHEMA_VERSION, "checks": checks}


def is_git_repository(path: Path) -> bool:
    """Return whether ``path`` is inside a work tree or is a bare repository.

    Raises ``OSError`` when Git cannot be started.
    """
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


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
