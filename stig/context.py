"""Context assembly (SPEC §08).

The read path is a deterministic function of the annotation, not a retrieval
heuristic. Because state is colocated with code, opening the right files is the
retrieval step.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

from .annotations import Annotation
from .graph import ImportGraph
from .repo import ARCHITECTURE_FILE, Repo


@dataclass
class Context:
    text: str
    snapshot: dict[str, str]  # file path -> content hash of every file read in
    files_read: list[str] = field(default_factory=list)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _signatures(text: str) -> list[str]:
    """Top-level def/class signatures — a compressed repo map (SPEC §08 item 4)."""
    out: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            out.append(f"{prefix} {node.name}(...)")
        elif isinstance(node, ast.ClassDef):
            out.append(f"class {node.name}")
    return out


class ContextBuilder:
    def __init__(self, repo: Repo, token_budget: int = 12000):
        self.repo = repo
        # Deterministic char budget (~4 chars/token) for the compressed map.
        self.char_budget = token_budget * 4

    def build(self, active: Annotation, annotations: list[Annotation]) -> Context:
        files = self.repo.files_map()
        py_files = {p: t for p, t in files.items() if p.endswith(".py")}
        graph = ImportGraph(py_files)

        # Hashes are recorded for every file *read*, but the snapshot only keeps
        # the ones that survive budget trimming into the final prompt: the race
        # check exists to catch edits to state the model actually reasoned about,
        # and snapshotting a trimmed-away file discards activations for nothing.
        hashes: dict[str, str] = {}
        sections: list[str] = []

        def read_in(path: str) -> str:
            text = files.get(path, "")
            hashes[path] = _hash(text)
            return text

        # Item 1 (MUST): the annotation itself and its full containing file.
        sections.append(
            f"### ACTIVE ANNOTATION\n"
            f"{active.header_text()}\n"
            f"kind={active.kind} id={active.id} status={active.status}\n"
            f"body: {active.full_body}"
        )
        active_text = read_in(active.file)
        sections.append(f"### FILE: {active.file}\n{active_text}")

        # Item 2 (MUST): direct imports and direct importers (one hop each way).
        hop_files: list[str] = []
        if active.file.endswith(".py"):
            hop_files = sorted(
                graph.direct_imports(active.file) | graph.direct_importers(active.file)
            )
        hop_section: list[tuple[str, str]] = []  # (path, rendered section)
        for path in hop_files:
            hop_section.append((path, f"### RELATED FILE: {path}\n{read_in(path)}"))

        # Item 3 (MUST, never dropped): all @decision bodies + all ARCHITECTURE,
        # plus relevance-scoped @tried (active file, or goal= names active id).
        decisions = [a for a in annotations if a.kind == "decision"]
        dec_lines = [f"- [{a.file}] {a.id}: {a.full_body}" for a in decisions]
        tried = [
            a
            for a in annotations
            if a.kind == "tried"
            and (a.file == active.file or a.attrs.get("goal") == active.id)
        ]
        tried_lines = [
            f"- {a.id} (goal={a.attrs.get('goal', '?')}, "
            f"diff_hash={a.attrs.get('diff_hash', '?')}): {a.full_body}"
            for a in tried
        ]
        arch_text = read_in(ARCHITECTURE_FILE) if self.repo.exists(ARCHITECTURE_FILE) else ""
        item3 = ["### DECISIONS (repo-wide)"]
        item3.extend(dec_lines or ["(none)"])
        item3.append("\n### RELEVANT PRIOR ATTEMPTS (@tried)")
        item3.extend(tried_lines or ["(none)"])
        item3.append(f"\n### {ARCHITECTURE_FILE}\n{arch_text}")
        item3_section = "\n".join(item3)

        # Item 4 (SHOULD, first to be dropped): signatures of everything else.
        other = [
            p
            for p in sorted(py_files)
            if p != active.file and p not in hop_files
        ]
        map_lines: list[str] = []
        for path in other:
            sigs = _signatures(py_files[path])
            if sigs:
                map_lines.append(f"{path}: " + "; ".join(sigs))
        map_section = "### REPO MAP (signatures)\n" + ("\n".join(map_lines) or "(none)")

        # Budget: items 1 and 3 are never dropped. Drop item 4, then trim item 2.
        must = "\n\n".join(sections + [item3_section])
        remaining = self.char_budget - len(must)
        if remaining > len(map_section):
            hop_and_map = hop_section + [(None, map_section)]
        else:
            hop_and_map = list(hop_section)  # drop item 4
        # Trim item 2 by import distance (arbitrary but deterministic: last first).
        while hop_and_map and (
            len(must) + len("\n\n".join(s for _p, s in hop_and_map)) > self.char_budget
        ):
            hop_and_map.pop()

        included = {p for p, _s in hop_and_map if p} | {
            active.file,
            ARCHITECTURE_FILE,
        }
        snapshot = {p: h for p, h in hashes.items() if p in included}
        text = "\n\n".join(sections + [s for _p, s in hop_and_map] + [item3_section])
        return Context(text=text, snapshot=snapshot, files_read=sorted(snapshot))

    def disk_hashes(self, snapshot: dict[str, str]) -> dict[str, str]:
        """Recompute hashes for the snapshotted files, from current disk state."""
        out: dict[str, str] = {}
        for path in snapshot:
            out[path] = _hash(self.repo.read(path)) if self.repo.exists(path) else ""
        return out
