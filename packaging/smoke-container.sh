#!/usr/bin/env bash
set -euo pipefail

image="${1:-stig:local}"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

run() {
    docker run --rm --init --user "$(id -u):$(id -g)" \
        --mount "type=bind,src=$scratch,dst=/workspace" "$image" "$@"
}

docker run --rm "$image" --version
run init
run check
run status
run run --dry-run
run run
run run

# Verify the installed SDK and a real manifest-derived check environment.
# Stale host environments must be replaced before they can run checks.
mkdir -p "$scratch/.stig/venv/bin"
printf 'host-only interpreter\n' > "$scratch/.stig/venv/bin/python"
printf 'old-host-cache\n' > "$scratch/.stig/venv.hash"
docker run --rm --init --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$scratch,dst=/workspace" \
    --entrypoint python "$image" -c '
import anthropic
from stig.checks import RealChecks
result = RealChecks().run("/workspace")
print(result.output)
assert result.ok, result.output
'

# Exercise one activation and its commit without a paid model call.
docker run --rm --init --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$scratch,dst=/workspace" \
    --entrypoint python "$image" -c '
import json
from stig.checks import RealChecks
from stig.gitutil import Git
from stig.models import ScriptedModel
from stig.repo import Repo
from stig.scheduler import Scheduler
repo = Repo("/workspace")
git = Git(repo.root)
repo.append_lines("ARCHITECTURE.anno", ["# @goal(g01, status=open): record a decision"])
git.commit("smoke: add goal")
model = ScriptedModel([json.dumps({"updates": [{"id": "g01", "status": "satisfied"}]})])
result = Scheduler(repo, git, model, RealChecks()).run()
assert result.code == "fixpoint", result.report
assert result.activations == 1
assert git.activation_count() == 1
assert not git.has_uncommitted_changes()
print("container activation passed")
'
