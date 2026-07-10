"""The CLI surface (SPEC §12).

    stig run [--budget N] [--dry-run] [--trust] [--adopt]
    stig step
    stig status
    stig check
    stig strip [--all] [--archive]
    stig seed "<prompt>"

stig step and stig run produce identical per-activation behavior; run is just
step in a loop. This keeps the single-step path honest and debuggable.
"""

from __future__ import annotations

import argparse
import sys

from .annotations import KIND_PREFIX
from .checks import RealChecks
from .gitutil import Git, init_repo
from .models import DEFAULT_MODEL, AnthropicModel
from .regions import region_hash, region_has_executable_lines, repo_structure_hash
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


def cmd_run(args) -> int:
    repo = Repo(args.root)
    git = Git(repo.root)
    if not git.is_repo():
        print("stig: not a git repository", file=sys.stderr)
        return 2
    if not args.dry_run and not _dirty_guard(git, args.adopt):
        return 1
    sched = _make_scheduler(args, repo, git)
    outcome = sched.run(dry_run=args.dry_run)
    print(f"[{outcome.code}] {outcome.report}")
    print(f"activations: {outcome.activations}")
    return outcome.exit_code


def cmd_step(args) -> int:
    repo = Repo(args.root)
    git = Git(repo.root)
    if not _dirty_guard(git, args.adopt):
        return 1
    sched = _make_scheduler(args, repo, git)
    result = sched.step()
    if result.outcome == "terminal":
        print(f"[{result.terminal.code}] {result.terminal.report}")
        return result.terminal.exit_code
    print(f"{result.outcome}: {result.kind}({result.active_id}) — {result.detail}")
    return 0


def cmd_status(args) -> int:
    repo = Repo(args.root)
    annotations = repo.parse_all()
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
    except DuplicateIDError as exc:
        errors.append(str(exc))

    annotations = repo.parse_all()
    py_files = repo.python_files()
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
                name = enforced_by.split("::")[-1]
                exists = any(f"def {name}" in t for t in py_files.values()) if name else False
                if not exists:
                    errors.append(f"{ann.id}: enforced_by test {enforced_by!r} not found")
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
    annotations = repo.parse_all()
    satisfied_goals = {
        a.id for a in annotations if a.kind == "goal" and a.status == "satisfied"
    }

    archived: list[str] = []
    to_remove: list = []
    for ann in annotations:
        if args.all:
            if ann.kind in ("goal", "unresolved", "decision", "tried"):
                to_remove.append(ann)
            continue
        if ann.kind == "goal" and ann.status == "satisfied":
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


def cmd_seed(args) -> int:
    """Sugar: one model call that writes initial @goal annotations (SPEC §12)."""
    repo = Repo(args.root)
    if not repo.exists("."):
        pass
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
    from . import __version__

    parser = argparse.ArgumentParser(prog="stig", description=__doc__)
    parser.add_argument("--version", action="version", version=f"stig {__version__}")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model id for handlers")
    parser.add_argument("--no-venv", action="store_true", help="run checks in the current env")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="loop to fixpoint, blocked, or budget")
    p_run.add_argument("--budget", type=int, default=50)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--trust", action="store_true")
    p_run.add_argument("--adopt", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_step = sub.add_parser("step", help="exactly one activation")
    p_step.add_argument("--trust", action="store_true")
    p_step.add_argument("--adopt", action="store_true")
    p_step.set_defaults(func=cmd_step)

    p_status = sub.add_parser("status", help="table of every annotation")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="parse + hash pass only; CI-friendly")
    p_check.set_defaults(func=cmd_check)

    p_strip = sub.add_parser("strip", help="remove resolved annotations")
    p_strip.add_argument("--all", action="store_true")
    p_strip.add_argument("--archive", action="store_true")
    p_strip.set_defaults(func=cmd_strip)

    p_seed = sub.add_parser("seed", help="write initial @goal annotations from a prompt")
    p_seed.add_argument("prompt")
    p_seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _ = (KIND_PREFIX, init_repo)  # referenced for completeness of the module surface
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
