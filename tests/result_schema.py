"""shared/result.schema.json に対する、テスト用の小さな検証。

jsonschema ライブラリに頼らず、スキーマが使う語だけを実装する: type、required、properties、
additionalProperties、items、enum、const、pattern、minimum、minLength、$ref (#/$defs/...)、
format (date-time)。対応しない語があれば失敗にして、黙って通さない。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "shared" / "result.schema.json"
TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
}
KNOWN_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
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
    "$defs",
}


def load_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


def validate(document: Any, schema: dict[str, Any] | None = None) -> list[str]:
    """スキーマに合わない箇所を "<場所>: <理由>" の一覧で返す。空なら合っている。"""
    root = schema or load_schema()
    errors: list[str] = []
    _check(document, root, root, "$", errors)
    return errors


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    assert isinstance(node, dict)
    return node


def _check(
    value: Any, schema: dict[str, Any], root: dict[str, Any], where: str, errors: list[str]
) -> None:
    unknown = set(schema) - KNOWN_KEYWORDS
    if unknown:
        raise ValueError(f"unsupported schema keywords at {where}: {sorted(unknown)}")
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, where, errors)
        return
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
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']}")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{where}: not a date-time")
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
                _check(item, schema["additionalProperties"], root, f"{where}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: unexpected property {key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], root, f"{where}[{index}]", errors)
