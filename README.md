# Stig — a non-agentic coding system

> All working state lives in the repository itself — files plus git history — as
> typed annotations. Stateless model calls fire on annotations they can act on.
> The system terminates when nothing is actionable and nothing is blocked.
> **There is no agent.**

Stig (from *stigmergy*: coordination through traces left in a shared medium) is a
reference implementation of [SPEC-001 · Stig MVP · draft 0.3](#spec).

## The three components and the deliberate absence

- **The medium** — the repository. Source files and git history carry all working
  state. Files hold typed annotations in comments; git history holds provenance
  (activation trailers, reverted-diff records). Nothing lives in process memory
  across iterations.
- **The transforms** — stateless LLM calls, one per annotation type, that read a
  slice of the repo and emit a diff plus annotation status updates. No call knows
  any other call happened except through what is written in the files.
- **The scheduler** — a deliberately dumb loop: parse, pick, dispatch, apply,
  commit, repeat. It terminates at fixpoint.
- **The absence** — no persistent agent, no conversation history, no session
  state. If the process is killed at any point, restarting resumes correctly from
  the repository alone.

**Design invariant:** intelligence about *what* to do lives in annotations
(inspectable, versioned, human-editable); intelligence about *how* to do it lives
in the frozen model. The scheduler contains neither.

## Install

```bash
pip install -e ".[anthropic,dev]"   # anthropic extra powers the real model calls
```

Handler calls use `claude-opus-4-8` by default and read credentials from the
environment (`ANTHROPIC_API_KEY`, or an `ant auth login` profile). The scheduler
machinery runs without the optional dependency — only live activations need it.

## The five kinds (SPEC §03–§05)

| Kind          | Role                                                        | Statuses |
|---------------|------------------------------------------------------------|----------|
| `@goal`       | Work to be done. The generative kind — its handler writes code. | `open → satisfied · stuck` |
| `@constraint` | An invariant a region must satisfy. Verifies only — never edits. | `asserted → verified · violated`; `verified + enforced_by= → enforced` |
| `@unresolved` | An open question blocking or shaping work.                  | `open → answered · needs-human` |
| `@decision`   | Permanent record of a choice and its rationale.            | `recorded` (terminal) |
| `@tried`      | Permanent record of a failed approach, with `diff_hash=`.  | `recorded` (terminal) |

Grammar (single-line comments), with optional `..`-indented continuation lines:

```python
# @goal(g04, status=open, after=g02): expose a streaming variant of fetch_all
# @constraint(c09, status=asserted): never hold db_lock across an await
# @decision(d02, status=recorded): chose sqlite over postgres — single-user tool,
#   .. zero-config install matters more than concurrent writes
# @tried(t01, status=recorded, goal=g04, diff_hash=8a41f2c09d3e): async generator …
async def fetch_all(query: str) -> list[Row]:
    ...
```

All four annotations attach to `fetch_all` — the next definition below them
(decorator-style, SPEC §03) — so `c09`'s region is the function body. The one
root-level `ARCHITECTURE.anno` file holds repo-scoped annotations.

## CLI (SPEC §12)

```
stig run [--budget N] [--dry-run] [--trust] [--adopt]   # loop to fixpoint, blocked, or budget
stig step [--trust] [--adopt]                            # exactly one activation
stig status                                              # every annotation, status, strikes
stig check                                               # parse + hash pass only; CI-friendly (exits 1 on staleness/dups/grammar)
stig strip [--all] [--archive]                           # remove resolved goals/questions; keep decisions & tried
stig seed "<prompt>"                                     # sugar: write initial @goal annotations
```

Global flags: `--root <dir>` (default `.`), `--model <id>` (default
`claude-opus-4-8`), `--no-venv` (run checks in the current environment rather
than a manifest-derived venv).

| Flag | Effect |
|------|--------|
| `--budget N` | stop after N activations (default 50) |
| `--dry-run` | report which annotation *would* activate, then exit. Writes nothing — no ID minting, no staleness demotion, no commit |
| `--trust` | skip the check suite; accept the model's diff without pytest/ruff arbitration. Every other gate — annotation-line guard, oscillation, patch application — still runs |
| `--adopt` | commit pre-existing uncommitted changes as human changes instead of refusing to run |
| `--all` | widen `strip` to every goal and question regardless of status. `@decision` and `@tried` are the permanent record; `--all` does not touch them |
| `--archive` | relocate `@tried` of satisfied goals into `ARCHITECTURE.anno` instead of deleting them |

`stig step` and `stig run` produce identical per-activation behavior; run is just
step in a loop.

A goal may depend on several annotations at once: `after=g02&c04` activates only
once every named annotation has reached a terminal success status.

## How one activation works (SPEC §06)

```
loop:
  annos      = parse_repo()                # incl. ARCHITECTURE.anno
  assign_missing_ids(annos)                # human-added annotations get IDs written back
  demote_stale_constraints(annos)          # hash mismatch or missing enforcing test ⇒ verified → asserted
  actionable = [a for a in annos if a.actionable and deps_met(a) and not gated(a)]
  if not actionable:
      exit BLOCKED if anything open/stuck/needs-human else FIXPOINT
  a = pick(actionable)                      # fixed priority rule
  ctx, snapshot = assemble_context(a)       # hash of every file read into context
  result = handlers[a.kind](a, ctx)         # stateless call
  if disk_hashes() != snapshot: continue    # human edited mid-call ⇒ discard, no strike
  apply_diff(result.diff)                    # rejected if it touches annotation lines
  run_checks()                               # pytest + ruff in a manifest-derived venv; fail ⇒ revert & record @tried
  update_statuses(result.updates)
  git_commit(a)                              # one activation = one commit
```

## Module map

| Module            | SPEC section | Responsibility |
|-------------------|--------------|----------------|
| `annotations.py`  | §03, §04, §05 | grammar, kinds, statuses, lifecycles |
| `regions.py`      | §03, §09 | region resolution, region/structure hashing, staleness |
| `repo.py`         | §01, §04 | the medium: files, ID minting, targeted edits |
| `graph.py`        | §08 | cheap static import graph (one hop each way) |
| `context.py`      | §08 | deterministic context assembly + snapshot |
| `diffutil.py`     | §07, §11 | diff channel guard, `diff_hash` |
| `patcher.py`      | §07 | tolerant unified-diff applier (context-matched, ignores hunk line numbers) |
| `gitutil.py`      | §10 | one activation = one commit; history as medium |
| `checks.py`       | §06 | pytest + ruff in a manifest-derived venv |
| `models.py`       | §01, §07 | the transforms (Anthropic + scriptable) |
| `handlers.py`     | §07 | one handler per kind; strict output contract |
| `scheduler.py`    | §06, §11 | the loop, gating, priority, strikes, oscillation |
| `cli.py`          | §12 | the CLI surface |

## Failure handling (SPEC §11)

- **Loops/thrash** — a per-annotation strike cap (default 3), stored as `strikes=N`
  on the annotation itself (never in scheduler memory). At cap, each actionable
  kind is driven out of its actionable status: a goal becomes `stuck`, a
  constraint `violated`, a question `needs-human`.
- **No-progress activations** — an activation that emits no diff, no accepted
  status transition, and no new annotation leaves the repository identical and
  the annotation still actionable. That is a strike, not a success; otherwise the
  loop would pick the same annotation forever and never reach fixpoint.
- **Malformed model output** — a response that fails the output contract is an
  ordinary failed activation (revert, strike, commit), never a crash.
- **Oscillation** — each `@tried` records `diff_hash=` of the reverted diff; a new
  diff matching a prior one is a strike without re-applying.
- **In-flight edits** — context files are hash-snapshotted at assembly; a mismatch
  when the handler returns discards the activation with no strike and no commit.
- **Injection** — annotation bodies are treated as data, never instructions; the
  diff channel cannot touch annotation lines; only grammar-valid status
  transitions from the structured channel are applied. The guard is enforced by
  comparing each file's annotation lines before and after, at the moment of
  writing — not by inspecting `+`/`-` markers, which a whole-file overwrite, a
  deletion, or an unmarked line slips past. Diff paths are untrusted and may not
  escape the repository root; a diff applies whole or not at all.
- **Human reopen** — an actionable annotation whose `strikes` is at cap implies a
  human edit; the scheduler resets `strikes=0`, keeping the `@tried` history.

## Tests

```bash
python -m pytest -q      # 99 tests, hermetic (scripted model + stub checks)
python -m ruff check stig tests
```

The suite exercises the acceptance-test scenarios (SPEC §13) at the machinery
level: reaching fixpoint, kill-and-resume equivalence, co-editing, staleness
demotion, the stuck/blocked path, and the constraint graduation relay — plus the
CLI surface, the structured-channel parser, and the diff-guard/applier
agreement.

<a name="spec"></a>
## Specification

`SPEC-001 · Stig MVP · draft 0.3` is the authoritative, normative description of
this behavior (RFC 2119 keywords apply). It is a separate document and is **not
vendored in this repository** — the `SPEC §NN` references throughout the source
point into it. This README summarizes it; where the two disagree, the spec wins.
