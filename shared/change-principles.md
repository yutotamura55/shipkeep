# Principles for changing a target repository

Every shipkeep skill that changes the repository it works on follows these principles.
Examples are refactoring, fixing a reported vulnerability, and adding quality tools such as a linter or a type checker.
Inspection scripts never change the repository; these principles apply to the agent's changes after an inspection.

## Before changing

1. **Plan and approval.** Show the user the purpose, the files you will change, what you will leave untouched, and how you will verify the result.
   Change nothing until the user approves the plan.
   If the work turns out to need something outside the approved plan (a new dependency, a change of behavior, more files), stop and ask again.
2. **Branch.** Fetch the latest default branch of the remote, and create a dedicated branch from it. Without a remote, branch from the local default branch.
   Keep one purpose per branch. Put unrelated problems you notice in the report instead of fixing them on the same branch.

## While changing

3. **Checks before each commit.** Run the repository's own check command (formatting, lint, type checking, tests) before each commit, and commit only when it passes.
   If the repository has no check command, run the tools that exist (compiler warnings, an existing linter) on the changed files, and say what you ran.
4. **No silencing.** Do not make a failure disappear with a suppression comment (`# type: ignore`, `@ts-ignore`, `@Suppress`, `# noqa`, and so on) or by relaxing the configuration.
   When an exception is unavoidable, explain the reason in a code comment and in the pull request.
5. **Separate commits.** Keep a commit that reformats files in bulk, a commit that changes behavior, and a commit that only restructures code apart from each other.
6. **No secrets or personal data.** Never write the value of a secret or personal data (email addresses other than a GitHub noreply address, local user names, local paths) in code, commits, pull requests, or Issues.
   Refer to such a value by its role and location only.

## After changing

7. **Review.** Before opening a pull request, have the complete diff reviewed independently, for example by a separate agent session that sees the requirements and the diff but not your reasoning.
   Treat each finding as a candidate: confirm it in the code before fixing it, and reject it only with evidence.
8. **Pull request, not merge.** Open a pull request at most. Never merge it, enable auto-merge, or push to the default branch. The user decides whether to merge.
9. **Report.** Report what you changed, the checks you ran with their results, and what you could not verify, as separate items.
