# Changelog

All notable changes to Stig are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

- Added a non-root Docker CLI image with pinned runtime dependencies, mounted
  repository usage, and a CI smoke test covering checks and an activation commit.
- Bound the fallback pytest and ruff checks to 120 seconds, including process
  group cleanup on timeout.
- Rebuild project environments when the runtime, platform, or repository path
  changes; clear old environments so removed dependencies cannot linger.
- Commit annotation normalization when stopping without an activation, so a
  completed or budget-limited run does not leave the repository dirty.
- Updated macOS builds to the macOS 15 ARM64 and Intel runners.
- Corrected credential documentation to match the Anthropic SDK integration.

- Checks are now declared in the medium: a `[[tool.stig.checks]]` array in
  `pyproject.toml` names the commands Stig runs after every activation,
  instead of a hardcoded pytest+ruff pair. Repositories that declare nothing
  keep the old behavior.
- `main` is now the trunk. CI and the DMG build watch `main` (and version
  tags); the `claude/stig-spec-i4gtmh` integration branch is retired now
  that its history has landed there.

## [0.3.0-alpha.1] — 2026-07-19

Initial public alpha: the scheduler loop, the five annotation kinds, the CLI
surface, and a macOS `.dmg` build.

- Core system: the annotation grammar (`@goal`, `@constraint`, `@unresolved`,
  `@decision`, `@tried`), region resolution and staleness hashing, the
  tolerant unified-diff applier, the diff-channel injection guard, and the
  scheduler loop (parse → pick → dispatch → apply → check → commit).
- `stig init`, plus a tutorial walked end-to-end against a live model before
  being written down.
- `stig run`/`step`/`status`/`check`/`strip`/`seed`, `--trust`, `--adopt`,
  `--dry-run`, and the strike/oscillation/staleness-demotion failure model.
- CI: ruff, a Python 3.10–3.14 test matrix, a wheel-install smoke test, and
  `stig check` dogfooding against this repository's own annotations.
- GitHub Actions pipeline building an installable, notarization-free macOS
  `.dmg` via PyInstaller, for both Apple Silicon and Intel.
- 144 hermetic tests (scripted model + stub checks) exercising fixpoint,
  kill-and-resume, co-editing races, staleness demotion, and the
  stuck/blocked/constraint-graduation paths.

[Unreleased]: https://github.com/matthewholliday/stig/compare/v0.3.0-alpha.1...HEAD
[0.3.0-alpha.1]: https://github.com/matthewholliday/stig/releases/tag/v0.3.0-alpha.1
