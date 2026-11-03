# Security policy

## Reporting a vulnerability

Please report a vulnerability in shipkeep privately, not in a public issue or pull request.

1. Open the repository's **Security and quality** tab on GitHub (called **Security** on older pages) and choose **Report a vulnerability** (GitHub private vulnerability reporting).
2. Describe what the problem is, how to reproduce it, and what an attacker could do with it.

The report becomes a private security advisory. People who can read it: you, everyone with admin access to this repository, anyone the maintainer adds to the advisory, and GitHub staff under GitHub's terms. Do not put secrets in the report that you would not share with them.

The maintainer intends to reply in that private advisory, to confirm the problem, to prepare a fix privately, and to publish the fix together with a security advisory that credits you, unless you ask otherwise. shipkeep is maintained by one person in their spare time, so there is no promised response time. When a problem will not be fixed, the maintainer intends to say so in the advisory.

## Scope

shipkeep is a set of agent skills with small Python scripts that read Git repositories and GitHub settings. Reports that matter most:

- a script that writes to the repository it inspects, or sends data anywhere;
- a secret or personal data that a script prints or a skill tells the agent to paste;
- a check that reports "ready" for a repository that is not safe to publish.

## Supported versions

There are no releases yet. The `main` branch is the supported version.
