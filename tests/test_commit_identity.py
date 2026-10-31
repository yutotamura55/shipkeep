from __future__ import annotations

import json
from pathlib import Path

from gitrepo import GitRepo

import scan

# 個人のものに見えるアドレスをソースに残さないよう、実行時に組み立てる。
PERSONAL_EMAIL = "someone" + "@" + "mail.example.net"


def check(result: dict[str, object], check_id: str) -> dict[str, object]:
    checks = result["checks"]
    assert isinstance(checks, list)
    return next(c for c in checks if c["id"] == check_id)


def test_identity_passes_when_every_commit_uses_a_noreply_address(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"})
    repo.commit({"b.txt": "b\n"}, email="noreply@github.com")

    assert check(scan.scan(tmp_path), "commit_identity")["status"] == "pass"


def test_identity_fails_and_names_the_commit_with_a_personal_address(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"})
    bad = repo.commit({"b.txt": "b\n"}, email=PERSONAL_EMAIL)

    found = check(scan.scan(tmp_path), "commit_identity")

    assert found["status"] == "fail"
    findings = found["findings"]
    assert isinstance(findings, list)
    assert {f["commit"] for f in findings} == {bad[:12]}
    assert {f["kind"] for f in findings} == {"author_email", "committer_email"}


def test_identity_checks_commits_on_every_branch(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"})
    repo.git("switch", "--quiet", "-c", "side")
    repo.commit({"b.txt": "b\n"}, email=PERSONAL_EMAIL)
    repo.git("switch", "--quiet", "main")

    assert check(scan.scan(tmp_path), "commit_identity")["status"] == "fail"


def test_identity_never_prints_the_address(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"}, email=PERSONAL_EMAIL)

    assert PERSONAL_EMAIL not in json.dumps(scan.scan(tmp_path))
