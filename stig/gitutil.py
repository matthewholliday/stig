"""Git integration (SPEC §10). Git history is part of the medium.

One activation = one commit. Activation numbering and audit provenance are
derived from git history, never from process memory.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(RuntimeError):
    pass


@dataclass
class Git:
    root: str

    def _run(self, *args: str, check: bool = True, input_text: str | None = None):
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            input=input_text,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc

    # -- repository state ----------------------------------------------------

    def is_repo(self) -> bool:
        proc = self._run("rev-parse", "--is-inside-work-tree", check=False)
        return proc.returncode == 0

    def has_commits(self) -> bool:
        return self._run("rev-parse", "--verify", "HEAD", check=False).returncode == 0

    def head(self) -> str | None:
        if not self.has_commits():
            return None
        return self._run("rev-parse", "HEAD").stdout.strip()

    def has_uncommitted_changes(self) -> bool:
        """True if the working tree or index has changes (tracked or untracked)."""
        proc = self._run("status", "--porcelain")
        return bool(proc.stdout.strip())

    def changed_files_in_head(self) -> set[str]:
        """Files touched by the commit at HEAD (SPEC §06 priority rule)."""
        if not self.has_commits():
            return set()
        # For the root commit, diff-tree has no parent; use --root.
        proc = self._run(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "HEAD"
        )
        return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}

    def activation_count(self) -> int:
        """Number of activations already recorded in history (SPEC §10)."""
        if not self.has_commits():
            return 0
        proc = self._run("log", "--grep", "^activation:", "--format=%H", check=False)
        return len([ln for ln in proc.stdout.splitlines() if ln.strip()])

    # -- mutations -----------------------------------------------------------

    def stage_all(self) -> None:
        self._run("add", "-A")

    def commit(self, message: str) -> str:
        self.stage_all()
        # Allow empty so an activation that produced no net change still records.
        self._run("commit", "--allow-empty", "-m", message)
        return self.head() or ""

    def revert_worktree(self) -> None:
        """Discard all working-tree changes; the tree never accumulates half work."""
        if self.has_commits():
            self._run("checkout", "--", ".", check=False)
        self._run("clean", "-fd", check=False)

    def apply_patch(self, diff_text: str) -> None:
        """Apply a unified diff. Raises GitError if it does not apply cleanly."""
        if not diff_text.strip():
            return
        if not diff_text.endswith("\n"):
            diff_text += "\n"
        check = self._run("apply", "--check", "-", check=False, input_text=diff_text)
        if check.returncode != 0:
            raise GitError(f"patch does not apply: {check.stderr.strip()}")
        self._run("apply", "-", input_text=diff_text)


def init_repo(root: str) -> Git:
    git = Git(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "stig@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Stig"], cwd=root, check=True)
    return git
