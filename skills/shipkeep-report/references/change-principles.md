# Principles for changing a target repository

Every shipkeep skill that changes the repository it works on follows these principles.
Examples are refactoring, fixing a reported vulnerability, and adding quality tools such as a linter or a type checker.
Inspection steps never change the repository; these principles apply to the changes the agent makes after an inspection.

## Before changing

1. **Plan and approval.** Show the user the purpose, the files you will change, what you will leave untouched, and how you will verify the result.
   Change nothing until the user approves the plan.
   If the work turns out to need something outside the approved plan (a new dependency, a change of behavior, more files), stop and ask again.
2. **Clean start.** Check the working tree first. If it has uncommitted changes, or the branch name you want already exists, stop and ask the user; never stash, discard, or overwrite their work.
3. **Branch.** Fetch the latest default branch of the remote, and create a dedicated branch from it. Without a remote, branch from the local default branch.
   Keep one purpose per branch. Put unrelated problems you notice in the report instead of fixing them on the same branch.
4. **Baseline.** Run the repository's own check command (formatting, lint, type checking, tests) once before changing anything, and record which checks already fail.
   If some already fail, tell the user and agree on how to proceed before you start.

## While changing

5. **Checks before each commit.** Run the check command before each commit. Commit only when your change adds no new failure compared with the baseline; when the baseline was clean, that means every check passes.
   If the repository has no check command, run the tools that exist (compiler warnings, an existing linter) on the changed files, and say what you ran.
6. **No silencing.** Do not make a failure disappear with a suppression comment (`# type: ignore`, `@ts-ignore`, `@Suppress`, `# noqa`, and so on) or by loosening an existing configuration.
   When an exception is unavoidable, explain the reason in a code comment and in the pull request.
   Introducing a new tool in stages is not silencing: a first configuration that existing code already passes, or a recorded baseline of existing findings that only new code must beat, is allowed when the plan shows the stages and how the rules will be tightened.
   A baseline is a committed file (for example ESLint's `eslint-suppressions.json`, `mypy-baseline.txt`). The check command also fails when the file lists findings that no longer exist, so fixed findings leave it, and a pull request that adds entries to the file is reviewed like a suppression: new code must not be added to the baseline.
7. **Deterministic changes first, in their own commit.** Before you change code by hand, apply what a tool can do the same way every time, in this order: the formatter, the linter's fixes marked safe, the fix attached to a rule (ast-grep, Semgrep), then a language codemod. Commit that separately, then make the remaining change in small steps.
   Keep a commit that reformats files in bulk, a commit that changes behavior, and a commit that only restructures code apart from each other.
8. **Never reveal secrets or personal data.** Never write the value of a secret or of personal data (email addresses other than a GitHub noreply address, local user names, local paths) anywhere: code, commits, pull requests, Issues, the plan, the report, or the conversation.
   Avoid commands whose output would print such a value. Refer to it by its kind and location (file, line, commit) only.

## After changing

9. **Review.** Before handing the change over, have the complete diff reviewed independently, for example by a separate agent session that sees the requirements and the diff but not your reasoning.
   Treat each finding as a candidate: confirm it in the code before fixing it, and reject it only with evidence.
10. **Hand over; merge only under the repository's own rules.** On a GitHub remote, open a pull request. Merge it only when the target repository's own instructions (for example its `AGENTS.md`) explicitly allow the agent to merge, and only after every condition those instructions set is met; the repository's list is the complete set of conditions (a typical list is passing CI, completed reviews, and a judgment that the change is not critical risk). Without such instructions, never merge. Never enable auto-merge or push to the default branch.
    Without a remote where a pull request can be opened, stop on the local branch and tell the user its name. The user decides whether to merge.
11. **Report.** Report what you changed, the checks you ran with their results compared with the baseline, and what you could not verify, as separate items.
12. **Turn a found type of problem into a check, not into prose.** When an inspection or a review finds a type of problem that could recur, propose a machine-checked rule in the repository's check command (a linter rule, an ast-grep or Semgrep rule, a type), instead of an item in `AGENTS.md` for the agent to remember. A rule that blocks commits or merges must have no false positives in this repository and should come with a mechanical fix; a rule that only reports may have a few. When more than one finding in ten is judged not useful, fix the rule or remove it. Prose in `AGENTS.md` is for what no tool can check.
