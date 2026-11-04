"""ASCII でない文字で始まるユーザー名のホームのパス (#19)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from gitrepo import GitRepo

import scan

# 偽の秘密情報は実行時に連結する。秘密情報のスキャナーに本物と受け取られないため。
AWS_KEY_ID = "AKIA" + "Q" * 16
# 実在の人の名前にしない。「テスト」と、ASCII でない文字で始まる造語。
NON_ASCII_HOMES = [
    "/home/" + "テスト" + "/projects/demo",
    "/Users/" + "ünit-dev" + "/demo",
    "C:\\Users\\" + "テスト" + "\\demo",
    "/home/" + "\U0001f9ea" + "test/demo",
    "/home/" + "\u0301" + "test/demo",
]


def check(root: Path, check_id: str) -> dict[str, object]:
    checks = scan.scan(root)["checks"]
    assert isinstance(checks, list)
    return next(c for c in checks if c["id"] == check_id)


@pytest.mark.parametrize("home", NON_ASCII_HOMES)
def test_a_home_path_with_a_non_ascii_user_name_is_found(tmp_path: Path, home: str) -> None:
    repo = GitRepo(tmp_path)
    commit = repo.commit({"notes.md": f"see {home}\n"})

    found = check(tmp_path, "history_personal_data")

    assert found["status"] == "fail"
    assert found["findings"] == [
        {
            "kind": "local_path",
            "commit": commit[:12],
            "path": "notes.md",
            "line": 1,
            "context": "see <local_path>",
        }
    ]


@pytest.mark.parametrize("home", NON_ASCII_HOMES)
def test_a_non_ascii_home_path_is_masked_on_a_secret_line(tmp_path: Path, home: str) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"config.txt": f"key {AWS_KEY_ID} from {home} now\n"})

    found = check(tmp_path, "history_secrets")
    findings = found["findings"]
    assert isinstance(findings, list)

    assert [item["context"] for item in findings] == [
        "key <aws_access_key_id> from <local_path> now"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "see (/home/user) and /home/runner/work",
        "見て /home/user、それから",
        "例えば /home/userのように書く",
    ],
)
def test_generic_user_names_stay_unreported_next_to_punctuation(tmp_path: Path, line: str) -> None:
    """ユーザー名の文字を広げても、閉じ括弧や全角の句読点を名前に含めない。"""
    repo = GitRepo(tmp_path)
    repo.commit({"notes.md": line + "\n"})

    assert check(tmp_path, "history_personal_data")["status"] == "pass"


def test_a_home_path_with_a_user_name_that_is_not_utf8_is_found(tmp_path: Path) -> None:
    """UTF-8 でないバイトで始まるユーザー名 (例: Shift_JIS で書かれた行) も見つける。"""
    repo = GitRepo(tmp_path)
    (tmp_path / "notes.md").write_bytes(b"see /home/\x83e\x83X\x83g/demo\n")
    repo.git("add", "notes.md")
    repo.git("commit", "--quiet", "-m", "change")

    found = check(tmp_path, "history_personal_data")

    assert found["status"] == "fail"
    findings = found["findings"]
    assert isinstance(findings, list)
    assert [(item["kind"], item["context"]) for item in findings] == [
        ("local_path", "see <local_path>")
    ]
