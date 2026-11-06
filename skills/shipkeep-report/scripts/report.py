"""shipkeep の結果を、GitHub の Issue にする案に変え、承認された案のとおりに作る。

サブコマンド:
  validate  結果のファイルが共通の形式 (references/result.schema.json) に合うかを確かめる。
  plan      結果から、作る Issue の案をファイルに書く。GitHub は読むだけで、何も作らない。
  create    承認された案のファイルどおりに、ラベルと Issue を作る。
  record    結果を、対象リポジトリの .shipkeep/history/ に写す。
  dismiss   対応しないと決めた項目を、.shipkeep/dismissed.json に書く。
  recurring .shipkeep/history/ の記録から、何度も失敗した項目を挙げる。

GitHub への書き込みは create だけが行い、案のファイルにない Issue は作らない。エージェントが
結果を読むときにだまされても、書き込みは承認した案の範囲に限られる。出力とエラーには、
ローカルのパス、秘密情報、個人情報の値を出さない。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote, unquote

import result_schema

PLAN_FORMAT = "shipkeep-issue-plan/1"
DISMISSED_FORMAT = "shipkeep-dismissed/1"
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
# 重大度のない fail (ほかの道具から変換した結果など) は medium として扱う。
DEFAULT_SEVERITY = "medium"
DEFAULT_LABELS = ("shipkeep", "needs-triage")
DEFAULT_LIMIT = 3
MAX_EVIDENCE_LINES = 20
MAX_TITLE_LENGTH = 120
FULLWIDTH_AT = "\uff20"
FULLWIDTH_NUMBER_SIGN = "\uff03"
ISSUE_REFERENCE = re.compile(r"#(?=\d)")
# 題名を縮めるときも、要約はこの文字数より短くしない。
MIN_TITLE_SUMMARY = 20
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LABEL = re.compile(r"^[^,\r\n]{1,50}$")
# Issue の本文に埋める目印。ID には空白や記号が入りうるので、パーセントエンコードする。
MARKER = re.compile(r"<!-- shipkeep-item tool=([A-Za-z0-9%._~-]+) id=([A-Za-z0-9%._~-]+) -->")

# 個人情報に見える値。結果を作る shipkeep のスクリプトは値を伏せるが、ほかの道具から手で
# 変換した結果には残りうる。Issue や記録に書く前に、ここでもう一度止める。
# 判定は shipkeep-publish-check の scan.py に合わせる (skill は別々に導入されるので写して持つ)。
# ただし、パスの途中に現れる /home/ (src/pages/home/ など) は数えず、git@ は clone の URL に
# 使うホストに限る。
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOME_PATH = re.compile(
    r"(?<![\w./\\-])(?:/home/|/Users/|[A-Za-z]:\\+Users\\+)([A-Za-z0-9._-]+|[^\x00-\x7f\s/\\]+)"
)
GENERIC_USER_NAMES = {"user", "username", "runner", "ubuntu", "app", "node", "vscode", "shared"}
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net"}
SERVICE_ADDRESSES = {"support@github.com", "noreply@github.com"}
GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
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
# Issue の目印を数えるのは、リポジトリの関係者が作った Issue だけ。誰でも Issue を作れる
# 公開リポジトリで、目印を書いた Issue によって項目を隠されないようにする。
TRUSTED_AUTHORS = {"OWNER", "MEMBER", "COLLABORATOR"}
SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

Key = tuple[str, str]
GhRunner = Callable[[list[str], str | None], tuple[int, str]]


class ReportError(Exception):
    """利用者に伝える、ローカルのパスや値を含まない理由を持つ。"""


def executable(name: str) -> str:
    """PATH にある実行ファイルの絶対パスを返す。見つからなければ ``OSError`` を送出する。"""
    path = shutil.which(name)
    if path is None:
        raise OSError(f"{name} が PATH にありません")
    # PATH に相対のディレクトリや空の項目があると、which は相対パスや名前だけを返す。
    return os.path.abspath(path)


def run_gh(args: list[str], stdin: str | None = None) -> tuple[int, str]:
    """gh を実行し、(終了コード, 標準出力) を返す。

    gh のエラー出力にはローカルの情報が含まれることがあるため、捨てる。gh を起動できなければ
    終了コード 127 を返す。
    """
    try:
        # 引数はリストで、シェルを通さない。
        completed = subprocess.run(  # noqa: S603
            [executable("gh"), *args],
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return 127, ""
    return completed.returncode, completed.stdout


# --- 読み込み -------------------------------------------------------------------------------


def strings(value: Any) -> list[str]:
    """JSON の値に含まれる文字列 (オブジェクトのキーを含む) をすべて返す。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [str(key), *strings(item)]]
    return []


def read_json(path: Path, what: str) -> Any:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ReportError(f"{what}を読めません") from error
    except json.JSONDecodeError as error:
        raise ReportError(f"{what}が JSON ではありません") from error
    # JSON の \ud800 のような対になっていないサロゲートは読めるが、UTF-8 で書けない。
    try:
        for text in strings(document):
            text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ReportError(f"{what}に、Unicode として正しくない文字列があります") from error
    return document


def write_json(path: Path, document: Any, what: str) -> str:
    """JSON を UTF-8 で書き、書いたバイト列の SHA-256 を返す。"""
    try:
        data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path.write_bytes(data)
    except (OSError, UnicodeError) as error:
        raise ReportError(f"{what}に書き込めません") from error
    return hashlib.sha256(data).hexdigest()


def load_result(path: Path, position: int) -> dict[str, Any]:
    document = read_json(path, f"結果のファイル {position} ")
    # スキーマは最上位を object とするので、validate() が通れば dict になる。型を絞るために並べる。
    if result_schema.validate(document) or not isinstance(document, dict):
        raise ReportError(
            f"結果のファイル {position} が、shipkeep の結果の形式に合いません。"
            "詳しくは validate コマンドで確かめてください"
        )
    metadata = {key: value for key, value in document.items() if key != "items"}
    if any(looks_personal(text) for text in strings(metadata)):
        raise ReportError(
            f"結果のファイル {position} の項目の外に、個人情報に見える文字列があります。"
            "先に伏せてください"
        )
    return document


def load_dismissed(path: Path | None) -> dict[Key, dict[str, Any]]:
    """dismissed.json を読み、(tool, id) から項目への辞書を返す。ファイルがなければ空。"""
    if path is None or not path.exists():
        return {}
    document = read_json(path, "対応しない項目のファイル")
    if not (
        isinstance(document, dict)
        and document.get("format") == DISMISSED_FORMAT
        and isinstance(document.get("items"), list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("tool"), str)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("reason"), str)
            for item in document["items"]
        )
    ):
        raise ReportError(f"対応しない項目のファイルが {DISMISSED_FORMAT} の形式ではありません")
    return {(item["tool"], item["id"]): item for item in document["items"]}


def parse_pages(text: str) -> list[Any]:
    """gh api --paginate の出力を読む。1 つの配列にも、配列を続けて並べた形にも対応する。"""
    decoder = json.JSONDecoder()
    items: list[Any] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        page, index = decoder.raw_decode(text, index)
        if not isinstance(page, list):
            raise ReportError("GitHub から想定と違う応答が返りました")
        items.extend(page)
    return items


def gh_list(gh: GhRunner, path: str, what: str) -> list[Any]:
    code, out = gh(["api", "--paginate", path], None)
    if code != 0:
        raise ReportError(
            f"gh で{what}を読めませんでした。gh にサインインしているかを確かめてください"
        )
    try:
        return parse_pages(out)
    except json.JSONDecodeError as error:
        raise ReportError("GitHub から想定と違う応答が返りました") from error


def existing_issues(gh: GhRunner, repository: str) -> dict[Key, list[dict[str, Any]]]:
    """リポジトリの Issue (PR を除く、閉じたものを含む) から、目印のあるものを集める。

    ラベルで絞らない。利用者がラベルを外しても、同じ項目の Issue を作り直さないため。
    """
    issues = gh_list(gh, f"repos/{repository}/issues?state=all&per_page=100", " Issue ")
    found: dict[Key, list[dict[str, Any]]] = {}
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        if issue.get("author_association") not in TRUSTED_AUTHORS:
            continue
        key = marker_key(issue.get("body") or "")
        if key is None:
            continue
        found.setdefault(key, []).append(
            {
                "number": issue.get("number"),
                "state": issue.get("state"),
                "state_reason": issue.get("state_reason"),
            }
        )
    return found


def marker_key(body: str) -> Key | None:
    """本文の 1 行目の目印から (tool, id) を読む。

    1 行目だけを見る。結果から写した文 (コードスパンの中) に目印を書かれても、別の項目の
    Issue として数えないため。
    """
    first_line = body.split("\n", 1)[0].strip()
    match = MARKER.fullmatch(first_line)
    if match is None:
        return None
    return unquote(match.group(1)), unquote(match.group(2))


def existing_labels(gh: GhRunner, repository: str) -> set[str]:
    labels = gh_list(gh, f"repos/{repository}/labels?per_page=100", "ラベル")
    # GitHub のラベルの名前は大文字小文字を区別しない。
    return {
        str(label["name"]).lower()
        for label in labels
        if isinstance(label, dict) and "name" in label
    }


# --- 個人情報の確認 ------------------------------------------------------------------------


def is_public_address(address: str) -> bool:
    lowered = address.lower()
    local, _, domain = lowered.rpartition("@")
    return (
        lowered.endswith("@users.noreply.github.com")
        or lowered in SERVICE_ADDRESSES
        or "noreply" in local
        or "no-reply" in local
        or (local == "git" and domain in GIT_HOSTS)
        or domain in EXAMPLE_DOMAINS
        or domain.rsplit(".", 1)[-1] in FILE_EXTENSIONS
    )


def looks_personal(text: str) -> bool:
    """個人のメールアドレスやホームのパスに見える値を含むかを返す。値そのものは返さない。"""
    if any(name.lower() not in GENERIC_USER_NAMES for name in HOME_PATH.findall(text)):
        return True
    return any(not is_public_address(address) for address in EMAIL.findall(text))


def item_texts(item: dict[str, Any]) -> list[str]:
    return strings(item)


# --- 本文の組み立て -------------------------------------------------------------------------


# コードスパンの区切りは中のバッククォートの並びより長くする。GitHub の Markdown (cmark-gfm) は
# 80 本を超える区切りをコードスパンとして扱わないため、中の並びを先に 10 本までに縮める。
MAX_BACKTICK_RUN = 10
LONG_BACKTICK_RUN = re.compile("`{" + str(MAX_BACKTICK_RUN + 1) + ",}")


def code_span(text: str) -> str:
    """Markdown のコードスパンにする。メンションやリンクを働かせず、値を書いたまま見せる。"""
    text = " ".join(str(text).split())
    text = LONG_BACKTICK_RUN.sub("`" * MAX_BACKTICK_RUN, text)
    if not text:
        return "（空）"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def marker(key: Key) -> str:
    tool, item_id = key
    return f"<!-- shipkeep-item tool={quote(tool, safe='')} id={quote(item_id, safe='')} -->"


def title_for(item: dict[str, Any]) -> str:
    # 要約を先に置き、何の Issue かを一覧で読めるようにする。道具の名前は本文に書く。
    # タイトルではコードスパンが使えない。@ と # を全角 (U+FF20、U+FF03) にし、メンションや
    # Issue への参照として読まれないようにする。
    # 長いときは要約を縮め、項目を見分ける ID を残す。ID だけで長すぎるときは全体を縮める。
    prefix = "[shipkeep] "
    suffix = neutralize_title(f"（{' '.join(str(item['id']).split())}）")
    summary = neutralize_title(" ".join(str(item["summary"]).split()))
    room = MAX_TITLE_LENGTH - len(prefix) - len(suffix)
    if len(summary) > room >= MIN_TITLE_SUMMARY:
        summary = summary[: room - 1] + "…"
    text = prefix + summary + suffix
    if len(text) > MAX_TITLE_LENGTH:
        text = text[: MAX_TITLE_LENGTH - 1] + "…"
    return text


def neutralize_title(text: str) -> str:
    """@ と、数字の前の # を全角にする。GitHub が # を Issue への参照と読むのは数字が続くとき。"""
    return ISSUE_REFERENCE.sub(FULLWIDTH_NUMBER_SIGN, text.replace("@", FULLWIDTH_AT))


def evidence_line(evidence: dict[str, Any]) -> str:
    parts = [code_span(evidence["kind"])]
    if "commit" in evidence:
        parts.append(f"コミット {code_span(evidence['commit'][:12])}")
    if "path" in evidence:
        place = code_span(evidence["path"])
        if "line" in evidence:
            place += f" の {evidence['line']} 行目"
        parts.append(place)
    if "context" in evidence:
        parts.append(f"行の内容 {code_span(evidence['context'])}")
    if "detail" in evidence:
        parts.append(code_span(evidence["detail"]))
    return "- " + " · ".join(parts)


def body_for(key: Key, item: dict[str, Any], commit: str | None, previous: int | None) -> str:
    tool, _ = key
    lines = [
        marker(key),
        "shipkeep の点検で、対応を決める必要のある項目が見つかりました。",
        "",
        f"- 道具：{code_span(tool)}",
        f"- 検査：{code_span(item['check'])}",
    ]
    if "scope" in item:
        lines.append(f"- 範囲：{code_span(item['scope'])}")
    status = f"- 状態：{item['status']}"
    if "severity" in item:
        status += f" · 重大度：{item['severity']}"
    if "confidence" in item:
        status += f" · 確度：{item['confidence']}"
    lines.append(status)
    if commit:
        lines.append(f"- 点検したコミット：{code_span(commit[:12])}")
    if previous is not None:
        lines.append(
            f"- 以前に #{previous} として作り、完了として閉じました。"
            "最新の点検でも報告されています。"
        )
    lines += ["", "### 要約", "", code_span(item["summary"])]
    evidence = item.get("evidence", [])
    if evidence:
        lines += ["", "### 根拠", ""]
        lines += [evidence_line(e) for e in evidence[:MAX_EVIDENCE_LINES]]
        if len(evidence) > MAX_EVIDENCE_LINES:
            lines.append(
                f"- ほかに {len(evidence) - MAX_EVIDENCE_LINES} 件が、結果のファイルにあります"
            )
    if "note" in item:
        lines += ["", "### 注記", "", code_span(item["note"])]
    if "remediation" in item:
        lines += ["", "### 直し方", "", code_span(item["remediation"])]
    lines += [
        "",
        "---",
        "秘密情報や個人情報にあたりうる値は伏せてあります。"
        "この項目に対応しない場合は、Issue を not planned として閉じ、"
        "`.shipkeep/dismissed.json` に記録してください。記録すると、後の点検でこの項目の Issue は"
        "作られません。",
    ]
    return "\n".join(lines) + "\n"


# --- plan -----------------------------------------------------------------------------------


Candidate = tuple[Key, dict[str, Any], str | None, dict[str, int]]


def candidates(
    results: list[dict[str, Any]], min_severity: str, include_unknown: bool
) -> list[Candidate]:
    """Issue にする候補を、重大度の高い順に返す。位置は (結果の番号, 項目の番号) で 1 始まり。"""
    threshold = SEVERITY_ORDER[min_severity]
    found: list[Candidate] = []
    for result_number, result in enumerate(results, 1):
        tool = result["tool"]["name"]
        commit = result.get("target", {}).get("commit")
        for item_number, item in enumerate(result["items"], 1):
            if item["status"] == "fail":
                severity = item.get("severity", DEFAULT_SEVERITY)
                if SEVERITY_ORDER[severity] < threshold:
                    continue
            elif not (item["status"] == "unknown" and include_unknown):
                continue
            where = {"result": result_number, "item": item_number}
            found.append(((tool, item["id"]), item, commit, where))

    def order(entry: Candidate) -> tuple[int, int, str, str]:
        (tool, item_id), item, _, _ = entry
        # fail を重大度の高い順に並べ、確認できなかった項目 (unknown) はその後に置く。
        if item["status"] != "fail":
            return (1, 0, tool, item_id)
        return (0, -SEVERITY_ORDER[item.get("severity", DEFAULT_SEVERITY)], tool, item_id)

    return sorted(found, key=order)


def make_plan(
    results: list[dict[str, Any]],
    repository: str,
    dismissed: dict[Key, dict[str, Any]],
    issues: dict[Key, list[dict[str, Any]]],
    labels_present: set[str],
    labels: tuple[str, ...],
    limit: int,
    min_severity: str,
    include_unknown: bool,
) -> dict[str, Any]:
    create: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[Key] = set()
    for key, item, commit, where in candidates(results, min_severity, include_unknown):
        if key in seen:
            continue
        seen.add(key)
        if any(looks_personal(text) for text in item_texts(item)):
            # 個人情報に見える値は ID に入っていることもあるので、ID を出さず位置で示す。
            skipped.append({**where, "reason": "blocked_personal_data"})
            continue
        entry: dict[str, Any] = {"tool": key[0], "id": key[1]}
        filed = issues.get(key, [])
        open_issue = next((i for i in filed if i["state"] == "open"), None)
        # completed 以外の理由 (not planned、duplicate、理由なし) で閉じた Issue があれば作らない。
        closed = next(
            (i for i in filed if i["state"] == "closed" and i["state_reason"] != "completed"), None
        )
        completed = next((i for i in filed if i["state_reason"] == "completed"), None)
        if key in dismissed:
            skipped.append({**entry, "reason": "dismissed"})
        elif open_issue is not None:
            skipped.append({**entry, "reason": "already_open", "issue": open_issue["number"]})
        elif closed is not None:
            reason = closed["state_reason"]
            skipped.append(
                {
                    **entry,
                    "reason": f"closed_as_{reason}" if reason else "closed_without_reason",
                    "issue": closed["number"],
                }
            )
        elif len(create) >= limit:
            skipped.append({**entry, "reason": "over_limit"})
        else:
            previous = completed["number"] if completed else None
            create.append(
                {
                    **entry,
                    "title": title_for(item),
                    # 表示のための写し。create は下の item・commit・previous から組み立て直す。
                    "body": body_for(key, item, commit, previous),
                    "labels": list(labels),
                    "item": item,
                    "commit": commit,
                    "previous": previous,
                }
            )
    return {
        "format": PLAN_FORMAT,
        "repository": repository,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labels_to_create": [label for label in labels if label.lower() not in labels_present],
        "create": create,
        "skipped": skipped,
    }


def print_plan(plan: dict[str, Any]) -> None:
    print(f"{plan['repository']} に作る Issue：{len(plan['create'])} 件")
    for entry in plan["create"]:
        print(f"  作る：{entry['title']}")
    for label in plan["labels_to_create"]:
        print(f"  作るラベル：{label}")
    for entry in plan["skipped"]:
        issue = f"、#{entry['issue']}" if "issue" in entry else ""
        if "tool" in entry:
            name = f"{entry['tool']} {entry['id']}"
        else:
            name = f"結果 {entry['result']} の項目 {entry['item']}"
        print(f"  作らない：{name}：{reason_text(entry['reason'])}（{entry['reason']}{issue}）")


# 作らない理由の説明。案のファイルには、プログラムが読む理由の名前を書く。
REASON_TEXTS = {
    "dismissed": "対応しない項目として記録済み",
    "already_open": "開いている Issue がある",
    "closed_as_not_planned": "not planned として閉じた Issue がある",
    "closed_as_duplicate": "duplicate として閉じた Issue がある",
    "closed_without_reason": "理由を付けずに閉じた Issue がある",
    "blocked_personal_data": "個人情報に見える文字列があるため止めた",
    "over_limit": "1 回に作る数の上限を超えた",
}


def reason_text(reason: str) -> str:
    if reason in REASON_TEXTS:
        return REASON_TEXTS[reason]
    if reason.startswith("closed_as_"):
        return f"{reason.removeprefix('closed_as_')} として閉じた Issue がある"
    return reason


# --- create ---------------------------------------------------------------------------------


ITEM_SCHEMA_REF = "#/$defs/item"
COMMIT = re.compile(r"^[0-9a-f]{7,64}$")


def parse_plan(data: bytes) -> dict[str, Any]:
    """案のファイルの中身を読み、各項目の本文を組み立て直して確かめる。

    本文は案のファイルの文字列を使わず、項目のデータから plan と同じ関数で組み立て直す。結果から
    来た文字列は必ずコードスパンに入るので、メンションや他のリポジトリへの参照は起きない。
    組み立て直した本文やタイトルが案の写しと違えば、案が書き換えられたとみなす。
    """
    try:
        plan = json.loads(data.decode("utf-8"))
        for text in strings(plan):
            text.encode("utf-8")
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReportError(f"案のファイルが {PLAN_FORMAT} の形式ではありません") from error
    schema = result_schema.load_schema()
    item_schema = {"$ref": ITEM_SCHEMA_REF, "$defs": schema["$defs"]}
    valid = (
        isinstance(plan, dict)
        and plan.get("format") == PLAN_FORMAT
        and isinstance(plan.get("repository"), str)
        and isinstance(plan.get("labels_to_create"), list)
        and isinstance(plan.get("create"), list)
        and all(
            isinstance(entry, dict)
            and all(isinstance(entry.get(k), str) for k in ("tool", "id", "title", "body"))
            and isinstance(entry.get("labels"), list)
            and isinstance(entry.get("item"), dict)
            and not result_schema.validate(entry["item"], item_schema)
            and entry["item"]["id"] == entry["id"]
            and (
                entry.get("commit") is None
                or (isinstance(entry["commit"], str) and COMMIT.fullmatch(entry["commit"]))
            )
            and (entry.get("previous") is None or type(entry["previous"]) is int)
            for entry in plan["create"]
        )
    )
    if not valid:
        raise ReportError(f"案のファイルが {PLAN_FORMAT} の形式ではありません")
    labels = [
        *plan["labels_to_create"],
        *(label for entry in plan["create"] for label in entry["labels"]),
    ]
    if not all(isinstance(label, str) and LABEL.match(label) for label in labels):
        raise ReportError(f"案のファイルが {PLAN_FORMAT} の形式ではありません")
    if any(looks_personal(label) for label in labels):
        raise ReportError("案のファイルのラベルに、個人情報に見えるものがあります")
    keys = [(entry["tool"], entry["id"]) for entry in plan["create"]]
    if len(set(keys)) != len(keys):
        raise ReportError("案のファイルに、同じ項目が 2 回あります")
    for number, (entry, key) in enumerate(zip(plan["create"], keys, strict=True), 1):
        body = body_for(key, entry["item"], entry.get("commit"), entry.get("previous"))
        if entry["body"] != body or entry["title"] != title_for(entry["item"]):
            raise ReportError(
                f"案のファイルの項目 {number} が、今の plan コマンドで作る内容と違います。"
                "plan からやり直してください"
            )
    return dict(plan)


def read_approved_plan(path: Path, digest: str) -> dict[str, Any]:
    """案のファイルを 1 回だけ読み、承認されたハッシュと合うときだけ中身を返す。

    ハッシュの計算と解析に同じバイト列を使う。間にファイルが差し替えられても、承認していない
    中身を使わない。
    """
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ReportError("案のファイルを読めません") from error
    if hashlib.sha256(data).hexdigest() != digest:
        raise ReportError("承認の後に案のファイルが変わりました。plan からやり直してください")
    return parse_plan(data)


def create_issues(plan: dict[str, Any], repository: str, gh: GhRunner) -> int:
    """案のとおりにラベルと Issue を作り、失敗した数を返す。作る直前に重複を確かめ直す。"""
    if plan["repository"] != repository:
        raise ReportError("この案は、別のリポジトリ向けに作られています")
    issues = existing_issues(gh, repository)
    present = existing_labels(gh, repository)
    for label in plan["labels_to_create"]:
        if label.lower() in present:
            continue
        status, _ = gh(
            ["api", "-X", "POST", f"repos/{repository}/labels", "--input", "-"],
            json.dumps({"name": label, "color": "ededed", "description": "shipkeep が作成"}),
        )
        if status != 0:
            # ラベルがないまま Issue を作ると、GitHub が別の色で作るか失敗する。ここで止める。
            print(
                f"ラベル {label} を作れませんでした。Issue は 1 件も作っていません", file=sys.stderr
            )
            return 1
        present.add(label.lower())
    failures = 0
    for number, entry in enumerate(plan["create"], 1):
        key = (entry["tool"], entry["id"])
        filed = issues.get(key, [])
        blocking = next(
            (i for i in filed if i["state"] == "open" or i["state_reason"] != "completed"),
            None,
        )
        if blocking is not None:
            print(f"項目 {number} は作りませんでした：#{blocking['number']} として作成済みです")
            continue
        body = body_for(key, entry["item"], entry.get("commit"), entry.get("previous"))
        if any(looks_personal(text) for text in [entry["title"], body, *strings(entry["item"])]):
            print(f"項目 {number} は作りませんでした：個人情報に見える文字列があります")
            failures += 1
            continue
        payload = {"title": entry["title"], "body": body, "labels": entry["labels"]}
        status, out = gh(
            ["api", "-X", "POST", f"repos/{repository}/issues", "--input", "-"],
            json.dumps(payload),
        )
        created = None
        if status == 0:
            try:
                created = json.loads(out).get("number")
            except (json.JSONDecodeError, AttributeError):
                created = None
        if created is None:
            print(f"項目 {number} の Issue を作れませんでした", file=sys.stderr)
            failures += 1
            continue
        issues.setdefault(key, []).append(
            {"number": created, "state": "open", "state_reason": None}
        )
        print(f"#{created} を作りました：{entry['title']}")
    return failures


# --- record / dismiss / recurring ----------------------------------------------------------


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", text).strip("-.") or "result"


def record(result_path: Path, history: Path) -> str:
    """結果を history に写し、書いたファイルの名前を返す。同じ結果が記録済みなら書かない。"""
    document = load_result(result_path, 1)
    if any(looks_personal(text) for text in strings(document)):
        raise ReportError("結果に、個人情報に見える文字列があります。先に伏せてください")
    day = str(document.get("generated_at", ""))[:10] or date.today().isoformat()
    name = f"{safe_name(day)}-{safe_name(document['tool']['name'])}"
    commit = document.get("target", {}).get("commit")
    if commit:
        name += f"-{commit[:7]}"
    if history.is_dir():
        for existing in history.glob("*.json"):
            try:
                if json.loads(existing.read_text(encoding="utf-8")) == document:
                    raise ReportError("この結果は記録済みです")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
    try:
        history.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReportError("履歴のディレクトリに書き込めません") from error
    candidate = history / f"{name}.json"
    number = 2
    while candidate.exists():
        candidate = history / f"{name}-{number}.json"
        number += 1
    write_json(candidate, document, "履歴のファイル")
    return candidate.name


def dismiss(path: Path, tool: str, item_id: str, reason: str, issue: int | None) -> None:
    """dismissed.json に項目を書く。同じ (tool, id) があれば置き換える。"""
    if not reason.strip():
        raise ReportError("対応しない理由を書いてください")
    if looks_personal(" ".join((tool, item_id, reason))):
        raise ReportError("個人情報に見える文字列があります。値を書かずに書き直してください")
    items = list(load_dismissed(path).values())
    items = [i for i in items if (i["tool"], i["id"]) != (tool, item_id)]
    entry: dict[str, Any] = {
        "tool": tool,
        "id": item_id,
        "reason": " ".join(reason.split()),
        "dismissed_on": date.today().isoformat(),
    }
    if issue is not None:
        entry["issue"] = issue
    items.append(entry)
    items.sort(key=lambda i: (i["tool"], i["id"]))
    document = {"format": DISMISSED_FORMAT, "items": items}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReportError("対応しない項目のファイルに書き込めません") from error
    write_json(path, document, "対応しない項目のファイル")


def recurring(history: Path, minimum: int) -> dict[str, Any]:
    """記録のうち、minimum 回以上 fail だった項目を、回数の多い順に返す。"""
    counts: dict[Key, dict[str, Any]] = {}
    skipped = 0
    duplicates = 0
    runs: set[tuple[str, str, str]] = set()
    files = sorted(history.glob("*.json")) if history.is_dir() else []
    for path in files:
        try:
            document = load_result(path, 1)
        except ReportError:
            skipped += 1
            continue
        tool = document["tool"]["name"]
        # 同じ点検を 2 回記録しても、1 回と数える。コミットも日時もない記録は見分けられない
        # ので、別々の点検として数える。
        commit = document.get("target", {}).get("commit", "")
        generated_at = document.get("generated_at", "")
        if commit or generated_at:
            run = (tool, commit, generated_at)
            if run in runs:
                duplicates += 1
                continue
            runs.add(run)
        failed = {item["id"]: item for item in document["items"] if item["status"] == "fail"}
        for item_id, item in failed.items():
            entry = counts.setdefault(
                (tool, item_id), {"tool": tool, "id": item_id, "check": item["check"], "count": 0}
            )
            entry["count"] += 1
            entry["last"] = path.name
    found = sorted(
        (e for e in counts.values() if e["count"] >= minimum),
        key=lambda e: (-e["count"], e["tool"], e["id"]),
    )
    return {
        "records": len(files),
        "unreadable": skipped,
        "duplicates": duplicates,
        "recurring": found,
    }


# --- コマンドライン -------------------------------------------------------------------------


class QuietArgumentParser(argparse.ArgumentParser):
    """引数の誤りを、使い方とともに終了コード 2 で知らせる。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        # argparse の文は、渡された値 (誤って渡したパスなど) をそのまま含むため出さない。
        print("エラー：引数が正しくありません。--help で使い方を確かめてください", file=sys.stderr)
        raise SystemExit(2)


def repository_arg(value: str) -> str:
    if not REPOSITORY.match(value):
        raise argparse.ArgumentTypeError("owner/name の形で指定してください")
    return value


def digest_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("16 進数の SHA-256 を指定してください")
    return value


def label_arg(value: str) -> str:
    if not LABEL.match(value) or looks_personal(value):
        raise argparse.ArgumentTypeError(
            "ラベルは 1〜50 文字で、カンマと個人情報を含めないでください"
        )
    return value


def build_parser() -> QuietArgumentParser:
    parser = QuietArgumentParser(
        description=(
            "shipkeep の結果から GitHub の Issue の案を作り、承認された案の Issue を作ります。"
            "点検の履歴と、対応しない項目を .shipkeep/ に残します。"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="結果のファイルが形式に合うかを確かめる")
    validate.add_argument("results", nargs="+", type=Path)

    plan = commands.add_parser("plan", help="作る Issue の案を書く。何も変えない")
    plan.add_argument("results", nargs="+", type=Path)
    plan.add_argument(
        "--repo", required=True, type=repository_arg, help="owner/name の形のリポジトリ"
    )
    plan.add_argument("--output", required=True, type=Path, help="案を書くファイル")
    plan.add_argument("--dismissed", type=Path, help=".shipkeep/dismissed.json のファイル")
    plan.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="1 回に作る Issue の数")
    plan.add_argument(
        "--min-severity",
        choices=tuple(SEVERITY_ORDER),
        default="medium",
        help="対象にする最も低い重大度",
    )
    plan.add_argument(
        "--include-unknown",
        action="store_true",
        help="確かめられなかった検査（unknown）も Issue にする",
    )
    plan.add_argument(
        "--label",
        action="append",
        type=label_arg,
        help=f"付けるラベル（くり返し指定できる）。既定：{', '.join(DEFAULT_LABELS)}",
    )

    create = commands.add_parser("create", help="案のラベルと Issue を作る")
    create.add_argument("plan", type=Path)
    create.add_argument(
        "--repo", required=True, type=repository_arg, help="owner/name の形のリポジトリ"
    )
    create.add_argument(
        "--sha256",
        required=True,
        type=digest_arg,
        help="plan が出し、利用者が承認した案の SHA-256",
    )

    record_parser = commands.add_parser("record", help="結果を .shipkeep/history/ に写す")
    record_parser.add_argument("result", type=Path)
    record_parser.add_argument("--into", required=True, type=Path, help="履歴のディレクトリ")

    dismiss_parser = commands.add_parser("dismiss", help="対応しない項目を記録する")
    dismiss_parser.add_argument(
        "--file", required=True, type=Path, help="dismissed.json のファイル"
    )
    dismiss_parser.add_argument("--tool", required=True)
    dismiss_parser.add_argument("--id", required=True, dest="item_id")
    dismiss_parser.add_argument("--reason", required=True)
    dismiss_parser.add_argument("--issue", type=int)

    recurring_parser = commands.add_parser("recurring", help="何度も失敗した項目を挙げる")
    recurring_parser.add_argument("history", type=Path)
    recurring_parser.add_argument("--min-count", type=int, default=2)
    return parser


def use_utf8_output() -> None:
    """標準出力と標準エラーを UTF-8 にする。

    表示は日本語なので、端末やリダイレクト先の文字コード (Windows の cp1252 など) で書けずに
    止まらないようにする。
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None, gh: GhRunner = run_gh) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            failed = False
            for position, path in enumerate(args.results, 1):
                errors = result_schema.validate(read_json(path, f"結果のファイル {position} "))
                for error in errors:
                    print(f"結果 {position}：{error}")
                failed = failed or bool(errors)
                if not errors:
                    print(f"結果 {position}：形式に合っています")
            return 1 if failed else 0
        if args.command == "plan":
            if args.limit < 0:
                raise ReportError("--limit は 0 以上にしてください")
            results = [load_result(p, i) for i, p in enumerate(args.results, 1)]
            labels = tuple(args.label) if args.label else DEFAULT_LABELS
            plan = make_plan(
                results,
                args.repo,
                load_dismissed(args.dismissed),
                existing_issues(gh, args.repo),
                existing_labels(gh, args.repo),
                labels,
                args.limit,
                args.min_severity,
                args.include_unknown,
            )
            digest = write_json(args.output, plan, "案のファイル")
            print_plan(plan)
            print(f"案の SHA-256：{digest}")
            return 0
        if args.command == "create":
            plan_document = read_approved_plan(args.plan, args.sha256)
            return 1 if create_issues(plan_document, args.repo, gh) else 0
        if args.command == "record":
            print(f"記録しました：{record(args.result, args.into)}")
            return 0
        if args.command == "dismiss":
            dismiss(args.file, args.tool, args.item_id, args.reason, args.issue)
            print(f"対応しない項目として記録しました：{args.tool} {args.item_id}")
            return 0
        if args.min_count < 1:
            raise ReportError("--min-count は 1 以上にしてください")
        print(json.dumps(recurring(args.history, args.min_count), ensure_ascii=False, indent=2))
        return 0
    except ReportError as error:
        print(f"エラー：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # 取り込んだ側の出力の設定を変えないよう、スクリプトとして実行したときだけ切り替える。
    use_utf8_output()
    raise SystemExit(main())
