"""よく変わり、かつ大きいソースのファイル (ホットスポット) を挙げる。

リポジトリは読むだけで変更しない。変更の回数は HEAD から辿れる直近のコミット
(マージを除く) で数え、大きさは HEAD のファイルの行数で測る。複雑度は言語ごとの
道具でエージェントが測るため、ここでは扱わない。結果は JSON を 1 つ標準出力に出す。
出力に含めるのは、リポジトリの中の相対パスと数値だけである。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

SCHEMA_VERSION = 1

# ソースとして数える拡張子。文書、設定、ロックファイル、生成物は除く。
SOURCE_EXTENSIONS = frozenset(
    (
        "c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx", "cs", "go", "rs", "java", "kt",
        "kts", "scala", "swift", "rb", "php", "py", "js", "mjs", "cjs", "jsx", "ts", "mts",
        "cts", "tsx", "vue", "svelte", "dart", "ex", "exs", "erl", "hs", "ml", "fs", "clj",
        "lua", "pl", "r", "jl", "zig", "nim", "sh", "bash", "ps1", "tf", "sql",
    )
)  # fmt: skip
# 生成されることが多いファイル。大きいが、直す対象ではない。
GENERATED_SUFFIXES = (".d.ts", ".min.js", ".pb.go", "_pb2.py", ".g.dart", ".freezed.dart")
# 1 回しか変わっていないファイルは、よく変わるとは言えない。
MIN_CHANGES = 2
VENDORED = ("node_modules/", "vendor/", ".venv/", "venv/", "site-packages/", "third_party/")
# git log の出力で、コミットの区切りを示す行の先頭。パスに現れない制御文字を使う。
COMMIT_MARK = "\x01"


class HotspotError(Exception):
    """調べられない理由を、利用者に見せる英語の文で持つ。"""


def git(root: Path, *args: str) -> str:
    """読み取り専用の Git コマンドを実行し、標準出力を返す。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotePath=false", *args],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise HotspotError("git could not be started") from error
    if result.returncode != 0:
        raise HotspotError(f"git {args[0]} failed")
    return result.stdout.decode("utf-8", errors="replace")


def is_source(path: str) -> bool:
    """依存の置き場所の外にある、生成物でないソースのファイルなら真。"""
    if any(path.startswith(d) or f"/{d}" in path for d in VENDORED):
        return False
    if path.endswith(GENERATED_SUFFIXES):
        return False
    return PurePosixPath(path).suffix[1:].lower() in SOURCE_EXTENSIONS


def change_counts(root: Path, max_commits: int) -> tuple[Counter[str], int]:
    """直近のコミットでファイルごとの変更の回数を数え、数えたコミットの数も返す。"""
    output = git(
        root,
        "log",
        "--no-merges",
        "--no-renames",
        "--format=%x01",  # COMMIT_MARK を出す
        "--name-only",
        f"--max-count={max_commits}",
        "HEAD",
    )
    counts: Counter[str] = Counter()
    commits = 0
    for line in output.splitlines():
        if line.startswith(COMMIT_MARK):
            commits += 1
        elif line:
            counts[line] += 1
    return counts, commits


def line_counts(root: Path) -> dict[str, int]:
    """HEAD のテキストのファイルごとの行数を返す。"""
    try:
        output = git(root, "grep", "-I", "-z", "-c", "", "HEAD")
    except HotspotError:
        # 一致する行がないと git grep は 1 で終わる (空のファイルしかない場合など)。
        return {}
    lines: dict[str, int] = {}
    for record in output.splitlines():
        name, _, count = record.partition("\0")
        lines[name.removeprefix("HEAD:")] = int(count)
    return lines


def hotspots(root: Path, max_commits: int = 500, limit: int = 20) -> dict[str, Any]:
    """変更の回数と行数の積が大きい順に、ソースのファイルを返す。"""
    try:
        git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    except HotspotError as error:
        raise HotspotError("the path is not inside a Git repository with commits") from error
    counts, commits = change_counts(root, max_commits)
    lines = line_counts(root)
    files: list[dict[str, Any]] = [
        {"path": path, "changes": counts[path], "lines": size, "score": counts[path] * size}
        for path, size in lines.items()
        if is_source(path) and counts[path] >= MIN_CHANGES
    ]
    files.sort(key=lambda item: (-item["score"], -item["changes"], item["path"]))
    return {"schema": SCHEMA_VERSION, "commits_counted": commits, "files": files[:limit]}


class QuietArgumentParser(argparse.ArgumentParser):
    """引数の誤りを、使い方とともに終了コード 2 で知らせる。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = QuietArgumentParser(
        description=(
            "List source files that change often and are large (hotspots), "
            "as refactoring candidates. Reads Git history only."
        )
    )
    parser.add_argument("repository", nargs="?", default=".", help="path inside the repository")
    parser.add_argument("--max-commits", type=int, default=500, help="recent commits to count")
    parser.add_argument("--limit", type=int, default=20, help="number of files to list")
    args = parser.parse_args(argv)
    if args.max_commits < 1 or args.limit < 1:
        parser.error("--max-commits and --limit must be positive")
    try:
        result = hotspots(Path(args.repository), args.max_commits, args.limit)
    except HotspotError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
