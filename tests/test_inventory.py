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


def test_workflow_tools_are_found_in_workflows(tmp_path: Path) -> None:
    workflow = (
        "jobs:\n  lint:\n    steps:\n"
        "      - run: actionlint\n"
        "      - run: zizmor .\n"
        "      - uses: github/codeql-action/init@0000000000000000000000000000000000000000\n"
    )
    repo = GitRepo(tmp_path)
    repo.commit({".github/workflows/ci.yml": workflow, ".github/dependabot.yml": "version: 2\n"})

    result = inventory.inventory(tmp_path)

    for aspect in ("ci", "workflow_lint", "workflow_security", "codeql", "dependency_updates"):
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
    assert "not inside a Git repository" in capsys.readouterr().err


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
