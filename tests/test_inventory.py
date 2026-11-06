"""shipkeep-quality-tools の inventory.py を、使い捨てのリポジトリで確かめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo

import inventory


def row(result: dict[str, Any], scope: str, aspect: str) -> dict[str, Any]:
    for item in result["aspects"]:
        if item["scope"] == scope and item["aspect"] == aspect:
            return dict(item)
    raise AssertionError(f"{scope}.{aspect} is missing")


def test_an_empty_python_project_lacks_every_language_tool(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "print('hi')\n"})

    result = inventory.inventory(tmp_path)

    assert [lang["id"] for lang in result["languages"]] == ["python"]
    for aspect in inventory.LANGUAGE_ASPECTS:
        assert row(result, "python", aspect)["status"] == "fail"
    assert row(result, "repository", "ci")["status"] == "fail"


def test_tools_in_pyproject_are_found_with_their_evidence(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "pyproject.toml": (
                "[tool.ruff.lint]\n"
                'select = ["E", "S"]\n'
                "[tool.mypy]\nstrict = true\n"
                "[tool.coverage.run]\nbranch = true\n"
            ),
            "tests/test_app.py": "def test_ok() -> None:\n    pass\n",
        }
    )

    result = inventory.inventory(tmp_path)

    assert row(result, "python", "lint")["tools"] == ["ruff"]
    assert row(result, "python", "type_check")["evidence"] == ["pyproject.toml"]
    assert row(result, "python", "security_lint")["tools"] == ["ruff S rules"]
    assert row(result, "python", "coverage")["status"] == "pass"
    assert row(result, "python", "tests")["tools"] == ["test files"]


def test_ruff_without_the_s_rules_is_not_a_security_lint(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"pyproject.toml": '[tool.ruff.lint]\nselect = ["E", "SIM"]\n'})

    result = inventory.inventory(tmp_path)

    assert row(result, "python", "security_lint")["status"] == "fail"


def test_a_typescript_project_is_read_from_package_json(tmp_path: Path) -> None:
    package = {
        "scripts": {"check": "eslint . && tsc --noEmit", "test": "vitest run --coverage"},
        "devDependencies": {"eslint": "^9", "typescript": "^5", "vitest": "^3", "knip": "^5"},
    }
    repo = GitRepo(tmp_path)
    repo.commit({"package.json": json.dumps(package)})

    result = inventory.inventory(tmp_path)

    assert [lang["id"] for lang in result["languages"]] == ["javascript"]
    for aspect in ("lint", "type_check", "tests", "coverage", "unused_code", "unused_dependencies"):
        assert row(result, "javascript", aspect)["status"] == "pass", aspect
    assert row(result, "javascript", "format")["status"] == "fail"
    assert row(result, "repository", "check_command")["tools"] == ["npm run check"]


def test_one_language_does_not_borrow_another_languages_config(tmp_path: Path) -> None:
    package = {"devDependencies": {"jest": "^30"}, "scripts": {"cov": "jest --coverage"}}
    repo = GitRepo(tmp_path)
    repo.commit({"package.json": json.dumps(package), "pyproject.toml": "[project]\n"})

    result = inventory.inventory(tmp_path)

    assert row(result, "javascript", "coverage")["status"] == "pass"
    assert row(result, "python", "coverage")["status"] == "fail"


def test_a_language_without_reference_is_unknown_and_flagged(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"go.mod": "module example.com/demo\n", "main.go": "package main\n"})

    result = inventory.inventory(tmp_path)

    assert result["languages"] == [
        {
            "id": "go",
            "name": "Go",
            "reference": False,
            "detected_by": "manifest",
            "evidence": ["go.mod"],
        }
    ]
    assert row(result, "go", "lint")["status"] == "unknown"


def test_workflow_checks_are_not_applicable_without_github_actions(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n"})

    result = inventory.inventory(tmp_path)

    assert row(result, "repository", "workflow_lint")["status"] == "not_applicable"
    assert row(result, "repository", "workflow_security")["status"] == "not_applicable"
    assert row(result, "repository", "dependency_review")["status"] == "not_applicable"


def test_workflow_tools_are_found_in_workflows(tmp_path: Path) -> None:
    workflow = (
        "jobs:\n  lint:\n    steps:\n"
        "      - run: actionlint\n"
        "      - run: zizmor .\n"
        "      - uses: github/codeql-action/init@0000000000000000000000000000000000000000\n"
        "      - uses: actions/dependency-review-action@1111111111111111111111111111111111111111\n"
    )
    repo = GitRepo(tmp_path)
    repo.commit({".github/workflows/ci.yml": workflow, ".github/dependabot.yml": "version: 2\n"})

    result = inventory.inventory(tmp_path)

    aspects = (
        "ci",
        "workflow_lint",
        "workflow_security",
        "codeql",
        "dependency_updates",
        "dependency_review",
    )
    for aspect in aspects:
        assert row(result, "repository", aspect)["status"] == "pass", aspect


def test_codeql_is_unknown_without_a_workflow(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({".github/workflows/ci.yml": "jobs: {}\n"})

    assert row(inventory.inventory(tmp_path), "repository", "codeql")["status"] == "unknown"


def test_untracked_and_node_modules_files_are_ignored(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "package.json": "{}",
            "node_modules/x/package.json": json.dumps({"devDependencies": {"eslint": "1"}}),
        }
    )
    (tmp_path / "ruff.toml").write_text("")

    result = inventory.inventory(tmp_path)

    assert row(result, "javascript", "lint")["status"] == "fail"
    assert [lang["id"] for lang in result["languages"]] == ["javascript"]


def test_counts_match_the_rows(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n"})

    result = inventory.inventory(tmp_path)

    assert sum(result["counts"].values()) == len(result["aspects"])


def test_main_prints_json_without_the_local_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n"})

    assert inventory.main([str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["schema"] == 1
    assert str(tmp_path) not in captured.out + captured.err


def test_main_exits_with_2_outside_a_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert inventory.main([str(tmp_path)]) == 2
    assert "Git のリポジトリの中にありません" in capsys.readouterr().err


def test_a_language_found_only_by_sources_is_marked(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"package.json": "{}", "backend/app.py": "print(1)\n", "native/main.lua": ""})

    result = inventory.inventory(tmp_path)

    found = {lang["id"]: lang["detected_by"] for lang in result["languages"]}
    assert found == {"python": "sources", "javascript": "manifest"}
    assert result["unrecognized_source_extensions"] == ["lua"]


def test_ruff_settings_in_ruff_toml_and_rule_codes_are_read(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"ruff.toml": '[lint]\nextend-select = ["S1", "S6"]\n', "app.py": "x = 1\n"})

    result = inventory.inventory(tmp_path)

    assert row(result, "python", "security_lint")["tools"] == ["ruff S rules"]


def test_tools_listed_in_setup_py_are_found(tmp_path: Path) -> None:
    setup = "setup(extras_require={'dev': ['pytest', 'mypy', 'flake8']})\n"
    repo = GitRepo(tmp_path)
    repo.commit({"setup.py": setup})

    result = inventory.inventory(tmp_path)

    for aspect in ("lint", "type_check", "tests"):
        assert row(result, "python", aspect)["status"] == "pass", aspect


def test_coverage_in_a_jest_config_is_found(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {"package.json": "{}", "jest.config.js": "module.exports = {collectCoverage: true}\n"}
    )

    assert row(inventory.inventory(tmp_path), "javascript", "coverage")["status"] == "pass"


def test_a_ci_job_named_check_is_not_a_check_command(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({".gitlab-ci.yml": "check:\n  script: echo\n", "app.py": "x = 1\n"})

    result = inventory.inventory(tmp_path)

    assert row(result, "repository", "check_command")["status"] == "fail"
    assert row(result, "repository", "ci")["tools"] == ["GitLab CI"]


def test_vendored_files_are_not_evidence(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit(
        {
            "package.json": "{}",
            "node_modules/x/.eslintrc.json": "{}",
            "vendor/lib/helper.py": "x = 1\n",
        }
    )

    result = inventory.inventory(tmp_path)

    assert [lang["id"] for lang in result["languages"]] == ["javascript"]
    assert row(result, "javascript", "lint")["status"] == "fail"


def test_a_symlinked_config_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("mypy\n")
    repo = GitRepo(tmp_path / "repo")
    (tmp_path / "repo" / "requirements.txt").symlink_to(outside)
    repo.git("add", "requirements.txt")
    repo.commit({"app.py": "x = 1\n"})

    result = inventory.inventory(tmp_path / "repo")

    assert row(result, "python", "type_check")["status"] == "fail"


def test_cmake_projects_are_listed_as_c_cpp(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"CMakeLists.txt": "project(demo)\n", "main.cpp": "int main() {}\n"})

    result = inventory.inventory(tmp_path)

    assert [lang["id"] for lang in result["languages"]] == ["c_cpp"]


def test_unrecognized_sources_are_listed_by_extension(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"a.lua": "", "b.lua": "x = 1\n", "c.lua": "y = 2\n", "README.md": "# x\n"})

    result = inventory.inventory(tmp_path)

    assert result["languages"] == []
    assert result["unrecognized_source_extensions"] == ["lua"]


def test_a_symlinked_parent_directory_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ci.yml").write_text("run: zizmor .\n")
    repo = GitRepo(tmp_path / "repo")
    repo.commit({".github/workflows/ci.yml": "jobs: {}\n"})
    workflows = tmp_path / "repo" / ".github" / "workflows"
    (workflows / "ci.yml").unlink()
    workflows.rmdir()
    workflows.symlink_to(outside, target_is_directory=True)

    result = inventory.inventory(tmp_path / "repo")

    assert row(result, "repository", "workflow_security")["status"] == "fail"


def test_unused_code_and_unused_dependencies_are_separate(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"pyproject.toml": '[dependency-groups]\ndev = ["vulture"]\n'})

    result = inventory.inventory(tmp_path)

    assert row(result, "python", "unused_code")["status"] == "pass"
    assert row(result, "python", "unused_dependencies")["status"] == "fail"


@pytest.mark.parametrize(("name", "language"), [("src/main.cxx", "c_cpp"), ("script.kts", "jvm")])
def test_less_common_extensions_still_identify_the_language(
    tmp_path: Path, name: str, language: str
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({name: "x\n"})

    assert [lang["id"] for lang in inventory.inventory(tmp_path)["languages"]] == [language]


def test_main_exits_with_2_when_git_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    assert inventory.main([str(tmp_path)]) == 2
    assert "git を起動できません" in capsys.readouterr().err


def test_dependency_review_fails_when_workflows_lack_it(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    workflow = "jobs:\n  test:\n    steps:\n      - uses: actions/dependency-review\n"
    repo.commit({".github/workflows/ci.yml": workflow})

    assert row(inventory.inventory(tmp_path), "repository", "dependency_review")["status"] == "fail"


@pytest.mark.parametrize(
    ("files"),
    [
        {"package.json": '{"description": "uses actions/dependency-review-action@v4"}'},
        {".github/workflows/ci.yml": "  - uses: other-actions/dependency-review-action@v4\n"},
        {".github/workflows/ci.yml": "  - uses: actions/dependency-review-action-extra@v4\n"},
        {".github/workflows/ci.yml": "# uses: actions/dependency-review-action@v4\n"},
    ],
)
def test_dependency_review_is_only_the_action_in_a_workflow(
    tmp_path: Path, files: dict[str, str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({".github/workflows/base.yml": "jobs: {}\n", **files})

    assert row(inventory.inventory(tmp_path), "repository", "dependency_review")["status"] == "fail"


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/dependency-review-action@abc # v4",
        "        uses: 'actions/dependency-review-action@v4'",
        '      - uses: "actions/dependency-review-action@v4"',
        '      - "uses": actions/dependency-review-action@v4',
        "      - {uses: actions/dependency-review-action@v4}",
        "      - uses: >-\n          actions/dependency-review-action@v4",
    ],
)
def test_dependency_review_is_found_in_each_way_to_write_uses(tmp_path: Path, line: str) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({".github/workflows/review.yaml": "jobs:\n  r:\n    steps:\n" + line + "\n"})

    assert row(inventory.inventory(tmp_path), "repository", "dependency_review")["status"] == "pass"


@pytest.mark.parametrize(
    ("files", "aspect", "tool"),
    [
        ({"sgconfig.yml": "ruleDirs: [rules]\n"}, "custom_rules", "ast-grep"),
        ({".semgrep/no-raw-sql.yml": "rules: []\n"}, "custom_rules", "semgrep rules"),
        ({"eslint-suppressions.json": "{}\n"}, "baseline", "eslint bulk suppressions"),
        ({"mypy-baseline.txt": ""}, "baseline", "mypy-baseline"),
    ],
)
def test_project_rules_and_baselines_are_found(
    tmp_path: Path, files: dict[str, str], aspect: str, tool: str
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n", **files})

    found = row(inventory.inventory(tmp_path), "repository", aspect)

    assert found["status"] == "pass"
    assert found["tools"] == [tool]


@pytest.mark.parametrize("aspect", ["custom_rules", "baseline"])
def test_missing_rules_or_baseline_is_not_a_failure(tmp_path: Path, aspect: str) -> None:
    """規則と baseline は、問題の型や既存の違反があるときに提案する。ないこと自体は不足でない。"""
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n"})

    found = row(inventory.inventory(tmp_path), "repository", aspect)

    assert found["status"] == "not_applicable"
    assert "提案" in found["note"]


def test_a_declared_language_is_listed_before_any_file_exists(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"LICENSE": "MIT\n"})

    result = inventory.inventory(tmp_path, declared=("python", "javascript", "go"))

    found = {lang["id"]: lang for lang in result["languages"]}
    assert set(found) == {"python", "javascript", "go"}
    assert all(
        lang["detected_by"] == "declared" and lang["evidence"] == [] for lang in found.values()
    )
    python_rows = [row for row in result["aspects"] if row["scope"] == "python"]
    assert python_rows and all(row["status"] == "fail" for row in python_rows)
    go_rows = [row for row in result["aspects"] if row["scope"] == "go"]
    assert go_rows and all(row["status"] == "unknown" for row in go_rows)


def test_a_declared_language_that_files_show_keeps_its_evidence(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"pyproject.toml": "[project]\nname = 'x'\n"})

    result = inventory.inventory(tmp_path, declared=("python",))

    (python,) = result["languages"]
    assert python["detected_by"] == "manifest"
    assert python["evidence"] == ["pyproject.toml"]


def test_main_accepts_repeated_language_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"LICENSE": "MIT\n"})

    assert inventory.main([str(tmp_path), "--language", "python", "--language", "javascript"]) == 0

    result = json.loads(capsys.readouterr().out)
    # 並びは表 (REFERENCED_LANGUAGES、OTHER_LANGUAGES) の順で、引数の順ではない。
    assert {lang["id"] for lang in result["languages"]} == {"python", "javascript"}


def test_main_rejects_an_unknown_language(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        inventory.main([str(tmp_path), "--language", "cobol"])

    assert exit_info.value.code == 2
    # 誤って渡した値 (パスなど) を含めないため、argparse の文ではなく定型の文を出す。
    assert "cobol" not in capsys.readouterr().err


def test_a_failing_git_ls_files_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n"})
    real_run_git = inventory.run_git

    def failing_ls_files(git: str, root: Path, *args: str) -> tuple[int, str]:
        if args[:1] == ("ls-files",):
            return 128, ""
        return real_run_git(git, root, *args)

    monkeypatch.setattr(inventory, "run_git", failing_ls_files)

    with pytest.raises(inventory.InventoryError, match="ls-files"):
        inventory.inventory(tmp_path)


def test_an_oversized_config_file_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"pyproject.toml": "[tool.ruff]\nline-length = 100\n" + "#" * 200 + "\n"})
    monkeypatch.setattr(inventory, "MAX_CONFIG_BYTES", 100)

    result = inventory.inventory(tmp_path)

    rows = {row["aspect"]: row for row in result["aspects"] if row["scope"] == "python"}
    # 大きすぎる設定は本文を読まないため、[tool.ruff] があっても lint の Ruff は見つからない。
    # 言語は名前で見分ける。
    assert rows["lint"]["status"] == "fail"
    assert [lang["id"] for lang in result["languages"]] == ["python"]


def test_an_unreadable_config_file_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"pyproject.toml": "[tool.ruff]\n"})
    real_read_text = Path.read_text

    def unreadable(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if self.name == "pyproject.toml":
            raise OSError("permission denied")
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", unreadable)

    result = inventory.inventory(tmp_path)

    rows = {row["aspect"]: row for row in result["aspects"] if row["scope"] == "python"}
    assert rows["lint"]["status"] == "fail"
