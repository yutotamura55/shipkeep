"""公開する前に直すべき問題を、Git リポジトリから探す。

リポジトリは読むだけで変更しない。対象は push で公開されるもの、つまりローカルの
ブランチとタグに加え、origin のブランチである。origin のブランチは、GitHub の
リポジトリを公開すると一緒に公開される。結果は JSON を 1 つ標準出力に出し、
エージェントが項目ごとに報告の行へ変換できるようにする。

出力には秘密情報、個人情報、禁止語、ローカルのパスの値を含めない。指摘に含めるのは、
種類、場所、それらの値を伏せた行だけである。
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

SCHEMA_VERSION = 1

Check = dict[str, Any]
Finding = dict[str, Any]

# この script が解析する出力を変えてしまう設定。利用者の設定で指摘が隠れないよう、
# すべての Git コマンドで固定する。
GIT_SETTINGS = (
    "core.quotePath=false",
    "color.ui=never",
    "diff.noprefix=false",
    "diff.mnemonicPrefix=false",
    "diff.relative=false",
    "log.showRoot=true",
    "log.showSignature=false",
)
# push で公開されるものと、既存の GitHub リポジトリを公開したときに公開されるもの。
# stash と origin 以外のリモートは対象外。
PUBLISHED_REFS = ("--branches", "--tags", "--remotes=origin")
PUBLISHED_REF_PREFIXES = ("refs/heads", "refs/tags", "refs/remotes/origin")
# マージコミットはすべての親と比べ、マージ自体が持ち込んだ行 (コンフリクトの解消など)
# だけを探す。
MERGE_DIFFS = "--diff-merges=dense-combined"
ORIGIN_FETCH_ALL = "refs/heads/*:refs/remotes/origin/*"
COMMIT_HASH = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

LICENSE_NAMES = {"license", "license.md", "license.txt", "licence", "licence.md", "copying"}
README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}

SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "llm_api_key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "stripe_secret_key": re.compile(r"\b[rs]k_live_[0-9A-Za-z]{24,}"),
}
# 鍵のような文字の長い並びは、どのパターンにも当たらなくても該当行で伏せる。
# 未知の形式の秘密情報かもしれないため。
KEY_LIKE = re.compile(r"[A-Za-z0-9+/_-]{20,}")
# ローカルのパスの始まり。括弧や引用符の直後なら閉じる記号まで伏せ、空白を含むパスも
# 隠す。それ以外は次の空白まで伏せる。
LOCAL_PATH_START = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\+Users\\+)[A-Za-z0-9_.-]+")
CLOSERS = {"(": ")", "[": "]", "<": ">", "'": "'", '"': '"', "`": "`"}
# "=" や ":" の後の値は、長さを問わずパスワードの可能性がある。
ASSIGNED_VALUE = re.compile(r"[=:]\s*([^\s'\"]+)")
CONTEXT_LENGTH = 200

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# RFC 2606 で文書用に予約されたドメイン。公開してよいのは完全に一致するものだけで、
# mail.example.net のようなサブドメインは報告する。
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net"}
# logo@2x.png のような素材のファイル名は、メールアドレスに見える。
FILE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "svg",
    "webp",
    "avif",
    "ico",
    "bmp",
    "tif",
    "tiff",
    "pdf",
    "css",
    "js",
    "mp4",
    "webm",
    "woff",
    "woff2",
    "ttf",
    "otf",
}
USER_NAME = r"([A-Za-z0-9_.-]+)(?![A-Za-z0-9_.-])"
HOME_PATHS = (
    re.compile(r"(/home/)" + USER_NAME),
    re.compile(r"(/Users/)" + USER_NAME),
    re.compile(r"([A-Za-z]:\\+Users\\+)" + USER_NAME),
)
# 実際の端末ではなく、文書や CI に現れる利用者名。
GENERIC_USER_NAMES = {"user", "username", "runner", "ubuntu", "app", "node", "vscode", "shared"}

# ロックファイルには、第三者のパッケージ作者が公開した連絡先が写される。そのため
# アドレスは報告しない。ローカルのパスは報告する。
LOCK_FILE_NAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
}
# -diff 属性で文字が隠れないよう、バイナリファイルも文字列として読む。次の拡張子の
# ファイルは、バイナリのデータに誤って一致しないよう読み飛ばす。
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".7z",
    ".jar",
    ".war",
    ".class",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp3",
    ".mp4",
    ".mov",
    ".webm",
    ".wasm",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".pyc",
    ".sqlite",
    ".db",
}

ENV_EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")
PRIVATE_KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
# キーストアには必ず秘密鍵が入っている。PEM ファイルは公開の証明書であることが多く、
# その中の秘密鍵は秘密情報のパターンで見つける。
PRIVATE_KEY_FILE_SUFFIXES = (".p12", ".pfx", ".jks", ".keystore")
CREDENTIAL_FILE_NAMES = {"credentials.json", ".netrc", ".pypirc"}

GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

# .env ファイルを読むコードと設定。単語そのものではなく呼び出しやキーに一致させ、
# dotenv に触れているだけの文を数えない。os.environ と process.env は対象外にする。
# シェルや CI が設定した変数も読むので、.env ファイルがある根拠にならないため。
ENV_FILE_USAGE = (
    r"load_dotenv\(|dotenv_values\(|require\(['\"]dotenv['\"]\)|['\"]dotenv/config['\"]"
    r"|from ['\"]dotenv['\"]|^\s*env_file\s*[:=]"
)
DOTENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
# プロジェクトの最上位を示すファイル。.env ファイルはこの階層に置かれる。
PROJECT_MARKERS = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "Gemfile",
)
# Cloudflare Workers のプロジェクトは、ローカルの秘密情報を .dev.vars に置く。
WRANGLER_CONFIG_NAMES = {"wrangler.toml", "wrangler.json", "wrangler.jsonc"}
WRANGLER_EXAMPLE_NAMES = {".dev.vars.example"}
TEST_PATHSPECS = (
    ":(exclude,glob)**/tests/**",
    ":(exclude,glob)**/test/**",
    ":(exclude,glob)**/__tests__/**",
    ":(exclude,glob)**/test_*.py",
    ":(exclude,glob)**/*.test.*",
    ":(exclude,glob)**/*.spec.*",
)

HIDDEN_PATH = "(hidden: the path contains confidential text)"


def git_command(root: Path, *args: str) -> list[str]:
    command = ["git", "-C", str(root)]
    for setting in GIT_SETTINGS:
        command += ["-c", setting]
    return [*command, *args]


def git(root: Path, *args: str, check: bool = True) -> str:
    """読み取り専用の Git コマンドを ``root`` で実行し、標準出力を返す。"""
    completed = subprocess.run(git_command(root, *args), capture_output=True, check=check)
    # 手作業でデコードする。テキストモードではファイル内の単独の CR が LF に変わり、
    # patch の行を偽造できてしまう。
    return completed.stdout.decode("utf-8", errors="replace")


def git_succeeds(root: Path, *args: str) -> bool:
    completed = subprocess.run(git_command(root, *args), capture_output=True, check=False)
    return completed.returncode == 0


def repository_root(path: Path) -> Path:
    """``path`` を含む作業ツリーの最上位を返す。bare リポジトリなら ``path`` を返す。"""
    if git(path, "rev-parse", "--is-bare-repository").strip() == "true":
        return path
    return Path(git(path, "rev-parse", "--show-toplevel").strip())


def has_commits(root: Path) -> bool:
    return git_succeeds(root, "rev-parse", "--verify", "--quiet", "HEAD")


def result(
    check_id: str, status: str, summary: str, findings: list[Finding] | None = None
) -> Check:
    return {"id": check_id, "status": status, "summary": summary, "findings": findings or []}


def unquote_path(text: str) -> str:
    """Git が特殊なファイル名に付ける C 言語風の引用を元に戻す。"""
    if text.startswith('"') and text.endswith('"'):
        unquoted = ast.literal_eval(text)
        if isinstance(unquoted, str):
            return unquoted
    return text


@dataclass(frozen=True)
class AddedLine:
    commit: str
    path: str
    line: int
    text: str


def added_lines(root: Path) -> Iterator[AddedLine]:
    """公開されるコミットが追加した行を、場所とともに 1 行ずつ返す。

    マージコミットでは、すべての親と異なる行だけを数える。
    """
    log = git(
        root,
        "log",
        *PUBLISHED_REFS,
        MERGE_DIFFS,
        "--patch",
        "--text",
        "--no-textconv",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--format=%x00commit %H",
    )
    commit = ""
    path: str | None = None
    in_header = False
    parents = 1
    line = 0
    # LF だけで分割する。--text ではファイルの内容に別の改行が含まれることがある。
    for text in log.split("\n"):
        if text.startswith("\0commit ") and COMMIT_HASH.fullmatch(text[8:]):
            commit = text[8:20]
            path, in_header = None, False
        elif text.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            path, in_header = None, True
        elif in_header and text.startswith("+++ "):
            # ファイル名に空白があると、Git は名前の末尾にタブを付ける。
            name = unquote_path(text.removeprefix("+++ ").rstrip("\t"))
            path = name.removeprefix("b/") if name.startswith("b/") else None
            if path is not None and PurePosixPath(path).suffix.lower() in BINARY_EXTENSIONS:
                path = None
        elif text.startswith("@@"):
            in_header = False
            # hunk のヘッダーの "@" は、コミットの親の数より 1 つ多い。
            parents = len(text) - len(text.lstrip("@")) - 1
            match = re.search(r"\+(\d+)", text)
            line = int(match.group(1)) if match else 0
        elif text.startswith("\\"):
            continue  # "\ No newline at end of file" の行
        elif not in_header and path is not None and text[:parents] != " " * parents:
            markers = text[:parents]
            if "-" in markers:
                continue  # 削除された行は結果に含まれない
            if markers == "+" * parents:
                yield AddedLine(commit, path, line, text[parents:])
            line += 1


def added_files(root: Path) -> Iterator[tuple[str, str]]:
    """公開されるコミットが追加した各ファイルの、短いハッシュとパスを返す。"""
    log = git(
        root,
        "log",
        *PUBLISHED_REFS,
        MERGE_DIFFS,
        "--diff-filter=A",
        "--name-only",
        "--no-renames",
        "--format=%x00%H",
    )
    commit = ""
    for line in log.splitlines():
        if line.startswith("\0"):
            commit = line[1:13]
        elif line:
            yield commit, unquote_path(line)


def commit_messages(root: Path) -> Iterator[tuple[str, str]]:
    """公開される各コミットの、短いハッシュとメッセージ全文を返す。"""
    # コミットメッセージに NUL は入らないので、-z で安全にレコードを分けられる。
    log = git(root, "log", *PUBLISHED_REFS, "-z", "--format=%H%n%B")
    for record in log.split("\0"):
        commit, _, message = record.partition("\n")
        if COMMIT_HASH.fullmatch(commit):
            yield commit[:12], message


def published_ref_names(root: Path) -> list[str]:
    return git(root, "for-each-ref", "--format=%(refname)", *PUBLISHED_REF_PREFIXES).splitlines()


def is_noreply_address(address: str) -> bool:
    address = address.lower()
    return address.endswith("@users.noreply.github.com") or address == "noreply@github.com"


def is_public_address(address: str) -> bool:
    """本文にあるアドレス風の文字列を公開してよいかを返す。"""
    local, _, domain = address.lower().rpartition("@")
    return (
        is_noreply_address(address)
        or "noreply" in local
        or "no-reply" in local
        or local == "git"
        or domain in EXAMPLE_DOMAINS
        or domain.rsplit(".", 1)[-1] in FILE_EXTENSIONS
    )


def has_personal_address(text: str) -> bool:
    return any(not is_public_address(address) for address in EMAIL.findall(text))


def has_home_path(text: str) -> bool:
    return any(
        name.lower() not in GENERIC_USER_NAMES
        for pattern in HOME_PATHS
        for _, name in pattern.findall(text)
    )


def has_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS.values())


def matching_terms(text: str, denylist: list[str]) -> list[int]:
    """``text`` に現れる禁止語の番号 (1 始まり) を返す。"""
    lowered = text.lower()
    return [number for number, term in enumerate(denylist, 1) if term.lower() in lowered]


Span = tuple[int, int, str]


def local_path_spans(text: str) -> list[Span]:
    """各ローカルパスの範囲を返す。終わりが不明なときは行末まで伏せる。

    括弧や引用符の直後のパスは閉じる記号までとし、空白を含むパスも隠す。それ以外は
    次の空白までとする。閉じる記号がさらに続くときや、範囲に引用符が入るときは、
    終わりがあいまいである。入れ子の括弧やエスケープされた引用符で早く切れるおそれが
    あり、範囲に飲み込まれた引用符があると、その後の引用文が見えてしまう。そのときは
    行末まで伏せる。隠しすぎることはあっても、隠し足りないことはない。
    """
    spans = []
    for match in LOCAL_PATH_START.finditer(text):
        start = match.start()
        opener = text[start - 1] if start > 0 else ""
        if opener in CLOSERS:
            closer = CLOSERS[opener]
            end = text.find(closer, match.end())
            if text.count(closer, match.end()) > 1:
                end = -1
        else:
            gap = re.search(r"\s", text[match.end() :])
            end = match.end() + gap.start() if gap else -1
        end = len(text) if end == -1 else end
        if any(quote in text[start:end] for quote in "'\"`"):
            end = len(text)
        spans.append((start, end, "local_path"))
    return spans


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """各引用符を次の同じ種類の引用符と組にし、引用文の中身の範囲を返す。"""
    spans = []
    position = 0
    while position < len(text):
        quote = text[position]
        if quote in "'\"":
            end = text.find(quote, position + 1)
            end = len(text) if end == -1 else end
            spans.append((position + 1, end))
            position = end + 1
        else:
            position += 1
    return spans


def masked(text: str, denylist: list[str]) -> str:
    """``text`` の秘密の値をすべてプレースホルダーに置き換えて返す。

    どの規則も元の文字列の範囲に印を付け、プレースホルダーは最後に一度だけ書く。
    プレースホルダーに似た文字列で規則がだまされないようにするためである。
    """
    detected: list[Span] = []
    for kind, pattern in SECRET_PATTERNS.items():
        detected += [(m.start(), m.end(), kind) for m in pattern.finditer(text)]
    detected += [
        (m.start(), m.end(), "email_address")
        for m in EMAIL.finditer(text)
        if not is_public_address(m.group(0))
    ]
    detected += local_path_spans(text)
    for number, term in enumerate(denylist, 1):
        detected += [
            (m.start(), m.end(), f"term {number}")
            for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE)
        ]
    # 一般の規則は、検出した値を空白にした文字列で実行する。検出した値の中の引用符や
    # スラッシュが、新しい範囲を始めないようにするため。
    blanked = list(text)
    for start, end, _ in detected:
        blanked[start:end] = "\x01" * (end - start)
    rest = "".join(blanked)
    generic = [
        *quoted_spans(rest),
        *((m.start(1), m.end(1)) for m in ASSIGNED_VALUE.finditer(rest)),
        *((m.start(), m.end()) for m in KEY_LIKE.finditer(rest)),
    ]
    # 検出した値の中に収まる一般の範囲は、何も加えない。
    spans = detected + [
        (start, end, "redacted")
        for start, end in generic
        if end > start
        and not any(d_start <= start and end <= d_end for d_start, d_end, _ in detected)
    ]

    # 重なる範囲は 1 つのプレースホルダーにする。種類がすべて同じならその種類、
    # 異なれば "redacted" にする。
    groups: list[list[Span]] = []
    for span in sorted(spans):
        if groups and span[0] < max(end for _, end, _ in groups[-1]):
            groups[-1].append(span)
        else:
            groups.append([span])
    merged: list[Span] = []
    for group in groups:
        labels = {label for _, _, label in group}
        label = labels.pop() if len(labels) == 1 else "redacted"
        merged.append((group[0][0], max(end for _, end, _ in group), label))
    parts = []
    position = 0
    for start, end, label in merged:
        parts += [text[position:start], f"<{label}>"]
        position = end
    parts.append(text[position:])
    result_text = "".join(parts).strip()
    if len(result_text) > CONTEXT_LENGTH:
        return result_text[:CONTEXT_LENGTH] + "..."
    return result_text


def sensitive_file_kind(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name in {".env", ".dev.vars"} or (
        name.startswith(".env.") and not name.endswith(ENV_EXAMPLE_SUFFIXES)
    ):
        return "env_file"
    if name.endswith((".tfstate", ".tfstate.backup")):
        return "terraform_state"
    if name.endswith(".tfvars"):
        return "terraform_variables"
    if name in PRIVATE_KEY_FILE_NAMES or name.endswith(PRIVATE_KEY_FILE_SUFFIXES):
        return "private_key_file"
    if name in CREDENTIAL_FILE_NAMES:
        return "credentials_file"
    return None


def check_history_coverage(root: Path) -> Check:
    """公開される履歴のすべてが手元のリポジトリにあることを確かめる。"""
    findings: list[Finding] = []
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        findings.append({"kind": "shallow_clone"})
    fetch_specs = git(root, "config", "--get-all", "remote.origin.fetch", check=False).split()
    narrowed = any(spec.startswith("^") for spec in fetch_specs)
    if fetch_specs and (
        narrowed or not any(spec.lstrip("+") == ORIGIN_FETCH_ALL for spec in fetch_specs)
    ):
        findings.append({"kind": "partial_fetch"})
    if findings:
        return result(
            "history_coverage",
            "unknown",
            "the local copy lacks part of the history; other history checks may miss problems",
            findings,
        )
    return result("history_coverage", "pass", "the local copy holds the full history")


def check_commit_identity(root: Path) -> Check:
    """author または committer のアドレスが GitHub の noreply でないコミットを探す。"""
    findings = []
    log = git(root, "log", *PUBLISHED_REFS, "--format=%H%x00%ae%x00%ce")
    for line in log.splitlines():
        commit, author, committer = line.split("\0")
        for kind, address in (("author_email", author), ("committer_email", committer)):
            if not is_noreply_address(address):
                findings.append({"kind": kind, "commit": commit[:12]})
    if findings:
        commits = len({f["commit"] for f in findings})
        return result(
            "commit_identity",
            "fail",
            f"{commits} commit(s) record an address that is not a GitHub noreply address",
            findings,
        )
    return result("commit_identity", "pass", "every commit uses a GitHub noreply address")


@dataclass
class HistoryFindings:
    secrets: list[Finding]
    personal: list[Finding]
    terms: list[Finding]


def search_history(root: Path, denylist: list[str]) -> HistoryFindings:
    """追加行、ファイル名、メッセージ、ref から、秘密情報、個人情報、禁止語を探す。"""
    found = HistoryFindings([], [], [])
    for added in added_lines(root):
        location = {
            "commit": added.commit,
            "path": added.path,
            "line": added.line,
            "context": masked(added.text, denylist),
        }
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(added.text):
                found.secrets.append({"kind": kind, **location})
        is_lock_file = PurePosixPath(added.path).name in LOCK_FILE_NAMES
        if has_personal_address(added.text) and not is_lock_file:
            found.personal.append({"kind": "email_address", **location})
        if has_home_path(added.text):
            found.personal.append({"kind": "local_path", **location})
        for number in matching_terms(added.text, denylist):
            found.terms.append({"kind": "term_in_content", "term": number, **location})
    for commit, path in added_files(root):
        for number in matching_terms(path, denylist):
            found.terms.append({"kind": "term_in_path", "term": number, "commit": commit})
    for commit, message in commit_messages(root):
        if has_personal_address(message):
            found.personal.append({"kind": "email_in_commit_message", "commit": commit})
        if has_home_path(message):
            found.personal.append({"kind": "local_path_in_commit_message", "commit": commit})
        for number in matching_terms(message, denylist):
            found.terms.append({"kind": "term_in_commit_message", "term": number, "commit": commit})
    for ref in published_ref_names(root):
        for number in matching_terms(ref, denylist):
            found.terms.append({"kind": "term_in_ref", "term": number})
    return found


def check_history_content(root: Path, denylist: list[str] | None) -> list[Check]:
    found = search_history(root, denylist or [])
    if found.secrets:
        secrets_check = result(
            "history_secrets",
            "fail",
            f"{len(found.secrets)} added line(s) look like secrets",
            found.secrets,
        )
    else:
        secrets_check = result(
            "history_secrets", "pass", "no added line matches the built-in secret patterns"
        )
    if found.personal:
        personal_check = result(
            "history_personal_data",
            "fail",
            f"{len(found.personal)} place(s) in the history contain personal data",
            found.personal,
        )
    else:
        personal_check = result(
            "history_personal_data", "pass", "no personal address or local home path was found"
        )
    if denylist is None:
        terms_check = result("denylist", "not_applicable", "no denylist was given")
    elif found.terms:
        terms_check = result(
            "denylist",
            "fail",
            f"{len(found.terms)} place(s) in the history contain a denylist term",
            found.terms,
        )
    else:
        terms_check = result("denylist", "pass", "no denylist term appears in the history")
    return [secrets_check, personal_check, terms_check]


def check_sensitive_files(root: Path) -> Check:
    """コミットしてはいけないファイルを、それを追加したすべてのコミットから探す。"""
    findings = [
        {"kind": kind, "commit": commit, "path": path}
        for commit, path in added_files(root)
        if (kind := sensitive_file_kind(path))
    ]
    if findings:
        return result(
            "sensitive_files",
            "fail",
            f"{len(findings)} file(s) that should not be published were committed",
            findings,
        )
    return result("sensitive_files", "pass", "no file that should not be published was committed")


def tree_paths(root: Path, *options: str) -> list[str]:
    """HEAD のコミットにあるパスを、リポジトリの最上位から返す。"""
    listing = git(root, "ls-tree", *options, "--full-tree", "--name-only", "HEAD")
    return [unquote_path(path) for path in listing.splitlines()]


def project_directory(path: str, paths: set[str]) -> str:
    """``path`` より上で最も近い、プロジェクトのファイルか env の例を持つディレクトリを返す。

    そのようなディレクトリがなければ "" (リポジトリの最上位) を返す。
    """
    directory = PurePosixPath(path).parent
    markers = (*PROJECT_MARKERS, *sorted(DOTENV_EXAMPLE_NAMES))
    while str(directory) != ".":
        if any(f"{directory}/{marker}" in paths for marker in markers):
            return f"{directory}/"
        directory = directory.parent
    return ""


def env_file_users(root: Path, paths: set[str]) -> set[str]:
    """.env ファイルを読むコード (テストを除く) があるプロジェクトのディレクトリを返す。"""
    listing = git(
        root,
        "grep",
        "-I",
        "-l",
        "--no-textconv",
        "-E",
        ENV_FILE_USAGE,
        "HEAD",
        "--",
        ":(top)",
        *TEST_PATHSPECS,
        check=False,
    )
    files = [unquote_path(line.removeprefix("HEAD:")) for line in listing.split("\n") if line]
    return {project_directory(file, paths) for file in files}


def glob_match(pattern: list[str], path: list[str]) -> bool:
    """Git と同じ方法でパスを照合する。"*" は 1 階層内、"**" は複数の階層に一致する。"""
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(glob_match(pattern[1:], path[skip:]) for skip in range(len(path) + 1))
    return (
        bool(path)
        and fnmatch.fnmatchcase(path[0], pattern[0])
        and glob_match(pattern[1:], path[1:])
    )


def gitignore_matches(pattern: str, relative: str) -> bool | None:
    """.gitignore の 1 行が ``relative`` のファイルを無視するかを返す。

    ``relative`` は .gitignore のあるディレクトリからの相対パス。その行が当てはまらない
    ときは None、無視するときは True、無視の対象から戻すときは False を返す。
    """
    pattern = pattern.strip()
    if not pattern or pattern.startswith("#"):
        return None
    negated = pattern.startswith("!")
    pattern = pattern.removeprefix("!")
    if pattern.endswith("/"):
        return None  # ディレクトリだけに一致する
    # スラッシュを含むパターンは .gitignore からの相対。それ以外はどの階層にも一致する。
    parts = pattern.removeprefix("/").split("/") if "/" in pattern else ["**", pattern]
    if not glob_match(parts, relative.split("/")):
        return None
    return not negated


def is_ignored(root: Path, paths: set[str], target: str) -> bool:
    """HEAD の .gitignore ファイルが ``target`` のファイルを無視するかを返す。"""
    ignored = False
    parts = PurePosixPath(target).parts
    for depth in range(len(parts)):
        directory = "/".join(parts[:depth])
        gitignore = f"{directory}/.gitignore" if directory else ".gitignore"
        if gitignore not in paths:
            continue
        relative = "/".join(parts[depth:])
        for line in git(root, "show", f"HEAD:{gitignore}").splitlines():
            matched = gitignore_matches(line, relative)
            if matched is not None:
                ignored = matched
    return ignored


def env_targets(root: Path, paths: list[str]) -> dict[str, set[str]]:
    """プロジェクトが使うローカルの env ファイルごとに、対応する例のファイル名を返す。"""
    targets: dict[str, set[str]] = {}
    for path in paths:
        directory, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
        prefix = "" if directory == "." else f"{directory}/"
        if name in DOTENV_EXAMPLE_NAMES or name == ".env":
            targets[f"{prefix}.env"] = DOTENV_EXAMPLE_NAMES
        if name in WRANGLER_CONFIG_NAMES | WRANGLER_EXAMPLE_NAMES:
            targets[f"{prefix}.dev.vars"] = WRANGLER_EXAMPLE_NAMES
    for prefix in env_file_users(root, set(paths)):
        targets.setdefault(f"{prefix}.env", DOTENV_EXAMPLE_NAMES)
    return targets


def check_env_files(root: Path) -> Check:
    """プロジェクトが使うローカルの env ファイルに、ignore の規則と例があるかを確かめる。"""
    paths = tree_paths(root, "-r")
    path_set = set(paths)
    targets = env_targets(root, paths)
    if not targets:
        return result("env_files", "not_applicable", "the project does not read a local env file")
    findings: list[Finding] = []
    for target, examples in sorted(targets.items()):
        if not is_ignored(root, path_set, target):
            findings.append({"kind": "env_not_ignored", "path": ".gitignore", "file": target})
        directory = PurePosixPath(target).parent
        if not any(str(directory / example).removeprefix("./") in path_set for example in examples):
            findings.append({"kind": "env_example_missing", "file": target})
    if findings:
        return result(
            "env_files", "fail", "local env files are not set up for publishing", findings
        )
    return result("env_files", "pass", "local env files are ignored and examples are committed")


def check_license(root: Path) -> Check:
    names = [name for name in tree_paths(root) if name.lower() in LICENSE_NAMES]
    if names:
        return result("license", "pass", f"HEAD has a license file: {names[0]}")
    return result("license", "fail", "HEAD has no license file at the top level")


def check_readme(root: Path) -> Check:
    names = [name for name in tree_paths(root) if name.lower() in README_NAMES]
    for name in names:
        if git(root, "show", f"HEAD:{name}").strip():
            return result("readme", "pass", f"HEAD has a README: {name}")
    if names:
        return result("readme", "fail", f"the README at HEAD is empty: {names[0]}")
    return result("readme", "fail", "HEAD has no README at the top level")


def github_repository(root: Path) -> str | None:
    """origin のリモートが GitHub にあれば "owner/name" を返す。"""
    url = git(root, "config", "--get", "remote.origin.url", check=False).strip()
    match = GITHUB_REMOTE.match(url)
    return match.group(1) if match else None


def fetch_security_settings(repository: str) -> dict[str, Any] | None:
    """gh CLI で GitHub リポジトリのセキュリティ設定を読む。

    gh がない、サインインしていない、リポジトリを読めないときは None を返す。
    gh のエラー出力にはローカルの情報が含まれることがあるため、捨てる。
    """
    try:
        completed = subprocess.run(
            ["gh", "api", f"repos/{repository}"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    settings = data.get("security_and_analysis") if isinstance(data, dict) else None
    return settings if isinstance(settings, dict) else {}


def check_github_settings(
    root: Path,
    fetch: Callable[[str], dict[str, Any] | None] = fetch_security_settings,
) -> Check:
    """origin の GitHub で秘密情報のスキャンと push protection が有効かを確かめる。"""
    repository = github_repository(root)
    if repository is None:
        return result("github_settings", "not_applicable", "the origin remote is not on GitHub")
    settings = fetch(repository)
    if settings is None:
        return result("github_settings", "unknown", "the settings could not be read with gh")
    features = (
        ("secret_scanning", "secret_scanning_disabled"),
        ("secret_scanning_push_protection", "push_protection_disabled"),
    )
    if any(not isinstance(settings.get(name), dict) for name, _ in features):
        return result(
            "github_settings", "unknown", "GitHub does not show the secret scanning settings"
        )
    findings: list[Finding] = [
        {"kind": kind} for name, kind in features if settings[name].get("status") != "enabled"
    ]
    if findings:
        return result(
            "github_settings", "fail", "secret scanning or push protection is off", findings
        )
    return result("github_settings", "pass", "secret scanning and push protection are on")


def hide_confidential_paths(checks: list[Check], denylist: list[str]) -> None:
    """秘密情報、個人情報、禁止語を含む指摘のパスをすべて置き換える。"""
    for check in checks:
        for finding in check["findings"]:
            for field in ("path", "file"):
                value = finding.get(field)
                if isinstance(value, str) and (
                    has_secret(value)
                    or has_personal_address(value)
                    or has_home_path(value)
                    or matching_terms(value, denylist)
                ):
                    finding[field] = HIDDEN_PATH


CHECK_IDS = (
    "history_coverage",
    "commit_identity",
    "history_secrets",
    "history_personal_data",
    "denylist",
    "sensitive_files",
    "env_files",
    "license",
    "readme",
)


def scan(root: Path, denylist: list[str] | None = None) -> dict[str, Any]:
    """``root`` を含むリポジトリの検査結果を返す。

    ``denylist`` は履歴に現れてはいけない語の一覧。指摘では語そのものを示さず、
    一覧の中の番号 (1 始まり) で示す。
    """
    root = repository_root(root)
    if not has_commits(root):
        checks = [
            result(check_id, "unknown", "the repository has no commits") for check_id in CHECK_IDS
        ]
    else:
        checks = [
            check_history_coverage(root),
            check_commit_identity(root),
            *check_history_content(root, denylist),
            check_sensitive_files(root),
            check_env_files(root),
            check_license(root),
            check_readme(root),
        ]
    checks.append(check_github_settings(root))
    hide_confidential_paths(checks, denylist or [])
    counts = {
        status: sum(1 for check in checks if check["status"] == status)
        for status in ("pass", "fail", "unknown", "not_applicable")
    }
    return {"schema": SCHEMA_VERSION, "counts": counts, "checks": checks}


def is_git_repository(path: Path) -> bool:
    """``path`` が作業ツリーの中にあるか、bare リポジトリであるかを返す。

    Git を起動できないときは ``OSError`` を送出する。
    """
    return git_succeeds(path, "rev-parse", "--git-dir")


def read_denylist(path: Path) -> list[str]:
    """語を 1 行に 1 つずつ読む。空行と '#' で始まる行は飛ばす。

    ファイルを読めないときは ``OSError``、UTF-8 でないときは ``UnicodeDecodeError``
    を送出する。
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [term.strip() for term in lines if term.strip() and not term.strip().startswith("#")]


class QuietArgumentParser(argparse.ArgumentParser):
    """引数のエラーを、引数を繰り返さずに報告する。ローカルパスが含まれることがあるため。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, "error: invalid arguments; run with --help\n")


def main(argv: list[str] | None = None) -> int:
    parser = QuietArgumentParser(
        description="Scan a Git repository for problems to fix before making it public."
    )
    parser.add_argument("root", type=Path, help="path to the repository to scan")
    parser.add_argument(
        "--denylist",
        type=Path,
        help="file with one term per line that must not appear in the history",
    )
    args = parser.parse_args(argv)
    # エラーメッセージは固定文にする。例外のメッセージにはローカルパスが入ることがある。
    denylist = None
    if args.denylist is not None:
        try:
            denylist = read_denylist(args.denylist)
        except UnicodeDecodeError:
            print("the denylist file must be UTF-8 text", file=sys.stderr)
            return 2
        except OSError:
            print("cannot read the denylist file", file=sys.stderr)
            return 2
    root: Path = args.root
    try:
        is_repository = root.is_dir() and is_git_repository(root)
    except OSError:
        print("cannot run git: install Git and make sure it is on PATH", file=sys.stderr)
        return 2
    if not is_repository:
        print("not a Git repository: the given path", file=sys.stderr)
        return 2
    try:
        output = scan(root, denylist)
    except (subprocess.CalledProcessError, OSError, ValueError):
        print("git failed while reading the repository", file=sys.stderr)
        return 2
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
