# Measuring complexity and duplication

Checked on 2026-11-03. Before using a tool, check that it supports the target language and note its version.

## Tools

| Target | Complexity | Duplication |
| --- | --- | --- |
| Many languages | Lizard (cyclomatic complexity; `-Ecognitive` for cognitive complexity) | jscpd |
| Python | Ruff rule `C901`, radon | jscpd |
| TypeScript, Vue | ESLint `complexity` rule; `cognitive-complexity` from eslint-plugin-sonarjs (Vue files need vue-eslint-parser) | jscpd |
| Kotlin (Android) | detekt | jscpd |
| PHP | PHPMD | jscpd |
| C# | .NET code analysis rule `CA1502` | jscpd |

For a language not in the table, look for the tool its official documentation or main community recommends today, check that it is maintained, and cite the source and date. Lizard and jscpd support many languages and are a reasonable first try.

## How to read the numbers

- Look at functions and methods, not file averages or totals.
- Record the tool, version, settings, and target files with the result, and measure again after the change under the same conditions.
- A high number is a lead, not a verdict. Read the code: branches the specification needs (input validation, date boundaries) are not targets. Repeated state, mixed responsibilities, and needless exception handling are.
- In a TypeScript/Vue project, functions above a cognitive complexity of 10 were often high only because of many `?.` and `??` operators, with a short flow. Read before judging.
- jscpd finds only token-level duplicates. The same rule written two different ways (an `if`/`else if` chain and a `&&` expression) does not show up; trace such duplicates in the code and show the places.
- Thresholds here are for finding candidates. A project that wants a threshold as a quality bar decides it separately.

## Hotspots

`scripts/hotspots.py` ranks source files by the number of recent commits that changed them times their current line count. A file that changes often and is large is where restructuring pays off most. It skips files changed only once, generated files (such as `.d.ts`), documentation, and vendored directories.

Line count is only a proxy for size. For the top files, measure complexity with the tools above, then read the code.
