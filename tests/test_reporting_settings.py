"""公開した後に外部から届くものへの備えの点検 (#26)。

SECURITY.md、非公開の脆弱性報告、Dependabot alerts、Actions の GITHUB_TOKEN の権限。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo

import scan

GITHUB_IDS = ["private_reporting", "dependabot_alerts", "workflow_token"]
REPOSITORY = "repos/octo/demo"


class FakeApi:
    """GitHub API の代わり。パスごとに (HTTP の状態コード, 本文) を返す。"""

    def __init__(self, responses: dict[str, tuple[int, Any] | None]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def __call__(self, path: str) -> tuple[int, Any] | None:
        self.requested.append(path)
        return self.responses.get(path)


def ready_responses(**overrides: tuple[int, Any] | None) -> dict[str, tuple[int, Any] | None]:
    """すべての備えが整った公開リポジトリの応答。``overrides`` で一部を変える。"""
    responses: dict[str, tuple[int, Any] | None] = {
        REPOSITORY: (200, {"private": False, "permissions": {"admin": True}}),
        f"{REPOSITORY}/private-vulnerability-reporting": (200, {"enabled": True}),
        f"{REPOSITORY}/vulnerability-alerts": (204, None),
        f"{REPOSITORY}/actions/permissions/workflow": (
            200,
            {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
        ),
    }
    for name, response in overrides.items():
        responses[f"{REPOSITORY}/{name}" if name != "repository" else REPOSITORY] = response
    return responses


def github_checks(root: Path, api: FakeApi) -> dict[str, dict[str, Any]]:
    checks = scan.check_github_readiness(root, api=api)
    assert [c["id"] for c in checks] == GITHUB_IDS
    return {c["id"]: c for c in checks}


def repo_with_remote(path: Path, url: str | None = "https://github.com/octo/demo.git") -> GitRepo:
    repo = GitRepo(path)
    repo.commit({"a.txt": "a\n"})
    if url is not None:
        repo.git("remote", "add", "origin", url)
    return repo


def statuses(checks: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {name: check["status"] for name, check in checks.items()}


# --- SECURITY.md ---------------------------------------------------------------


def security_policy(root: Path) -> dict[str, Any]:
    found = next(c for c in scan.scan(root)["checks"] if c["id"] == "security_policy")
    assert isinstance(found, dict)
    return found


@pytest.mark.parametrize("path", ["SECURITY.md", ".github/SECURITY.md", "docs/security.md"])
def test_security_policy_passes_in_each_place_github_reads(tmp_path: Path, path: str) -> None:
    GitRepo(tmp_path).commit({path: "Report vulnerabilities through GitHub.\n"})

    assert security_policy(tmp_path)["status"] == "pass"


def test_security_policy_fails_without_the_file(tmp_path: Path) -> None:
    GitRepo(tmp_path).commit({"README.md": "# demo\n", "src/SECURITY.md": "elsewhere\n"})

    assert security_policy(tmp_path)["status"] == "fail"


def test_security_policy_fails_when_the_file_is_empty(tmp_path: Path) -> None:
    GitRepo(tmp_path).commit({"SECURITY.md": "\n"})

    assert security_policy(tmp_path)["status"] == "fail"


def test_security_policy_is_unknown_without_commits(tmp_path: Path) -> None:
    GitRepo(tmp_path)

    assert security_policy(tmp_path)["status"] == "unknown"


# --- GitHub の設定 ----------------------------------------------------------------


def test_github_checks_pass_when_everything_is_ready(tmp_path: Path) -> None:
    repo_with_remote(tmp_path)

    checks = github_checks(tmp_path, FakeApi(ready_responses()))

    assert statuses(checks) == dict.fromkeys(GITHUB_IDS, "pass")


def test_github_checks_fail_and_name_each_problem(tmp_path: Path) -> None:
    repo_with_remote(tmp_path)
    api = FakeApi(
        ready_responses(
            **{
                "private-vulnerability-reporting": (200, {"enabled": False}),
                "vulnerability-alerts": (404, {"message": "Not Found"}),
                "actions/permissions/workflow": (
                    200,
                    {
                        "default_workflow_permissions": "write",
                        "can_approve_pull_request_reviews": True,
                    },
                ),
            }
        )
    )

    checks = github_checks(tmp_path, api)

    assert statuses(checks) == dict.fromkeys(GITHUB_IDS, "fail")
    assert [f["kind"] for f in checks["private_reporting"]["findings"]] == [
        "private_reporting_disabled"
    ]
    assert [f["kind"] for f in checks["dependabot_alerts"]["findings"]] == [
        "dependabot_alerts_disabled"
    ]
    assert [f["kind"] for f in checks["workflow_token"]["findings"]] == [
        "workflow_token_writable",
        "workflow_can_approve_reviews",
    ]


def test_github_checks_do_not_apply_without_a_github_remote(tmp_path: Path) -> None:
    repo_with_remote(tmp_path, "https://gitlab.com/octo/demo.git")
    api = FakeApi({})

    assert statuses(github_checks(tmp_path, api)) == dict.fromkeys(GITHUB_IDS, "not_applicable")
    assert api.requested == []


def test_github_checks_are_unknown_when_gh_cannot_be_used(tmp_path: Path) -> None:
    repo_with_remote(tmp_path)

    checks = github_checks(tmp_path, FakeApi(ready_responses(repository=None)))

    assert statuses(checks) == dict.fromkeys(GITHUB_IDS, "unknown")


@pytest.mark.parametrize("permissions", [{"admin": False}, {}, None])
def test_a_404_is_not_read_as_disabled_without_admin_access(
    tmp_path: Path, permissions: dict[str, bool] | None
) -> None:
    """管理者でない人には、有効な設定も 404 に見える。そのため確認できないと報告する。"""
    repo_with_remote(tmp_path)
    body: dict[str, Any] = {"private": False}
    if permissions is not None:
        body["permissions"] = permissions
    api = FakeApi(
        ready_responses(
            repository=(200, body),
            **{
                "private-vulnerability-reporting": (404, {"message": "Not Found"}),
                "vulnerability-alerts": (404, {"message": "Not Found"}),
                "actions/permissions/workflow": (404, {"message": "Not Found"}),
            },
        )
    )

    checks = github_checks(tmp_path, api)

    assert statuses(checks) == dict.fromkeys(GITHUB_IDS, "unknown")
    assert api.requested == [REPOSITORY]


@pytest.mark.parametrize(
    "name",
    ["private-vulnerability-reporting", "vulnerability-alerts", "actions/permissions/workflow"],
)
@pytest.mark.parametrize("response", [None, (500, None), (200, "not an object"), (403, None)])
def test_each_setting_is_unknown_when_its_answer_cannot_be_read(
    tmp_path: Path, name: str, response: tuple[int, Any] | None
) -> None:
    repo_with_remote(tmp_path)

    checks = github_checks(tmp_path, FakeApi(ready_responses(**{name: response})))

    broken = {
        "private-vulnerability-reporting": "private_reporting",
        "vulnerability-alerts": "dependabot_alerts",
        "actions/permissions/workflow": "workflow_token",
    }[name]
    expected = dict.fromkeys(GITHUB_IDS, "pass")
    expected[broken] = "unknown"
    assert statuses(checks) == expected


def test_private_reporting_waits_for_a_private_repository_to_become_public(
    tmp_path: Path,
) -> None:
    """非公開の報告は公開リポジトリでしか有効にできない。公開の前は該当しないとする。"""
    repo_with_remote(tmp_path)
    api = FakeApi(
        ready_responses(repository=(200, {"private": True, "permissions": {"admin": True}}))
    )

    checks = github_checks(tmp_path, api)

    assert checks["private_reporting"]["status"] == "not_applicable"
    assert "公開" in checks["private_reporting"]["summary"]
    assert f"{REPOSITORY}/private-vulnerability-reporting" not in api.requested


def test_scan_includes_the_new_checks(tmp_path: Path) -> None:
    GitRepo(tmp_path).commit({"a.txt": "a\n"})

    ids = [c["id"] for c in scan.scan(tmp_path)["checks"]]

    assert ids[-4:] == ["github_settings", *GITHUB_IDS]
    assert "security_policy" in ids


# --- gh の呼び出し -----------------------------------------------------------------


def fake_gh(directory: Path, output: str, code: int) -> None:
    """``output`` を出して ``code`` で終わる偽の gh を ``directory`` に置く。"""
    script = directory / "gh"
    script.write_text(
        f"#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\necho 'local detail' >&2\nexit {code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def use_fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, code: int) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh(bin_dir, output, code)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_gh_api_reads_the_status_and_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = 'HTTP/2.0 200 OK\nContent-Type: application/json\n\n{"enabled": true}'
    use_fake_gh(tmp_path, monkeypatch, output, 0)

    assert scan.gh_api("repos/octo/demo/private-vulnerability-reporting") == (
        200,
        {"enabled": True},
    )


def test_gh_api_reads_a_404_although_gh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_gh(tmp_path, monkeypatch, "HTTP/2.0 404 Not Found\nX-A: b\n\n", 1)

    assert scan.gh_api("repos/octo/demo/vulnerability-alerts") == (404, None)


def test_gh_api_reads_an_empty_204(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_gh(tmp_path, monkeypatch, "HTTP/2.0 204 No Content\nX-A: b\n", 0)

    assert scan.gh_api("repos/octo/demo/vulnerability-alerts") == (204, None)


@pytest.mark.parametrize("output", ["not logged in", "HTTP/2.0 200 OK\n\n{broken"])
def test_gh_api_returns_none_when_the_answer_is_not_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    use_fake_gh(tmp_path, monkeypatch, output, 1)

    assert scan.gh_api("repos/octo/demo") is None


def test_gh_api_returns_none_without_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert scan.gh_api("repos/octo/demo") is None


@pytest.mark.parametrize("body", [{}, {"enabled": None}, {"enabled": "true"}])
def test_private_reporting_is_unknown_when_the_answer_has_no_boolean(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    repo_with_remote(tmp_path)
    api = FakeApi(ready_responses(**{"private-vulnerability-reporting": (200, body)}))

    assert github_checks(tmp_path, api)["private_reporting"]["status"] == "unknown"


@pytest.mark.parametrize(
    "body",
    [
        {"default_workflow_permissions": "read"},
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": None},
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": "false"},
        {"can_approve_pull_request_reviews": False},
        {"default_workflow_permissions": "admin", "can_approve_pull_request_reviews": False},
    ],
)
def test_workflow_token_does_not_pass_on_a_missing_or_odd_value(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    repo_with_remote(tmp_path)
    api = FakeApi(ready_responses(**{"actions/permissions/workflow": (200, body)}))

    assert github_checks(tmp_path, api)["workflow_token"]["status"] == "unknown"


def test_workflow_token_still_fails_when_only_one_value_is_known(tmp_path: Path) -> None:
    """書き込みできると分かれば、もう一方の値がなくても指摘する。"""
    repo_with_remote(tmp_path)
    api = FakeApi(
        ready_responses(
            **{"actions/permissions/workflow": (200, {"default_workflow_permissions": "write"})}
        )
    )

    found = github_checks(tmp_path, api)["workflow_token"]

    assert found["status"] == "fail"
    assert [f["kind"] for f in found["findings"]] == ["workflow_token_writable"]


@pytest.mark.parametrize("visibility", [None, "public"])
def test_private_reporting_is_unknown_when_the_visibility_is_not_shown(
    tmp_path: Path, visibility: str | None
) -> None:
    repo_with_remote(tmp_path)
    body: dict[str, Any] = {"permissions": {"admin": True}}
    if visibility is not None:
        body["private"] = visibility
    api = FakeApi(ready_responses(repository=(200, body)))

    assert github_checks(tmp_path, api)["private_reporting"]["status"] == "unknown"
