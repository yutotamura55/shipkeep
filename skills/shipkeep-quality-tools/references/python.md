# Python

Checked on 2026-11-03. Check the current version and status of each tool before installing.

| Aspect | Recommended | Alternatives and notes |
| --- | --- | --- |
| Format | Ruff (`ruff format`) | Black |
| Lint | Ruff (`ruff check`) | flake8, pylint. Ruff covers most of their rules in one tool |
| Type check | mypy | Pyright. Astral's ty was still in beta in mid-2026. New projects use `strict = true` |
| Security lint | Ruff `S` rules (flake8-bandit equivalent), plus Semgrep/Opengrep from the common layer | Bandit |
| Tests | pytest | unittest (standard library) |
| Coverage | pytest-cov (`--cov`, `--cov-fail-under=<floor>`, `--cov-report=xml` for diff coverage) | coverage.py directly |
| Unused code | vulture | vulture reports with a confidence score and misses or misreports code that is called dynamically: review its findings |
| Unused dependencies | Research the current tool | deptry is a candidate; its status was not verified |
| Dependency audit | pip-audit | OSV-Scanner |

Notes:

- Put tool settings in `pyproject.toml` when the project has one.
- Adding Ruff's `S` rules to an existing project usually finds `assert` in tests (`S101`), because pytest relies on `assert`. Ignoring `S101` for test files only is an exception under principle 6 of `change-principles.md`: put it in the plan with this reason, get approval, and explain it in a configuration comment and in the pull request.
