# Languages without a reference

When the inventory marks a language with `"reference": false`, research its tools before proposing. Do not propose a tool from memory.

## How to research

1. For each aspect (format, lint, type check, security lint, tests, coverage, unused code, unused dependencies, dependency audit), look for the tool the language's official documentation or its main community recommends today. Prefer the language's official tooling (for example `go vet`, `cargo clippy`, `dotnet format`).
2. Check that the tool is maintained: a recent release, and no notice of deprecation.
3. When the language has no such thing (for example type checking in a statically typed, compiled language is the compiler), mark the aspect "not applicable" and say why.
4. Write the source and the date for each proposal. When sources disagree or you cannot decide, show the candidates with their differences and let the user choose.
5. The common layer (`common.md`) still applies: actionlint, zizmor, Semgrep/Opengrep, OSV-Scanner, diff coverage, Dependabot.

## Starting points

These are leads to verify, not decisions.

| Language | Format and lint | Type check | Mutation testing |
| --- | --- | --- | --- |
| Kotlin (Android) | ktlint, Android lint, detekt | Kotlin compiler; consider treating warnings as errors | PIT (JVM) |
| C# | `dotnet format`, .NET code analysis | C# compiler with `Nullable` enabled; consider `TreatWarningsAsErrors` | |
| PHP | PHP_CodeSniffer, PHP-CS-Fixer | PHPStan or Psalm | Infection |
| Terraform | `terraform fmt -check`, tflint, checkov | `terraform validate` | |
| Rust | `cargo fmt`, `cargo clippy` | Rust compiler | cargo-mutants |
| Go | `gofmt`, `go vet` | Go compiler | |

MegaLinter's documentation lists linters per language and is a useful index when researching.
