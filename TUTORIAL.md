# Stig tutorial — build a small library, annotation by annotation

This is a hands-on walkthrough of [Stig](README.md). You'll build `tempo`, a
tiny duration-parsing library, without ever writing a prompt to a chat window.
Every instruction you give lives in the repository as a typed annotation.

Everything below was executed end to end against a live model before being
written down. The outputs are transcripts, not illustrations.

**What you'll see, in order:**

| Step | Mechanic |
|------|----------|
| 1–3 | Goals, dependency ordering, one activation = one commit |
| 4 | `@constraint` — verification that never edits code |
| 5 | `@unresolved` — how Stig hands a decision back to you |
| 6 | `@decision` — recording your answer so it stays answered |
| 7 | Staleness demotion — editing code invalidates its verification |
| 8 | Graduation to `enforced` |
| 9 | `stig strip` — collapsing finished work |
| 10 | What goes wrong, and how to unwedge it |

Budget roughly **10 model activations** for the whole tutorial.

---

## 0. Install

Stig requires Python **3.10+**. Check before you start — the system `python3` on
macOS is often 3.9, and Stig will not install against it.

```bash
python3 -V   # must be >= 3.10
```

From the Stig checkout:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[anthropic,dev]"
stig --help
```

The stock `python3 -m venv .venv && pip install -e ".[anthropic,dev]"` works too,
*if* your interpreter's bundled pip is compatible with it. On the machine this
tutorial was written on it wasn't — both Homebrew Pythons ship a pip too new for
`ensurepip` to bootstrap, and `python3 -m venv` fails outright:

```
Error: Command '[.../bin/python3.10', '-m', 'ensurepip', ...]' returned non-zero exit status 1.
```

`uv` sidesteps that, which is why it leads here. If stock venv works for you,
use it.

The `anthropic` extra is what powers real activations. Credentials come from the
environment — `ANTHROPIC_API_KEY`, or an `ant auth login` profile.

> **A note on the two virtualenvs.** The one you just made is where the `stig`
> command lives. Separately, Stig builds its *own* venv at `.stig/venv` inside
> whatever project it's working on, and runs pytest and ruff in there. That
> second venv is a derived artifact of the project's manifest — Stig rebuilds it
> whenever `pyproject.toml` or `requirements.txt` changes, and `.stig/` ignores
> itself so it never lands in your history. Pass `--no-venv` to run checks in
> the current environment instead.

---

## 1. Scaffold the project

```bash
$ stig init tempo
initialized /path/to/tempo
  package: tempo/
  created: git repository, pyproject.toml, ARCHITECTURE.anno, tempo/__init__.py, tests/__init__.py
  committed: stig init: scaffold project

Next: write a goal in ARCHITECTURE.anno, commit, then `stig run`.

$ cd tempo
```

That's a git repository, a manifest (so the check suite has something to build a
venv from), the annotation file, a package, a tests directory — and a commit,
because Stig refuses to run against a dirty tree.

`init` never overwrites. Run it inside an existing project and it adds only
what's missing, reporting the rest as `kept existing`. Re-running it is a no-op.

Two flags worth knowing: `--package NAME` overrides the package directory name
(otherwise derived from the directory, so `my-cool.tool` becomes `my_cool_tool`),
and `--no-commit` leaves the tree dirty if you'd rather commit yourself.

Note that a fresh scaffold already contains one annotation:

```
# @decision(, status=recorded): library code lives in `tempo/` and tests in
#   .. `tests/`. New modules go inside `tempo/` — a top-level `tempo.py`
#   .. would shadow the package on import and silently win.
```

That isn't decoration. `@decision` bodies are the one thing context assembly
never drops, so every handler sees it. Without it, handlers write `tempo.py`
beside the empty `tempo/`, the package wins every import, and downstream goals
run against a module whose contents have silently vanished — which is exactly
what happened while this tutorial was being written.

---

## 2. Write the goals

This is the whole interface. Append to `ARCHITECTURE.anno` — the one root-level
file that holds repo-scoped annotations:

```
# @goal(, status=open): create tempo/duration.py with parse_duration(text) that
#   .. accepts strings like "90s", "5m", "1h30m", "2h 15m 30s" and returns the total
#   .. number of seconds as an int. Raise ValueError on empty or unparseable input.
# @goal(, status=open, after=g01): create tests/test_duration.py with pytest tests
#   .. covering each unit, combined units, whitespace, and the ValueError cases.
```

Three things to notice:

- **The ID slot is empty.** `@goal(, status=open)` — Stig mints `g01`, `g02`, …
  in document order and writes them back into your file on the first run. Write
  IDs yourself only if you need to.
- **`..` continues a line.** Any line starting with `#` and an indented `..`
  belongs to the annotation above it. Use it freely; goals should be specific.
- **`after=g01` orders the work.** `g02` will not activate until `g01` reaches a
  terminal success status. Since you know IDs get minted in document order, you
  can forward-reference them. For multiple dependencies: `after=g01&c04`.

Commit — Stig refuses to run against a dirty tree, so that a failed activation
can never take your uncommitted work down with it:

```bash
git add -A && git commit -m "seed project"
```

### Alternative: let Stig write the goals

If you'd rather start from a sentence than a list, `stig seed` drafts goals — or
fold it into scaffolding with `stig init --seed "..."`, which gets you from
nothing to a committed, ready-to-run project in one command:

```bash
stig seed "a Python library that converts between temperature units"
```

One model call, and it appends open goals to `ARCHITECTURE.anno`:

```
# Seeded from: a Python library that converts between temperature units
# @goal(, status=open): Provide a Python library that converts temperatures between Celsius, Fahrenheit, and Kelvin
# @goal(, status=open): Expose simple conversion functions for each unit pair (e.g. celsius_to_fahrenheit, kelvin_to_celsius)
# @goal(, status=open): Offer a unified convert(value, from_unit, to_unit) interface that dispatches to the correct conversion
...
```

It's sugar, and it's undisciplined — nine goals from one sentence, including
scope you may not want. **Treat the output as a draft to prune**, and mean it: a
seeded 8-goal Roman-numeral library run end to end produced working code and 41
passing tests, but two goals still hit the strike cap and went `stuck`, because
vague goals ("provide a clear public API") give a handler nothing to verify
itself against. Goals you wrote yourself fail far less often.

For this tutorial, stick with the two hand-written goals.

---

## 3. Look before you leap

```bash
$ stig status
ID     KIND        STATUS       STK  LOCATION
------------------------------------------------------------
-      goal        open         0    ARCHITECTURE.anno:1
-      goal        open         0    ARCHITECTURE.anno:4
```

IDs show as `-` because nothing has minted them yet. Now ask what *would*
happen:

```bash
$ stig run --dry-run
[dry-run] would activate goal(g01): create tempo/duration.py with parse_duration(text) that
accepts strings like "90s", "5m", "1h30m", "2h 15m 30s" and returns the total number of
seconds as an int. Raise ValueError on empty or unparseable input.
activations: 0
```

`--dry-run` writes nothing at all — no ID minting, no staleness demotion, no
commit. It shows you the ID `g01` *would* get. Use it whenever you're unsure
which annotation is next.

### Run it

```bash
$ stig run --budget 6
[fixpoint] reached fixpoint — nothing actionable or open
activations: 2
```

Two activations, two commits:

```bash
$ git log --oneline
229ee62 stig(g02): create tests/test_duration.py with pytest tests covering eac
15decd3 stig(g01): create tempo/duration.py with parse_duration(text) that acce
bff258b seed project
```

**One activation, one commit** — always. The commit subject names the annotation
that caused it. That's your audit trail: `git log` is a record of which
annotation produced which change, and `git revert` on a single commit undoes
exactly one unit of work.

Both goals are now `satisfied`, written back into the file:

```
# @goal(g01, status=satisfied): create tempo/duration.py with parse_duration(text) that
...
# @goal(g02, status=satisfied, after=g01): create tests/test_duration.py with pytest tests
```

And the code is real:

```bash
$ .stig/venv/bin/python -m pytest -q
.......                                                                  [100%]
7 passed in 0.00s
```

Your numbers will differ. A clean re-run of these exact steps produced 20 tests
instead of 7 — same goals, same fixpoint, different code. Activations are
stateless model calls, so treat every transcript here as one sample, not a
fixture. What's stable is the *shape*: two activations, two commits, both goals
satisfied, a green suite.

Nothing forced those tests to pass at the end. They passed *during* the
activation: Stig applied the model's diff, ran pytest and ruff, and would have
reverted the whole thing and recorded a `@tried` if either had failed.

> **`stig step` vs `stig run`.** `stig step` performs exactly one activation.
> `stig run` is that same code path in a loop. When you're learning the system,
> or debugging a goal that keeps failing, step through it.

---

## 4. Add a constraint

Goals generate. Constraints only verify — a constraint handler is structurally
incapable of editing your code. Attach one by placing it directly above a
definition; the region it governs is that function's body.

In `tempo/duration.py`, above `def parse_duration`:

```python
# @constraint(, status=asserted): parse_duration never returns a negative value
#   .. and never raises anything other than ValueError for str input.
def parse_duration(text):
```

While you're here, add an open question to `ARCHITECTURE.anno`:

```
# @unresolved(, status=open): should parse_duration accept fractional units like "1.5h"?
```

Commit and run:

```bash
$ git add -A && git commit -m "human: add constraint + open question"
$ stig run --budget 5
```

---

## 5. Stig stops and asks

```
[blocked] blocked — no actionable annotation, but work remains:
  g03 [tempo/duration.py] goal: open but gated or blocked by a dependency — write a
    pytest test enforcing c01: assert parse_duration never returns a negative value
    across a range of valid inputs, and that for str inputs it raises only ValueError
    (never any other exception type) on invalid/edge-case strings.
  u01 [ARCHITECTURE.anno] unresolved: needs a human answer — Q: should parse_duration
    accept fractional units like "1.5h"? ... whether it SHOULD accept fractional units
    is a product/design decision with no supporting spec, decision, or prior attempt in
    the repo. Escalating for a human to decide desired behavior.
activations: 2
```

Read that carefully, because three distinct mechanics fired at once.

**The constraint verified itself, then spawned a goal.** `c01` is now `verified`
and carries a `region_hash`. Verifying wasn't enough for it, though — it also
wrote a *new* goal, `g03`, asking for a test that actually enforces the
invariant. A constraint can't write that test itself, so it delegates by
creating an annotation. This is the whole coordination model: components talk by
leaving traces in the medium, never by calling each other.

**`blocked` is not `fixpoint`.** Fixpoint means nothing is actionable and
nothing is open. Blocked means work remains but Stig can't move it. Different
exit codes; in CI you care about the difference.

**A repo-scoped `needs-human` question gates every goal.** That's why `g03`
didn't run despite having no `after=`. `u01` lives in `ARCHITECTURE.anno`, so
its scope is the whole repository, and Stig declines to build on top of an
unanswered design question. This is the deliberate handoff: the model recognized
the question wasn't derivable from the repo and escalated rather than guessing.

---

## 6. Answer it with a decision

You answer by editing files. Delete the `@unresolved` line and replace it with
the permanent record of what you decided:

```
# @decision(, status=recorded): parse_duration accepts integer units only. Fractional
#   .. units like "1.5h" stay a ValueError — the return type is int seconds and
#   .. silent rounding would hide precision loss.
```

`@decision` is terminal and permanent. It's how the *reasoning* survives — six
months from now the rationale sits next to the code, and `stig strip` will never
remove it.

```bash
$ git add -A && git commit -m "human: answer u01 with a decision"
$ stig run --budget 5
[fixpoint] reached fixpoint — nothing actionable or open
activations: 1
```

The gate lifted, `g03` ran, and everything settled:

```bash
$ stig status
ID     KIND        STATUS       STK  LOCATION
------------------------------------------------------------
c01    constraint  verified     0    tempo/duration.py:15
d01    decision    recorded     0    ARCHITECTURE.anno:6
g01    goal        satisfied    0    ARCHITECTURE.anno:1
g02    goal        satisfied    0    ARCHITECTURE.anno:4
g03    goal        satisfied    0    tempo/duration.py:14

$ stig check
ok: 5 annotations, no errors
```

---

## 7. Edit the code and watch verification decay

A `verified` constraint stores a hash of the region it verified. Change that
region and the verification is, by construction, stale. Add a comment inside
`parse_duration`:

```python
    total = 0
    # human tweak: note the running tally
```

```bash
$ git add -A && git commit -m "human: edit function body"
$ stig check
error: c01: stale verification (region changed)
$ echo $?
1
```

`stig check` is parse-and-hash only — no model calls, fast, and it exits 1. Put
it in CI and a pull request that quietly edits a verified region fails the
build.

```bash
$ stig run --dry-run
[dry-run] would activate constraint(c01): parse_duration never returns a negative value
and never raises anything other than ValueError for str input.
```

Stig demoted `c01` from `verified` back to `asserted` and queued it for
re-verification. Nobody tracked this. There is no watcher process and no cache —
the hash is in the file, the region is in the file, and they disagree.

---

## 8. Graduate the constraint to `enforced`

`verified` means a model read the region and agreed. `enforced` is stronger: a
named test pins the invariant, so regressions fail your suite rather than
waiting for the next activation.

Graduation needs `enforced_by=<test name>`. `c01` is a conjunction — two clauses —
so it needs two tests, and `enforced_by` takes an `&`-separated list, the same
separator `after=` uses:

```
enforced_by=test_never_returns_negative&test_str_input_only_raises_value_error
```

You don't have to ask for this. The constraint already spawned `g03` for exactly
this purpose back in step 5, so just let it run:

```bash
$ stig run --budget 8
[fixpoint] reached fixpoint — nothing actionable or open
activations: 2
```

The tests exist and `c01` records both:

```
# @constraint(c01, status=verified, region_hash=2e1066571bb6,
#   enforced_by=test_never_returns_negative&test_str_input_only_raises_value_error): ...
```

Enforcement is all-or-nothing. If either test disappears, part of the invariant
is unguarded, so the constraint demotes and `stig check` names the missing one:

```bash
$ stig check
error: c01: enforced_by test(s) not found: test_never_returns_negative
```

Note it's still `verified`, not `enforced`. Graduation happens *during* a
constraint activation, and `verified` is a terminal success status — `c01` won't
activate again on its own. To collect the promotion, re-assert it:

```bash
$ sed -i '' 's/@constraint(c01, status=verified/@constraint(c01, status=asserted/' tempo/duration.py
$ git add -A && git commit -m "human: re-assert c01"
$ stig run --budget 3
[fixpoint] reached fixpoint — nothing actionable or open
activations: 1

$ grep '@constraint(c01' tempo/duration.py
# @constraint(c01, status=enforced, region_hash=04a89ffacc74, enforced_by=test_c01_invariants): ...
```

`enforced` has a sharper staleness rule than `verified`: it demotes if the
region changes **or** if the named test disappears. Delete `test_c01_invariants`
and `c01` drops straight back to `asserted`.

---

## 9. Collapse the finished work

Satisfied goals and answered questions are noise once they're done. Permanent
records are not.

```bash
$ stig strip
stripped 4 annotation(s); archived 0

$ stig status
ID     KIND        STATUS       STK  LOCATION
------------------------------------------------------------
c01    constraint  enforced     0    tempo/duration.py:14
d01    decision    recorded     0    ARCHITECTURE.anno:1

$ .stig/venv/bin/python -m pytest -q
..........                                                               [100%]
10 passed in 0.00s
```

Four satisfied goals gone; the enforced invariant and the recorded decision
remain. `strip` removes only resolved goals and answered questions — `@decision`
and `@tried` are the permanent record, and even `--all` won't touch them.
`--archive` relocates the `@tried` of satisfied goals into `ARCHITECTURE.anno`
instead of deleting them.

The library works, ten tests pass, and what's left in the tree is the standing
truth: one enforced invariant, one recorded decision.

---

## 10. When it goes wrong

Everything below happened during this tutorial's actual run. It is worth
knowing.

### A grammar error will not fix itself

Attribute values are rendered into a comma-separated, paren-delimited list, so a
value containing `,`, `(`, or `)` used to produce a header Stig's own parser
rejected — wedging every subsequent command:

```bash
$ stig run
stig: attribute 'test_str_input_raises_only_value_error' is not key=value
```

That specific hole is closed: values are sanitized at render time, so writing an
unparseable header is now structurally impossible, and a comma in a
multi-valued attribute folds onto `&`. But a **hand-edited** annotation can still
be malformed, and the recovery is worth knowing.

`stig status` sometimes still works when `run` won't, which makes it a decent
triage tool. Stop running — re-running a wedged repo just appends more repair
goals — then `grep` for the quoted fragment, fix the line by hand, commit, and
delete any goals the failed attempts left behind.

Symptoms worth recognizing:

| Message | Meaning |
|---------|---------|
| `attribute '<x>' is not key=value` | Hand-edited annotation header is malformed. Grep for `<x>`, fix by hand. |
| `enforced_by test(s) not found: <x>` | The named test was renamed or deleted. Restore it, or re-point `enforced_by`. |
| `stale verification (region changed)` | Expected. Re-run Stig, or re-verify. |
| `refusing to run with uncommitted changes` | Commit them, or use `--adopt`. |
| `[blocked]` with a `needs-human` line | Yours to answer. See step 5. |
| `[blocked]` with a `stuck` goal | Hit the strike cap. Read its `@tried` records. |

### Strikes

An annotation that fails repeatedly accumulates `strikes=N` **on the annotation
itself**, capped at 3. At the cap, goals go `stuck`, constraints `violated`,
questions `needs-human`. Nothing is remembered in scheduler memory — kill the
process mid-run and restart, and it resumes from the files alone.

A stuck goal usually means the goal is underspecified. Read the `@tried` records
it left behind — each names a failed approach and the hash of the reverted diff —
then rewrite the goal to be more specific. Editing an annotation at the cap
resets its strikes to 0 while keeping the `@tried` history.

### `--adopt` and `--trust`

- `--adopt` commits pre-existing uncommitted changes as human changes rather
  than refusing to run. Convenient; know that it commits whatever is in your
  tree.
- `--trust` skips the pytest/ruff check suite. Faster, and it means a model diff
  lands without ever being executed. Every other gate — the annotation-line
  guard, oscillation detection, path escaping, patch application — still runs.
  Reach for it when the project has no meaningful test suite yet, not to get
  past a failing one.

---

## What to take away

The loop you drove is five lines long, holds no memory, and contains no
intelligence about your project:

```
parse the repo → pick one actionable annotation → one stateless model call
→ apply, check, commit → repeat until nothing is actionable
```

Everything that made `tempo` work is sitting in the files. The goals that
generated it, the invariant that guards it, the decision behind the design, and
a commit per unit of work. Delete the `stig` process at any moment and the
project state is unharmed, because the project state was never in the process.

That's the point. Your leverage is in writing precise annotations — and
annotations are reviewable, diffable, and versioned in a way a chat transcript
never is.

### Reference

```
stig run [--budget N] [--dry-run] [--trust] [--adopt]   # loop to fixpoint, blocked, or budget
stig step [--trust] [--adopt]                            # exactly one activation
stig status                                              # every annotation, status, strikes
stig check                                               # parse + hash only; exits 1 on staleness
stig strip [--all] [--archive]                           # remove resolved goals/questions
stig seed "<prompt>"                                     # draft initial @goal annotations
```

Global flags: `--root <dir>`, `--model <id>`, `--no-venv`.

See [README.md](README.md) for the kind/status tables, the module map, and the
full failure-handling model.
