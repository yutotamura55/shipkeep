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

- GitHub can also enforce SHA pinning: the allowed-actions policy (repository Settings → Actions → General) has an option that requires every action to be pinned to a full-length commit SHA, and it can block a specific action or version (added in August 2025; [changelog](https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/)). It rejects an unpinned action when the workflow runs, not when the pull request is opened. Check what it covers (for example reusable workflows) in the [settings documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository) before proposing it. Propose it after every workflow is already pinned, so that turning it on does not break CI.

Why this matters: in March 2026 attackers force-pushed malicious code to most version tags of `aquasecurity/trivy-action`, a security scanner's own action, and stole CI secrets from pipelines that used the tags (CVE-2026-33634; [SafeDep](https://safedep.io/trivy-teampcp-supply-chain-compromise/)). Quality and security tools are part of the supply chain too.

## Security static analysis across languages

- **Semgrep Community Edition** or its fork **Opengrep**. Both engines are LGPL 2.1.
- Semgrep-maintained rules are under the Semgrep Rules License v1.0, limited to internal, non-competing, non-SaaS use ([Semgrep](https://semgrep.dev/docs/faq/comparisons/opengrep)). Running them on the user's own repository fits that use, but never copy or bundle those rules into the project or into shipkeep; configure the tool to fetch them.
- Check the current activity and the rule source of both projects, and let the user choose when it is unclear.
- **CodeQL**: code scanning is available for public repositories on GitHub.com; private repositories must be owned by an organization on GitHub Team or Enterprise with GitHub Code Security enabled ([docs](https://docs.github.com/en/code-security/concepts/code-scanning/code-scanning)). GitHub announced it as free for public repositories in 2020 ([blog](https://github.blog/2020-09-30-code-scanning-is-now-available/)); the current documentation states only the conditions for private repositories. The default setup needs no workflow file and runs each analysis on GitHub Actions ([docs](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configure-code-scanning)). It is a repository setting, so the inventory reports it as unknown; ask the user to check Settings → Advanced Security (called Code security in older pages).
- When CodeQL runs, a branch ruleset can also block merging a pull request whose new alerts reach a chosen severity ("Require code scanning results"), on public repositories too ([how to set it](https://docs.github.com/en/enterprise-cloud@latest/code-security/how-tos/find-and-fix-code-vulnerabilities/manage-your-configuration/set-merge-protection)). It does not apply to Dependabot pull requests that the default setup analyzes ([concept](https://docs.github.com/en/code-security/concepts/code-scanning/merge-protection)).

## Dependencies

- **OSV-Scanner** reads the lockfiles of many ecosystems and reports known vulnerabilities, and can also report dependency licenses. Apache-2.0. Its README labels V2 as beta; check the current status.
- **Dependabot** (`.github/dependabot.yml`) or **Renovate** keeps dependencies updated. Dependabot security updates are a repository setting: tell the user how to turn them on.
  - Since July 2026, Dependabot version updates wait three days after a release by default before opening a pull request; security updates do not wait. Change it with `cooldown` in `dependabot.yml` ([changelog](https://github.blog/changelog/2026-07-14-dependabot-version-updates-introduce-default-package-cooldown/)). Group minor and patch updates (`groups`) so that the pull requests stay few.
  - **Malware alerts**: Dependabot can alert when a dependency is a known malicious package, from npm (March 2026) and more ecosystems through the OpenSSF malicious-packages data (July 2026). It is off until the user turns it on in the security settings, ([changelog](https://github.blog/changelog/2026-03-17-dependabot-now-detects-malware-in-npm-dependencies/), [changelog](https://github.blog/changelog/2026-07-28-dependabot-alerts-on-malicious-packages-across-more-ecosystems)). A malware alert offers no fixed version: the fix is to remove the package ([Advisory Database](https://docs.github.com/en/code-security/concepts/vulnerability-reporting-and-management/github-advisory-database)).
  - **Reachability analysis**: for JavaScript and TypeScript, Dependabot can mark each alert as reachable, potentially reachable, unreachable, or unavailable, from whether the project's code can call the vulnerable function. Public preview since October 2026, free during the preview, off by default; turn it on in Settings → Advanced Security. It never closes an alert ([changelog](https://github.blog/changelog/2026-10-28-prioritize-dependabot-alerts-with-reachability-analysis/), [docs](https://docs.github.com/en/code-security/concepts/supply-chain-security/about-reachability-analysis-for-dependabot-alerts)). Propose it only for JavaScript and TypeScript projects.
- **Dependency review** (`actions/dependency-review-action`) shows the vulnerabilities and licenses of the dependencies a pull request adds, and can fail the pull request when one is vulnerable. It is available for all public repositories; private repositories need GitHub Code Security or GitHub Advanced Security, and the dependency graph must be on ([docs](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-review)). Propose it when the project uses GitHub Actions; pin it by SHA like any action.
- Prefer the language's own audit (for example `pip-audit`, `npm audit`) when it exists, and OSV-Scanner for languages without one or for a single report across languages.

## Turning a found type of problem into a rule

When an inspection, a review, or an incident finds a type of problem that could recur ("a handler under `server/api/` that never calls `requireUser(`"), propose a rule that a tool checks on every run, in the repository's check command and CI, instead of an item for the agent to remember. Prose instructions are weak as enforcement: in a 2026 study of 1,650 Claude Code sessions, the odds that the agent followed an annotation instruction fell by about 5.6% with each function it generated, whatever the layout of the configuration file (arXiv 2605.10039, abstract read on 2026-11-03).

| Tool | Writes rules in | Can attach a fix | Limits |
| --- | --- | --- | --- |
| ast-grep | YAML on a tree-sitter syntax tree; many languages | Yes (`fix`) | No type information, control flow, or data flow ([comparison](https://ast-grep.github.io/advanced/tool-comparison.html)) |
| Semgrep CE / Opengrep, with the project's own rules | YAML; patterns look like code | Yes (`fix`, applied with `--autofix`, tried with `--dryrun`) | Only Semgrep-maintained rules have the limited license noted above; the project's own rules have none |
| ESLint custom rules, `no-restricted-syntax` | JavaScript | Depends on the rule | JS/TS only |

Keep the rule files in the repository (for example `sgconfig.yml` with a `rules/` directory, `.semgrep.yml`), run them from the check command, and apply Google's operating criteria for static analysis (Sadowski et al., CACM 2018): a check that blocks a build or merge needs an effective false-positive rate of zero and a mechanical fix; a check shown at review may have under 10%; when developers mark more than 10% of a rule's findings "not useful", fix or remove the rule. Record each rule's origin (the finding that led to it) and its false positives in the pull request that adds it.

## Deterministic fixes first

Before an agent changes code by hand, let tools make the changes that are the same every time, in this order, each in its own commit: the formatter; the linter's fixes marked safe (Ruff distinguishes safe and unsafe fixes); the fix attached to a rule (ast-grep, Semgrep); a language codemod. Google fixed every existing violation mechanically before promoting a check to an error, because warnings were ignored (CACM 2018). Codemod tools reported for each language (OpenRewrite for Java, jscodeshift and jssg for JS/TS, LibCST for Python, Comby across languages) were found through search summaries only on 2026-11-03; read their documentation before proposing one.

## Baselines for existing projects

A new rule on an existing project fails on old code. Instead of loosening the rule or adding suppression comments in bulk, record the existing findings once and fail only on new ones:

- **ESLint bulk suppressions** (ESLint 9.24.0 and later, April 2025): `eslint --suppress-all --fix` writes the existing violations to `eslint-suppressions.json`; the rule stays `error` for new code; `--prune-suppressions` removes entries that were fixed, and leftover entries make the exit code non-zero ([docs](https://eslint.org/docs/latest/use/suppressions)).
- **mypy-baseline**: `mypy | mypy-baseline sync` records existing errors in `mypy-baseline.txt`; `mypy | mypy-baseline filter` shows only new ones ([README](https://github.com/orsinium-labs/mypy-baseline)).
- **Ruff** has no baseline feature (checked 2026-11-03): use the staged rollout above, starting from a configuration that existing code passes. Do not use `ruff check --add-noqa` as a baseline; it writes suppression comments into the code.

Commit the baseline file, and make the check command fail when the file lists findings that no longer exist, so the baseline only shrinks (see `change-principles.md`, principle 6).

## Agent hooks are aids, not enforcement

Agent hooks (Claude Code's `PreToolUse` and `Stop` can block an action; `PostToolUse` cannot; checked in the official documentation on 2026-11-03) can run the check command before a commit and save a round trip. Their configuration sits where the agent can read and change it, so they never replace the required CI check and the branch ruleset, which the agent cannot bypass. Propose a hook only as a convenience, after the CI check exists.

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
| Repository security score | OpenSSF Scorecard (`ossf/scorecard-action`) | A public repository. It scores settings such as branch protection, pinned dependencies, and workflow permissions, and can send the results to code scanning ([scorecard-action](https://github.com/ossf/scorecard-action)) |
| Results in the Security tab | SARIF upload (`github/codeql-action/upload-sarif`) for zizmor, Semgrep/Opengrep, or OSV-Scanner | The user wants findings listed as code scanning alerts instead of only in CI logs, and the repository can use code scanning: public, or private with GitHub Code Security ([docs](https://docs.github.com/en/code-security/concepts/code-scanning/code-scanning)). The tools themselves still run without it |

## Not proposed by default

- **MegaLinter, Super-Linter**: they run many linters, but as a separate system from the project's own check command, and they are heavy. MegaLinter is AGPL-3.0. Their linter lists are useful when researching a language.
- **SonarQube Cloud, Qlty, Codacy, and similar services**: do not make the project depend on a service. Mention them only if the user asks.
- **Trivy**: OSV-Scanner covers dependency auditing. If the user wants Trivy, pin a known-safe version by SHA.
- **GitHub AI Scan**: AI-based scanning of pull requests (generally available since October 2026) and of whole repositories (preview). It also covers languages CodeQL does not, such as Shell, Terraform, and Dockerfiles, but each scan uses paid Code Security AI credits even on public repositories, and its findings cannot block a merge ([changelog](https://github.blog/changelog/2026-10-15-ai-scan-for-pull-requests-generally-available/), [repository scan](https://docs.github.com/en/code-security/concepts/code-scanning/ai-scan-for-repositories)). The pages checked on 2026-11-03 did not state how many credits one scan uses. Mention it only if the user asks, with the cost.
- **GitHub Code Quality**: available only for repositories owned by an organization on GitHub Team or Enterprise Cloud ([docs](https://docs.github.com/en/code-security/concepts/about-code-quality)).

## GitHub settings the user changes

These are repository settings, not files. Never change them yourself; list the ones that fit the project in the proposal, with where to find them:

- required status checks for the check command (branch ruleset);
- Dependabot security updates, and malware alerts (Settings → Advanced Security);
- reachability analysis, for JavaScript and TypeScript (Settings → Advanced Security);
- CodeQL default setup (Settings → Advanced Security), and "Require code scanning results" in the branch ruleset when CodeQL runs;
- requiring actions pinned to a full-length commit SHA (Settings → Actions → General), once every workflow is pinned.
