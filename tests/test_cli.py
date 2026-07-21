"""The CLI surface.

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


def test_check_reports_a_malformed_check_manifest(in_repo, capsys):
    """A bad check declaration is the same class of error as bad grammar: CI
    should catch it here, not as a mystery failed activation later."""
    repo, _ = in_repo
    repo.write(
        "pyproject.toml",
        '[project]\nname = "x"\n\n[[tool.stig.checks]]\nname = "t"\ncmd = "pytest -q"\n',
    )
    assert main(["check"]) == 1
    assert "cmd must be" in capsys.readouterr().err


def test_check_accepts_a_well_formed_check_manifest(in_repo, capsys):
    repo, _ = in_repo
    repo.write(
        "pyproject.toml",
        '[project]\nname = "x"\n\n[[tool.stig.checks]]\n'
        'name = "tests"\ncmd = ["python", "-m", "pytest", "-q"]\n',
    )
    assert main(["check"]) == 0
    assert "ok:" in capsys.readouterr().out


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
    """@decision and @tried are never consumed. --all widens the net
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


# -- init -------------------------------------------------------------------

def test_init_scaffolds_a_runnable_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "tempo"]) == 0
    root = tmp_path / "tempo"
    for rel in ("pyproject.toml", "ARCHITECTURE.anno", "tempo/__init__.py", "tests/__init__.py"):
        assert (root / rel).exists(), rel
    assert (root / ".git").is_dir()
    assert 'name = "tempo"' in (root / "pyproject.toml").read_text()


def test_init_commits_so_the_dirty_guard_passes(tmp_path, monkeypatch):
    """A scaffold left uncommitted is exactly what `stig run` refuses to touch."""
    monkeypatch.chdir(tmp_path)
    main(["init", "tempo"])
    from stig.gitutil import Git
    assert not Git(str(tmp_path / "tempo")).has_uncommitted_changes()


def test_init_no_commit_leaves_the_tree_dirty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init", "tempo", "--no-commit"])
    from stig.gitutil import Git
    assert Git(str(tmp_path / "tempo")).has_uncommitted_changes()


def test_init_never_clobbers_existing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "mine"\n')
    (root / "ARCHITECTURE.anno").write_text("# @goal(g07, status=open): keep me\n")
    assert main(["init", "legacy"]) == 0
    assert (root / "pyproject.toml").read_text() == '[project]\nname = "mine"\n'
    assert "keep me" in (root / "ARCHITECTURE.anno").read_text()


def test_init_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "tempo"]) == 0
    before = (tmp_path / "tempo" / "ARCHITECTURE.anno").read_text()
    assert main(["init", "tempo"]) == 0
    assert (tmp_path / "tempo" / "ARCHITECTURE.anno").read_text() == before


def test_init_template_guidance_is_not_itself_actionable(tmp_path, monkeypatch):
    """A '# @goal' example in the template would be a goal the scheduler runs.

    The only annotation a fresh scaffold may contain is the layout @decision,
    which is terminal and never actionable.
    """
    monkeypatch.chdir(tmp_path)
    main(["init", "tempo"])
    from stig.repo import Repo
    kinds = [a.kind for a in Repo(str(tmp_path / "tempo")).parse_all()]
    assert kinds == ["decision"]


def test_init_records_the_layout_so_handlers_cannot_shadow_the_package(tmp_path, monkeypatch):
    """Regression: handlers wrote `<pkg>.py` beside the scaffolded `<pkg>/`.

    The empty package won every import, so downstream goals ran against a module
    whose contents had silently vanished.
    """
    monkeypatch.chdir(tmp_path)
    main(["init", "tempo"])
    from stig.repo import Repo
    decision = Repo(str(tmp_path / "tempo")).parse_all()[0]
    assert decision.kind == "decision"
    assert "tempo/" in decision.full_body and "shadow" in decision.full_body


def test_init_derives_a_valid_package_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "my-cool.tool"]) == 0
    assert (tmp_path / "my-cool.tool" / "my_cool_tool" / "__init__.py").exists()


def test_init_package_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "proj", "--package", "core"]) == 0
    assert (tmp_path / "proj" / "core" / "__init__.py").exists()
