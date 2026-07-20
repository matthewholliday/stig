"""Region resolution and staleness hashing.

The region is the span of code an annotation governs. Default: an annotation
attaches to the next definition (``def`` / ``class``) that follows it in the
same scope — decorator-style — and its region is that definition's full body.
If no definition follows in the enclosing scope, the region runs from the
annotation line to the end of the enclosing scope. ``region=file`` overrides.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .annotations import Annotation, is_annotation_line

_DEF_RE = re.compile(r"^(async\s+def|def|class)\b")
_DECORATOR_RE = re.compile(r"^@\w")
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+\S")


@dataclass(frozen=True)
class Region:
    """A resolved region as an inclusive [start, end] line range (0-based)."""

    start: int
    end: int
    kind: str  # "definition", "scope", or "file"

    def line_range(self) -> range:
        return range(self.start, self.end + 1)

    def overlaps(self, other: "Region") -> bool:
        return self.start <= other.end and other.start <= self.end


def _leading_ws(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _block_end(lines: list[str], def_idx: int) -> int:
    """Last line belonging to the body of a def/class starting at ``def_idx``."""
    def_indent = _leading_ws(lines[def_idx])
    last = def_idx
    i = def_idx + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        if _leading_ws(line) > def_indent:
            last = i
            i += 1
        else:
            break
    return last


def _scope_end(lines: list[str], anno_idx: int, indent: int) -> int:
    """End of the scope enclosing an annotation at ``anno_idx`` (indent level)."""
    last = anno_idx
    i = anno_idx + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        if _leading_ws(line) >= indent:
            last = i
            i += 1
        else:
            break
    return last


def resolve_region(ann: Annotation, file_text: str) -> Region:
    """Resolve the governed region of ``ann`` within its file."""
    lines = file_text.splitlines()
    if ann.attrs.get("region") == "file":
        return Region(0, max(0, len(lines) - 1), "file")

    indent = len(ann.indent)
    # Scan forward from just after the annotation (and its continuation lines),
    # skipping blank lines, other annotation lines, and decorators.
    i = ann.end_line + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        if is_annotation_line(line):
            i += 1
            continue
        if _DECORATOR_RE.match(line.strip()):
            i += 1
            continue
        break

    if i < len(lines):
        stripped = lines[i].strip()
        if _DEF_RE.match(stripped) and _leading_ws(lines[i]) == indent:
            return Region(i, _block_end(lines, i), "definition")

    # No definition follows in the enclosing scope: annotation line to end of scope.
    end = _scope_end(lines, ann.start_line, indent) if indent else max(0, len(lines) - 1)
    return Region(ann.start_line, end, "scope")


def _normalize_region_text(lines: list[str], region: Region) -> str:
    """Whitespace-normalized region text, annotation lines excluded."""
    out: list[str] = []
    for idx in region.line_range():
        if idx >= len(lines):
            break
        line = lines[idx]
        if is_annotation_line(line):
            continue
        collapsed = " ".join(line.split())
        if collapsed:
            out.append(collapsed)
    return "\n".join(out)


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def region_hash(ann: Annotation, file_text: str) -> str:
    """sha256[:12] of the normalized governed region."""
    region = resolve_region(ann, file_text)
    return _sha12(_normalize_region_text(file_text.splitlines(), region))


def region_has_executable_lines(ann: Annotation, file_text: str) -> bool:
    """True if the resolved region contains at least one non-comment code line."""
    lines = file_text.splitlines()
    region = resolve_region(ann, file_text)
    for idx in region.line_range():
        if idx >= len(lines):
            break
        line = lines[idx]
        if is_annotation_line(line):
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def enforcing_test_exists(enforced_by: str, py_files: dict[str, str]) -> bool:
    """True if every test named by an ``enforced_by=`` reference is defined.

    Shared by ``stig check`` and the scheduler's staleness demotion so the two
    can never reach opposite conclusions about the same annotation.

    A constraint whose body is a conjunction often needs more than one test, so
    ``enforced_by`` accepts an ``&``-separated list — the same separator
    ``after=`` uses. Enforcement is all-or-nothing: if any named test is missing,
    part of the invariant is unguarded and the constraint demotes.
    """
    names = enforcing_test_names(enforced_by)
    return bool(names) and not missing_enforcing_tests(enforced_by, py_files)


def enforcing_test_names(enforced_by: str) -> list[str]:
    """The bare test names an ``enforced_by=`` reference points at."""
    names = [n.split("::")[-1].strip() for n in enforced_by.split("&")]
    return [n for n in names if n]


def missing_enforcing_tests(enforced_by: str, py_files: dict[str, str]) -> list[str]:
    """Which named tests are absent — so errors can name the culprit, not the list."""
    return [
        name
        for name in enforcing_test_names(enforced_by)
        if not any(
            re.compile(rf"def\s+{re.escape(name)}\b").search(text) for text in py_files.values()
        )
    ]


def repo_structure_hash(files: dict[str, str]) -> str:
    """Hash of the sorted set of (file path, import statements) pairs.

    Repo-scoped constraints demote on structural change (new files, changed
    imports), not on every edit.
    """
    entries: list[str] = []
    for path in sorted(files):
        imports = [
            line.strip()
            for line in files[path].splitlines()
            if _IMPORT_RE.match(line)
        ]
        entries.append(path + "\n" + "\n".join(imports))
    return _sha12("\n--\n".join(entries))
