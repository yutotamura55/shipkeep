from __future__ import annotations

import json
from pathlib import Path

from gitrepo import GitRepo

import scan

# Fake secrets and personal data are joined at run time, so that this file
# holds nothing that a secret scanner or a reader would take for the real thing.
AWS_KEY_ID = "AKIA" + "Q" * 16
GITHUB_TOKEN = "ghp" + "_" + "a1B2" * 9
PRIVATE_KEY_HEADER = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
PERSONAL_EMAIL = "someone" + "@" + "mail.example.net"
HOME_PATH = "/home/" + "alice" + "/projects/demo"


def check(root: Path, check_id: str) -> dict[str, object]:
    checks = scan.scan(root)["checks"]
    assert isinstance(checks, list)
    return next(c for c in checks if c["id"] == check_id)


def findings(found: dict[str, object]) -> list[dict[str, object]]:
    items = found["findings"]
    assert isinstance(items, list)
    return items


def test_secrets_pass_for_a_clean_history(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "API_KEY = os.environ['API_KEY']\n"})

    assert check(tmp_path, "history_secrets")["status"] == "pass"


def test_secrets_report_the_commit_path_and_line_of_a_key(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({"config.py": f"region = 'x'\n\nkey = '{AWS_KEY_ID}'\n"})

    found = check(tmp_path, "history_secrets")

    assert found["status"] == "fail"
    assert findings(found) == [
        {
            "kind": "aws_access_key_id",
            "commit": commit[:12],
            "path": "config.py",
            "line": 3,
            "context": "key = '<aws_access_key_id>'",
        }
    ]


def test_secrets_find_a_key_that_a_later_commit_deleted(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"token.txt": GITHUB_TOKEN + "\n"})
    repo.commit({"token.txt": ""})

    found = check(tmp_path, "history_secrets")

    assert found["status"] == "fail"
    assert [f["kind"] for f in findings(found)] == ["github_token"]


def test_secrets_find_a_private_key(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"deploy/key": PRIVATE_KEY_HEADER + "\nabc\n"})

    assert [f["kind"] for f in findings(check(tmp_path, "history_secrets"))] == ["private_key"]


def test_secrets_are_never_printed(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": f"{AWS_KEY_ID}\n{GITHUB_TOKEN}\n"})

    output = json.dumps(scan.scan(tmp_path))

    assert AWS_KEY_ID not in output
    assert GITHUB_TOKEN not in output


def test_personal_data_passes_for_noreply_and_example_addresses(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "README.md": "Contact 1+me@users.noreply.github.com or user@example.com.\n"
            "Clone git@github.com:owner/repo.git into /home/user/repo.\n"
        },
        message="docs\n\nCo-Authored-By: Bot <noreply@anthropic.com>",
    )

    assert check(tmp_path, "history_personal_data")["status"] == "pass"


def test_personal_data_finds_an_address_and_a_home_path(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({"notes.md": f"mail {PERSONAL_EMAIL}\nsee {HOME_PATH}\n"})

    found = check(tmp_path, "history_personal_data")

    assert found["status"] == "fail"
    assert findings(found) == [
        {
            "kind": "email_address",
            "commit": commit[:12],
            "path": "notes.md",
            "line": 1,
            "context": "mail <email_address>",
        },
        {
            "kind": "local_path",
            "commit": commit[:12],
            "path": "notes.md",
            "line": 2,
            "context": "see <local_path>",
        },
    ]


def test_personal_data_finds_an_address_in_a_commit_message(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({"a.txt": "a\n"}, message=f"fix\n\nReported-by: {PERSONAL_EMAIL}")

    found = check(tmp_path, "history_personal_data")

    assert findings(found) == [{"kind": "email_in_commit_message", "commit": commit[:12]}]


def test_personal_data_is_never_printed(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.txt": f"{PERSONAL_EMAIL} {HOME_PATH}\n"}, message=PERSONAL_EMAIL)

    output = json.dumps(scan.scan(tmp_path))

    assert PERSONAL_EMAIL not in output
    assert "alice" not in output


def test_sensitive_files_pass_for_example_env_files(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({".env.example": "API_KEY=\n", "app.py": "print(1)\n"})

    assert check(tmp_path, "sensitive_files")["status"] == "pass"


def test_sensitive_files_find_files_that_were_added_and_later_deleted(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    first = repo.commit({".env": "API_KEY=x\n", "infra/terraform.tfstate": "{}\n"})
    repo.commit({".env": "", "infra/terraform.tfstate": ""})

    found = check(tmp_path, "sensitive_files")

    assert found["status"] == "fail"
    assert sorted(findings(found), key=lambda f: str(f["path"])) == [
        {"kind": "env_file", "commit": first[:12], "path": ".env"},
        {"kind": "terraform_state", "commit": first[:12], "path": "infra/terraform.tfstate"},
    ]


def test_personal_data_ignores_package_authors_in_lock_files(tmp_path: Path) -> None:
    # Lock files copy the published contact of third-party package authors.
    repo = GitRepo(tmp_path)
    repo.commit({"package-lock.json": f'{{"author": "{PERSONAL_EMAIL}"}}\n'})

    assert check(tmp_path, "history_personal_data")["status"] == "pass"


def test_secrets_are_still_searched_in_lock_files(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"package-lock.json": f'{{"token": "{GITHUB_TOKEN}"}}\n'})

    assert check(tmp_path, "history_secrets")["status"] == "fail"


def test_personal_data_ignores_text_that_only_looks_like_a_home_path(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"scan.py": 're.compile(r"/Users/([^/\\s]+)/")\n'})

    assert check(tmp_path, "history_personal_data")["status"] == "pass"
