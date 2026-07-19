"""Machinery-level coverage of the scheduler, including the acceptance-test
scenarios (SPEC §13) exercised with a scripted model and stub checks."""

from __future__ import annotations

from stig.checks import StubChecks
from stig.models import ScriptedModel
from stig.scheduler import Scheduler

from conftest import git_diff, handler_json, new_file_diff


def make_sched(workrepo, responses, checks=None, **kw):
    repo, git = workrepo
    model = ScriptedModel(responses)
    return Scheduler(repo, git, model, checks or StubChecks(ok=True), **kw), repo, git


def annotation(repo, ann_id):
    return next((a for a in repo.parse_all() if a.id == ann_id), None)


# -- happy paths ------------------------------------------------------------

def test_goal_activation_writes_code_and_satisfies(workrepo):
    repo, git = workrepo
    src = "# @goal(g01, status=open): implement add\ndef add(a, b):\n    return None\n"
    repo.write("calc.py", src)
    git.commit("human: seed")
    new_src = src.replace("    return None", "    return a + b")
    diff = git_diff(repo, "calc.py", new_src)
    resp = handler_json(diff=diff, updates=[{"id": "g01", "status": "satisfied"}])

    sched, repo, git = make_sched(workrepo, [resp])
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    assert annotation(repo, "g01").status == "satisfied"
    assert "return a + b" in repo.read("calc.py")


def test_constraint_verification_stamps_region_hash(workrepo):
    repo, git = workrepo
    repo.write(
        "db.py",
        "# @constraint(c01, status=asserted): fetch returns a list\n"
        "def fetch():\n    return []\n",
    )
    git.commit("human: seed")
    resp = handler_json(updates=[{"id": "c01", "status": "verified"}])

    sched, repo, git = make_sched(workrepo, [resp])
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    c01 = annotation(repo, "c01")
    assert c01.status == "verified"
    assert "region_hash" in c01.attrs


def test_unresolved_answered(workrepo):
    repo, git = workrepo
    repo.write("q.py", "# @unresolved(u01, status=open): which db?\nx = 1\n")
    git.commit("human: seed")
    resp = handler_json(updates=[{"id": "u01", "status": "answered"}])

    sched, repo, git = make_sched(workrepo, [resp])
    outcome = sched.run()
    assert outcome.code == "fixpoint"
    assert annotation(repo, "u01").status == "answered"


# -- staleness (SPEC §09) ---------------------------------------------------

def test_region_change_demotes_verified_constraint(workrepo):
    repo, git = workrepo
    repo.write(
        "db.py",
        "# @constraint(c01, status=asserted): fetch returns a list\n"
        "def fetch():\n    return []\n",
    )
    git.commit("human: seed")
    sched, repo, git = make_sched(
        workrepo, [handler_json(updates=[{"id": "c01", "status": "verified"}])]
    )
    sched.step()
    assert annotation(repo, "c01").status == "verified"

    # A human edits the governed region.
    repo.write(
        "db.py",
        "# @constraint(c01, status=verified, region_hash="
        + annotation(repo, "c01").attrs["region_hash"]
        + "): fetch returns a list\n"
        "def fetch():\n    return [1, 2, 3]\n",
    )
    git.commit("human: edit region")

    annotations = sched._prepare()
    assert next(a for a in annotations if a.id == "c01").status == "asserted"


# -- failure handling (SPEC §11) --------------------------------------------

def test_stuck_after_strike_cap_with_distinct_tries(workrepo):
    repo, git = workrepo
    stub = "# @goal(g01, status=open): impossible\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    # Three DISTINCT diffs so each records a @tried with a distinct diff_hash.
    diffs = [
        git_diff(repo, "m.py", stub.replace("return 0", f"return {n}"))
        for n in (1, 2, 3)
    ]
    responses = [handler_json(diff=d, updates=[{"id": "g01", "status": "satisfied"}]) for d in diffs]

    sched, repo, git = make_sched(workrepo, responses, checks=StubChecks(ok=False))
    outcome = sched.run()

    assert outcome.code == "blocked"
    g01 = annotation(repo, "g01")
    assert g01.status == "stuck"
    assert g01.strikes == 3
    tried = [a for a in repo.parse_all() if a.kind == "tried" and a.attrs.get("goal") == "g01"]
    hashes = {t.attrs.get("diff_hash") for t in tried}
    assert len(hashes) == 3  # three distinct diff_hash values


def test_oscillation_counts_as_strike_without_apply(workrepo):
    repo, git = workrepo
    stub = "# @goal(g01, status=open): loops\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    diff = git_diff(repo, "m.py", stub.replace("return 0", "return 1"))
    # Same diff every time; checks fail the first, oscillation catches the rest.
    responses = [handler_json(diff=diff, updates=[]) for _ in range(3)]

    sched, repo, git = make_sched(workrepo, responses, checks=StubChecks(ok=False))
    outcome = sched.run()

    assert outcome.code == "blocked"
    assert annotation(repo, "g01").status == "stuck"
    tried = [a for a in repo.parse_all() if a.kind == "tried" and a.attrs.get("goal") == "g01"]
    assert len(tried) == 1  # oscillation dedupes @tried by diff_hash


def test_strike_reset_on_human_reopen(workrepo):
    repo, git = workrepo
    repo.write(
        "m.py",
        "# @goal(g01, status=open, strikes=3): reopened by hand\ndef f():\n    return 0\n",
    )
    git.commit("human: reopen")
    sched, repo, git = make_sched(workrepo, [])
    sched._prepare()
    assert annotation(repo, "g01").strikes == 0


# -- co-editing race (SPEC §06) ---------------------------------------------

def test_inflight_edit_discards_without_strike(workrepo):
    repo, git = workrepo
    stub = "# @goal(g01, status=open): implement\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    diff = git_diff(repo, "m.py", stub.replace("return 0", "return 1"))

    class RaceModel(ScriptedModel):
        def complete(self, system, user):
            # A human edits the file during the model call.
            repo.write("m.py", stub.replace("return 0", "return 99"))
            return super().complete(system, user)

    model = RaceModel([handler_json(diff=diff, updates=[{"id": "g01", "status": "satisfied"}])])
    sched = Scheduler(repo, git, model, StubChecks(ok=True))
    result = sched.step()

    assert result.outcome == "retry"
    assert annotation(repo, "g01").status == "open"
    assert annotation(repo, "g01").strikes == 0


# -- gating (SPEC §06) ------------------------------------------------------

def test_needs_human_gates_overlapping_goal(workrepo):
    repo, git = workrepo
    repo.write(
        "m.py",
        "# @unresolved(u01, status=needs-human): design decision needed\n"
        "# @goal(g01, status=open): build on it\n"
        "def f():\n    return 0\n",
    )
    git.commit("human: seed")
    sched, repo, git = make_sched(workrepo, [])
    outcome = sched.run()

    assert outcome.code == "blocked"
    assert annotation(repo, "g01").status == "open"  # never activated


def test_after_dependency_blocks_until_satisfied(workrepo):
    repo, git = workrepo
    repo.write(
        "m.py",
        "# @goal(g01, status=open): first\n"
        "def a():\n    return 0\n\n"
        "# @goal(g02, status=open, after=g01): second\n"
        "def b():\n    return 0\n",
    )
    git.commit("human: seed")
    # Only g01 is actionable first; satisfy it, then g02 becomes actionable.
    r1 = handler_json(updates=[{"id": "g01", "status": "satisfied"}])
    r2 = handler_json(updates=[{"id": "g02", "status": "satisfied"}])
    sched, repo, git = make_sched(workrepo, [r1, r2])
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    assert annotation(repo, "g01").status == "satisfied"
    assert annotation(repo, "g02").status == "satisfied"


# -- graduation relay (SPEC §07, §09) ---------------------------------------

def test_graduation_relay_verifier_to_enforced(workrepo):
    repo, git = workrepo
    repo.write("app.py", "import os\n\ndef run():\n    return os.getcwd()\n")
    repo.write(
        "ARCHITECTURE.anno",
        "# @constraint(c01, status=asserted): stdlib only\n",
    )
    git.commit("human: seed")

    # A: verifier verifies the repo-scoped constraint and proposes a graduation goal.
    a = handler_json(
        updates=[{"id": "c01", "status": "verified"}],
        new_annotations=[
            {"kind": "goal", "status": "open", "body": "write a pytest test enforcing c01"}
        ],
    )
    # B: goal handler writes the test and sets provisional enforced_by on c01.
    test_diff = new_file_diff(
        "test_stdlib.py", "def test_stdlib_only():\n    assert True\n"
    )
    b = handler_json(
        diff=test_diff,
        updates=[
            {"id": "g01", "status": "satisfied"},
            {"id": "c01", "attrs": {"enforced_by": "test_stdlib_only"}},
        ],
    )
    # C: final verification pass ratifies — sets status enforced.
    c = handler_json(updates=[{"id": "c01", "status": "enforced"}])

    sched, repo, git = make_sched(workrepo, [a, b, c])
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    c01 = annotation(repo, "c01")
    assert c01.status == "enforced"
    assert c01.attrs.get("enforced_by") == "test_stdlib_only"
    assert annotation(repo, "g01").status == "satisfied"


# -- budget (SPEC §06) ------------------------------------------------------

def test_new_annotation_status_normalized(workrepo):
    repo, git = workrepo
    repo.write("db.py", "# @constraint(c01, status=asserted): x\ndef fetch():\n    return []\n")
    git.commit("human: seed")
    # Handler proposes a decision with an out-of-vocabulary status.
    resp = handler_json(
        updates=[{"id": "c01", "status": "verified"}],
        new_annotations=[{"kind": "decision", "status": "accepted", "body": "chose X"}],
    )
    sched, repo, git = make_sched(workrepo, [resp])
    sched.step()
    dec = next(a for a in repo.parse_all() if a.kind == "decision")
    assert dec.status == "recorded"  # normalized from the invalid "accepted"


def test_budget_exhaustion(workrepo):
    repo, git = workrepo
    # Each activation makes real progress (a distinct diff), so only the budget
    # stops the loop — nothing else would.
    stub = "# @goal(g01, status=open): never finished\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    responses = [
        handler_json(diff=git_diff(repo, "m.py", stub.replace("return 0", f"return {n}")))
        for n in range(1, 11)
    ]
    sched, repo, git = make_sched(workrepo, responses, budget=3)
    outcome = sched.run()
    assert outcome.code == "budget"
    assert outcome.activations == 3


def test_no_progress_activation_strikes_out_instead_of_spinning(workrepo):
    """SPEC §06 termination: an activation that changes nothing leaves the repo
    identical and the annotation actionable, so it would be picked forever. It
    must take a strike and eventually leave the actionable set."""
    repo, git = workrepo
    repo.write("m.py", "# @goal(g01, status=open): never satisfied\ndef f():\n    return 0\n")
    git.commit("human: seed")
    responses = [handler_json(updates=[]) for _ in range(10)]
    sched, repo, git = make_sched(workrepo, responses, budget=50)
    outcome = sched.run()

    assert outcome.code == "blocked"  # terminated without touching the budget
    g01 = annotation(repo, "g01")
    assert g01.status == "stuck"
    assert g01.strikes == 3


def test_malformed_model_response_is_a_strike_not_a_crash(workrepo):
    """SPEC §11: a response that fails the output contract is an ordinary failed
    activation. It must never abort the loop."""
    repo, git = workrepo
    repo.write("m.py", "# @goal(g01, status=open): do it\ndef f():\n    return 0\n")
    git.commit("human: seed")
    sched, repo, git = make_sched(workrepo, ["I refuse to emit JSON." for _ in range(5)])
    outcome = sched.run()

    assert outcome.code == "blocked"
    assert annotation(repo, "g01").status == "stuck"


def test_diff_containing_braces_parses(workrepo):
    """A diff value full of braces and quotes must not defeat the JSON scan."""
    repo, git = workrepo
    stub = "# @goal(g01, status=open): add a dict\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    new = stub.replace('    return 0', '    d = {"k": [1, 2]}\n    return d')
    resp = handler_json(
        diff=git_diff(repo, "m.py", new), updates=[{"id": "g01", "status": "satisfied"}]
    )
    sched, repo, git = make_sched(workrepo, [resp])
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    assert '"k": [1, 2]' in repo.read("m.py")


def test_unresolved_answer_is_written_into_the_body(workrepo):
    """SPEC §05: the answer only survives if it lands in the medium."""
    repo, git = workrepo
    repo.write("q.py", "# @unresolved(u01, status=open): which db?\nx = 1\n")
    git.commit("human: seed")
    resp = handler_json(
        updates=[{"id": "u01", "status": "answered", "body": "which db? sqlite — see d01"}]
    )
    sched, repo, git = make_sched(workrepo, [resp])
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    u01 = annotation(repo, "u01")
    assert u01.status == "answered"
    assert "sqlite" in u01.full_body


def test_strike_cap_applies_to_constraints_and_questions(workrepo):
    """SPEC §11: every actionable kind needs a cap status, or the loop never
    terminates for that kind."""
    repo, git = workrepo
    repo.write("db.py", "# @constraint(c01, status=asserted): holds\ndef f():\n    return []\n")
    git.commit("human: seed")
    sched, repo, git = make_sched(workrepo, [handler_json(updates=[]) for _ in range(10)])
    outcome = sched.run()

    assert outcome.code == "blocked"
    c01 = annotation(repo, "c01")
    assert c01.status == "violated"
    assert c01.strikes == 3


def test_dry_run_writes_nothing(workrepo):
    """SPEC §12: --dry-run reports the pick; it must not mint IDs or demote."""
    repo, git = workrepo
    repo.write("m.py", "# @goal(, status=open): needs an ID\ndef f():\n    return 0\n")
    git.commit("human: seed")
    before = repo.read("m.py")

    sched, repo, git = make_sched(workrepo, [])
    outcome = sched.run(dry_run=True)

    assert outcome.code == "dry-run"
    assert "g01" in outcome.report  # the in-memory ID shows up in the report
    assert repo.read("m.py") == before  # ...but was never written to disk
    assert not git.has_uncommitted_changes()


def test_trust_skips_the_check_suite(workrepo):
    """SPEC §12: --trust accepts the diff without pytest/ruff arbitration."""
    repo, git = workrepo
    stub = "# @goal(g01, status=open): implement\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    diff = git_diff(repo, "m.py", stub.replace("return 0", "return 1"))
    resp = handler_json(diff=diff, updates=[{"id": "g01", "status": "satisfied"}])

    # Checks would fail; --trust means they never run.
    sched, repo, git = make_sched(workrepo, [resp], checks=StubChecks(ok=False), trust=True)
    outcome = sched.run()

    assert outcome.code == "fixpoint"
    assert annotation(repo, "g01").status == "satisfied"
    assert "return 1" in repo.read("m.py")


def test_failed_first_activation_keeps_minted_id(workrepo):
    """The revert on failure must not lose the ID minted this same iteration —
    the strike has to land on the right annotation."""
    repo, git = workrepo
    stub = "# @goal(, status=open): no ID yet\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: seed")
    diff = git_diff(repo, "m.py", stub.replace("return 0", "return 1"))

    sched, repo, git = make_sched(
        workrepo, [handler_json(diff=diff)], checks=StubChecks(ok=False)
    )
    result = sched.step()

    assert result.outcome == "failed"
    g01 = annotation(repo, "g01")
    assert g01 is not None  # the ID survived the revert
    assert g01.strikes == 1
    assert "no ID yet" in g01.full_body


def test_stale_demoted_constraint_still_reaches_its_strike_cap(workrepo):
    """The revert on failure undoes the staleness demotion `_prepare` wrote. If
    it is not redone, the constraint is back at `verified`, never matches its cap
    status, and accrues strikes until the budget runs out."""
    repo, git = workrepo
    repo.write(
        "db.py",
        "# @constraint(c01, status=verified, region_hash=deadbeef0000): holds\n"
        "def f():\n    return []\n",
    )
    git.commit("human: seed")
    sched, repo, git = make_sched(
        workrepo, [handler_json(updates=[]) for _ in range(20)], budget=20
    )
    outcome = sched.run()

    assert outcome.code == "blocked"  # not "budget"
    c01 = annotation(repo, "c01")
    assert c01.status == "violated"
    assert c01.strikes == 3


def test_reopened_goal_gets_a_full_set_of_fresh_attempts(workrepo):
    """The revert also undoes the human-reopen strike reset. Without redoing it,
    a reopened goal goes 3 → 4 and is stuck again after a single activation."""
    repo, git = workrepo
    stub = "# @goal(g01, status=open, strikes=3): reopened by hand\ndef f():\n    return 0\n"
    repo.write("m.py", stub)
    git.commit("human: reopen")
    diffs = [
        git_diff(repo, "m.py", stub.replace("return 0", f"return {n}")) for n in (1, 2, 3)
    ]
    sched, repo, git = make_sched(
        workrepo, [handler_json(diff=d) for d in diffs], checks=StubChecks(ok=False)
    )
    outcome = sched.run()

    assert outcome.code == "blocked"
    g01 = annotation(repo, "g01")
    assert g01.status == "stuck"
    assert g01.strikes == 3  # three fresh attempts, not one


def test_restating_the_current_status_is_not_progress(workrepo):
    """Re-asserting a value the annotation already holds must not buy another
    activation, or the no-progress strike never fires."""
    repo, git = workrepo
    repo.write("m.py", "# @goal(g01, status=open): do it\ndef f():\n    return 0\n")
    git.commit("human: seed")
    responses = [
        handler_json(diff="\n", updates=[{"id": "g01", "status": "open",
                                          "attrs": {"nope": ""}}])
        for _ in range(10)
    ]
    sched, repo, git = make_sched(workrepo, responses, budget=20)
    outcome = sched.run()

    assert outcome.code == "blocked"
    assert annotation(repo, "g01").status == "stuck"


def test_kill_and_resume_is_equivalent_to_an_uninterrupted_run(workrepo):
    """SPEC §13: state lives only in the repo, so a killed run resumes from the
    repository alone — the scheduler object carries nothing across iterations."""
    repo, git = workrepo
    src = "# @goal(g01, status=open): first\ndef a():\n    return 0\n"
    repo.write("m.py", src)
    repo.write(
        "n.py", "# @goal(g02, status=open, after=g01): second\ndef b():\n    return 0\n"
    )
    git.commit("human: seed")

    r1 = handler_json(
        diff=git_diff(repo, "m.py", src.replace("return 0", "return 1")),
        updates=[{"id": "g01", "status": "satisfied"}],
    )
    r2 = handler_json(updates=[{"id": "g02", "status": "satisfied"}])

    # One activation, then throw the scheduler away entirely (the "kill").
    sched, repo, git = make_sched(workrepo, [r1])
    sched.step()
    del sched

    # A brand-new scheduler, with no memory of the first, finishes the job.
    resumed, repo, git = make_sched(workrepo, [r2])
    outcome = resumed.run()

    assert outcome.code == "fixpoint"
    assert annotation(repo, "g01").status == "satisfied"
    assert annotation(repo, "g02").status == "satisfied"
    assert "return 1" in repo.read("m.py")
