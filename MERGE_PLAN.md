# Branch Merge Plan

_Generated 2026-07-24. Reflects the branch state at that time; re-verify the
"ahead/behind" counts before executing if branches have moved since._

## TL;DR

All feature work has **already converged** onto `claude/stig-spec-i4gtmh`. The
three other feature branches are fully contained in it and carry nothing unique.
The one merge that has **not** happened is landing that integration branch into
`main` — and that merge is a clean fast-forward with zero conflicts. So "merging
the branches together" reduces to one PR plus some cleanup.

## Current state

| Branch | Tip | vs `main` | Status |
|---|---|---|---|
| `main` | `ba404b5` | base | Old base (2026-07-10). Has received **none** of the feature work. |
| `claude/stig-spec-i4gtmh` | `321280e` | +12 / -0 | **Integration branch.** Contains PRs #1, #3, #4, #5 already merged in. |
| `claude/github-pipeline-mac-dmg-lkqi1e` | `9777a1f` | +2 / -0 | ✅ Fully contained in stig-spec (PR #1). Stale. |
| `claude/stig-audit-fixes` | `e49d6c4` | +5 / -0 | ✅ Fully contained in stig-spec (PRs #3, #4). Stale. |
| `claude/stig-testing-design-ca7c98` | `2188228` | +11 / -0 | ✅ Fully contained in stig-spec (PR #5). Stale. |

Notes:
- `main` is a strict ancestor of `stig-spec`, so `stig-spec → main` **fast-forwards** (no merge commit needed, no conflicts). Verified with `git merge-tree`.
- The release tag `v0.3.0-alpha.1` points at `5686d59`, which lives **inside** stig-spec's history but is **not** yet on `main`. Landing stig-spec into main brings the tagged commit onto the default branch.
- PRs #1, #3, #4, #5 all targeted `claude/stig-spec-i4gtmh` as their base and are already merged there. None of them targeted `main`.

## The convergence, visually

```
main (ba404b5) ──────────────────────────────────┐ (ancestor of all)
                                                  │
claude/github-pipeline-mac-dmg-lkqi1e ──► merged (PR #1) ──┐
claude/stig-audit-fixes ──────────────► merged (PR #3,#4) ─┤
claude/stig-testing-design-ca7c98 ────► merged (PR #5) ────┤
                                                           ▼
                            claude/stig-spec-i4gtmh (321280e)  ◄── everything lives here
                                                           │
                                                           ▼
                                                    ??? not yet on main
```

## Plan

### Step 1 — Land the integration branch into `main` (the only real merge)

Open a PR from `claude/stig-spec-i4gtmh` into `main` and merge it. Because it
fast-forwards, this can be a plain merge with no conflict resolution.

```bash
git fetch origin
git checkout main
git merge --ff-only origin/claude/stig-spec-i4gtmh
git push origin main
```

Prefer doing it through a PR (repo convention) so CI runs against `main`:
open `claude/stig-spec-i4gtmh` → `main`, confirm the 8 CI checks are green,
then merge.

**Decision to confirm:** fast-forward vs. a merge commit. Fast-forward keeps
history linear and is available here. If you'd rather have an explicit
"integration landed" marker on `main`, use `git merge --no-ff` instead.

### Step 2 — Delete the three redundant feature branches

They contain nothing that isn't already in stig-spec (and, after Step 1, in
`main`). Deleting them prevents future confusion about which branch is current.

```bash
git push origin --delete claude/github-pipeline-mac-dmg-lkqi1e
git push origin --delete claude/stig-audit-fixes
git push origin --delete claude/stig-testing-design-ca7c98
```

Safety check before deleting each one (should print nothing):

```bash
git log --oneline origin/claude/stig-spec-i4gtmh..origin/<branch>
```

### Step 3 — Retire `claude/stig-spec-i4gtmh`

Once `main` contains it (Step 1), the branch is redundant too. Two options:

- **Keep it** if any external workflow triggers key off the branch name. (PR #5
  explicitly notes the name is load-bearing for workflow triggers — check
  `.github/workflows/*` before deleting.)
- **Delete it** if nothing references it, same as Step 2.

### Step 4 — Verify

```bash
git fetch origin
git log --oneline origin/main | head -15          # feature commits now on main
git merge-base --is-ancestor v0.3.0-alpha.1 origin/main && echo "tag on main"
```

## What this plan deliberately does NOT do

- **No content merge / conflict resolution** — there are no conflicts; every
  branch is already an ancestor of the integration branch.
- **No rebasing or history rewriting** — the merged PR history on stig-spec is
  preserved as-is.
- **No changes to the release tag** — `v0.3.0-alpha.1` stays where it is; it
  simply becomes reachable from `main` after Step 1.
