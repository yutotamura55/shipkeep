"""表示を日本語で書き、どの端末やリダイレクト先でも書けることを確かめる (#45)。"""

from __future__ import annotations

import inspect
import io
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import hotspots
import inventory
import report
import scan

MODULES = [scan, inventory, hotspots, report]
SKILLS = Path(__file__).resolve().parents[1] / "skills"
SCRIPTS = [
    SKILLS / "shipkeep-publish-check" / "scripts" / "scan.py",
    SKILLS / "shipkeep-quality-tools" / "scripts" / "inventory.py",
    SKILLS / "shipkeep-refactor" / "scripts" / "hotspots.py",
    SKILLS / "shipkeep-report" / "scripts" / "report.py",
]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__)
def test_use_utf8_output_switches_text_streams_to_utf8(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    # TextIOWrapper でない流れ (テストの差し替えなど) は、そのままにする。
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    module.use_utf8_output()

    assert stdout.encoding == "utf-8"
    stdout.write("日本語")
    stdout.flush()
    assert stdout.buffer.getvalue().decode("utf-8") == "日本語"
    assert sys.stderr is stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_help_is_japanese_even_when_the_output_encoding_is_ascii(script: Path) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
    completed = subprocess.run(  # noqa: S603 (このリポジトリのスクリプトを固定の引数で起動する)
        [sys.executable, str(script), "--help"],
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    help_text = completed.stdout.decode("utf-8")
    # 説明は日本語。ASCII の出力先でも、文字を落とさず UTF-8 で書く。
    assert any(not char.isascii() for char in help_text)
    assert "\\u" not in help_text


def test_inventory_summaries_say_what_is_there_and_what_is_missing() -> None:
    def summary(status: str, scope: str = "python", tools: list[str] | None = None) -> str:
        row = {"aspect": "dependency_audit", "scope": scope, "status": status}
        return inventory.summary_for({**row, "tools": tools or []}, "Python")

    assert summary("pass", tools=["pip-audit"]) == "Python の依存の監査の道具がある（pip-audit）"
    assert summary("fail") == "Python の依存の監査の道具が見つからない"
    assert summary("unknown") == "Python の依存の監査を判定できない"
    assert summary("not_applicable") == "Python の依存の監査は対象外"


def test_join_words_puts_spaces_only_between_latin_and_japanese() -> None:
    assert inventory.join_words("JavaScript / TypeScript", "の", "lint", "の道具") == (
        "JavaScript / TypeScript の lint の道具"
    )
    assert inventory.join_words("リポジトリ全体", "の", "CI") == "リポジトリ全体の CI"
    assert (
        inventory.join_words("C#", "（ソースファイルだけで検出）")
        == "C#（ソースファイルだけで検出）"
    )


def test_every_aspect_has_a_japanese_name() -> None:
    aspects = set(inventory.LANGUAGE_ASPECTS)
    aspects |= {aspect for aspect, _, _ in inventory.REPOSITORY_ASPECTS}
    assert aspects == set(inventory.ASPECT_NAMES)


def test_titles_lead_with_the_summary_and_neutralize_mentions_and_references() -> None:
    title = report.title_for({"id": "readme", "summary": "@owner を #1 で呼ぶ"})

    # 全角の @ と # は、ruff の RUF001 に当たるのでコードポイントで書く。
    assert title == "[shipkeep] \uff20owner を \uff031 で呼ぶ（readme）"


def test_long_titles_keep_the_id_and_cut_the_summary() -> None:
    title = report.title_for({"id": "lint/python", "summary": "長い要約" * 60})

    assert len(title) == report.MAX_TITLE_LENGTH
    assert title.endswith("…（lint/python）")


def test_titles_keep_a_number_sign_that_is_not_a_reference() -> None:
    title = report.title_for({"id": "lint/csharp", "summary": "C# の lint の道具が見つからない"})

    assert title == "[shipkeep] C# の lint の道具が見つからない（lint/csharp）"


def test_plan_output_explains_each_reason_in_japanese(capsys: pytest.CaptureFixture[str]) -> None:
    report.print_plan(
        {
            "repository": "owner/demo",
            "create": [],
            "labels_to_create": [],
            "skipped": [
                {"tool": "t", "id": "a", "reason": "over_limit"},
                {"tool": "t", "id": "b", "reason": "already_open", "issue": 12},
                {"tool": "t", "id": "c", "reason": "closed_as_reopened"},
                {"tool": "t", "id": "d", "reason": "new_reason"},
            ],
        }
    )

    out = capsys.readouterr().out
    assert "t a：1 回に作る数の上限を超えた（over_limit）" in out
    assert "t b：開いている Issue がある（already_open、#12）" in out
    assert "t c：reopened として閉じた Issue がある（closed_as_reopened）" in out
    assert "t d：new_reason（new_reason）" in out


def test_a_pass_row_without_tools_has_no_empty_parentheses() -> None:
    row = {"aspect": "tests", "scope": "python", "status": "pass", "tools": []}

    assert inventory.summary_for(row, "Python") == "Python のテストの道具がある"


def test_the_copies_of_use_utf8_output_are_the_same() -> None:
    # skill は別々に導入されるので、同じ関数を各スクリプトに写して持つ。写しがずれないようにする。
    sources = {inspect.getsource(module.use_utf8_output) for module in MODULES}
    assert len(sources) == 1


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_running_as_a_script_switches_the_output_to_utf8(
    script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(sys, "argv", [str(script), "--help"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(script), run_name="__main__")

    assert exit_info.value.code == 0
    stdout.flush()
    assert any(not char.isascii() for char in stdout.buffer.getvalue().decode("utf-8"))
