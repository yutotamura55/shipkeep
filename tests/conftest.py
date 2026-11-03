from __future__ import annotations

import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
for skill in ("shipkeep-publish-check", "shipkeep-quality-tools", "shipkeep-refactor"):
    sys.path.insert(0, str(SKILLS / skill / "scripts"))
