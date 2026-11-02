"""skill ごとに置く共有の文書の写しが、`shared/` の原本と一致することを確かめる。

skills CLI は skill のディレクトリだけを導入先に写す。そのため、複数の skill が使う文書は、
原本を `shared/` に置き、各 skill の `references/` に同じ内容の写しを置く。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"


def test_shared_directory_has_the_change_principles() -> None:
    assert (SHARED / "change-principles.md").is_file()


def test_every_copy_under_skills_matches_its_shared_original() -> None:
    for original in SHARED.glob("*.md"):
        for copy in ROOT.glob(f"skills/*/references/{original.name}"):
            assert copy.read_bytes() == original.read_bytes(), (
                f"{copy.relative_to(ROOT)} differs from {original.relative_to(ROOT)}; "
                "copy the shared original again"
            )
