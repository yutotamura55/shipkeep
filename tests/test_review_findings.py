"""scan の最初の版の自己レビューで見つかった事例。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from gitrepo import GitRepo

import scan

AWS_KEY_ID = "AKIA" + "Q" * 16
CLIENT_NAME = "Acme" + "Holdings"


def check(result: dict[str, object], check_id: str) -> dict[str, object]:
    checks = result["checks"]
    assert isinstance(checks, list)
    return next(c for c in checks if c["id"] == check_id)


def status(root: Path, check_id: str) -> object:
    return check(scan.scan(root), check_id)["status"]


def finding_list(found: dict[str, object]) -> list[dict[str, object]]:
    items = found["findings"]
    assert isinstance(items, list)
    return items


# 1. 利用者の Git の設定で、検査が読む内容を変えない。
@pytest.mark.parametrize(
    "setting",
    [("diff.noprefix", "true"), ("log.showRoot", "false"), ("diff.mnemonicPrefix", "true")],
)
def test_history_scan_ignores_git_settings_that_change_the_patch(
    tmp_path: Path, setting: tuple[str, str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"config.py": f"key = '{AWS_KEY_ID}'\n"})
    repo.git("config", *setting)

    assert status(tmp_path, "history_secrets") == "fail"


# 2. 作業ツリーのサブディレクトリを指定しても、リポジトリ全体を調べる。
def test_scanning_a_subdirectory_reads_the_top_of_the_repository(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"LICENSE": "MIT\n", "README.md": "# demo\n", "sub/notes.md": "x\n"})

    result = scan.scan(tmp_path / "sub")

    assert check(result, "license")["status"] == "pass"
    assert check(result, "readme")["status"] == "pass"


def test_cli_scans_a_subdirectory_without_a_readme_of_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"sub/README.md": "# sub\n"})

    assert scan.main([str(tmp_path / "sub")]) == 0
    assert check(json.loads(capsys.readouterr().out), "readme")["status"] == "fail"


# 3. BOM 付きや別の文字コードで保存した禁止語のファイル。
def test_denylist_file_with_a_bom_matches_its_first_term(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    GitRepo(repo_path).commit({"a.txt": f"{CLIENT_NAME}\n"})
    denylist = tmp_path / "denylist.txt"
    denylist.write_bytes("\ufeff".encode() + f"{CLIENT_NAME}\n".encode())

    assert scan.main([str(repo_path), "--denylist", str(denylist)]) == 0
    assert check(json.loads(capsys.readouterr().out), "denylist")["status"] == "fail"


def test_denylist_file_that_is_not_utf8_stops_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    GitRepo(repo_path).commit({"a.txt": "a\n"})
    denylist = tmp_path / "denylist.txt"
    denylist.write_bytes("顧客名\n".encode("shift_jis"))

    assert scan.main([str(repo_path), "--denylist", str(denylist)]) == 2
    err = capsys.readouterr().err
    assert "UTF-8" in err
    assert "Traceback" not in err
    assert str(tmp_path) not in err


# 4. .gitignore のパターン。
@pytest.mark.parametrize(("pattern", "expected"), [("**/.env\n", "pass"), (".env/\n", "fail")])
def test_env_ignore_rule_follows_gitignore_semantics(
    tmp_path: Path, pattern: str, expected: str
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "src/app.ts": "const k = process.env.KEY\n",
            ".gitignore": pattern,
            ".env.example": "KEY=\n",
        }
    )

    assert status(tmp_path, "env_files") == expected


# 5. Wrangler のプロジェクトは秘密情報を .dev.vars に置く。
def test_wrangler_project_passes_when_dev_vars_is_ignored(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "wrangler.toml": 'name = "demo"\n',
            ".gitignore": ".dev.vars\n",
            ".dev.vars.example": "KEY=\n",
        }
    )

    assert status(tmp_path, "env_files") == "pass"


def test_wrangler_project_fails_when_only_env_is_ignored(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {"wrangler.toml": 'name = "demo"\n', ".gitignore": ".env\n", ".dev.vars.example": "KEY=\n"}
    )

    found = check(scan.scan(tmp_path), "env_files")

    assert found["status"] == "fail"
    assert finding_list(found) == [
        {"kind": "env_not_ignored", "path": ".gitignore", "file": ".dev.vars"}
    ]


# 6. ファイル名とブランチ名の禁止語。
def test_denylist_finds_a_term_in_a_file_name_and_hides_it(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({f"docs/{CLIENT_NAME}-proposal.md": "draft\n"})

    found = check(scan.scan(tmp_path, denylist=[CLIENT_NAME]), "denylist")

    assert found["status"] == "fail"
    assert finding_list(found) == [{"kind": "term_in_path", "term": 1, "commit": commit[:12]}]


def test_findings_never_show_a_path_that_contains_a_denylist_term(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({f"{CLIENT_NAME}/.env": f"KEY={AWS_KEY_ID}\n"})

    output = json.dumps(scan.scan(tmp_path, denylist=[CLIENT_NAME]))

    assert CLIENT_NAME not in output


def test_denylist_finds_a_term_in_a_branch_name(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"})
    repo.git("branch", f"feature/{CLIENT_NAME.lower()}")

    found = check(scan.scan(tmp_path, denylist=[CLIENT_NAME]), "denylist")

    assert finding_list(found) == [{"kind": "term_in_ref", "term": 1}]


# 7. 行末や引用符の前にあるホームのパス。
@pytest.mark.parametrize(
    "line", ["HOME=/home/" + "alice", 'path = "/Users/' + 'alice"', "cd C:\\Users\\" + "alice"]
)
def test_home_paths_without_a_trailing_slash_are_found(tmp_path: Path, line: str) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"notes.md": line + "\n"})

    assert status(tmp_path, "history_personal_data") == "fail"


# 8. ヘッダーに見える patch の行と、空白や引用符を含むファイル名。
def test_content_that_looks_like_a_patch_header_does_not_hide_later_lines(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({"notes.md": "++ b/elsewhere\n" + f"key = '{AWS_KEY_ID}'\n"})

    found = check(scan.scan(tmp_path), "history_secrets")

    assert finding_list(found) == [
        {
            "kind": "aws_access_key_id",
            "commit": commit[:12],
            "path": "notes.md",
            "line": 2,
            "context": "key = '<aws_access_key_id>'",
        }
    ]


@pytest.mark.parametrize("name", ["my notes.md", 'say "hi".md'])
def test_file_names_with_spaces_or_quotes_are_read(tmp_path: Path, name: str) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({name: f"key = '{AWS_KEY_ID}'\n"})

    found = check(scan.scan(tmp_path), "history_secrets")

    assert [f["path"] for f in finding_list(found)] == [name]


# 9. 調べるのは push で公開されるものだけ。ブランチ、タグ、origin のリモート。
def test_stashed_work_is_not_scanned(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"config.py": "key = None\n"})
    (tmp_path / "config.py").write_text(f"key = '{AWS_KEY_ID}'\n")
    repo.git("stash", "--quiet")

    assert status(tmp_path, "history_secrets") == "pass"


def test_branches_of_other_remotes_are_not_scanned_but_origin_is(tmp_path: Path) -> None:
    upstream = GitRepo(tmp_path / "upstream")
    upstream.commit({"a.txt": f"{AWS_KEY_ID}\n"})
    repo = GitRepo(tmp_path / "repo")
    repo.commit({"b.txt": "b\n"})
    repo.git("fetch", "--quiet", str(tmp_path / "upstream"), "main:refs/remotes/upstream/main")

    assert status(tmp_path / "repo", "history_secrets") == "pass"

    repo.git("fetch", "--quiet", str(tmp_path / "upstream"), "main:refs/remotes/origin/old")

    assert status(tmp_path / "repo", "history_secrets") == "fail"


# 10. アドレスに見える素材のファイル名。
def test_retina_image_names_are_not_addresses(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"index.html": '<img src="logo@2x.png" srcset="bg@3x.webp">\n'})

    assert status(tmp_path, "history_personal_data") == "pass"


def test_git_failure_stops_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"})
    # オブジェクトが欠けていると、履歴を読むコマンドがすべて失敗する。
    objects = tmp_path / ".git" / "objects"
    for folder in objects.iterdir():
        if len(folder.name) == 2:
            for item in folder.iterdir():
                item.chmod(0o600)
                item.unlink()
    subprocess.run(["git", "-C", str(tmp_path), "status"], capture_output=True, check=False)

    assert scan.main([str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(tmp_path) not in err
