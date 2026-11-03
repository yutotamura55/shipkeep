# Tools for every repository, whatever the language

These tools do not depend on the project's language. Propose them to every project, then add the language tools from the language reference on top.
Facts below were checked on 2026-11-03. Before installing, check the current version and status of each tool in its official documentation.

## One check command

- Provide one command that runs every check (formatting, lint, type checking, security lint, tests with coverage), such as `scripts/check.sh`, `make check`, or `npm run check`, and write it in the README or `AGENTS.md`.
- If the project has a development container, make the same command work inside it.
- Run the same command in CI and make it a required check for pull requests. Making a check required is a repository setting: tell the user how to turn it on; do not change settings yourself.

## Rolling out to an existing project

Turning everything on at once floods an existing project with errors and stops normal work. Plan stages, each on its own branch and pull request:

1. Formatter and linter with a configuration that existing code already passes; wire them into CI. Keep the bulk reformatting commit separate.
2. Type checking with a lenient configuration; wire it into CI.
3. Tighten rules and type checking step by step. Write the remaining steps as Issues for the user.

A new project starts strict from the first commit (for example TypeScript `strict`, mypy `strict`).

## Coverage

- Do not pick a fixed percentage. Measure the current coverage when installing, use it as the floor, and fail when coverage drops below it.
- Also check the coverage of the lines a pull request changes (diff coverage). A repository-wide number hides untested new code: one write-up shows a change that left overall coverage at 84% while only 41% of the changed lines were tested ([agentpatterns.ai](https://agentpatterns.ai/verification/diff-coverage-gate-agent-prs/)).
- diff-cover reads Cobertura-style XML reports and compares against the base branch, for example `diff-cover coverage.xml --compare-branch origin/main --fail-under 80`. In GitHub Actions, check out with `fetch-depth: 0` so the base branch resolves.

## GitHub Actions workflows

When the project uses GitHub Actions:

- **actionlint** checks workflow correctness: syntax, expression types, action inputs, and the shell scripts in `run:`.
- **zizmor** checks workflow security: template injection, credentials left in the checkout, excessive `permissions`, and actions not pinned to a commit SHA. MIT license. Some audits need a GitHub token.
- Every workflow you add or change:
  - pins each action to a full commit SHA, with the tag in a comment (`uses: owner/action@<sha> # v1.2.3`);
  - sets `persist-credentials: false` on `actions/checkout` unless a later step must push;
  - declares the smallest `permissions` the job needs (usually `contents: read`).
- Look up the SHA of a tag; never write one from memory:

  ```bash
  gh api repos/OWNER/REPO/git/ref/tags/TAG --jq '.object.type + " " + .object.sha'
  # When the type is "tag" (an annotated tag), resolve it to the commit:
  gh api repos/OWNER/REPO/git/tags/SHA --jq '.object.sha'
  ```

  `OWNER`, `REPO`, `TAG`, and `SHA` are placeholders, not literal values.

Why this matters: in March 2026 attackers force-pushed malicious code to most version tags of `aquasecurity/trivy-action`, a security scanner's own action, and stole CI secrets from pipelines that used the tags (CVE-2026-33634; [SafeDep](https://safedep.io/trivy-teampcp-supply-chain-compromise/)). Quality and security tools are part of the supply chain too.

## Security static analysis across languages

- **Semgrep Community Edition** or its fork **Opengrep**. Both engines are LGPL 2.1.
- Semgrep-maintained rules are under the Semgrep Rules License v1.0, limited to internal, non-competing, non-SaaS use ([Semgrep](https://semgrep.dev/docs/faq/comparisons/opengrep)). Running them on the user's own repository fits that use, but never copy or bundle those rules into the project or into shipkeep; configure the tool to fetch them.
- Check the current activity and the rule source of both projects, and let the user choose when it is unclear.
- **CodeQL**: on a public GitHub repository, the default setup is free and needs no workflow file. It is a repository setting, so the inventory reports it as unknown; ask the user to check Settings → Code security.

## Dependencies

- **OSV-Scanner** reads the lockfiles of many ecosystems and reports known vulnerabilities, and can also report dependency licenses. Apache-2.0. Its README labels V2 as beta; check the current status.
- **Dependabot** (`.github/dependabot.yml`) or **Renovate** keeps dependencies updated. Dependabot security updates are a repository setting: tell the user how to turn them on.
- Prefer the language's own audit (for example `pip-audit`, `npm audit`) when it exists, and OSV-Scanner for languages without one or for a single report across languages.

## Propose only when the condition holds

Do not add these by default. Propose one, with its condition, when the project matches.

| Method | Example tools | When |
| --- | --- | --- |
| Mutation testing | mutmut, Cosmic Ray (Python); StrykerJS (JS/TS); PIT (JVM); cargo-mutants (Rust); Infection (PHP) | Tests exist, and coverage alone does not show whether they catch faults. It is slow: run it on changed files or on a schedule, not on every pull request |
| Property-based testing | Hypothesis (Python), fast-check (JS/TS) | Parsers, save-and-reload code, date arithmetic. The project writes the tests; propose the library and the places that suit it |
| Architecture rules | import-linter (Python), dependency-cruiser (JS/TS), ArchUnit (JVM) | A mid-sized or larger project that wants layers (UI, domain, storage). Decide the rules with the user |
| Fuzzing | ClusterFuzzLite | Code that parses external input. Supports C, C++, JVM, Go, Python, Rust, and Swift ([docs](https://google.github.io/clusterfuzzlite/)) |
| File-specific checks | hadolint (Dockerfile); tflint, checkov (Terraform) | The project has those files |
| Git hooks | pre-commit, lefthook | The user wants the check command to run before each local commit. Never a replacement for the required CI check |

## Not proposed by default

- **MegaLinter, Super-Linter**: they run many linters, but as a separate system from the project's own check command, and they are heavy. MegaLinter is AGPL-3.0. Their linter lists are useful when researching a language.
- **SonarQube Cloud, Qlty, Codacy, and similar services**: do not make the project depend on a service. Mention them only if the user asks.
- **Trivy**: OSV-Scanner covers dependency auditing. If the user wants Trivy, pin a known-safe version by SHA.
