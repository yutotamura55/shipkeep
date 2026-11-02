"""shipkeep-refactor の hotspots.py を、使い捨てのリポジトリで確かめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo

import hotspots


def paths(result: dict[str, Any]) -> list[str]:
    files = result["files"]
    assert isinstance(files, list)
    return [item["path"] for item in files]


def test_files_that_change_often_and_are_large_come_first(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    for i in range(3):
        repo.commit({"big.py": "x = 1\n" * (50 + i), "small.py": f"y = {i}\n"})
    repo.commit({"big.py": "x = 2\n" * 60})

    result = hotspots.hotspots(tmp_path)

    assert paths(result) == ["big.py", "small.py"]
    assert result["files"][0] == {"path": "big.py", "changes": 4, "lines": 60, "score": 240}
    assert result["commits_counted"] == 4


def test_files_changed_once_docs_generated_and_vendored_are_skipped(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    for i in range(2):
        repo.commit(
            {
                "README.md": f"# v{i}\n",
                "types.d.ts": f"declare const a{i}: number;\n",
                "node_modules/x/index.js": f"var a = {i};\n",
                "app.ts": f"export const a = {i};\n",
            }
        )
    repo.commit({"once.py": "x = 1\n"})

    assert paths(hotspots.hotspots(tmp_path)) == ["app.ts"]


def test_max_commits_limits_the_history_counted(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    for i in range(4):
        repo.commit({"a.py": f"x = {i}\n"})

    result = hotspots.hotspots(tmp_path, max_commits=2)

    assert result["commits_counted"] == 2
    assert result["files"] == [{"path": "a.py", "changes": 2, "lines": 1, "score": 2}]


def test_limit_caps_the_list(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    for i in range(2):
        repo.commit({f"m{n}.py": f"x = {i}\n" for n in range(5)})

    assert len(paths(hotspots.hotspots(tmp_path, limit=3))) == 3


def test_main_prints_json_without_the_local_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.py": "x = 1\n"})

    assert hotspots.main([str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["schema"] == 1
    assert str(tmp_path) not in captured.out + captured.err


def test_main_exits_with_2_without_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    GitRepo(tmp_path)

    assert hotspots.main([str(tmp_path)]) == 2
    assert "with commits" in capsys.readouterr().err


def test_main_rejects_a_non_positive_limit(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        hotspots.main([".", "--limit", "0"])

    assert exit_info.value.code == 2
