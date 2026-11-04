"""3 つのスクリプトの --format result の出力が、shared/result.schema.json に合うことを確かめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo
from result_schema import load_schema, validate

import hotspots
import inventory
import scan

PERSONAL_EMAIL = "someone" + "@" + "mail.example.net"


def make_repo(tmp_path: Path) -> str:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
            "app.py": "x = 1\n",
            "notes.md": f"mail {PERSONAL_EMAIL}\n",
            "LICENSE": "MIT\n",
            "README.md": "# demo\n",
        }
    )
    # hotspots は 2 回以上変わったファイルだけを数える。
    return repo.commit({"app.py": "x = 2\n"})


def run(main: object, argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert callable(main)
    assert main(argv) == 0
    document: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert isinstance(document, dict)
    return document


def test_the_schema_file_is_valid_json_with_the_expected_name() -> None:
    schema = load_schema()

    assert schema["title"] == "shipkeep result"
    assert schema["properties"]["format"]["const"] == "shipkeep-result/1"


def test_scan_result_matches_the_schema(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit = make_repo(tmp_path)

    document = run(scan.main, [str(tmp_path), "--format", "result"], capsys)

    assert validate(document) == []
    assert document["tool"] == {"name": "shipkeep-publish-check"}
    assert document["target"] == {"commit": commit}
    items = {item["id"]: item for item in document["items"]}
    personal = items["history_personal_data"]
    assert personal["status"] == "fail"
    assert personal["severity"] == "high"
    assert personal["confidence"] == "likely"
    assert personal["evidence"][0]["kind"] == "email_address"
    assert PERSONAL_EMAIL not in json.dumps(document)
    # 失敗していない項目には重大度を付けない。
    assert "severity" not in items["license"]


def test_scan_result_puts_unknown_finding_keys_into_detail() -> None:
    evidence = scan.to_evidence({"kind": "env_example_missing", "file": ".env", "line": 3})

    assert evidence == {"kind": "env_example_missing", "line": 3, "detail": "file=.env"}


def test_inventory_result_matches_the_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit = make_repo(tmp_path)

    document = run(
        inventory.main, [str(tmp_path), "--format", "result", "--language", "go"], capsys
    )

    assert validate(document) == []
    assert document["tool"] == {"name": "shipkeep-quality-tools"}
    assert document["target"] == {"commit": commit}
    items = {item["id"]: item for item in document["items"]}
    assert items["language/python"]["status"] == "info"
    assert items["language/go"]["confidence"] == "confirmed"
    lint = items["lint/python"]
    assert lint["status"] == "pass"
    assert {"kind": "tools", "detail": "ruff"} in lint["evidence"]
    assert items["type_check/python"]["severity"] == "low"
    assert items["dependency_audit/python"]["severity"] == "medium"
    assert items["lint/go"]["status"] == "unknown"
    assert "note" in items["lint/go"]


def test_hotspots_result_matches_the_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit = make_repo(tmp_path)

    document = run(hotspots.main, [str(tmp_path), "--format", "result"], capsys)

    assert validate(document) == []
    assert document["tool"] == {"name": "shipkeep-refactor"}
    assert document["target"] == {"commit": commit}
    assert document["items"], "the repository has source files"
    item = document["items"][0]
    assert item["id"].startswith("hotspot/")
    assert item["status"] == "info"
    assert "severity" not in item
    assert document["counts"] == {"info": len(document["items"])}


def test_scan_result_without_commits_has_no_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    GitRepo(tmp_path)

    document = run(scan.main, [str(tmp_path), "--format", "result"], capsys)

    assert validate(document) == []
    assert "target" not in document


def test_the_default_output_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_repo(tmp_path)

    document = run(scan.main, [str(tmp_path)], capsys)

    assert set(document) == {"schema", "counts", "checks"}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"format": "shipkeep-result/1", "tool": {"name": "x"}}, "missing items"),
        (
            {"format": "shipkeep-result/2", "tool": {"name": "x"}, "items": []},
            "must be 'shipkeep-result/1'",
        ),
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [{"id": "Bad Id", "check": "c", "status": "fail", "summary": "s"}],
            },
            "does not match",
        ),
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [{"id": "c", "check": "c", "status": "warn", "summary": "s"}],
            },
            "must be one of",
        ),
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [{"id": "c", "check": "c", "status": "pass", "summary": "s", "extra": 1}],
            },
            "unexpected property extra",
        ),
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [],
                "counts": {"a": -1},
            },
            "below 0",
        ),
    ],
)
def test_the_validator_rejects_documents_that_break_the_schema(
    document: dict[str, object], message: str
) -> None:
    errors = validate(document)

    assert any(message in error for error in errors), errors


def test_inventory_head_commit_is_none_when_git_is_missing_or_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert inventory.head_commit(tmp_path) is None

    monkeypatch.setattr("shutil.which", lambda name: "git")

    def failing(git: str, root: Path, *args: str) -> tuple[int, str]:
        raise OSError("cannot start git")

    monkeypatch.setattr(inventory, "run_git", failing)
    assert inventory.head_commit(tmp_path) is None

    monkeypatch.setattr(inventory, "run_git", lambda git, root, *args: (128, ""))
    assert inventory.head_commit(tmp_path) is None


def test_hotspots_head_commit_is_none_outside_a_repository(tmp_path: Path) -> None:
    assert hotspots.head_commit(tmp_path) is None


def test_inventory_result_without_commits_has_no_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    GitRepo(tmp_path)

    document = run(inventory.main, [str(tmp_path), "--format", "result"], capsys)

    assert validate(document) == []
    assert "target" not in document
