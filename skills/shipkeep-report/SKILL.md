---
name: shipkeep-report
description: Use after an inspection (shipkeep-publish-check, shipkeep-quality-tools, shipkeep-refactor, or another tool's findings converted to the shipkeep result format) to turn the failed items into GitHub Issues the user approves, without duplicates, and to keep the inspection history and the items the user decided not to fix in the repository's .shipkeep/ directory. Also lists checks that keep failing, as candidates for a machine-checked rule.
---

# Turn inspection results into Issues and keep the record

This skill takes results in the common shipkeep result format
(`references/result.schema.json`) and:

- plans GitHub Issues for the failed items, at most a few per run, and skips
  items that are already filed, closed as not planned, or dismissed;
- creates exactly the Issues of a plan the user approved;
- keeps the history of results and the dismissed items in the target
  repository's `.shipkeep/` directory, reviewed like any other change;
- lists items that failed in several recorded runs, so that a recurring type of
  problem becomes a rule a tool checks.

All steps use `scripts/report.py` with Python 3.10 or newer. Reading and
creating Issues needs an authenticated `gh`. The script never prints a local
path, a secret, or personal data, and it refuses to file or record text that
looks like a personal email address or a home directory path.

Planning only reads GitHub. Creating Issues and writing `.shipkeep/` change
things: do them only after the user approves, and follow
`references/change-principles.md` for the changes to the repository.

## Steps

1. **Get results in the common format.** Run the inspection scripts with
   `--format result` and write each result to a file outside the target
   repository (inspection never changes it), for example:

   ```bash
   python3 <publish-check skill>/scripts/scan.py <repository> --format result > /tmp/scan.json
   ```

   For another tool's findings (for example vibe-code-launch-check or
   prod-readiness), write a result file by hand that follows
   `references/result.schema.json`: one item per finding, a stable `id` (the
   check, then `/` and what it is about), `status` `fail` for a problem,
   `severity` for a fail, `confidence` from how the tool found it, and evidence
   with locations only. Mask every secret and personal value. Then check it:

   ```bash
   python3 <this skill's directory>/scripts/report.py validate <result.json>...
   ```

   Exit code 1 means a file does not match; fix it from the messages.

2. **Plan the Issues.** This reads the repository's Issues and labels and
   writes the plan; it creates nothing:

   ```bash
   python3 <this skill's directory>/scripts/report.py plan <result.json>... \
     --repo OWNER/NAME --output /tmp/plan.json \
     [--dismissed <repository>/.shipkeep/dismissed.json] \
     [--limit 3] [--min-severity medium] [--include-unknown] [--label NAME ...]
   ```

   Defaults: at most 3 Issues per run, failed items of severity medium or
   higher (a fail without a severity counts as medium), labels `shipkeep` and
   `needs-triage`. `--include-unknown` also files checks that could not run.
   The plan lists, for each candidate, either the Issue to create or why it is
   skipped:

   | Reason | Meaning |
   | --- | --- |
   | `dismissed` | Listed in `.shipkeep/dismissed.json` |
   | `already_open` | An open Issue for the same item exists |
   | `closed_as_not_planned` | The user closed the Issue as not planned; suggest recording it with `dismiss` |
   | `blocked_personal_data` | The item's text looks like personal data; mask it in the result and plan again |
   | `over_limit` | More candidates than `--limit`; they are filed in a later run |

   An item whose earlier Issue was closed as completed but still fails is filed
   again, with a link to the earlier Issue. Duplicates are found by a marker in
   the Issue body, so removing the label does not cause a duplicate.

3. **Let the user approve.** Show the user the titles to create, the labels to
   create, and the skipped items with their reasons. Do not paste the bodies'
   values into the conversation; the bodies hold the masked evidence. The user
   may ask for a lower limit or other options: plan again. Create nothing until
   the user approves this plan.

4. **Create the Issues** of the approved plan:

   ```bash
   python3 <this skill's directory>/scripts/report.py create /tmp/plan.json --repo OWNER/NAME
   ```

   It creates the missing labels, then each Issue, after checking again that no
   Issue for the same item appeared since the plan. Exit code 1 means some
   creation failed; report which. Report the created Issue numbers.

5. **Keep the record**, when the user wants it, on a dedicated branch of the
   target repository as `references/change-principles.md` says:

   ```bash
   python3 <this skill's directory>/scripts/report.py record <result.json> --into <repository>/.shipkeep/history
   python3 <this skill's directory>/scripts/report.py dismiss --file <repository>/.shipkeep/dismissed.json \
     --tool TOOL --id ITEM_ID --reason "why it will not be fixed" [--issue N]
   ```

   Record the results of each inspection. Dismiss an item only when the user
   decided not to fix it, with their reason. Commit `.shipkeep/` and open a
   pull request: a new dismissed entry hides a problem from later runs, so it
   is reviewed like a suppression comment.

6. **Turn recurring problems into rules.** List the items that failed in
   several recorded runs:

   ```bash
   python3 <this skill's directory>/scripts/report.py recurring <repository>/.shipkeep/history [--min-count 2]
   ```

   For each, propose a check that a tool runs on every change (principle 12 of
   `references/change-principles.md`): a linter rule, an ast-grep or Semgrep
   rule, a CI check, or a repository setting. Let the user decide.

## The files in .shipkeep/

| File | Content |
| --- | --- |
| `.shipkeep/history/<date>-<tool>-<commit>.json` | A result in the common format, as recorded by `record` |
| `.shipkeep/dismissed.json` | `{"format": "shipkeep-dismissed/1", "items": [{"tool", "id", "reason", "dismissed_on", "issue"?}]}` |

## Limits

- Duplicates are found only through the marker that this script writes into
  Issue bodies; Issues written by hand about the same problem are not matched.
- The personal-data check looks for email addresses and home directory paths
  only. Masking secrets is the job of the tool that produced the result.
- `create` posts with the identity of the signed-in `gh` user. For scheduled
  runs, run `create` in a separate step that has write access, after a person
  or a rule has accepted the plan.
