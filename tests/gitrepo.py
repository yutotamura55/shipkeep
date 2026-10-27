"""Build throwaway Git repositories for tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

NOREPLY_EMAIL = "12345+tester@users.noreply.github.com"


class GitRepo:
    """A Git repository in a temporary directory, with helpers to commit files."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.git("init", "--quiet", "--initial-branch=main")

    def git(self, *args: str, email: str = NOREPLY_EMAIL) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": email,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        return result.stdout

    def commit(
        self,
        files: dict[str, str],
        message: str = "change",
        email: str = NOREPLY_EMAIL,
    ) -> str:
        """Write ``files`` (an empty string deletes the file) and commit them."""
        for name, content in files.items():
            target = self.path / name
            if content == "" and target.exists():
                self.git("rm", "--quiet", name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            self.git("add", name)
        self.git("commit", "--quiet", "--allow-empty", "-m", message, email=email)
        return self.git("rev-parse", "HEAD").strip()
