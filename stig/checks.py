"""Checks and the environment boundary (SPEC §06).

run_checks() executes in a dedicated venv that is a derived artifact of the
dependency manifest — never hand-managed state. The scheduler hashes the
manifest each iteration and rebuilds the venv on mismatch (the same staleness
logic as constraints). Installation failures are check failures.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol

MANIFEST_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")


@dataclass
class CheckResult:
    ok: bool
    output: str


class Checks(Protocol):
    def run(self, root: str) -> CheckResult:  # pragma: no cover - protocol
        ...


class StubChecks:
    """Deterministic checks for tests: a fixed verdict, or a predicate on the tree."""

    def __init__(self, ok: bool = True, output: str = "", predicate=None):
        self._ok = ok
        self._output = output
        self._predicate = predicate

    def run(self, root: str) -> CheckResult:
        if self._predicate is not None:
            ok = bool(self._predicate(root))
            return CheckResult(ok, "" if ok else "predicate failed")
        return CheckResult(self._ok, self._output)


def _manifest_hash(root: str) -> str:
    parts: list[str] = []
    for name in MANIFEST_FILES:
        path = os.path.join(root, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                parts.append(f"{name}\n{fh.read()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


class RealChecks:
    """pytest + ruff in a venv rebuilt whenever the manifest hash changes."""

    def __init__(self, use_venv: bool = True):
        self.use_venv = use_venv

    def _venv_python(self, root: str) -> str:
        base = os.path.join(root, ".stig", "venv")
        exe = "python.exe" if os.name == "nt" else "python"
        return os.path.join(base, "Scripts" if os.name == "nt" else "bin", exe)

    def _ensure_venv(self, root: str) -> tuple[bool, str]:
        stig_dir = os.path.join(root, ".stig")
        os.makedirs(stig_dir, exist_ok=True)
        hash_path = os.path.join(stig_dir, "venv.hash")
        current = _manifest_hash(root)
        stored = ""
        if os.path.exists(hash_path):
            with open(hash_path, encoding="utf-8") as fh:
                stored = fh.read().strip()
        venv_python = self._venv_python(root)
        if stored == current and os.path.exists(venv_python):
            return True, ""
        # Rebuild: the venv is a derived artifact of the manifest.
        venv_dir = os.path.join(stig_dir, "venv")
        proc = subprocess.run(
            [sys.executable, "-m", "venv", venv_dir], capture_output=True, text=True
        )
        if proc.returncode != 0:
            return False, f"venv creation failed: {proc.stderr}"
        pip = [venv_python, "-m", "pip", "install", "-q", "pytest", "ruff"]
        if os.path.exists(os.path.join(root, "requirements.txt")):
            pip += ["-r", "requirements.txt"]
        elif os.path.exists(os.path.join(root, "pyproject.toml")):
            pip += ["-e", "."]
        proc = subprocess.run(pip, cwd=root, capture_output=True, text=True)
        if proc.returncode != 0:
            return False, f"dependency install failed: {proc.stdout}\n{proc.stderr}"
        with open(hash_path, "w", encoding="utf-8") as fh:
            fh.write(current)
        return True, ""

    def run(self, root: str) -> CheckResult:
        python = sys.executable
        if self.use_venv:
            ok, msg = self._ensure_venv(root)
            if not ok:
                return CheckResult(False, msg)
            python = self._venv_python(root)

        pytest = subprocess.run(
            [python, "-m", "pytest", "-q"], cwd=root, capture_output=True, text=True
        )
        # Exit code 5 means "no tests collected" — not a failure.
        if pytest.returncode not in (0, 5):
            return CheckResult(False, f"pytest failed:\n{pytest.stdout}\n{pytest.stderr}")

        ruff = subprocess.run(
            [python, "-m", "ruff", "check", "."], cwd=root, capture_output=True, text=True
        )
        # A missing ruff (nonzero with an import error) is tolerated; lint failures are not.
        if ruff.returncode not in (0,) and "No module named" not in ruff.stderr:
            return CheckResult(False, f"ruff failed:\n{ruff.stdout}\n{ruff.stderr}")

        return CheckResult(True, "checks passed")
