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
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [
                    {"id": "c", "check": "c", "status": "pass", "summary": "s", "severity": "low"}
                ],
            },
            "must not match",
        ),
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [],
                "generated_at": "2026-11-04",
            },
            "not an RFC 3339 date-time",
        ),
        (
            {
                "format": "shipkeep-result/1",
                "tool": {"name": "x"},
                "items": [],
                "generated_at": "2026-11-04T10:00:00",
            },
            "not an RFC 3339 date-time",
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

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git")

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


def test_hotspots_ids_allow_paths_with_spaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"my app.py": "x = 1\n"})
    repo.commit({"my app.py": "x = 2\n"})

    document = run(hotspots.main, [str(tmp_path), "--format", "result"], capsys)

    assert validate(document) == []
    assert document["items"][0]["id"] == "hotspot/my app.py"


def test_inventory_result_lists_unrecognized_extensions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"main.lua": "print(1)\n"})

    document = run(inventory.main, [str(tmp_path), "--format", "result"], capsys)

    assert validate(document) == []
    items = {item["id"]: item for item in document["items"]}
    assert items["unrecognized_extension/lua"]["status"] == "info"
    assert sum(document["counts"].values()) == len(document["items"])


def test_scan_result_reports_git_failures_with_a_fixed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_repo(tmp_path)

    def failing(root: Path) -> str | None:
        raise OSError("git vanished")

    monkeypatch.setattr(scan, "head_commit", failing)

    assert scan.main([str(tmp_path), "--format", "result"]) == 2
    err = capsys.readouterr().err
    assert "git failed while reading the repository" in err
    assert str(tmp_path) not in err


def test_the_validator_refuses_schemas_it_does_not_understand() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 3}}}
    with pytest.raises(ValueError, match="maxLength"):
        validate({"a": "x"}, schema)
    with pytest.raises(ValueError, match="unsupported format"):
        validate("x", {"type": "string", "format": "uri"})


def test_the_validator_applies_constraints_next_to_a_ref() -> None:
    schema = {"$defs": {"n": {"type": "integer"}}, "$ref": "#/$defs/n", "minimum": 1}
    assert validate(1, schema) == []
    assert validate(0, schema) == ["$: below 1"]


@pytest.mark.parametrize(
    "value", ["2026-11-04t10:00:00z", "2026-11-04T10:00:00.5+09:00", "2026-11-04T23:59:59-05:30"]
)
def test_the_validator_accepts_rfc_3339_date_times_in_either_case(value: str) -> None:
    assert validate(value, {"type": "string", "format": "date-time"}) == []


@pytest.mark.parametrize(
    "value", ["2026-13-04T10:00:00Z", "2026-11-04T24:00:00Z", "2026-11-04T10:00:00+24:00"]
)
def test_the_validator_rejects_impossible_date_times(value: str) -> None:
    assert validate(value, {"type": "string", "format": "date-time"}) == [
        "$: not an RFC 3339 date-time"
    ]
