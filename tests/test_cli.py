"""The CLI surface (SPEC §12).

These exercise the commands that never touch a model — status, check, strip —
plus the guards that keep run/step from turning a malformed medium or a missing
git repo into a traceback.
"""

from __future__ import annotations

import pytest

from stig.cli import main


@pytest.fixture
def in_repo(workrepo, monkeypatch):
    repo, git = workrepo
    monkeypatch.chdir(repo.root)
    return repo, git


# -- status -----------------------------------------------------------------

def test_status_lists_annotations(in_repo, capsys):
    repo, _ = in_repo
    repo.write("m.py", "# @goal(g01, status=open): do it\ndef f():\n    return 0\n")
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "g01" in out and "goal" in out and "open" in out


def test_status_on_empty_repo(in_repo, capsys):
    assert main(["status"]) == 0
    assert "no annotations" in capsys.readouterr().out


# -- check ------------------------------------------------------------------

def test_check_clean(in_repo, capsys):
    repo, _ = in_repo
    repo.write("m.py", "# @goal(g01, status=open): do it\ndef f():\n    return 0\n")
    assert main(["check"]) == 0
    assert "ok:" in capsys.readouterr().out


def test_check_reports_duplicate_ids(in_repo, capsys):
    repo, _ = in_repo
    repo.write("a.py", "# @goal(g01, status=open): a\ndef f():\n    return 0\n")
    repo.write("b.py", "# @goal(g01, status=open): b\ndef g():\n    return 0\n")
    assert main(["check"]) == 1
    assert "g01" in capsys.readouterr().err


def test_check_reports_grammar_errors_without_crashing(in_repo, capsys):
    """A malformed attribute is a user-fixable error, not a traceback."""
    repo, _ = in_repo
    repo.write("m.py", "# @goal(g01, notakeyvalue): broken\ndef f():\n    return 0\n")
    assert main(["check"]) == 1
    assert "error:" in capsys.readouterr().err


def test_check_reports_stale_verification(in_repo, capsys):
    repo, _ = in_repo
    repo.write(
        "m.py",
        "# @constraint(c01, status=verified, region_hash=deadbeef0000): holds\n"
        "def f():\n    return 0\n",
    )
    assert main(["check"]) == 1
    assert "stale verification" in capsys.readouterr().err


# -- strip ------------------------------------------------------------------

def _bodies(repo):
    return {a.id: a.kind for a in repo.parse_all()}


def test_strip_removes_resolved_keeps_permanent(in_repo, capsys):
    repo, _ = in_repo
    repo.write(
        "m.py",
        "# @goal(g01, status=satisfied): done\n"
        "# @goal(g02, status=open): not done\n"
        "# @unresolved(u01, status=answered): asked and answered\n"
        "# @decision(d01, status=recorded): chose sqlite\n"
        "# @tried(t01, status=recorded, goal=g01, diff_hash=abc): failed approach\n"
        "def f():\n    return 0\n",
    )
    assert main(["strip"]) == 0
    remaining = _bodies(repo)
    assert "g01" not in remaining and "u01" not in remaining
    assert "g02" in remaining  # still open
    assert "d01" in remaining and "t01" in remaining  # the permanent record


def test_strip_all_still_keeps_the_permanent_record(in_repo):
    """SPEC §03: @decision and @tried are never consumed. --all widens the net
    over goals and questions; it does not mean 'including those'."""
    repo, _ = in_repo
    repo.write(
        "m.py",
        "# @goal(g01, status=open): unfinished\n"
        "# @decision(d01, status=recorded): chose sqlite\n"
        "# @tried(t01, status=recorded, goal=g01, diff_hash=abc): failed approach\n"
        "def f():\n    return 0\n",
    )
    assert main(["strip", "--all"]) == 0
    remaining = _bodies(repo)
    assert "g01" not in remaining  # --all takes open goals too
    assert "d01" in remaining and "t01" in remaining


def test_strip_archive_relocates_tried_of_satisfied_goals(in_repo):
    repo, _ = in_repo
    repo.write(
        "m.py",
        "# @goal(g01, status=satisfied): done\n"
        "# @tried(t01, status=recorded, goal=g01, diff_hash=abc): failed approach\n"
        "def f():\n    return 0\n",
    )
    assert main(["strip", "--archive"]) == 0
    assert "t01" not in repo.read("m.py")
    arch = repo.read("ARCHITECTURE.anno")
    assert "t01" in arch and "diff_hash=abc" in arch  # the record moved, not died


# -- guards -----------------------------------------------------------------

def test_run_outside_a_git_repo_exits_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["run"]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_step_outside_a_git_repo_exits_cleanly(tmp_path, monkeypatch, capsys):
    """step guarded the same way run is — it used to raise GitError."""
    monkeypatch.chdir(tmp_path)
    assert main(["step"]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_run_refuses_a_dirty_worktree(in_repo, capsys):
    repo, _ = in_repo
    repo.write("m.py", "# @goal(g01, status=open): do it\ndef f():\n    return 0\n")
    assert main(["run"]) == 1
    assert "uncommitted changes" in capsys.readouterr().err


def test_run_reports_a_malformed_medium_without_crashing(in_repo, capsys):
    repo, git = in_repo
    repo.write("a.py", "# @goal(g01, status=open): a\ndef f():\n    return 0\n")
    repo.write("b.py", "# @goal(g01, status=open): b\ndef g():\n    return 0\n")
    git.commit("human: seed")
    assert main(["run"]) == 1
    assert "duplicate ID" in capsys.readouterr().err


def test_dry_run_leaves_the_worktree_clean(in_repo, capsys):
    repo, git = in_repo
    repo.write("m.py", "# @goal(, status=open): needs an ID\ndef f():\n    return 0\n")
    git.commit("human: seed")
    assert main(["run", "--dry-run"]) == 0
    assert "would activate" in capsys.readouterr().out
    assert not git.has_uncommitted_changes()


def test_adopt_commits_pre_existing_changes(in_repo):
    repo, git = in_repo
    repo.write("m.py", "# @goal(g01, status=satisfied): already done\ndef f():\n    return 0\n")
    # Nothing is actionable, so this reaches fixpoint without a model call.
    assert main(["run", "--adopt"]) == 0
    assert not git.has_uncommitted_changes()


def test_root_flag_targets_another_directory(workrepo, tmp_path_factory, monkeypatch, capsys):
    repo, _ = workrepo
    repo.write("m.py", "# @goal(g01, status=open): do it\ndef f():\n    return 0\n")
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert main(["--root", repo.root, "status"]) == 0
    assert "g01" in capsys.readouterr().out
