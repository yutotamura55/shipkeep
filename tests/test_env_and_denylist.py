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
    repo.commit({"src/app.js": "require('dotenv').config()\n", ".gitignore": "node_modules/\n"})

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
            "context": "<term 2> project",
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


def test_env_files_ignore_environment_use_that_does_not_need_a_dotenv_file(
    tmp_path: Path,
) -> None:
    # Passing the environment to a child process, and test fixtures that mention
    # process.env, do not mean the project reads a .env file.
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "tool.py": "import os\nsubprocess.run(cmd, env={**os.environ})\n",
            "tests/test_app.py": "fixture = 'const key = process.env.KEY'\n",
        }
    )

    assert check(scan.scan(tmp_path), "env_files")["status"] == "not_applicable"


def test_env_files_apply_to_a_python_project_that_loads_dotenv(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app/settings.py": "from dotenv import load_dotenv\nload_dotenv()\n"})

    assert check(scan.scan(tmp_path), "env_files")["status"] == "fail"


def test_env_files_ignore_words_that_only_mention_dotenv(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "checker.py": 'USAGE = r"dotenv|env_file|process\\.env"\nCHECK_ID = "env_files"\n',
            "README.md": "This tool does not use dotenv or dotenv/config.\n",
        }
    )

    assert check(scan.scan(tmp_path), "env_files")["status"] == "not_applicable"


def test_env_files_apply_to_a_compose_file_with_an_env_file(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"compose.yaml": "services:\n  app:\n    env_file: .env\n"})

    assert check(scan.scan(tmp_path), "env_files")["status"] == "fail"


def test_env_files_ignore_code_that_only_names_dev_vars(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"checker.py": 'SENSITIVE = {".env", ".dev.vars"}\n'})

    assert check(scan.scan(tmp_path), "env_files")["status"] == "not_applicable"


def test_env_files_apply_to_a_wrangler_project(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"wrangler.jsonc": '{ "name": "demo" }\n', ".gitignore": ".dev.vars\n"})

    assert check(scan.scan(tmp_path), "env_files")["status"] == "fail"


def test_env_files_apply_to_code_that_imports_dotenv_config(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"src/index.js": "import 'dotenv/config'\n"})

    assert check(scan.scan(tmp_path), "env_files")["status"] == "fail"


def test_env_files_ignore_a_script_that_only_sets_process_env(tmp_path: Path) -> None:
    # Reading or setting process.env does not mean the project keeps a .env file.
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "scripts/e2e.mjs": "process.env.BROWSERS_PATH ??= '0'\n",
            "src/app.ts": "const key = import.meta.env.VITE_KEY\n",
        }
    )

    assert check(scan.scan(tmp_path), "env_files")["status"] == "not_applicable"


def test_env_files_apply_when_an_env_file_is_committed(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"api/.env": "KEY=1\n", "README.md": "# demo\n"})

    found = check(scan.scan(tmp_path), "env_files")

    assert found["status"] == "fail"
    assert found["findings"] == [
        {"kind": "env_not_ignored", "path": ".gitignore", "file": "api/.env"},
        {"kind": "env_example_missing", "file": "api/.env"},
    ]
