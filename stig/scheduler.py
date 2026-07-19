"""The scheduler (SPEC §06). A deliberately dumb loop with no memory between
iterations beyond the repository itself: parse, pick, dispatch, apply, commit,
repeat. It terminates at fixpoint — when nothing is actionable and nothing
remains open, stuck, or waiting on a human.

Intelligence about *what* to do lives in annotations. Intelligence about *how*
to do it lives in the frozen model. The scheduler contains neither.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .annotations import Annotation, status_is_valid
from .checks import Checks
from .context import ContextBuilder
from .diffutil import AnnotationTouchError, assert_no_annotation_lines, diff_hash
from .gitutil import Git
from .handlers import HANDLERS, HandlerParseError, HandlerResult, NewAnnotation
from .models import Model
from .patcher import PatchError, apply_diff
from .regions import enforcing_test_exists, region_hash, repo_structure_hash, resolve_region
from .repo import ARCHITECTURE_FILE, Repo

STRIKE_CAP = 3
DEFAULT_BUDGET = 50

# Where an annotation lands when it hits the strike cap (SPEC §11). Every
# actionable kind needs one: a kind with no cap status would accrue strikes
# forever and keep the loop from ever reaching fixpoint or blocked.
_CAP_STATUS: dict[str, tuple[str, str]] = {
    # kind: (status it must currently hold, status it is driven to)
    "goal": ("open", "stuck"),
    "constraint": ("asserted", "violated"),
    "unresolved": ("open", "needs-human"),
}

# Terminal success statuses used for dependency satisfaction (SPEC §06).
_DEP_SATISFIED = {
    "goal": {"satisfied"},
    "constraint": {"verified", "enforced"},
    "unresolved": {"answered"},
}


@dataclass
class StepResult:
    outcome: str  # "activated", "failed", "retry", "terminal"
    active_id: str | None = None
    kind: str | None = None
    detail: str = ""
    commit: str | None = None
    terminal: "Outcome | None" = None


@dataclass
class Outcome:
    code: str  # "fixpoint", "blocked", "budget", "dry-run"
    report: str
    activations: int = 0

    @property
    def exit_code(self) -> int:
        return 0 if self.code in ("fixpoint", "dry-run") else 1


def _id_key(ann_id: str | None):
    if not ann_id:
        return ("", 0)
    m = re.match(r"([a-z]+)(\d+)", ann_id)
    if m:
        return (m.group(1), int(m.group(2)))
    return (ann_id, 0)


class Scheduler:
    def __init__(
        self,
        repo: Repo,
        git: Git,
        model: Model,
        checks: Checks,
        *,
        budget: int = DEFAULT_BUDGET,
        strike_cap: int = STRIKE_CAP,
        trust: bool = False,
        context_builder: ContextBuilder | None = None,
        logger=None,
    ):
        self.repo = repo
        self.git = git
        self.model = model
        self.checks = checks
        self.budget = budget
        self.strike_cap = strike_cap
        self.trust = trust
        self.ctx = context_builder or ContextBuilder(repo)
        self.log = logger or (lambda *a, **k: None)
        self._strike_resets: set[str] = set()

    # -- top of loop: parse, mint ids, demote stale (SPEC §06) ----------------

    def _prepare(self, *, write: bool = True) -> list[Annotation]:
        """Parse, mint IDs, demote stale verifications.

        With ``write=False`` the same normalization happens purely in memory:
        ``--dry-run`` must be able to report what would activate without
        leaving a single byte changed in the repository.
        """
        if not write:
            annotations = self.repo.parse_all()
            self.repo.check_duplicates()
            self._assign_missing_ids_in_memory(annotations)
            self._reset_reopened_strikes(annotations, write=False)
            self._demote_stale(annotations, write=False)
            return annotations
        self.repo.assign_missing_ids()
        self.repo.check_duplicates()
        annotations = self.repo.parse_all()
        self._reset_reopened_strikes(annotations)
        self._demote_stale(annotations)
        return self.repo.parse_all()

    def _assign_missing_ids_in_memory(self, annotations: list[Annotation]) -> None:
        counters = self.repo._next_counters(annotations)  # noqa: SLF001 - internal helper
        from .annotations import KIND_PREFIX

        for ann in annotations:
            if ann.id is None:
                counters[ann.kind] += 1
                ann.id = f"{KIND_PREFIX[ann.kind]}{counters[ann.kind]:02d}"

    def _reset_reopened_strikes(self, annotations: list[Annotation], *, write: bool = True) -> None:
        """Human reopened a capped annotation (SPEC §11): an actionable status
        combined with strikes-at-cap implies a human edit — the scheduler itself
        drives a capped annotation *out* of its actionable status. Reset
        strikes=0 and keep the @tried history."""
        for ann in annotations:
            actionable_status = _CAP_STATUS.get(ann.kind, (None, None))[0]
            if ann.status == actionable_status and ann.strikes >= self.strike_cap:
                ann.strikes = 0
                if write:
                    self.repo.set_header(ann)
                self._strike_resets.add(ann.id or "")

    def _demote_stale(self, annotations: list[Annotation], *, write: bool = True) -> None:
        """Demote verified/enforced constraints whose region changed or whose
        enforcing test disappeared (SPEC §09)."""
        py_files = self.repo.python_files()
        struct_hash = repo_structure_hash(py_files)
        for ann in annotations:
            if ann.kind != "constraint" or ann.status not in ("verified", "enforced"):
                continue
            repo_scoped = self.repo.is_repo_scoped(ann)
            expected = struct_hash if repo_scoped else region_hash(ann, self.repo.read(ann.file))
            stored = ann.attrs.get("region_hash")
            demote = stored is None or stored != expected
            if ann.status == "enforced":
                enforced_by = ann.attrs.get("enforced_by")
                if not enforced_by or not enforcing_test_exists(enforced_by, py_files):
                    demote = True
            if demote:
                ann.status = "asserted"
                if write:
                    self.repo.set_header(ann)

    # -- actionable set, gating, priority (SPEC §06) --------------------------

    def _actionable(self, annotations: list[Annotation]) -> list[Annotation]:
        index = {a.id: a for a in annotations if a.id}
        out: list[Annotation] = []
        for ann in annotations:
            if ann.kind == "unresolved" and ann.status == "open":
                out.append(ann)
            elif ann.kind == "constraint" and ann.status == "asserted":
                out.append(ann)
            elif ann.kind == "goal" and ann.status == "open":
                if (
                    self._deps_met(ann, index)
                    and not self._dep_blocked(ann, index)
                    and not self._gated(ann, annotations)
                ):
                    out.append(ann)
        return out

    def _deps_met(self, goal: Annotation, index: dict[str, Annotation]) -> bool:
        for dep in self._after(goal):
            target = index.get(dep)
            if target is None:
                return False
            if target.status not in _DEP_SATISFIED.get(target.kind, set()):
                return False
        return True

    def _dep_blocked(self, goal: Annotation, index, _seen=None) -> bool:
        """The dependency chain contains a stuck goal or needs-human (SPEC §06)."""
        seen = _seen or set()
        for dep in self._after(goal):
            if dep in seen:
                continue
            seen.add(dep)
            target = index.get(dep)
            if target is None:
                continue
            if target.kind == "goal" and target.status == "stuck":
                return True
            if target.kind == "unresolved" and target.status == "needs-human":
                return True
            if target.kind == "goal" and target.status == "open":
                if self._dep_blocked(target, index, seen):
                    return True
        return False

    def _gated(self, goal: Annotation, annotations: list[Annotation]) -> bool:
        """A needs-human question governing an overlapping region, or repo-scoped,
        gates the work it shapes (SPEC §06)."""
        goal_region = None
        for ann in annotations:
            if ann.kind != "unresolved" or ann.status != "needs-human":
                continue
            if self.repo.is_repo_scoped(ann):
                return True
            if ann.file != goal.file:
                continue
            if goal_region is None:
                goal_region = resolve_region(goal, self.repo.read(goal.file))
            q_region = resolve_region(ann, self.repo.read(ann.file))
            if q_region.overlaps(goal_region):
                return True
        return False

    @staticmethod
    def _after(goal: Annotation) -> list[str]:
        raw = goal.attrs.get("after", "")
        return [d.strip() for d in raw.split("&") if d.strip()] if raw else []

    def _pick(self, actionable: list[Annotation]) -> Annotation:
        """Fixed priority rule (SPEC §06). Ties break by ID order."""
        touched = self.git.changed_files_in_head()

        def tier(ann: Annotation) -> int:
            if ann.kind == "unresolved":
                return 0  # (1) questions gate everything
            if ann.kind == "constraint":
                is_touched = ann.file in touched or (
                    self.repo.is_repo_scoped(ann) and bool(touched)
                )
                return 1 if is_touched else 3  # (2) freshly-changed constraints, then (4)
            if ann.kind == "goal":
                return 2  # (3) open goals with deps met, in ID order
            return 4

        return sorted(actionable, key=lambda a: (tier(a), _id_key(a.id)))[0]

    # -- termination (SPEC §06) ----------------------------------------------

    def _is_pending(self, ann: Annotation) -> bool:
        if ann.kind == "goal":
            return ann.status in ("open", "stuck")
        if ann.kind == "unresolved":
            return ann.status in ("open", "needs-human")
        if ann.kind == "constraint":
            # `violated` is terminal for the scheduler but not for the project:
            # an unrepaired invariant is outstanding work, so it blocks rather
            # than letting the run report fixpoint.
            return ann.status in ("asserted", "violated")
        return False

    def _terminal(self, annotations: list[Annotation], activations: int) -> Outcome:
        pending = [a for a in annotations if self._is_pending(a)]
        if not pending:
            return Outcome("fixpoint", "reached fixpoint — nothing actionable or open", activations)
        lines = ["blocked — no actionable annotation, but work remains:"]
        for ann in sorted(pending, key=lambda a: _id_key(a.id)):
            reason = {
                ("goal", "stuck"): "stuck (strike cap reached)",
                ("goal", "open"): "open but gated or blocked by a dependency",
                ("unresolved", "needs-human"): "needs a human answer",
                ("unresolved", "open"): "open question",
                ("constraint", "asserted"): "asserted, unverified",
                ("constraint", "violated"): "violated — needs a repair",
            }.get((ann.kind, ann.status), ann.status or "")
            lines.append(f"  {ann.id} [{ann.file}] {ann.kind}: {reason} — {ann.full_body}")
        return Outcome("blocked", "\n".join(lines), activations)

    # -- one activation (SPEC §06, §07, §10) ---------------------------------

    def step(self) -> StepResult:
        annotations = self._prepare()
        actionable = self._actionable(annotations)
        if not actionable:
            return StepResult(outcome="terminal", terminal=self._terminal(annotations, 0))
        active = self._pick(actionable)
        return self._activate(active, annotations)

    def run(self, dry_run: bool = False) -> Outcome:
        if dry_run:
            # Read-only: parse and pick, write nothing (SPEC §12).
            annotations = self._prepare(write=False)
            actionable = self._actionable(annotations)
            if not actionable:
                return self._terminal(annotations, 0)
            active = self._pick(actionable)
            return Outcome(
                "dry-run",
                f"would activate {active.kind}({active.id}): {active.full_body}",
                0,
            )
        activations = 0
        while True:
            annotations = self._prepare()
            actionable = self._actionable(annotations)
            if not actionable:
                return self._terminal(annotations, activations)
            active = self._pick(actionable)
            if activations >= self.budget:
                return Outcome("budget", f"activation budget of {self.budget} exhausted", activations)
            self._activate(active, annotations)
            activations += 1

    def _activate(self, active: Annotation, annotations: list[Annotation]) -> StepResult:
        ctx = self.ctx.build(active, annotations)
        # A malformed model response is an ordinary failed activation: it takes a
        # strike and is recorded in the medium (SPEC §11). It must never abort the
        # loop — a crash here would leave the repo mid-activation with no record.
        try:
            result = HANDLERS[active.kind](active, ctx, self.model)
        except HandlerParseError as exc:
            return self._fail(active, "", f"malformed model response: {exc}")

        # Co-editing race (SPEC §06): a snapshot mismatch when the handler returns
        # discards the activation without a strike, without a commit, retry on
        # fresh state — the model reasoned about a state that no longer exists.
        if self.ctx.disk_hashes(ctx.snapshot) != ctx.snapshot:
            return StepResult(outcome="retry", active_id=active.id, kind=active.kind,
                              detail="context changed during model call; discarded")

        # A whitespace-only diff is no diff: `apply_diff` no-ops on it, so
        # treating it as truthy would let it pass as progress.
        diff = result.diff if (result.diff or "").strip() else ""
        try:
            if diff:
                assert_no_annotation_lines(diff)
        except AnnotationTouchError as exc:
            return self._fail(active, diff, f"diff touched annotation lines: {exc}")

        # Oscillation (SPEC §11): a diff hash matching any @tried for this
        # annotation is a strike without applying or re-calling the model.
        if diff:
            h = diff_hash(diff)
            if h in self._tried_hashes(active, annotations):
                return self._fail(active, diff, "oscillation: diff repeats a prior @tried")

        if diff:
            try:
                apply_diff(self.repo, diff)
            except PatchError as exc:
                return self._fail(active, diff, f"diff won't apply: {exc}")

        # --trust skips the check suite (SPEC §12): the operator accepts the
        # model's output without pytest/ruff arbitration. Every other gate —
        # the annotation-line guard, oscillation, the patch itself — still runs.
        if not self.trust:
            check = self.checks.run(self.repo.root)
            if not check.ok:
                return self._fail(active, diff, f"checks failed: {check.output.strip()[:400]}")

        return self._apply_success(active, result, diff)

    # -- success path --------------------------------------------------------

    def _apply_success(self, active: Annotation, result: HandlerResult, diff: str) -> StepResult:
        # Re-parse after the code diff shifted line numbers.
        annotations = self.repo.parse_all()
        index = {a.id: a for a in annotations if a.id}
        transitions: list[str] = []
        mutated = False  # any accepted write to an annotation header

        for upd in result.updates:
            target = index.get(upd.id)
            if target is None:
                continue
            old_status = target.status
            status_changed = (
                upd.status is not None
                and upd.status != old_status
                and status_is_valid(target.kind, upd.status)
            )
            if status_changed:
                target.status = upd.status
                transitions.append(f"{target.id} {old_status} → {upd.status}")
            if upd.body and upd.body != target.body.strip():
                # The body is where an @unresolved answer lives; without this the
                # answer the handler produced would be discarded (SPEC §05, §07).
                target.body = upd.body
                mutated = True
            # Only a real change counts as progress: re-asserting a value the
            # annotation already holds must not buy another activation, or the
            # no-progress strike below never fires.
            for key, value in upd.set_attrs.items():
                if value == "":
                    if target.attrs.pop(key, None) is not None:
                        mutated = True
                elif target.attrs.get(key) != value:
                    target.attrs[key] = value
                    mutated = True
            # Verification is a claim about a specific version (SPEC §09): stamp
            # region_hash only on the transition *into* verified/enforced — not on
            # an unrelated attr update (e.g. a provisional enforced_by), so a later
            # structural change can still demote the constraint (graduation relay).
            if (
                target.kind == "constraint"
                and status_changed
                and upd.status in ("verified", "enforced")
            ):
                target.attrs["region_hash"] = self._constraint_hash(target)
            self.repo.set_header(target)

        spawned = self._insert_new_annotations(active, result.new_annotations, annotations)

        # Termination guarantee (SPEC §06): an activation that changed nothing —
        # no diff, no accepted status transition, no body, no spawned annotation —
        # leaves the annotation actionable in an identical repo, so the next
        # iteration picks it again forever. Treat no progress as a failure so the
        # strike cap eventually drives it out of the actionable set.
        if not diff and not transitions and not spawned and not mutated:
            return self._fail(active, "", "handler produced no diff and no status change")

        commit = self._commit(active, "activated", transitions, spawned)
        detail = ", ".join(transitions) or "no status change"
        return StepResult("activated", active.id, active.kind, detail, commit)

    def _constraint_hash(self, ann: Annotation) -> str:
        if self.repo.is_repo_scoped(ann):
            return repo_structure_hash(self.repo.python_files())
        return region_hash(ann, self.repo.read(ann.file))

    def _insert_new_annotations(
        self, active: Annotation, new_annos: list[NewAnnotation], annotations: list[Annotation]
    ) -> list[str]:
        if not new_annos:
            return []
        # The scheduler is the sole minting authority (SPEC §04).
        counters: dict[str, int] = {}
        base = self.repo._next_counters(annotations)  # noqa: SLF001 - internal helper
        spawned: list[str] = []
        rendered: list[str] = []
        from .annotations import DEFAULT_STATUS, KIND_PREFIX

        for na in new_annos:
            if na.kind not in KIND_PREFIX:  # ignore an unknown proposed kind
                continue
            counters.setdefault(na.kind, base.get(na.kind, 0))
            counters[na.kind] += 1
            new_id = f"{KIND_PREFIX[na.kind]}{counters[na.kind]:02d}"
            # Normalize an out-of-vocabulary status to the kind's default so the
            # medium stays self-describing (SPEC §04, §05).
            status = na.status if status_is_valid(na.kind, na.status) else DEFAULT_STATUS[na.kind]
            attrs = {"status": status, **na.attrs}
            ann = Annotation(
                kind=na.kind, id=new_id, attrs=attrs, body=na.body,
                file=active.file, start_line=0, end_line=0, indent=active.indent,
            )
            rendered.append(ann.header_text())
            spawned.append(f"{new_id} ({status})")
        # Place spawned annotations with the annotation that generated the work.
        fresh = {a.id: a for a in self.repo.parse_all() if a.id}
        anchor = fresh.get(active.id)
        if anchor is not None and active.file != ARCHITECTURE_FILE:
            self.repo.insert_lines_before(active.file, anchor.start_line, rendered)
        else:
            self.repo.append_lines(ARCHITECTURE_FILE, rendered)
        return spawned

    # -- failure path (SPEC §10, §11) ----------------------------------------

    def _tried_hashes(self, active: Annotation, annotations: list[Annotation]) -> set[str]:
        return {
            a.attrs.get("diff_hash", "")
            for a in annotations
            if a.kind == "tried" and a.attrs.get("goal") == active.id
        }

    def _fail(self, active: Annotation, diff: str, reason: str) -> StepResult:
        # A failed activation reverts all code changes, then records the failure
        # and commits that as the activation's single commit (SPEC §10).
        self.git.revert_worktree()
        # The revert discards everything `_prepare` wrote this iteration — the
        # minted IDs, the staleness demotions, the human-reopen strike resets —
        # because none of it was committed. Re-run the whole normalization, not
        # just minting: without the demotion a stale constraint is back at
        # `verified` and never matches its cap status, so it accrues strikes
        # forever; without the reset a reopened goal goes straight back to stuck
        # on its first retry instead of getting a fresh set of attempts.
        # `_prepare` is deterministic, so it reproduces exactly what was lost.
        annotations = self._prepare()
        fresh = {a.id: a for a in annotations if a.id}
        target = fresh.get(active.id)
        if target is None:
            # The annotation itself is gone (a human deleted it mid-activation).
            # Record the failure in history; there is nothing left to strike.
            commit = self._commit(active, "failed", [], [], reason=reason)
            return StepResult("failed", active.id, active.kind, reason, commit)

        target.strikes = target.strikes + 1
        transitions: list[str] = [f"{target.id} strike {target.strikes}/{self.strike_cap}"]
        actionable_status, capped_status = _CAP_STATUS.get(target.kind, (None, None))
        if (
            capped_status
            and target.strikes >= self.strike_cap
            and target.status == actionable_status
        ):
            target.status = capped_status
            transitions.append(f"{target.id} {actionable_status} → {capped_status}")
        self.repo.set_header(target)

        spawned: list[str] = []
        h = diff_hash(diff) if diff else ""
        existing = self._tried_hashes(target, annotations)
        if h and h not in existing:
            attrs = {"status": "recorded", "goal": target.id or "", "diff_hash": h}
            new_id = self.repo.next_id_for("tried", self.repo.parse_all())
            tried = Annotation(
                kind="tried", id=new_id, attrs=attrs, body=reason[:200],
                file=target.file, start_line=0, end_line=0, indent=target.indent,
            )
            anchor = {a.id: a for a in self.repo.parse_all() if a.id}.get(target.id)
            idx = anchor.start_line if anchor else 0
            self.repo.insert_lines_before(target.file, idx, [tried.header_text()])
            spawned.append(f"{new_id} (recorded)")

        commit = self._commit(target, "failed", transitions, spawned, reason=reason)
        return StepResult("failed", target.id, target.kind, reason, commit)

    # -- git (SPEC §10): one activation = one commit -------------------------

    def _commit(
        self,
        active: Annotation,
        outcome: str,
        transitions: list[str],
        spawned: list[str],
        reason: str = "",
    ) -> str:
        activation_no = self.git.activation_count() + 1
        subject = f"stig({active.id}): {active.full_body[:60]}"
        lines = [subject, "", f"activation: {activation_no}", f"handler: {active.kind}"]
        for t in transitions:
            lines.append(f"status: {t}")
        for s in spawned:
            lines.append(f"spawned: {s}")
        if active.id in self._strike_resets:
            lines.append("strikes: reset (human reopen)")
            self._strike_resets.discard(active.id)
        if outcome == "failed" and reason:
            lines.append(f"failed: {reason[:120]}")
        model_id = getattr(self.model, "model", "scripted")
        lines.append(f"model: {model_id}")
        return self.git.commit("\n".join(lines))
