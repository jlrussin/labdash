"""Language adapter for LabDash analyses.

Each LabDash collection is wholly Python or wholly R (set via
`language:` in `collection.yaml`; default `python`). The runner,
builder, and import-graph code dispatch through a Language adapter
so they don't bake in per-language assumptions.

The adapter answers four questions for the runner/builder pipeline:

    * What filename does an analysis script use? (`analysis.py` / `analysis.R`)
    * What file extension does `_lib/` use?      (`.py` / `.R`)
    * How do I run one analysis?                 (in-process import / Rscript subprocess)
    * How do I parse its `_lib/` imports?        (AST / regex)

It also carries display-only hints (Pygments lexer + Monaco language id)
so the dashboard renders code in the right language without the rest of
the codebase caring.

There are exactly two adapters today: `PythonLanguage` (which reproduces
the original in-process behaviour) and `RLanguage` (subprocess via
`Rscript`). New languages would add another `@dataclass(frozen=True)`
adapter and an entry in `_LANGUAGES`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml


# ── Protocol-like dataclass ────────────────────────────────────────────


@dataclass(frozen=True)
class Language:
    """Per-language adapter. Bind a few callables, get a polymorphic runner.

    The four behaviour fields (`run_analysis`, `parse_lib_imports`,
    `parse_wrapper_imports`, `highlight_source`) are bound to concrete
    implementations by the module-level singletons below. Tests can build
    a Language with stubs.

    Fields:
      name                  — "python" | "r"
      analysis_filename     — basename of the per-slug script
      lib_extension         — extension of `_lib/` files (".py" / ".R")
      lexer_name            — Pygments lexer key (display only)
      monaco_language_id    — Monaco editor mode (display only)
      run_analysis(analysis, output_dir) -> result dict
                            — must populate {slug, success, stats, error, duration_s}
      parse_lib_imports(path, lib_dir) -> set[str]
                            — relative `_lib/` paths this file directly imports
      parse_wrapper_imports(analysis_path, lib_dir) -> list[str]
                            — same as above but sorted; used by the builder for
                              the per-card "shared code" tabs
      highlight_source(code) -> str
                            — Pygments-rendered HTML (no <pre> wrapper)
    """

    name: str
    analysis_filename: str
    lib_extension: str
    lexer_name: str
    monaco_language_id: str
    run_analysis: Callable[[dict, Path], dict]
    parse_lib_imports: Callable[[Path, Path], set[str]]
    parse_wrapper_imports: Callable[[Path, Path], list[str]]
    highlight_source: Callable[[str], str]


# ── Concrete implementations ───────────────────────────────────────────
#
# Imports happen lazily inside each callable so that `import labdash.languages`
# stays cheap and so `RLanguage`-only code (which pulls in subprocess/jsonlite
# helpers) doesn't cost anything for Python-only collections.


def _python_run(analysis: dict, output_dir: Path) -> dict:
    from . import _python_runtime
    return _python_runtime.run(analysis, output_dir)


def _python_parse_lib_imports(path: Path, lib_dir: Path) -> set[str]:
    from .lib_graph import parse_lib_imports
    return parse_lib_imports(path, lib_dir=lib_dir)


def _python_parse_wrapper_imports(analysis_path: Path, lib_dir: Path) -> list[str]:
    return sorted(_python_parse_lib_imports(analysis_path, lib_dir))


def _python_highlight(code: str) -> str:
    from pygments import highlight
    from pygments.lexers import PythonLexer
    from pygments.formatters import HtmlFormatter
    return highlight(
        code, PythonLexer(),
        HtmlFormatter(nowrap=True, style=PYGMENTS_STYLE),
    )


def _r_run(analysis: dict, output_dir: Path) -> dict:
    from . import r_runner
    return r_runner.run(analysis, output_dir)


def _r_parse_lib_imports(path: Path, lib_dir: Path) -> set[str]:
    from .r_import_parser import parse_r_lib_imports
    return parse_r_lib_imports(path, lib_dir=lib_dir)


def _r_parse_wrapper_imports(analysis_path: Path, lib_dir: Path) -> list[str]:
    return sorted(_r_parse_lib_imports(analysis_path, lib_dir))


def _r_highlight(code: str) -> str:
    from pygments import highlight
    from pygments.lexers import SLexer
    from pygments.formatters import HtmlFormatter
    return highlight(
        code, SLexer(),
        HtmlFormatter(nowrap=True, style=PYGMENTS_STYLE),
    )


# Module-level so callers don't have to thread this through.
PYGMENTS_STYLE = "one-dark"


PythonLanguage = Language(
    name="python",
    analysis_filename="analysis.py",
    lib_extension=".py",
    lexer_name="python",
    monaco_language_id="python",
    run_analysis=_python_run,
    parse_lib_imports=_python_parse_lib_imports,
    parse_wrapper_imports=_python_parse_wrapper_imports,
    highlight_source=_python_highlight,
)


RLanguage = Language(
    name="r",
    analysis_filename="analysis.R",
    lib_extension=".R",
    lexer_name="r",
    monaco_language_id="r",
    run_analysis=_r_run,
    parse_lib_imports=_r_parse_lib_imports,
    parse_wrapper_imports=_r_parse_wrapper_imports,
    highlight_source=_r_highlight,
)


_LANGUAGES: dict[str, Language] = {
    "python": PythonLanguage,
    "r": RLanguage,
}


# ── Resolution ─────────────────────────────────────────────────────────


def resolve_language(collection_config: dict | None) -> Language:
    """Return the Language for a collection. Defaults to Python.

    Reads `collection.yaml`'s top-level `language:` field (case-insensitive,
    lowercased). Unknown values raise `ValueError` rather than silently
    falling back, so typos don't run as Python by accident.
    """
    if not collection_config:
        return PythonLanguage
    raw = collection_config.get("language")
    if raw is None:
        return PythonLanguage
    key = str(raw).strip().lower()
    if key not in _LANGUAGES:
        known = ", ".join(sorted(_LANGUAGES))
        raise ValueError(
            f"Unknown collection language: {raw!r}. Known: {known}."
        )
    return _LANGUAGES[key]


def resolve_language_for(analyses_dir: Path) -> Language:
    """Convenience: read `<analyses_dir>/collection.yaml` and resolve."""
    cfg_path = analyses_dir / "collection.yaml"
    if not cfg_path.exists():
        return PythonLanguage
    try:
        config = yaml.safe_load(cfg_path.read_text()) or {}
    except yaml.YAMLError:
        return PythonLanguage
    return resolve_language(config)
