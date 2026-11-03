from __future__ import annotations

from pathlib import Path

from gitrepo import GitRepo

import scan


def status_of(result: dict[str, object], check_id: str) -> str:
    checks = result["checks"]
    assert isinstance(checks, list)
    for check in checks:
        if check["id"] == check_id:
            return str(check["status"])
    raise AssertionError(f"check {check_id} is missing")


def test_license_passes_when_head_has_a_license_file(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"LICENSE": "MIT License\n"})

    assert status_of(scan.scan(tmp_path), "license") == "pass"


def test_license_fails_when_head_has_no_license_file(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "# demo\n"})

    assert status_of(scan.scan(tmp_path), "license") == "fail"


def test_license_ignores_a_license_file_that_is_not_committed(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "# demo\n"})
    (tmp_path / "LICENSE").write_text("MIT License\n")

    assert status_of(scan.scan(tmp_path), "license") == "fail"


def test_readme_passes_when_head_has_a_non_empty_readme(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "# demo\n"})

    assert status_of(scan.scan(tmp_path), "readme") == "pass"


def test_readme_fails_when_the_readme_is_blank(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "  \n\n"})

    assert status_of(scan.scan(tmp_path), "readme") == "fail"


def test_readme_fails_when_there_is_no_readme(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"LICENSE": "MIT License\n"})

    assert status_of(scan.scan(tmp_path), "readme") == "fail"


def test_files_are_unknown_in_a_repository_without_commits(tmp_path: Path) -> None:
    GitRepo(tmp_path)

    result = scan.scan(tmp_path)

    assert status_of(result, "license") == "unknown"
    assert status_of(result, "readme") == "unknown"
