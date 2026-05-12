"""Transitive `_lib/` import graph and closure for staleness detection.

LabDash mandates statically-resolvable imports in `_lib/` (and in wrappers'
references to `_lib/`) so that editing any `_lib/` file reliably marks every
transitively-dependent wrapper as stale. Star imports, dynamic imports, and
parse errors raise `LibImportError` at build time with a precise pointer to
the offending file and line.

Public API:
    LibImportError                 — typed error with file:line:hint message
    parse_lib_imports(py, *, lib_dir) -> set[str]
    build_lib_graph(lib_dir)       -> dict[str, set[str]]
    transitive_lib_closure(roots, graph) -> set[str]
"""

from __future__ import annotations

import ast
from pathlib import Path


class LibImportError(Exception):
    """A wrapper or `_lib/` file uses a non-statically-resolvable import."""


def _format_error(py_path: Path, lineno: int | None, snippet: str, hint: str) -> str:
    loc = f"{py_path}:{lineno}" if lineno is not None else str(py_path)
    return f"{loc}\n  Cannot statically resolve import:\n    {snippet}\n  {hint}"


def _is_dynamic_import_call(node: ast.Call) -> tuple[bool, str | None]:
    """Detect `importlib.import_module("_lib...")` and `__import__("_lib...")`.

    Returns (is_dynamic_import_of_lib, target_string_if_static). For our
    purposes we ban any dynamic import that targets `_lib` (even when the
    string is a constant) so agents don't try to be clever.
    """
    func = node.func
    name: str | None = None
    if isinstance(func, ast.Attribute):
        # importlib.import_module(...)
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
            and func.attr == "import_module"
        ):
            name = "importlib.import_module"
    elif isinstance(func, ast.Name):
        # __import__(...) or bare import_module(...) (from importlib import import_module)
        if func.id in ("__import__", "import_module"):
            name = func.id

    if name is None:
        return False, None

    # If the first positional arg is a Constant str starting with "_lib", it
    # targets _lib. If it's anything else (Name, fstring, computed), we still
    # ban it because we can't prove it doesn't target _lib.
    if not node.args:
        return False, None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        if arg.value == "_lib" or arg.value.startswith("_lib."):
            return True, f"{name}({arg.value!r})"
        return False, None
    # Non-constant first arg → cannot prove safety; ban it.
    return True, f"{name}(<dynamic>)"


def parse_lib_imports(py_path: Path, *, lib_dir: Path) -> set[str]:
    """Return the set of `_lib/`-relative paths imported by `py_path`.

    Resolves `from _lib.plots.sde_accuracy import make` →
    `"plots/sde_accuracy.py"`. Only returns entries that resolve to an
    existing file under `lib_dir`. Returns the empty set if the file has
    no `_lib` imports.

    Raises `LibImportError` on:
      - star imports of `_lib.X` (`from _lib.X import *`)
      - dynamic imports of `_lib.X` via `importlib.import_module` or
        `__import__` (constant or non-constant target)
      - syntax errors or unreadable files
    """
    try:
        source = py_path.read_text()
    except OSError as e:
        raise LibImportError(
            _format_error(
                py_path, None, repr(e),
                "File could not be read. LabDash requires all `_lib/` and "
                "wrapper files to parse cleanly.",
            )
        )

    try:
        tree = ast.parse(source, filename=str(py_path))
    except SyntaxError as e:
        raise LibImportError(
            _format_error(
                py_path, e.lineno, (e.text or "").rstrip(),
                f"SyntaxError: {e.msg}. Fix the file before building.",
            )
        )

    rels: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod != "_lib" and not mod.startswith("_lib."):
                continue
            # Star imports of _lib are banned.
            for alias in node.names:
                if alias.name == "*":
                    raise LibImportError(
                        _format_error(
                            py_path, node.lineno,
                            f"from {mod} import *",
                            "LabDash mandates explicit imports in `_lib/` so "
                            "staleness detection is correct. Replace the star "
                            "import with explicit names.",
                        )
                    )
            # `from _lib.plots.sde_accuracy import make` → "plots/sde_accuracy.py"
            # `from _lib import style` → register "style.py" (one alias per name).
            if mod == "_lib":
                # `from _lib import X, Y` — each name is a sibling module.
                for alias in node.names:
                    rel = alias.name.replace(".", "/") + ".py"
                    if (lib_dir / rel).is_file():
                        rels.add(rel)
            else:
                rel = mod[len("_lib."):].replace(".", "/") + ".py"
                if (lib_dir / rel).is_file():
                    rels.add(rel)
        elif isinstance(node, ast.Import):
            # `import _lib.plots.sde_accuracy` (rare but supported)
            for alias in node.names:
                name = alias.name
                if name != "_lib" and not name.startswith("_lib."):
                    continue
                if name == "_lib":
                    continue  # nothing concrete to point at
                rel = name[len("_lib."):].replace(".", "/") + ".py"
                if (lib_dir / rel).is_file():
                    rels.add(rel)
        elif isinstance(node, ast.Call):
            is_dyn, snippet = _is_dynamic_import_call(node)
            if is_dyn:
                raise LibImportError(
                    _format_error(
                        py_path, node.lineno, snippet or "<dynamic import>",
                        "LabDash mandates static imports in `_lib/` so "
                        "staleness detection is correct. Replace the dynamic "
                        "import with an explicit `from _lib.X import Y`.",
                    )
                )

    return rels


def build_lib_graph(lib_dir: Path, *, language=None) -> dict[str, set[str]]:
    """Adjacency map keyed by `_lib/`-relative POSIX path.

    Walks `lib_dir` for files matching the language's `_lib/` extension
    (`.py` for Python, `.R` for R) and parses each via the language's
    static import parser. The value at `rel` is the set of
    `_lib/`-relative paths that file directly imports; callers compose
    transitively via `transitive_lib_closure`.

    When `language` is omitted, defaults to Python — preserves the
    pre-language-adapter behaviour for older callers.

    Raises `LibImportError` on the first file with a non-static or
    unresolvable import.
    """
    if language is None:
        from .languages import PythonLanguage
        language = PythonLanguage
    graph: dict[str, set[str]] = {}
    pattern = f"*{language.lib_extension}"
    for path in sorted(lib_dir.rglob(pattern)):
        if not path.is_file():
            continue
        rel = path.relative_to(lib_dir).as_posix()
        graph[rel] = language.parse_lib_imports(path, lib_dir)
    return graph


def transitive_lib_closure(
    roots: set[str] | list[str], graph: dict[str, set[str]]
) -> set[str]:
    """BFS over `graph` starting from `roots`. Returns `roots ∪ reachable`.

    Cycle-safe via a `seen` set. Roots that don't appear in `graph` are kept
    in the output but do not contribute further traversal (no `KeyError`).
    """
    closure: set[str] = set(roots)
    queue: list[str] = list(roots)
    while queue:
        cur = queue.pop()
        for nxt in graph.get(cur, ()):
            if nxt not in closure:
                closure.add(nxt)
                queue.append(nxt)
    return closure
