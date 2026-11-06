"""report.py (結果から Issue の案を作り、承認された案のとおりに作る) のテスト。"""

from __future__ import annotations

import hashlib
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
            data: list[dict[str, Any]] = [
                {"state_reason": None, "author_association": "OWNER", **i} for i in self.issues
            ]
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
    generated_at: str = "2026-11-05T01:02:03+00:00",
) -> Path:
    document = {
        "format": "shipkeep-result/1",
        "tool": {"name": tool},
        "target": {"commit": "a" * 40},
        "generated_at": generated_at,
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


def create(tmp_path: Path, gh: Any, repo: str = REPO, digest: str | None = None) -> int:
    path = tmp_path / "plan.json"
    return report.main(
        [
            "create",
            str(path),
            "--repo",
            repo,
            "--sha256",
            digest or hashlib.sha256(path.read_bytes()).hexdigest(),
        ],
        gh,
    )


# --- validate ---------------------------------------------------------------------------------


def test_validate_accepts_a_result_and_reports_a_broken_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write_result(tmp_path, [item("license")])
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "shipkeep-result/1", "items": []}))

    assert report.main(["validate", str(good), str(broken)]) == 1

    out = capsys.readouterr().out
    assert "結果 1：形式に合っています" in out
    assert "結果 2：$：tool がありません" in out
    assert str(tmp_path) not in out


def test_validate_reports_a_file_that_is_not_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "x.json"
    path.write_text("not json")

    assert report.main(["validate", str(path)]) == 2
    err = capsys.readouterr().err
    assert "結果のファイル 1 が JSON ではありません" in err
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
    assert "以前に #9 として作り" in entry["body"]


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
    assert "gh で Issue を読めませんでした" in capsys.readouterr().err


def test_plan_rejects_a_result_that_breaks_the_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"format": "shipkeep-result/1"}))

    code, _ = plan(tmp_path, [broken], FakeGh())

    assert code == 2
    assert "結果のファイル 1 が、shipkeep の結果の形式に合いません" in capsys.readouterr().err


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
    assert "- 範囲：`repository`" in body
    assert "コミット `bbbbbbbbbbbb`" in body
    assert "`a b.md` の 3 行目" in body
    assert "ほかに 6 件が、結果のファイルにあります" in body
    assert "### 直し方" in body
    assert body.startswith(report.marker(("shipkeep-publish-check", "readme/docs @team")))


def test_code_span_handles_empty_and_backticks() -> None:
    assert report.code_span("") == "（空）"
    assert report.code_span("`x`") == "`` `x` ``"
    assert report.code_span("a\nb") == "`a b`"


def test_code_span_keeps_fences_short_enough_for_github() -> None:
    # GitHub は 80 本を超える区切りをコードスパンとみなさない。中の並びを縮めて区切りを短く保つ。
    span = report.code_span("`" * 80 + " @someone other/repo#1")
    fence = span[: len(span) - len(span.lstrip("`"))]
    assert len(fence) == report.MAX_BACKTICK_RUN + 1
    assert span.endswith(fence)


def test_long_titles_are_cut() -> None:
    title = report.title_for({"id": "x/" + "y" * 300, "summary": "s"})
    assert len(title) == report.MAX_TITLE_LENGTH
    assert title.endswith("…")


# --- create -----------------------------------------------------------------------------------


def test_create_makes_labels_and_issues_of_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license"), item("readme")])
    gh = FakeGh()
    plan(tmp_path, [result], gh)

    assert create(tmp_path, gh) == 0

    assert sorted(gh.labels) == ["needs-triage", "shipkeep"]
    assert [p["title"] for p in gh.created()] == [
        "[shipkeep] summary of license（license）",
        "[shipkeep] summary of readme（readme）",
    ]
    out = capsys.readouterr().out
    assert "#101 を作りました" in out and "#102 を作りました" in out

    # 同じ案をもう一度作っても、重複しない。
    assert create(tmp_path, gh) == 0
    assert len(gh.created()) == 2
    assert "#101 として作成済み" in capsys.readouterr().out


def test_create_refuses_a_plan_for_another_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())

    assert create(tmp_path, FakeGh(), repo="other/repo") == 2
    assert "別のリポジトリ" in capsys.readouterr().err


def test_create_reports_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())

    # ラベルを作れなければ、Issue は 1 件も作らない。
    gh = FakeGh(fail={"create_issue", "create_label"})
    assert create(tmp_path, gh) == 1
    assert (
        "ラベル shipkeep を作れませんでした。Issue は 1 件も作っていません"
        in capsys.readouterr().err
    )
    assert gh.created() == []

    gh = FakeGh(labels=["Shipkeep", "NEEDS-TRIAGE"], fail={"create_issue"})
    assert create(tmp_path, gh) == 1
    assert "項目 1 の Issue を作れませんでした" in capsys.readouterr().err
    # ラベルは大文字小文字を区別せずに比べ、作り直さない。
    assert gh.labels == ["Shipkeep", "NEEDS-TRIAGE"]


def test_create_refuses_a_plan_edited_to_hold_personal_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())
    path = tmp_path / "plan.json"
    document = json.loads(path.read_text())
    # 項目のデータと表示用の本文を、そろえて書き換える (plan の確認を通らなかった値を想定)。
    entry = document["create"][0]
    entry["item"]["summary"] = f"contact {PERSONAL_EMAIL}"
    key = (entry["tool"], entry["id"])
    entry["body"] = report.body_for(key, entry["item"], entry["commit"], entry["previous"])
    entry["title"] = report.title_for(entry["item"])
    path.write_text(json.dumps(document))
    gh = FakeGh(labels=["shipkeep", "needs-triage"])

    # 承認の後にファイルが変わったので、承認したハッシュと合わない。
    assert create(tmp_path, gh, digest="0" * 64) == 2
    assert "承認の後に案のファイルが変わりました" in capsys.readouterr().err
    # 新しいハッシュで渡しても、個人情報に見える値があれば作らない。
    assert create(tmp_path, gh) == 1
    assert gh.created() == []
    out = capsys.readouterr().out
    assert "項目 1 は作りませんでした：個人情報に見える文字列があります" in out
    assert PERSONAL_EMAIL not in out


@pytest.mark.parametrize(
    "change",
    [
        {"format": "other"},
        {"create": [{"tool": "t", "id": "i", "title": "x", "body": "y", "labels": ["a,b"]}]},
        {"labels_to_create": [5]},
        {"labels_to_create": ["someone" + "@" + "mail.example.net"]},
    ],
)
def test_create_rejects_a_malformed_plan(tmp_path: Path, change: dict[str, Any]) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())
    path = tmp_path / "plan.json"
    document = {**json.loads(path.read_text()), **change}
    path.write_text(json.dumps(document))

    assert create(tmp_path, FakeGh()) == 2


# --- record / dismiss / recurring ------------------------------------------------------------


def test_record_writes_a_dated_file_without_overwriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    history = tmp_path / ".shipkeep" / "history"

    later = write_result(
        tmp_path, [item("license")], name="later.json", generated_at="2026-11-05T09:00:00Z"
    )

    assert report.main(["record", str(result), "--into", str(history)]) == 0
    assert report.main(["record", str(later), "--into", str(history)]) == 0
    # 同じ結果は 2 回記録しない。
    assert report.main(["record", str(result), "--into", str(history)]) == 2

    names = sorted(p.name for p in history.iterdir())
    assert names == [
        "2026-11-05-shipkeep-publish-check-aaaaaaa-2.json",
        "2026-11-05-shipkeep-publish-check-aaaaaaa.json",
    ]
    captured = capsys.readouterr()
    assert "記録済み" in captured.err
    assert str(tmp_path) not in captured.out + captured.err


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

    # 呼び名は否定を含まない「見送る項目」にする（#51。打ち消しても二重否定にならない）。
    assert "見送る項目として記録しました：t license" in capsys.readouterr().out
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


def test_reason_text_names_dismissed_items_with_the_new_term() -> None:
    # 案の表示でも、記録した項目を「見送る項目」と呼ぶ（#51）。
    assert report.reason_text("dismissed") == "見送る項目として記録済み"


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
            # 1 つの記録に同じ ID が 2 回あっても、1 回と数える。
            [item("license"), item("readme"), item("readme")],
            [item("license"), item("readme", status="pass")],
            [item("license")],
        ]
    ):
        text = write_result(tmp_path, items, generated_at=f"2026-11-0{number + 1}T00:00:00Z")
        (history / f"r{number}.json").write_text(text.read_text())
    # 同じ点検を 2 回記録したものは数えない。
    (history / "r2-copy.json").write_text((history / "r2.json").read_text())
    (history / "broken.json").write_text("{}")

    assert report.main(["recurring", str(history)]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["records"] == 5
    assert document["unreadable"] == 1
    assert document["duplicates"] == 1
    assert document["recurring"] == [
        {
            "tool": "shipkeep-publish-check",
            "id": "license",
            "check": "license",
            "count": 3,
            "last": "r2-copy.json",
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
    with pytest.raises(report.ReportError, match="想定と違う応答"):
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
    assert "想定と違う応答" in capsys.readouterr().err


def test_unreadable_files_are_reported_without_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert report.main(["validate", str(tmp_path)]) == 2
    assert "結果のファイル 1 を読めません" in capsys.readouterr().err

    result = write_result(tmp_path, [item("license")])
    missing_dir = tmp_path / "missing" / "plan.json"
    code = report.main(
        ["plan", str(result), "--repo", REPO, "--output", str(missing_dir)], FakeGh()
    )
    assert code == 2
    assert "案のファイルに書き込めません" in capsys.readouterr().err


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
    assert "`author_email` · 行の内容" in entry["body"]
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
    assert "- 状態：fail\n" in body
    assert "この項目を見送る場合は、Issue を not planned として閉じ" in body
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

    assert create(tmp_path, odd) == 1
    assert "の Issue を作れませんでした" in capsys.readouterr().err


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
    with pytest.raises(report.ReportError, match="履歴のディレクトリ"):
        report.record(path, blocker / "history")
    with pytest.raises(report.ReportError, match="見送る項目のファイル"):
        report.dismiss(blocker / "dismissed.json", "t", "i", "reason", None)


@pytest.mark.parametrize("label", ["a,b", "someone" + "@" + "mail.example.net"])
def test_a_bad_label_is_rejected(tmp_path: Path, label: str) -> None:
    result = write_result(tmp_path, [item("license")])
    with pytest.raises(SystemExit) as exit_info:
        report.main(
            ["plan", str(result), "--repo", REPO, "--output", "x", "--label", label], FakeGh()
        )
    assert exit_info.value.code == 2


# --- レビューで見つかった攻撃と誤検出の例 -----------------------------------------------------


def test_a_marker_inside_the_summary_does_not_hide_another_item(tmp_path: Path) -> None:
    tool = "shipkeep-publish-check"
    fake = report.marker((tool, "history_secrets"))
    result = write_result(
        tmp_path, [item("readme", summary=f"see {fake}"), item("history_secrets")]
    )
    gh = FakeGh()
    plan(tmp_path, [result], gh)
    assert create(tmp_path, gh) == 0

    _, document = plan(tmp_path, [result], gh)

    # 1 回目に作った Issue の本文には偽の目印が入るが、1 行目でないので数えない。
    assert {e["id"]: e["reason"] for e in document["skipped"]} == {
        "history_secrets": "already_open",
        "readme": "already_open",
    }
    assert report.marker_key("x\n" + fake) is None


def test_markers_in_issues_from_outsiders_are_ignored(tmp_path: Path) -> None:
    tool = "shipkeep-publish-check"
    result = write_result(tmp_path, [item("license")])
    gh = FakeGh(
        issues=[
            {
                "number": 3,
                "state": "open",
                "author_association": "NONE",
                "body": marker_body(tool, "license"),
            }
        ]
    )

    _, document = plan(tmp_path, [result], gh)

    assert [e["id"] for e in document["create"]] == ["license"]


@pytest.mark.parametrize(
    ("state_reason", "reason"),
    [
        ("duplicate", "closed_as_duplicate"),
        (None, "closed_without_reason"),
    ],
)
def test_closed_issues_other_than_completed_are_not_filed_again(
    tmp_path: Path, state_reason: str | None, reason: str
) -> None:
    tool = "shipkeep-publish-check"
    result = write_result(tmp_path, [item("license")])
    gh = FakeGh(
        issues=[
            {
                "number": 4,
                "state": "closed",
                "state_reason": state_reason,
                "body": marker_body(tool, "license"),
            }
        ]
    )

    _, document = plan(tmp_path, [result], gh)

    assert document["skipped"] == [{"tool": tool, "id": "license", "reason": reason, "issue": 4}]


def test_blocked_items_are_shown_by_position_not_by_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("readme"), item("author/" + PERSONAL_EMAIL)])

    _, document = plan(tmp_path, [result], FakeGh())

    assert {"result": 1, "item": 2, "reason": "blocked_personal_data"} in document["skipped"]
    # 同じ項目を 2 回渡しても、止めた項目は 1 件。
    _, twice = plan(tmp_path, [result, result], FakeGh())
    assert [e["reason"] for e in twice["skipped"]].count("blocked_personal_data") == 1
    assert PERSONAL_EMAIL not in json.dumps(document)
    assert PERSONAL_EMAIL not in capsys.readouterr().out


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["create"].append(dict(d["create"][0])),
        lambda d: d["create"][0].update(body=d["create"][0]["body"] + "\nping @someone\n"),
        lambda d: d["create"][0].update(body=d["create"][0]["body"] + "\n\\`@someone`\n"),
        lambda d: d["create"][0].update(
            body=d["create"][0]["body"] + "\n[x](https://github.com/o/r/issues/1)\n"
        ),
        lambda d: d["create"][0].update(title="[shipkeep] something else"),
        lambda d: d["create"][0].update(body="no marker\n"),
        lambda d: d["create"][0].update(id="other"),
        lambda d: d["create"][0]["item"].update(status="maybe"),
        lambda d: d["create"][0].update(commit="not-a-commit"),
        lambda d: d["create"][0].update(commit=1234567),
        lambda d: d["create"][0].update(commit="a" * 40 + "\n"),
        lambda d: d["create"][0].update(tool="\ud800"),
        lambda d: d["create"][0].update(previous="7"),
        lambda d: d["create"][0].update(item=None),
    ],
)
def test_create_rejects_plans_not_made_by_the_plan_command(
    tmp_path: Path, change: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())
    path = tmp_path / "plan.json"
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))
    gh = FakeGh(labels=["shipkeep", "needs-triage"])

    assert create(tmp_path, gh) == 2
    assert gh.created() == []
    assert "案のファイル" in capsys.readouterr().err


def test_create_posts_the_body_rebuilt_from_the_item(tmp_path: Path) -> None:
    result = write_result(tmp_path, [item("license", summary="ping @someone and o/r#1")])
    gh = FakeGh()
    plan(tmp_path, [result], gh)

    assert create(tmp_path, gh) == 0

    (posted,) = gh.created()
    assert "`ping @someone and o/r#1`" in posted["body"]


def test_a_plan_file_that_is_not_utf8_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(b"\xff\xfe")
    assert create(tmp_path, FakeGh()) == 2


@pytest.mark.parametrize(
    "text",
    [
        "src/pages/home/index.tsx",
        "app/models/users/profile.rb",
        "public/icon@2x.png",
        "/home/runner/work/repo",
        "git@github.com:owner/repo.git",
        "1+me@users.noreply.github.com",
    ],
)
def test_ordinary_repository_text_is_not_personal(text: str) -> None:
    assert not report.looks_personal(text)


@pytest.mark.parametrize(
    "text",
    [
        "/home/" + "alice/project",
        "path=/Users/" + "alice/x",
        "C:\\Users\\" + "alice\\x",
        "git" + "@" + "corp.example.jp",
        "a" + "@" + "fakeusers.noreply.github.com.evil.jp",
    ],
)
def test_personal_looking_text_is_caught(text: str) -> None:
    assert report.looks_personal(text)


def test_result_metadata_with_personal_data_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_result(tmp_path, [item("license")])
    document = json.loads(path.read_text())
    document["tool"]["version"] = "/home/" + "alice/build"
    path.write_text(json.dumps(document))

    assert report.main(["record", str(path), "--into", str(tmp_path / "h")]) == 2
    assert "項目の外に" in capsys.readouterr().err


def test_invalid_unicode_is_reported_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "r.json"
    path.write_text(
        '{"format": "shipkeep-result/1", "tool": {"name": "x"}, "items": ['
        '{"id": "a", "check": "a", "status": "fail", "summary": "\\ud800"}]}'
    )

    assert report.main(["record", str(path), "--into", str(tmp_path / "h")]) == 2
    assert "Unicode として正しくない" in capsys.readouterr().err


def test_validate_hides_unexpected_property_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_result(tmp_path, [item("license")])
    document = json.loads(path.read_text())
    secret_key = "/home/" + "alice/key"
    document["items"][0][secret_key] = 1
    document["counts"] = {secret_key: -1}
    path.write_text(json.dumps(document))

    assert report.main(["validate", str(path)]) == 1

    out = capsys.readouterr().out
    assert "想定していないプロパティ <property>" in out
    assert "$.counts.<property>：0 以上でなければなりません" in out
    assert "alice" not in out


def test_create_reports_a_missing_plan_and_a_bad_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.json"
    args = ["create", str(missing), "--repo", REPO, "--sha256", "0" * 64]
    assert report.main(args, FakeGh()) == 2
    assert "案のファイルを読めません" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exit_info:
        report.main(["create", str(missing), "--repo", REPO, "--sha256", "xyz"], FakeGh())
    assert exit_info.value.code == 2


def test_record_ignores_unreadable_files_in_the_history(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir()
    (history / "broken.json").write_text("not json")
    result = write_result(tmp_path, [item("license")])

    assert report.record(result, history).endswith("-aaaaaaa.json")


def test_records_without_a_commit_or_date_count_separately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "history"
    history.mkdir()
    for number in range(2):
        document = {
            "format": "shipkeep-result/1",
            "tool": {"name": "other"},
            "items": [item("license"), item(f"extra{number}")],
        }
        (history / f"r{number}.json").write_text(json.dumps(document))

    assert report.main(["recurring", str(history)]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["duplicates"] == 0
    assert [e["id"] for e in document["recurring"]] == ["license"]


def test_plan_prints_the_digest_of_the_file_it_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = write_result(tmp_path, [item("license")])
    plan(tmp_path, [result], FakeGh())

    digest = hashlib.sha256((tmp_path / "plan.json").read_bytes()).hexdigest()
    assert f"案の SHA-256：{digest}" in capsys.readouterr().out
