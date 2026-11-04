"""scan.py と inventory.py の、部分的な clone・引数の誤り・UTF-8 でないパスの扱い (#14)。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from gitrepo import GitRepo

import inventory
import scan


def add_raw(repo: GitRepo, raw: bytes, content: str) -> None:
    """UTF-8 でない名前のファイルを書き、索引に加える。"""
    name = os.fsdecode(raw)
    target = repo.path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    repo.git("add", "--", name)


def scan_check(result: dict[str, Any], check_id: str) -> dict[str, Any]:
    for item in result["checks"]:
        if item["id"] == check_id:
            return dict(item)
    raise AssertionError(f"{check_id} is missing")


def test_scan_refuses_a_partial_clone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "# x\n"})
    repo.git("config", "extensions.partialClone", "origin")

    with pytest.raises(scan.ScanError, match="partial clone"):
        scan.scan(tmp_path)

    assert scan.main([str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "partial clone" in err
    assert str(tmp_path) not in err


def test_scan_argument_errors_do_not_echo_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        scan.main([str(tmp_path), "--unknown", str(tmp_path / "secret-name")])

    assert "secret-name" not in capsys.readouterr().err


def test_scan_reads_a_gitignore_in_a_non_utf8_directory(tmp_path: Path) -> None:
    # .gitignore を読むとき、パスを Git に渡し直す。置換文字にすると別の名前になり、
    # git show が失敗していた。
    repo = GitRepo(tmp_path)
    # .env は、無視する規則を加える前に索引へ入れる (規則の後では git add が拒む)。
    add_raw(repo, b"caf\xe9/.env", "KEY=1\n")
    add_raw(repo, b"caf\xe9/.gitignore", ".env\n")
    add_raw(repo, b"caf\xe9/.env.example", "KEY=\n")
    repo.commit({"README.md": "# x\n"})

    result = scan.scan(tmp_path)

    assert scan_check(result, "env_files")["status"] == "pass"
    sensitive = scan_check(result, "sensitive_files")["findings"]
    assert [item["path"] for item in sensitive] == ["caf�/.env"]
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_inventory_argument_errors_do_not_echo_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        inventory.main([str(tmp_path), str(tmp_path / "secret-name")])

    assert "secret-name" not in capsys.readouterr().err


def test_inventory_reads_a_config_in_a_non_utf8_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 置換文字にした名前ではファイルを開けず、設定を見落としていた。
    repo = GitRepo(tmp_path)
    add_raw(repo, b"caf\xe9/pyproject.toml", "[tool.mypy]\nstrict = true\n")
    repo.commit({"app.py": "x = 1\n"})

    result = inventory.inventory(tmp_path)

    type_check = next(
        r for r in result["aspects"] if r["scope"] == "python" and r["aspect"] == "type_check"
    )
    assert type_check["evidence"] == ["caf�/pyproject.toml"]
    assert inventory.main([str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == inventory.SCHEMA_VERSION


def test_scan_refuses_a_promisor_remote(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "# x\n"})
    repo.git("config", "remote.origin.promisor", "true")

    with pytest.raises(scan.ScanError, match="partial clone"):
        scan.scan(tmp_path)


def test_scan_and_inventory_tell_git_not_to_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Git 2.44 以降では、この環境変数で欠けたオブジェクトの取得を止める。
    repo = GitRepo(tmp_path)
    repo.commit({"README.md": "# x\n", "app.py": "x = 1\n"})
    seen: list[str | None] = []
    original = subprocess.run

    def recording_run(command: list[str], **kwargs: Any) -> Any:
        if "git" in os.path.basename(command[0]):
            seen.append((kwargs.get("env") or {}).get("GIT_NO_LAZY_FETCH"))
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    scan.scan(tmp_path)
    inventory.inventory(tmp_path)

    assert seen
    assert set(seen) == {"1"}


@pytest.mark.parametrize("setting", ["extensions.partialClone", "remote.origin.promisor"])
def test_inventory_refuses_a_partial_clone(
    tmp_path: Path, setting: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = GitRepo(tmp_path)
    repo.commit({"app.py": "x = 1\n"})
    repo.git("config", setting, "origin" if setting.startswith("extensions") else "true")

    assert inventory.main([str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "partial clone" in err
    assert str(tmp_path) not in err


def test_inventory_works_under_a_non_utf8_checkout_path(tmp_path: Path) -> None:
    root = tmp_path / os.fsdecode(b"r\xe9po")
    repo = GitRepo(root)
    repo.commit({"app.py": "x = 1\n"})

    result = inventory.inventory(root)

    assert [lang["id"] for lang in result["languages"]] == ["python"]


def test_scan_reads_a_quoted_non_utf8_path(tmp_path: Path) -> None:
    # タブを含む名前を Git は引用符で囲む。中の UTF-8 でないバイトもそのまま戻す。
    repo = GitRepo(tmp_path)
    add_raw(repo, b"caf\xe9\tx/.env", "KEY=1\n")
    add_raw(repo, b"caf\xe9\tx/.gitignore", ".env\n")
    add_raw(repo, b"caf\xe9\tx/.env.example", "KEY=\n")
    repo.commit({"README.md": "# x\n"})

    result = scan.scan(tmp_path)

    assert scan_check(result, "env_files")["status"] == "pass"
    sensitive = scan_check(result, "sensitive_files")["findings"]
    assert [item["path"] for item in sensitive] == ["caf�\tx/.env"]


@pytest.mark.parametrize(
    ("quoted", "expected"),
    [
        ('"a\\tb"', "a\tb"),
        ('"q\\"\\\\"', 'q"\\'),
        ('"caf\\351"', os.fsdecode(b"caf\xe9")),
        ('"\\346\\227\\245"', "日"),
        ("plain", "plain"),
    ],
)
def test_unquote_path_decodes_git_quoting_as_bytes(quoted: str, expected: str) -> None:
    assert scan.unquote_path(quoted) == expected
