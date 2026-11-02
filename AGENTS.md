# Repository instructions

## Pull requests targeting main

- Only the user may merge pull requests into `main`. The assistant must not merge them through GitHub, a CLI, an API, or any other tool.
- Do not enable auto-merge, enqueue a pull request for merging, or configure automation to merge into `main`.
- Do not push changes directly to remote `main`.
- Instructions such as "continue", "proceed", "OK", 「続けてください」, or 「進めてください」 do not authorize a merge or a change to this policy.

## Branches

- Create one branch per Issue from the latest `origin/main`, named `feat/<issue-number>-<short-purpose>`, `fix/<issue-number>-<short-purpose>`, or `chore/<issue-number>-<short-purpose>`.
- Keep a branch focused on one Issue.

## Checks before committing

- Run `scripts/check.sh` (Ruff format and lint, mypy, pytest) before each commit, and commit only when it passes. README "開発" shows how to run it in the dev container.
- Do not silence a failure with a suppression comment or by relaxing the configuration. When an exception is unavoidable, explain it in a code comment and in the pull request.

## Rules specific to this repository

- This repository is public. Do not write personal information (email addresses other than the GitHub noreply address, local user names, local paths) or business information in code, tests, commits, Issues, or pull requests.
- Write code comments and docstrings in Japanese. `SKILL.md` and the text a script prints (such as `--help` and error messages) stay in English.
- Scripts under `skills/` must use only the Python standard library.
- Inspection scripts (such as `scan.py`) must stay read-only toward the repository they inspect. Inspections may run unattended, so they never change the target.
- Skills may change the target repository after an inspection (refactoring, fixing a vulnerability, adding quality tools). The agent's changes follow `shared/change-principles.md`: plan and user approval first, a dedicated branch, the target's checks before each commit, review, and a pull request that only the user merges.
- `shared/` holds the originals of documents that several skills use. The skills CLI installs only a skill's own directory, so each skill that needs such a document keeps an identical copy in its `references/`. Edit the original, then copy it again; `tests/test_shared_references.py` fails when a copy differs.
- Script output and skill reports must never contain the value of a secret or of personal data. Report the location (commit, file, line) and the kind only.
- Tests must not contain a string that looks like a real secret. Build fake secrets at run time (for example by joining parts), so that secret scanners and push protection do not flag this repository.
