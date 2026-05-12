"""Static import parser for R analyses and `_lib/*.R` files.

Mirrors the strict-mandate enforcement of `lib_graph.parse_lib_imports`
for Python: the only sanctioned way for an R script to pull in
`_lib/` code is the bundled `lab_source("rel/path.R")` helper. Any
other form (computed paths, conditional sourcing, `source("_lib/...")`)
raises `LibImportError`, surfacing the violation as a build-time
diagnostic at file:line.

The parser does NOT execute R. It tokenizes line-by-line looking for
`lab_source(...)` and other `source(...)` calls referencing `_lib/`.
Pure regex is sufficient because the canonical form is so narrow.

Returns the set of `_lib/`-relative POSIX paths that the file
imports — matching the Python parser's contract so the rest of
`lib_graph` (graph construction, transitive closure, staleness)
works unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from .lib_graph import LibImportError


# `lab_source("foo.R")` or `lab_source("subdir/foo.R")`. The argument
# must be a literal double-quoted or single-quoted string; anything
# fancier (variables, file.path(...), paste(...), backtick names) is a
# strict-mandate violation.
_LAB_SOURCE_LITERAL = re.compile(
    r"""
    \blab_source\s*\(             # function name + opening paren
    \s*
    (?P<quote>['"])               # capture opening quote
    (?P<rel>[^'"]+)               # the path itself, no embedded quotes
    (?P=quote)                    # closing quote must match opening
    \s*\)
    """,
    re.VERBOSE,
)

# Any `lab_source(...)` invocation, even if the literal-string parser
# above rejected it. We use this to detect the violation when the user
# tried to call `lab_source` with a non-literal argument.
_LAB_SOURCE_ANY = re.compile(r"\blab_source\s*\(")

# Bare `source(...)` calls; we flag these as violations whenever they
# *appear to* reference `_lib/`. Plain `source()` against unrelated
# files (e.g. a user helper at the project root) is left alone.
_BARE_SOURCE_LIB = re.compile(r"""\bsource\s*\(\s*['"][^'"]*_lib[/'"]""")


def _format_error(py_path: Path, lineno: int, snippet: str, hint: str) -> str:
    return f"{py_path}:{lineno}\n  Cannot statically resolve import:\n    {snippet}\n  {hint}"


def parse_r_lib_imports(path: Path, *, lib_dir: Path) -> set[str]:
    """Return the set of `_lib/`-relative paths imported by `path`.

    Recognises exactly `lab_source("X.R")` and
    `lab_source("subdir/X.R")`. The path must point to an existing file
    under `lib_dir` to be returned (matches the Python parser's
    behaviour, which silently drops unresolvable names rather than
    raising — non-existent imports cause R itself to fail at runtime
    with a clearer message).

    Raises `LibImportError` on:
      - `lab_source(<non-literal>)` (variable, expression, computed)
      - `source("_lib/...")` outside of `lab_source` (any bare source
        pointing at `_lib/` is a strict-mandate violation)
      - file read errors
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise LibImportError(
            f"{path}\n  Cannot read R source: {e!r}.\n  "
            "Fix file permissions / encoding before building."
        )

    rels: set[str] = set()

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        # Strip everything after the first un-escaped `#` (R comments).
        # Inside string literals `#` is fine; we don't model that, but
        # since we only care about lines that begin a function call
        # with `lab_source(`, the simple split is sufficient.
        line = _strip_comment(raw_line)
        if not line.strip():
            continue

        # First: handle literal lab_source calls.
        for m in _LAB_SOURCE_LITERAL.finditer(line):
            rel = m.group("rel").strip()
            if not rel:
                continue
            if (lib_dir / rel).is_file():
                rels.add(rel)
            # If the file doesn't exist, we leave it un-added; R will
            # raise a clean error at runtime via `lab_source`. This
            # matches `parse_lib_imports`'s lenient handling.

        # Detect non-literal `lab_source(...)` invocations.
        for m in _LAB_SOURCE_ANY.finditer(line):
            tail = line[m.end():].lstrip()
            if not tail.startswith(("'", '"')):
                snippet = raw_line.strip()
                raise LibImportError(_format_error(
                    path, lineno, snippet,
                    "lab_source() requires a single literal string. "
                    "Replace any variables/expressions with the path literal."
                ))

        # Detect bare `source("_lib/...")` calls — strict-mandate violation.
        if _BARE_SOURCE_LIB.search(line):
            snippet = raw_line.strip()
            raise LibImportError(_format_error(
                path, lineno, snippet,
                "Use `lab_source(\"path/under/_lib.R\")` instead of `source(...)` "
                "for `_lib/` imports — labdash's staleness graph only sees "
                "`lab_source` calls."
            ))

    return rels


def _strip_comment(line: str) -> str:
    """Return `line` with R-style trailing `# ...` stripped.

    Naive: ignores `#` inside string literals. Good enough for the
    lab_source / source detection use case because both must appear as
    the first non-whitespace function call on the line for them to
    matter.
    """
    in_single = False
    in_double = False
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and (in_single or in_double):
            # Skip the escaped character.
            out.append(ch)
            out.append(line[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
        i += 1
    return "".join(out)
