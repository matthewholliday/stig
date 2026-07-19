"""The annotation grammar and lifecycle model (SPEC §03, §04, §05).

An annotation is a single-line, machine-parseable comment carrying typed state:
a kind, a stable ID, a status, optional attributes, and free-text body.

    # @<kind>(<id>[, key=value]*): <body text>

Continuation lines (a comment line immediately following, indented with ``..``)
extend the body of the annotation directly above them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The five kinds and their one-letter ID prefixes (SPEC §03, §04).
# ---------------------------------------------------------------------------

KIND_PREFIX: dict[str, str] = {
    "goal": "g",
    "constraint": "c",
    "unresolved": "u",
    "decision": "d",
    "tried": "t",
}
PREFIX_KIND: dict[str, str] = {v: k for k, v in KIND_PREFIX.items()}

KINDS = tuple(KIND_PREFIX)

# Lifecycles (SPEC §05). The status set for each kind is closed.
VALID_STATUS: dict[str, set[str]] = {
    "goal": {"open", "satisfied", "stuck"},
    "constraint": {"asserted", "verified", "violated", "enforced"},
    "unresolved": {"open", "answered", "needs-human"},
    "decision": {"recorded"},
    "tried": {"recorded"},
}

# Kinds that are a permanent record: never actionable, never consumed (SPEC §03).
PERMANENT_KINDS = {"decision", "tried"}

# The status a newly-minted annotation of each kind starts in (SPEC §05).
DEFAULT_STATUS: dict[str, str] = {
    "goal": "open",
    "constraint": "asserted",
    "unresolved": "open",
    "decision": "recorded",
    "tried": "recorded",
}

# ---------------------------------------------------------------------------
# Grammar (SPEC §04).
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"^(?P<indent>[ \t]*)#\s*@(?P<kind>[A-Za-z]+)\((?P<inside>[^)]*)\):(?P<body>.*)$"
)
_CONT_RE = re.compile(r"^(?P<indent>[ \t]*)#\s*\.\.\s?(?P<body>.*)$")


class GrammarError(ValueError):
    """A line looked like an annotation but violated the grammar."""


class AnnotationTouchError(ValueError):
    """The diff channel tried to add, modify, or delete an annotation line.

    Defined here rather than in ``diffutil`` because both the pre-check and the
    applier — which cannot import ``diffutil`` without a cycle — must raise it.
    """


@dataclass
class Annotation:
    """One parsed annotation and its location in the medium."""

    kind: str
    id: str | None
    attrs: dict[str, str]  # ordered; always contains "status"
    body: str
    file: str  # repo-relative path
    start_line: int  # 0-based index of the header line
    end_line: int  # 0-based index of the last continuation line (== start if none)
    indent: str = ""
    continuation: list[str] = field(default_factory=list)  # raw continuation lines

    # -- convenience views ---------------------------------------------------

    @property
    def status(self) -> str | None:
        return self.attrs.get("status")

    @status.setter
    def status(self, value: str) -> None:
        self.attrs["status"] = value

    @property
    def strikes(self) -> int:
        try:
            return int(self.attrs.get("strikes", "0"))
        except ValueError:
            return 0

    @strikes.setter
    def strikes(self, value: int) -> None:
        self.attrs["strikes"] = str(value)

    @property
    def full_body(self) -> str:
        """Body plus any continuation text, joined with spaces."""
        parts = [self.body.strip()]
        for raw in self.continuation:
            m = _CONT_RE.match(raw)
            if m:
                parts.append(m.group("body").strip())
        return " ".join(p for p in parts if p)

    def prefix(self) -> str:
        return KIND_PREFIX[self.kind]

    # -- rendering -----------------------------------------------------------

    def header_text(self) -> str:
        """Render the header line (without trailing newline)."""
        inside = self.id or ""
        for key, value in self.attrs.items():
            inside += f", {key}={value}"
        body = self.body
        if body and not body.startswith(" "):
            body = " " + body
        return f"{self.indent}# @{self.kind}({inside}):{body}"

    def clone(self) -> Annotation:
        return Annotation(
            kind=self.kind,
            id=self.id,
            attrs=dict(self.attrs),
            body=self.body,
            file=self.file,
            start_line=self.start_line,
            end_line=self.end_line,
            indent=self.indent,
            continuation=list(self.continuation),
        )


def parse_inside(inside: str) -> tuple[str | None, dict[str, str]]:
    """Parse the ``<id>[, key=value]*`` portion inside the parentheses."""
    parts = [p.strip() for p in inside.split(",")]
    raw_id = parts[0].strip() if parts else ""
    ann_id: str | None = raw_id or None
    attrs: dict[str, str] = {}
    for part in parts[1:]:
        if not part:
            continue
        if "=" not in part:
            raise GrammarError(f"attribute {part!r} is not key=value")
        key, value = part.split("=", 1)
        attrs[key.strip()] = value.strip()
    return ann_id, attrs


def parse_file(text: str, path: str) -> list[Annotation]:
    """Parse every annotation (with continuation lines) out of one file."""
    lines = text.splitlines()
    annotations: list[Annotation] = []
    i = 0
    while i < len(lines):
        m = _HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        kind = m.group("kind").lower()
        if kind not in KINDS:
            i += 1
            continue
        ann_id, attrs = parse_inside(m.group("inside"))
        body = m.group("body")
        if body.startswith(" "):
            body = body[1:]
        start = i
        cont: list[str] = []
        j = i + 1
        while j < len(lines) and _CONT_RE.match(lines[j]):
            cont.append(lines[j])
            j += 1
        annotations.append(
            Annotation(
                kind=kind,
                id=ann_id,
                attrs=attrs,
                body=body,
                file=path,
                start_line=start,
                end_line=j - 1,
                indent=m.group("indent"),
                continuation=cont,
            )
        )
        i = j
    return annotations


def is_annotation_line(line: str) -> bool:
    """True for a header line or a continuation line (SPEC §07 diff guard)."""
    return bool(_HEADER_RE.match(line) or _CONT_RE.match(line))


def annotation_lines(text: str) -> list[str]:
    """Every annotation line in a file, in order — the diff guard's invariant.

    A code diff MUST leave this list identical. Comparing before and after is
    what makes the guard total: it does not matter whether a diff reaches the
    annotation through a ``+``/``-`` marker, an unmarked line, a whole-file
    overwrite, or a deletion.
    """
    return [ln for ln in text.splitlines() if is_annotation_line(ln)]


def status_is_valid(kind: str, status: str | None) -> bool:
    return status in VALID_STATUS.get(kind, set())
