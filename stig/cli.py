"""The CLI surface (SPEC §12).

    stig run [--budget N] [--dry-run] [--trust] [--adopt]
    stig step
    stig status
    stig check
    stig strip [--all] [--archive]
    stig init [name] [--package P] [--seed "<prompt>"] [--no-commit]
    stig seed "<prompt>"

stig step and stig run produce identical per-activation behavior; run is just
step in a loop. This keeps the single-step path honest and debuggable.
"""

from __future__ import annotations

import argparse
import os
import sys

from .annotations import PERMANENT_KINDS, GrammarError
from .checks import MANIFEST_FILES, RealChecks
from .gitutil import Git, GitError, init_repo
from .models import DEFAULT_MODEL, AnthropicModel
from .regions import (
    missing_enforcing_tests,
    region_has_executable_lines,
    region_hash,
    repo_structure_hash,
)
from .repo import ARCHITECTURE_FILE, DuplicateIDError, Repo
from .scheduler import Scheduler


def _dirty_guard(git: Git, adopt: bool) -> bool:
    """Stig refuses to run with uncommitted human changes (SPEC §10)."""
    if not git.has_uncommitted_changes():
        return True
    if adopt:
        git.commit("human: adopted uncommitted changes")
        return True
    print(
        "stig: refusing to run with uncommitted changes present.\n"
        "Commit them yourself, or re-run with --adopt to commit them as human changes.",
        file=sys.stderr,
    )
    return False


def _make_scheduler(args, repo: Repo, git: Git) -> Scheduler:
    model = AnthropicModel(model=getattr(args, "model", DEFAULT_MODEL))
    checks = RealChecks(use_venv=not getattr(args, "no_venv", False))
    return Scheduler(
        repo, git, model, checks,
        budget=getattr(args, "budget", 50),
        trust=getattr(args, "trust", False),
    )


def _medium_error(exc: Exception) -> int:
    """A malformed medium is a user-fixable error, not a traceback (SPEC §04)."""
    print(f"stig: {exc}", file=sys.stderr)
    return 1


def cmd_run(args) -> int:
    repo = Repo(args.root)
    git = Git(repo.root)
    if not git.is_repo():
        print("stig: not a git repository", file=sys.stderr)
        return 2
    if not args.dry_run and not _dirty_guard(git, args.adopt):
        return 1
    sched = _make_scheduler(args, repo, git)
    try:
        outcome = sched.run(dry_run=args.dry_run)
    except (DuplicateIDError, GrammarError) as exc:
        return _medium_error(exc)
    print(f"[{outcome.code}] {outcome.report}")
    print(f"activations: {outcome.activations}")
    return outcome.exit_code


def cmd_step(args) -> int:
    repo = Repo(args.root)
    git = Git(repo.root)
    if not git.is_repo():
        print("stig: not a git repository", file=sys.stderr)
        return 2
    if not _dirty_guard(git, args.adopt):
        return 1
    sched = _make_scheduler(args, repo, git)
    try:
        result = sched.step()
    except (DuplicateIDError, GrammarError) as exc:
        return _medium_error(exc)
    if result.outcome == "terminal":
        print(f"[{result.terminal.code}] {result.terminal.report}")
        return result.terminal.exit_code
    print(f"{result.outcome}: {result.kind}({result.active_id}) — {result.detail}")
    return 0


def cmd_status(args) -> int:
    repo = Repo(args.root)
    try:
        annotations = repo.parse_all()
    except GrammarError as exc:
        return _medium_error(exc)
    if not annotations:
        print("no annotations found")
        return 0
    print(f"{'ID':<6} {'KIND':<11} {'STATUS':<12} {'STK':<4} LOCATION")
    print("-" * 60)
    for ann in sorted(annotations, key=lambda a: (a.kind, a.id or "")):
        loc = f"{ann.file}:{ann.start_line + 1}"
        print(f"{ann.id or '-':<6} {ann.kind:<11} {ann.status or '-':<12} "
              f"{ann.strikes:<4} {loc}")
    return 0


def cmd_check(args) -> int:
    """Parse + hash pass only; CI-friendly (SPEC §12)."""
    repo = Repo(args.root)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        repo.check_duplicates()
        annotations = repo.parse_all()
        py_files = repo.python_files()
    except (DuplicateIDError, GrammarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    struct_hash = repo_structure_hash(py_files)
    for ann in annotations:
        if ann.kind == "constraint" and ann.status in ("verified", "enforced"):
            expected = (
                struct_hash if repo.is_repo_scoped(ann)
                else region_hash(ann, repo.read(ann.file))
            )
            if ann.attrs.get("region_hash") != expected:
                errors.append(f"{ann.id}: stale verification (region changed)")
            if ann.status == "enforced":
                enforced_by = ann.attrs.get("enforced_by", "")
                missing = missing_enforcing_tests(enforced_by, py_files)
                if not enforced_by.strip():
                    errors.append(f"{ann.id}: enforced but carries no enforced_by test")
                elif missing:
                    errors.append(
                        f"{ann.id}: enforced_by test(s) not found: {', '.join(missing)}"
                    )
        # Empty-region warning (SPEC §03).
        if ann.kind in ("goal", "constraint") and not repo.is_repo_scoped(ann):
            if repo.exists(ann.file) and not region_has_executable_lines(ann, repo.read(ann.file)):
                warnings.append(f"{ann.id}: region contains zero executable lines")

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        return 1
    print(f"ok: {len(annotations)} annotations, no errors")
    return 0


def cmd_strip(args) -> int:
    """Remove resolved annotations; keep permanent records by default (SPEC §12)."""
    repo = Repo(args.root)
    try:
        annotations = repo.parse_all()
    except GrammarError as exc:
        return _medium_error(exc)
    satisfied_goals = {
        a.id for a in annotations if a.kind == "goal" and a.status == "satisfied"
    }

    archived: list[str] = []
    to_remove: list = []
    for ann in annotations:
        # @decision and @tried are the permanent record (SPEC §03): they are
        # never consumed, and --all does not mean "including those".
        if ann.kind in PERMANENT_KINDS and not (
            args.archive and ann.kind == "tried" and ann.attrs.get("goal") in satisfied_goals
        ):
            continue
        if args.all and ann.kind in ("goal", "unresolved"):
            # --all widens the net to every goal/question, resolved or not.
            to_remove.append(ann)
        elif ann.kind == "goal" and ann.status == "satisfied":
            to_remove.append(ann)
        elif ann.kind == "unresolved" and ann.status == "answered":
            to_remove.append(ann)
        elif args.archive and ann.kind == "tried" and ann.attrs.get("goal") in satisfied_goals:
            # Relocate @tried of satisfied goals into ARCHITECTURE, tagged.
            tag = f"{ann.file}:{ann.start_line + 1}"
            clone = ann.clone()
            clone.attrs["from"] = tag
            archived.append(clone.header_text())
            to_remove.append(ann)

    # Remove bottom-up per file so indices stay valid.
    by_file: dict[str, list] = {}
    for ann in to_remove:
        by_file.setdefault(ann.file, []).append(ann)
    for rel, anns in by_file.items():
        for ann in sorted(anns, key=lambda a: a.start_line, reverse=True):
            repo.delete_range(rel, ann.start_line, ann.end_line)
    if archived:
        repo.append_lines(ARCHITECTURE_FILE, archived)

    print(f"stripped {len(to_remove)} annotation(s); archived {len(archived)}")
    return 0


_PYPROJECT_TEMPLATE = """\
[project]
name = "{package}"
version = "0.1.0"
requires-python = ">=3.10"

[tool.ruff]
line-length = 100
"""

# The guidance is deliberately prose, not example annotations: a line matching
# the grammar here would be a *real* annotation, so a "sample" goal is a goal the
# scheduler mints an ID for and executes on the first run.
#
# The @decision is the exception, and is meant to be one. Scaffolding picks a
# layout, and a layout the handlers cannot see is a trap: they write `<pkg>.py`
# next to the scaffolded `<pkg>/`, the empty package shadows it on import, and
# every downstream goal fails against a module whose contents vanished.
# @decision bodies are the one context item that is never dropped, so recording
# the choice is what makes it binding rather than merely true.
_ARCHITECTURE_TEMPLATE = """\
# Repo-scoped annotations live in this file. Everything Stig does starts here.
#
# Write goals as single-line comments, leaving the ID slot empty — Stig mints
# g01, g02, ... in document order and writes them back:
#
#     hash @goal(, status=open): <what you want built, specifically>
#
# ...where `hash` is a literal # character. Continue a long line with an
# indented `..` comment. Order work with `after=g01`, or `after=g01&g02` for
# several dependencies. Ask a question with @unresolved, record a choice with
# @decision, and attach an @constraint directly above the definition it governs.
#
# Delete this comment block once you have written your first goal.

# @decision(, status=recorded): library code lives in `{package}/` and tests in
#   .. `tests/`. New modules go inside `{package}/` — a top-level `{package}.py`
#   .. would shadow the package on import and silently win.
"""


def _package_name(raw: str) -> str:
    """Turn a directory name into a valid Python package identifier."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"pkg_{cleaned}" if cleaned else "app"
    return cleaned.lower()


def _write_if_absent(
    root: str, rel: str, content: str, created: list[str], skipped: list[str]
) -> None:
    """Scaffolding never clobbers. Re-running init on a real project is safe."""
    path = os.path.join(root, rel)
    if os.path.exists(path):
        skipped.append(rel)
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    created.append(rel)


def _ensure_git_identity(git: Git) -> None:
    """A commit fails outright if no identity is configured anywhere.

    Scaffolding that ends in a failed commit leaves exactly the dirty tree the
    scheduler refuses to run against, so set a repo-local fallback rather than
    letting init dead-end.
    """
    for key, fallback in (("user.email", "stig@example.com"), ("user.name", "Stig")):
        if not git.config_get(key):
            git.config_set(key, fallback)


def cmd_init(args) -> int:
    """Scaffold a Stig-ready project: git repo, manifest, annotation file, commit."""
    root = os.path.abspath(os.path.join(args.root, args.name) if args.name else args.root)
    os.makedirs(root, exist_ok=True)

    package = _package_name(args.package or os.path.basename(root))
    created: list[str] = []
    skipped: list[str] = []

    git = Git(root)
    if not git.is_repo():
        init_repo(root)
        created.append("git repository")
    _ensure_git_identity(git)

    existing_manifest = next(
        (m for m in MANIFEST_FILES if os.path.exists(os.path.join(root, m))), None
    )
    if existing_manifest:
        skipped.append(existing_manifest)
    else:
        _write_if_absent(root, "pyproject.toml",
                         _PYPROJECT_TEMPLATE.format(package=package), created, skipped)

    _write_if_absent(root, ARCHITECTURE_FILE,
                     _ARCHITECTURE_TEMPLATE.format(package=package), created, skipped)
    _write_if_absent(root, f"{package}/__init__.py", "", created, skipped)
    _write_if_absent(root, "tests/__init__.py", "", created, skipped)

    if args.seed:
        rc = cmd_seed(argparse.Namespace(root=root, model=args.model, prompt=args.seed))
        if rc != 0:
            return rc

    print(f"initialized {root}")
    print(f"  package: {package}/")
    if created:
        print(f"  created: {', '.join(created)}")
    if skipped:
        print(f"  kept existing: {', '.join(skipped)}")

    if args.no_commit:
        print("\nnot committed (--no-commit). Stig refuses to run against a dirty tree.")
    elif git.has_uncommitted_changes():
        git.commit("stig init: scaffold project")
        print("  committed: stig init: scaffold project")

    if not args.seed:
        print(f"\nNext: write a goal in {ARCHITECTURE_FILE}, commit, then `stig run`.")
    else:
        print("\nNext: review the seeded goals, commit any edits, then `stig run`.")
    return 0


def cmd_seed(args) -> int:
    """Sugar: one model call that writes initial @goal annotations (SPEC §12)."""
    repo = Repo(args.root)
    model = AnthropicModel(model=getattr(args, "model", DEFAULT_MODEL))
    system = (
        "You are Stig's seed command. Given a prompt describing a project, emit "
        "initial `# @goal(, status=open): <text>` annotation lines (one per line, "
        "IDs omitted — the scheduler mints them). Emit only the annotation lines."
    )
    text = model.complete(system, args.prompt)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("# @goal")]
    if not lines:
        print("stig: seed produced no @goal annotations", file=sys.stderr)
        return 1
    header = [f"# Seeded from: {args.prompt[:80]}"]
    repo.append_lines(ARCHITECTURE_FILE, header + lines)
    print(f"seeded {len(lines)} goal(s) into {ARCHITECTURE_FILE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stig", description=__doc__)
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model id for handlers")
    parser.add_argument("--no-venv", action="store_true", help="run checks in the current env")
    sub = parser.add_subparsers(dest="command", required=True)

    _TRUST_HELP = "skip the check suite; accept the model's diff without pytest/ruff"
    _ADOPT_HELP = "commit pre-existing uncommitted changes as human changes first"

    p_run = sub.add_parser("run", help="loop to fixpoint, blocked, or budget")
    p_run.add_argument("--budget", type=int, default=50, help="max activations (default: 50)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="report what would activate; write nothing")
    p_run.add_argument("--trust", action="store_true", help=_TRUST_HELP)
    p_run.add_argument("--adopt", action="store_true", help=_ADOPT_HELP)
    p_run.set_defaults(func=cmd_run)

    p_step = sub.add_parser("step", help="exactly one activation")
    p_step.add_argument("--trust", action="store_true", help=_TRUST_HELP)
    p_step.add_argument("--adopt", action="store_true", help=_ADOPT_HELP)
    p_step.set_defaults(func=cmd_step)

    p_status = sub.add_parser("status", help="table of every annotation")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="parse + hash pass only; CI-friendly")
    p_check.set_defaults(func=cmd_check)

    p_strip = sub.add_parser("strip", help="remove resolved annotations")
    p_strip.add_argument("--all", action="store_true")
    p_strip.add_argument("--archive", action="store_true")
    p_strip.set_defaults(func=cmd_strip)

    p_init = sub.add_parser("init", help="scaffold a Stig-ready project")
    p_init.add_argument("name", nargs="?", help="directory to create (default: --root)")
    p_init.add_argument("--package", help="package dir name (default: derived from directory)")
    p_init.add_argument("--seed", help="also seed initial @goal annotations from a prompt")
    p_init.add_argument("--no-commit", action="store_true",
                        help="scaffold but leave the tree uncommitted")
    p_init.set_defaults(func=cmd_init)

    p_seed = sub.add_parser("seed", help="write initial @goal annotations from a prompt")
    p_seed.add_argument("prompt")
    p_seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GitError as exc:
        print(f"stig: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
