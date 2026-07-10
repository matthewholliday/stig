"""The medium: the repository as a store of annotations (SPEC §01, §04).

The repository — source files and git history — carries all working state.
This module reads and writes the file half; ``gitutil`` covers the history half.
"""

from __future__ import annotations

import os

from .annotations import KIND_PREFIX, Annotation, parse_file

ARCHITECTURE_FILE = "ARCHITECTURE.anno"
_SKIP_DIRS = {".git", ".stig", "__pycache__", ".venv", "venv", ".pytest_cache", ".ruff_cache"}


class DuplicateIDError(ValueError):
    """Two annotations share an ID — a grammar error (SPEC §04)."""


class Repo:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # -- paths ---------------------------------------------------------------

    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def source_paths(self) -> list[str]:
        """Repo-relative paths that may carry annotations (.py + ARCHITECTURE)."""
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py") or name == ARCHITECTURE_FILE:
                    rel = os.path.relpath(os.path.join(dirpath, name), self.root)
                    out.append(rel.replace(os.sep, "/"))
        return sorted(out)

    def python_files(self) -> dict[str, str]:
        return {p: self.read(p) for p in self.source_paths() if p.endswith(".py")}

    def files_map(self) -> dict[str, str]:
        return {p: self.read(p) for p in self.source_paths()}

    def read(self, rel: str) -> str:
        with open(self.path(rel), encoding="utf-8") as fh:
            return fh.read()

    def write(self, rel: str, text: str) -> None:
        full = self.path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.path(rel))

    # -- parsing -------------------------------------------------------------

    def parse_all(self) -> list[Annotation]:
        annotations: list[Annotation] = []
        for rel in self.source_paths():
            annotations.extend(parse_file(self.read(rel), rel))
        return annotations

    def is_repo_scoped(self, ann: Annotation) -> bool:
        return ann.file == ARCHITECTURE_FILE

    # -- ID minting (SPEC §04): the scheduler is the sole minting authority ---

    def _next_counters(self, annotations: list[Annotation]) -> dict[str, int]:
        counters: dict[str, int] = {k: 0 for k in KIND_PREFIX}
        for ann in annotations:
            if not ann.id:
                continue
            prefix = KIND_PREFIX[ann.kind]
            if ann.id.startswith(prefix):
                try:
                    n = int(ann.id[len(prefix):])
                except ValueError:
                    continue
                counters[ann.kind] = max(counters[ann.kind], n)
        return counters

    def assign_missing_ids(self) -> list[str]:
        """Assign IDs to human-added (ID-less) annotations and write them back.

        Returns the list of newly assigned IDs.
        """
        annotations = self.parse_all()
        counters = self._next_counters(annotations)
        assigned: list[str] = []
        # Group edits per file, applied bottom-up so line indices stay valid.
        by_file: dict[str, list[Annotation]] = {}
        for ann in annotations:
            if ann.id is None:
                counters[ann.kind] += 1
                ann.id = f"{KIND_PREFIX[ann.kind]}{counters[ann.kind]:02d}"
                assigned.append(ann.id)
                by_file.setdefault(ann.file, []).append(ann)
        for rel, anns in by_file.items():
            lines = self.read(rel).splitlines(keepends=True)
            for ann in sorted(anns, key=lambda a: a.start_line, reverse=True):
                nl = "\n" if lines[ann.start_line].endswith("\n") else ""
                lines[ann.start_line] = ann.header_text() + nl
            self.write(rel, "".join(lines))
        return assigned

    def check_duplicates(self) -> None:
        seen: dict[str, Annotation] = {}
        for ann in self.parse_all():
            if ann.id is None:
                continue
            if ann.id in seen:
                raise DuplicateIDError(
                    f"duplicate ID {ann.id!r} in {ann.file} and {seen[ann.id].file}"
                )
            seen[ann.id] = ann

    def next_id_for(self, kind: str, annotations: list[Annotation]) -> str:
        counters = self._next_counters(annotations)
        return f"{KIND_PREFIX[kind]}{counters[kind] + 1:02d}"

    # -- targeted edits ------------------------------------------------------

    def set_header(self, ann: Annotation) -> None:
        """Rewrite the header line of ``ann`` in place (single-line replace)."""
        lines = self.read(ann.file).splitlines(keepends=True)
        nl = "\n" if lines[ann.start_line].endswith("\n") else ""
        lines[ann.start_line] = ann.header_text() + nl
        self.write(ann.file, "".join(lines))

    def insert_lines_before(self, rel: str, line_idx: int, new_lines: list[str]) -> None:
        lines = self.read(rel).splitlines(keepends=True)
        block = [ln + "\n" for ln in new_lines]
        lines[line_idx:line_idx] = block
        self.write(rel, "".join(lines))

    def append_lines(self, rel: str, new_lines: list[str]) -> None:
        text = self.read(rel) if self.exists(rel) else ""
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(ln + "\n" for ln in new_lines)
        self.write(rel, text)

    def delete_range(self, rel: str, start: int, end: int) -> None:
        lines = self.read(rel).splitlines(keepends=True)
        del lines[start : end + 1]
        self.write(rel, "".join(lines))
