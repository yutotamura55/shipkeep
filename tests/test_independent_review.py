"""Cases found in the independent review of the scan."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from gitrepo import GitRepo

import scan

AWS_KEY_ID = "AKIA" + "Q" * 16
PERSONAL_EMAIL = "someone" + "@" + "mail.example.net"
HOME = "/home/" + "alice"
CLIENT_NAME = "Acme" + "Holdings"


def check(result: dict[str, object], check_id: str) -> dict[str, object]:
    checks = result["checks"]
    assert isinstance(checks, list)
    return next(c for c in checks if c["id"] == check_id)


def finding_list(found: dict[str, object]) -> list[dict[str, object]]:
    items = found["findings"]
    assert isinstance(items, list)
    return items


# 1. Paths that hold a secret or personal data are hidden as well.
def test_paths_with_personal_data_or_secrets_are_hidden(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {f"{PERSONAL_EMAIL}.txt": f"{AWS_KEY_ID}\n", f"keys/{AWS_KEY_ID}.txt": f"{AWS_KEY_ID}\n"}
    )

    output = json.dumps(scan.scan(tmp_path))

    assert PERSONAL_EMAIL not in output
    assert AWS_KEY_ID not in output


# 2. Control characters in a commit message cannot shift the records.
def test_a_message_with_control_characters_cannot_leak_a_term(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"}, message=f"start\x01{CLIENT_NAME}")

    result = scan.scan(tmp_path, denylist=[CLIENT_NAME])

    assert check(result, "denylist")["status"] == "fail"
    assert CLIENT_NAME not in json.dumps(result)


# 3. Undecodable Git output does not crash the scan.
def test_a_remote_url_that_is_not_utf8_does_not_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "a\n"})
    subprocess.run(
        [b"git", b"-C", bytes(tmp_path), b"config", b"remote.origin.url", b"https://h/\xff"],
        check=True,
    )

    assert scan.main([str(tmp_path)]) == 0
    assert "Traceback" not in capsys.readouterr().err


# 4. Text conversion commands configured in the repository never run.
def test_textconv_commands_are_not_run(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path / "repo")
    repo.commit({".gitattributes": "*.txt diff=conv\n", "a.txt": f"{AWS_KEY_ID}\n"})
    marker = tmp_path / "textconv-ran"
    repo.git("config", "diff.conv.textconv", f"touch {marker}; cat")

    found = check(scan.scan(tmp_path / "repo"), "history_secrets")

    assert not marker.exists()
    assert found["status"] == "fail"


# 5. Lines and files that only a merge commit adds are searched.
def test_a_secret_added_while_resolving_a_merge_conflict_is_found(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": "base\n"})
    repo.git("switch", "--quiet", "-c", "side")
    repo.commit({"a.txt": "side\n"})
    repo.git("switch", "--quiet", "main")
    repo.commit({"a.txt": "main\n"})
    repo.git("merge", "--quiet", "side", check=False)  # stops on the conflict
    (tmp_path / "a.txt").write_text(f"{AWS_KEY_ID}\n")
    repo.git("add", "a.txt")
    merge = repo.commit({".env": "KEY=1\n"}, message="merge side")
    assert len(repo.git("rev-list", "--parents", "-n", "1", merge).split()) == 3

    secrets = finding_list(check(scan.scan(tmp_path), "history_secrets"))
    files = finding_list(check(scan.scan(tmp_path), "sensitive_files"))

    assert [(f["commit"], f["path"], f["line"]) for f in secrets] == [(merge[:12], "a.txt", 1)]
    assert [(f["commit"], f["path"]) for f in files] == [(merge[:12], ".env")]


# 6. A -diff attribute does not hide text.
def test_text_marked_as_binary_is_still_searched(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({".gitattributes": "secret.txt -diff\n", "secret.txt": f"key {AWS_KEY_ID}\n"})

    assert check(scan.scan(tmp_path), "history_secrets")["status"] == "fail"


# 7. Incomplete local history is reported instead of passing.
@pytest.mark.parametrize(
    ("options", "expected"),
    [([], "pass"), (["--depth", "1"], "unknown"), (["--single-branch"], "unknown")],
)
def test_history_coverage_depends_on_how_the_repository_was_cloned(
    tmp_path: Path, options: list[str], expected: str
) -> None:
    source = GitRepo(tmp_path / "source")
    source.commit({"a.txt": "a\n"})
    source.commit({"b.txt": "b\n"})
    source.git("branch", "other")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", *options, f"file://{tmp_path / 'source'}", str(clone)],
        check=True,
    )

    assert check(scan.scan(clone), "history_coverage")["status"] == expected


# 8. Lock files are exempt from address checks only.
def test_local_paths_in_lock_files_are_found(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"package-lock.json": f'{{"resolved": "file:{HOME}/lib"}}\n'})

    assert check(scan.scan(tmp_path), "history_personal_data")["status"] == "fail"


# 9. Home paths in commit messages.
def test_a_home_path_in_a_commit_message_is_found(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({"a.txt": "a\n"}, message=f"copy from {HOME}/old")

    found = check(scan.scan(tmp_path), "history_personal_data")

    assert finding_list(found) == [{"kind": "local_path_in_commit_message", "commit": commit[:12]}]


# 11. Certificates are public; private key contents are found by the secret check.
def test_a_certificate_file_is_not_a_sensitive_file(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"certs/server.pem": "-----BEGIN " + "CERTIFICATE-----\nMIIB\n"})

    assert check(scan.scan(tmp_path), "sensitive_files")["status"] == "pass"


# 12. Env files below the top level.
@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({".gitignore": "/.env\n"}, "fail"),
        ({".gitignore": ".env\n"}, "pass"),
        ({".gitignore": "node_modules/\n", "api/.gitignore": ".env\n"}, "pass"),
    ],
)
def test_env_files_in_subdirectories_must_be_ignored(
    tmp_path: Path, files: dict[str, str], expected: str
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"api/.env.example": "KEY=\n", **files})

    assert check(scan.scan(tmp_path), "env_files")["status"] == expected


# 14. Each finding with a line carries the line with the values masked.
def test_findings_carry_the_line_with_values_masked(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "config.py": f"key = '{AWS_KEY_ID}'\n",
            "notes.md": f"mail {PERSONAL_EMAIL} from {HOME}/demo\n",
            "plan.md": f"pitch to {CLIENT_NAME} with token=abcdefghijklmnopqrstuvwxyz0123\n",
        }
    )

    result = scan.scan(tmp_path, denylist=[CLIENT_NAME])

    contexts = {
        str(f["path"]): f["context"]
        for c in ("history_secrets", "history_personal_data", "denylist")
        for f in finding_list(check(result, c))
    }
    assert contexts == {
        "config.py": "key = '<aws_access_key_id>'",
        "notes.md": "mail <email_address> from <local_path>",
        "plan.md": "pitch to <term 1> with token=<redacted>",
    }


# 18. Argument errors do not echo what was given.
def test_argument_errors_do_not_echo_the_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        scan.main(["repo", f"{HOME}/secret-project"])

    assert stopped.value.code == 2
    assert "alice" not in capsys.readouterr().err


# Second round of the independent review.


def contexts_of(result: dict[str, object], check_id: str) -> list[object]:
    return [f["context"] for f in finding_list(check(result, check_id))]


def test_context_masks_values_after_assignments_and_inside_quotes(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.env": f"AWS={AWS_KEY_ID} PASSWORD=hunter2 note='short pw'\n"})

    contexts = contexts_of(scan.scan(tmp_path), "history_secrets")

    assert contexts == ["AWS=<aws_access_key_id> PASSWORD=<redacted> note='<redacted>'"]


def test_context_masks_the_whole_local_path(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"notes.md": f"open {HOME}/client-x/plan.md now\n"})

    contexts = contexts_of(scan.scan(tmp_path), "history_personal_data")

    assert contexts == ["open <local_path> now"]


def test_control_characters_in_content_cannot_forge_commit_records(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"data.txt": f"x\v\0commit {CLIENT_NAME}\n{AWS_KEY_ID}\n"})

    result = scan.scan(tmp_path, denylist=[CLIENT_NAME])

    assert check(result, "history_secrets")["status"] == "fail"
    assert CLIENT_NAME not in json.dumps(result)


def test_a_negative_fetch_refspec_makes_coverage_unknown(tmp_path: Path) -> None:
    source = GitRepo(tmp_path / "source")
    source.commit({"a.txt": "a\n"})
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", f"file://{tmp_path / 'source'}", str(clone)], check=True
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "--add", "remote.origin.fetch", "^refs/heads/old"],
        check=True,
    )

    assert check(scan.scan(clone), "history_coverage")["status"] == "unknown"


@pytest.mark.parametrize(
    ("env_path", "expected"),
    [("apps/team/.env.example", "pass"), ("apps/team/api/.env.example", "fail")],
)
def test_a_single_star_does_not_cross_directories(
    tmp_path: Path, env_path: str, expected: str
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({env_path: "KEY=\n", ".gitignore": "apps/*/.env\n"})

    assert check(scan.scan(tmp_path), "env_files")["status"] == expected


def test_env_use_in_a_subproject_does_not_require_a_root_env(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "api/compose.yaml": "services:\n  app:\n    env_file: .env\n",
            "api/.env.example": "KEY=\n",
            "api/.gitignore": ".env\n",
        }
    )

    assert check(scan.scan(tmp_path), "env_files")["status"] == "pass"


# Third round of the independent review.


def test_carriage_returns_in_content_cannot_forge_commit_records(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    fake = "\0commit " + "a" * 40
    repo.commit({"data.txt": f"x\r{fake}\r\ndiff --git a/b b/b\n{AWS_KEY_ID}\n"})

    assert check(scan.scan(tmp_path), "history_secrets")["status"] == "fail"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            f'AWS={AWS_KEY_ID} pw="it\'s secret"',
            'AWS=<aws_access_key_id> pw="<redacted>"',
        ),
        (f"AWS={AWS_KEY_ID} pw=abc,def;ghi", "AWS=<aws_access_key_id> pw=<redacted>"),
        (f"AWS={AWS_KEY_ID} pw=<hunter2>", "AWS=<aws_access_key_id> pw=<redacted>"),
    ],
)
def test_context_masks_values_in_unusual_forms(tmp_path: Path, line: str, expected: str) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.env": line + "\n"})

    assert contexts_of(scan.scan(tmp_path), "history_secrets") == [expected]


def test_context_masks_a_local_path_with_spaces_inside_brackets(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"notes.md": f"see [plan]({HOME}/Client X/plan v2.md) today\n"})

    contexts = contexts_of(scan.scan(tmp_path), "history_personal_data")

    assert contexts == ["see [plan](<local_path>) today"]


@pytest.mark.parametrize("marker", [None, "Pipfile"])
def test_env_use_in_a_subproject_without_a_known_manifest(
    tmp_path: Path, marker: str | None
) -> None:
    repo = GitRepo(tmp_path)
    files = {
        "api/app.py": "from dotenv import load_dotenv\nload_dotenv()\n",
        "api/.gitignore": ".env\n",
    }
    if marker is None:
        files["api/.env.example"] = "KEY=\n"
    else:
        files[f"api/{marker}"] = "[packages]\n"
        files["api/.env.example"] = "KEY=\n"
    repo.commit(files)

    assert check(scan.scan(tmp_path), "env_files")["status"] == "pass"
