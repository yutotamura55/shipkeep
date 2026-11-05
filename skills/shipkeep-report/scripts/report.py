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
        raise OSError(f"{name} is not on PATH")
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
        raise ReportError(f"cannot read the {what}") from error
    except json.JSONDecodeError as error:
        raise ReportError(f"the {what} is not JSON") from error
    # JSON の \ud800 のような対になっていないサロゲートは読めるが、UTF-8 で書けない。
    try:
        for text in strings(document):
            text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ReportError(f"the {what} holds text that is not valid Unicode") from error
    return document


def write_json(path: Path, document: Any, what: str) -> None:
    try:
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReportError(f"cannot write the {what}") from error


def load_result(path: Path, position: int) -> dict[str, Any]:
    document = read_json(path, f"result file {position}")
    # スキーマは最上位を object とするので、validate() が通れば dict になる。型を絞るために並べる。
    if result_schema.validate(document) or not isinstance(document, dict):
        raise ReportError(
            f"result file {position} does not match the shipkeep result format; "
            "run the validate command for details"
        )
    metadata = {key: value for key, value in document.items() if key != "items"}
    if any(looks_personal(text) for text in strings(metadata)):
        raise ReportError(
            f"result file {position} has text outside its items that looks like personal "
            "data; mask it first"
        )
    return document


def load_dismissed(path: Path | None) -> dict[Key, dict[str, Any]]:
    """dismissed.json を読み、(tool, id) から項目への辞書を返す。ファイルがなければ空。"""
    if path is None or not path.exists():
        return {}
    document = read_json(path, "dismissed file")
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
        raise ReportError(f"the dismissed file is not in the {DISMISSED_FORMAT} format")
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
            raise ReportError("GitHub returned an unexpected response")
        items.extend(page)
    return items


def gh_list(gh: GhRunner, path: str, what: str) -> list[Any]:
    code, out = gh(["api", "--paginate", path], None)
    if code != 0:
        raise ReportError(f"could not read the {what} with gh; check that gh is signed in")
    try:
        return parse_pages(out)
    except json.JSONDecodeError as error:
        raise ReportError("GitHub returned an unexpected response") from error


def existing_issues(gh: GhRunner, repository: str) -> dict[Key, list[dict[str, Any]]]:
    """リポジトリの Issue (PR を除く、閉じたものを含む) から、目印のあるものを集める。

    ラベルで絞らない。利用者がラベルを外しても、同じ項目の Issue を作り直さないため。
    """
    issues = gh_list(gh, f"repos/{repository}/issues?state=all&per_page=100", "Issues")
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
    labels = gh_list(gh, f"repos/{repository}/labels?per_page=100", "labels")
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


def code_span(text: str) -> str:
    """Markdown のコードスパンにする。メンションやリンクを働かせず、値を書いたまま見せる。"""
    text = " ".join(str(text).split())
    if not text:
        return "(empty)"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


# 本文のうち、コードスパンの外に置いてよいのは決まった文だけ。メンション (@名前) や、ほかの
# リポジトリの参照 (owner/repo#1) があれば、案が書き換えられたとみなす。
CODE_SPAN = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
NOTIFYING = re.compile(r"@[A-Za-z0-9]|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d")


def notifies(body: str) -> bool:
    """コードスパンと 1 行目の目印を除いた本文に、通知を起こしうる書き方があるかを返す。"""
    rest = body.split("\n", 1)[1] if "\n" in body else ""
    return bool(NOTIFYING.search(CODE_SPAN.sub("", rest)))


def marker(key: Key) -> str:
    tool, item_id = key
    return f"<!-- shipkeep-item tool={quote(tool, safe='')} id={quote(item_id, safe='')} -->"


def title_for(tool: str, item: dict[str, Any]) -> str:
    # タイトルではコードスパンが使えない。@ を全角の @ (U+FF20) にし、メンションとして
    # 読まれないようにする。
    text = " ".join(f"[shipkeep] {tool}: {item['id']}".split()).replace("@", FULLWIDTH_AT)
    if len(text) > MAX_TITLE_LENGTH:
        text = text[: MAX_TITLE_LENGTH - 3] + "..."
    return text


def evidence_line(evidence: dict[str, Any]) -> str:
    parts = [code_span(evidence["kind"])]
    if "commit" in evidence:
        parts.append(f"commit {code_span(evidence['commit'][:12])}")
    if "path" in evidence:
        place = code_span(evidence["path"])
        if "line" in evidence:
            place += f" line {evidence['line']}"
        parts.append(place)
    if "context" in evidence:
        parts.append(f"context {code_span(evidence['context'])}")
    if "detail" in evidence:
        parts.append(code_span(evidence["detail"]))
    return "- " + " · ".join(parts)


def body_for(key: Key, item: dict[str, Any], commit: str | None, previous: int | None) -> str:
    tool, _ = key
    lines = [
        marker(key),
        "shipkeep found an item that needs a decision.",
        "",
        f"- Tool: {code_span(tool)}",
        f"- Check: {code_span(item['check'])}",
    ]
    if "scope" in item:
        lines.append(f"- Scope: {code_span(item['scope'])}")
    status = f"- Status: {item['status']}"
    if "severity" in item:
        status += f" · Severity: {item['severity']}"
    if "confidence" in item:
        status += f" · Confidence: {item['confidence']}"
    lines.append(status)
    if commit:
        lines.append(f"- Inspected commit: {code_span(commit[:12])}")
    if previous is not None:
        lines.append(
            f"- Filed before as #{previous} and closed as completed; "
            "the latest inspection still reports it."
        )
    lines += ["", "### Summary", "", code_span(item["summary"])]
    evidence = item.get("evidence", [])
    if evidence:
        lines += ["", "### Evidence", ""]
        lines += [evidence_line(e) for e in evidence[:MAX_EVIDENCE_LINES]]
        if len(evidence) > MAX_EVIDENCE_LINES:
            lines.append(f"- ...and {len(evidence) - MAX_EVIDENCE_LINES} more in the result file")
    if "note" in item:
        lines += ["", "### Note", "", code_span(item["note"])]
    if "remediation" in item:
        lines += ["", "### How to fix", "", code_span(item["remediation"])]
    lines += [
        "",
        "---",
        "Values that could be secrets or personal data are masked. "
        "If this item will not be fixed, close this Issue as not planned, and record it "
        "in `.shipkeep/dismissed.json` so that later inspections do not file it again.",
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
        if any(looks_personal(text) for text in item_texts(item)):
            # 個人情報に見える値は ID に入っていることもあるので、ID を出さず位置で示す。
            skipped.append({**where, "reason": "blocked_personal_data"})
            continue
        if key in seen:
            continue
        seen.add(key)
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
                    "title": title_for(key[0], item),
                    "body": body_for(key, item, commit, previous),
                    "labels": list(labels),
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
    print(f"Issues to create in {plan['repository']}: {len(plan['create'])}")
    for entry in plan["create"]:
        print(f"  create: {entry['title']}")
    for label in plan["labels_to_create"]:
        print(f"  label to create: {label}")
    for entry in plan["skipped"]:
        issue = f" (#{entry['issue']})" if "issue" in entry else ""
        if "tool" in entry:
            name = f"{entry['tool']} {entry['id']}"
        else:
            name = f"result {entry['result']} item {entry['item']}"
        print(f"  skip: {name}: {entry['reason']}{issue}")


# --- create ---------------------------------------------------------------------------------


def load_plan(path: Path) -> dict[str, Any]:
    plan = read_json(path, "plan file")
    valid = (
        isinstance(plan, dict)
        and plan.get("format") == PLAN_FORMAT
        and isinstance(plan.get("repository"), str)
        and isinstance(plan.get("labels_to_create"), list)
        and all(isinstance(label, str) and LABEL.match(label) for label in plan["labels_to_create"])
        and isinstance(plan.get("create"), list)
        and all(
            isinstance(entry, dict)
            and all(isinstance(entry.get(k), str) for k in ("tool", "id", "title", "body"))
            and isinstance(entry.get("labels"), list)
            and all(isinstance(label, str) and LABEL.match(label) for label in entry["labels"])
            for entry in plan["create"]
        )
    )
    if not valid:
        raise ReportError(f"the plan file is not in the {PLAN_FORMAT} format")
    keys = [(entry["tool"], entry["id"]) for entry in plan["create"]]
    if len(set(keys)) != len(keys):
        raise ReportError("the plan file lists the same item twice")
    for number, (entry, key) in enumerate(zip(plan["create"], keys, strict=True), 1):
        # 案のファイルが書き換えられていないかを、作り方の決まりで確かめる。
        if (
            marker_key(entry["body"]) != key
            or entry["title"] != title_for(key[0], {"id": key[1]})
            or notifies(entry["body"])
        ):
            raise ReportError(f"entry {number} of the plan file was not made by the plan command")
    return dict(plan)


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ReportError("cannot read the plan file") from error


def create_issues(plan: dict[str, Any], repository: str, gh: GhRunner) -> int:
    """案のとおりにラベルと Issue を作り、失敗した数を返す。作る直前に重複を確かめ直す。"""
    if plan["repository"] != repository:
        raise ReportError("the plan was made for another repository")
    issues = existing_issues(gh, repository)
    present = existing_labels(gh, repository)
    for label in plan["labels_to_create"]:
        if label.lower() in present:
            continue
        status, _ = gh(
            ["api", "-X", "POST", f"repos/{repository}/labels", "--input", "-"],
            json.dumps({"name": label, "color": "ededed", "description": "Filed by shipkeep"}),
        )
        if status != 0:
            # ラベルがないまま Issue を作ると、GitHub が別の色で作るか失敗する。ここで止める。
            print(f"failed to create the label {label}; no Issue was created", file=sys.stderr)
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
            print(f"skipped entry {number}: already filed as #{blocking['number']}")
            continue
        if looks_personal(entry["title"]) or looks_personal(entry["body"]):
            print(f"skipped entry {number}: the text looks like personal data")
            failures += 1
            continue
        payload = {"title": entry["title"], "body": entry["body"], "labels": entry["labels"]}
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
            print(f"failed to create the Issue of entry {number}", file=sys.stderr)
            failures += 1
            continue
        issues.setdefault(key, []).append(
            {"number": created, "state": "open", "state_reason": None}
        )
        print(f"created #{created}: {entry['title']}")
    return failures


# --- record / dismiss / recurring ----------------------------------------------------------


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", text).strip("-.") or "result"


def record(result_path: Path, history: Path) -> str:
    """結果を history に写し、書いたファイルの名前を返す。同じ結果が記録済みなら書かない。"""
    document = load_result(result_path, 1)
    if any(looks_personal(text) for text in strings(document)):
        raise ReportError("the result contains text that looks like personal data; mask it first")
    day = str(document.get("generated_at", ""))[:10] or date.today().isoformat()
    name = f"{safe_name(day)}-{safe_name(document['tool']['name'])}"
    commit = document.get("target", {}).get("commit")
    if commit:
        name += f"-{commit[:7]}"
    if history.is_dir():
        for existing in history.glob("*.json"):
            try:
                if json.loads(existing.read_text(encoding="utf-8")) == document:
                    raise ReportError("this result is already recorded")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
    try:
        history.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReportError("cannot write to the history directory") from error
    candidate = history / f"{name}.json"
    number = 2
    while candidate.exists():
        candidate = history / f"{name}-{number}.json"
        number += 1
    write_json(candidate, document, "history file")
    return candidate.name


def dismiss(path: Path, tool: str, item_id: str, reason: str, issue: int | None) -> None:
    """dismissed.json に項目を書く。同じ (tool, id) があれば置き換える。"""
    if not reason.strip():
        raise ReportError("give the reason for not fixing the item")
    if looks_personal(" ".join((tool, item_id, reason))):
        raise ReportError("the text looks like personal data; write it without the value")
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
        raise ReportError("cannot write the dismissed file") from error
    write_json(path, document, "dismissed file")


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
        # 同じ点検を 2 回記録しても、1 回と数える。
        run = (tool, document.get("target", {}).get("commit", ""), document.get("generated_at", ""))
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
        print("error: invalid arguments; see --help", file=sys.stderr)
        raise SystemExit(2)


def repository_arg(value: str) -> str:
    if not REPOSITORY.match(value):
        raise argparse.ArgumentTypeError("use owner/name")
    return value


def digest_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("a SHA-256 digest in hex")
    return value


def label_arg(value: str) -> str:
    if not LABEL.match(value):
        raise argparse.ArgumentTypeError("a label is 1 to 50 characters without commas")
    return value


def build_parser() -> QuietArgumentParser:
    parser = QuietArgumentParser(
        description=(
            "Turn shipkeep results into a plan of GitHub Issues, create the Issues of an "
            "approved plan, and keep the history and dismissed items in .shipkeep/."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="check result files against the format")
    validate.add_argument("results", nargs="+", type=Path)

    plan = commands.add_parser("plan", help="write the Issues to create; changes nothing")
    plan.add_argument("results", nargs="+", type=Path)
    plan.add_argument("--repo", required=True, type=repository_arg, help="owner/name")
    plan.add_argument("--output", required=True, type=Path, help="where to write the plan")
    plan.add_argument("--dismissed", type=Path, help="the .shipkeep/dismissed.json file")
    plan.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Issues per run")
    plan.add_argument(
        "--min-severity", choices=tuple(SEVERITY_ORDER), default="medium", help="lowest severity"
    )
    plan.add_argument(
        "--include-unknown", action="store_true", help="also file checks that could not run"
    )
    plan.add_argument(
        "--label",
        action="append",
        type=label_arg,
        help=f"label to add (repeatable); default: {', '.join(DEFAULT_LABELS)}",
    )

    create = commands.add_parser("create", help="create the labels and Issues of a plan")
    create.add_argument("plan", type=Path)
    create.add_argument("--repo", required=True, type=repository_arg, help="owner/name")
    create.add_argument(
        "--sha256",
        required=True,
        type=digest_arg,
        help="the plan digest that plan printed and the user approved",
    )

    record_parser = commands.add_parser("record", help="copy a result into .shipkeep/history/")
    record_parser.add_argument("result", type=Path)
    record_parser.add_argument("--into", required=True, type=Path, help="the history directory")

    dismiss_parser = commands.add_parser("dismiss", help="record an item that will not be fixed")
    dismiss_parser.add_argument("--file", required=True, type=Path, help="dismissed.json")
    dismiss_parser.add_argument("--tool", required=True)
    dismiss_parser.add_argument("--id", required=True, dest="item_id")
    dismiss_parser.add_argument("--reason", required=True)
    dismiss_parser.add_argument("--issue", type=int)

    recurring_parser = commands.add_parser("recurring", help="list items that failed repeatedly")
    recurring_parser.add_argument("history", type=Path)
    recurring_parser.add_argument("--min-count", type=int, default=2)
    return parser


def main(argv: list[str] | None = None, gh: GhRunner = run_gh) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            failed = False
            for position, path in enumerate(args.results, 1):
                errors = result_schema.validate(read_json(path, f"result file {position}"))
                for error in errors:
                    print(f"result {position}: {error}")
                failed = failed or bool(errors)
                if not errors:
                    print(f"result {position}: ok")
            return 1 if failed else 0
        if args.command == "plan":
            if args.limit < 0:
                raise ReportError("--limit must be 0 or more")
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
            write_json(args.output, plan, "plan file")
            print_plan(plan)
            print(f"plan sha256: {file_digest(args.output)}")
            return 0
        if args.command == "create":
            if file_digest(args.plan) != args.sha256:
                raise ReportError("the plan file changed after it was approved; plan again")
            return 1 if create_issues(load_plan(args.plan), args.repo, gh) else 0
        if args.command == "record":
            print(f"recorded {record(args.result, args.into)}")
            return 0
        if args.command == "dismiss":
            dismiss(args.file, args.tool, args.item_id, args.reason, args.issue)
            print(f"dismissed {args.tool} {args.item_id}")
            return 0
        if args.min_count < 1:
            raise ReportError("--min-count must be 1 or more")
        print(json.dumps(recurring(args.history, args.min_count), ensure_ascii=False, indent=2))
        return 0
    except ReportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
