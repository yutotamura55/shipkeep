"""Scan a Git repository for problems to fix before making it public.

The scan only reads the repository. It looks at what a push publishes: the
local branches and tags, and the branches of the origin remote, which become
public too when a repository on GitHub is made public. It prints one JSON
document to standard output, so that an agent can turn each check into a
report row.

The output never contains the value of a secret, personal data, a denylist
term, or a local path: findings give the kind and the location only.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1

Check = dict[str, Any]
Finding = dict[str, Any]

# Settings that would change the output this script parses. They are fixed on
# every Git command, so that the user's configuration cannot hide a finding.
GIT_SETTINGS = (
    "core.quotePath=false",
    "color.ui=never",
    "diff.noprefix=false",
    "diff.mnemonicPrefix=false",
    "diff.relative=false",
    "log.showRoot=true",
    "log.showSignature=false",
)
# What a push publishes, and what becomes public when an existing GitHub
# repository is made public. Stashes and other remotes are left out.
PUBLISHED_REFS = ("--branches", "--tags", "--remotes=origin")
PUBLISHED_REF_PREFIXES = ("refs/heads", "refs/tags", "refs/remotes/origin")

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
# Asset names such as logo@2x.png look like addresses.
FILE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "svg",
    "webp",
    "avif",
    "ico",
    "bmp",
    "tif",
    "tiff",
    "pdf",
    "css",
    "js",
    "mp4",
    "webm",
    "woff",
    "woff2",
    "ttf",
    "otf",
}
USER_NAME = r"([A-Za-z0-9_.-]+)(?![A-Za-z0-9_.-])"
HOME_PATHS = (
    re.compile(r"/home/" + USER_NAME),
    re.compile(r"/Users/" + USER_NAME),
    re.compile(r"[A-Za-z]:\\+Users\\+" + USER_NAME),
)
# User names that appear in documentation and CI rather than on a real machine.
GENERIC_USER_NAMES = {"user", "username", "runner", "ubuntu", "app", "node", "vscode", "shared"}

# Lock files copy the published contact details of third-party package authors,
# so they are searched for secrets but not for personal data.
LOCK_FILE_NAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
}

ENV_EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")
PRIVATE_KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
PRIVATE_KEY_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
CREDENTIAL_FILE_NAMES = {"credentials.json", ".netrc", ".pypirc"}

GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

# Code and configuration that load a .env file. The patterns match the call or
# the key, not the bare word, so that text that only mentions dotenv does not
# count. Reading os.environ or process.env is left out: it also reads variables
# set by the shell or CI, so it does not show that a .env file exists.
ENV_FILE_USAGE = (
    r"load_dotenv\(|dotenv_values\(|require\(['\"]dotenv['\"]\)|['\"]dotenv/config['\"]"
    r"|from ['\"]dotenv['\"]|^\s*env_file\s*[:=]"
)
DOTENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
# Cloudflare Workers projects keep local secrets in .dev.vars.
WRANGLER_CONFIG_NAMES = {"wrangler.toml", "wrangler.json", "wrangler.jsonc"}
WRANGLER_EXAMPLE_NAMES = {".dev.vars.example"}
TEST_PATHSPECS = (
    ":(exclude,glob)**/tests/**",
    ":(exclude,glob)**/test/**",
    ":(exclude,glob)**/__tests__/**",
    ":(exclude,glob)**/test_*.py",
    ":(exclude,glob)**/*.test.*",
    ":(exclude,glob)**/*.spec.*",
)

HIDDEN_PATH = "(hidden: the path contains a denylist term)"


def git_command(root: Path, *args: str) -> list[str]:
    command = ["git", "-C", str(root)]
    for setting in GIT_SETTINGS:
        command += ["-c", setting]
    return [*command, *args]


def git(root: Path, *args: str) -> str:
    """Run a read-only Git command in ``root`` and return its standard output."""
    completed = subprocess.run(
        git_command(root, *args),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout


def git_succeeds(root: Path, *args: str) -> bool:
    completed = subprocess.run(git_command(root, *args), capture_output=True, check=False)
    return completed.returncode == 0


def repository_root(path: Path) -> Path:
    """Return the top of the work tree that contains ``path``, or ``path`` for a bare one."""
    if git(path, "rev-parse", "--is-bare-repository").strip() == "true":
        return path
    return Path(git(path, "rev-parse", "--show-toplevel").strip())


def has_commits(root: Path) -> bool:
    return git_succeeds(root, "rev-parse", "--verify", "--quiet", "HEAD")


def result(
    check_id: str, status: str, summary: str, findings: list[Finding] | None = None
) -> Check:
    return {"id": check_id, "status": status, "summary": summary, "findings": findings or []}


def unquote_path(text: str) -> str:
    """Undo the C-style quoting Git applies to unusual file names."""
    if text.startswith('"') and text.endswith('"'):
        unquoted = ast.literal_eval(text)
        if isinstance(unquoted, str):
            return unquoted
    return text


@dataclass(frozen=True)
class AddedLine:
    commit: str
    path: str
    line: int
    text: str


def added_lines(root: Path) -> Iterator[AddedLine]:
    """Yield every line that a published commit added, with its location."""
    log = git(
        root,
        "log",
        *PUBLISHED_REFS,
        "--patch",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--format=%x00commit %H",
    )
    commit = ""
    path: str | None = None
    in_header = False
    line = 0
    for text in log.splitlines():
        if text.startswith("\0commit "):
            commit = text.removeprefix("\0commit ")[:12]
            path, in_header = None, False
        elif text.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and text.startswith("+++ "):
            # Git ends the name with a tab when it contains a space.
            name = unquote_path(text.removeprefix("+++ ").rstrip("\t"))
            path = name.removeprefix("b/") if name.startswith("b/") else None
        elif text.startswith("@@ "):
            in_header = False
            match = re.search(r"\+(\d+)", text)
            line = int(match.group(1)) if match else 0
        elif not in_header and text.startswith("+") and path is not None:
            yield AddedLine(commit, path, line, text[1:])
            line += 1


def added_files(root: Path) -> Iterator[tuple[str, str]]:
    """Yield the abbreviated hash and path of every file that a published commit added."""
    log = git(
        root,
        "log",
        *PUBLISHED_REFS,
        "--diff-filter=A",
        "--name-only",
        "--no-renames",
        "--format=%x00%H",
    )
    commit = ""
    for line in log.splitlines():
        if line.startswith("\0"):
            commit = line[1:13]
        elif line:
            yield commit, unquote_path(line)


def commit_messages(root: Path) -> Iterator[tuple[str, str]]:
    """Yield the abbreviated hash and the full message of every published commit."""
    log = git(root, "log", *PUBLISHED_REFS, "--format=%H%x00%B%x01")
    for entry in log.split("\x01"):
        commit, _, message = entry.strip("\n").partition("\0")
        if commit:
            yield commit[:12], message


def published_ref_names(root: Path) -> list[str]:
    return git(root, "for-each-ref", "--format=%(refname)", *PUBLISHED_REF_PREFIXES).splitlines()


def is_noreply_address(address: str) -> bool:
    address = address.lower()
    return address.endswith("@users.noreply.github.com") or address == "noreply@github.com"


def is_public_address(address: str) -> bool:
    """Return whether an address-like string in content is safe to publish."""
    local, _, domain = address.lower().rpartition("@")
    return (
        is_noreply_address(address)
        or "noreply" in local
        or "no-reply" in local
        or local == "git"
        or domain in EXAMPLE_DOMAINS
        or domain.rsplit(".", 1)[-1] in FILE_EXTENSIONS
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


def matching_terms(text: str, denylist: list[str]) -> list[int]:
    """Return the 1-based numbers of the denylist terms that appear in ``text``."""
    lowered = text.lower()
    return [number for number, term in enumerate(denylist, 1) if term.lower() in lowered]


def check_commit_identity(root: Path) -> Check:
    """Find commits whose author or committer address is not a GitHub noreply address."""
    findings = []
    log = git(root, "log", *PUBLISHED_REFS, "--format=%H%x00%ae%x00%ce")
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


def check_history_content(root: Path, denylist: list[str] | None) -> list[Check]:
    """Search added lines, file names, messages, and refs for secrets, personal data, and terms."""
    terms_to_find = denylist or []
    secrets: list[Finding] = []
    personal: list[Finding] = []
    terms: list[Finding] = []
    for added in added_lines(root):
        location = {"commit": added.commit, "path": added.path, "line": added.line}
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(added.text):
                secrets.append({"kind": kind, **location})
        if PurePosixPath(added.path).name not in LOCK_FILE_NAMES:
            for kind in personal_data_kinds(added.text):
                personal.append({"kind": kind, **location})
        for number in matching_terms(added.text, terms_to_find):
            terms.append({"kind": "term_in_content", "term": number, **location})
    for commit, path in added_files(root):
        for number in matching_terms(path, terms_to_find):
            terms.append({"kind": "term_in_path", "term": number, "commit": commit})
    for commit, message in commit_messages(root):
        if any(not is_public_address(address) for address in EMAIL.findall(message)):
            personal.append({"kind": "email_in_commit_message", "commit": commit})
        for number in matching_terms(message, terms_to_find):
            terms.append({"kind": "term_in_commit_message", "term": number, "commit": commit})
    for ref in published_ref_names(root):
        for number in matching_terms(ref, terms_to_find):
            terms.append({"kind": "term_in_ref", "term": number})

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
    if denylist is None:
        terms_check = result("denylist", "not_applicable", "no denylist was given")
    elif terms:
        terms_check = result(
            "denylist",
            "fail",
            f"{len(terms)} place(s) in the history contain a denylist term",
            terms,
        )
    else:
        terms_check = result("denylist", "pass", "no denylist term appears in the history")
    return [secrets_check, personal_check, terms_check]


def check_sensitive_files(root: Path) -> Check:
    """Find files that should never be committed, in every commit that added one."""
    findings = [
        {"kind": kind, "commit": commit, "path": path}
        for commit, path in added_files(root)
        if (kind := sensitive_file_kind(path))
    ]
    if findings:
        return result(
            "sensitive_files",
            "fail",
            f"{len(findings)} file(s) that should not be published were committed",
            findings,
        )
    return result("sensitive_files", "pass", "no file that should not be published was committed")


def tree_paths(root: Path, *options: str) -> list[str]:
    """Return the paths in the HEAD commit, from the top of the repository."""
    listing = git(root, "ls-tree", *options, "--full-tree", "--name-only", "HEAD")
    return [unquote_path(path) for path in listing.splitlines()]


def reads_env_file(root: Path) -> bool:
    """Return whether code outside the tests at HEAD shows signs of reading a .env file."""
    return git_succeeds(
        root, "grep", "-I", "-q", "-E", ENV_FILE_USAGE, "HEAD", "--", ":(top)", *TEST_PATHSPECS
    )


def gitignore_ignores(root: Path, paths: list[str], name: str) -> bool:
    """Return whether the top-level .gitignore at HEAD ignores a top-level file ``name``."""
    if ".gitignore" not in paths:
        return False
    ignored = False
    for raw in git(root, "show", "HEAD:.gitignore").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negated = pattern.startswith("!")
        pattern = pattern.removeprefix("!")
        if pattern.endswith("/"):
            continue  # matches directories only
        pattern = pattern.removeprefix("**/").removeprefix("/")
        if "/" in pattern:
            continue  # anchored below the top level
        if fnmatch.fnmatchcase(name, pattern):
            ignored = not negated
    return ignored


def check_env_files(root: Path) -> Check:
    """Check that a project that keeps secrets in a local file ignores it and commits an example."""
    paths = tree_paths(root, "-r")
    names = {PurePosixPath(path).name for path in paths}
    required = []
    if names & DOTENV_EXAMPLE_NAMES or reads_env_file(root):
        required.append((".env", DOTENV_EXAMPLE_NAMES))
    if names & (WRANGLER_CONFIG_NAMES | WRANGLER_EXAMPLE_NAMES):
        required.append((".dev.vars", WRANGLER_EXAMPLE_NAMES))
    if not required:
        return result("env_files", "not_applicable", "the project does not read a local env file")
    findings: list[Finding] = []
    for file, examples in required:
        if not gitignore_ignores(root, paths, file):
            findings.append({"kind": "env_not_ignored", "path": ".gitignore", "file": file})
        if not names & examples:
            findings.append({"kind": "env_example_missing", "file": file})
    if findings:
        return result(
            "env_files", "fail", "local env files are not set up for publishing", findings
        )
    return result("env_files", "pass", "local env files are ignored and examples are committed")


def check_license(root: Path) -> Check:
    names = [name for name in tree_paths(root) if name.lower() in LICENSE_NAMES]
    if names:
        return result("license", "pass", f"HEAD has a license file: {names[0]}")
    return result("license", "fail", "HEAD has no license file at the top level")


def check_readme(root: Path) -> Check:
    names = [name for name in tree_paths(root) if name.lower() in README_NAMES]
    for name in names:
        if git(root, "show", f"HEAD:{name}").strip():
            return result("readme", "pass", f"HEAD has a README: {name}")
    if names:
        return result("readme", "fail", f"the README at HEAD is empty: {names[0]}")
    return result("readme", "fail", "HEAD has no README at the top level")


def github_repository(root: Path) -> str | None:
    """Return "owner/name" of the origin remote when it is on GitHub."""
    completed = subprocess.run(
        git_command(root, "config", "--get", "remote.origin.url"),
        capture_output=True,
        text=True,
        check=False,
    )
    match = GITHUB_REMOTE.match(completed.stdout.strip())
    return match.group(1) if match else None


def fetch_security_settings(repository: str) -> dict[str, Any] | None:
    """Read the security settings of a GitHub repository with the gh CLI.

    Returns None when gh is missing, not signed in, or cannot see the repository.
    The error output of gh is discarded because it can contain local details.
    """
    try:
        completed = subprocess.run(
            ["gh", "api", f"repos/{repository}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    settings = data.get("security_and_analysis") if isinstance(data, dict) else None
    return settings if isinstance(settings, dict) else {}


def check_github_settings(
    root: Path,
    fetch: Callable[[str], dict[str, Any] | None] = fetch_security_settings,
) -> Check:
    """Check that GitHub secret scanning and push protection are on for the origin remote."""
    repository = github_repository(root)
    if repository is None:
        return result("github_settings", "not_applicable", "the origin remote is not on GitHub")
    settings = fetch(repository)
    if settings is None:
        return result("github_settings", "unknown", "the settings could not be read with gh")
    features = (
        ("secret_scanning", "secret_scanning_disabled"),
        ("secret_scanning_push_protection", "push_protection_disabled"),
    )
    if any(not isinstance(settings.get(name), dict) for name, _ in features):
        return result(
            "github_settings", "unknown", "GitHub does not show the secret scanning settings"
        )
    findings: list[Finding] = [
        {"kind": kind} for name, kind in features if settings[name].get("status") != "enabled"
    ]
    if findings:
        return result(
            "github_settings", "fail", "secret scanning or push protection is off", findings
        )
    return result("github_settings", "pass", "secret scanning and push protection are on")


def hide_paths_with_terms(checks: list[Check], denylist: list[str]) -> None:
    """Replace every finding path that contains a denylist term, so the output never shows one."""
    for check in checks:
        for finding in check["findings"]:
            path = finding.get("path")
            if isinstance(path, str) and matching_terms(path, denylist):
                finding["path"] = HIDDEN_PATH


CHECK_IDS = (
    "commit_identity",
    "history_secrets",
    "history_personal_data",
    "denylist",
    "sensitive_files",
    "env_files",
    "license",
    "readme",
)


def scan(root: Path, denylist: list[str] | None = None) -> dict[str, Any]:
    """Return the scan result for the repository that contains ``root``.

    ``denylist`` holds terms that must not appear in the history; findings refer
    to a term by its 1-based position in the list, never by its text.
    """
    root = repository_root(root)
    if not has_commits(root):
        checks = [
            result(check_id, "unknown", "the repository has no commits") for check_id in CHECK_IDS
        ]
    else:
        checks = [
            check_commit_identity(root),
            *check_history_content(root, denylist),
            check_sensitive_files(root),
            check_env_files(root),
            check_license(root),
            check_readme(root),
        ]
    checks.append(check_github_settings(root))
    hide_paths_with_terms(checks, denylist or [])
    counts = {
        status: sum(1 for check in checks if check["status"] == status)
        for status in ("pass", "fail", "unknown", "not_applicable")
    }
    return {"schema": SCHEMA_VERSION, "counts": counts, "checks": checks}


def is_git_repository(path: Path) -> bool:
    """Return whether ``path`` is inside a work tree or is a bare repository.

    Raises ``OSError`` when Git cannot be started.
    """
    return git_succeeds(path, "rev-parse", "--git-dir")


def read_denylist(path: Path) -> list[str]:
    """Read one term per line; blank lines and lines starting with '#' are skipped.

    Raises ``OSError`` when the file cannot be read, and ``UnicodeDecodeError``
    when it is not UTF-8 text.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [term.strip() for term in lines if term.strip() and not term.strip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("root", type=Path, help="path to the repository to scan")
    parser.add_argument(
        "--denylist",
        type=Path,
        help="file with one term per line that must not appear in the history",
    )
    args = parser.parse_args(argv)
    # Error messages are fixed text: exception messages can contain local paths.
    denylist = None
    if args.denylist is not None:
        try:
            denylist = read_denylist(args.denylist)
        except UnicodeDecodeError:
            print("the denylist file must be UTF-8 text", file=sys.stderr)
            return 2
        except OSError:
            print("cannot read the denylist file", file=sys.stderr)
            return 2
    root: Path = args.root
    try:
        is_repository = root.is_dir() and is_git_repository(root)
    except OSError:
        print("cannot run git: install Git and make sure it is on PATH", file=sys.stderr)
        return 2
    if not is_repository:
        print("not a Git repository: the given path", file=sys.stderr)
        return 2
    try:
        output = scan(root, denylist)
    except subprocess.CalledProcessError:
        print("git failed while reading the repository", file=sys.stderr)
        return 2
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
