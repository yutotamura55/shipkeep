# JavaScript and TypeScript

Checked on 2026-11-03. Check the current version and status of each tool before installing.

| Aspect | Recommended | Alternatives and notes |
| --- | --- | --- |
| Format | Prettier | Biome (format and lint in one tool) |
| Lint | ESLint | Biome, oxlint |
| Type check | `tsc --noEmit` with `strict` in `tsconfig.json` | Nuxt: `nuxt typecheck` (`nuxt build` does not type-check). Vue: `vue-tsc`. JavaScript only: `tsc --noEmit` with `allowJs` and `checkJs` and JSDoc types |
| Security lint | Semgrep/Opengrep from the common layer | Research ESLint security plugins at install time; their status was not verified |
| Tests | Vitest | Jest, `node --test`, Playwright for end-to-end tests |
| Coverage | Vitest coverage (`@vitest/coverage-v8`), with a Cobertura report for diff coverage | c8, nyc, Jest `--coverage` |
| Unused code | Knip (unused files and exports) | Knip often reports exported types that are kept on purpose: review its findings |
| Unused dependencies | Knip (the same tool covers both) | depcheck |
| Dependency audit | `npm audit` (or the package manager's equivalent) | OSV-Scanner |

Notes:

- Add the check command as a `check` script in `package.json`, for example `"check": "prettier --check . && eslint . && tsc --noEmit && vitest run --coverage"`. This is a format example; adapt it to the tools the project has.
- In a monorepo, inspect each package's `package.json`; the inventory looks at the repository as a whole.
