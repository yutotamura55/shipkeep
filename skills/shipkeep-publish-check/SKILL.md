---
name: shipkeep-publish-check
description: Use before making a Git repository public, or when reviewing a repository that is already public, to find secrets and personal data anywhere in its history and to check the files and settings that publishing needs (license, README, .env handling, GitHub secret scanning).
---

# Check a repository before publishing it

Making a repository public publishes every commit on every branch, not only the
current files. This skill scans the committed history and reports, check by
check, what is ready and what must be fixed first.

The skill only reads. Do not change the repository, rewrite history, push, or
change GitHub settings while running it. Fixing is a separate step that the user
decides on after reading the report.

## Never reveal what you find

The point of the check is to keep secrets and personal data private. Treat
every value you find as confidential, including in this conversation:

- Never print, quote, or paste a secret, an email address, a local user name, a
  local path, or a denylist term. Not even part of one.
- Refer to a finding by its kind and location: commit, file, and line.
- When you open a file to confirm a finding, describe what you saw ("an AWS
  access key ID assigned to `key`") instead of showing the line.

## Steps

1. **Pick the repository.** Use the path the user gave, or the current
   repository. Commit or set aside uncommitted work first if the user wants it
   checked: the scan reads commits only, because that is what a push publishes.
2. **Find the denylist, if any.** A denylist is a plain text file outside the
   repository with one private term per line, such as client or product names
   that must not be published. Lines starting with `#` are comments. Use the
   file the user names, or `~/.config/shipkeep/denylist.txt` when it exists.
   Never ask the user to type the terms into the conversation.
3. **Run the scan** with Python 3.11 or newer and Git. `gh` is optional: when it
   is installed and signed in, the scan also reads the GitHub settings.

   ```bash
   python3 <this skill's directory>/scripts/scan.py <repository> [--denylist <file>]
   ```

   The script prints one JSON document. It holds kinds and locations only, never
   a secret, an address, a path, or a denylist term, so the user can safely
   share it. Exit code 2 means the path is not a Git
   repository, Git is missing, or the denylist cannot be read; report the
   message and stop.
4. **Confirm the findings.** For each `fail` finding with a file and line, open
   the line at that commit (`git show <commit>:<path>`) and judge whether it is
   real. A placeholder, a test fixture, or a documentation example is a false
   positive; keep it in the report, marked as such, with the reason.
5. **Report** in the user's language, using the format below.

## Report format

Start with one line: how many checks need fixing, or that the repository is
ready to publish. Take the numbers from `counts` in the scan output instead of
counting rows yourself. Then a table with one row per check, in the order of the scan
output.

| Status in the JSON | Write it as |
| --- | --- |
| `pass` | Ready |
| `fail` | Needs fixing |
| `unknown` | Cannot verify, with what the user should check by hand |
| `not_applicable` | Not applicable, with the reason |

Under the table, list each finding of each failed check with its kind and
location, and whether you confirmed it. Then give the fixes, most urgent first.

## What each check means and how to fix it

| Check | What it looks for | How to fix |
| --- | --- | --- |
| `commit_identity` | Commits whose author or committer address is not a GitHub noreply address | Set the noreply address in Git, then rewrite the history with `git filter-repo --mailmap` |
| `history_secrets` | Added lines that look like private keys or API tokens, including lines a later commit deleted | **Revoke and replace the credential first**: removing it from history does not make a leaked key safe. Then remove it with `git filter-repo` |
| `history_personal_data` | Personal email addresses and local home paths in added lines and commit messages | Edit the files and rewrite the history, or accept it if the user decides the data may be public |
| `denylist` | Denylist terms in added lines and commit messages | Same as personal data |
| `sensitive_files` | Files that should never be committed (`.env`, private keys, Terraform state), even if deleted later | Remove them from the history, and replace any credential they held |
| `env_files` | In a project that reads a `.env` file: `.env` in `.gitignore`, and an example such as `.env.example` | Add the ignore rule and an example file with empty values |
| `license` | A license file at the top level of HEAD | Choose a license with the user; without one, others may not reuse the code |
| `readme` | A non-empty README at the top level of HEAD | Write what the project is and how to use it |
| `github_settings` | GitHub secret scanning and push protection on the `origin` repository | Turn them on in the repository's Settings → Code security |

Rewriting history changes commit hashes. Tell the user that it needs a force
push, that open pull requests and forks keep the old commits, and that GitHub
support must be asked to purge cached views of a leaked secret. Leave these
operations to the user.

## Limits

State these limits in the report when they matter:

- Secret detection uses built-in patterns for common key formats. It does not
  find every kind of secret, such as passwords in custom formats.
- Merge commits are not searched separately; their changes are searched in the
  commits they merge.
- The scan does not see uncommitted files, other remotes, or settings outside
  GitHub.
