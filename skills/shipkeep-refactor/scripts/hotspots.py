"""よく変わり、かつ大きいソースのファイル (ホットスポット) を挙げる。

リポジトリは読むだけで変更しない。変更の回数は HEAD から辿れる直近のコミット
(マージを除く) で数え、大きさは HEAD のファイルの行数で測る。複雑度は言語ごとの
道具でエージェントが測るため、ここでは扱わない。結果は JSON を 1 つ標準出力に出す。
出力に含めるのは、リポジトリの中の相対パスと数値だけである。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
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
# 依存の置き場所と、ビルドの出力の置き場所。手で直すコードではない。
EXCLUDED_DIRS = (
    "node_modules/", "vendor/", ".venv/", "venv/", "site-packages/", "third_party/",
    "dist/", "build/", "out/", "target/", ".next/", ".nuxt/", ".output/", "coverage/",
    "generated/",
)  # fmt: skip
# git log の出力で、コミットの区切りを示す。パスに現れない制御文字を使う。
COMMIT_MARK = "\x01"
# 行数を数えるときに、1 回の git grep に渡すパスの数。引数の長さの上限を避ける。
PATHS_PER_GREP = 200
GIT_NOT_STARTED = "git を起動できません"


class HotspotError(Exception):
    """調べられない理由を、利用者に見せる英語の文で持つ。"""


def executable(name: str) -> str:
    """PATH にある実行ファイルの絶対パスを返す。見つからなければ ``OSError`` を送出する。

    起動の時点で探し直さず、ここで一度だけ絶対パスに決める。
    """
    path = shutil.which(name)
    if path is None:
        raise OSError(f"{name} が PATH にありません")
    # PATH に相対のディレクトリや空の項目があると、which は相対パスや名前だけを返す。
    return os.path.abspath(path)


def git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
    """読み取り専用の Git コマンドを実行し、標準出力を返す。"""
    # 部分的な clone で欠けたオブジェクトを取りに行かない (取得すると対象に書き込む)。
    # この環境変数は Git 2.44 以降で働く。古い Git には partial_clone() で備える。
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    # 引数はリストで、シェルを通さない。外から来る値は、-C のパス、-- の後の
    # :(literal) 付きのパス、整数として検査した --max-count の値だけである。
    try:
        result = subprocess.run(  # noqa: S603
            [executable("git"), "-C", str(root), "-c", "core.quotePath=false", *args],
            capture_output=True,
            check=False,
            env=env,
        )
    except OSError as error:
        raise HotspotError(GIT_NOT_STARTED) from error
    if result.returncode not in allowed:
        raise HotspotError(f"git {args[0]} が失敗しました")
    # UTF-8 でないパスも元のバイト列に戻せるよう surrogateescape で読む。
    # subprocess は引数を同じ規則でバイト列に戻すため、パスをそのまま Git に渡せる。
    return result.stdout.decode("utf-8", errors="surrogateescape")


def shown(path: str) -> str:
    """JSON に出せる形のパス。UTF-8 でないバイトは置換文字にする。"""
    return path.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def partial_clone(top: Path) -> bool:
    """部分的な clone (欠けたオブジェクトを後から取りに行く) なら真。"""
    output = git(
        top,
        "config",
        "--get-regexp",
        r"^(extensions\.partialclone|remote\..*\.promisor)$",
        allowed=(0, 1),
    )
    return bool(output.strip())


def is_source(path: str) -> bool:
    """依存やビルドの出力の外にある、生成物でないソースのファイルなら真。"""
    if any(path.startswith(d) or f"/{d}" in path for d in EXCLUDED_DIRS):
        return False
    if path.endswith(GENERATED_SUFFIXES):
        return False
    return PurePosixPath(path).suffix[1:].lower() in SOURCE_EXTENSIONS


def change_counts(top: Path, max_commits: int) -> tuple[Counter[str], int]:
    """直近のコミットでファイルごとの変更の回数を数え、数えたコミットの数も返す。"""
    # -z で、引用符や改行を含むパスもそのまま受け取る。出力は「区切り \0 \n パス \0
    # パス \0 ... 区切り \0 ...」の形で、各コミットの最初のパスの前に \n が付く。
    output = git(
        top,
        "log",
        "--no-merges",
        "--no-renames",
        "-z",
        "--format=%x01",  # COMMIT_MARK を出す
        "--name-only",
        f"--max-count={max_commits}",
        "HEAD",
    )
    counts: Counter[str] = Counter()
    commits = 0
    first_after_mark = False
    for token in output.split("\0"):
        if token == COMMIT_MARK:
            commits += 1
            first_after_mark = True
            continue
        if first_after_mark:
            token = token.removeprefix("\n")
            first_after_mark = False
        if token:
            counts[token] += 1
    return counts, commits


def line_counts(top: Path, paths: list[str]) -> dict[str, int]:
    """HEAD の ``paths`` のうち、テキストのファイルの行数を返す。"""
    lines: dict[str, int] = {}
    for start in range(0, len(paths), PATHS_PER_GREP):
        chunk = paths[start : start + PATHS_PER_GREP]
        # 一致する行がないと git grep は 1 で終わる (空のファイルだけの場合など)。
        # それ以外の失敗 (壊れたオブジェクト、部分的な clone で欠けた内容) は止める。
        output = git(
            top, "grep", "-I", "-z", "-c", "", "HEAD", "--", *[f":(literal){p}" for p in chunk],
            allowed=(0, 1),
        )  # fmt: skip
        # 出力は「HEAD:パス \0 行数 \n HEAD:パス \0 行数 \n」の形。パスに改行が
        # 含まれても崩れないよう、\0 で分けてから行数の後の \n で次のパスに移る。
        pieces = output.split("\0")
        name = pieces[0]
        for piece in pieces[1:]:
            count, _, rest = piece.partition("\n")
            lines[name.removeprefix("HEAD:")] = int(count)
            name = rest
    return lines


def hotspots(root: Path, max_commits: int = 500, limit: int = 20) -> dict[str, Any]:
    """変更の回数と行数の積が大きい順に、ソースのファイルを返す。"""
    try:
        top = Path(git(root, "rev-parse", "--show-toplevel").strip())
        git(top, "rev-parse", "--verify", "--quiet", "HEAD")
    except HotspotError as error:
        if str(error) == GIT_NOT_STARTED:
            raise
        raise HotspotError(
            "指定したパスは、コミットのある Git のリポジトリの中にありません"
        ) from error
    if partial_clone(top):
        # 欠けたオブジェクトを読むと Git が取りに行き、対象のリポジトリに書き込む。
        raise HotspotError(
            "部分的な clone（partial clone）です。履歴を読むと、欠けたオブジェクトを取りに行き、"
            "このリポジトリに書き込むおそれがあります。完全な clone に対して実行してください"
        )
    counts, commits = change_counts(top, max_commits)
    candidates = sorted(p for p, n in counts.items() if n >= MIN_CHANGES and is_source(p))
    lines = line_counts(top, candidates)
    files: list[dict[str, Any]] = [
        {"path": shown(path), "changes": counts[path], "lines": size, "score": counts[path] * size}
        for path, size in lines.items()
    ]
    files.sort(key=lambda item: (-item["score"], -item["changes"], item["path"]))
    return {"schema": SCHEMA_VERSION, "commits_counted": commits, "files": files[:limit]}


class JapaneseHelpFormatter(argparse.HelpFormatter):
    """使い方の見出しを日本語にする。argparse の既定の見出しは英語のため。"""

    def add_usage(
        self, usage: str | None, actions: Any, groups: Any, prefix: str | None = None
    ) -> None:
        super().add_usage(usage, actions, groups, "使い方: " if prefix is None else prefix)


class QuietArgumentParser(argparse.ArgumentParser):
    """引数の誤りを、使い方とともに終了コード 2 で知らせる。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # 引数の見出しと -h の説明を日本語にする。argparse の既定の文は英語のため。
        kwargs["add_help"] = False
        kwargs.setdefault("formatter_class", JapaneseHelpFormatter)
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置引数"
        self._optionals.title = "オプション"
        self.add_argument("-h", "--help", action="help", help="この説明を表示して終わる")

    def error(self, message: str) -> NoReturn:
        # argparse の文は、渡された値 (誤って渡したパスなど) をそのまま含むため出さない。
        self.print_usage(sys.stderr)
        print("エラー：引数が正しくありません。--help で使い方を確かめてください", file=sys.stderr)
        raise SystemExit(2)


RESULT_FORMAT = "shipkeep-result/1"
TOOL_NAME = "shipkeep-refactor"


def to_result(output: dict[str, Any], commit: str | None) -> dict[str, Any]:
    """hotspots() の出力を shared/result.schema.json の形式にする。候補なので status は info。"""
    items = [
        {
            "id": f"hotspot/{row['path']}",
            "check": "hotspot",
            "status": "info",
            "confidence": "confirmed",
            "summary": (
                f"{row['path']}：直近の {output['commits_counted']} コミットで "
                f"{row['changes']} 回変更、{row['lines']} 行、スコア {row['score']}"
            ),
            "evidence": [
                {
                    "kind": "file",
                    "path": row["path"],
                    "detail": (
                        f"changes={row['changes']}; lines={row['lines']}; score={row['score']}"
                    ),
                }
            ],
        }
        for row in output["files"]
    ]
    result: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "tool": {"name": TOOL_NAME},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {"info": len(items)},
        "items": items,
    }
    if commit:
        result["target"] = {"commit": commit}
    return result


def head_commit(root: Path) -> str | None:
    """HEAD のコミットの SHA。読めなければ None。"""
    try:
        return git(root, "rev-parse", "HEAD").strip() or None
    except (HotspotError, OSError):
        return None


def use_utf8_output() -> None:
    """標準出力と標準エラーを UTF-8 にする。

    表示は日本語なので、端末やリダイレクト先の文字コード (Windows の cp1252 など) で書けずに
    止まらないようにする。
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = QuietArgumentParser(
        description=(
            "よく変更され、大きいソースファイル（ホットスポット）を、リファクタリングの候補として"
            "一覧にします。Git の履歴だけを読みます。"
        )
    )
    parser.add_argument("repository", nargs="?", default=".", help="リポジトリの中のパス")
    parser.add_argument("--max-commits", type=int, default=500, help="数える直近のコミットの数")
    parser.add_argument("--limit", type=int, default=20, help="一覧にするファイルの数")
    parser.add_argument(
        "--format",
        choices=("files", "result"),
        default="files",
        help="出力の形。files（既定）か、shipkeep の共通の結果の形式の result",
    )
    args = parser.parse_args(argv)
    if args.max_commits < 1 or args.limit < 1:
        parser.error("--max-commits と --limit は 1 以上にしてください")
    try:
        result = hotspots(Path(args.repository), args.max_commits, args.limit)
    except HotspotError as error:
        print(f"エラー：{error}", file=sys.stderr)
        return 2
    if args.format == "result":
        result = to_result(result, head_commit(Path(args.repository)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # 取り込んだ側の出力の設定を変えないよう、スクリプトとして実行したときだけ切り替える。
    use_utf8_output()
    raise SystemExit(main())
