from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scan


def git_init(path: Path, *options: str) -> None:
    subprocess.run(["git", "init", "--quiet", *options, str(path)], check=True)


def test_scan_prints_json_for_a_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git_init(tmp_path)

    assert scan.main([str(tmp_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == 1
    assert result["checks"], "the scan reports at least one check"
    for check in result["checks"]:
        assert check["status"] in {"pass", "fail", "unknown", "not_applicable"}


def test_scan_output_does_not_contain_the_local_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git_init(tmp_path)

    scan.main([str(tmp_path)])

    captured = capsys.readouterr()
    assert str(tmp_path) not in captured.out
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize("layout", ["subdirectory", "bare"])
def test_scan_accepts_other_repository_layouts(
    tmp_path: Path, layout: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if layout == "bare":
        target = tmp_path / "repo.git"
        git_init(target, "--bare")
    else:
        git_init(tmp_path)
        target = tmp_path / "sub"
        target.mkdir()

    assert scan.main([str(target)]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("kind", ["plain directory", "missing path", "file"])
def test_scan_rejects_a_path_that_is_not_a_repository(
    tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    if kind == "plain directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("not a repository\n")

    assert scan.main([str(target)]) == 2
    captured = capsys.readouterr()
    assert "not a Git repository" in captured.err
    assert str(tmp_path) not in captured.err


def test_scan_reports_missing_git_without_a_traceback_or_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git_init(tmp_path)
    monkeypatch.setenv("PATH", "")

    assert scan.main([str(tmp_path)]) == 2

    captured = capsys.readouterr()
    assert "cannot run git" in captured.err
    assert "Traceback" not in captured.err
    assert str(tmp_path) not in captured.err
