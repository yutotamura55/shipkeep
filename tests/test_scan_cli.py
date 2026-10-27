from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scan


def test_scan_prints_json_for_a_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)

    assert scan.main([str(tmp_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == scan.SCHEMA_VERSION
    assert result["checks"] == []


def test_scan_rejects_a_directory_that_is_not_a_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert scan.main([str(tmp_path)]) == 2
    assert "not a Git repository" in capsys.readouterr().err
