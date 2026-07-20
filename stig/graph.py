"""A cheap static import graph.

Context assembly walks direct imports of a file and direct importers of it —
one hop each way. This is a deterministic function of the source, not a
retrieval heuristic.
"""

from __future__ import annotations

import ast


def _module_name(path: str) -> str:
    """Best-effort dotted module name for a repo-relative .py path."""
    no_ext = path[:-3] if path.endswith(".py") else path
    parts = [p for p in no_ext.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports_of(text: str) -> set[str]:
    """Top-level module names imported by a source file."""
    names: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                # `from pkg import b` also imports the submodule pkg.b.
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


class ImportGraph:
    """Direct-import / direct-importer lookup over a set of Python files."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self._module_to_path: dict[str, str] = {}
        self._imports: dict[str, set[str]] = {}
        for path, text in files.items():
            if not path.endswith(".py"):
                continue
            self._module_to_path[_module_name(path)] = path
            self._imports[path] = _imports_of(text)

    def _resolve(self, module: str) -> str | None:
        if module in self._module_to_path:
            return self._module_to_path[module]
        # Handle `from pkg.mod import x` where pkg.mod maps to a file, and also
        # bare-name imports of sibling modules.
        tail = module.split(".")[-1]
        for mod, path in self._module_to_path.items():
            if mod.split(".")[-1] == tail:
                return path
        return None

    def direct_imports(self, path: str) -> set[str]:
        """Repo files directly imported by ``path`` (one hop out)."""
        out: set[str] = set()
        for mod in self._imports.get(path, set()):
            resolved = self._resolve(mod)
            if resolved and resolved != path:
                out.add(resolved)
        return out

    def direct_importers(self, path: str) -> set[str]:
        """Repo files that directly import ``path`` (one hop in)."""
        target = _module_name(path)
        out: set[str] = set()
        for other, mods in self._imports.items():
            if other == path:
                continue
            for mod in mods:
                if self._resolve(mod) == path or mod == target:
                    out.add(other)
                    break
        return out
