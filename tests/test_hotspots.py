"""shipkeep-refactor の hotspots.py を、使い捨てのリポジトリで確かめる。"""

from __future__ import annotations

import json
import os
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
    for i in range(2):
        repo.commit({"a.py": f"x = {i}\n"})

    assert hotspots.main([str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["files"][0]["path"] == "a.py"
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


def test_a_subdirectory_reports_paths_from_the_repository_root(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    for i in range(2):
        repo.commit({"sub/a.py": f"x = {i}\n", "b.py": f"y = {i}\n"})

    assert paths(hotspots.hotspots(tmp_path / "sub")) == ["b.py", "sub/a.py"]


@pytest.mark.parametrize("name", ['q"b.py', "tab\tname.py", "new\nline.py", "日本語.py"])
def test_unusual_file_names_are_counted(tmp_path: Path, name: str) -> None:
    repo = GitRepo(tmp_path)
    for i in range(2):
        repo.commit({name: f"x = {i}\n"})

    assert paths(hotspots.hotspots(tmp_path)) == [name]


def test_build_output_is_skipped(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    for i in range(2):
        repo.commit({"dist/bundle.js": f"var a = {i};\n" * 100, "src/app.ts": f"x({i});\n"})

    assert paths(hotspots.hotspots(tmp_path)) == ["src/app.ts"]


def test_a_failing_git_grep_is_an_error_not_an_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = GitRepo(tmp_path)
    for i in range(2):
        repo.commit({"a.py": f"x = {i}\n"})
    real_git = hotspots.git

    def failing_grep(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
        if args[0] == "grep":
            raise hotspots.HotspotError("git grep failed")
        return real_git(root, *args, allowed=allowed)

    monkeypatch.setattr(hotspots, "git", failing_grep)

    with pytest.raises(hotspots.HotspotError):
        hotspots.hotspots(tmp_path)


def test_a_non_utf8_file_name_is_counted(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    raw = os.fsdecode(b"caf\xe9.py")
    for i in range(2):
        (tmp_path / raw).write_text(f"x = {i}\n")
        repo.git("add", "--", raw)
        repo.commit({})

    result = hotspots.hotspots(tmp_path)

    assert paths(result) == ["caf\ufffd.py"]
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_a_partial_clone_is_refused(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.py": "x = 1\n"})
    repo.git("config", "extensions.partialClone", "origin")

    with pytest.raises(hotspots.HotspotError, match="partial clone"):
        hotspots.hotspots(tmp_path)


def test_argument_errors_do_not_echo_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        hotspots.main([".", "--max-commits", str(tmp_path)])

    assert str(tmp_path) not in capsys.readouterr().err


def test_main_exits_with_2_when_git_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    assert hotspots.main([str(tmp_path)]) == 2
    assert "git could not be started" in capsys.readouterr().err


def test_executable_returns_an_absolute_path_for_a_relative_path_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "bin" / "demo-tool"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")

    assert hotspots.executable("demo-tool") == str(tool)
