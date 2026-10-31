from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo

import scan

ENABLED = {"status": "enabled"}
DISABLED = {"status": "disabled"}


class FakeGitHub:
    """テストから呼べない GitHub API の代わりをする。"""

    def __init__(self, settings: dict[str, Any] | None) -> None:
        self.settings = settings
        self.requested: list[str] = []

    def __call__(self, repository: str) -> dict[str, Any] | None:
        self.requested.append(repository)
        return self.settings


def settings_check(root: Path, github: FakeGitHub) -> dict[str, Any]:
    found = scan.check_github_settings(root, fetch=github)
    assert found["id"] == "github_settings"
    return found


def repo_with_remote(path: Path, url: str | None) -> GitRepo:
    repo = GitRepo(path)
    repo.commit({"a.txt": "a\n"})
    if url is not None:
        repo.git("remote", "add", "origin", url)
    return repo


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octo/demo.git",
        "https://github.com/octo/demo",
        "git@github.com:octo/demo.git",
        "ssh://git@github.com/octo/demo.git",
    ],
)
def test_settings_are_requested_for_the_github_remote(tmp_path: Path, url: str) -> None:
    repo_with_remote(tmp_path, url)
    github = FakeGitHub({"secret_scanning": ENABLED, "secret_scanning_push_protection": ENABLED})

    settings_check(tmp_path, github)

    assert github.requested == ["octo/demo"]


def test_settings_do_not_apply_without_a_github_remote(tmp_path: Path) -> None:
    repo_with_remote(tmp_path, "https://gitlab.com/octo/demo.git")
    github = FakeGitHub(None)

    assert settings_check(tmp_path, github)["status"] == "not_applicable"
    assert github.requested == []


def test_settings_pass_when_scanning_and_push_protection_are_on(tmp_path: Path) -> None:
    repo_with_remote(tmp_path, "https://github.com/octo/demo.git")
    github = FakeGitHub({"secret_scanning": ENABLED, "secret_scanning_push_protection": ENABLED})

    assert settings_check(tmp_path, github)["status"] == "pass"


def test_settings_fail_and_name_each_disabled_feature(tmp_path: Path) -> None:
    repo_with_remote(tmp_path, "https://github.com/octo/demo.git")
    github = FakeGitHub({"secret_scanning": DISABLED, "secret_scanning_push_protection": DISABLED})

    found = settings_check(tmp_path, github)

    assert found["status"] == "fail"
    assert [f["kind"] for f in found["findings"]] == [
        "secret_scanning_disabled",
        "push_protection_disabled",
    ]


def test_settings_are_unknown_when_github_cannot_be_asked(tmp_path: Path) -> None:
    repo_with_remote(tmp_path, "https://github.com/octo/demo.git")

    assert settings_check(tmp_path, FakeGitHub(None))["status"] == "unknown"


def test_settings_are_unknown_when_github_hides_them(tmp_path: Path) -> None:
    # 秘密情報のスキャンがないプランの非公開リポジトリは、これらの項目を返さない。
    repo_with_remote(tmp_path, "https://github.com/octo/demo.git")

    assert settings_check(tmp_path, FakeGitHub({}))["status"] == "unknown"


def test_scan_includes_the_settings_check(tmp_path: Path) -> None:
    repo_with_remote(tmp_path, None)

    checks = scan.scan(tmp_path)["checks"]

    assert any(c["id"] == "github_settings" for c in checks)
