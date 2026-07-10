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
stig step                                                # exactly one activation
stig status                                              # every annotation, status, strikes
stig check                                               # parse + hash pass only; CI-friendly (exits 1 on staleness/dups/grammar)
stig strip [--all] [--archive]                           # remove resolved goals/questions; keep decisions & tried
stig seed "<prompt>"                                     # sugar: write initial @goal annotations
```

`stig step` and `stig run` produce identical per-activation behavior; run is just
step in a loop.

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
| `gitutil.py`      | §10 | one activation = one commit; history as medium |
| `checks.py`       | §06 | pytest + ruff in a manifest-derived venv |
| `models.py`       | §01, §07 | the transforms (Anthropic + scriptable) |
| `handlers.py`     | §07 | one handler per kind; strict output contract |
| `scheduler.py`    | §06, §11 | the loop, gating, priority, strikes, oscillation |
| `cli.py`          | §12 | the CLI surface |

## Failure handling (SPEC §11)

- **Loops/thrash** — a per-annotation strike cap (default 3), stored as `strikes=N`
  on the annotation itself (never in scheduler memory). At cap, a goal becomes
  `stuck`.
- **Oscillation** — each `@tried` records `diff_hash=` of the reverted diff; a new
  diff matching a prior one is a strike without re-applying.
- **In-flight edits** — context files are hash-snapshotted at assembly; a mismatch
  when the handler returns discards the activation with no strike and no commit.
- **Injection** — annotation bodies are treated as data, never instructions; the
  diff channel cannot touch annotation lines; only grammar-valid status
  transitions from the structured channel are applied.
- **Human reopen** — an actionable annotation whose `strikes` is at cap implies a
  human edit; the scheduler resets `strikes=0`, keeping the `@tried` history.

## Tests

```bash
python -m pytest -q      # 32 tests, hermetic (scripted model + stub checks)
python -m ruff check stig
```

The suite exercises the acceptance-test scenarios (SPEC §13) at the machinery
level: reaching fixpoint, kill-and-resume equivalence, co-editing, staleness
demotion, the stuck/blocked path, and the constraint graduation relay.

<a name="spec"></a>
See the full normative specification — `SPEC-001 · Stig MVP · draft 0.3` — for the
authoritative behavior. RFC 2119 keywords apply.
