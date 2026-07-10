"""Diff channel handling (SPEC §07, §11).

The two output channels are disjoint: the diff channel is for code only, and
``apply_diff`` rejects any hunk that adds, modifies, or deletes an annotation
line. New annotations enter only through the structured channel; status changes
enter only through the status-update channel. Without this, a generated diff
could rewrite a constraint's status directly and the injection defense would be
decorative.
"""

from __future__ import annotations

import hashlib

from .annotations import is_annotation_line


class AnnotationTouchError(ValueError):
    """A diff hunk tried to add, modify, or delete an annotation line."""


def diff_hash(diff_text: str) -> str:
    """sha256[:12] of a diff — stored as diff_hash= on @tried (SPEC §11)."""
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:12]


def _hunk_body_lines(diff_text: str):
    """Yield (marker, content) for +/- lines inside hunks (not headers)."""
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if line.startswith(("--- ", "+++ ", "diff ", "index ", "new file", "deleted file", "rename ", "similarity ")):
            in_hunk = False
            continue
        if not in_hunk:
            continue
        if line[:1] in ("+", "-"):
            yield line[0], line[1:]


def assert_no_annotation_lines(diff_text: str) -> None:
    """Reject a diff that touches annotation lines (SPEC §07)."""
    for _marker, content in _hunk_body_lines(diff_text):
        if is_annotation_line(content):
            raise AnnotationTouchError(
                f"diff channel may not touch annotation lines: {content.strip()!r}"
            )
