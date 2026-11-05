"""report.py (結果から Issue の案を作り、承認された案のとおりに作る) のテスト。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import report
from result_schema import validate

REPO = "owner/demo"
PERSONAL_EMAIL = "someone" + "@" + "mail.example.net"


class FakeGh:
    """gh の代わり。GitHub の Issue とラベルを持ち、呼ばれた操作を記録する。"""

    def __init__(
        self,
        issues: list[dict[str, Any]] | None = None,
        labels: list[str] | None = None,
        fail: set[str] | None = None,
        pages: bool = False,
    ) -> None:
        self.issues = issues or []
        self.labels = labels or []
        self.fail = fail or set()
        self.pages = pages
        self.calls: list[tuple[list[str], str | None]] = []
        self.next_number = 100

    def __call__(self, args: list[str], stdin: str | None) -> tuple[int, str]:
        self.calls.append((args, stdin))
        path = next(a for a in args if a.startswith("repos/"))
        if path.split("?")[0].endswith("/issues") and "POST" in args:
            if "create_issue" in self.fail:
                return 1, ""
            payload = json.loads(stdin or "{}")
            self.next_number += 1
            self.issues.append(
                {"number": self.next_number, "state": "open", "body": payload["body"]}
            )
            return 0, json.dumps({"number": self.next_number})
        if path.split("?")[0].endswith("/labels") and "POST" in args:
            if "create_label" in self.fail:
                return 1, ""
            self.labels.append(json.loads(stdin or "{}")["name"])
            return 0, "{}"
        if "list" in self.fail:
            return 1, ""
        if "/issues?" in path:
            data: list[dict[str, Any]] = [{"state_reason": None, **i} for i in self.issues]
        else:
            data = [{"name": name} for name in self.labels]
        if self.pages and len(data) > 1:
            middle = len(data) // 2
            return 0, json.dumps(data[:middle]) + "\n" + json.dumps(data[middle:])
        return 0, json.dumps(data)

    def created(self) -> list[dict[str, Any]]:
        return [
            json.loads(stdin or "{}")
            for args, stdin in self.calls
            if "POST" in args and any(a.endswith("/issues") for a in args)
        ]


def item(
    item_id: str, status: str = "fail", severity: str | None = "high", **extra: Any
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": item_id,
        "check": item_id.split("/")[0],
        "status": status,
        "summary": f"summary of {item_id}",
        "confidence": "confirmed",
        **extra,
    }
    if severity and status == "fail":
        entry["severity"] = severity
    return entry


def write_result(
    tmp_path: Path,
    items: list[dict[str, Any]],
    name: str = "result.json",
    tool: str = "shipkeep-publish-check",
) -> Path:
    document = {
        "format": "shipkeep-result/1",
        "tool": {"name": tool},
        "target": {"commit": "a" * 40},
        "generated_at": "2026-11-05T01:02:03+00:00",
        "items": items,
    }
    assert validate(document) == []
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def plan(
    tmp_path: Path, results: list[Path], gh: FakeGh, *extra: str
) -> tuple[int, dict[str, Any]]:
    output = tmp_path / "plan.json"
    code = report.main(
        ["plan", *map(str, results), "--repo", REPO, "--output", str(output), *extra], gh
    )
    return code, json.loads(output.read_text()) if output.exists() else {}


def marker_body(tool: str, item_id: str) -> str:
    return report.marker((tool, item_id)) + "\nold body"


# --- validate ---------------------------------------------------------------------------------


def test_validate_accepts_a_result_and_reports_a_broken_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write_result(tmp_path, [item("license")])
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "shipkeep-result/1", "items": []}))

    assert report.main(["validate", str(good), str(broken)]) == 1

    out = capsys.readouterr().out
    assert "result 1: ok" in out
    assert "result 2: $: missing tool" in out
    assert str(tmp_path) not in out


def test_validate_reports_a_file_that_is_not_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "x.json"
    path.write_text("not json")

    assert report.main(["validate", str(path)]) == 2
    err = capsys.readouterr().err
    assert "result file 1 is not JSON" in err
    assert str(tmp_path) not in err


# --- plan -------------------------------------------------------------------------------------


def test_plan_files_failed_items_by_severity_up_to_the_limit(tmp_path: Path) -> None:
    result = write_result(
        tmp_path,
        [
            item("readme", severity="low"),
            item("license", severity="medium"),
            item("history_secrets", severity="critical"),
            item("sensitive_files", severity="critical"),
            item("history_personal_data", severity="high"),
            item("env_files", status="pass"),
        ],
    )
    gh = FakeGh(labels=["shipkeep"])

    code, document = plan(tmp_path, [result], gh)

    assert code == 0
    assert [e["id"] for e in document["create"]] == [
        "history_secrets",
        "sensitive_files",
        "history_personal_data",
    ]
    assert document["skipped"] == [
        {"tool": "shipkeep-publish-check", "id": "license", "reason": "over_limit"}
    ]
    assert document["labels_to_create"] == ["needs-triage"]
    assert document["create"][0]["labels"] == ["shipkeep", "needs-triage"]
    # plan は GitHub を読むだけで、何も作らない。
    assert gh.created() == []
    assert not any("POST" in args for args, _ in gh.calls)


def test_plan_skips_dismissed_open_and_not_planned_items(tmp_path: Path) -> None:
    tool = "shipkeep-publish-check"
    result = write_result(
        tmp_path,
        [item("license"), item("readme"), item("denylist"), item("history_secrets")],
    )
    dismissed = tmp_path / "dismissed.json"
    report.dismiss(dismissed, tool, "license", "the project is not licensed on purpose", None)
    gh = FakeGh(
        issues=[
            {"number": 5, "state": "open", "body": marker_body(tool, "readme")},
            {
                "number": 6,
                "state": "closed",
                "state_reason": "not_planned",
                "body": marker_body(tool, "denylist"),
            },
            {
                "number": 7,
                "state": "open",
                "body": marker_body(tool, "history_secrets"),
                "pull_request": {},
            },
        ]
    )

    _, document = plan(tmp_path, [result], gh, "--dismissed", str(dismissed))

    assert document["skipped"] == [
        {"tool": tool, "id": "denylist", "reason": "closed_as_not_planned", "issue": 6},
        {"tool": tool, "id": "license", "reason": "dismissed"},
        {"tool": tool, "id": "readme", "reason": "already_open", "issue": 5},
    ]
    # PR の本文の目印は数えない。
    assert [e["id"] for e in document["create"]] == ["history_secrets"]


def test_plan_files_again_an_item_closed_as_completed_that_still_fails(tmp_path: Path) -> None:
    tool = "shipkeep-publish-check"
    result = write_result(tmp_path, [item("license")])
    gh = FakeGh(
        issues=[
            {
                "number": 9,
                "state": "closed",
                "state_reason": "completed",
                "body": marker_body(tool, "license"),
            }
        ]
    )

    _, document = plan(tmp_path, [result], gh)

    (entry,) = document["create"]
    assert "Filed before as #9" in entry["body"]


def test_plan_blocks_an_item_with_personal_data(tmp_path: Path) -> None:
    result = write_result(
        tmp_path,
        [
            item(
                "history_personal_data",
                evidence=[{"kind": "email_address", "context": f"mail {PERSONAL_EMAIL}"}],
            ),
            item("local", evidence=[{"kind": "path", "detail": "/home/" + "someone/project"}]),
        ],
    )

    _, document = plan(tmp_path, [result], FakeGh())

    assert document["create"] == []
    assert {e["reason"] for e in document["skipped"]} == {"blocked_personal_data"}
    assert PERSONAL_EMAIL not in json.dumps(document)


def test_plan_respects_min_severity_unknown_and_missing_severity(tmp_path: Path) -> None:
    result = write_result(
        tmp_path,
        [
            item("low_one", severity="low"),
            item("no_severity", severity=None),
            item("not_run", status="unknown"),
        ],
    )

    _, default = plan(tmp_path, [result], FakeGh())
    _, wide = plan(
        tmp_path, [result], FakeGh(), "--min-severity", "low", "--include-unknown", "--limit", "10"
    )

    assert [e["id"] for e in default["create"]] == ["no_severity"]
    assert [e["id"] for e in wide["create"]] == ["no_severity", "low_one", "not_run"]


def test_plan_reads_paginated_responses_and_custom_labels(tmp_path: Path) -> None:
    tool = "shipkeep-publish-check"
    result = write_result(tmp_path, [item("license"), item("readme")])
    gh = FakeGh(
        issues=[
            {"number": 1, "state": "open", "body": "unrelated"},
            {"number": 2, "state": "open", "body": marker_body(tool, "license")},
        ],
        labels=["bug", "triage"],
        pages=True,
    )

    _, document = plan(tmp_path, [result], gh, "--label", "triage")

    assert [e["id"] for e in document["create"]] == ["readme"]
    assert document["labels_to_create"] == []
    assert document["create"][0]["labels"] == ["triage"]


def test_plan_reports_gh_failures_without_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])

    code, _ = plan(tmp_path, [result], FakeGh(fail={"list"}))

    assert code == 2
    assert "could not read the Issues with gh" in capsys.readouterr().err


def test_plan_rejects_a_result_that_breaks_the_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "shipkeep-result/1"}))

    code, _ = plan(tmp_path, [broken], FakeGh())

    assert code == 2
    assert "result file 1 does not match" in capsys.readouterr().err


def test_plan_rejects_bad_arguments(tmp_path: Path) -> None:
    result = write_result(tmp_path, [item("license")])
    with pytest.raises(SystemExit) as exit_info:
        report.main(["plan", str(result), "--repo", "not a repo", "--output", "x"], FakeGh())
    assert exit_info.value.code == 2
    assert plan(tmp_path, [result], FakeGh(), "--limit", "-1")[0] == 2


# --- 本文 -------------------------------------------------------------------------------------


def test_body_neutralizes_mentions_and_keeps_masked_values(tmp_path: Path) -> None:
    result = write_result(
        tmp_path,
        [
            item(
                "readme/docs @team",
                summary="ping @someone about `code`",
                note="see #12",
                remediation="write it",
                scope="repository",
                evidence=[
                    {
                        "kind": "email_address",
                        "commit": "b" * 40,
                        "path": "a b.md",
                        "line": 3,
                        "context": "mail <email_address>",
                        "detail": "x",
                    }
                ]
                + [{"kind": "file", "path": f"f{n}.md"} for n in range(25)],
            )
        ],
    )

    _, document = plan(tmp_path, [result], FakeGh())

    (entry,) = document["create"]
    assert "@" not in entry["title"]
    body = entry["body"]
    assert "`` ping @someone about `code` ``" in body
    assert "- Scope: `repository`" in body
    assert "commit `bbbbbbbbbbbb`" in body
    assert "`a b.md` line 3" in body
    assert "...and 6 more in the result file" in body
    assert "### How to fix" in body
    assert body.startswith(report.marker(("shipkeep-publish-check", "readme/docs @team")))


def test_code_span_handles_empty_and_backticks() -> None:
    assert report.code_span("") == "(empty)"
    assert report.code_span("`x`") == "`` `x` ``"
    assert report.code_span("a\nb") == "`a b`"


def test_long_titles_are_cut() -> None:
    title = report.title_for("tool", {"id": "x/" + "y" * 300})
    assert len(title) == report.MAX_TITLE_LENGTH
    assert title.endswith("...")


# --- create -----------------------------------------------------------------------------------


def test_create_makes_labels_and_issues_of_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license"), item("readme")])
    gh = FakeGh()
    plan(tmp_path, [result], gh)

    assert report.main(["create", str(tmp_path / "plan.json"), "--repo", REPO], gh) == 0

    assert sorted(gh.labels) == ["needs-triage", "shipkeep"]
    assert [p["title"] for p in gh.created()] == [
        "[shipkeep] shipkeep-publish-check: license",
        "[shipkeep] shipkeep-publish-check: readme",
    ]
    out = capsys.readouterr().out
    assert "created #101" in out and "created #102" in out

    # 同じ案をもう一度作っても、重複しない。
    assert report.main(["create", str(tmp_path / "plan.json"), "--repo", REPO], gh) == 0
    assert len(gh.created()) == 2
    assert "already filed as #101" in capsys.readouterr().out


def test_create_refuses_a_plan_for_another_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())

    assert (
        report.main(["create", str(tmp_path / "plan.json"), "--repo", "other/repo"], FakeGh()) == 2
    )
    assert "another repository" in capsys.readouterr().err


def test_create_reports_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())

    gh = FakeGh(fail={"create_issue", "create_label"})
    assert report.main(["create", str(tmp_path / "plan.json"), "--repo", REPO], gh) == 1
    err = capsys.readouterr().err
    assert "failed to create the label" in err
    assert "failed to create the Issue for shipkeep-publish-check license" in err


def test_create_refuses_a_plan_edited_to_hold_personal_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())
    path = tmp_path / "plan.json"
    document = json.loads(path.read_text())
    document["create"][0]["body"] += f"\ncontact {PERSONAL_EMAIL}\n"
    path.write_text(json.dumps(document))
    gh = FakeGh(labels=["shipkeep", "needs-triage"])

    assert report.main(["create", str(path), "--repo", REPO], gh) == 1
    assert gh.created() == []
    assert "looks like personal data" in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [
        {"format": "other"},
        {"create": [{"tool": "t", "id": "i", "title": "x", "body": "y", "labels": ["a,b"]}]},
        {"labels_to_create": [5]},
    ],
)
def test_create_rejects_a_malformed_plan(tmp_path: Path, change: dict[str, Any]) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())
    path = tmp_path / "plan.json"
    document = {**json.loads(path.read_text()), **change}
    path.write_text(json.dumps(document))

    assert report.main(["create", str(path), "--repo", REPO], FakeGh()) == 2


# --- record / dismiss / recurring ------------------------------------------------------------


def test_record_writes_a_dated_file_without_overwriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    history = tmp_path / ".shipkeep" / "history"

    assert report.main(["record", str(result), "--into", str(history)]) == 0
    assert report.main(["record", str(result), "--into", str(history)]) == 0

    names = sorted(p.name for p in history.iterdir())
    assert names == [
        "2026-11-05-shipkeep-publish-check-aaaaaaa-2.json",
        "2026-11-05-shipkeep-publish-check-aaaaaaa.json",
    ]
    assert str(tmp_path) not in capsys.readouterr().out


def test_record_refuses_personal_data(tmp_path: Path) -> None:
    result = write_result(tmp_path, [item("x", note=f"by {PERSONAL_EMAIL}")])

    assert report.main(["record", str(result), "--into", str(tmp_path / "h")]) == 2
    assert not (tmp_path / "h").exists()


def test_dismiss_adds_replaces_and_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".shipkeep" / "dismissed.json"
    args = ["dismiss", "--file", str(path), "--tool", "t", "--id", "license"]

    assert report.main([*args, "--reason", "first", "--issue", "4"]) == 0
    assert report.main([*args, "--reason", "second   reason"]) == 0
    assert (
        report.main(["dismiss", "--file", str(path), "--tool", "a", "--id", "b", "--reason", "x"])
        == 0
    )

    document = json.loads(path.read_text())
    assert document["format"] == "shipkeep-dismissed/1"
    assert [(i["tool"], i["id"], i["reason"]) for i in document["items"]] == [
        ("a", "b", "x"),
        ("t", "license", "second reason"),
    ]
    assert "issue" not in document["items"][1]
    assert report.main([*args, "--reason", "  "]) == 2
    assert report.main([*args, "--reason", f"ask {PERSONAL_EMAIL}"]) == 2
    assert PERSONAL_EMAIL not in capsys.readouterr().err


def test_a_broken_dismissed_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dismissed.json"
    path.write_text(json.dumps({"format": "shipkeep-dismissed/1", "items": [{"tool": "t"}]}))
    result = write_result(tmp_path, [item("license")])

    assert plan(tmp_path, [result], FakeGh(), "--dismissed", str(path))[0] == 2


def test_recurring_counts_failures_across_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "history"
    history.mkdir()
    for number, items in enumerate(
        [
            [item("license"), item("readme")],
            [item("license"), item("readme", status="pass")],
            [item("license")],
        ]
    ):
        (history / f"r{number}.json").write_text(write_result(tmp_path, items).read_text())
    (history / "broken.json").write_text("{}")

    assert report.main(["recurring", str(history)]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["records"] == 4
    assert document["unreadable"] == 1
    assert document["recurring"] == [
        {
            "tool": "shipkeep-publish-check",
            "id": "license",
            "check": "license",
            "count": 3,
            "last": "r2.json",
        }
    ]
    assert report.main(["recurring", str(history), "--min-count", "0"]) == 2
    assert report.main(["recurring", str(tmp_path / "missing")]) == 0


def test_run_gh_reports_a_missing_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert report.run_gh(["api", "repos/x/y"]) == (127, "")


# --- 境界とエラーの経路 -----------------------------------------------------------------------


def test_run_gh_runs_the_gh_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text('#!/bin/sh\necho "[]"\nexit 0\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    assert report.run_gh(["api", "repos/x/y"]) == (0, "[]\n")


def test_parse_pages_handles_blank_text_and_rejects_non_lists() -> None:
    assert report.parse_pages("  \n") == []
    with pytest.raises(report.ReportError, match="unexpected response"):
        report.parse_pages('{"a": 1}')


def test_plan_reports_a_response_that_is_not_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])

    def broken(args: list[str], stdin: str | None) -> tuple[int, str]:
        return 0, "not json"

    output = tmp_path / "plan.json"
    code = report.main(["plan", str(result), "--repo", REPO, "--output", str(output)], broken)

    assert code == 2
    assert "unexpected response" in capsys.readouterr().err


def test_unreadable_files_are_reported_without_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert report.main(["validate", str(tmp_path)]) == 2
    assert "cannot read the result file 1" in capsys.readouterr().err

    result = write_result(tmp_path, [item("license")])
    missing_dir = tmp_path / "missing" / "plan.json"
    code = report.main(
        ["plan", str(result), "--repo", REPO, "--output", str(missing_dir)], FakeGh()
    )
    assert code == 2
    assert "cannot write the plan file" in capsys.readouterr().err


def test_plan_allows_public_addresses_and_merges_duplicate_items(tmp_path: Path) -> None:
    public = "1+me" + "@" + "users.noreply.github.com"
    first = write_result(
        tmp_path,
        [item("commit_identity", evidence=[{"kind": "author_email", "context": f"by {public}"}])],
        name="a.json",
    )
    second = write_result(tmp_path, [item("commit_identity")], name="b.json")

    _, document = plan(tmp_path, [first, second], FakeGh())

    (entry,) = document["create"]
    assert "`author_email` · context" in entry["body"]
    assert "...and" not in entry["body"]


def test_body_without_optional_fields(tmp_path: Path) -> None:
    document = {
        "format": "shipkeep-result/1",
        "tool": {"name": "other-tool"},
        "items": [
            {"id": "x", "check": "x", "status": "fail", "summary": "s", "evidence": [{"kind": "k"}]}
        ],
    }
    path = tmp_path / "r.json"
    path.write_text(json.dumps(document))

    _, result = plan(tmp_path, [path], FakeGh())

    body = result["create"][0]["body"]
    assert "- Status: fail\n" in body
    assert "Inspected commit" not in body
    assert "- `k`\n" in body


def test_create_reports_a_response_without_a_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())
    gh = FakeGh(labels=["shipkeep", "needs-triage"])

    def odd(args: list[str], stdin: str | None) -> tuple[int, str]:
        if "POST" in args:
            return 0, "not json"
        return gh(args, stdin)

    assert report.main(["create", str(tmp_path / "plan.json"), "--repo", REPO], odd) == 1
    assert "failed to create the Issue" in capsys.readouterr().err


def test_record_without_a_commit_and_write_errors(tmp_path: Path) -> None:
    document = {
        "format": "shipkeep-result/1",
        "tool": {"name": "other tool/1"},
        "items": [],
    }
    path = tmp_path / "r.json"
    path.write_text(json.dumps(document))
    history = tmp_path / "history"

    name = report.record(path, history)

    assert name.endswith("-other-tool-1.json")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(report.ReportError, match="history directory"):
        report.record(path, blocker / "history")
    with pytest.raises(report.ReportError, match="dismissed file"):
        report.dismiss(blocker / "dismissed.json", "t", "i", "reason", None)


def test_a_bad_label_is_rejected(tmp_path: Path) -> None:
    result = write_result(tmp_path, [item("license")])
    with pytest.raises(SystemExit) as exit_info:
        report.main(
            ["plan", str(result), "--repo", REPO, "--output", "x", "--label", "a,b"], FakeGh()
        )
    assert exit_info.value.code == 2
