# Security policy

## Reporting a vulnerability

Please report a vulnerability in shipkeep privately, not in a public issue or pull request.

1. Open the repository's **Security and quality** tab on GitHub (called **Security** on older pages) and choose **Report a vulnerability** (GitHub private vulnerability reporting).
2. Describe what the problem is, how to reproduce it, and what an attacker could do with it.

GitHub keeps the report private: it is shared only with the maintainer, with you, and with anyone the maintainer adds to work on it. The maintainer replies in that private advisory. The aim is to confirm the problem, prepare a fix privately, and publish the fix together with a security advisory that credits you, unless you ask otherwise. When a problem cannot be fixed, the advisory says so.

## Scope

shipkeep is a set of agent skills with small Python scripts that read Git repositories and GitHub settings. Reports that matter most:

- a script that writes to the repository it inspects, or sends data anywhere;
- a secret or personal data that a script prints or a skill tells the agent to paste;
- a check that reports "ready" for a repository that is not safe to publish.

## Supported versions

There are no releases yet. The `main` branch is the supported version.
