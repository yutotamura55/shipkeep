"""3 つのスクリプトの --format result の出力が、shared/result.schema.json に合うことを確かめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo

import hotspots
import inventory
import scan
from result_schema import load_schema, validate

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


def test_the_validator_reports_type_and_length_errors() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string", "minLength": 2}}}
    assert validate({"a": 1}, schema) == ["$.a: must be string"]
    assert validate({"a": "x"}, schema) == ["$.a: shorter than 2"]


def test_the_validator_rejects_refs_it_cannot_follow() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        validate(1, {"$ref": "other.json#/x"})
    # 事前の走査は $defs の中身を確かめるので、配列を指す参照は required などで作る。
    with pytest.raises(ValueError, match="does not point"):
        validate(1, {"required": ["a"], "$ref": "#/required"})


def test_the_end_anchor_does_not_accept_a_trailing_newline() -> None:
    schema = {"type": "string", "pattern": "^[a-z]+$"}
    assert validate("readme", schema) == []
    assert validate("readme\n", schema) == ["$: does not match ^[a-z]+$"]


def test_patterns_without_an_end_anchor_match_anywhere() -> None:
    assert validate("a readme\n", {"type": "string", "pattern": "readme"}) == []


def test_scan_remediations_match_the_skill_md_table() -> None:
    skill_md = (
        Path(__file__).resolve().parents[1] / "skills/shipkeep-publish-check/SKILL.md"
    ).read_text(encoding="utf-8")
    # 「What each check means and how to fix it」の表だけを読む。
    lines = skill_md.splitlines()
    start = lines.index("| Check | What it looks for | How to fix |")
    rows: dict[str, str] = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        assert len(cells) == 3, line
        check_id = cells[0].strip("`")
        assert check_id not in rows, check_id
        rows[check_id] = cells[2].replace("**", "").replace("`", "")
    assert set(rows) == set(scan.REMEDIATIONS) == set(scan.SEVERITIES)
    for check_id, text in scan.REMEDIATIONS.items():
        assert rows[check_id] == text, check_id


def test_scan_result_puts_the_remediation_on_failed_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_repo(tmp_path)

    document = run(scan.main, [str(tmp_path), "--format", "result"], capsys)

    for item in document["items"]:
        has_fix = item["status"] in ("fail", "unknown")
        assert ("remediation" in item) == has_fix, item["id"]
        assert "`" not in item.get("remediation", "")
    assert validate(document) == []


def test_scan_result_explains_unknown_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_repo(tmp_path)
    output = scan.scan(tmp_path)
    output["checks"][0]["status"] = "unknown"

    document = scan.to_result(output, None)

    fix = document["items"][0]["remediation"]
    assert fix.startswith("The scan could not verify this")
    assert scan.REMEDIATIONS[document["items"][0]["id"]] in fix


def test_inventory_has_a_remediation_for_every_aspect_that_can_fail() -> None:
    for language in inventory.REFERENCED_LANGUAGES:
        recommended = inventory.recommendations(language.reference)
        # 参照の表は、どの観点にも推奨を書いている。
        assert set(recommended) == set(inventory.LANGUAGE_ASPECTS), language.id
        for aspect in inventory.LANGUAGE_ASPECTS:
            fix = inventory.remediation({"aspect": aspect, "scope": language.id, "status": "fail"})
            assert fix is not None
            assert f"references/{language.reference}" in fix
            assert recommended[aspect] in fix
            assert "`" not in fix
    for aspect, _, _ in inventory.REPOSITORY_ASPECTS:
        if aspect in inventory.OPTIONAL_ASPECTS or aspect == "codeql":
            continue
        fix = inventory.remediation({"aspect": aspect, "scope": "repository", "status": "fail"})
        assert fix is not None, aspect
        assert "`" not in fix


def test_repository_fixes_fit_each_aspect() -> None:
    def fix(aspect: str) -> str:
        text = inventory.remediation({"aspect": aspect, "scope": "repository", "status": "fail"})
        assert text is not None
        return text

    assert "from the check command" not in fix("check_command")
    assert "from the check command" not in fix("ci")
    assert "dependabot.yml" in fix("dependency_updates")
    assert "dependency graph" in fix("dependency_review")
    audit = fix("dependency_audit")
    assert audit.index("own tool") < audit.index("OSV-Scanner")


def test_security_lint_points_to_the_common_layer() -> None:
    fix = inventory.remediation(
        {"aspect": "security_lint", "scope": "javascript", "status": "fail"}
    )
    assert fix is not None
    assert "references/common.md" in fix


def test_recommendations_without_the_reference_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(inventory, "REFERENCES", tmp_path)
    assert inventory.recommendations("python.md") == {}
    fix = inventory.remediation({"aspect": "lint", "scope": "python", "status": "fail"})
    assert fix == (
        "Use what references/python.md of shipkeep-quality-tools recommends for lint. "
        "Check its current version, run it from the check command, and run that in CI"
    )


def test_unknown_rows_get_a_fix_only_when_one_applies() -> None:
    def fix(scope: str, aspect: str, reference: bool) -> str | None:
        return inventory.remediation(
            {"aspect": aspect, "scope": scope, "status": "unknown"}, reference
        )

    assert fix("go", "lint", False) == inventory.RESEARCH_FIX
    assert fix("repository", "codeql", True) == inventory.CODEQL_FIX
    assert fix("repository", "sast", True) is None
    assert fix("python", "lint", True) is None
    # 参照のない言語の fail (今はない) も、調べ方を返す。
    assert inventory.remediation({"aspect": "lint", "scope": "go", "status": "fail"}) == (
        inventory.RESEARCH_FIX
    )


def test_inventory_result_puts_remediations_on_failed_and_unknown_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_repo(tmp_path)

    document = run(
        inventory.main, [str(tmp_path), "--format", "result", "--language", "go"], capsys
    )

    items = {item["id"]: item for item in document["items"]}
    assert "pip-audit" in items["dependency_audit/python"]["remediation"]
    assert "references/python.md" in items["dependency_audit/python"]["remediation"]
    assert "own tool" in items["dependency_audit/repository"]["remediation"]
    assert "other-languages.md" in items["lint/go"]["remediation"]
    assert "Advanced Security" in items["codeql/repository"]["remediation"]
    assert "remediation" not in items["lint/python"]
    assert validate(document) == []


def test_inventory_has_no_remediation_for_passing_or_unlisted_aspects() -> None:
    assert inventory.remediation({"aspect": "lint", "scope": "python", "status": "pass"}) is None
    unlisted = {"aspect": "something_new", "scope": "repository", "status": "fail"}
    assert inventory.remediation(unlisted) is None


def test_recommendations_ignore_rows_that_are_not_aspects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "x.md").write_text(
        "| Aspect | Recommended | Notes |\n| --- | --- | --- |\n"
        "| Lint | `tool-a` | x |\n| Packaging | tool-b | y |\n| Other | text |\n"
        "| Format | tool-c \\| tool-d | z |\n"
    )
    monkeypatch.setattr(inventory, "REFERENCES", tmp_path)

    assert inventory.recommendations("x.md") == {"lint": "tool-a", "format": "tool-c | tool-d"}
