"""The environment boundary (SPEC §06)."""

from __future__ import annotations

import os

from stig.checks import StubChecks, _manifest_hash, ensure_stig_dir


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
