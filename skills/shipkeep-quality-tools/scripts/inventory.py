"""プロジェクトにそろっている品質の道具と、足りない道具を調べる。

リポジトリは読むだけで変更しない。Git が追跡しているファイルの名前と、設定ファイル
(pyproject.toml、package.json、CI の workflow など) の本文から、観点ごとに道具の
手がかりを探す。結果は JSON を 1 つ標準出力に出し、エージェントが提案の表に変換する。

手がかりは名前の一致で判断する。設定の中身 (規則が有効か、CI で実際に動くか) までは
確かめないため、エージェントが根拠のファイルを読んで確かめる前提である。
出力に含めるのは、リポジトリの中の相対パスと道具の名前だけである。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

SCHEMA_VERSION = 1
MAX_EVIDENCE = 5
MAX_CONFIG_BYTES = 1_000_000

Row = dict[str, Any]


@dataclass(frozen=True)
class Tool:
    """1 つの道具と、それが入っていることを示す手がかり。"""

    name: str
    # 設定ファイルの本文に現れる文字列のパターン。
    pattern: str = ""
    # このパターンに当たるファイルがあれば、道具が入っているとみなす。
    files: tuple[str, ...] = ()
    # 空でなければ、本文を探すファイルをこのパターンに当たるものに限る。
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Language:
    """参照のある言語。見分ける手がかりと、観点ごとの道具を持つ。"""

    id: str
    name: str
    markers: tuple[str, ...]
    # ソースのファイルの拡張子。マニフェストがなくても、これがあれば言語として挙げる。
    extensions: tuple[str, ...]
    # 本文を探す設定ファイル。共通の設定ファイル (SHARED_CONFIGS) にも加えて探す。
    configs: tuple[str, ...]
    aspects: dict[str, tuple[Tool, ...]]


PYTHON_CONFIGS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "ruff.toml",
    ".ruff.toml",
    "tox.ini",
    "noxfile.py",
    "Pipfile",
    "*requirements*.txt",
    "*requirements*.in",
)
JAVASCRIPT_CONFIGS = ("package.json", "jest.config.*", "vitest.config.*", "vite.config.*")
# どの言語の検査も実行しうる場所。
SHARED_CONFIGS = (
    "Makefile",
    "justfile",
    "scripts/*.sh",
    ".pre-commit-config.yaml",
    "lefthook.yml",
    "lefthook.yaml",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".gitlab-ci.yml",
)
# 本文を読む設定ファイルのすべて。
CONFIG_FILES = PYTHON_CONFIGS + JAVASCRIPT_CONFIGS + SHARED_CONFIGS

# 言語の観点の並び。報告もこの順に並べる。
LANGUAGE_ASPECTS = (
    "format",
    "lint",
    "type_check",
    "security_lint",
    "tests",
    "coverage",
    "unused_code",
    "unused_dependencies",
    "dependency_audit",
)

JAVASCRIPT_TEST_FILES = tuple(
    f"*.{kind}.{ext}"
    for kind in ("test", "spec")
    for ext in ("js", "mjs", "cjs", "jsx", "ts", "mts", "cts", "tsx")
)

KNIP = Tool("knip", r"\bknip\b", ("knip.json", "knip.jsonc", "knip.config.*"))
SEMGREP = Tool("semgrep", r"\bsemgrep\b")
OPENGREP = Tool("opengrep", r"\bopengrep\b")
OSV_SCANNER = Tool("osv-scanner", r"\bosv-scanner\b")

PYTHON = Language(
    id="python",
    name="Python",
    markers=("pyproject.toml", "setup.py", "setup.cfg", "*requirements*.txt", "Pipfile"),
    extensions=("py",),
    configs=PYTHON_CONFIGS,
    aspects={
        "format": (
            Tool("ruff format", r"\bruff[ -]format\b|\[tool\.ruff\.format\]"),
            Tool("black", r"\bblack\b"),
        ),
        "lint": (
            Tool("ruff", r"\bruff\b", ("ruff.toml", ".ruff.toml")),
            Tool("flake8", r"\bflake8\b", (".flake8",)),
            Tool("pylint", r"\bpylint\b", (".pylintrc", "pylintrc")),
        ),
        "type_check": (
            Tool("mypy", r"\bmypy\b", ("mypy.ini", ".mypy.ini")),
            Tool("pyright", r"\b(?:based)?pyright\b", ("pyrightconfig.json",)),
        ),
        "security_lint": (
            # Ruff の S は flake8-bandit 相当の規則 (S101 のような個別の番号も含む)。
            # ALL は S を含む。extend-select も select として当たる。
            Tool("ruff S rules", r"""select\s*=\s*\[[^\]]*["'](?:S\d*|ALL)["']"""),
            Tool("bandit", r"\bbandit\b", (".bandit",)),
            SEMGREP,
            OPENGREP,
        ),
        "tests": (
            Tool("pytest", r"\bpytest\b", ("pytest.ini", "conftest.py", "*/conftest.py")),
            Tool("unittest", r"-m unittest\b"),
            Tool("test files", files=("test_*.py", "*_test.py")),
        ),
        "coverage": (
            Tool("pytest-cov", r"\bpytest-cov\b|--cov\b"),
            Tool(
                "coverage.py",
                # 設定の節、コマンド、依存の一覧の行頭 (coverage==7、"coverage[toml]>=7")。
                r"(?m)\[tool\.coverage|\bcoverage (?:run|report)\b"
                r"|^\s*\"?coverage(?:\[toml\])?\s*[=<>~!\"]",
                (".coveragerc",),
            ),
        ),
        "unused_code": (Tool("vulture", r"\bvulture\b"),),
        "unused_dependencies": (Tool("deptry", r"\bdeptry\b"),),
        "dependency_audit": (
            Tool("pip-audit", r"\bpip-audit\b"),
            OSV_SCANNER,
        ),
    },
)

JAVASCRIPT = Language(
    id="javascript",
    name="JavaScript / TypeScript",
    markers=("package.json",),
    extensions=("js", "mjs", "cjs", "jsx", "ts", "mts", "cts", "tsx", "vue", "svelte"),
    configs=JAVASCRIPT_CONFIGS,
    aspects={
        "format": (
            Tool("prettier", r"\bprettier\b", (".prettierrc*", "prettier.config.*")),
            Tool("biome", r"@biomejs/biome", ("biome.json", "biome.jsonc")),
            Tool("dprint", r"\bdprint\b", ("dprint.json",)),
        ),
        "lint": (
            Tool("eslint", r"\beslint\b", ("eslint.config.*", ".eslintrc*")),
            Tool("biome", r"@biomejs/biome", ("biome.json", "biome.jsonc")),
            Tool("oxlint", r"\boxlint\b"),
        ),
        "type_check": (
            Tool("typescript", r'"typescript"\s*:|\btsc\b|\bvue-tsc\b|\bnuxt typecheck\b'),
        ),
        "security_lint": (
            Tool("eslint-plugin-security", r"eslint-plugin-security\b"),
            SEMGREP,
            OPENGREP,
        ),
        "tests": (
            Tool("vitest", r"\bvitest\b", ("vitest.config.*",)),
            Tool("jest", r"\bjest\b", ("jest.config.*",)),
            Tool("mocha", r"\bmocha\b", (".mocharc*",)),
            Tool("playwright", r"\bplaywright\b", ("playwright.config.*",)),
            Tool("node:test", r"\bnode\b[^\"\n]*\s--test\b"),
            Tool("test files", files=JAVASCRIPT_TEST_FILES),
        ),
        "coverage": (
            Tool("vitest coverage", r"@vitest/coverage-"),
            Tool("c8", r"\bc8\b"),
            Tool("nyc", r"\bnyc\b"),
            Tool("--coverage", r"--coverage\b|collectCoverage"),
        ),
        "unused_code": (KNIP,),
        "unused_dependencies": (KNIP, Tool("depcheck", r"\bdepcheck\b")),
        "dependency_audit": (
            Tool("npm audit", r"\b(?:npm|pnpm|yarn|bun) audit\b"),
            OSV_SCANNER,
        ),
    },
)

REFERENCED_LANGUAGES = (PYTHON, JAVASCRIPT)

# 参照を持たない言語。見分けるだけで、道具はエージェントがその場で調べる。
OTHER_LANGUAGES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("go", "Go", ("go.mod",), ("go",)),
    ("rust", "Rust", ("Cargo.toml",), ("rs",)),
    (
        "jvm",
        "Java / Kotlin (JVM)",
        ("build.gradle", "build.gradle.kts", "pom.xml"),
        ("java", "kt", "kts"),
    ),
    ("php", "PHP", ("composer.json",), ("php",)),
    ("csharp", "C#", ("*.csproj", "*.sln"), ("cs",)),
    ("ruby", "Ruby", ("Gemfile",), ("rb",)),
    ("dart", "Dart / Flutter", ("pubspec.yaml",), ("dart",)),
    ("swift", "Swift", ("Package.swift", "*.xcodeproj/*"), ("swift",)),
    ("elixir", "Elixir", ("mix.exs",), ("ex", "exs")),
    ("terraform", "Terraform", (), ("tf",)),
    (
        "c_cpp",
        "C / C++",
        ("CMakeLists.txt", "meson.build", "*.vcxproj"),
        ("c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx"),
    ),
    ("scala", "Scala", ("build.sbt",), ("scala",)),
    ("haskell", "Haskell", ("*.cabal", "stack.yaml"), ("hs",)),
    ("deno", "Deno", ("deno.json", "deno.jsonc"), ()),
)

# 表にない言語のソースの拡張子。見つかれば、エージェントが言語を判断する。
# シェルや SQL は多くのリポジトリに補助としてあるため含めない。
UNLISTED_EXTENSIONS = frozenset(
    ("erl", "ml", "fs", "clj", "lua", "pl", "r", "jl", "zig", "nim", "cr", "v", "groovy", "m")
)
# 依存として取り込んだファイルの置き場所。手がかりから外す。
VENDORED = ("node_modules/", "vendor/", ".venv/", "venv/", "site-packages/", "third_party/")

WORKFLOW_FILES = (".github/workflows/*.yml", ".github/workflows/*.yaml")

# 言語を問わない観点。workflow の検査は GitHub Actions を使う場合だけ該当する。
REPOSITORY_ASPECTS: tuple[tuple[str, tuple[Tool, ...], bool], ...] = (
    (
        "check_command",
        (
            Tool("scripts/check.sh", files=("scripts/check.sh",)),
            Tool("make check", r"(?m)^check\s*:", sources=("Makefile", "justfile")),
            Tool("npm run check", r'"check"\s*:\s*"', sources=("package.json",)),
        ),
        False,
    ),
    (
        "ci",
        (
            Tool("GitHub Actions", files=WORKFLOW_FILES),
            Tool("GitLab CI", files=(".gitlab-ci.yml",)),
        ),
        False,
    ),
    ("workflow_lint", (Tool("actionlint", r"\bactionlint\b"),), True),
    ("workflow_security", (Tool("zizmor", r"\bzizmor\b"),), True),
    ("sast", (SEMGREP, OPENGREP), False),
    ("codeql", (Tool("codeql", r"github/codeql-action"),), False),
    ("dependency_audit", (OSV_SCANNER,), False),
    (
        "dependency_updates",
        (
            Tool("dependabot", files=(".github/dependabot.yml", ".github/dependabot.yaml")),
            Tool("renovate", files=("renovate.json", "renovate.json5", ".github/renovate.json")),
        ),
        False,
    ),
    ("diff_coverage", (Tool("diff-cover", r"\bdiff[-_]cover\b"),), False),
)


class InventoryError(Exception):
    """調べられない理由を、利用者に見せる英語の文で持つ。"""


def matches(path: str, pattern: str) -> bool:
    """パターンに `/` がなければファイル名で、あればリポジトリの中のパスで比べる。"""
    if "/" in pattern:
        return fnmatch.fnmatchcase(path, pattern)
    return fnmatch.fnmatchcase(PurePosixPath(path).name, pattern)


def tracked_files(root: Path) -> tuple[Path, list[str]]:
    """リポジトリの最上位と、Git が追跡しているファイルの相対パスを返す。"""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise InventoryError("git could not be started") from error
    if top.returncode != 0:
        raise InventoryError("the path is not inside a Git repository")
    top_path = Path(top.stdout.strip())
    listing = subprocess.run(
        ["git", "-C", str(top_path), "-c", "core.quotePath=false", "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0:
        raise InventoryError("git ls-files failed")
    names = listing.stdout.decode("utf-8", errors="replace").split("\0")
    return top_path, sorted(name for name in names if name and not is_vendored(name))


def is_vendored(name: str) -> bool:
    """依存として取り込まれた場所のファイルなら真。"""
    return any(name.startswith(d) or f"/{d}" in name for d in VENDORED)


def read_configs(top: Path, files: list[str]) -> dict[str, str]:
    """設定ファイルの本文を読む。大きすぎるもの、読めないものは飛ばす。"""
    texts: dict[str, str] = {}
    for name in files:
        if not any(matches(name, p) for p in CONFIG_FILES):
            continue
        path = top / name
        try:
            # パスのどの部分のシンボリックリンクもたどらない。リポジトリの外のファイルや、
            # 読み終わらないデバイスを読まないため。
            parts = [
                top / Path(*PurePosixPath(name).parts[: i + 1])
                for i in range(len(PurePosixPath(name).parts))
            ]
            if any(part.is_symlink() for part in parts):
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
                continue
            texts[name] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return texts


def find(tools: tuple[Tool, ...], files: list[str], texts: dict[str, str]) -> Row:
    """道具ごとに手がかりを探し、見つかった道具と根拠のファイルを返す。"""
    found: list[str] = []
    evidence: list[str] = []
    for tool in tools:
        hits = [f for f in files if any(matches(f, p) for p in tool.files)]
        if tool.pattern:
            regex = re.compile(tool.pattern)
            hits += [
                name
                for name, text in texts.items()
                if (not tool.sources or any(matches(name, p) for p in tool.sources))
                and regex.search(text)
            ]
        if hits:
            if tool.name not in found:
                found.append(tool.name)
            evidence += [h for h in sorted(set(hits)) if h not in evidence]
    return {
        "status": "pass" if found else "fail",
        "tools": found,
        "evidence": evidence[:MAX_EVIDENCE],
    }


def inventory(root: Path) -> dict[str, Any]:
    """リポジトリを調べ、言語ごとの観点と、言語を問わない観点の結果を返す。"""
    top, files = tracked_files(root)
    texts = read_configs(top, files)

    languages: list[Row] = []
    rows: list[Row] = []
    extensions = Counter(PurePosixPath(f).suffix[1:].lower() for f in files)

    def detect(markers: tuple[str, ...], exts: tuple[str, ...]) -> tuple[list[str], str]:
        """マニフェストか、ソースのファイルで言語を見分け、根拠と見分け方を返す。"""
        manifests = [f for f in files if any(matches(f, p) for p in markers)]
        if manifests:
            return manifests, "manifest"
        sources = [f for f in files if PurePosixPath(f).suffix[1:].lower() in exts]
        return sources, "sources"

    for language in REFERENCED_LANGUAGES:
        markers, detected_by = detect(language.markers, language.extensions)
        if not markers:
            continue
        languages.append(
            {
                "id": language.id,
                "name": language.name,
                "reference": True,
                "detected_by": detected_by,
                "evidence": markers[:MAX_EVIDENCE],
            }
        )
        own = language.configs + SHARED_CONFIGS
        own_texts = {n: t for n, t in texts.items() if any(matches(n, p) for p in own)}
        for aspect in LANGUAGE_ASPECTS:
            row: Row = {"scope": language.id, "aspect": aspect}
            row.update(find(language.aspects[aspect], files, own_texts))
            rows.append(row)
    for language_id, name, patterns, exts in OTHER_LANGUAGES:
        markers, detected_by = detect(patterns, exts)
        if not markers:
            continue
        languages.append(
            {
                "id": language_id,
                "name": name,
                "reference": False,
                "detected_by": detected_by,
                "evidence": markers[:MAX_EVIDENCE],
            }
        )
        for aspect in LANGUAGE_ASPECTS:
            rows.append(
                {
                    "scope": language_id,
                    "aspect": aspect,
                    "status": "unknown",
                    "tools": [],
                    "evidence": [],
                    "note": "no reference for this language; research the tools",
                }
            )

    unrecognized = [ext for ext, _ in extensions.most_common() if ext in UNLISTED_EXTENSIONS][
        :MAX_EVIDENCE
    ]

    uses_actions = any(matches(f, p) for f in files for p in WORKFLOW_FILES)
    for aspect, tools, needs_actions in REPOSITORY_ASPECTS:
        row = {"scope": "repository", "aspect": aspect}
        if needs_actions and not uses_actions:
            row.update(
                {
                    "status": "not_applicable",
                    "tools": [],
                    "evidence": [],
                    "note": "no GitHub Actions workflow",
                }
            )
        else:
            row.update(find(tools, files, texts))
            if aspect == "codeql" and row["status"] == "fail":
                # CodeQL の default setup はリポジトリの設定で、ファイルに現れない。
                row["status"] = "unknown"
                row["note"] = "no CodeQL workflow; check the default setup in GitHub settings"
        rows.append(row)

    counts = {s: 0 for s in ("pass", "fail", "unknown", "not_applicable")}
    for row in rows:
        counts[row["status"]] += 1
    return {
        "schema": SCHEMA_VERSION,
        "languages": languages,
        # 表にない言語のソースの拡張子 (多い順)。エージェントが言語を判断する。
        "unrecognized_source_extensions": unrecognized,
        "counts": counts,
        "aspects": rows,
    }


class QuietArgumentParser(argparse.ArgumentParser):
    """引数の誤りを、使い方とともに終了コード 2 で知らせる。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = QuietArgumentParser(
        description=(
            "List the quality tools a repository already has and the ones it lacks. "
            "Reads tracked files only and never changes the repository."
        )
    )
    parser.add_argument("repository", nargs="?", default=".", help="path inside the repository")
    args = parser.parse_args(argv)
    try:
        result = inventory(Path(args.repository))
    except InventoryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
