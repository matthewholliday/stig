"""Checks and the environment boundary.

run_checks() executes in a dedicated venv that is a derived artifact of the
dependency manifest — never hand-managed state. The scheduler hashes the
manifest each iteration and rebuilds the venv on mismatch (the same staleness
logic as constraints). Installation failures are check failures.

*What* runs is itself declared in the medium: a ``[[tool.stig.checks]]`` array
in ``pyproject.toml``. That keeps arbitration versioned, human-editable, and
reachable by a handler through the ordinary guarded diff channel — while the
scheduler stays exactly as dumb, running whatever the file names. Nothing here
executes a command that came from an annotation body; annotation bodies are
data. With no declaration, the built-in pytest+ruff pair runs, so a
repository that never opts in behaves as it always did.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Protocol

try:  # pragma: no cover - one branch per interpreter
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

MANIFEST_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")

# Every check is bounded. The scheduler is a dumb loop with no watchdog, so a
# smoke check that boots a server and never returns would hang it forever.
DEFAULT_TIMEOUT = 120.0

_SPEC_KEYS = {"name", "cmd", "timeout", "ok_exit"}
_PYTHON_NAMES = {"python", "python3", "python.exe"}


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


# -- the declared manifest ---------------------------------------------------

class CheckManifestError(Exception):
    """A malformed [[tool.stig.checks]] declaration."""


@dataclass(frozen=True)
class CheckSpec:
    """One declared command. ``cmd`` is argv, never a shell string."""

    name: str
    cmd: list[str]
    timeout: float = DEFAULT_TIMEOUT
    ok_exit: list[int] = field(default_factory=lambda: [0])


def load_check_specs(root: str) -> list[CheckSpec] | None:
    """Parse ``[[tool.stig.checks]]`` out of pyproject.toml.

    Returns None when nothing is declared — the caller falls back to the
    built-in pair. Raises CheckManifestError on a malformed declaration, which
    callers turn into a failed check rather than a crash.
    """
    path = os.path.join(root, "pyproject.toml")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CheckManifestError(f"pyproject.toml is unreadable: {exc}") from exc

    tool = data.get("tool")
    stig = tool.get("stig") if isinstance(tool, dict) else None
    raw = stig.get("checks") if isinstance(stig, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        # An empty array reads as "no arbitration at all", which is what
        # --trust is for; silently accepting every diff must be a flag the
        # operator passes, not a shape the manifest can drift into.
        raise CheckManifestError(
            "tool.stig.checks must be a non-empty array of tables ([[tool.stig.checks]])"
        )
    return [_parse_spec(i, entry) for i, entry in enumerate(raw)]


def _parse_spec(index: int, entry: object) -> CheckSpec:
    where = f"tool.stig.checks[{index}]"
    if not isinstance(entry, dict):
        raise CheckManifestError(f"{where}: must be a table")
    unknown = set(entry) - _SPEC_KEYS
    if unknown:
        raise CheckManifestError(
            f"{where}: unknown key(s) {', '.join(sorted(unknown))}; "
            f"expected {', '.join(sorted(_SPEC_KEYS))}"
        )

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CheckManifestError(f"{where}: name must be a non-empty string")
    name = name.strip()

    cmd = entry.get("cmd")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(p, str) for p in cmd):
        raise CheckManifestError(
            f"{where} ({name}): cmd must be a non-empty array of strings — "
            'an argv list like ["python", "-m", "pytest"], not a shell string'
        )

    timeout = entry.get("timeout", DEFAULT_TIMEOUT)
    if _not_a_number(timeout) or timeout <= 0:
        raise CheckManifestError(f"{where} ({name}): timeout must be a positive number")

    ok_exit = entry.get("ok_exit", [0])
    if (
        not isinstance(ok_exit, list)
        or not ok_exit
        or not all(isinstance(c, int) and not isinstance(c, bool) for c in ok_exit)
    ):
        raise CheckManifestError(
            f"{where} ({name}): ok_exit must be a non-empty array of integers"
        )

    return CheckSpec(name=name, cmd=list(cmd), timeout=float(timeout), ok_exit=list(ok_exit))


def _not_a_number(value: object) -> bool:
    # bool is an int subclass, and `timeout = true` is a mistake, not a timeout.
    return isinstance(value, bool) or not isinstance(value, (int, float))


def ensure_stig_dir(root: str) -> str:
    """Create ``.stig/`` as a self-ignoring directory and return its path.

    Everything under ``.stig`` is a derived artifact — the venv, its manifest
    hash — not working state, so none of it belongs in the medium. Commits stage
    with ``git add -A``, so without the nested ``.gitignore`` the first
    activation would commit an entire venv into the user's history. Being
    ignored also means ``git clean -fd`` on a failed activation leaves it alone
    instead of forcing a rebuild every time.
    """
    stig_dir = os.path.join(root, ".stig")
    os.makedirs(stig_dir, exist_ok=True)
    gitignore = os.path.join(stig_dir, ".gitignore")
    if not os.path.exists(gitignore):
        with open(gitignore, "w", encoding="utf-8") as fh:
            fh.write("*\n")
    return stig_dir


def _manifest_hash(root: str) -> str:
    parts: list[str] = []
    for name in MANIFEST_FILES:
        path = os.path.join(root, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                parts.append(f"{name}\n{fh.read()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def _terminate(proc: subprocess.Popen) -> None:
    """Kill the whole process group, not just the direct child.

    A declared check may boot a server that forks; killing the child alone
    leaves orphans holding ports, and the next activation then fails for a
    reason that has nothing to do with its diff. Windows gets the direct kill —
    there is no group here to signal.
    """
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), 9)
        else:
            proc.kill()
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass


class RealChecks:
    """Declared checks — or the built-in pytest+ruff pair — in a manifest-derived venv."""

    def __init__(self, use_venv: bool = True):
        self.use_venv = use_venv

    def _venv_python(self, root: str) -> str:
        base = os.path.join(root, ".stig", "venv")
        exe = "python.exe" if os.name == "nt" else "python"
        return os.path.join(base, "Scripts" if os.name == "nt" else "bin", exe)

    def _ensure_venv(self, root: str) -> tuple[bool, str]:
        stig_dir = ensure_stig_dir(root)
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

    def _env(self, root: str) -> dict[str, str]:
        """Declared checks see the same venv the built-in pair runs in."""
        env = dict(os.environ)
        if not self.use_venv:
            return env
        venv_dir = os.path.join(root, ".stig", "venv")
        bin_dir = os.path.join(venv_dir, "Scripts" if os.name == "nt" else "bin")
        env["VIRTUAL_ENV"] = venv_dir
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env.pop("PYTHONHOME", None)
        return env

    def _resolve(self, cmd: list[str], python: str) -> list[str]:
        """A bare `python` in a declared cmd means *this* run's interpreter."""
        resolved = list(cmd)
        if resolved[0] in _PYTHON_NAMES:
            resolved[0] = python
        return resolved

    def _run_spec(self, spec: CheckSpec, root: str, python: str) -> CheckResult:
        cmd = self._resolve(spec.cmd, python)
        kwargs = {} if os.name == "nt" else {"start_new_session": True}
        try:
            proc = subprocess.Popen(
                cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=self._env(root), **kwargs,
            )
        except (OSError, ValueError) as exc:
            return CheckResult(False, f"check '{spec.name}' could not run: {exc}")
        try:
            stdout, stderr = proc.communicate(timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            return CheckResult(
                False,
                f"check '{spec.name}' timed out after {spec.timeout:g}s: {' '.join(cmd)}",
            )
        if proc.returncode not in spec.ok_exit:
            return CheckResult(
                False,
                f"check '{spec.name}' failed (exit {proc.returncode}):\n{stdout}\n{stderr}",
            )
        return CheckResult(True, f"check '{spec.name}' passed")

    def _run_default(self, root: str, python: str) -> CheckResult:
        """The built-in pair, for repositories that declare nothing."""
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

    def run(self, root: str) -> CheckResult:
        python = sys.executable
        if self.use_venv:
            ok, msg = self._ensure_venv(root)
            if not ok:
                return CheckResult(False, msg)
            python = self._venv_python(root)

        try:
            specs = load_check_specs(root)
        except CheckManifestError as exc:
            # A malformed manifest is an ordinary failed activation (revert,
            # strike, commit), never a crash — same as malformed model output.
            return CheckResult(False, f"check manifest is malformed: {exc}")

        if specs is None:
            return self._run_default(root, python)

        # Sequential and fail-fast: the first failure is the one the handler
        # needs to see, and later checks would run against a tree about to be
        # reverted anyway.
        for spec in specs:
            result = self._run_spec(spec, root, python)
            if not result.ok:
                return result
        return CheckResult(True, f"checks passed ({len(specs)} declared)")
