from __future__ import annotations

import difflib
import json

import pytest

from stig.gitutil import init_repo
from stig.repo import Repo


@pytest.fixture
def workrepo(tmp_path):
    """A fresh git repo with an initial commit; returns (Repo, Git)."""
    root = str(tmp_path)
    git = init_repo(root)
    (tmp_path / "ARCHITECTURE.anno").write_text("# architecture-scoped annotations\n")
    git.commit("human: initial")
    return Repo(root), git


def write(repo: Repo, rel: str, text: str) -> None:
    repo.write(rel, text)


def git_diff(repo: Repo, rel: str, new_content: str) -> str:
    """A git-applyable unified diff from the current file content to new_content."""
    old = repo.read(rel) if repo.exists(rel) else ""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    return "".join(diff)


def new_file_diff(rel: str, content: str) -> str:
    """A git-applyable diff that creates a new file."""
    if not content.endswith("\n"):
        content += "\n"
    lines = content.splitlines()
    body = "".join(f"+{ln}\n" for ln in lines)
    return (
        f"diff --git a/{rel} b/{rel}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{rel}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}"
    )


def handler_json(diff: str = "", updates=None, new_annotations=None) -> str:
    return json.dumps(
        {
            "diff": diff,
            "updates": updates or [],
            "new_annotations": new_annotations or [],
        }
    )
