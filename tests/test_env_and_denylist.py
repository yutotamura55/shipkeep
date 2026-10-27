from __future__ import annotations

import json
from pathlib import Path

import pytest
from gitrepo import GitRepo

import scan

# A business name the user does not want to publish, kept out of this file's text.
CLIENT_NAME = "Acme" + "Holdings"


def check(result: dict[str, object], check_id: str) -> dict[str, object]:
    checks = result["checks"]
    assert isinstance(checks, list)
    return next(c for c in checks if c["id"] == check_id)


def kinds(found: dict[str, object]) -> list[str]:
    items = found["findings"]
    assert isinstance(items, list)
    return sorted(str(f["kind"]) for f in items)


def test_env_files_do_not_apply_without_environment_variables(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"main.py": "print('hello')\n"})

    assert check(scan.scan(tmp_path), "env_files")["status"] == "not_applicable"


def test_env_files_pass_with_an_ignored_env_and_an_example(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "main.py": "import os\ntoken = os.environ['TOKEN']\n",
            ".gitignore": "__pycache__/\n.env\n",
            ".env.example": "TOKEN=\n",
        }
    )

    assert check(scan.scan(tmp_path), "env_files")["status"] == "pass"


def test_env_files_accept_a_wildcard_pattern_and_a_sample_file(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "src/app.ts": "const key = process.env.KEY\n",
            ".gitignore": "/.env*\n!.env.sample\n",
            ".env.sample": "KEY=\n",
        }
    )

    assert check(scan.scan(tmp_path), "env_files")["status"] == "pass"


def test_env_files_fail_without_an_ignore_rule_and_an_example(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"src/app.ts": "const key = process.env.KEY\n", ".gitignore": "node_modules/\n"})

    found = check(scan.scan(tmp_path), "env_files")

    assert found["status"] == "fail"
    assert kinds(found) == ["env_example_missing", "env_not_ignored"]


def test_denylist_does_not_apply_without_a_list(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": f"{CLIENT_NAME}\n"})

    assert check(scan.scan(tmp_path), "denylist")["status"] == "not_applicable"


def test_denylist_passes_when_no_term_appears(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "nothing to hide\n"})

    result = scan.scan(tmp_path, denylist=[CLIENT_NAME])

    assert check(result, "denylist")["status"] == "pass"


def test_denylist_reports_terms_in_content_and_messages_by_their_number(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    in_file = repo.commit({"notes.md": "intro\n" + CLIENT_NAME.lower() + " project\n"})
    in_message = repo.commit({"b.txt": "b\n"}, message=f"Prepare the {CLIENT_NAME} demo")

    found = check(scan.scan(tmp_path, denylist=["unused term", CLIENT_NAME]), "denylist")

    assert found["status"] == "fail"
    assert found["findings"] == [
        {
            "kind": "term_in_content",
            "term": 2,
            "commit": in_file[:12],
            "path": "notes.md",
            "line": 2,
        },
        {"kind": "term_in_commit_message", "term": 2, "commit": in_message[:12]},
    ]
    assert CLIENT_NAME.lower() not in json.dumps(found).lower()


def test_cli_reads_the_denylist_file_and_skips_comments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = GitRepo(repo_path)
    repo.commit({"a.txt": f"{CLIENT_NAME}\n"})
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"# client names\n\n{CLIENT_NAME}\n")

    assert scan.main([str(repo_path), "--denylist", str(denylist)]) == 0

    found = check(json.loads(capsys.readouterr().out), "denylist")
    assert found["status"] == "fail"
    items = found["findings"]
    assert isinstance(items, list)
    assert [f["term"] for f in items] == [1]
