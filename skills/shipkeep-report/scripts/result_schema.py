"""shipkeep の結果の形式 (references/result.schema.json) に対する、小さな検証。

report.py とテストが使う。原本のスキーマは shared/result.schema.json で、この skill の
references/ に同じ内容の写しを置く。jsonschema ライブラリに頼らず (skill のスクリプトは
標準ライブラリだけを使う)、スキーマが使う語だけを実装する: type、required、properties、
additionalProperties、items、enum、const、pattern、minimum、minLength、$ref (#/$defs/...)、
format (date-time だけ)、allOf、if/then/else、not。検証の前にスキーマ全体を走査し、対応しない語や
format があれば ValueError にして、黙って通さない。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references" / "result.schema.json"
TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
}
ANNOTATIONS = {"$schema", "$id", "title", "description"}
ASSERTIONS = {
    "type",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "pattern",
    "minimum",
    "minLength",
    "$ref",
    "format",
    "allOf",
    "if",
    "then",
    "else",
    "not",
}
SUPPORTED_FORMATS = {"date-time"}
# RFC 3339 の date-time。時刻とタイムゾーンを必ず含む。T と Z は小文字でもよい (RFC 3339 5.6)。
# datetime.fromisoformat に頼らない。Python 3.10 は秒の小数が 1 桁や 2 桁の値を受け付けない。
DATE_TIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$",
    re.IGNORECASE,
)


# エラーの文に入れてよいプロパティの名前。入力のキーは何でもありうる (パスやアドレスなど) ので、
# この形でないものは名前を伏せる。
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@lru_cache(maxsize=1)
def _cached_schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def load_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(_cached_schema())
    return schema


def _name(key: str) -> str:
    return key if SAFE_NAME.match(key) else "<property>"


def _search(pattern: str, value: str) -> bool:
    """JSON Schema (ECMA-262) の pattern として調べる。末尾の $ は文字列の終わりだけに一致させる。

    Python の $ は末尾の改行の直前にも一致するため、\\Z に置き換える。
    """
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.search(pattern, value) is not None


def check_supported(schema: dict[str, Any], where: str = "#") -> None:
    """スキーマ全体を走査し、対応しない語や format があれば ValueError を送出する。"""
    unknown = set(schema) - ANNOTATIONS - ASSERTIONS - {"$defs"}
    if unknown:
        raise ValueError(f"unsupported schema keywords at {where}: {sorted(unknown)}")
    if "format" in schema and schema["format"] not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format at {where}: {schema['format']}")
    for key in ("properties", "$defs"):
        for name, sub in schema.get(key, {}).items():
            check_supported(sub, f"{where}/{key}/{name}")
    for key in ("items", "additionalProperties", "if", "then", "else", "not"):
        if isinstance(schema.get(key), dict):
            check_supported(schema[key], f"{where}/{key}")
    for index, sub in enumerate(schema.get("allOf", [])):
        check_supported(sub, f"{where}/allOf/{index}")


def validate(document: Any, schema: dict[str, Any] | None = None) -> list[str]:
    """スキーマに合わない箇所を "<場所>: <理由>" の一覧で返す。空なら合っている。"""
    root = schema or load_schema()
    check_supported(root)
    errors: list[str] = []
    _check(document, root, root, "$", errors)
    return errors


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"$ref does not point to a schema: {ref}")
    return node


def _valid(value: Any, schema: dict[str, Any], root: dict[str, Any]) -> bool:
    errors: list[str] = []
    _check(value, schema, root, "$", errors)
    return not errors


def _check(
    value: Any, schema: dict[str, Any], root: dict[str, Any], where: str, errors: list[str]
) -> None:
    if "$ref" in schema:
        # 参照先を検証し、同じ階層のほかの制約も続けて検証する (2020-12 の規則)。
        _check(value, _resolve(schema["$ref"], root), root, where, errors)
    for sub in schema.get("allOf", []):
        _check(value, sub, root, where, errors)
    if "if" in schema:
        branch = "then" if _valid(value, schema["if"], root) else "else"
        if branch in schema:
            _check(value, schema[branch], root, where, errors)
    if "not" in schema and _valid(value, schema["not"], root):
        errors.append(f"{where}: must not match the 'not' schema")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: must be one of {schema['enum']}")
    if "type" in schema:
        expected = TYPES[schema["type"]]
        # bool は int の部分型なので、integer では除く。
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            errors.append(f"{where}: must be {schema['type']}")
            return
    if isinstance(value, str):
        if "pattern" in schema and not _search(schema["pattern"], value):
            errors.append(f"{where}: does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']}")
        if schema.get("format") == "date-time" and not _is_date_time(value):
            errors.append(f"{where}: not an RFC 3339 date-time")
    is_integer = isinstance(value, int) and not isinstance(value, bool)
    if is_integer and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{where}: below {schema['minimum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing {key}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], root, f"{where}.{key}", errors)
            elif isinstance(schema.get("additionalProperties"), dict):
                _check(item, schema["additionalProperties"], root, f"{where}.{_name(key)}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: unexpected property {_name(key)}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], root, f"{where}[{index}]", errors)


def _is_date_time(value: str) -> bool:
    match = DATE_TIME.match(value)
    if not match:
        return False
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    try:
        datetime(year, month, day, hour, minute, second)
    except ValueError:
        return False
    offset_hour, offset_minute = match.group(8), match.group(9)
    if offset_hour is None:
        return True
    return int(offset_hour) <= 23 and int(offset_minute) <= 59
