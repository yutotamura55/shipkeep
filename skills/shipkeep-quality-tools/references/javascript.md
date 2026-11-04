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
- A framework's ESLint configuration can lag behind ESLint. On 2026-11-04 `create-next-app` 16.4.5 installed ESLint 9, which npm marks as no longer supported, while the plugins that `eslint-config-next` uses (react, import, jsx-a11y) declared no support for ESLint 10 and `npm install eslint@10` printed ERESOLVE warnings. Before raising a major version, read the peer dependencies of the framework's configuration and its plugins; stay on the supported version when they conflict, and say so in the report.
- Stage `npm audit` when development tools carry advisories the project cannot fix: audit production dependencies with `npm audit --omit=dev` at the default level, and the full tree with `--audit-level=high` (or the level the user chooses), with the reason written in the check command. ESLint 9 and its plugins pulled an old minimatch with a moderate advisory on 2026-11-04.
- Next.js 16 generates route types that `tsc` needs (for example `LayoutProps`): run `next typegen && tsc --noEmit` in the check command, as the Next.js CLI documentation recommends.
