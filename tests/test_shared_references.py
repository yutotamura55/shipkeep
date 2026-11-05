"""skill ごとに置く共有の文書・スキーマの写しが、`shared/` の原本と一致することを確かめる。

skills CLI は skill のディレクトリだけを導入先に写す。そのため、複数の skill が使う文書は、
原本を `shared/` に置き、使う skill の `references/` に同じ内容の写しを置く。
SKILL.md が `references/<名前>` を参照していれば写しが要り、写しがあれば SKILL.md から参照する。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"


def problems(root: Path) -> list[str]:
    """``root`` の skill について、共有の文書の写しの不足・食い違い・未参照を挙げる。"""
    found: list[str] = []
    for original in sorted(f for f in (root / "shared").iterdir() if f.is_file()):
        reference = f"references/{original.name}"
        for skill in sorted((root / "skills").iterdir()):
            skill_md = skill / "SKILL.md"
            if not skill_md.is_file():
                continue
            copy = skill / reference
            referenced = reference in skill_md.read_text(encoding="utf-8")
            if referenced and not copy.is_file():
                found.append(
                    f"{skill.name}: SKILL.md refers to {reference}, but the copy is missing"
                )
            elif copy.is_file() and not referenced:
                found.append(f"{skill.name}: {reference} exists, but SKILL.md does not refer to it")
            elif copy.is_file() and copy.read_bytes() != original.read_bytes():
                found.append(f"{skill.name}: {reference} differs from shared/{original.name}")
    return found


def test_shared_directory_has_the_change_principles() -> None:
    assert (SHARED / "change-principles.md").is_file()


def test_this_repository_has_no_problem_with_shared_copies() -> None:
    assert problems(ROOT) == []


def make_layout(tmp_path: Path, skill_md: str, copy: str | None) -> Path:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "change-principles.md").write_text("original\n")
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(skill_md)
    if copy is not None:
        (skill / "references").mkdir()
        (skill / "references" / "change-principles.md").write_text(copy)
    return tmp_path


def test_a_matching_referenced_copy_is_fine(tmp_path: Path) -> None:
    root = make_layout(tmp_path, "Read references/change-principles.md\n", "original\n")
    assert problems(root) == []


def test_a_missing_copy_is_reported(tmp_path: Path) -> None:
    root = make_layout(tmp_path, "Read references/change-principles.md\n", None)
    assert len(problems(root)) == 1


def test_a_different_copy_is_reported(tmp_path: Path) -> None:
    root = make_layout(tmp_path, "Read references/change-principles.md\n", "edited\n")
    assert len(problems(root)) == 1


def test_an_unreferenced_copy_is_reported(tmp_path: Path) -> None:
    root = make_layout(tmp_path, "No reference here\n", "original\n")
    assert len(problems(root)) == 1


def test_a_skill_without_reference_or_copy_is_fine(tmp_path: Path) -> None:
    root = make_layout(tmp_path, "No reference here\n", None)
    assert problems(root) == []


def test_a_differing_json_copy_is_reported(tmp_path: Path) -> None:
    root = make_layout(
        tmp_path,
        "Read references/change-principles.md and references/x.schema.json\n",
        "original\n",
    )
    (root / "shared" / "x.schema.json").write_text('{"a": 1}\n')
    (root / "skills" / "demo" / "references" / "x.schema.json").write_text('{"a": 2}\n')

    assert problems(root) == ["demo: references/x.schema.json differs from shared/x.schema.json"]
