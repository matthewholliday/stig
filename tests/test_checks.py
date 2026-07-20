"""The environment boundary.

Two halves: the .stig/ derived-artifact rules, and the declared check manifest —
parsing it, running it, and the guarantee that a repository declaring nothing
still gets the built-in pytest+ruff pair.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from stig.checks import (
    DEFAULT_TIMEOUT,
    CheckManifestError,
    RealChecks,
    StubChecks,
    _manifest_hash,
    ensure_stig_dir,
    load_check_specs,
)


def test_stub_predicate_inspects_the_tree(workrepo):
    repo, _ = workrepo
    checks = StubChecks(predicate=lambda root: os.path.exists(os.path.join(root, "m.py")))
    assert not checks.run(repo.root).ok
    repo.write("m.py", "x = 1\n")
    assert checks.run(repo.root).ok


def test_manifest_hash_tracks_the_manifest(workrepo):
    repo, _ = workrepo
    before = _manifest_hash(repo.root)
    repo.write("requirements.txt", "pytest\n")
    assert _manifest_hash(repo.root) != before


def test_declaring_checks_rebuilds_the_venv(workrepo):
    """The declarations live in pyproject.toml, which is already a manifest
    file — so editing them invalidates the derived venv, as it should."""
    repo, _ = workrepo
    repo.write("pyproject.toml", '[project]\nname = "x"\n')
    before = _manifest_hash(repo.root)
    repo.write(
        "pyproject.toml",
        '[project]\nname = "x"\n\n[[tool.stig.checks]]\nname = "t"\ncmd = ["true"]\n',
    )
    assert _manifest_hash(repo.root) != before


def test_stig_dir_never_enters_the_medium(workrepo):
    """The venv is a derived artifact, not working state. Commits stage with
    `git add -A`, so an un-ignored .stig would put a whole venv into the user's
    history on the first activation."""
    repo, git = workrepo
    stig_dir = ensure_stig_dir(repo.root)
    # Stand in for the venv the real _ensure_venv builds here.
    os.makedirs(os.path.join(stig_dir, "venv", "bin"), exist_ok=True)
    repo.write(".stig/venv/bin/python", "#!/bin/sh\n")
    repo.write(".stig/venv.hash", "deadbeef\n")

    git.stage_all()
    staged = git._run("diff", "--cached", "--name-only").stdout.split()
    assert not any(p.startswith(".stig") for p in staged)


def test_stig_dir_survives_a_failed_activation_revert(workrepo):
    """`git clean -fd` on revert must not delete the venv and force a rebuild
    on every failure."""
    repo, git = workrepo
    ensure_stig_dir(repo.root)
    repo.write(".stig/venv.hash", "deadbeef\n")
    git.revert_worktree()
    assert repo.exists(".stig/venv.hash")


# -- parsing the declared manifest -------------------------------------------

def _pyproject(repo, body: str) -> None:
    repo.write("pyproject.toml", '[project]\nname = "x"\nversion = "0.1.0"\n' + body)


def test_no_pyproject_means_the_built_in_pair(workrepo):
    repo, _ = workrepo
    assert load_check_specs(repo.root) is None


def test_no_checks_table_means_the_built_in_pair(workrepo):
    """A repository that never opts in must behave exactly as it always did."""
    repo, _ = workrepo
    _pyproject(repo, "\n[tool.ruff]\nline-length = 100\n")
    assert load_check_specs(repo.root) is None


def test_specs_parse_in_declaration_order(workrepo):
    repo, _ = workrepo
    _pyproject(repo, """
[[tool.stig.checks]]
name = "tests"
cmd = ["python", "-m", "pytest", "-q"]
timeout = 300
ok_exit = [0, 5]

[[tool.stig.checks]]
name = "smoke"
cmd = ["python", "-m", "myapp", "--help"]
""")
    specs = load_check_specs(repo.root)
    assert [s.name for s in specs] == ["tests", "smoke"]
    assert specs[0].cmd == ["python", "-m", "pytest", "-q"]
    assert specs[0].timeout == 300
    assert specs[0].ok_exit == [0, 5]
    # Defaults: every check is bounded, and only exit 0 passes.
    assert specs[1].timeout == DEFAULT_TIMEOUT
    assert specs[1].ok_exit == [0]


@pytest.mark.parametrize("body, fragment", [
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = "pytest -q"\n', "argv list"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = []\n', "non-empty array"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a", 2]\n', "array of strings"),
    ('\n[[tool.stig.checks]]\ncmd = ["a"]\n', "name must be"),
    ('\n[[tool.stig.checks]]\nname = ""\ncmd = ["a"]\n', "name must be"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a"]\ntimeout = 0\n', "positive number"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a"]\ntimeout = -1\n', "positive number"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a"]\ntimeout = true\n', "positive number"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a"]\nok_exit = []\n', "array of integers"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a"]\nok_exit = 0\n', "array of integers"),
    ('\n[[tool.stig.checks]]\nname = "t"\ncmd = ["a"]\nshell = "rm -rf /"\n', "unknown key"),
    ('\ntool_stig_broken = [\n', "unreadable"),
])
def test_malformed_declarations_are_named_not_guessed_at(workrepo, body, fragment):
    repo, _ = workrepo
    _pyproject(repo, body)
    with pytest.raises(CheckManifestError) as exc:
        load_check_specs(repo.root)
    assert fragment in str(exc.value)


def test_an_empty_checks_array_is_an_error_not_silent_acceptance(workrepo):
    """Running with no arbitration at all is what --trust is for; the manifest
    must not be able to drift into it."""
    repo, _ = workrepo
    _pyproject(repo, "\n[tool.stig]\nchecks = []\n")
    with pytest.raises(CheckManifestError):
        load_check_specs(repo.root)


# -- running the declared manifest -------------------------------------------

@pytest.fixture
def checks():
    """Real runner, current interpreter — no venv is built during tests."""
    return RealChecks(use_venv=False)


def test_a_passing_declaration_passes(workrepo, checks):
    repo, _ = workrepo
    _pyproject(repo, '\n[[tool.stig.checks]]\nname = "ok"\ncmd = ["python", "-c", "pass"]\n')
    assert checks.run(repo.root).ok


def test_a_failing_declaration_names_the_check_and_shows_its_output(workrepo, checks):
    repo, _ = workrepo
    _pyproject(repo, """
[[tool.stig.checks]]
name = "smoke"
cmd = ["python", "-c", "import sys; print('boom'); sys.exit(3)"]
""")
    result = checks.run(repo.root)
    assert not result.ok
    # The handler sees this text as the @tried body; it has to say what failed.
    assert "smoke" in result.output
    assert "exit 3" in result.output
    assert "boom" in result.output


def test_checks_fail_fast(workrepo, checks):
    """A later check must not run against a tree that is about to be reverted."""
    repo, _ = workrepo
    _pyproject(repo, """
[[tool.stig.checks]]
name = "first"
cmd = ["python", "-c", "import sys; sys.exit(1)"]

[[tool.stig.checks]]
name = "second"
cmd = ["python", "-c", "open('ran.txt', 'w').close()"]
""")
    assert not checks.run(repo.root).ok
    assert not repo.exists("ran.txt")


def test_ok_exit_widens_what_counts_as_success(workrepo, checks):
    repo, _ = workrepo
    _pyproject(repo, """
[[tool.stig.checks]]
name = "tolerant"
cmd = ["python", "-c", "import sys; sys.exit(5)"]
ok_exit = [0, 5]
""")
    assert checks.run(repo.root).ok


def test_a_hanging_check_is_a_failed_check(workrepo, checks):
    """The scheduler is a dumb loop with no watchdog: an unbounded check would
    hang it forever, so the timeout is the watchdog."""
    repo, _ = workrepo
    _pyproject(repo, """
[[tool.stig.checks]]
name = "server"
cmd = ["python", "-c", "import time; time.sleep(60)"]
timeout = 1
""")
    result = checks.run(repo.root)
    assert not result.ok
    assert "timed out" in result.output and "server" in result.output


def test_a_timeout_kills_the_whole_process_group(workrepo, checks):
    """A smoke check that boots a server forks; an orphan left holding a port
    would fail the *next* activation for a reason unrelated to its diff."""
    repo, _ = workrepo
    repo.write("grandchild.py", 'import time\ntime.sleep(3)\nopen("orphan.txt", "w").close()\n')
    repo.write("boot.py", (
        "import subprocess, sys, time\n"
        'subprocess.Popen([sys.executable, "grandchild.py"])\n'
        "time.sleep(60)\n"
    ))
    _pyproject(repo, """
[[tool.stig.checks]]
name = "server"
cmd = ["python", "boot.py"]
timeout = 1
""")
    assert not checks.run(repo.root).ok
    # The grandchild would write its marker at t+3s had it survived the kill.
    time.sleep(4)
    assert not repo.exists("orphan.txt")


def test_a_missing_executable_is_a_failed_check_not_a_crash(workrepo, checks):
    repo, _ = workrepo
    _pyproject(repo, '\n[[tool.stig.checks]]\nname = "gone"\ncmd = ["stig-no-such-binary"]\n')
    result = checks.run(repo.root)
    assert not result.ok
    assert "gone" in result.output


def test_a_malformed_manifest_is_a_failed_check_not_a_crash(workrepo, checks):
    """Same class as malformed model output: revert, strike, commit — never a
    traceback out of the scheduler."""
    repo, _ = workrepo
    _pyproject(repo, '\n[[tool.stig.checks]]\nname = "t"\ncmd = "not argv"\n')
    result = checks.run(repo.root)
    assert not result.ok
    assert "malformed" in result.output


def test_bare_python_resolves_to_the_running_interpreter(workrepo, checks):
    repo, _ = workrepo
    _pyproject(repo, f"""
[[tool.stig.checks]]
name = "interpreter"
cmd = ["python", "-c", "import sys; sys.exit(0 if sys.executable == r'{sys.executable}' else 1)"]
""")
    assert checks.run(repo.root).ok


def test_declaring_nothing_still_runs_the_built_in_pair(workrepo, checks, monkeypatch):
    """The regression guard: no declaration must mean today's exact behavior,
    including the exit-5 and missing-ruff tolerances."""
    repo, _ = workrepo
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr("stig.checks.subprocess.run", spy)
    # No tests here at all: pytest exits 5, which the built-in path tolerates.
    assert checks.run(repo.root).ok
    assert any("pytest" in " ".join(c) for c in calls)
    assert any("ruff" in " ".join(c) for c in calls)
