# Security policy

## Reporting a vulnerability

Please report a vulnerability in shipkeep privately, not in a public issue or pull request.

1. Open the repository's **Security** tab on GitHub and choose **Report a vulnerability** (GitHub private vulnerability reporting).
2. Describe what the problem is, how to reproduce it, and what an attacker could do with it.

The report is visible only to the maintainer and to you. You will get a reply in that private advisory. When the problem is confirmed, the fix is prepared privately and published together with a security advisory that credits you, unless you ask otherwise.

## Scope

shipkeep is a set of agent skills with small Python scripts that read Git repositories and GitHub settings. Reports that matter most:

- a script that writes to the repository it inspects, or sends data anywhere;
- a secret or personal data that a script prints or a skill tells the agent to paste;
- a check that reports "ready" for a repository that is not safe to publish.

## Supported versions

There are no releases yet. The `main` branch is the supported version.
