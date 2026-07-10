"""A tolerant unified-diff applier (SPEC §07 diff channel).

`git apply` is strict about hunk-header line counts, which stateless model calls
frequently get wrong (bare ``@@`` markers, off-by-one offsets). This applier
ignores the hunk-header numbers entirely and locates each hunk by matching its
context/removed lines against the file, which is far more robust for
model-generated diffs. The annotation-line guard (``diffutil``) still runs first,
so tolerance here never lets a diff rewrite annotation lines.
"""

from __future__ import annotations

from .repo import Repo


class PatchError(ValueError):
    pass


def _strip_prefix(path: str) -> str | None:
    path = path.strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _sections(diff: str):
    """Yield (old_path, new_path, body_lines) for each file in the diff."""
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old = _strip_prefix(lines[i][4:])
            new = _strip_prefix(lines[i + 1][4:])
            i += 2
            body: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("diff --git "):
                    break
                if nxt.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                    break
                body.append(nxt)
                i += 1
            yield old, new, body
        else:
            i += 1


def _hunks(body: list[str]):
    """Split a file body into hunks, each a list of (marker, text)."""
    hunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] | None = None
    for line in body:
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if current is None:
            current = []
            hunks.append(current)
        if line == "":
            current.append((" ", ""))
        elif line[0] == "\\":  # "\ No newline at end of file"
            continue
        elif line[0] in (" ", "+", "-"):
            current.append((line[0], line[1:]))
        else:
            current.append((" ", line))
    return [h for h in hunks if h]


def _blocks(hunk: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    old = [text for marker, text in hunk if marker in (" ", "-")]
    new = [text for marker, text in hunk if marker in (" ", "+")]
    return old, new


def _find(haystack: list[str], needle: list[str], start: int) -> int:
    if not needle:
        return -1
    n = len(needle)
    for i in range(start, len(haystack) - n + 1):
        if haystack[i : i + n] == needle:
            return i
    # Whitespace-tolerant fallback.
    hs = [h.rstrip() for h in haystack]
    nd = [x.rstrip() for x in needle]
    for i in range(start, len(hs) - n + 1):
        if hs[i : i + n] == nd:
            return i
    return -1


def _apply_to_file(original: str, hunks: list[list[tuple[str, str]]]) -> str:
    lines = original.split("\n")
    trailing_nl = original.endswith("\n")
    if trailing_nl:
        lines = lines[:-1]  # drop the empty element from the final newline
    search = 0
    for hunk in hunks:
        old, new = _blocks(hunk)
        if not old:  # pure addition with no anchoring context
            lines.extend(new)
            search = len(lines)
            continue
        idx = _find(lines, old, search)
        if idx < 0:
            raise PatchError(f"hunk context not found: {old[:2]!r}")
        lines[idx : idx + len(old)] = new
        search = idx + len(new)
    text = "\n".join(lines)
    if trailing_nl or not text.endswith("\n"):
        text += "\n"
    return text


def apply_diff(repo: Repo, diff_text: str) -> list[str]:
    """Apply a unified diff to the repo. Returns changed paths; raises PatchError."""
    if not diff_text.strip():
        return []
    changed: list[str] = []
    sections = list(_sections(diff_text))
    if not sections:
        raise PatchError("no file sections found in diff")
    for old_path, new_path, body in sections:
        path = new_path or old_path
        if path is None:
            raise PatchError("diff section has no target path")
        hunks = _hunks(body)
        if old_path is None:  # new file
            _, new_lines = _blocks([m for h in hunks for m in h])
            content = "\n".join(new_lines)
            repo.write(path, content + "\n" if not content.endswith("\n") else content)
        else:
            original = repo.read(path) if repo.exists(path) else ""
            repo.write(path, _apply_to_file(original, hunks))
        changed.append(path)
    return changed
