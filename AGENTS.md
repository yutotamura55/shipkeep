# Repository instructions

## Merging pull requests

The assistant may merge a pull request into the repository's default branch (check it with `gh repo view --json defaultBranchRef`) itself when all of these hold:

1. At least one CI check ran on the pull request's head commit, and every check passed. When CI cannot run, or no check ran, do not merge.
2. The self-review and an independent review by a model of a different family are done, their Important findings are fixed, and the fixes were reviewed again.
3. The pull request targets the default branch. A pull request built on another pull request's branch waits until that one is merged and its base is changed to the default branch.
4. The change is not "critical risk" (below).
5. Items 1, 2, and 4 apply to the pull request's current head commit. After any new commit, redo them. Merge with `gh pr merge --match-head-commit <that commit>` (on older gh, `gh api -X PUT repos/<owner>/<repo>/pulls/<number>/merge -f sha=<that commit>`) so that only the judged revision is merged.

Choices listed under "確認してほしいこと" in the pull request body are merged as the assistant chose them; the user reviews them after the merge and changes them in a new pull request. A choice that a revert cannot undo makes the change critical risk.

A change is **critical risk** when it cannot be undone, harms people or money outside the repository, could break without any test noticing, or changes the safeguards themselves. In particular:

- payments, billing, or anything that costs money;
- authentication, authorization, sessions, rate limits, or cryptography;
- destructive changes to persistent data or its formats (dropping, renaming, or retyping columns or tables, bulk updates or deletes, backup and restore formats); adding a column or table only is not;
- production infrastructure, deployment, or secret and environment settings;
- publishing a release or changing signing;
- safeguards: CI workflows, branch protection, `AGENTS.md` and other agent rules, agent permissions;
  One change is exempt: increasing `fail_under` in `pyproject.toml` to the coverage percentage (two decimals, truncated) that a successful full run of `scripts/check.sh` reported, as "Checks before committing" requires, without narrowing the test or coverage scope and without changing any other check setting. It tightens the check and involves no judgment. Lowering it, or any other change to the check configuration, is a change to a safeguard (the user decided this on 2026-11-04).
  the skills this repository builds (`SKILL.md`, references, and scripts under `skills/`, and their originals in `shared/`) are the product, not agent rules for this repository, even when their steps change, merge, or grant permissions in a target repository. This repository's own `AGENTS.md`, CI, and branch protection remain safeguards, and a change under `skills/` or `shared/` is still critical risk when any other criterion in this section applies (for example personal data or a new dependency);
- handling of secrets or personal data, and history rewriting;
- irreversible external operations (sending messages to other people, deleting external resources, changing repository visibility);
- license or other legal texts;
- a new dependency, unless all of these hold: it exists in the official registry under exactly that name; it is established and maintained; its license is compatible; it runs no install script, or its script was read and found safe; its version and hash are pinned in the lockfile; and it passes the dependency audit. Lockfile-only changes count as dependency changes;
- a major dependency update, unless every breaking change in its changelog was traced to the code that uses it and verified by tests or by running it. When that investigation is large, do the update in a separate Issue.

Judge the risk as the highest of three judgments, none of which can lower another: the path and content rules, the implementing agent, and the different-family reviewer, which must give file, line, and reason. The path and content rules come from the maintainer's `pr_risk.py`, which lives outside this repository; when it is not available, check every changed path (both sides of a rename) and the changed lines of every file, including migrations and lockfiles, against the list above by hand, and say so in the pull request body. When the reviewer cannot run, treat the change as critical risk. Write the three judgments in the pull request body. When in doubt, treat the change as critical risk.

Leave a critical-risk pull request for the user to merge, and say why at the top of its body. Only an explicit request from the user to merge that specific pull request authorizes the assistant to merge it; general instructions such as "continue", "proceed", "OK", 「続けてください」, or 「進めてください」 do not.

Never push directly to the default branch, enable auto-merge, enqueue a pull request, or bypass branch protection (for example with `gh pr merge --admin`). Use the merge method this repository already uses. After merging, confirm that CI on the default branch passed and, where a merge deploys to production, that the deployment succeeded; if either fails, open a revert pull request and merge it under the same rules. Report every merged pull request to the user with its URL, its open choices, and the risk judgments.

## Branches

- Create one branch per Issue from the latest `origin/main`, named `feat/<issue-number>-<short-purpose>`, `fix/<issue-number>-<short-purpose>`, or `chore/<issue-number>-<short-purpose>`.
- Keep a branch focused on one Issue.

## Checks before committing

- Run `scripts/check.sh` (Ruff format and lint including the security rules, mypy, pytest with a coverage floor) before each commit, and commit only when it passes. README "開発" shows how to run it in the dev container.
- Do not silence a failure with a suppression comment or by relaxing the configuration. When an exception is unavoidable, explain it in a code comment and in the pull request.
- When tests raise coverage, raise `fail_under` in `pyproject.toml` to the new measured value (two decimals); never lower it to make a change pass.

## Rules specific to this repository

- This repository is public. Do not write personal information (email addresses other than the GitHub noreply address, local user names, local paths) or business information in code, tests, commits, Issues, or pull requests.
- Write code comments and docstrings in Japanese. Write the text a script prints for people in Japanese only: Issue titles and bodies, summaries, notes, remediations, `--help`, and error messages. Keep check ids, status names, JSON keys, and the Issue marker as they are, because programs read them. `SKILL.md` and `references/` stay in English. The user decided the comment rule on 2026-10-31 and the output language on 2026-11-06 (#45); an agent had earlier added "script output stays in English" without the user's decision.
- Scripts under `skills/` must use only the Python standard library.
- Inspection never changes the target repository: neither inspection scripts (such as `scan.py`) nor the agent steps of an inspection. Inspections may run unattended.
- Skills may change the target repository after an inspection (refactoring, fixing a vulnerability, adding quality tools). Such a skill keeps a copy of `shared/change-principles.md` in its `references/`, tells the agent to follow it, and defines no rule that contradicts it.
- `shared/` holds the originals of documents that several skills use, because the skills CLI installs only a skill's own directory. Edit the original, then copy it again. `tests/test_shared_references.py` fails when a copy is missing, differs, or is not referred to from `SKILL.md`.
- Script output and skill reports must never contain the value of a secret or of personal data. Report the location (commit, file, line) and the kind only.
- Tests must not contain a string that looks like a real secret. Build fake secrets at run time (for example by joining parts), so that secret scanners and push protection do not flag this repository.
